"""浏览状态读写（纯 Python，零 Qt）。

存 ~/.config/workspace/filemanager_state.json（WORKSPACE_HOME 可覆盖），
**不动 workspace.db**。写法照旧版 MetaCache.flush（filemanager.py:1052-1064）：
先写 .tmp 再 os.replace，同分区原子替换。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import vpath

VERSION = 1
DEFAULT_LEFT_WIDTH = 483
MIN_PANEL_WIDTH = 320
DEFAULT_TOTAL_WIDTH = 986

_PANEL_KEYS = ("cwd", "sortColumn", "sortDesc", "showHidden", "history", "histPos")


def default_state(home: str) -> dict:
    h = vpath.encode_path(home)
    panel = {
        "cwd": h,
        "sortColumn": 0,
        "sortDesc": False,
        "showHidden": False,
        "history": [h],
        "histPos": 0,
    }
    return {
        "version": VERSION,
        "activeSide": "left",
        "leftWidth": DEFAULT_LEFT_WIDTH,
        "panels": {"left": dict(panel), "right": dict(panel)},
    }


def clamp_width(w, total: int = DEFAULT_TOTAL_WIDTH) -> int:
    try:
        value = int(w)
    except (TypeError, ValueError):
        return DEFAULT_LEFT_WIDTH
    high = max(MIN_PANEL_WIDTH, total - MIN_PANEL_WIDTH)
    return max(MIN_PANEL_WIDTH, min(high, value))


def load(path, home: str) -> dict:
    """读状态。坏 JSON / 版本不符 / 缺字段一律逐项回退，绝不抛。"""
    base = default_state(home)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return base
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return base

    out = dict(base)
    if data.get("activeSide") in ("left", "right"):
        out["activeSide"] = data["activeSide"]
    out["leftWidth"] = clamp_width(data.get("leftWidth", DEFAULT_LEFT_WIDTH))

    panels = data.get("panels")
    if not isinstance(panels, dict):
        return out
    for side in ("left", "right"):
        src = panels.get(side)
        if not isinstance(src, dict):
            continue
        dst = dict(base["panels"][side])
        for key in _PANEL_KEYS:
            if key in src:
                dst[key] = src[key]
        try:
            dst["sortColumn"] = int(dst["sortColumn"])
        except (TypeError, ValueError):
            dst["sortColumn"] = 0
        if dst["sortColumn"] not in (0, 1, 2, 3):
            dst["sortColumn"] = 0
        dst["sortDesc"] = bool(dst["sortDesc"])
        dst["showHidden"] = bool(dst["showHidden"])
        if not isinstance(dst["history"], list):
            dst["history"] = [dst["cwd"]]
        dst["history"] = [str(x) for x in dst["history"] if isinstance(x, str)][:100]
        try:
            dst["histPos"] = int(dst["histPos"])
        except (TypeError, ValueError):
            dst["histPos"] = 0
        if not dst["history"]:
            dst["history"] = [dst["cwd"]]
        dst["histPos"] = max(0, min(len(dst["history"]) - 1, dst["histPos"]))
        out["panels"][side] = dst
    out["panels"]["left"] = dict(out["panels"]["left"])
    out["panels"]["right"] = dict(out["panels"]["right"])
    return out


def save(path, data: dict) -> bool:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except (OSError, TypeError, ValueError):
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def validate(value, home: str, source) -> str:
    """把配置里的 cwd 校验成可读目录；失败逐级 parent 回退到家目录。

    不存在的目录、无权限、未注册的 scheme（例如 M2 之前遇到 zip+file://）
    都会安全回退，不会让启动时卡在无效路径上。
    """
    fallback = vpath.encode_path(home)
    try:
        cur = vpath.normalize(value)
    except (ValueError, TypeError):
        return fallback
    if not cur:
        return fallback

    # 未注册的 scheme（M2 之前的 zip+file://）直接回退，不要顺着 parent 掉进文件系统根
    from .sources import SOURCE_REGISTRY
    known = set(SOURCE_REGISTRY) or {getattr(source, "scheme", "file")}
    try:
        if vpath.parse(cur)[0] not in known:
            return fallback
    except (ValueError, TypeError):
        return fallback

    root = vpath.encode_path(os.sep)
    for _ in range(64):
        if cur == root:
            break
        try:
            if source.exists(cur) and source.is_dir(cur):
                return cur
        except Exception:
            pass
        try:
            up = vpath.parent(cur)
        except (ValueError, TypeError):
            break
        if up == cur:
            break
        cur = up
    return fallback
