"""Office 文档预览器：版面走 LibreOffice 转 PDF，纯文字走标准库拆包。

两条路的分工见 `filecore/office.py` 的模块头。这里的核心决定是**版面路径直接
返回 `kind="pdf"`** —— 转出来的就是一份真 PDF，交给 `pdf_pages` + `image://pdfpage`
那条现成管线，页数、翻页、缩放三档、页图 LRU 全部免费复用，一行都不用改。

代价是 `kind` 被占用成 "pdf"，预览窗的标题图标会变成 PDF 的灰青。所以 payload 里
多带一个 `sourceKind: "office"`，QML 侧按它决定图标和类型文字的着色（见
PreviewWindow.qml 的 pvSourceKind）。
"""

from __future__ import annotations

import os

from .. import office, vpath
from .base import PreviewPayload, PreviewSpec, common_meta

# 元信息行：键名 → 中文标签。顺序就是显示顺序。
_STAT_LABELS = (
    ("paragraphs", "段落"), ("sheets", "工作表"), ("slides", "幻灯片"),
    ("rows", "行"), ("words", "字数"), ("tables", "表格"),
    ("images", "图片"), ("title", "标题"), ("author", "作者"),
)


def _detail_rows(stats: dict) -> list:
    """把统计字典摊成 [[标签, 值], ...]，QML 直接按两列画。"""
    out = []
    for key, label in _STAT_LABELS:
        value = stats.get(key)
        if value in (None, "", 0):
            continue
        out.append([label, str(value)])
    return out[:8]


class OfficePreviewer:
    priority = 33      # PDF(35) 之下、文本(30) 之上：按 kind 或扩展名精确命中

    def can_handle(self, entry) -> bool:
        if entry.is_dir:
            return False
        if entry.kind == "office":
            return True
        return office.is_office(entry.name)

    def prepare(self, entry) -> PreviewSpec:
        """<10ms：只拼标题，不读文件、不探测工具。"""
        return PreviewSpec(kind="office", title=entry.name, meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        data = {
            "sourceKind": "office",
            "error": "", "hint": "", "text": "",
            "format": office.format_label(entry.name),
            "truncated": False, "encrypted": False,
            "layout": False, "detail": [],
        }
        # 默认开：设置面板没写过时按「有 soffice 就用版面」处理
        layout_on = bool(getattr(ctx, "office_layout", True))

        try:
            local = vpath.to_local(entry.vpath)
        except (ValueError, TypeError, NotImplementedError):
            data["hint"] = "压缩包内的 Office 文档需要先解压到本地才能预览"
            return PreviewPayload("office", data)
        if not os.path.isfile(local):
            data["error"] = "文件不存在或无法读取"
            return PreviewPayload("office", data)

        # 先抽文字。两个作用：任何情况下都有内容可展示；加密文档能立刻识别出来，
        # 省掉一次白跑 1~8 秒的 LibreOffice 转换。
        max_chars = min(office.MAX_CHARS, max(4096, int(ctx.max_text_bytes)))
        text = office.read_text(local, max_chars, ctx.cancel)
        data["text"] = text.get("text", "")
        data["truncated"] = bool(text.get("truncated"))
        data["encrypted"] = bool(text.get("encrypted"))
        data["format"] = text.get("format", data["format"])
        data["detail"] = _detail_rows(text.get("stats") or {})
        if text.get("hint"):
            data["hint"] = text["hint"]

        if data["encrypted"]:
            # 加密的 OOXML 连 soffice 也要密码，转换只会白等
            return PreviewPayload("office", data)

        if not layout_on:
            if not data["text"]:
                data["hint"] = (data["hint"] or "没有可提取的文字")
            data["hint"] = (data["hint"] + "；" if data["hint"] else "") + \
                "版面预览已关闭，可到设置里打开「Office 版面」"
            return PreviewPayload("office", data)

        # 有缓存就直接用，**不看有没有 soffice**：上次转过、这次 LibreOffice
        # 被卸了 / 换了机器，缓存里那份 PDF 依然是对的，没道理退回纯文字。
        have_cache = bool(office.cached_pdf(local))
        if not have_cache and not office.can_convert():
            lack = "没找到 LibreOffice，无法渲染版面"
            if not data["text"]:
                data["hint"] = lack + "；装一个（apt install libreoffice-writer）就能看到文档样张"
            else:
                data["hint"] = lack + "，当前只显示文字"
            return PreviewPayload("office", data)

        if ctx.cancel is not None and ctx.cancel.is_cancelled():
            data["hint"] = "已取消"
            return PreviewPayload("office", data)

        pdf, err = office.convert_pdf(local, ctx.cancel)
        if pdf:
            # 关键一行：交出 PDF 路径、kind 换成 pdf，整条 PDF 管线接管。
            # sourceKind 让预览窗仍然认得出「这是 Word 不是 PDF」。
            return PreviewPayload("pdf", {
                "localPath": pdf,
                "sourceKind": "office",
                "fromOffice": True,
                "detail": data["detail"],
                "format": data["format"],
            })

        # 转换失败（缺工具以外的原因）：把文字拿出来顶住，别给用户一片空白
        if err:
            data["hint"] = (("版面渲染失败：%s" % err) if not data["text"]
                            else "版面渲染失败（%s），当前只显示文字" % err)
        return PreviewPayload("office", data)

    def cleanup(self) -> None:
        return None
