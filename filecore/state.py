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

# VERSION 绝不能 bump：load() 遇版本不符直接返回默认值，
# 会把用户现有的 cwd / history / 收藏 / 面板比例全部清空。
VERSION = 1
DEFAULT_LEFT_WIDTH = 483          # 483 / 966 = 0.5
DEFAULT_TOTAL_WIDTH = 986         # 默认窗口 1240 反推，仅用于推导基准
BASE_WIDTH = 966                  # 986 - 20(拖拽手柄)：leftWidth 的归一化基准
MIN_RATIO = 0.30                  # 与 PANEL_MIN_WIDTH 联动，改动需重跑四档几何探针
WIDTH_LOW = 290                   # round(966 * 0.30)
WIDTH_HIGH = 676                  # 966 - 290，对称上限
PANEL_MIN_WIDTH = 180             # QML Layout.minimumWidth 的配套值

_PANEL_KEYS = ("cwd", "sortColumn", "sortDesc", "showHidden", "history", "histPos")


def _clean_favorites(raw) -> list[str]:
    """收藏项逐项回退：只收合法 vpath 字符串、去重、拒绝未知 scheme。

    僵尸路径（目录暂时不存在）**保留**，不静默删用户数据；渲染时灰显即可。
    vpath.normalize 会把未知 scheme 静默拼成本地相对路径，必须先挡住。
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            raw = item.strip()
            # 必须先校验原始 scheme：normalize() 会把未知 scheme 静默拼成
            # 本地相对路径（如 bogus://x -> file:///$PWD/x），之后再查就全是 file 了。
            if vpath.parse(raw)[0] not in (vpath.LOCAL, vpath.ARCHIVE):
                continue
            n = vpath.normalize(raw)
            scheme = vpath.parse(n)[0]
        except (ValueError, TypeError, OSError):
            continue
        if not n or scheme not in (vpath.LOCAL, vpath.ARCHIVE):
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        if len(out) >= 100:
            break
    return out


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
        "favorites": [],
        "panels": {"left": dict(panel), "right": dict(panel)},
    }


def clamp_width(w, total: int = DEFAULT_TOTAL_WIDTH) -> int:
    """leftWidth 是 966 基准下的归一化像素，不是屏幕像素。

    旧实现按 986 总宽钳在 [320, 666]，导致窗口最大化后拖拽手柄拖不动。
    改为对称比例边界 [290, 676]（约 0.30 ~ 0.70），与窗口宽度无关。
    total 形参保留只为兼容旧调用，不再使用。
    """
    try:
        value = int(w)
    except (TypeError, ValueError):
        return DEFAULT_LEFT_WIDTH
    return max(WIDTH_LOW, min(WIDTH_HIGH, value))


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
    out["favorites"] = _clean_favorites(data.get("favorites"))

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
