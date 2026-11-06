"""QuickLook 式两阶段预览器协议（M1a：协议完整、注册表为空）。

参照 QuickLook 的 IViewer（reference/插件体系研究笔记 :66-94）：
prepare 只做 <10ms 的元数据工作，view 才做耗时加载 —— 这正是「先出窗口后出内容」、
治 3~4MB 图片预览卡顿的关键。

本步刻意不写任何抛 NotImplementedError 的空方法：
协议 + pick() 完整可用，只是 REGISTRY 为空所以恒返回 None；
M1b 只要 register(...) 四次，上层和 QML 一行都不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PreviewSpec:
    kind: str = "fallback"
    title: str = ""
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PreviewPayload:
    """只装纯数据（str/int/list/bytes），绝不装 QImage / QPixmap。"""
    kind: str
    data: Any = None


class Previewer(Protocol):
    priority: int

    def can_handle(self, entry) -> bool: ...
    def prepare(self, entry) -> PreviewSpec: ...
    def view(self, entry, ctx) -> PreviewPayload: ...
    def cleanup(self) -> None: ...


REGISTRY: list = []


def register(previewer: Previewer) -> None:
    REGISTRY.append(previewer)
    REGISTRY.sort(key=lambda p: getattr(p, "priority", 0), reverse=True)


def pick(entry):
    """priority 降序，首个 can_handle 命中者胜；单个插件出错不影响匹配链。"""
    for previewer in REGISTRY:
        try:
            if previewer.can_handle(entry):
                return previewer
        except Exception:
            continue
    return None


def clear() -> None:
    REGISTRY.clear()
