"""文件夹摘要预览器：只扫一层、不递归，超大目录可中途取消。"""

from __future__ import annotations

from .. import vpath
from ..entries import human_size, kind_label
from ..listing import scan_dir, sort_entries
from .base import PreviewPayload, PreviewSpec, common_meta


class DirectoryPreviewer:
    priority = 20

    def can_handle(self, entry) -> bool:
        return bool(entry.is_dir)

    def prepare(self, entry) -> PreviewSpec:
        return PreviewSpec(kind="directory", title=entry.name,
                           meta=common_meta(entry))

    def view(self, entry, ctx) -> PreviewPayload:
        entries, err, truncated = scan_dir(
            vpath.to_local(entry.vpath), entry.vpath, False, ctx.cancel)
        dirs = [e for e in entries if e.is_dir]
        files = [e for e in entries if not e.is_dir]
        counts: dict[str, int] = {}
        for e in files:
            counts[e.kind] = counts.get(e.kind, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        ordered = sort_entries(entries, 0, False)[:ctx.max_list_items]
        return PreviewPayload("directory", {
            "count": len(entries),
            "dirCount": len(dirs),
            "fileCount": len(files),
            "totalText": human_size(sum(e.size for e in files)),
            "kinds": [{"label": kind_label(k), "count": n} for k, n in top],
            "items": [{"name": e.name, "sizeText": e.size_text, "kind": e.kind,
                       "kindLabel": kind_label(e.kind), "isDir": e.is_dir}
                      for e in ordered],
            "error": err or "",
            "truncated": bool(truncated),
        })

    def cleanup(self) -> None:
        return None
