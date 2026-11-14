"""压缩包预览器：统计 + 顶层前 12 项 + 能不能进去逛的提示。

payload 形状**故意与目录摘要一致**（count / dirCount / fileCount / totalText / kinds /
items），QML 侧复用同一块 UI，只多显示一行「包大小 / 是否加密 / 怎么解压」。
M2 的取数全在 filecore/archives.py，这里只负责翻译成 QML 能吃的纯数据。
"""

from __future__ import annotations

import os

from .. import archives, vpath
from ..entries import human_size, kind_label
from .base import PreviewPayload, PreviewSpec, common_meta


class ArchivePreviewer:
    priority = 25       # 在文本(30)之下、目录(20)之上：只按扩展名命中，不误抢

    def can_handle(self, entry) -> bool:
        if entry.is_dir:
            return False
        return bool(archives.format_of(entry.name or entry.vpath))

    def prepare(self, entry) -> PreviewSpec:
        return PreviewSpec(kind="archive", title=entry.name,
                           meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        try:
            local = vpath.to_local(entry.vpath)
        except (ValueError, TypeError):
            # 压缩包里的压缩包：没有物理路径可读，得先解出来
            data = dict(common_meta(entry))
            data["hint"] = "压缩包内的压缩包需要先解压到本地才能查看"
            data["error"] = ""
            return PreviewPayload("archive", data)

        info = archives.summary(local, ctx.cancel)
        try:
            packed = human_size(os.path.getsize(local))
        except (OSError, ValueError, TypeError):
            packed = ""

        counts: dict[str, int] = {}
        for it in info.get("items") or []:
            kind = "folder" if it.get("isDir") else it.get("kind", "other")
            counts[kind] = counts.get(kind, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        format_name = {"zip": "ZIP", "tar": "TAR", "rar": "RAR", "7z": "7z",
                       "gzip": "GZip"}.get(info.get("format", ""), info.get("format", ""))

        data = dict(common_meta(entry))
        data.update({
            "format": format_name,
            "packedText": packed,
            "count": int(info.get("count", 0)),
            "dirCount": int(info.get("dirCount", 0)),
            "fileCount": int(info.get("fileCount", 0)),
            "totalText": human_size(info.get("total", 0)),
            "encrypted": bool(info.get("encrypted")),
            "truncated": bool(info.get("truncated")),
            "kinds": [{"label": kind_label(k), "count": n} for k, n in top],
            "items": [_item(e) for e in _top_entries(local, ctx)],
            "error": info.get("error", ""),
            "hint": _hint(info, local),
        })
        return PreviewPayload("archive", data)

    def cleanup(self) -> None:
        return None


def _top_entries(local: str, ctx):
    """顶层条目（最多 12 条）。复用 archives.list_dir，命名与目录预览保持一致。"""
    base = vpath.encode_archive(local, "")
    entries, _err, _tr = archives.list_dir(local, "", base, False, ctx.cancel)
    return entries[:ctx.max_list_items]


def _item(entry) -> dict:
    return {"name": entry.name, "sizeText": entry.size_text, "kind": entry.kind,
            "kindLabel": kind_label(entry.kind), "isDir": entry.is_dir}


def _hint(info: dict, local: str) -> str:
    if info.get("error"):
        return info["error"]
    if info.get("encrypted"):
        return "包已加密：目录能看，解压暂不支持（M3 再补密码框）"
    if not archives.can_browse(local) or archives.format_of(local) == "gzip":
        return "这个格式不能进去浏览；右键「解压到此处」可以取出内容"
    if info.get("truncated"):
        return "条目超过 10 万，仅统计了前一部分 · 双击可进入浏览"
    return "双击进入浏览 · 右键「解压到此处」取出内容"
