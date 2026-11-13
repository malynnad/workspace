"""统一路径表示 vpath。

本地目录   file:///Users/dannylam/Documents
压缩包内   zip+file:///Users/dannylam/a.zip!/dir/sub/

`!/` 之后是容器内相对路径。上层（FilePanel / FileListModel / QML）只认字符串，
一律不碰 os.path，路径操作全部委托给这些函数与 FileSource。
"""

from __future__ import annotations

import os
from urllib.parse import quote, unquote

LOCAL = "file"
ARCHIVE = "zip+file"
SEP = "!/"


def _split_scheme(v: str) -> tuple[str, str]:
    scheme, mark, rest = v.partition("://")
    if not mark:
        raise ValueError(f"不是合法的 vpath: {v!r}")
    return scheme, rest


def parse(v: str) -> tuple[str, str | None, str]:
    """返回 (scheme, container, inner)。

    container 仅压缩包方案有值，是宿主机上压缩包的绝对路径（已解码）。
    inner 是容器内相对路径（已解码）；本地方案恒为空串，本地绝对路径放在
    container 之外、由 to_local() 取。
    """
    scheme, rest = _split_scheme(v)
    if scheme == ARCHIVE:
        if SEP in rest:
            raw_container, raw_inner = rest.split(SEP, 1)
            return scheme, unquote(raw_container), unquote(raw_inner)
        return scheme, unquote(rest), ""
    if scheme != LOCAL:
        # 未知方案也按 file 解析，由 FileSource 注册表决定能不能处理
        pass
    return scheme, None, unquote(rest)


def encode_path(abs_path: str) -> str:
    p = os.path.abspath(abs_path)
    return LOCAL + "://" + quote(p, safe="/")


def encode_archive(container: str, inner: str = "") -> str:
    c = os.path.abspath(container)
    i = (inner or "").strip("/")
    return ARCHIVE + "://" + quote(c, safe="/") + SEP + quote(i, safe="/")


def to_local(v: str) -> str:
    scheme, _, inner = parse(v)
    if scheme != LOCAL:
        raise ValueError(f"不是本地路径: {v!r}")
    return inner or "/"


def container_of(v: str) -> str:
    scheme, container, _ = parse(v)
    if scheme != ARCHIVE:
        raise ValueError(f"不是压缩包路径: {v!r}")
    return container or "/"


def normalize(v: str) -> str:
    """规整任意合法 vpath（去尾斜杠、重新百分号编码）。失败返回空串。"""
    try:
        scheme, container, inner = parse(v)
    except (ValueError, TypeError):
        return ""
    if scheme == ARCHIVE:
        return encode_archive(container or "/", inner)
    return encode_path(inner or "/")


def join(v: str, name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"非法名称: {name!r}")
    scheme, container, inner = parse(v)
    if scheme == ARCHIVE:
        combined = (inner.rstrip("/") + "/" + name).lstrip("/")
        return encode_archive(container or "/", combined)
    return encode_path(os.path.join(to_local(v), name))


def parent(v: str) -> str:
    """上一级。压缩包回到容器根后再上一级会退出到压缩包所在目录。"""
    scheme, container, inner = parse(v)
    if scheme == ARCHIVE:
        clean = inner.strip("/")
        if clean:
            parts = clean.split("/")
            return encode_archive(container or "/", "/".join(parts[:-1]))
        return encode_path(os.path.dirname(container or "/"))
    p = os.path.abspath(to_local(v))
    if p == os.path.dirname(p):
        return encode_path(p)
    return encode_path(os.path.dirname(p.rstrip("/")) or "/")


def basename(v: str) -> str:
    """vpath 最后一段名字（已解码）。根返回空串。

    给「列表里还没有这一项」的时刻兜底：刚新建的目录要立刻进入行内改名，
    那时扫描还没回来，按 vpath 取不到 Entry。
    """
    try:
        _scheme, _container, inner = parse(v)
    except (ValueError, TypeError):
        return ""
    text = (inner or "").rstrip("/")
    if not text or text == "/":
        return ""
    return text.rsplit("/", 1)[-1]


def display(v: str) -> str:
    """地址栏用的可读文本（解码百分号）。"""
    try:
        scheme, container, inner = parse(v)
    except (ValueError, TypeError):
        return v or ""
    if scheme == ARCHIVE:
        return (container or "/") + SEP + inner
    return inner or "/"


def is_hidden(name: str) -> bool:
    return name.startswith(".") and name not in (".", "..")


def from_user(text: str, home: str) -> str:
    """地址栏输入 → vpath。接受绝对路径 / file:// / ~/ 开头 / 复合 scheme。"""
    t = (text or "").strip().strip('"')
    if not t:
        return ""
    if "://" in t:
        return normalize(t)
    if t.startswith("~"):
        return encode_path(os.path.join(home, t[1:].lstrip("/")))
    if os.path.isabs(t):
        return encode_path(t)
    return encode_path(os.path.join(home, t))
