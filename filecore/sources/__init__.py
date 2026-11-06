"""FileSource 抽象与注册表。

设计参照 Double Commander 的 TFileSource（见 reference/插件体系研究笔记）：
本地目录和压缩包是同一个接口，面板根本不知道自己在看哪一种。

M1a 只注册 LocalSource；M2 新建 archive.py 并 register(ArchiveSource()) 一行即可，
上层 FilePanel / FileListModel / QML 一行都不用改。
"""

from __future__ import annotations

from typing import Protocol

from .. import vpath


class UnknownScheme(Exception):
    """该 scheme 还没有对应的 FileSource（例如 M2 之前的 zip+file://）。"""


class FileSource(Protocol):
    scheme: str

    def can_handle(self, v: str) -> bool: ...
    def normalize(self, v: str) -> str: ...
    def exists(self, v: str) -> bool: ...
    def is_dir(self, v: str) -> bool: ...
    def list_dir(self, v: str, show_hidden: bool, cancel=None): ...
    def parent(self, v: str) -> str: ...
    def join(self, v: str, name: str) -> str: ...
    def entry(self, v: str): ...
    def capabilities(self, v: str) -> dict: ...


SOURCE_REGISTRY: dict[str, FileSource] = {}


def register(source: FileSource) -> None:
    """按 scheme 覆盖注册，重复调用是幂等的。"""
    SOURCE_REGISTRY[source.scheme] = source


def resolve(v: str) -> FileSource:
    scheme = vpath.parse(v)[0]
    source = SOURCE_REGISTRY.get(scheme)
    if source is None:
        raise UnknownScheme(f"暂不支持该路径类型：{scheme}")
    return source


def capabilities_of(v: str) -> dict:
    try:
        return resolve(v).capabilities(v)
    except UnknownScheme:
        return {"sources": list(SOURCE_REGISTRY.keys())}
