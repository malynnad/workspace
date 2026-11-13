"""剪贴板编解码（零 Qt）。

只做数据转换，不碰 QClipboard：Qt 层把 QMimeData 的 `data(mime)` 取成 bytes 传进来，
把这里返回的 dict 写回去。

同时写两种格式，是为了跟桌面环境互通：

* `text/uri-list`  —— 标准格式，几乎所有文件管理器都读；
* `x-special/gnome-copied-files` —— Nautilus/GNOME 系（麒麟的 Peony 也是这一支）
  用它区分「复制」和「剪切」，第一行是 `copy` 或 `cut`，后面每行一个 URI。

只写了 uri-list 的话，粘到 Peony 里一律当成复制，剪切语义会丢；只写 gnome 格式的话，
不认识它的程序一个文件都拿不到。两个都写才是桌面上的常态。
"""

from __future__ import annotations

import os
from urllib.parse import quote, unquote, urlsplit

from .. import vpath

MIME_URI = "text/uri-list"
MIME_GNOME = "x-special/gnome-copied-files"
MIME_TEXT = "text/plain"

OPERATIONS = ("copy", "cut")


def path_to_uri(local_path: str) -> str:
    p = os.path.abspath(local_path)
    return "file://" + quote(p, safe="/")


def uri_to_vpath(uri: str) -> str:
    """file:// 开头的 URI → vpath；非本地（http、zip+file）返回空串。"""
    text = (uri or "").strip()
    if not text:
        return ""
    if text.startswith(vpath.ARCHIVE + "://"):
        return ""
    if text.startswith("file://"):
        try:
            parts = urlsplit(text)
        except ValueError:
            return ""
        if parts.netloc not in ("", "localhost"):
            return ""
        return vpath.encode_path(unquote(parts.path))
    return ""


def encode(operation: str, vpaths) -> dict[str, bytes]:
    """生成要写进剪贴板的两份数据。operation 非 copy/cut 时按 copy 处理。"""
    op = operation if operation in OPERATIONS else "copy"
    uris = []
    for v in vpaths or []:
        try:
            uris.append(path_to_uri(vpath.to_local(v)))
        except (ValueError, TypeError):
            continue
    if not uris:
        return {}
    crlf = "\r\n"
    return {
        MIME_URI: (crlf.join(uris) + crlf).encode("utf-8"),
        MIME_GNOME: ((op + crlf) + crlf.join(uris) + crlf).encode("utf-8"),
    }


def _lines(blob) -> list[str]:
    if isinstance(blob, bytes):
        text = blob.decode("utf-8", "replace")
    else:
        text = str(blob or "")
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _collect(uris) -> tuple[list[str], int]:
    vpaths = []
    ignored = 0
    seen = set()
    for uri in uris:
        v = uri_to_vpath(uri)
        if not v:
            # text/plain 里可能是裸路径，这里兜一下
            if os.path.isabs(uri):
                v = vpath.encode_path(uri)
            else:
                ignored += 1
                continue
        if not os.path.lexists(vpath.to_local(v)):
            ignored += 1
            continue
        if v in seen:
            continue
        seen.add(v)
        vpaths.append(v)
    return vpaths, ignored


def parse(data: dict) -> dict:
    """从剪贴板 MIME 表解析出 {operation, vpaths, ignored}。

    data 是 {mime: bytes}；键不存在就是 None。优先级：
    gnome（能带 cut）> uri-list > text/plain（当纯路径列表用）。
    """
    result = {"operation": "copy", "vpaths": [], "ignored": 0}
    if not data:
        return result

    gnome = data.get(MIME_GNOME)
    if gnome:
        lines = _lines(gnome)
        if lines:
            head = lines[0].lower()
            if head in OPERATIONS:
                result["operation"] = head
                lines = lines[1:]
            vpaths, ignored = _collect(lines)
            result["vpaths"], result["ignored"] = vpaths, ignored
            if vpaths:
                return result

    uri = data.get(MIME_URI)
    if uri:
        vpaths, ignored = _collect(_lines(uri))
        result["vpaths"], result["ignored"] = vpaths, ignored
        result["operation"] = "copy"
        if vpaths:
            return result

    text = data.get(MIME_TEXT)
    if text:
        vpaths, ignored = _collect(_lines(text))
        result["vpaths"], result["ignored"] = vpaths, ignored
        result["operation"] = "copy"
    return result
