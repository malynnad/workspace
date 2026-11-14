"""兜底信息卡：所有没被前几种预览器接住的文件都走这里（只读元信息）。"""

from __future__ import annotations

from .. import vpath
from .base import PreviewPayload, PreviewSpec, common_meta

_OFFICE_HINT = "这份文档读不出来（格式不支持、文件已损坏，或缺少 LibreOffice）"

HINTS = {
    "pdf": "这份 PDF 无法预览（文件可能已损坏）",
    "archive": "压缩包内容读不出来（格式不支持，或文件已损坏）",
    # office 正常情况下由 OfficePreviewer 接住（M2），到这儿说明连纯 Python 拆包
    # 都失败了 —— 别再说「暂未接入」，那是旧文案。
    # word/excel/ppt 是 M2 之后拆出来的三个 kind（见 entries.py），文案共用一条；
    # "office" 留着兜底，旧状态文件里可能还存着它。
    "word": _OFFICE_HINT,
    "excel": _OFFICE_HINT,
    "ppt": _OFFICE_HINT,
    "office": _OFFICE_HINT,
    "video": "视频预览暂未接入",
    "audio": "音频预览暂未接入",
    "font": "字体预览暂未接入",
    "image": "此图片格式在当前系统缺少解码插件，暂不能预览",
}


class FallbackPreviewer:
    priority = 0

    def can_handle(self, entry) -> bool:
        return True

    def prepare(self, entry) -> PreviewSpec:
        return PreviewSpec(kind="fallback", title=entry.name,
                           meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        data = dict(common_meta(entry))
        data["kind"] = entry.kind
        data["hint"] = _hint(entry)
        data["error"] = ""
        return PreviewPayload("fallback", data)

    def cleanup(self) -> None:
        return None


def _hint(entry) -> str:
    try:
        in_archive = vpath.parse(entry.vpath)[0] == vpath.ARCHIVE
    except (ValueError, TypeError):
        in_archive = False
    if in_archive and entry.kind not in ("text", "code"):
        # 包内的图片/PDF/视频都没有物理路径可交给解码器。文本/代码自己会走文本预览器，
        # 到不了这里；能到这儿的都是「必须先解压」的。
        return "压缩包内的这类文件需要先解压到本地才能预览"
    return HINTS.get(entry.kind, "此类型暂不支持预览内容")
