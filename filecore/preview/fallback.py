"""兜底信息卡：所有没被前三种预览器接住的文件都走这里（只读元信息）。"""

from __future__ import annotations

from .base import PreviewPayload, PreviewSpec, common_meta

HINTS = {
    "pdf": "PDF 预览将在 M2 加入",
    "archive": "压缩包内容预览将在 M2 加入",
    "office": "办公文档预览暂未接入",
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
        data["hint"] = HINTS.get(entry.kind, "此类型暂不支持预览内容")
        data["error"] = ""
        return PreviewPayload("fallback", data)

    def cleanup(self) -> None:
        return None
