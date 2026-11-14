"""Office 文档支持（纯 Python，零 Qt）：两条路，按设置面板的开关切换。

| 路径 | 谁干活 | 一份 50 页文档 | 保真度 |
|------|--------|----------------|--------|
| 版面（默认） | LibreOffice 无头转 PDF，再交回 PDF 渲染管线 | 1~8 秒，首次更久 | 版式 / 表格 / 图片全对 |
| 纯文字 | 标准库拆 OOXML + 外部 antiword | 20~40 毫秒 | 只有文字，样式丢光 |

**版面这条路的产物是「一份 PDF」，不是「一张预览图」** —— 这是相对旧 FileManager
（`_thumb_libreoffice` 只渲首页 900px）的实质改进：转一次落进缓存，之后翻页、缩放、
换清晰度档全由 `pdf_pages` + `image://pdfpage` 免费提供，翻到第 30 页也不用再转。

为什么不把 LibreOffice 设成硬依赖：它装完 300MB 起，麒麟 8GB 机器上转换时还要吃
200~400MB 内存。所以探测不到就自动走纯文字，开关也摆到设置面板里让用户自己关。

几个踩过的坑，改这里之前先看：

* **profile 必须隔离**。手工传 `-env:UserInstallation`，指向 cache 下我们自己的目录，
  不要用默认的 `~/.config/libreoffice` —— 用户此刻开着 Writer 时那边是锁着的，转换
  会直接秒退。同时又不能每次用临时目录：每次都要重新初始化 profile，白白多花 1~3 秒，
  热转换能压到 0.5~1.5 秒正是不重建 profile 换来的。
* **转换进程要杀进程组**。soffice 是个壳，真正干活的是 soffice.bin，只 terminate 壳
  会留下孤儿进程一直占着 200MB。Popen 时 `start_new_session=True`，超时/取消走
  `killpg`。
* **首次转换慢得没有道理**（冷启动 + 建 profile 常到 10 秒以上），所以超时给 120 秒；
  但也因此必须有磁盘缓存，同一个文件第二次打开必须是毫秒级。
* **`.doc` 拆不了**。docx/xlsx/pptx 是 ZIP + XML，标准库就能拆；`.doc` 是 OLE2 复合
  二进制，纯 Python 解析基本不可行，只能借 antiword / catdoc，没有再借 soffice 转 txt。
* **加密的 docx 不是 zip**。OOXML 加密后整份变成 OLE 容器，`zipfile` 直接
  BadZipFile；这种要认出来给「文档已加密」的提示，而不是报「文件损坏」。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------- 类型与限额 ----------------

# 走这里预览的扩展名。判断入口在 FileSource 的 kind_of（entries.py），
# 这里跟着一份是为了 OfficePreviewer 能按扩展名精确命中。
WORD_EXT = (".docx", ".docm", ".doc", ".rtf", ".odt", ".wps")
SHEET_EXT = (".xlsx", ".xlsm", ".xls", ".ods", ".et")
SLIDE_EXT = (".pptx", ".pptm", ".ppt", ".odp", ".dps")
OFFICE_EXT = WORD_EXT + SHEET_EXT + SLIDE_EXT

OOXML_EXT = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm")
ODF_EXT = (".odt", ".ods", ".odp")

# 纯 Python 抽取的天花板。文档预览是「看一眼」的场景，不需要整本。
MAX_CHARS = 120_000          # 约 6 万字，够 50 页正文
MAX_SHEET_ROWS = 2_000       # Excel 表格按行截断，再长就该用表格软件了
MAX_SHEET_COLS = 200
MAX_SLIDES = 300
MAX_XML_BYTES = 64 * 1024 * 1024    # 单个 XML 部件上限，防畸形包把内存打满

CONVERT_TIMEOUT = 120        # 秒。首次冷启动建 profile 常到 10 秒以上，留足余量
CACHE_MAX_BYTES = 300 * 1024 * 1024     # 转换产物缓存上限，超了按 mtime 淘汰
CACHE_MAX_FILES = 120

_LOCK = threading.Lock()     # soffice 共享 profile，同一时刻只能跑一份


class OfficeError(Exception):
    """Office 文档自身的错误（加密 / 损坏 / 缺外部工具），message 是给人看的中文。"""


# ---------------- 外部程序探测 ----------------

_TOOL_ENV = None


def _which(names, extra_dirs=()):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    # LibreOffice 的 AppImage / 手工安装经常不在 PATH 里，补两个常见位置
    for d in extra_dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return ""


def tools(refresh: bool = False) -> dict:
    """返回 {"soffice", "antiword", "catdoc", "unrtf"}，探不到的键值是空串。

    探测结果全局缓存：`shutil.which` 要扫 PATH，每次开预览窗都扫一遍没必要。
    """
    global _TOOL_ENV
    if _TOOL_ENV is not None and not refresh:
        return _TOOL_ENV
    _TOOL_ENV = {
        "soffice": _which(("soffice", "libreoffice"),
                          ("/usr/lib/libreoffice/program", "/opt/libreoffice/program",
                           "/Applications/LibreOffice.app/Contents/MacOS")),
        "antiword": _which(("antiword",)),
        "catdoc": _which(("catdoc",)),
        "unrtf": _which(("unrtf",)),
    }
    return _TOOL_ENV


def can_convert() -> bool:
    """能不能走「转 PDF」这条路 —— 只看有没有 soffice，不试真转。"""
    return bool(tools()["soffice"])


# ---------------- 转换产物缓存 ----------------

def cache_root() -> Path:
    """转换产物与 LibreOffice profile 的落脚点。

    WORKSPACE_CACHE 可覆盖（探针用它做隔离）；否则按 XDG 放 ~/.cache/workspace。
    刻意不放进 APP_HOME（~/.config/workspace）：那里是配置，这里是可以随时删的
    三百兆缓存，混在一起用户清理时会误删配置。
    """
    env = os.environ.get("WORKSPACE_CACHE")
    base = Path(env) if env else Path.home() / ".cache" / "workspace"
    return base / "office"


def _cache_key(local: str, st: os.stat_result) -> str:
    """缓存键 = 真实路径 + mtime + size。

    不用内容哈希：算一份 100MB 文档的 sha1 要几百毫秒，比命中的收益还大。
    (路径, mtime, size) 变了说明文件被覆盖过，重转一次才是对的。
    """
    raw = "%s|%d|%d" % (os.path.realpath(local), int(st.st_mtime), st.st_size)
    return hashlib.sha1(raw.encode("utf-8", "surrogateescape")).hexdigest()[:24]


def cached_pdf(local: str) -> str:
    """只为问「有没有现成的」，不触发转换。没有就返回空串。"""
    try:
        st = os.stat(local)
    except OSError:
        return ""
    p = cache_root() / (_cache_key(local, st) + ".pdf")
    try:
        if p.is_file() and p.stat().st_size > 0:
            return str(p)
    except OSError:
        pass
    return ""


def _profile_uri() -> str:
    """独立的 LibreOffice profile，复用同一个目录（省掉每次重建的 1~3 秒）。"""
    p = cache_root() / "profile"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve().as_uri()
    except OSError:
        return ""


def _kill_group(proc, pgid: int = 0) -> None:
    """连壳带子进程一起杀。

    soffice 是个 wrapper，真正干活的是 sofice.bin —— 只 terminate 壳会留下孤儿
    进程一直占着 200MB。传给 pgid 就无条件杀（取消/超时这种「壳可能已经退了、
    但子进程还在」的情况），不传就只在壳还活着时动手。
    """
    if not pgid and proc.poll() is not None:
        return
    try:
        target = pgid or os.getpgid(proc.pid)
    except OSError:
        return
    try:
        os.killpg(target, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_soffice(local: str, outdir: str, timeout: int, cancel=None):
    """跑一次无头转 PDF。产物由调用方在 outdir 里找。"""
    return _run_soffice_with(local, outdir, "pdf", timeout, cancel)


def convert_pdf(local: str, cancel=None, timeout: int = CONVERT_TIMEOUT):
    """把 Office 文档转成 PDF，返回 (pdf_path, error)。

    命中缓存直接返回，**不启动 LibreOffice** —— 这是第二次打开同一份文档能毫秒级
    出画面的全部原因。转换走临时目录 + os.replace，中途失败/取消不会留下半份 PDF
    污染缓存。
    """
    local = str(local)
    try:
        st = os.stat(local)
    except OSError as exc:
        return "", "无法读取文件：%s" % (exc.strerror or exc)
    if not os.path.isfile(local):
        return "", "这不是一个文件"

    hit = cached_pdf(local)
    if hit:
        return hit, ""

    if not can_convert():
        return "", "没找到 LibreOffice（soffice），装一个才能渲染版面"

    key = _cache_key(local, st)
    dest = cache_root() / (key + ".pdf")
    try:
        cache_root().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return "", "无法创建缓存目录：%s" % (exc.strerror or exc)

    # 共享 profile 只能串行：两个 soffice 抢同一个 UserInstallation 会互相踢。
    # 预览池本来就是单线程，这把锁是给「探针并发调用」兜底的。
    with _LOCK:
        hit = cached_pdf(local)          # 排队期间别人可能已经转好了
        if hit:
            return hit, ""
        if cancel is not None and cancel.is_cancelled():
            return "", "已取消"
        td = tempfile.mkdtemp(prefix="ws-office-")
        try:
            ok, msg = _run_soffice(local, td, timeout, cancel)
            if not ok:
                return "", msg
            produced = ""
            try:
                for name in sorted(os.listdir(td)):
                    if name.lower().endswith(".pdf"):
                        produced = os.path.join(td, name)
                        break
            except OSError:
                produced = ""
            if not produced or os.path.getsize(produced) == 0:
                # 返回码 0 但没有产物：多半是加密文档或 soffice 静默失败
                return "", "转换没有产出 PDF（文档可能已加密或损坏）"
            tmp = dest.with_name(dest.name + ".part")
            try:
                shutil.move(produced, tmp)
                os.replace(tmp, dest)
            except OSError as exc:
                for p in (tmp, dest):
                    try:
                        if p.exists():
                            p.unlink()
                    except OSError:
                        pass
                return "", "写入缓存失败：%s" % (exc.strerror or exc)
        finally:
            shutil.rmtree(td, ignore_errors=True)
    prune_cache()
    return str(dest), ""


def prune_cache() -> None:
    """缓存按 mtime 淘汰到限额内。失败一律静默 —— 清缓存不该影响预览。"""
    root = cache_root()
    try:
        files = [(p.stat().st_mtime, p.stat().st_size, p)
                 for p in root.glob("*.pdf") if p.is_file()]
    except OSError:
        return
    if len(files) <= CACHE_MAX_FILES and sum(f[1] for f in files) <= CACHE_MAX_BYTES:
        return
    files.sort()
    total = sum(f[1] for f in files)
    count = len(files)
    for _, size, p in files:
        if count <= CACHE_MAX_FILES and total <= CACHE_MAX_BYTES:
            break
        try:
            p.unlink()
            count -= 1
            total -= size
        except OSError:
            pass


def clear_cache() -> None:
    """清掉所有转换产物（不动 profile，profile 清了下次要重建）。"""
    try:
        for p in cache_root().glob("*.pdf"):
            p.unlink()
    except OSError:
        pass


# ---------------- 纯文字抽取：OOXML（docx / xlsx / pptx） ----------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_O = "{urn:schemas-microsoft-com:office:office}"
_DC = "{http://purl.org/dc/elements/1.1/}"


def _read_part(z: zipfile.ZipFile, name: str, limit: int = MAX_XML_BYTES) -> bytes:
    """读一个 XML 部件，超过上限就拒绝 —— 防畸形包把内存打满。

    顺手挡一下内嵌实体：ElementTree 会展开内部 `<!ENTITY>`，几行嵌套别名就能炸出
    几个 GB（billion laughs）。正常的 Office 文档里没有自定义实体，出现即异常。
    """
    info = z.getinfo(name)
    if info.file_size > limit:
        raise OfficeError("文档内部数据过大（%.0f MB）" % (info.file_size / 1048576))
    data = z.read(name)
    if b"<!ENTITY" in data:
        raise OfficeError("文档内嵌了自定义实体，出于安全考虑不予解析")
    return data


def _text_of(node, tag: str) -> str:
    return "".join(t.text or "" for t in node.iter(tag))


def _docx_text(z: zipfile.ZipFile, max_chars: int):
    """按段落抽正文。表格单元格也是段落，天然被收进来（用制表符分隔）。"""
    xml = _read_part(z, "word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(_W + "body")
    if body is None:
        return "", {"paragraphs": 0, "words": 0, "tables": 0, "images": 0}

    lines = []
    stats = {"paragraphs": 0, "words": 0, "tables": 0, "images": 0}
    # 图片数只数 zip 里的 word/media —— 不要为了它再 iter 一遍整棵 XML 树，
    # 那在 10 万字的文档上要多花十几毫秒，而结果还不如直接数文件准。
    stats["images"] = _media_count(z)

    def walk(node):
        """递归走 body：段落出文字，表格按行出。不用 iter() 一把梭是因为要保留
        「表格行」这个结构 —— 直接把所有段落平铺，表格会变成一串没头没尾的词。"""
        for child in node:
            if child.tag == _W + "p":
                text = _text_of(child, _W + "t")
                stats["paragraphs"] += 1
                if text.strip():
                    lines.append(text)
            elif child.tag == _W + "tbl":
                stats["tables"] += 1
                for row in child.findall(_W + "tr"):
                    cells = []
                    for cell in row.findall(_W + "tc"):
                        cells.append(" ".join(_text_of(p, _W + "t")
                                              for p in cell.findall(_W + "p")).strip())
                    if any(cells):
                        lines.append("\t".join(cells))
            elif child.tag == _W + "sdt":           # 内容控件里还套着一层
                content = child.find(_W + "sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    text = "\n".join(lines)
    stats["words"] = len(re.sub(r"\s+", "", text))
    return text[:max_chars], stats


def _media_count(z: zipfile.ZipFile) -> int:
    try:
        return sum(1 for n in z.namelist()
                   if n.startswith("word/media/") or n.startswith("ppt/media/"))
    except Exception:
        return 0


def _docx_meta(z: zipfile.ZipFile) -> dict:
    """docProps/core.xml 里的标题/作者。缺了就当没有，别抛。"""
    out = {}
    try:
        root = ET.fromstring(_read_part(z, "docProps/core.xml", 1024 * 1024))
    except (KeyError, ET.ParseError, OfficeError, OSError):
        return out
    for tag in ("title", "creator"):
        node = root.find(_DC + tag)
        if node is not None and (node.text or "").strip():
            out["title" if tag == "title" else "author"] = node.text.strip()
    return out


def _col_index(ref: str) -> int:
    """单元格引用 A1 / BC12 → 列序号（0 基）。"""
    n = 0
    for ch in ref:
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - 64)
        elif "a" <= ch <= "z":
            n = n * 26 + (ord(ch) - 96)
        else:
            break
    return max(0, n - 1)


def _xlsx_text(z: zipfile.ZipFile, max_chars: int):
    """按行输出工作表，单元格用制表符连。只读值不读样式。

    共享字符串表是索引式的：sheet 里的 `<c t="s"><v>12</v></c>` 指向
    sharedStrings.xml 的第 12 个 `<si>`。不建这张表就只能看到一堆数字。
    """
    shared: list[str] = []
    try:
        sroot = ET.fromstring(_read_part(z, "xl/sharedStrings.xml"))
        for si in sroot.findall(_S + "si"):
            shared.append(_text_of(si, _S + "t"))
    except (KeyError, ET.ParseError, OfficeError, OSError):
        pass

    sheets = []
    try:
        for name in z.namelist():
            if re.match(r"xl/worksheets/sheet\d+\.xml$", name):
                sheets.append(name)
    except Exception:
        pass
    sheets.sort(key=lambda n: int(re.search(r"(\d+)", os.path.basename(n)).group(1)))

    out: list[str] = []
    stats = {"sheets": len(sheets), "rows": 0, "paragraphs": 0,
             "words": 0, "tables": len(sheets), "images": _media_count(z)}

    for idx, name in enumerate(sheets[:12]):        # 12 个表之后基本是数据转储
        if len("\n".join(out)) >= max_chars:
            break
        try:
            root = ET.fromstring(_read_part(z, name))
        except (ET.ParseError, OfficeError, OSError):
            continue
        out.append("[工作表 %d]" % (idx + 1))
        sheet_data = root.find(_S + "sheetData")
        if sheet_data is None:
            continue
        for r_i, row in enumerate(sheet_data.findall(_S + "row")):
            if r_i >= MAX_SHEET_ROWS:
                out.append("…（本表行数超过 %d，已截断）" % MAX_SHEET_ROWS)
                break
            cells: dict[int, str] = {}
            for c in row.findall(_S + "c"):
                ref = c.get("r", "")
                col = _col_index(ref) if ref else len(cells)
                if col >= MAX_SHEET_COLS:
                    continue
                t = c.get("t", "")
                v = c.find(_S + "v")
                if t == "s" and v is not None and (v.text or "").isdigit():
                    k = int(v.text)
                    val = shared[k] if 0 <= k < len(shared) else ""
                elif t == "inlineStr":
                    is_node = c.find(_S + "is")
                    val = _text_of(is_node, _S + "t") if is_node is not None else ""
                elif t == "str":
                    val = (v.text or "") if v is not None else ""
                else:
                    val = (v.text or "") if v is not None else ""
                if val:
                    cells[col] = val
            if cells:
                width = max(cells) + 1
                out.append("\t".join(cells.get(i, "") for i in range(width)))
                stats["rows"] += 1
        out.append("")

    text = "\n".join(out)
    stats["words"] = len(re.sub(r"\s+", "", text))
    stats["paragraphs"] = stats["rows"]
    return text[:max_chars], stats


def _pptx_text(z: zipfile.ZipFile, max_chars: int):
    """按幻灯片序号抽文本框。"""
    slides = []
    try:
        for name in z.namelist():
            m = re.match(r"ppt/slides/slide(\d+)\.xml$", name)
            if m:
                slides.append((int(m.group(1)), name))
    except Exception:
        pass
    slides.sort()
    out: list[str] = []
    for n, name in slides[:MAX_SLIDES]:
        if len("\n".join(out)) >= max_chars:
            break
        try:
            root = ET.fromstring(_read_part(z, name))
        except (ET.ParseError, OfficeError, OSError):
            continue
        texts = [t.text or "" for t in root.iter(_A + "t")]
        body = "\n".join(t for t in texts if t.strip())
        out.append("[第 %d 页]\n%s" % (n, body) if body else "[第 %d 页]" % n)
    text = "\n\n".join(out)
    stats = {"slides": len(slides), "paragraphs": len(slides),
             "words": len(re.sub(r"\s+", "", text)), "tables": 0,
             "images": _media_count(z)}
    return text[:max_chars], stats


# ---------------- 纯文字抽取：ODF（odt / ods / odp） ----------------

_T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TBL = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def _odf_text(z: zipfile.ZipFile, max_chars: int):
    """ODF 正文在 content.xml：标题、段落、表格单元格三样就够了。"""
    try:
        root = ET.fromstring(_read_part(z, "content.xml"))
    except (KeyError, ET.ParseError, OfficeError, OSError) as exc:
        raise OfficeError("无法解析文档内容：%s" % exc)
    lines = []
    for node in root.iter():
        if node.tag in (_T + "p", _T + "h"):
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(text)
        elif node.tag == _TBL + "table-row":
            cells = ["".join(c.itertext()).strip()
                     for c in node.findall(_TBL + "table-cell")]
            if any(cells):
                lines.append("\t".join(cells))
    text = "\n".join(lines)
    stats = {"paragraphs": len(lines), "words": len(re.sub(r"\s+", "", text)),
             "tables": len(list(root.iter(_TBL + "table"))), "images": 0}
    return text[:max_chars], stats


# ---------------- 纯文字抽取：老格式（doc / wps / rtf） ----------------

def _legacy_text(local: str, max_chars: int):
    """`.doc` / `.wps` / `.rtf` 没有纯 Python 解法，只能借外部程序。

    顺序是有讲究的：antiword 对中文 .doc 支持最好，catdoc 是它的兜底；
    rtf 走 unrtf。三个都没有就返回空，由上层给「装什么」的提示。
    """
    ext = os.path.splitext(local)[1].lower()
    t = tools()
    candidates = []
    if ext in (".doc", ".wps"):
        if t["antiword"]:
            candidates.append(([t["antiword"], local], "antiword"))
        if t["catdoc"]:
            candidates.append(([t["catdoc"], "-d", "utf-8", local], "catdoc"))
    elif ext == ".rtf":
        if t["unrtf"]:
            candidates.append(([t["unrtf"], "--text", local], "unrtf"))

    for cmd, name in candidates:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception:
            continue
        if r.returncode != 0 or not r.stdout:
            continue
        text = _decode(r.stdout)
        if name == "unrtf":
            text = _clean_rtf(text)
        text = text.strip()
        if text:
            return text[:max_chars], {"paragraphs": text.count("\n") + 1,
                                      "words": len(re.sub(r"\s+", "", text)),
                                      "tables": 0, "images": 0}
    return "", {}


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _clean_rtf(text: str) -> str:
    """unrtf 的输出带一堆 \\par / \\'xx / 花括号，粗洗一遍够看就行。"""
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\n{3,}", "\n\n", text)


# ---------------- 对外：抽文字 ----------------

def is_office(name: str) -> bool:
    return os.path.splitext((name or "").lower())[1] in OFFICE_EXT


def format_label(local: str) -> str:
    ext = os.path.splitext(local)[1].lower()
    return {
        ".docx": "Word 文档", ".docm": "Word 文档（含宏）", ".doc": "Word 文档（旧格式）",
        ".xlsx": "Excel 表格", ".xlsm": "Excel 表格（含宏）", ".xls": "Excel 表格（旧格式）",
        ".pptx": "PowerPoint 演示", ".pptm": "PowerPoint 演示（含宏）",
        ".ppt": "PowerPoint 演示（旧格式）",
        ".odt": "OpenDocument 文本文档", ".ods": "OpenDocument 表格",
        ".odp": "OpenDocument 演示", ".rtf": "RTF 文档",
        ".wps": "WPS 文档", ".et": "WPS 表格", ".dps": "WPS 演示",
    }.get(ext, "Office 文档")


def read_text(local: str, max_chars: int = MAX_CHARS, cancel=None) -> dict:
    """纯 Python 抽文字。返回 {"text","truncated","encrypted","hint","stats","format"}。

    永远不抛：失败也返回可展示的 hint，预览窗不该因为一份坏文档变成空白。
    """
    out = {"text": "", "truncated": False, "encrypted": False, "hint": "",
           "stats": {}, "format": format_label(local)}
    ext = os.path.splitext(local)[1].lower()
    try:
        size = os.path.getsize(local)
    except OSError as exc:
        out["hint"] = "无法读取文件：%s" % (exc.strerror or exc)
        return out

    if cancel is not None and cancel.is_cancelled():
        out["hint"] = "已取消"
        return out

    if ext in OOXML_EXT or ext in ODF_EXT:
        try:
            with zipfile.ZipFile(local) as z:
                if ext in (".docx", ".docm"):
                    text, stats = _docx_text(z, max_chars)
                    out["stats"] = stats
                    out["stats"].update(_docx_meta(z))
                elif ext in (".xlsx", ".xlsm"):
                    text, stats = _xlsx_text(z, max_chars)
                    out["stats"] = stats
                elif ext in (".pptx", ".pptm"):
                    text, stats = _pptx_text(z, max_chars)
                    out["stats"] = stats
                else:
                    text, stats = _odf_text(z, max_chars)
                    out["stats"] = stats
        except zipfile.BadZipFile:
            # OOXML 加密后整份变成 OLE 复合文档，不再是 zip —— 这不是损坏
            out["encrypted"] = True
            out["hint"] = "这份文档已加密，需要密码才能读取内容"
            return out
        except OfficeError as exc:
            out["hint"] = str(exc)
            return out
        except (OSError, ET.ParseError, KeyError) as exc:
            out["hint"] = "文档结构异常：%s" % exc
            return out
        out["truncated"] = len(text) >= max_chars
        out["text"] = text
        if not text.strip():
            out["hint"] = "文档里没有可提取的文字（可能全是图片或扫描件）"
        return out

    if ext in (".doc", ".wps", ".rtf"):
        text, stats = _legacy_text(local, max_chars)
        out["stats"] = stats
        out["truncated"] = len(text) >= max_chars
        out["text"] = text
        if not text.strip():
            t = tools()
            if ext == ".rtf":
                need = "unrtf"
            else:
                need = "antiword 或 catdoc"
            out["hint"] = ("读不了这个格式，装 %s 就能抽出文字（%s）"
                           % (need, "已装" if (t["antiword"] or t["catdoc"] or t["unrtf"])
                              else "当前未安装"))
        return out

    # .xls / .ppt / .et / .dps：OLE2 二进制，纯 Python 不碰
    if size > 0:
        out["hint"] = ("%s 是旧式二进制格式，纯 Python 抽不了文字；"
                       "打开「Office 版面」开关（或用 LibreOffice 打开）才能看内容"
                       % out["format"])
    return out


def probe(local: str) -> dict:
    """元信息统计（段落/字数/表格/图片）。失败返回 {}，绝不抛。"""
    ext = os.path.splitext(local)[1].lower()
    if ext not in (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"):
        return {}
    try:
        with zipfile.ZipFile(local) as z:
            if ext in (".docx", ".docm"):
                _, stats = _docx_text(z, 1)      # 只要统计，max_chars=1 省事
                stats.update(_docx_meta(z))
                return stats
            if ext in (".xlsx", ".xlsm"):
                _, stats = _xlsx_text(z, 1)
                return stats
            _, stats = _pptx_text(z, 1)
            return stats
    except Exception:
        return {}


def _run_soffice_with(local: str, outdir: str, convert_to: str, timeout: int, cancel=None):
    """跑一次无头转换，转换目标可配（pdf / txt）。返回 (ok, message)。

    取消与超时都要**杀进程组**：CancelToken.cancel() 只 terminate 主进程，
    而 soffice 的壳退掉之后 soffice.bin 还活着，会一直占着 200MB 内存 ——
    在 8GB 的麒麟上连着翻几个 docx 就能把机器拖死。
    """
    exe = tools()["soffice"]
    if not exe:
        return False, "没找到 LibreOffice（soffice）"
    cmd = [exe, "--headless", "--norestore", "--nologo",
           "--nolockcheck", "--nodefault", "--invisible"]
    uri = _profile_uri()
    if uri:
        cmd.append("-env:UserInstallation=" + uri)
    cmd += ["--convert-to", convert_to, "--outdir", outdir, local]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                start_new_session=True)
    except OSError as exc:
        return False, "启动转换进程失败：%s" % exc
    # 先记下 pgid：进程退出后 getpgid 就查不到了，而那时恰恰是最需要杀子进程的时候
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = 0
    if cancel is not None:
        cancel.attach(proc)
    try:
        try:
            proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc, pgid)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            return False, "转换超时（%d 秒）" % timeout
        if cancel is not None and cancel.is_cancelled():
            _kill_group(proc, pgid)          # 壳可能已退，子进程还得清
            return False, "已取消"
        if proc.returncode != 0:
            return False, "转换进程异常退出（%s）" % proc.returncode
        return True, ""
    finally:
        if cancel is not None:
            cancel.detach(proc)
        # 正常收尾只处理「壳还活着」的情况：那时 pgid 一定还是我们自己的。
        # 已经退出的进程不要拿旧 pgid 去 killpg —— 那个号可能已经被系统分配给
        # 别人的进程组了。取消/超时那两条路径已经显式杀过了。
        if proc.poll() is None:
            _kill_group(proc)


def status() -> dict:
    """给设置面板/自检看的现状：有没有 soffice、缓存多大。"""
    t = tools()
    root = cache_root()
    size = 0
    count = 0
    try:
        for p in root.glob("*.pdf"):
            try:
                size += p.stat().st_size
                count += 1
            except OSError:
                pass
    except OSError:
        pass
    return {
        "soffice": t["soffice"],
        "antiword": t["antiword"],
        "catdoc": t["catdoc"],
        "unrtf": t["unrtf"],
        "canConvert": bool(t["soffice"]),
        "cacheDir": str(root),
        "cacheFiles": count,
        "cacheBytes": size,
    }
