"""压缩包读取 / 解压 / 压缩（纯 Python，零 Qt）。

分工跟 `preview/`、`ops/` 一致：这里只碰文件系统与 zip/tar 格式，绝不 import PySide6；
线程池、进度条、Toast、剪贴板全在 `file_bridge.py`。

格式矩阵（拍板，别在这里加戏）：

| 格式 | 怎么处理 | 说明 |
|------|----------|------|
| ZIP 家族 .zip .jar .apk .epub … | 标准库 zipfile | 全平台零依赖 |
| TAR 家族 .tar .tar.gz .tgz .tar.bz2 .txz | 标准库 tarfile | 同上 |
| 裸 .gz .bz2 .xz（单文件压缩） | gzip/bz2/lzma 流式 | tarfile 解不了裸压缩，单独一条路 |
| RAR / 7z | 系统外部程序 | 7z → 7zz → 7za → unrar → bsdtar 逐个探 |

为什么不引入 py7zr / rarfile：py7zr 拖 pycryptodomex + pyzstd + brotli 一串依赖，
麒麟 aarch64 上 wheel 覆盖不保证（本项目只有 PyMuPDF 一个三方依赖，这个洁癖要守住）；
rarfile 本身也靠外部 unrar 干活，等于白多一层。外部程序找不到时降级成「能列文件名、
不能解压」，并在提示里写清楚装什么 —— 比装不上轮子直接不能用强。

安全底线（解压是不可信输入，这几条不能省）：
* 成员名一律 sanitize：去掉前导 `/`、丢弃含 `..` 的段 —— 防 zip slip；
* 符号链接 / 硬链接 / 设备文件一律跳过，不落盘 —— 否则「先写个指向 /etc 的链接，再顺着它
  写文件」就能写到压缩包外面去；
* 不覆盖已有文件：同名整棵解压到独立文件夹时走 `unique_name` 加序号，与复制粘贴同一套策略。

性能上的两个决定：
* 成员索引按 (路径, mtime, size) 缓存最多 4 份。tar.gz 建索引要把整条流解一遍，几百 MB
  的包每次进目录都重解就是「卡两秒」的来源；zip 中央目录本身很快，缓存只是顺手。
  代价是压缩包在外面被改动后，得等 mtime/size 变了（或重开）才看到新内容 —— 可接受。
* 压缩已经压过的类型（jpg/png/mp4/zip…）一律 ZIP_STORED，省掉纯亏的 CPU。
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime

from . import vpath
from .entries import kind_label, make_entry
from .ops.naming import unique_name
from .vpath import is_hidden

CHUNK = 1024 * 1024          # 1MB：与 ops/transfer 一致，8GB 机器上够快也不顶内存
MAX_MEMBERS = 100_000        # 索引上限：十万条之后的包基本是数据集，不是给人看的
MAX_ITEMS = 20_000           # 单层列目录上限，与 listing.MAX_ITEMS 对齐

_ZIP_SUFFIX = (".zip", ".jar", ".war", ".apk", ".aar", ".epub", ".cbz", ".xpi")
_TAR_SUFFIX = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2",
               ".tar.xz", ".txz")
_GZ_SUFFIX = (".gz", ".bz2", ".xz")
_ALL_SUFFIX = tuple(sorted(set(_TAR_SUFFIX + _ZIP_SUFFIX + _GZ_SUFFIX
                               + (".rar", ".7z")), key=len, reverse=True))

# 已经压过、再 deflate 纯亏 CPU 的类型（解压时反而更慢）
_STORED_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".heic", ".mp3", ".mp4",
    ".m4a", ".m4v", ".mov", ".mkv", ".avi", ".webm", ".flac", ".aac", ".ogg",
    ".zip", ".rar", ".7z", ".gz", ".bz2", ".xz", ".zst", ".pdf", ".docx", ".xlsx",
    ".pptx", ".apk", ".jar", ".woff", ".woff2", ".ttf", ".otf",
}


class _Cancelled(Exception):
    pass


class ArchiveError(Exception):
    """压缩包自身的错误（损坏 / 加密 / 缺外部工具），message 是给人看的中文。"""


@dataclass(frozen=True)
class Member:
    name: str            # 容器内相对路径，`/` 分隔，目录不带尾斜杠
    size: int
    mtime: float
    is_dir: bool


# ---------------- 格式识别 ----------------

def format_of(name: str) -> str:
    """按文件名判断容器格式：zip / tar / rar / 7z / gzip / ""（不认识）。

    只看名字，不读内容：M2 的判定入口是「双击了这个文件」，扩展名不够时宁可报错，
    也不去猜魔数（读一半再猜错，换来的是更莫名其妙的错误）。
    """
    n = (name or "").strip().lower().rsplit("/", 1)[-1]
    if not n:
        return ""
    for suf in _TAR_SUFFIX:                 # 先认 .tar.gz：它同时以 .gz 结尾
        if n.endswith(suf):
            return "tar"
    for suf in _ZIP_SUFFIX:
        if n.endswith(suf):
            return "zip"
    if n.endswith(".rar"):
        return "rar"
    if n.endswith(".7z"):
        return "7z"
    for suf in _GZ_SUFFIX:
        if n.endswith(suf):
            return "gzip"
    return ""


def is_archive(path: str) -> bool:
    return bool(format_of(path)) and os.path.isfile(path)


def stem_of(name: str) -> str:
    """压缩包名去掉后缀：`a.tar.gz` → `a`、`x.zip` → `x`。用于「解压到独立文件夹」。"""
    n = (name or "").rstrip("/")
    low = n.lower()
    for suf in _ALL_SUFFIX:
        if low.endswith(suf) and len(n) > len(suf):
            return n[: len(n) - len(suf)]
    return n


# ---------------- 外部工具探测（RAR / 7z） ----------------

_TOOL_ENV = None


def _which(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return ""


def tools(refresh: bool = False) -> dict:
    """返回 {"sevenZip": 路径, "unrar": 路径, "bsdtar": 路径, "kind": "7z"|"unrar"|"bsdtar"|""}。

    第一次探测后缓存：`shutil.which` 要扫 PATH，每次进目录都扫一遍没必要。
    """
    global _TOOL_ENV
    if _TOOL_ENV is not None and not refresh:
        return _TOOL_ENV
    seven = _which(("7z", "7zz", "7za"))
    unrar = _which(("unrar", "unar"))
    bsdtar = _which(("bsdtar",))
    kind = "7z" if seven else ("unrar" if unrar else ("bsdtar" if bsdtar else ""))
    _TOOL_ENV = {"sevenZip": seven, "unrar": unrar, "bsdtar": bsdtar, "kind": kind}
    return _TOOL_ENV


def can_browse(path: str) -> bool:
    """能不能像目录一样进去浏览。RAR/7z 没有外部工具时进不去，只给预览摘要。"""
    fmt = format_of(path)
    if fmt in ("zip", "tar"):
        return True
    if fmt in ("rar", "7z"):
        return bool(tools()["kind"])
    return False


def _need_tool(fmt: str) -> str:
    """缺工具时的中文提示：写清楚装什么，别只说「不支持」。"""
    if fmt == "rar":
        return "读取 RAR 需要 7z 或 unrar：sudo apt install p7zip-full 或 unrar"
    if fmt == "7z":
        return "读取 7z 需要 p7zip：sudo apt install p7zip-full"
    return "此格式需要外部解压程序"


# ---------------- 成员索引 ----------------

def _zip_name(info: zipfile.ZipInfo) -> str:
    """ZIP 里的成员名解码。

    zipfile 在 UTF-8 标志位没置位时一律按 cp437 解码 —— 而 Windows 上打的包多半是 GBK，
    于是「新建文件夹」全变乱码。这里把 cp437 还原成字节再按 utf-8 → gbk 试一遍。
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _zip_mtime(info: zipfile.ZipInfo) -> float:
    try:
        return datetime(*info.date_time).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _read_zip(path: str, cancel) -> tuple[list[Member], bool]:
    out: list[Member] = []
    encrypted = False
    try:
        with zipfile.ZipFile(path) as zf:
            for i, info in enumerate(zf.infolist()):
                if cancel is not None and (i & 255) == 0 and cancel.is_cancelled():
                    raise _Cancelled()
                if info.flag_bits & 0x1:
                    encrypted = True
                if _is_zip_symlink(info):
                    continue
                name = _clean(_zip_name(info))
                if not name:
                    continue
                is_dir = info.is_dir() or name.endswith("/")
                out.append(Member(name.rstrip("/") or "/", 0 if is_dir else info.file_size,
                                  _zip_mtime(info), is_dir))
                if len(out) >= MAX_MEMBERS:
                    break
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"压缩包已损坏或不是有效的 ZIP：{exc}") from exc
    return out, encrypted


def _read_tar(path: str, cancel) -> list[Member]:
    out: list[Member] = []
    try:
        with tarfile.open(path, "r:*") as tf:
            for i, member in enumerate(tf):        # 惰性迭代：可以中途取消
                if cancel is not None and (i & 255) == 0 and cancel.is_cancelled():
                    raise _Cancelled()
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    continue                        # 链接/设备/管道：不列，也不解
                name = _clean(member.name)
                if not name:
                    continue
                is_dir = member.isdir()
                out.append(Member(name.rstrip("/") or "/",
                                  0 if is_dir else int(member.size or 0),
                                  float(member.mtime or 0), is_dir))
                if len(out) >= MAX_MEMBERS:
                    break
    except tarfile.ReadError as exc:
        raise ArchiveError(f"压缩包已损坏或不是有效的 TAR：{exc}") from exc
    except (EOFError, OSError) as exc:
        raise ArchiveError(f"读取压缩包失败：{exc}") from exc
    return out


def _run(cmd, cancel=None, cwd=None, timeout=600):
    """跑外部解压程序。取消时 terminate，2 秒不退就 kill。"""
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"无法启动 {cmd[0]}：{exc}") from exc
    if cancel is not None:
        cancel.attach(proc)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    finally:
        if cancel is not None:
            cancel.detach(proc)
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _read_external(path: str, fmt: str, cancel) -> tuple[list[Member], bool]:
    env = tools()
    kind = env["kind"]
    if kind == "7z":
        code, out, err = _run([env["sevenZip"], "l", "-slt", "-p-", "--", path], cancel)
        if code not in (0, 1) and not out:
            raise ArchiveError(f"{os.path.basename(env['sevenZip'])} 读取失败：{err.strip()[:120]}")
        return _parse_7z_slt(out)
    if kind == "unrar":
        code, out, err = _run([env["unrar"], "lb", path], cancel)
        if code != 0:
            raise ArchiveError(f"unrar 读取失败：{err.strip()[:120]}")
        return _parse_names(out)
    if kind == "bsdtar":
        code, out, err = _run([env["bsdtar"], "-tf", path], cancel)
        if code != 0:
            raise ArchiveError(f"bsdtar 读取失败：{err.strip()[:120]}")
        return _parse_names(out)
    raise ArchiveError(_need_tool(fmt))


def _parse_names(text: str) -> tuple[list[Member], bool]:
    """只给名字的降级解析（unrar lb / bsdtar -tf）：大小与时间未知，显示成 `--`。"""
    out: list[Member] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        name = _clean(raw)
        if not name:
            continue
        is_dir = raw.endswith("/")
        out.append(Member(name.rstrip("/"), 0, 0.0, is_dir))
        if len(out) >= MAX_MEMBERS:
            break
    return out, False


def _parse_7z_slt(text: str) -> tuple[list[Member], bool]:
    """解析 `7z l -slt` 的 key = value 分块输出。

    块之间空行分隔，`----------` 之前是程序头（那一段的 Path 是压缩包自己，别当成员）。
    """
    out: list[Member] = []
    encrypted = False
    started = False
    cur: dict = {}

    def flush():
        if not cur:
            return
        name = _clean(str(cur.get("Path", "")))
        if not name:
            return
        attrs = str(cur.get("Attributes", ""))
        is_dir = name.endswith("/") or "D" in attrs
        try:
            size = int(str(cur.get("Size", "0")).strip() or 0)
        except ValueError:
            size = 0
        mtime = 0.0
        raw_time = str(cur.get("Modified", "")).strip().split(".")[0]
        if raw_time:
            try:
                mtime = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                mtime = 0.0
        out.append(Member(name.rstrip("/"), 0 if is_dir else size, mtime, is_dir))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("----------"):
            started = True
            cur = {}
            continue
        if not started:
            continue
        if not stripped:
            flush()
            cur = {}
            continue
        key, sep, value = stripped.partition(" = ")
        if not sep:
            continue
        key = key.strip()
        cur[key] = value.strip()
        if key == "Encrypted" and value.strip() in ("+", "1"):
            encrypted = True
    flush()
    if len(out) > MAX_MEMBERS:
        out = out[:MAX_MEMBERS]
    return out, encrypted


_INDEX_CACHE: dict = {}
_INDEX_ORDER: list = []
_INDEX_CACHE_MAX = 4


def _cache_key(path: str):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime_ns, st.st_size)


def clear_cache():
    _INDEX_CACHE.clear()
    _INDEX_ORDER.clear()


def read_index(container: str, cancel=None) -> tuple[list[Member], bool]:
    """读成员索引，返回 (成员, 是否加密)。缓存键带 mtime+size，包被改过后自动失效。"""
    fmt = format_of(container)
    if not fmt:
        raise ArchiveError("不认识的压缩包格式")
    key = _cache_key(container)
    if key is not None and key in _INDEX_CACHE:
        _INDEX_ORDER.remove(key)
        _INDEX_ORDER.append(key)
        return _INDEX_CACHE[key]

    if fmt == "zip":
        members, encrypted = _read_zip(container, cancel)
    elif fmt == "tar":
        members, encrypted = _read_tar(container, cancel), False
    elif fmt in ("rar", "7z"):
        members, encrypted = _read_external(container, fmt, cancel)
    else:                                   # 裸 .gz 只有一个成员，没目录可列
        inner = stem_of(os.path.basename(container))
        try:
            size = os.path.getsize(container)
        except OSError:
            size = 0
        return [Member(inner, size, 0.0, False)], False

    if key is not None:
        _INDEX_CACHE[key] = (members, encrypted)
        _INDEX_ORDER.append(key)
        while len(_INDEX_ORDER) > _INDEX_CACHE_MAX:
            _INDEX_CACHE.pop(_INDEX_ORDER.pop(0), None)
    return members, encrypted


# ---------------- 列目录 ----------------

def _clean(name: str) -> str:
    """成员名清洗：统一分隔符、去掉前导 / 与 . 段，含 `..` 的整条丢弃（返回空串）。"""
    n = (name or "").replace("\\", "/").strip()
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def list_dir(container: str, inner: str, base_vpath: str, show_hidden: bool = False,
             cancel=None) -> tuple[list, str, bool]:
    """列压缩包里的一层，返回 (Entry 列表, 错误文案, 是否截断)。

    走的是扁平成员表 → 一层目录的聚合：压缩包没有真正的目录项（有的包连目录条目都不写），
    所以不能像文件系统那样直接 scandir。
    """
    try:
        members, _encrypted = read_index(container, cancel)
    except _Cancelled:
        return [], None, False
    except ArchiveError as exc:
        return [], str(exc), False
    except (OSError, ValueError) as exc:
        return [], f"读取压缩包失败：{exc}", False

    prefix = (inner or "").strip("/")
    base = (prefix + "/") if prefix else ""
    seen: dict[str, list] = {}          # head -> [is_dir, size, mtime, order]
    order: list[str] = []
    truncated = False
    total = 0

    for i, m in enumerate(members):
        if cancel is not None and (i & 255) == 0 and cancel.is_cancelled():
            return [], None, False
        if base and not m.name.startswith(base):
            continue
        rest = m.name[len(base):]
        if not rest:
            continue
        head, sep, _tail = rest.partition("/")
        if not head or head in ("", "."):
            continue
        if not show_hidden and is_hidden(head):
            continue
        info = seen.get(head)
        if info is None:
            if len(seen) >= MAX_ITEMS:
                truncated = True
                break
            seen[head] = [bool(sep) or m.is_dir, 0 if sep else m.size, m.mtime, i]
            order.append(head)
            total += 1
        elif sep or m.is_dir:
            info[0] = True              # 同一名字后面出现子项，那就是目录
    entries = []
    for head in order:
        is_dir, size, mtime, _i = seen[head]
        try:
            vp = vpath.join(base_vpath, head)
        except ValueError:
            continue
        entries.append(make_entry(head, vp, is_dir, 0 if is_dir else size, mtime))
    return entries, None, truncated


# ---------------- 解压 ----------------

def _selected(name: str, names) -> bool:
    if not names:
        return True
    for n in names:
        n = (n or "").strip("/")
        if not n:
            return True                     # 选了根 = 全选
        if name == n or name.startswith(n + "/"):
            return True
    return False


def _write_stream(src, dst: str, size: int, cancel, tick, state: dict, written: list,
                  result: dict):
    """分块落盘 + 进度 + 取消。先写 .wspart 再原子改名，取消时不留半成品。

    目标已存在就跳过、**绝不覆盖** —— 与复制粘贴同一套策略（见 ops/transfer.py）。
    「解压到此处」撞上已有文件是常事，静默覆盖等于毁数据。
    """
    if os.path.lexists(dst):
        result["exists"] += 1
        state["done"] += max(0, int(size or 0))     # 跳过的字节也计进度，否则进度条永远差一截
        if tick is not None:
            tick(state["done"], state["total"])
        return False
    part = dst + ".wspart"
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        with open(part, "wb") as fo:
            while True:
                if cancel is not None and cancel.is_cancelled():
                    raise _Cancelled()
                buf = src.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
                state["done"] += len(buf)
                if tick is not None:
                    tick(state["done"], state["total"])
        os.replace(part, dst)
        written.append(dst)
        return True
    except BaseException:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise


def _extract_zip(path, root, names, cancel, tick, state, written, result):
    with zipfile.ZipFile(path) as zf:
        infos = []
        for info in zf.infolist():
            name = _zip_name(info)
            if _is_zip_symlink(info):
                continue
            if not _selected(_clean(name), names):
                continue
            infos.append((info, name))
        state["total"] = sum(0 if (i.is_dir() or _is_zip_symlink(i)) else i.file_size
                             for i, _n in infos)
        for info, raw in infos:
            if cancel is not None and cancel.is_cancelled():
                raise _Cancelled()
            name = _clean(raw)
            if not name:
                result["skipped"] += 1      # 越界名（../ 或绝对路径）
                continue
            dst = os.path.join(root, name)
            if info.is_dir() or raw.endswith("/"):
                try:
                    os.makedirs(dst, exist_ok=True)
                except OSError as exc:
                    result["failed"].append({"name": name, "error": str(exc)})
                continue
            try:
                if info.flag_bits & 0x1:
                    raise ArchiveError("压缩包已加密，暂不支持解压")
                with zf.open(info) as src:
                    if _write_stream(src, dst, int(info.file_size or 0), cancel,
                                     tick, state, written, result):
                        result["ok"] += 1
            except RuntimeError as exc:     # 压缩方法不支持 / 条目已损坏
                result["failed"].append({"name": name, "error": f"读取条目失败：{exc}"})
            except OSError as exc:
                result["failed"].append({"name": name, "error": str(exc)})


def _extract_tar(path, root, names, cancel, tick, state, written, result):
    with tarfile.open(path, "r:*") as tf:
        members = []
        for m in tf:
            if m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                continue
            name = _clean(m.name)
            if not name or not _selected(name, names):
                continue
            members.append((m, name))
        state["total"] = sum(0 if m.isdir() else int(m.size or 0) for m, _n in members)
        for m, name in members:
            if cancel is not None and cancel.is_cancelled():
                raise _Cancelled()
            dst = os.path.join(root, name)
            if m.isdir():
                try:
                    os.makedirs(dst, exist_ok=True)
                except OSError as exc:
                    result["failed"].append({"name": name, "error": str(exc)})
                continue
            src = tf.extractfile(m)
            if src is None:
                result["failed"].append({"name": name, "error": "无法读取条目"})
                continue
            try:
                with src:
                    if _write_stream(src, dst, int(m.size or 0), cancel, tick,
                                     state, written, result):
                        result["ok"] += 1
            except OSError as exc:
                result["failed"].append({"name": name, "error": str(exc)})


def _extract_external(path, fmt, root, names, cancel, tick, state, result):
    env = tools()
    if env["kind"] == "7z":
        cmd = [env["sevenZip"], "x", "-y", "-p-", "-o" + root, "--", path]
        cmd += [n for n in (names or []) if n]
    elif env["kind"] == "unrar":
        cmd = [env["unrar"], "x", "-o+", "-y", path] + [n for n in (names or []) if n]
    elif env["kind"] == "bsdtar":
        cmd = [env["bsdtar"], "-xf", path, "-C", root] + [n for n in (names or []) if n]
    else:
        raise ArchiveError(_need_tool(fmt))
    # 外部程序不给细粒度进度：只报「开始/结束」，进度条停在 0 然后跳 100。
    if tick is not None:
        tick(0, 0)
    code, out, err = _run(cmd, cancel, cwd=root)
    if cancel is not None and cancel.is_cancelled():
        raise _Cancelled()
    if code != 0:
        msg = (err or out).strip().splitlines()
        raise ArchiveError(f"解压失败：{msg[-1][:120] if msg else '未知错误'}")
    # 外部程序不回报「解了几个文件」：只标完成，别让上层报一个假数字。
    result["opaque"] = True
    return True


def extract(container: str, dest_parent: str, names=None, into_folder: bool = False,
            cancel=None, tick=None) -> dict:
    """解压。names=None 表示整包；into_folder=True 表示先建「包名」文件夹再解进去。

    返回纯 dict（可跨线程回传）：
        {"ok": 文件数, "failed": [{"name","error"}], "skipped": 跳过数,
         "cancelled": bool, "path": 落点 vpath, "dir": 本地目录}
    """
    result = {"ok": 0, "failed": [], "skipped": 0, "exists": 0, "cancelled": False,
              "path": "", "dir": "", "opaque": False}
    fmt = format_of(container)
    if not fmt:
        result["failed"].append({"name": os.path.basename(container),
                                 "error": "不认识的压缩包格式"})
        return result
    if not os.path.isdir(dest_parent):
        result["failed"].append({"name": "", "error": "目标不是文件夹"})
        return result

    created_root = ""
    try:
        if into_folder:
            name = unique_name(dest_parent, stem_of(os.path.basename(container)) or "解压")
            root = os.path.join(dest_parent, name)
            os.makedirs(root, exist_ok=True)
            created_root = root
        else:
            root = dest_parent

        state = {"done": 0, "total": 0}
        written: list[str] = []
        try:
            if fmt == "zip":
                _extract_zip(container, root, names, cancel, tick, state, written, result)
            elif fmt == "tar":
                _extract_tar(container, root, names, cancel, tick, state, written, result)
            elif fmt in ("rar", "7z"):
                _extract_external(container, fmt, root, names, cancel, tick, state, result)
            else:
                _extract_single(container, fmt, root, cancel, tick, state, written, result)
        except _Cancelled:
            for f in written:
                try:
                    os.unlink(f)
                except OSError:
                    pass
            if created_root:
                shutil.rmtree(created_root, ignore_errors=True)
            result["cancelled"] = True
            return result
    except ArchiveError as exc:
        if created_root:
            shutil.rmtree(created_root, ignore_errors=True)
        result["failed"].append({"name": os.path.basename(container), "error": str(exc)})
        return result
    except (OSError, ValueError, EOFError) as exc:
        if created_root:
            shutil.rmtree(created_root, ignore_errors=True)
        result["failed"].append({"name": os.path.basename(container), "error": str(exc)})
        return result

    if tick is not None:
        tick(state["done"], state["done"] or 0)
    dest = root
    result["dir"] = dest
    try:
        result["path"] = vpath.encode_path(dest)
    except (ValueError, TypeError):
        result["path"] = ""
    return result


def _extract_single(container, fmt, root, cancel, tick, state, written, result):
    """裸 .gz/.bz2/.xz：一条流压一个文件，解出来就是去掉后缀的那个名字。"""
    name = stem_of(os.path.basename(container)) or "解压结果"
    opener = {"gzip": gzip.open, "bz2": bz2.open, "xz": lzma.open}.get(fmt) or gzip.open
    with opener(container, "rb") as src:
        try:
            state["total"] = os.path.getsize(container)
        except OSError:
            state["total"] = 0
        if _write_stream(src, os.path.join(root, name), 0, cancel, tick,
                         state, written, result):
            result["ok"] = 1


# ---------------- 包内单文件读取（文本预览用） ----------------

def read_member_head(container: str, inner: str, max_bytes: int, cancel=None):
    """读一个成员的前 max_bytes 字节，返回 (bytes, 完整大小)。

    **只服务文本预览**：不解整包、不落临时文件。zip 走中央目录直接定位；tar.gz 没索引
    只能顺着流找到那个成员（大包会慢），所以只用来读文本 —— 图片/PDF 一律请用户先解压。
    """
    fmt = format_of(container)
    inner = (inner or "").strip("/")
    if not inner:
        raise ArchiveError("没有指定压缩包内的文件")
    if fmt == "zip":
        with zipfile.ZipFile(container) as zf:
            target = None
            for info in zf.infolist():
                if _clean(_zip_name(info)) == inner and not _is_zip_symlink(info):
                    target = info
                    break
            if target is None:
                raise ArchiveError("压缩包里找不到这个文件")
            if target.flag_bits & 0x1:
                raise ArchiveError("压缩包已加密，暂不支持预览")
            with zf.open(target) as fh:
                return fh.read(max_bytes), int(target.file_size)
    if fmt == "tar":
        with tarfile.open(container, "r:*") as tf:
            for i, m in enumerate(tf):
                if cancel is not None and (i & 63) == 0 and cancel.is_cancelled():
                    raise _Cancelled()
                if not m.isfile() or _clean(m.name) != inner:
                    continue
                fh = tf.extractfile(m)
                if fh is None:
                    raise ArchiveError("无法读取这个条目")
                with fh:
                    return fh.read(max_bytes), int(m.size or 0)
        raise ArchiveError("压缩包里找不到这个文件")
    if fmt in ("rar", "7z"):
        raise ArchiveError("压缩包内的文件暂不支持预览，请先解压")
    if fmt == "gzip":
        opener = {"gzip": gzip.open, "bz2": bz2.open, "xz": lzma.open}[fmt]
        with opener(container, "rb") as fh:
            return fh.read(max_bytes), 0
    raise ArchiveError("不认识的压缩包格式")


def read_text_member(v: str, max_bytes: int, max_lines: int, cancel=None) -> dict:
    """压缩包内成员的文本预览（供文本/代码预览器用）。

    返回结构与 `preview.textread.read_text` 完全一致，上层不用分叉。
    """
    from .preview.textread import decode_text        # 延迟导入：躲开包初始化期循环
    out = {"text": "", "encoding": "", "lines": 0, "truncated": False,
           "binary": False, "size": 0, "error": ""}
    try:
        scheme, container, inner = vpath.parse(v)
    except (ValueError, TypeError):
        out["error"] = "路径无效"
        return out
    if scheme != vpath.ARCHIVE or not container or not inner:
        out["error"] = "压缩包路径无效"
        return out
    try:
        head, size = read_member_head(container, inner, max_bytes, cancel)
    except _Cancelled:
        out["error"] = "已取消"
        return out
    except ArchiveError as exc:
        out["error"] = str(exc)
        return out
    except (OSError, EOFError, ValueError) as exc:
        out["error"] = f"读取失败：{exc}"
        return out
    return decode_text(head, size or len(head), max_bytes, max_lines)


# ---------------- 压缩（只做 ZIP） ----------------

def _plan_sources(sources) -> tuple[list[tuple[str, str, int]], int, int]:
    """返回 ([(本地路径, 归档内名字, 字节数)], 总字节, 跳过的符号链接数)。"""
    items: list[tuple[str, str, int]] = []
    total = 0
    skipped = 0
    for src in sources:
        base = os.path.basename(src.rstrip("/")) or "未命名"
        if os.path.islink(src):
            skipped += 1
            continue
        if os.path.isdir(src):
            items.append((src, base + "/", 0))
            for root, dirs, files in os.walk(src, followlinks=False,
                                             onerror=lambda e: None):
                rel_root = os.path.relpath(root, src)
                rel_root = "" if rel_root == "." else rel_root.replace(os.sep, "/")
                # 归档内名字必须带上源目录名（base），否则解出来是一堆散文件
                prefix = base + "/" + (rel_root + "/" if rel_root else "")
                dirs.sort()
                for d in list(dirs):
                    full = os.path.join(root, d)
                    if os.path.islink(full):
                        dirs.remove(d)
                        skipped += 1
                        continue
                    items.append((full, prefix + d + "/", 0))
                for f in sorted(files):
                    full = os.path.join(root, f)
                    if os.path.islink(full):
                        skipped += 1
                        continue
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        size = 0
                    items.append((full, prefix + f, size))
                    total += size
        else:
            try:
                size = os.path.getsize(src)
            except OSError:
                size = 0
            items.append((src, base, size))
            total += size
    return items, total, skipped


def compress_zip(sources, dest_path: str, cancel=None, tick=None) -> dict:
    """把一批本地路径压成一个 zip。dest_path 已含 .zip，调用方负责唯一命名。"""
    result = {"ok": 0, "failed": [], "skipped": 0, "cancelled": False, "path": ""}
    items, total, skipped = _plan_sources(sources)
    result["skipped"] = skipped
    if not items:
        result["failed"].append({"name": "", "error": "没有可压缩的内容"})
        return result

    part = dest_path + ".wspart"
    done = 0
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for src, arc, size in items:
                if cancel is not None and cancel.is_cancelled():
                    raise _Cancelled()
                if arc.endswith("/"):
                    zi = zipfile.ZipInfo(arc)
                    zi.external_attr = (0o40755 << 16) | 0x10
                    zi.compress_type = zipfile.ZIP_STORED
                    zf.writestr(zi, b"")
                    continue
                try:
                    zi = zipfile.ZipInfo.from_file(src, arc)
                except (OSError, ValueError) as exc:
                    result["failed"].append({"name": arc, "error": str(exc)})
                    continue
                ext = os.path.splitext(arc)[1].lower()
                zi.compress_type = (zipfile.ZIP_STORED if ext in _STORED_EXT
                                    else zipfile.ZIP_DEFLATED)
                try:
                    with open(src, "rb") as fi, zf.open(zi, "w") as fo:
                        while True:
                            if cancel is not None and cancel.is_cancelled():
                                raise _Cancelled()
                            buf = fi.read(CHUNK)
                            if not buf:
                                break
                            fo.write(buf)
                            done += len(buf)
                            if tick is not None:
                                tick(done, total)
                except OSError as exc:
                    result["failed"].append({"name": arc, "error": str(exc)})
                    continue
                result["ok"] += 1
        os.replace(part, dest_path)
    except _Cancelled:
        try:
            os.unlink(part)
        except OSError:
            pass
        result["cancelled"] = True
        return result
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        try:
            os.unlink(part)
        except OSError:
            pass
        result["failed"].append({"name": os.path.basename(dest_path), "error": str(exc)})
        return result
    if tick is not None:
        tick(total, total or 1)
    try:
        result["path"] = vpath.encode_path(dest_path)
    except (ValueError, TypeError):
        result["path"] = ""
    return result


def default_zip_name(sources) -> str:
    """默认包名：单个源用它的主干名（去掉任何扩展名），多个用「压缩包」。

    调用方再补 .zip 并做唯一命名。`报告.docx` → `报告.zip`、`a.zip` → `a.zip`。
    """
    names = [os.path.basename(s.rstrip("/")) for s in (sources or []) if s]
    if len(names) == 1:
        stem, _ext = os.path.splitext(names[0])
        return stem or names[0]
    return "压缩包"


def summary(container: str, cancel=None) -> dict:
    """压缩包摘要（预览窗用）：条目数、目录数、原始总大小、加密标记、格式。"""
    fmt = format_of(container)
    out = {"format": fmt or "未知", "count": 0, "dirCount": 0, "fileCount": 0,
           "total": 0, "encrypted": False, "error": "", "items": [], "truncated": False}
    if not fmt:
        out["error"] = "不认识的压缩包格式"
        return out
    try:
        members, encrypted = read_index(container, cancel)
    except _Cancelled:
        return out
    except ArchiveError as exc:
        out["error"] = str(exc)
        return out
    except (OSError, ValueError) as exc:
        out["error"] = f"读取压缩包失败：{exc}"
        return out
    files = [m for m in members if not m.is_dir]
    out["count"] = len(members)
    out["dirCount"] = len(members) - len(files)
    out["fileCount"] = len(files)
    out["total"] = sum(m.size for m in files)
    out["encrypted"] = bool(encrypted)
    out["truncated"] = len(members) >= MAX_MEMBERS
    # 只取顶层，预览窗里 12 条够看清楚「这是什么包」了。base 传入真实 vpath：
    # 传空串会让 vpath.join 抛错，条目全被吞掉（踩过一次）。
    base = vpath.encode_archive(container, "")
    top, _err, _tr = list_dir(container, "", base, False, cancel)
    out["items"] = [{"name": e.name, "sizeText": e.size_text, "kind": e.kind,
                     "kindLabel": kind_label(e.kind), "isDir": e.is_dir}
                    for e in top[:12]]
    return out
