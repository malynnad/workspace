"""复制 / 移动（零 Qt）。

对外只有 `run_transfer()` 一个入口，返回纯 dict（可跨线程回传）：

    {"ok": 3, "failed": [{"name": "...", "error": "没有权限"}],
     "paths": [目标 vpath...], "renamed": 1, "cancelled": False}

几条固定语义，改之前先想清楚：

* **同名一律自动重命名**（`报告.pdf` → `报告 (1).pdf`），绝不覆盖、绝不弹窗；
  同一批里两个同名源也不会撞车，靠 `taken` 占位。
* **移动同设备走 os.rename**（瞬时、保留全部属性）；跨设备 `EXDEV` 才降级成
  「先复制、校验成功再删源」。
* **分块复制**：1MB 一块，每块检查取消令牌，所以大文件能中途停，也会删掉半成品。
  不用 `shutil.copy2` —— 它是一整块 C 调用，既不能取消也给不出进度。
* 符号链接复制链接本身，不跟随（避免把外链内容拷进来）。
"""

from __future__ import annotations

import errno
import os
import shutil
import stat

from .. import vpath
from .naming import unique_name

CHUNK = 1024 * 1024          # 1MB：8GB 内存机器上够快，也不会把内存顶起来


class _Cancelled(Exception):
    pass


def human_error(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "没有权限"
    if isinstance(exc, FileNotFoundError):
        return "文件不存在"
    if isinstance(exc, IsADirectoryError):
        return "目标是一个文件夹"
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return "磁盘空间不足"
        if exc.errno == errno.ENAMETOOLONG:
            return "文件名过长"
        if exc.errno == errno.EROFS:
            return "只读文件系统"
        if exc.errno == errno.EXDEV:
            return "跨设备操作失败"
        return exc.strerror or str(exc)
    return f"{type(exc).__name__}: {exc}"


def _plan(src: str) -> tuple[list[tuple[str, int, bool]], int]:
    """枚举一个源要写哪些东西。

    返回 ([(相对路径, 字节数, 是否符号链接)], 总字节)。根项的相对路径是空串。
    目录项字节数为 0，但会出现在列表里 —— 靠它建目录并最后补 copystat。
    """
    if os.path.islink(src):
        return [("", 0, True)], 0
    if not os.path.isdir(src):
        try:
            size = os.path.getsize(src)
        except OSError:
            size = 0
        return [("", size, False)], size

    items: list[tuple[str, int, bool]] = [("", 0, False)]
    total = 0
    for root, dirs, files in os.walk(src, followlinks=False, onerror=lambda e: None):
        rel_root = os.path.relpath(root, src)
        if rel_root == ".":
            rel_root = ""
        # os.walk 默认 topdown，父目录先于子目录出现，建目录顺序天然正确
        for d in dirs:
            rel = os.path.join(rel_root, d) if rel_root else d
            items.append((rel, 0, os.path.islink(os.path.join(root, d))))
        for f in files:
            rel = os.path.join(rel_root, f) if rel_root else f
            full = os.path.join(root, f)
            if os.path.islink(full):
                items.append((rel, 0, True))
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            items.append((rel, size, False))
            total += size
    return items, total


def _tick(tick, done: int, total: int):
    if tick is not None:
        tick(done, total)


def _copy_file(src: str, dst: str, cancel, tick, state: dict):
    part = dst + ".wspart"
    try:
        with open(src, "rb") as fi, open(part, "wb") as fo:
            while True:
                if cancel is not None and cancel.is_cancelled():
                    raise _Cancelled()
                buf = fi.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
                state["done"] += len(buf)
                _tick(tick, state["done"], state["total"])
        try:
            shutil.copystat(src, part)
        except OSError:
            pass
        os.replace(part, dst)
    except BaseException:
        # 取消或失败都别留半成品（.wspart 也不能留）
        try:
            os.unlink(part)
        except OSError:
            pass
        raise


def _copy_entry(src: str, dst: str, items, cancel, tick, state):
    """把 src（文件 / 目录 / 链接）写到 dst。调用方保证 dst 不存在。"""
    multi = len(items) > 1 or items[0][0] != ""
    if not multi:
        rel, size, is_link = items[0]
        if is_link:
            os.symlink(os.readlink(src), dst)
            return
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            return
        _copy_file(src, dst, cancel, tick, state)
        return

    dirs: list[str] = []
    for rel, size, is_link in items:
        if cancel is not None and cancel.is_cancelled():
            raise _Cancelled()
        target = os.path.join(dst, rel) if rel else dst
        full_src = os.path.join(src, rel) if rel else src
        if is_link:
            os.makedirs(os.path.dirname(target) or dst, exist_ok=True)
            os.symlink(os.readlink(full_src), target)
        elif size == 0 and os.path.isdir(full_src) and not os.path.islink(full_src):
            os.makedirs(target, exist_ok=True)
            dirs.append(full_src)
        else:
            os.makedirs(os.path.dirname(target) or dst, exist_ok=True)
            _copy_file(full_src, target, cancel, tick, state)

    # 目录的权限/时间留到最后补，否则写文件过程中会被改掉
    for full_src in reversed(dirs):
        rel = os.path.relpath(full_src, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        try:
            shutil.copystat(full_src, target)
        except OSError:
            pass


def _remove_source(src: str):
    if os.path.islink(src) or not os.path.isdir(src):
        os.unlink(src)
    else:
        shutil.rmtree(src)


def _move_entry(src: str, dst: str, items, cancel, tick, state):
    try:
        os.rename(src, dst)
        state["done"] += state["size"]
        _tick(tick, state["done"], state["total"])
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    # 跨设备：复制成功后再删源。中途取消就只留副本、源保持不动。
    _copy_entry(src, dst, items, cancel, tick, state)
    _remove_source(src)


def copy_tree(src: str, dst: str, cancel=None, tick=None) -> None:
    """整棵复制一个路径（供回收站跨设备搬运复用）。失败抛 OSError。"""
    items, total = _plan(src)
    state = {"done": 0, "total": total, "size": total}
    _copy_entry(src, dst, items, cancel, tick, state)


def run_transfer(src_vpaths, dest_dir_vpath, move: bool = False,
                 cancel=None, tick=None) -> dict:
    """把 src_vpaths 复制/移动到 dest_dir_vpath 里。

    tick(done_bytes, total_bytes) 由调用方做节流，这里每 1MB 调一次。
    """
    result = {"ok": 0, "failed": [], "paths": [], "renamed": 0, "cancelled": False}

    try:
        dest_dir = vpath.to_local(dest_dir_vpath)
    except (ValueError, TypeError):
        result["failed"].append({"name": "", "error": "目标路径无效"})
        return result
    if not os.path.isdir(dest_dir):
        result["failed"].append({"name": "", "error": "目标不是文件夹"})
        return result

    sources = []
    total = 0
    for v in src_vpaths or []:
        try:
            src = vpath.to_local(v)
        except (ValueError, TypeError):
            result["failed"].append({"name": str(v), "error": "路径无效"})
            continue
        if not os.path.lexists(src):
            result["failed"].append({"name": os.path.basename(src), "error": "文件不存在"})
            continue
        try:
            items, size = _plan(src)
        except OSError as exc:
            result["failed"].append({"name": os.path.basename(src),
                                     "error": human_error(exc)})
            continue
        sources.append((src, items, size))
        total += size

    if not sources:
        return result

    dest_real = os.path.realpath(dest_dir)
    taken: set[str] = set()
    state = {"done": 0, "total": total, "size": 0}
    _tick(tick, 0, total)

    for src, items, size in sources:
        if cancel is not None and cancel.is_cancelled():
            result["cancelled"] = True
            break

        name = os.path.basename(src.rstrip("/")) or src
        src_real = os.path.realpath(src)
        if os.path.isdir(src_real) and (dest_real == src_real or
                                        dest_real.startswith(src_real + os.sep)):
            result["failed"].append({"name": name, "error": "不能放进它自己里面"})
            continue

        def taken_exists(p, _taken=taken):
            return p in _taken or os.path.exists(p)

        try:
            final_name = unique_name(dest_dir, name, exists=taken_exists)
        except OSError as exc:
            result["failed"].append({"name": name, "error": human_error(exc)})
            continue
        taken.add(os.path.join(dest_dir, final_name))
        if final_name != name:
            result["renamed"] += 1
        dst = os.path.join(dest_dir, final_name)
        state["size"] = size

        if move and os.path.lexists(dst):
            result["failed"].append({"name": name, "error": "目标已存在"})
            continue

        try:
            if move:
                _move_entry(src, dst, items, cancel, tick, state)
            else:
                _copy_entry(src, dst, items, cancel, tick, state)
        except _Cancelled:
            # 取消：删掉这一项留下的半成品，已完成的项保留
            try:
                if os.path.lexists(dst):
                    _remove_source(dst)
            except OSError:
                pass
            result["cancelled"] = True
            break
        except OSError as exc:
            if os.path.lexists(dst):
                _safe_remove(dst)
            result["failed"].append({"name": name, "error": human_error(exc)})
            continue
        except Exception as exc:                            # 兜底：不把异常抛到 UI
            if os.path.lexists(dst):
                _safe_remove(dst)
            result["failed"].append({"name": name, "error": human_error(exc)})
            continue

        result["ok"] += 1
        try:
            result["paths"].append(vpath.encode_path(dst))
        except (ValueError, TypeError):
            pass

    _tick(tick, total, total)
    return result


def _safe_remove(path: str):
    try:
        _remove_source(path)
    except OSError:
        pass


def make_directory(dest_dir_vpath: str, name: str) -> tuple[str, str]:
    """新建文件夹。返回 (新目录 vpath, 错误文案)。同名时自动加序号。"""
    try:
        dest_dir = vpath.to_local(dest_dir_vpath)
    except (ValueError, TypeError):
        return "", "目标路径无效"
    if not os.path.isdir(dest_dir):
        return "", "目标不是文件夹"
    try:
        final = unique_name(dest_dir, name)
        target = os.path.join(dest_dir, final)
        os.mkdir(target)
        return vpath.encode_path(target), ""
    except OSError as exc:
        return "", human_error(exc)


def rename_item(src_vpath: str, new_name: str) -> tuple[str, str]:
    """原地改名。返回 (新 vpath, 错误文案)。同名覆盖不允许。"""
    from .naming import validate_name

    err = validate_name(new_name)
    if err:
        return "", err
    try:
        src = vpath.to_local(src_vpath)
    except (ValueError, TypeError):
        return "", "路径无效"
    parent = os.path.dirname(src.rstrip("/"))
    target = os.path.join(parent, new_name.strip())
    if os.path.abspath(target) == os.path.abspath(src):
        return vpath.encode_path(src), ""
    if os.path.lexists(target):
        return "", "同名文件已存在"
    try:
        os.rename(src, target)
        return vpath.encode_path(target), ""
    except OSError as exc:
        return "", human_error(exc)


def is_same_tree(a: str, b: str) -> bool:
    ra, rb = os.path.realpath(a), os.path.realpath(b)
    return ra == rb or rb.startswith(ra + os.sep)


def file_mode_text(path: str) -> str:
    try:
        return stat.filemode(os.lstat(path).st_mode)
    except OSError:
        return ""
