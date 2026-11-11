"""PDF 预览器：filecore 这半边只做零 Qt 的轻活 —— 校验文件头、判一下加密标记。

真正的页面栅格化必须用 QPdfDocument（QtPdf），那是 Qt 类，不能出现在 filecore 里。
所以这里只产出「要渲染什么」，由 preview_provider 的 _view_job 在工作线程里接上
pdf_render.render_pdf() 把页图补进 payload —— 和数据层/协议层保持零 Qt 的边界。
"""

from __future__ import annotations

import os

from .. import vpath
from .base import PreviewPayload, PreviewSpec, common_meta

PDF_EXT = {".pdf"}
MAX_HEAD_BYTES = 1024


class PdfPreviewer:
    priority = 35      # 在文本(30)之上、图片(40)之下：按扩展名精确命中，不会误抢

    def can_handle(self, entry) -> bool:
        if entry.is_dir:
            return False
        if entry.kind == "pdf":
            return True
        return os.path.splitext(entry.name.lower())[1] in PDF_EXT

    def prepare(self, entry) -> PreviewSpec:
        return PreviewSpec(kind="pdf", title=entry.name, meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        data = {"localPath": "", "pageCount": 0, "pages": [], "error": "",
                "hint": "", "truncated": False, "encrypted": False}
        try:
            local = vpath.to_local(entry.vpath)
        except (ValueError, TypeError, NotImplementedError):
            data["error"] = "压缩包内的 PDF 暂不支持预览"
            return PreviewPayload("pdf", data)
        if not os.path.isfile(local):
            data["error"] = "文件不存在或无法读取"
            return PreviewPayload("pdf", data)

        # 只读文件头做一次廉价体检：不是 %PDF 就不必惊动 PDF 引擎。
        try:
            with open(local, "rb") as f:
                head = f.read(MAX_HEAD_BYTES)
        except OSError as exc:
            data["error"] = f"无法读取文件：{exc.strerror or exc}"
            return PreviewPayload("pdf", data)
        if not head.lstrip()[:4] == b"%PDF":
            data["error"] = "文件头不是 %PDF，文件可能已损坏或只是改了后缀"
            return PreviewPayload("pdf", data)

        data["localPath"] = local
        # /Encrypt 在 trailer 里，位置不固定；这里只在头部 1KB 里做个廉价提示，
        # 真正的判定交给 QPdfDocument（status / pageCount），见 pdf_render。
        if b"/Encrypt" in head:
            data["encrypted"] = True
        return PreviewPayload("pdf", data)

    def cleanup(self) -> None:
        return None
