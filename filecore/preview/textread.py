"""安全的纯文本读取（零 Qt、零第三方依赖）。

文本/代码预览器只在这里碰文件内容：限额读取、编码探测、二进制识别、
按行截断。任何 I/O / 解码异常都转成中文文案写进返回值，绝不抛到 QML。
"""

from __future__ import annotations

import os

from .base import MAX_TEXT_BYTES, MAX_TEXT_LINES

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def looks_binary(head: bytes) -> bool:
    """BOM 已单独识别，这里只看前 8 KB 的 NUL 与控制字符密度。"""
    sample = head[:8192]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control = sum(1 for b in sample if b < 0x09 or 0x0E <= b < 0x20)
    return control / len(sample) > 0.10


def _decode(head: bytes, truncated: bool) -> tuple[str, str]:
    """先 strict 探测编码，再宽松解码（尾部可能正好切在半个多字节字符上）。"""
    candidates = ["utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"]
    probe = head[:-4] if truncated and len(head) > 4 else head
    for enc in candidates:
        try:
            probe.decode(enc)
            return head.decode(enc, errors="replace"), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return head.decode("utf-8", errors="replace"), "utf-8"


def read_text(path: str, max_bytes: int = MAX_TEXT_BYTES,
              max_lines: int = MAX_TEXT_LINES, cancel=None) -> dict:
    """返回 {text, encoding, lines, truncated, binary, size, error}。"""
    out = {"text": "", "encoding": "", "lines": 0, "truncated": False,
           "binary": False, "size": 0, "error": ""}
    try:
        size = os.stat(path).st_size
    except PermissionError:
        out["error"] = "没有权限读取此文件"
        return out
    except (FileNotFoundError, NotADirectoryError):
        out["error"] = "文件不存在"
        return out
    except OSError as exc:
        out["error"] = f"读取失败：{exc}"
        return out
    limit = min(size, max_bytes)
    try:
        with open(path, "rb") as f:
            head = f.read(limit)
    except OSError as exc:
        out["error"] = f"读取失败：{exc}"
        return out
    if cancel is not None and cancel.is_cancelled():
        out["error"] = "已取消"
        return out
    return decode_text(head, size, max_bytes, max_lines)


def decode_text(head: bytes, size: int, max_bytes: int = MAX_TEXT_BYTES,
                max_lines: int = MAX_TEXT_LINES) -> dict:
    """已读到的字节 → 文本结果。文件与压缩包内成员共用这一份解码逻辑。

    head 是「前 min(size, max_bytes) 字节」，size 是完整大小（用来判断是否截断）。
    """
    out = {"text": "", "encoding": "", "lines": 0, "truncated": False,
           "binary": False, "size": int(size or 0), "error": ""}
    if not head:
        return out

    bom_encoding = ""
    for prefix, enc in _BOMS:
        if head.startswith(prefix):
            bom_encoding = enc
            break
    if not bom_encoding and looks_binary(head):
        out["binary"] = True
        return out

    truncated = size > max_bytes
    if bom_encoding:
        try:
            text = head.decode(bom_encoding, errors="replace")
            encoding = bom_encoding
        except (UnicodeDecodeError, LookupError):
            text, encoding = _decode(head, truncated)
    else:
        text, encoding = _decode(head, truncated)

    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    out["text"] = "\n".join(lines)
    out["encoding"] = encoding
    out["lines"] = len(lines)
    out["truncated"] = truncated
    return out
