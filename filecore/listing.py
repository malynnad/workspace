"""目录扫描、排序与过滤（纯 Python，零 Qt）。

旧版来源：filemanager.py:5834-5871（_scan 单趟 os.scandir）、:5530-5549（natural_key）、
:5552-5557（SORT_KEYS）、:5893-5904（set_filter）、:579-581（分块常量）。

注意：旧版 SORT_KEYS 用 (not e["dir"], key) 配 reverse=desc，降序时目录会被翻到
文件后面。这里改为「先按 is_dir 分组，desc 只作用于列 key」，目录恒优先。
"""

from __future__ import annotations

import os
import re

from .entries import Entry, make_entry
from .vpath import is_hidden, join

MAX_ITEMS = 20000          # 大目录上限，软渲染下避免卡死
CHUNK_SIZE = 200           # 模型每批插入条数
CHUNK_THRESHOLD = 1200     # 超过此值改成分批填充


def natural_key(name: str) -> list:
    """自然序：f2 < f10；数字段带原串长度保证同值稳定。"""
    parts = re.split(r"(\d+)", (name or "").lower())
    key = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part), len(part)))
        else:
            key.append((1, part, 0))
    return key


def _column_key(e: Entry, col: int):
    if col == 0:
        return natural_key(e.name)
    if col == 1:
        return (e.size, natural_key(e.name))
    if col == 2:
        return (e.kind, e.name.lower())
    return (e.mtime, natural_key(e.name))


def sort_entries(entries: list[Entry], col: int = 0, desc: bool = False) -> list[Entry]:
    """目录恒在文件前；desc 只影响列内的顺序。"""
    col = col if col in (0, 1, 2, 3) else 0
    dirs = [e for e in entries if e.is_dir]
    files = [e for e in entries if not e.is_dir]
    return (sorted(dirs, key=lambda e: _column_key(e, col), reverse=desc)
            + sorted(files, key=lambda e: _column_key(e, col), reverse=desc))


def visible_entries(entries: list[Entry], filt: str) -> list[Entry]:
    """大小写不敏感子串匹配名称，只作用于当前目录（不递归）。"""
    needle = (filt or "").strip().lower()
    if not needle:
        return list(entries)
    return [e for e in entries if needle in e.name.lower()]


def _dict_column_key(r: dict, col: int):
    name = r.get("name", "")
    if col == 0:
        return natural_key(name)
    if col == 1:
        return (int(r.get("size", 0)), natural_key(name))
    if col == 2:
        return (r.get("kind", ""), name.lower())
    return (float(r.get("mtime", 0)), natural_key(name))


def sort_dicts(rows: list[dict], col: int = 0, desc: bool = False) -> list[dict]:
    """对主线程缓存的 dict 行做同样的排序（过滤/排序不重新扫盘）。"""
    col = col if col in (0, 1, 2, 3) else 0
    dirs = [r for r in rows if r.get("isDir")]
    files = [r for r in rows if not r.get("isDir")]
    return (sorted(dirs, key=lambda r: _dict_column_key(r, col), reverse=desc)
            + sorted(files, key=lambda r: _dict_column_key(r, col), reverse=desc))


def visible_dicts(rows: list[dict], filt: str) -> list[dict]:
    needle = (filt or "").strip().lower()
    if not needle:
        return list(rows)
    return [r for r in rows if needle in r.get("name", "").lower()]


def scan_dir(abs_path: str, base_vpath: str, show_hidden: bool = False,
             cancel=None) -> tuple[list[Entry], str | None, bool]:
    """单趟扫描目录，返回 (条目, 错误文案, 是否因上限截断)。

    cancel 是 CancelToken（可为 None）；每 512 项检查一次。
    错误一律转成中文文案返回，绝不把异常抛到 QML。
    旧版对应 _scan :5834-5871 与分块填充 :5906-5956 的取数部分。
    """
    entries: list[Entry] = []
    try:
        with os.scandir(abs_path) as it:
            for i, de in enumerate(it):
                if cancel is not None and (i & 511) == 0 and cancel.is_cancelled():
                    return entries, None, False
                name = de.name
                if not show_hidden and is_hidden(name):
                    continue
                try:
                    st = de.stat(follow_symlinks=False)
                    is_dir = de.is_dir(follow_symlinks=False)
                    size = 0 if is_dir else st.st_size
                    mtime = st.st_mtime
                except OSError:
                    # 断链符号链接 / 竞态删除：降级成一条 0 字节普通项，不整目录失败
                    is_dir, size, mtime = False, 0, 0.0
                try:
                    vp = join(base_vpath, name)
                except ValueError:
                    continue
                entries.append(make_entry(name, vp, is_dir, size, mtime))
                if len(entries) >= MAX_ITEMS:
                    return entries, None, True
    except PermissionError:
        return [], "没有权限读取此文件夹", False
    except FileNotFoundError:
        return [], "文件夹不存在", False
    except NotADirectoryError:
        return [], "不是文件夹", False
    except OSError as exc:
        return [], f"读取失败：{exc}", False
    return entries, None, False
