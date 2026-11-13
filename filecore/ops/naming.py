"""唯一命名：目标目录已有同名时生成「名称 (1).ext」（零 Qt）。

粘贴/移动/新建都用它，保证同一个冲突策略：**自动重命名，不覆盖、不弹窗**。
规则与桌面环境一致：`报告.pdf` → `报告 (1).pdf`；`归档` → `归档 (1)`；
隐藏文件 `.bashrc` 整体当主干（不把 `.bashrc` 当成「无名的 .bashrc 扩展名」）。
"""

from __future__ import annotations

import os

MAX_SUFFIX = 9999


def split_ext(name: str) -> tuple[str, str]:
    """拆主干与扩展名。`a.tar.gz` → (`a.tar`, `.gz`)；`.bashrc` → (`.bashrc`, ``)；`Makefile` → (`Makefile`, ``)"""
    name = name or ""
    if not name:
        return "", ""
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    stem, ext = os.path.splitext(name)
    if not stem:                     # ".tar.gz" 这种以点开头的多段名，整体当主干
        return name, ""
    return stem, ext


def candidate(name: str, index: int) -> str:
    stem, ext = split_ext(name)
    return f"{stem} ({index}){ext}"


def unique_name(directory: str, name: str, exists=None) -> str:
    """在 directory 里给 name 找一个不冲突的名字。exists 可注入（测试用）。"""
    check = exists or os.path.exists
    if not check(os.path.join(directory, name)):
        return name
    for i in range(1, MAX_SUFFIX + 1):
        guess = candidate(name, i)
        if not check(os.path.join(directory, guess)):
            return guess
    raise OSError(f"{name} 的副本太多，无法生成新名称")


def validate_name(name: str) -> str:
    """校验用户输入的文件名，返回空串表示合法，否则返回中文错误文案。"""
    text = (name or "").strip()
    if not text:
        return "名称不能为空"
    if text in (".", ".."):
        return "名称不能是 . 或 .."
    if "/" in text or "\0" in text:
        return "名称不能包含 /"
    if len(text.encode("utf-8")) > 255:
        return "名称太长"
    return ""
