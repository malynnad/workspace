"""文本 / 代码预览器：限额读取 + 编码探测，二进制文件自动降级成信息卡。"""

from __future__ import annotations

import os

from .. import archives, vpath
from .base import PreviewPayload, PreviewSpec, common_meta
from .textread import read_text

OTHER_MAX = 64 * 1024          # 未知扩展名只在小文件上试探文本


class TextPreviewer:
    priority = 30

    def can_handle(self, entry) -> bool:
        if entry.is_dir:
            return False
        ext = os.path.splitext(entry.name.lower())[1]
        if ext == ".svg":
            return True                       # SVG 当源码看，不依赖 QtSvg 插件
        if entry.kind in ("text", "code"):
            return True
        return entry.kind == "other" and entry.size <= OTHER_MAX

    def prepare(self, entry) -> PreviewSpec:
        return PreviewSpec(kind="text", title=entry.name, meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        try:
            local = vpath.to_local(entry.vpath)
        except (ValueError, TypeError):
            # 压缩包里的文本/代码：直接从包里流式读前 N 字节，不用先解压。
            # 这是唯一支持「包内就地预览」的类型 —— 图片/PDF 还得先解出来。
            res = archives.read_text_member(entry.vpath, ctx.max_text_bytes,
                                            ctx.max_text_lines, ctx.cancel)
        else:
            res = read_text(local, max_bytes=ctx.max_text_bytes,
                            max_lines=ctx.max_text_lines, cancel=ctx.cancel)
        if res["binary"]:
            # 没扩展名的小文件其实是二进制：换成兜底信息卡，别显示乱码
            data = dict(common_meta(entry))
            data["hint"] = "这是二进制文件，暂不支持预览内容"
            data["error"] = ""
            return PreviewPayload("fallback", data)
        res["hint"] = ("仅显示前 %d KB / %d 行" %
                       (ctx.max_text_bytes // 1024, ctx.max_text_lines)
                       if res["truncated"] else
                       "编码 %s · %d 行" % (res["encoding"], res["lines"]))
        return PreviewPayload("text", res)

    def cleanup(self) -> None:
        return None
