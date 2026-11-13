"""写操作层（复制 / 移动 / 回收站 / 命名 / 剪贴板），**零 Qt 依赖**。

和 `filecore/preview/` 一样的分工：这里只做文件系统与数据格式，绝不 import PySide6。
Qt 那一半（QClipboard、线程池、信号）全在 `file_bridge.py`。
"""

from .clipboard import MIME_GNOME, MIME_TEXT, MIME_URI, encode, parse
from .naming import split_ext, unique_name, validate_name
from .transfer import (human_error, make_directory, rename_item, run_transfer)
from .trash import permanent_delete, trash_path, trash_paths

__all__ = [
    "MIME_GNOME", "MIME_TEXT", "MIME_URI", "encode", "parse",
    "split_ext", "unique_name", "validate_name",
    "human_error", "make_directory", "rename_item", "run_transfer",
    "permanent_delete", "trash_path", "trash_paths",
]
