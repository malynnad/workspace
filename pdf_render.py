"""PDF 页栅格化（Qt 层）：把每一页渲染成 PNG 落到缓存目录，QML 用 Image 读文件。

为什么落盘而不是把 QImage 交给 QML：
  1. 项目约定 payload 只装纯数据（str/int/list），工作线程不往上递 Qt 图形对象；
  2. 同一个 PDF 反复打开（逐个审核的典型用法）直接命中缓存，不再重渲染；
  3. QML 侧用 ListView + Image(cache:false)，只有看得见的那一两页占内存 ——
     A4 页 1400px 宽解码后约 11 MB，60 页全塞进去就是 600 MB，麒麟 8GB 扛不住。

两段式渲染（由 preview_provider 调度）：
  第一段只渲首屏 1 页（预算 0.5s）→ 窗口里立刻有内容；
  第二段接着渲剩下的（预算 3s）。靠 manifest 记住「渲到第几页」，
  第二段从断点续渲，不重复算第一页。manifest 每页写一次，中途退出也不会坏。

缓存目录：$XDG_CACHE_HOME/workspace/pdf/<sha1>/p000.png（WORKSPACE_CACHE_HOME 可覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QSize, QUrl
from PySide6.QtPdf import QPdfDocument

CACHE_VERSION = 1          # 渲染参数一改就加一，旧缓存自然作废
RENDER_W = 1400            # 页宽（像素）：半屏 960 宽在 1x / 1.25x 下都够清晰
MAX_PX = 6_000_000         # 单页像素上限，防畸形页面吃爆内存
MAX_PAGES = 80             # 单个 PDF 最多渲这么多页（再长也没人靠预览翻）
KEEP_DAYS = 7              # 缓存保留天数
KEEP_DIRS = 120            # 缓存目录数量上限

FIRST_PAGES = 1            # 第一段渲染页数
FIRST_BUDGET = 0.5         # 第一段时间预算（秒）
FULL_BUDGET = 3.0          # 第二段时间预算（秒）

_purged = False


def cache_root() -> Path:
    override = os.environ.get("WORKSPACE_CACHE_HOME")
    if override:
        return Path(override) / "pdf"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "workspace" / "pdf"


def _check_cancel(cancel) -> bool:
    try:
        return bool(cancel is not None and cancel.is_cancelled())
    except Exception:
        return False


def _purge_once(root: Path) -> None:
    """启动后第一次渲染时清一次旧缓存（按 mtime，7 天 / 120 个目录封顶）。"""
    global _purged
    if _purged:
        return
    _purged = True
    try:
        entries = [(p, p.stat().st_mtime) for p in root.iterdir() if p.is_dir()]
    except OSError:
        return
    now = time.time()
    entries.sort(key=lambda it: it[1], reverse=True)
    for idx, (path, mtime) in enumerate(entries):
        if idx < KEEP_DIRS and now - mtime < KEEP_DAYS * 86400:
            continue
        shutil.rmtree(path, ignore_errors=True)


def _read_manifest(work: Path) -> dict | None:
    try:
        meta = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("version") != CACHE_VERSION or meta.get("renderW") != RENDER_W:
        return None
    pages = meta.get("pages")
    if not isinstance(pages, list):
        return None
    # 文件被外部清掉的页直接丢掉，按断点重渲
    kept = [p for p in pages if (work / str(p.get("file", ""))).is_file()]
    meta["pages"] = kept
    return meta


def _write_manifest(work: Path, total: int, pages: list[dict]) -> None:
    try:
        (work / "manifest.json").write_text(json.dumps({
            "version": CACHE_VERSION, "renderW": RENDER_W,
            "total": total, "pages": pages}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass          # 写不进缓存不影响本次显示，只是下次要重渲


def _to_source(work: Path, page: dict) -> dict:
    return {"source": QUrl.fromLocalFile(str(work / str(page["file"]))).toString(),
            "w": int(page["w"]), "h": int(page["h"]), "index": int(page["index"])}


def _error_name(doc) -> str:
    err = doc.error()
    return getattr(err, "name", "") or str(err)


def _status_error(doc) -> str:
    """把 QPdfDocument 的 Error 翻成人话，别把坏文件说成「已加密」。"""
    name = _error_name(doc)
    if name in ("IncorrectPassword", "UnsupportedSecurityScheme"):
        return "PDF 已加密，需要密码才能预览"
    if name == "FileNotFound":
        return "文件不存在或无法读取"
    if name in ("InvalidFileFormat", "Unknown"):
        return "PDF 无法解析，文件可能已损坏"
    if name == "DataNotYetAvailable":
        return "PDF 仍在装载，稍后再试"
    return f"PDF 无法打开（{name}）"


def render_pdf(local_path: str, cancel=None, first_only: bool = False,
               password: str = "") -> dict:
    """在工作线程里调用：返回纯数据（不含任何 Qt 图形对象）。

    第一段（first_only=True）只渲首屏一页；第二段续渲剩下的。
    返回的 pending=True 表示还有页没渲完，调用方可以再排一次。

    password：加密 PDF 的打开密码。**必须在 load() 之前 setPassword()** ——
    QtPdf 只在装载时用一次密码，load 失败后再设不会重试（实测：状态一直停在
    Error/IncorrectPassword，把密码改对了也一样）。所以解锁只能整份重开一次；
    有缓存时这次重开几乎瞬间完成。
    密码本身不进缓存 key：同一份文件的页图与密码无关，也不该因为换个密码就重渲一遍。
    """
    out = {"pageCount": 0, "rendered": 0, "pages": [], "pending": False,
           "truncated": False, "encrypted": False, "error": "", "note": "",
           "needsPassword": False, "passwordError": ""}
    try:
        stat = os.stat(local_path)
    except OSError as exc:
        out["error"] = f"无法读取文件：{exc.strerror or exc}"
        return out

    root = cache_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        out["error"] = f"缓存目录不可用：{exc.strerror or exc}"
        return out
    _purge_once(root)

    key = hashlib.sha1(
        f"{CACHE_VERSION}|{local_path}|{stat.st_size}|{stat.st_mtime_ns}|{RENDER_W}"
        .encode("utf-8", "surrogatepass")).hexdigest()[:20]
    work = root / key

    meta = _read_manifest(work)
    pages: list[dict] = list(meta["pages"]) if meta else []
    start = len(pages)
    total = int(meta["total"]) if meta else 0
    if total and start >= total:                   # 整份都渲过：秒开
        # 加密文件也一样：页图已经在盘上，就不再要密码（逐个审核时尤其重要，
        # 同一个文件翻来翻去不该反复输密码）。
        out.update(pageCount=total, rendered=start, pages=[_to_source(work, p) for p in pages],
                   note=_note(total, start))
        return out

    doc = QPdfDocument()
    if password:
        doc.setPassword(password)                  # 必须先于 load，见函数注释
    doc.load(local_path)
    if doc.status() != QPdfDocument.Status.Ready:
        if pages:                                  # 有半套缓存但打不开：把旧的给出去
            # 加密文件且这次没带对密码时就是这条路：能看多少给多少（note 会写明
            # 「已渲染 N 页」）。下次重开只要带密码（provider 会记住本次会话的），
            # 就从这个断点接着渲完 —— 所以这里不设 pending，免得无谓地重试 3 轮。
            out.update(pageCount=total or start, rendered=start,
                       pages=[_to_source(work, p) for p in pages],
                       note=_note(total or start, start))
            return out
        name = _error_name(doc)
        if name in ("IncorrectPassword", "UnsupportedSecurityScheme"):
            # 交给 QML 弹密码框，而不是在内容区留一行错误文字。
            out["needsPassword"] = True
            out["encrypted"] = True
            if name == "UnsupportedSecurityScheme":
                out["passwordError"] = "这个 PDF 的加密方式暂不支持，请用其他阅读器打开"
            elif password:
                # QtPdf 对「没给密码」和「密码错」都报同一个 IncorrectPassword，
                # 想区分只能看自己有没有给过密码。
                out["passwordError"] = "密码不正确，请重新输入"
            return out
        out["error"] = _status_error(doc)
        return out

    fresh_total = int(doc.pageCount())
    if fresh_total <= 0:
        out["error"] = "PDF 没有任何页面"
        return out
    if fresh_total != total:                       # 文件变了：缓存作废重来
        shutil.rmtree(work, ignore_errors=True)
        pages, start, total = [], 0, fresh_total
    else:
        total = fresh_total
    out["pageCount"] = total
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        out["error"] = f"缓存目录不可用：{exc.strerror or exc}"
        return out

    limit = min(total, MAX_PAGES)
    budget = FIRST_BUDGET if first_only else FULL_BUDGET
    step_limit = min(limit, start + FIRST_PAGES) if first_only else limit

    started = time.monotonic()
    index = start
    while index < step_limit:
        if _check_cancel(cancel):
            break
        if index > start and time.monotonic() - started > budget:
            break
        page = _render_page(doc, work, index, total)
        if page is not None:
            pages.append(page)
        index += 1
        _write_manifest(work, total, pages)        # 每页落一次，断点可续

    out["pages"] = [_to_source(work, p) for p in pages]
    out["rendered"] = len(pages)
    out["pending"] = len(pages) < total
    out["truncated"] = len(pages) < total
    out["note"] = _note(total, len(pages))
    doc.close()
    return out


def _render_page(doc, work: Path, index: int, total: int) -> dict | None:
    try:
        size = doc.pagePointSize(index)
    except Exception:
        size = None
    w, h = RENDER_W, int(round(RENDER_W * 297 / 210))
    if size is not None and size.width() > 0 and size.height() > 0:
        h = int(round(RENDER_W * size.height() / size.width()))
    if w * h > MAX_PX:                             # 畸形长条页等比压回上限内
        factor = (MAX_PX / float(w * h)) ** 0.5
        w, h = max(1, int(w * factor)), max(1, int(h * factor))
    image = doc.render(index, QSize(w, h))
    if image.isNull():
        return None
    name = f"p{index:03d}.png"
    try:
        if not image.save(str(work / name), "PNG"):
            return None
    except Exception:
        return None
    return {"file": name, "w": image.width(), "h": image.height(), "index": index}


def _note(total: int, rendered: int) -> str:
    if rendered >= total:
        return f"共 {total} 页"
    return f"共 {total} 页 · 已渲染 {rendered} 页"
