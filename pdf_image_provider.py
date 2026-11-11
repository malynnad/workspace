"""PDF 页的图像提供器：把 QImage 直接交给 QML 的 Image，不落盘、不编码。

QML 侧用法（PreviewWindow.qml）：
    Image { source: "image://pdfpage/<token>/<页码>/<显示宽度>" }

id 用 `/` 分隔（第一次实现用了 `|`，会被 QML 编码成 %7C 再传下来，解析炸掉）。
宽度按 128 取整（pdf_pages.quantize_width）：拖窗口时只有跨过一档才重渲染。

一个工作线程 + 一次渲染一页，够了：可见页一般 1~3 张，A4 扫描件 30~40ms/页，
矢量文字页 2~3ms/页。渲染完的页在内存 LRU 里留几页，滚回去立刻就有。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtQuick import (QQuickAsyncImageProvider, QQuickImageResponse,
                             QQuickTextureFactory)

import pdf_pages

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-page")
CACHE_BYTES = 64 * 1024 * 1024      # LRU 上限：A4@1400px 约 11MB，能留 5~6 页

_CACHE: "OrderedDict[tuple, QImage]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_USED = [0]
_ALIVE: list = []                   # 防 GC：响应对象要活到 QML 取走纹理


def cache_bytes() -> int:
    with _CACHE_LOCK:
        return _CACHE_USED[0]


def cache_pages() -> int:
    with _CACHE_LOCK:
        return len(_CACHE)


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_USED[0] = 0


def _image_bytes(img: QImage) -> int:
    try:
        return int(img.sizeInBytes())
    except Exception:
        return img.width() * img.height() * 4


def _cache_get(key):
    with _CACHE_LOCK:
        img = _CACHE.get(key)
        if img is not None:
            _CACHE.move_to_end(key)
        return img


def _cache_put(key, img) -> None:
    size = _image_bytes(img)
    with _CACHE_LOCK:
        old = _CACHE.pop(key, None)
        if old is not None:
            _CACHE_USED[0] -= _image_bytes(old)
        _CACHE[key] = img
        _CACHE_USED[0] += size
        while _CACHE_USED[0] > CACHE_BYTES and len(_CACHE) > 1:
            _, dropped = _CACHE.popitem(last=False)
            _CACHE_USED[0] -= _image_bytes(dropped)


def _prune_alive() -> None:
    """只丢已经渲染完的响应对象；还在渲的一个都不能动（GC 掉会崩）。"""
    keep = []
    dropped = 0
    for resp in _ALIVE:
        if dropped < 16 and getattr(resp, "_done", False):
            dropped += 1
            continue
        keep.append(resp)
    _ALIVE[:] = keep


class _Response(QQuickImageResponse):
    """一次取页请求。渲染在工作线程，完成后排队投递 finished 到 GUI 线程。"""

    _ready = Signal()

    def __init__(self, token: str, page: int, width: float):
        super().__init__()
        self._img: QImage | None = None
        self._error = ""
        self._cancelled = False
        self._done = False
        # 自定义信号 + QueuedConnection：跨线程 emit 的标准写法，
        # 直接在子线程 emit finished 会因接收方在 GUI 线程而行为不确定。
        self._ready.connect(self.finished, Qt.QueuedConnection)
        _ALIVE.append(self)
        if len(_ALIVE) > 32:
            _prune_alive()
        POOL.submit(self._run, token, page, width)

    def _run(self, token, page, width):
        try:
            if self._cancelled:
                return
            key = (token, page, pdf_pages.quantize_width(width))
            img = _cache_get(key)
            if img is None:
                img = pdf_pages.render_page(token, page, width,
                                            cancelled=lambda: self._cancelled)
                if img is not None:
                    _cache_put(key, img)
            self._img = img
            if img is None:
                self._error = "这一页渲染失败"
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self._done = True
            self._ready.emit()

    def textureFactory(self):
        if self._img is None:
            return None
        return QQuickTextureFactory.textureFactoryForImage(self._img)

    def errorString(self) -> str:
        return self._error

    def cancel(self):
        # 滚走了 / 换了文件：不中断已经开始的栅格化（MuPDF 中途打断不安全），
        # 只是渲染完不再往上递。
        self._cancelled = True


class PdfImageProvider(QQuickAsyncImageProvider):
    def requestImageResponse(self, image_id, requested_size):
        parts = unquote(image_id).split("/")
        if len(parts) < 3:
            return _Response("", -1, 0)
        token = parts[0]
        try:
            page = int(parts[1])
            width = float(parts[2])
        except ValueError:
            return _Response("", -1, 0)
        # 提前拦掉越界页码：别再进线程池排一次队
        if page < 0:
            return _Response("", -1, 0)
        return _Response(token, page, width)


def register(engine) -> PdfImageProvider:
    """挂到 QQmlEngine 上（必须在 engine.load 之前调用）。"""
    provider = PdfImageProvider()
    engine.addImageProvider("pdfpage", provider)
    return provider
