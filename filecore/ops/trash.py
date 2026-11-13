"""移到回收站（零 Qt，按 freedesktop Trash 规范自己实现）。

不调 `gio trash`：麒麟上 gio 可能装也可能没装，而规范本身很简单 ——
`<trash>/{files,info}/`，info 里写一份 `[Trash Info]` 文本即可。自己写的好处是
失败原因能精确回传（只读盘、无权限、跨设备），代价是要自己处理唯一名与回滚。

位置选择顺序（和 Nautilus/gio 一致）：
1. 文件与家目录同设备 → `~/.local/share/Trash`；
2. 不同设备 → 该设备挂载点的 `.Trash/<uid>`（已存在时）或 `.Trash-<uid>`（可创建时）；
3. 都不行 → 退回 home trash，跨设备复制一份再删源（会占家目录空间，但不会删失败）。
"""

from __future__ import annotations

import errno
import os
import shutil
from datetime import datetime
from urllib.parse import quote

from .. import vpath
from .naming import split_ext
from .transfer import copy_tree, human_error


def _home_trash() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "Trash")


def _ensure_tree(trash: str) -> bool:
    try:
        os.makedirs(os.path.join(trash, "files"), exist_ok=True)
        os.makedirs(os.path.join(trash, "info"), exist_ok=True)
        return True
    except OSError:
        return False


def _topdir(path: str) -> str:
    """文件所在挂载点（st_dev 变化的那一层）。"""
    dev = os.lstat(path).st_dev
    current = path if os.path.isdir(path) else os.path.dirname(path)
    current = os.path.abspath(current)
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return current
        try:
            if os.stat(parent).st_dev != dev:
                return current
        except OSError:
            return current
        current = parent


def trash_location(local_path: str) -> str:
    """给一个绝对路径挑回收站目录（不创建）。"""
    home = _home_trash()
    try:
        same_device = os.lstat(local_path).st_dev == os.stat(home).st_dev
    except OSError:
        same_device = True
    if same_device:
        return home
    try:
        top = _topdir(local_path)
    except OSError:
        return home
    uid = os.getuid()
    candidates = [os.path.join(top, ".Trash", str(uid)),
                  os.path.join(top, f".Trash-{uid}")]
    for cand in candidates:
        if os.path.isdir(cand) and os.access(cand, os.W_OK):
            return cand
    try:
        cand = candidates[1]
        os.makedirs(os.path.join(cand, "files"), exist_ok=True)
        os.makedirs(os.path.join(cand, "info"), exist_ok=True)
        return cand
    except OSError:
        return home


def _trashinfo(name: str, local_path: str) -> str:
    when = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return ("[Trash Info]\n"
            f"Path={quote(os.path.abspath(local_path), safe='/')}\n"
            f"DeletionDate={when}\n")


def trash_path(local_path: str, cancel=None) -> dict:
    """把 local_path 移到回收站。

    返回 {"ok": True, "name": ..., "trash": ..., "copied": bool}
    或   {"ok": False, "error": "..."}。
    """
    if not os.path.lexists(local_path):
        return {"ok": False, "error": "文件不存在"}
    base_name = os.path.basename(local_path.rstrip("/")) or local_path
    trash = trash_location(local_path)
    if not _ensure_tree(trash):
        return {"ok": False, "error": "无法写入回收站目录"}

    files_dir = os.path.join(trash, "files")
    info_dir = os.path.join(trash, "info")
    # files/ 与 info/ 里的名字必须一一对应：两边任一已存在就要换名
    stem, ext = split_ext(base_name)
    name = base_name
    index = 1
    while (os.path.exists(os.path.join(files_dir, name)) or
           os.path.exists(os.path.join(info_dir, name + ".trashinfo"))):
        name = f"{stem} ({index}){ext}"
        index += 1
        if index > 9999:
            return {"ok": False, "error": "回收站里同名副本太多"}

    target = os.path.join(files_dir, name)
    info_path = os.path.join(info_dir, name + ".trashinfo")
    try:
        with open(info_path, "x", encoding="utf-8") as f:
            f.write(_trashinfo(name, local_path))
    except OSError as exc:
        return {"ok": False, "error": human_error(exc)}

    same_device = True
    try:
        same_device = os.lstat(local_path).st_dev == os.stat(files_dir).st_dev
    except OSError:
        pass

    try:
        if same_device:
            os.rename(local_path, target)
            copied = False
        else:
            copy_tree(local_path, target, cancel=cancel)
            try:
                if os.path.islink(local_path) or not os.path.isdir(local_path):
                    os.unlink(local_path)
                else:
                    shutil.rmtree(local_path)
            except OSError:
                # 复制成功但源删不掉：把副本撤掉，保持「要么全成要么不动」
                try:
                    shutil.rmtree(target)
                except OSError:
                    pass
                raise
            copied = True
    except OSError as exc:
        try:
            os.unlink(info_path)
        except OSError:
            pass
        if isinstance(exc, OSError) and exc.errno == errno.EXDEV:
            return {"ok": False, "error": "跨设备移动失败"}
        return {"ok": False, "error": human_error(exc)}
    except Exception as exc:
        try:
            os.unlink(info_path)
        except OSError:
            pass
        return {"ok": False, "error": human_error(exc)}

    return {"ok": True, "name": name, "trash": trash, "copied": copied}


def trash_paths(local_paths) -> dict:
    """批量移到回收站，逐个失败不影响其它项。"""
    result = {"ok": 0, "failed": [], "names": []}
    for path in local_paths or []:
        item = trash_path(str(path))
        if item.get("ok"):
            result["ok"] += 1
            result["names"].append(item.get("name", ""))
        else:
            result["failed"].append({"name": os.path.basename(str(path)),
                                     "error": item.get("error", "移动失败")})
    return result


def permanent_delete(local_path: str) -> dict:
    """永久删除（不进回收站）。调用方必须已经做过二次确认。"""
    if not os.path.lexists(local_path):
        return {"ok": False, "error": "文件不存在"}
    try:
        if os.path.islink(local_path) or not os.path.isdir(local_path):
            os.unlink(local_path)
        else:
            shutil.rmtree(local_path)
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": human_error(exc)}


def vpath_to_trash(v: str) -> dict:
    try:
        return trash_path(vpath.to_local(v))
    except (ValueError, TypeError):
        return {"ok": False, "error": "路径无效"}
