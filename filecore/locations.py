"""常用位置（纯 Python，零 Qt）。

搬自旧 PyQt5 版 filemanager.py:6253-6305 的思路，但只拷逻辑、不 import 旧模块
（它顶层就引入 PyQt5）。Linux 上按 XDG user-dirs.dirs 解析真实目录名，
避免麒麟中文家目录下硬编码 Desktop/Documents 找不到。

返回给 QML 的一律是 vpath，不是绝对路径：
cwd / history / state / 地址栏都统一用 vpath，M2 的 zip+file:// 也能兼容。
"""

from __future__ import annotations

import os
import sys

from . import vpath

_XDG_KEYS = ("DESKTOP", "DOCUMENTS", "DOWNLOAD", "PICTURES", "VIDEOS", "MUSIC")
_XDG_DEFAULTS = {
    "DESKTOP": "Desktop",
    "DOCUMENTS": "Documents",
    "DOWNLOAD": "Downloads",
    "PICTURES": "Pictures",
    "VIDEOS": "Videos",
    "MUSIC": "Music",
}
_CACHE: dict[tuple[str, str], dict[str, str]] = {}


def _xdg_user_dirs(home: str) -> dict[str, str]:
    """解析 ~/.config/user-dirs.dirs；缺项回退英文默认目录。"""
    cfg_dir = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    cache_key = (home, cfg_dir)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    out: dict[str, str] = {}
    path = os.path.join(cfg_dir, "user-dirs.dirs")
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                key = key.strip()
                if key.startswith("XDG_") and key.endswith("_DIR"):
                    key = key[4:-4]
                val = raw.strip().strip('"').strip("'")
                if key not in _XDG_KEYS or not val:
                    continue
                val = val.replace("$HOME", home)
                if val.startswith("$"):          # 未展开的其它变量，忽略
                    continue
                out[key] = os.path.abspath(os.path.expanduser(val))
    except (OSError, UnicodeError):
        pass

    for key, rel in _XDG_DEFAULTS.items():
        out.setdefault(key, os.path.join(home, rel))
    _CACHE[cache_key] = out
    return out


def standard_locations(home: str) -> list[dict]:
    """常用位置（只读分组）。只返回真实存在的目录，且按绝对路径去重。

    -> [{"name": str, "vpath": str}, ...]
    """
    pairs: list[tuple[str, str]] = [("主目录", home)]
    if sys.platform == "darwin":
        pairs += [
            ("桌面", os.path.join(home, "Desktop")),
            ("文稿", os.path.join(home, "Documents")),
            ("下载", os.path.join(home, "Downloads")),
            ("图片", os.path.join(home, "Pictures")),
            ("影片", os.path.join(home, "Movies")),
            ("音乐", os.path.join(home, "Music")),
        ]
    elif sys.platform.startswith("linux"):
        dirs = _xdg_user_dirs(home)
        pairs += [
            ("桌面", dirs["DESKTOP"]),
            ("文档", dirs["DOCUMENTS"]),
            ("下载", dirs["DOWNLOAD"]),
            ("图片", dirs["PICTURES"]),
            ("影片", dirs["VIDEOS"]),
            ("音乐", dirs["MUSIC"]),
        ]

    result: list[dict] = []
    seen: set[str] = set()
    for name, path in pairs:
        try:
            real = os.path.abspath(path)
            if real in seen or not os.path.isdir(real):
                continue
            seen.add(real)
            result.append({"name": name, "vpath": vpath.encode_path(real)})
        except (OSError, ValueError, TypeError):
            continue
    return result
