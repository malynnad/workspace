"""预览器包：协议在 base.py，内置四种预览器由 bootstrap() 注册。

M1a 只留空注册表；M1b 由 filecore.bootstrap() 调本函数一次即可。
M3 的第三方预览插件只要实现 can_handle / prepare / view / cleanup 并
调用 base.register(...)，优先级高于内置兜底就能接管，不需要改上层。
"""

from __future__ import annotations


def bootstrap():
    """注册内置预览器。重复调用是幂等的（先 clear 再注册）。"""
    from .archive import ArchivePreviewer
    from .base import clear, register
    from .directory import DirectoryPreviewer
    from .fallback import FallbackPreviewer
    from .image import ImagePreviewer
    from .office import OfficePreviewer
    from .pdf import PdfPreviewer
    from .text import TextPreviewer

    clear()
    for cls in (ImagePreviewer, PdfPreviewer, OfficePreviewer, TextPreviewer,
                ArchivePreviewer, DirectoryPreviewer, FallbackPreviewer):
        register(cls())
