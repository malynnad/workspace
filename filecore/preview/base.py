"""QuickLook 式两阶段预览器协议。

参照 QuickLook 的 IViewer：prepare 只做 <10ms 的元数据工作（窗口标题/类型/大小
立刻可显示），view 才做耗时 I/O —— 这正是「先出窗口后出内容」、治大图预览卡顿
的关键。view 一律跑在 TaskRunner 的 preview 池里，回传的 PreviewPayload 只装
str/int/bool/dict/list，绝不装 QImage / QPixmap（工作线程不能碰 Qt GUI 类）。

M1a 只留空注册表；M1b 由 preview/__init__.py 的 bootstrap() 注册四种内置预览器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_TEXT_BYTES = 256 * 1024        # 文本预览最多读 256 KB
MAX_TEXT_LINES = 4000              # 最多 4000 行，避免超长单行撑爆 TextEdit
MAX_IMAGE_BYTES = 32 * 1024 * 1024  # 超过 32 MB 不交给 QML Image 解码
MAX_LIST_ITEMS = 12                # 目录摘要最多列 12 项


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


@dataclass(frozen=True)
class PreviewContext:
    """view 阶段的限额与取消令牌（纯 Python，跨线程传值安全）。"""
    cancel: Any = None
    max_text_bytes: int = MAX_TEXT_BYTES
    max_text_lines: int = MAX_TEXT_LINES
    max_image_bytes: int = MAX_IMAGE_BYTES
    max_list_items: int = MAX_LIST_ITEMS
    # Office 版面（LibreOffice 转 PDF）总开关。只有 office 预览器读它，其余类型
    # 忽略。由设置面板经 FileManagerBackend 传进来 —— 不做成模块级全局变量，
    # 是为了让探针能直接构造「关掉」的 ctx 验证回退路径。
    office_layout: bool = True


def common_meta(entry) -> dict:
    """四种预览器共用的标题栏字段；不在 prepare 里读文件内容。"""
    from .. import vpath
    from ..entries import kind_label
    return {
        "kindLabel": kind_label(entry.kind),
        "sizeText": entry.size_text,
        "timeText": entry.time_text,
        "display": vpath.display(entry.vpath),
        "isDir": bool(entry.is_dir),
        "name": entry.name,
    }


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
