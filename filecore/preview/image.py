"""图片预览器：view 阶段只探文件头，真正的解码交给 QML Image 异步做。"""

from __future__ import annotations

import os

from .. import vpath
from . import probe
from .base import PreviewPayload, PreviewSpec, common_meta

# 只收 Qt 内置图片插件在各平台都能可靠解码的格式。
# SVG / HEIC / AVIF / TIFF / ICO 需要额外插件，麒麟软渲染下不保证有，
# 因此 SVG 交给文本预览器看源码，其余走兜底信息卡。
BITMAP_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


class ImagePreviewer:
    priority = 40

    def can_handle(self, entry) -> bool:
        if entry.is_dir or entry.kind != "image":
            return False
        return os.path.splitext(entry.name.lower())[1] in BITMAP_EXT

    def prepare(self, entry) -> PreviewSpec:
        return PreviewSpec(kind="image", title=entry.name, meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        data = {"source": "", "width": 0, "height": 0, "format": "",
                "tooLarge": False, "error": ""}
        if entry.size > ctx.max_image_bytes:
            data["tooLarge"] = True
            data["error"] = "图片超过 32 MB，已跳过大图解码"
            return PreviewPayload("image", data)
        info = probe.image_info(vpath.to_local(entry.vpath))
        if info:
            data.update(info)
        else:
            data["error"] = "无法读取图片尺寸，仍尝试加载"
        # vpath 本身就是合法的 file:// URL（中文与空格已百分号编码），
        # QML Image 直接用，不需要再拼一次路径。
        data["source"] = str(entry.vpath)
        return PreviewPayload("image", data)

    def cleanup(self) -> None:
        return None
