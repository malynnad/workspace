"""PDF 页栅格化：直接产出 QImage 交给 QML 的图像提供器（不落盘、不编码）。

为什么不再走「渲成 PNG 落盘 → QML 读文件」那条路（M1b 的第一版）：
  · 编码才是瓶颈。实测一页扫描件渲染 34ms、PNG 编码 265ms；一页矢量文字
    渲染只要 7ms、PNG 编码 120ms。PNG 要逐行 filter 再 zlib，对满页抗锯齿
    文字最慢，而预览根本不需要无损。
  · 落盘唯一的好处是「同一文件再打开命中缓存」，但重开一次也就 30ms/页，
    不如把最近看过的几页留在内存里（见 pdf_image_provider 的 LRU）。

两种渲染后端，按装了什么自动选：
  mupdf（PyMuPDF）—— 首选。默认就画注释，**包括 /Widget 数字签名字段**。
      电子合同/发票上的电子签章大量是这种形态，QtPdf 完全不画，这就是
      「签章不显示」的根因。
  qtpdf（QPdfDocument）—— 兜底（没装 PyMuPDF 时）。必须显式传 Annotations
      渲染标志，否则连 /Stamp 注释都不画；/Widget 无能为力。

线程模型：这个模块被 pdf_image_provider 的**单个**工作线程调用，内部再加一把
全局锁。MuPDF 不是线程安全的（旧版 FileManager 遇到过两个线程同时 open 直接
SIGSEGV），锁住整个「开 → 渲 → 关」过程最省心，代价是渲染串行（本来就只占
一个线程）。
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

# ---------------- 后端选择 ----------------
_MUPDF = None
_QTPDF = None
_ENGINE = ""                 # "mupdf" / "qtpdf" / ""
_IMPORT_LOCK = threading.Lock()

RENDER_MIN = 320             # 最小渲染宽度（再小就看不清字）
# 最大渲染宽度：要容得下「宽窗口 × 2.0 倍超采样」。半屏 960 逻辑宽、dpr 2、
# 2.0 档 = 3584；再宽就封顶，这时「极高」会被压成「清晰」。QML 里的 clamp
# 必须与本值一致（PreviewWindow.pdfDecodeW），两处不同步会出现「切档没反应」。
RENDER_MAX = 4096
RENDER_STEP = 128            # 宽度量化步长：拖窗口时避免每 1px 重渲一次
# 单页像素上限，防畸形页面（票据长条页）吃爆内存。2.0 档 A4 约 18.2M，
# 留一点余量到 20M：一页 RGB888 ≈ 60MB，8GB 机器同时只渲一页，能接受。
MAX_PX = 20_000_000
REG_LIMIT = 6                # 同时记住几份 PDF（每份含已打开的文档句柄）


def _load_backends() -> str:
    """导入后端。返回引擎名（"" 表示两个都没有）。导入本身 ~125ms，别放在首帧。

    WORKSPACE_PDF_ENGINE=qtpdf 可强制退回 QtPdf（麒麟机上万一 PyMuPDF 装不上或
    渲染异常时用；代价是 /Widget 数字签名字段的签章画不出来）。
    """
    global _MUPDF, _QTPDF, _ENGINE
    with _IMPORT_LOCK:
        if _ENGINE:
            return _ENGINE
        want = (os.environ.get("WORKSPACE_PDF_ENGINE") or "").strip().lower()
        if want != "qtpdf":
            try:
                import pymupdf as mupdf      # 新包名；1.24 之后 fitz 只是别名
                _MUPDF = mupdf
                _ENGINE = "mupdf"
                return _ENGINE
            except ImportError:
                _MUPDF = None
        try:
            from PySide6.QtPdf import QPdfDocument
            _QTPDF = QPdfDocument
            _ENGINE = "qtpdf"
        except ImportError:
            _ENGINE = ""
        return _ENGINE


def warm_up() -> None:
    """后台预热：把后端导入提前做掉（主程序启动后再调，别拖首帧）。"""
    _load_backends()


def engine_name() -> str:
    return _ENGINE or _load_backends()


# ---------------- 会话登记表 ----------------
class _Doc:
    """一份 PDF 的打开状态：页尺寸、密码、以及（mupdf 的）已打开句柄。"""

    __slots__ = ("path", "password", "engine", "page_count", "sizes", "handle", "error")

    def __init__(self, path, password, engine):
        self.path = path
        self.password = password
        self.engine = engine
        self.page_count = 0
        self.sizes: list[tuple[float, float]] = []     # 每页 (宽, 高)，单位 pt
        self.handle = None                              # QPdfDocument 或 fitz.Document
        self.error = ""

    def close(self):
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            handle.close()          # 两种后端都有 close()
        except Exception:
            pass


_REG: "OrderedDict[str, _Doc]" = OrderedDict()
_LOCK = threading.RLock()


def _token(path: Path, stat, password: str) -> str:
    """token = 文件身份 + 密码身份。换密码就换 token，
    这样解锁后 QML 的 image:// 地址变了，不会拿到加锁阶段的空图。"""
    pw = hashlib.sha1(password.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    raw = f"{path}|{stat.st_size}|{stat.st_mtime_ns}|{pw}|{_ENGINE}"
    return hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _register(token: str, doc: _Doc) -> _Doc:
    """放进登记表；超限就把最久没用的关掉。"""
    old = _REG.pop(token, None)
    if old is not None and old is not doc:
        old.close()
    _REG[token] = doc
    while len(_REG) > REG_LIMIT:
        _, evicted = _REG.popitem(last=False)
        evicted.close()
    return doc


def forget(token: str) -> None:
    """预览窗关掉/换文件时释放（图像提供器的 LRU 也会被清）。"""
    with _LOCK:
        doc = _REG.pop(token, None)
        if doc is not None:
            doc.close()


def forget_all() -> None:
    with _LOCK:
        while _REG:
            _, doc = _REG.popitem()
            doc.close()


# ---------------- 打开 / 探测 ----------------
def _open_mupdf(doc: _Doc) -> None:
    fitz = _MUPDF
    handle = fitz.open(doc.path)
    if handle.needs_pass:
        if not doc.password:
            handle.close()
            doc.error = "needs-password"
            return
        if handle.authenticate(doc.password) <= 0:
            handle.close()
            doc.error = "bad-password"
            return
    # 加密文件认证之后 needs_pass 仍是 True，is_encrypted 反而会变 False
    # （旧版踩过），所以别再拿这两个判断「是否需要密码」。
    doc.page_count = int(handle.page_count)
    doc.sizes = []
    for i in range(doc.page_count):
        page = handle.load_page(i)
        rect = page.rect                 # 已包含 /Rotate，横版页不会算错比例
        doc.sizes.append((float(rect.width) or 595.0, float(rect.height) or 842.0))
    doc.handle = handle


def _open_qtpdf(doc: _Doc) -> None:
    handle = _QTPDF()
    if doc.password:
        handle.setPassword(doc.password)   # 必须先于 load，否则不重试（实测）
    handle.load(doc.path)
    if handle.status() != _QTPDF.Status.Ready:
        name = getattr(handle.error(), "name", "") or ""
        handle.close()
        if name in ("IncorrectPassword", "UnsupportedSecurityScheme"):
            doc.error = "bad-password" if doc.password else "needs-password"
        elif name == "FileNotFound":
            doc.error = "file-not-found"
        else:
            doc.error = "broken"
        return
    doc.page_count = int(handle.pageCount())
    doc.sizes = []
    for i in range(doc.page_count):
        size = handle.pagePointSize(i)
        w, h = float(size.width()), float(size.height())
        if w <= 0 or h <= 0:
            w, h = 595.0, 842.0
        doc.sizes.append((w, h))
    doc.handle = handle


def _open(path: Path, password: str) -> _Doc:
    engine = _load_backends()
    doc = _Doc(str(path), password, engine)
    if not engine:
        doc.error = "no-engine"
        return doc
    try:
        if engine == "mupdf":
            _open_mupdf(doc)
        else:
            _open_qtpdf(doc)
    except Exception:
        # 坏文件/改后缀的假 PDF 都从这里出：不区分具体异常类型，统一说「无法解析」
        doc.error = "broken"
        try:
            doc.close()
        except Exception:
            pass
    return doc


def probe(local_path: str, password: str = "") -> dict:
    """主探测：页数 + 每页尺寸 + 密码状态 + token。**不渲染任何页**（毫秒级）。"""
    out = {"ok": False, "engine": "", "token": "", "pageCount": 0, "sizes": [],
           "needsPassword": False, "passwordError": "", "error": "", "note": ""}
    path = Path(local_path)
    try:
        stat = path.stat()
    except OSError:
        out["error"] = "文件不存在或无法读取"
        return out
    engine = _load_backends()
    out["engine"] = engine
    if not engine:
        out["error"] = "没有可用的 PDF 渲染后端（缺 PyMuPDF 且 QPdfDocument 不可用）"
        return out

    token = _token(path, stat, password)
    with _LOCK:
        doc = _REG.get(token)
        if doc is not None:
            _REG.move_to_end(token)
        else:
            doc = _register(token, _open(path, password))

    out["token"] = token
    if doc.error == "needs-password":
        out["needsPassword"] = True
        return out
    if doc.error == "bad-password":
        out["needsPassword"] = True
        out["passwordError"] = "密码不正确，请重新输入"
        return out
    if doc.error == "file-not-found":
        out["error"] = "文件不存在或无法读取"
        return out
    if doc.error == "broken":
        out["error"] = "PDF 无法解析，文件可能已损坏"
        return out
    if doc.error:
        out["error"] = f"PDF 无法打开（{doc.error}）"
        return out
    if doc.page_count <= 0:
        out["error"] = "PDF 没有任何页面"
        return out

    out["ok"] = True
    out["pageCount"] = doc.page_count
    out["sizes"] = [[round(w, 2), round(h, 2)] for w, h in doc.sizes]
    out["note"] = f"共 {doc.page_count} 页"
    return out


# ---------------- 渲染 ----------------
def quantize_width(width: float) -> int:
    """把 QML 传来的显示宽度量化，拖窗口时不会每 1px 重渲一页。"""
    w = int(round(float(width) / RENDER_STEP)) * RENDER_STEP
    return max(RENDER_MIN, min(RENDER_MAX, w))


def render_page(token: str, index: int, width: float, cancelled=None) -> QImage | None:
    """渲染一页，返回 QImage（RGB32/RGB888，调用方负责持有）。

    cancelled 是可选的 callable() -> bool，用于救回「已经滚走/换了文件」的请求。
    """
    with _LOCK:
        doc = _REG.get(token)
        if doc is None or doc.handle is None or doc.error:
            return None
        _REG.move_to_end(token)
        if index < 0 or index >= doc.page_count:
            return None
        if cancelled is not None and cancelled():
            return None
        want = quantize_width(width)
        try:
            if doc.engine == "mupdf":
                return _render_mupdf(doc, index, want)
            return _render_qtpdf(doc, index, want)
        except Exception:
            return None


def _clamp(w: int, h: int) -> tuple[int, int]:
    if w * h <= MAX_PX or w <= 1 or h <= 1:
        return w, h
    factor = (MAX_PX / float(w * h)) ** 0.5
    return max(1, int(w * factor)), max(1, int(h * factor))


def _render_mupdf(doc: _Doc, index: int, width: int) -> QImage | None:
    page = doc.handle.load_page(index)
    rect = page.rect
    zoom = width / max(rect.width, 1.0)
    # 长条页（票据、条码单）按宽度算出来的高度可能爆像素，先按总量把缩放压回去
    est = width * rect.height * zoom
    if est > MAX_PX:
        zoom *= (MAX_PX / est) ** 0.5
    pix = page.get_pixmap(matrix=_MUPDF.Matrix(zoom, zoom), alpha=False)
    if pix.width < 2 or pix.height < 2:
        return None
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    return img.copy()          # samples 是临时缓冲，必须拷一份自己持有


def _render_qtpdf(doc: _Doc, index: int, width: int) -> QImage | None:
    from PySide6.QtPdf import QPdfDocumentRenderOptions as Opts
    pw, ph = doc.sizes[index]
    w, h = _clamp(width, int(round(width * ph / max(pw, 1.0))))
    opts = Opts()
    # 不加这个标志 QtPdf 只画页面内容，注释（印章）一个都不画
    opts.setRenderFlags(Opts.RenderFlag.Annotations)
    img = doc.handle.render(index, QSize(w, h), opts)
    return None if img.isNull() else img
