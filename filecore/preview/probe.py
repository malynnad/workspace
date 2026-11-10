"""图片头信息探测（纯 Python，零 Qt）。

只读文件前 64 KB 解析宽高，绝不在工作线程里解码整张图。
QML 侧拿到 source 后用 Image 的 sourceSize 限制解码尺寸，大图不会卡 UI。
不认识的格式（SVG / HEIC / AVIF / TIFF 等）返回 None，由上层决定降级路径。
"""

from __future__ import annotations

_READ_LIMIT = 64 * 1024
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9,
        0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _u16be(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 2], "big")


def _u16le(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 2], "little")


def _png(d: bytes):
    if not d.startswith(b"\x89PNG\r\n\x1a\n") or d[12:16] != b"IHDR":
        return None
    return {"width": int.from_bytes(d[16:20], "big"),
            "height": int.from_bytes(d[20:24], "big"), "format": "PNG"}


def _gif(d: bytes):
    if d[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    return {"width": _u16le(d, 6), "height": _u16le(d, 8), "format": "GIF"}


def _bmp(d: bytes):
    if d[:2] != b"BM":
        return None
    w = int.from_bytes(d[18:22], "little", signed=True)
    h = int.from_bytes(d[22:26], "little", signed=True)
    return {"width": abs(w), "height": abs(h), "format": "BMP"}


def _jpeg(d: bytes):
    if not d.startswith(b"\xff\xd8"):
        return None
    i = 2
    n = len(d)
    while i + 4 <= n:
        if d[i] != 0xFF:
            i += 1
            continue
        marker = d[i + 1]
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = _u16be(d, i + 2)
        if seg < 2:
            break
        if marker in _SOF and i + 9 <= n:
            return {"width": _u16be(d, i + 7), "height": _u16be(d, i + 5),
                    "format": "JPEG"}
        i += 2 + seg
    return None


def _webp(d: bytes):
    if d[:4] != b"RIFF" or d[8:12] != b"WEBP":
        return None
    chunk = d[12:16]
    if chunk == b"VP8X" and len(d) >= 30:
        w = int.from_bytes(d[24:27], "little") + 1
        h = int.from_bytes(d[27:30], "little") + 1
        return {"width": w, "height": h, "format": "WebP"}
    if chunk == b"VP8L" and len(d) >= 25 and d[20] == 0x2F:
        bits = int.from_bytes(d[21:25], "little")
        return {"width": (bits & 0x3FFF) + 1,
                "height": ((bits >> 14) & 0x3FFF) + 1, "format": "WebP"}
    if chunk == b"VP8 " and len(d) >= 30 and d[23:26] == b"\x9d\x01\x2a":
        return {"width": _u16le(d, 26) & 0x3FFF,
                "height": _u16le(d, 28) & 0x3FFF, "format": "WebP"}
    return None


def image_info(path: str) -> dict | None:
    """返回 {width, height, format}；读不到或不认识返回 None。"""
    try:
        with open(path, "rb") as f:
            d = f.read(_READ_LIMIT)
    except OSError:
        return None
    if len(d) < 12:
        return None
    for fn in (_png, _gif, _bmp, _jpeg, _webp):
        try:
            info = fn(d)
        except (IndexError, ValueError):
            info = None
        if info and info["width"] > 0 and info["height"] > 0:
            return info
    return None
