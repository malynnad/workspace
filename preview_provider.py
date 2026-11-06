"""浮窗预览的 Qt 侧门面（M1a：完整但空闲）。

M1a 注册表为空 → available=False，按空格 / 双击文件不会有任何 UI。
M1b 只需在 filecore.preview 里 register(...) 四种预览器并接 requestArrived，
QML 侧一行都不用改。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot

from filecore.preview import base


class PreviewProvider(QObject):
    availableChanged = Signal()
    unavailable = Signal(str)
    requestArrived = Signal(str)          # M1b 接这个信号去做两阶段 prepare/view

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0                     # 防过期：连按方向键时只认最后一次

    @Property(bool, notify=availableChanged)
    def available(self):
        return len(base.REGISTRY) > 0

    @Slot()
    def refresh(self):
        self.availableChanged.emit()

    @Slot(str)
    def request(self, vpath: str):
        if not self.available or not vpath:
            self.unavailable.emit(vpath or "")
            return
        self._gen += 1
        self.requestArrived.emit(vpath)

    @Slot(result=int)
    def generation(self):
        return self._gen
