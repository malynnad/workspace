"""工作台文件管理器 · 纯逻辑层（零 Qt 依赖）。

这里只做路径、条目、枚举、排序、状态与协议定义。
任何模块都不得 import PySide6 / PyQt5 —— Qt 适配放在 file_bridge.py 等文件。
旧版 PyQt5 程序（/Users/dannylam/WorkBuddy/FileManager/filemanager.py）只作为
逻辑拷贝来源，绝不 import：它顶层就引入了 PyQt5。
"""

__version__ = "1.0"


def bootstrap():
    """注册内置 FileSource 与预览器。显式调用，避免包初始化期的循环依赖。"""
    from .sources import register
    from .sources.archive import ArchiveSource
    from .sources.local import LocalSource
    from .preview import bootstrap as register_previewers

    register(LocalSource())
    register(ArchiveSource())          # M2：压缩包当目录逛（zip+file://）
    register_previewers()
