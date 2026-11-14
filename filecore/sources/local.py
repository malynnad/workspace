"""本地目录 FileSource。

list_dir 直接复用 listing.scan_dir 的 os.scandir 单趟逻辑，产出的 Entry.vpath
是完整可回传的 vpath —— M2 的 ArchiveSource 只要产出同样字段即可无缝替换。
"""

from __future__ import annotations

import os

from .. import listing, vpath
from ..entries import make_entry
from . import FileSource


class LocalSource(FileSource):
    scheme = "file"

    def can_handle(self, v: str) -> bool:
        try:
            return vpath.parse(v)[0] == "file"
        except (ValueError, TypeError):
            return False

    def normalize(self, v: str) -> str:
        return vpath.normalize(v)

    def exists(self, v: str) -> bool:
        if not self.can_handle(v):
            return False
        try:
            return os.path.exists(vpath.to_local(v))
        except (ValueError, OSError):
            return False

    def is_dir(self, v: str) -> bool:
        if not self.can_handle(v):
            return False
        try:
            return os.path.isdir(vpath.to_local(v))
        except (ValueError, OSError):
            return False

    def list_dir(self, v: str, show_hidden: bool = False, cancel=None):
        """返回 (list[Entry], 错误文案|None, 是否截断)。"""
        return listing.scan_dir(vpath.to_local(v), v, show_hidden, cancel)

    def parent(self, v: str) -> str:
        return vpath.parent(v)

    def join(self, v: str, name: str) -> str:
        return vpath.join(v, name)

    def entry(self, v: str):
        p = vpath.to_local(v)
        is_dir = os.path.isdir(p)
        try:
            st = os.stat(p)
            size = 0 if is_dir else st.st_size
            mtime = st.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        name = os.path.basename(p.rstrip("/")) or "/"
        return make_entry(name, v, is_dir, size, mtime)

    def capabilities(self, v: str) -> dict:
        """本地目录的能力位。M1c 已接上写操作，四个写位翻 True；
        具体「这一下点不点得动」由上层结合选中数、剪贴板内容、面板 busy 判断，
        这里只回答「这个源支不支持这类操作」。

        M2 加的两个压缩包位：canExtract = 选中的压缩包能解压（解到本地），
        canCompress = 能在这里新建 zip。archiveView 用来区分「在压缩包里」和
        「在真实目录里」—— 右键菜单里那几项压缩/解压条目按它分叉。
        """
        return {
            "canWrite": True,
            "canPaste": True,
            "canRename": True,
            "canTrash": True,
            "canPreview": True,
            "canArchive": False,          # M3 的命令行槽位用，别动
            "canExtract": True,
            "canCompress": True,
            "archiveView": False,
            "sources": ["file"],
            "watch": False,
            "recursiveSearch": False,
            "thumbnails": False,
        }
