"""压缩包 FileSource：让面板把压缩包当目录逛（零 Qt）。

`zip+file:///path/a.zip!/dir/sub/` 这种 vpath 在 vpath.py 里早就定义好了，这里只是
把它接到 FileSource 注册表上 —— 面板、列表模型、QML 一行都不用改。

能力位是这套设计的关键：压缩包内部**只读**（要改就先解压出来），所以 canWrite /
canPaste / canRename / canTrash 全 False，而 canExtract 为 True。QML 的右键菜单直接
绑这些位，不写「if 是压缩包」的分支。
"""

from __future__ import annotations

import os

from .. import archives, vpath
from ..entries import make_entry
from . import FileSource


class ArchiveSource(FileSource):
    scheme = vpath.ARCHIVE            # "zip+file"

    def can_handle(self, v: str) -> bool:
        try:
            return vpath.parse(v)[0] == vpath.ARCHIVE
        except (ValueError, TypeError):
            return False

    def normalize(self, v: str) -> str:
        return vpath.normalize(v)

    def _parts(self, v: str):
        """(容器本地路径, 容器内相对路径)。非法 vpath 返回 ("", "")。"""
        try:
            scheme, container, inner = vpath.parse(v)
        except (ValueError, TypeError):
            return "", ""
        if scheme != vpath.ARCHIVE or not container:
            return "", ""
        return container, inner or ""

    def exists(self, v: str) -> bool:
        container, inner = self._parts(v)
        if not container or not os.path.isfile(container):
            return False
        if not inner:
            return True
        try:
            members, _enc = archives.read_index(container)
        except Exception:
            return False
        prefix = inner.strip("/")
        for m in members:
            if m.name == prefix or m.name.startswith(prefix + "/"):
                return True
        return False

    def is_dir(self, v: str) -> bool:
        container, inner = self._parts(v)
        if not container or not os.path.isfile(container):
            return False
        if not inner:
            return True
        try:
            members, _enc = archives.read_index(container)
        except Exception:
            return False
        prefix = inner.strip("/")
        for m in members:
            if m.is_dir and m.name == prefix:
                return True
            if m.name.startswith(prefix + "/"):
                return True          # 有子项的隐式目录
        return False

    def list_dir(self, v: str, show_hidden: bool = False, cancel=None):
        """返回 (list[Entry], 错误文案|None, 是否截断)。"""
        container, inner = self._parts(v)
        if not container or not os.path.isfile(container):
            return [], "压缩包不存在", False
        return archives.list_dir(container, inner, v, show_hidden, cancel)

    def parent(self, v: str) -> str:
        return vpath.parent(v)

    def join(self, v: str, name: str) -> str:
        return vpath.join(v, name)

    def entry(self, v: str):
        container, inner = self._parts(v)
        name = vpath.basename(v) or (os.path.basename(container) if container else "/")
        is_dir = self.is_dir(v) if inner else True
        size, mtime = 0, 0.0
        if inner and container:
            try:
                for m in archives.read_index(container)[0]:
                    if m.name == inner.strip("/"):
                        size, mtime = (0 if m.is_dir else m.size), m.mtime
                        break
            except Exception:
                pass
        return make_entry(name, v, is_dir, size, mtime)

    def capabilities(self, v: str) -> dict:
        """压缩包内部只读。写操作位全关，QML 据此隐藏菜单项、后端据此拒绝操作。"""
        return {
            "canWrite": False,
            "canPaste": False,
            "canRename": False,
            "canTrash": False,
            "canPreview": True,
            "canArchive": False,
            "canExtract": True,          # 选中条目可以解压到压缩包外面
            "canCompress": False,        # 包里的东西不能就地再压一个
            "archiveView": True,
            "sources": [vpath.ARCHIVE],
            "watch": False,              # 没有物理目录可挂 inotify
            "recursiveSearch": False,
            "thumbnails": False,
        }
