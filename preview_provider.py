"""浮窗预览的 Qt 侧门面：两阶段（prepare → view）+ 防过期。

主线程只做 <10ms 的 prepare（读 stat、选预览器、拼标题），立刻把 open=True
和标题/类型/大小发布给 QML —— 窗口先出来；耗时 I/O 交给 TaskRunner 的
preview 单线程池，回传纯数据后 payloadChanged 再填内容区。

防过期：每次 request / close 都把 _gen 加一，后台结果只认最后一次的 gen；
在途 job 通过 CancelToken 协作取消。连按 ↑↓ 时不会串出上一个文件的内容。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Property, Signal, Slot

from filecore.preview import base
from filecore.sources import UnknownScheme, resolve


def _view_job(previewer, entry, gen, cancel=None):
    """工作线程：只调零 Qt 的 previewer.view，异常一律转成中文结果。"""
    try:
        payload = previewer.view(entry, base.PreviewContext(cancel=cancel))
        return {"gen": gen, "kind": payload.kind,
                "data": payload.data if isinstance(payload.data, dict) else {}}
    except Exception as exc:
        return {"gen": gen, "error": f"预览失败：{type(exc).__name__}: {exc}"}


class PreviewProvider(QObject):
    availableChanged = Signal()
    unavailable = Signal(str)
    requestArrived = Signal(str)
    opened = Signal()
    closed = Signal()
    openChanged = Signal()
    specChanged = Signal()        # title / subtitle / kind / displayPath 变化
    payloadChanged = Signal()     # 内容区数据变化
    stateChanged = Signal()       # loading / ready / error
    currentVpathChanged = Signal()

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self._backend = backend
        self._gen = 0
        self._job = ""
        self._cur = ""
        self._open = False
        self._title = ""
        self._meta: dict = {}
        self._kind = ""
        self._payload: dict = {}
        self._state = "idle"
        self._error = ""

    # ---------------- Properties（QML 绑定） ----------------
    @Property(bool, notify=availableChanged)
    def available(self):
        return len(base.REGISTRY) > 0

    @Property(bool, notify=openChanged)
    def open(self):
        return self._open

    @Property(str, notify=specChanged)
    def title(self):
        return self._title

    @Property(str, notify=specChanged)
    def subtitle(self):
        parts = [self._meta.get("kindLabel", ""), self._meta.get("sizeText", ""),
                 self._meta.get("timeText", "")]
        return " · ".join(p for p in parts if p)

    @Property(str, notify=specChanged)
    def displayPath(self):
        return str(self._meta.get("display", ""))

    @Property(str, notify=specChanged)
    def kind(self):
        return self._kind

    @Property("QVariantMap", notify=payloadChanged)
    def payload(self):
        """内容区数据。QML 严禁直接当 model 用，必须 property var + Connections
        整体重新赋值（PySide6 的 Property(list/map) 不刷新视图的已知坑）。"""
        return dict(self._payload)

    @Property(str, notify=stateChanged)
    def state(self):
        return self._state

    @Property(str, notify=stateChanged)
    def errorText(self):
        return self._error

    @Property(bool, notify=stateChanged)
    def busy(self):
        return self._state == "loading"

    @Property(str, notify=currentVpathChanged)
    def currentVpath(self):
        return self._cur

    # ---------------- Slots ----------------
    @Slot()
    def refresh(self):
        self.availableChanged.emit()

    @Slot(str)
    def request(self, vpath: str):
        if not self.available or not vpath:
            self.unavailable.emit(vpath or "")
            return
        self._gen += 1
        gen = self._gen
        self._cancel_pending()
        try:
            source = resolve(vpath)
            if not source.exists(vpath):
                raise FileNotFoundError(vpath)
            entry = source.entry(vpath)
        except (UnknownScheme, ValueError, OSError) as exc:
            self._fail(f"无法读取文件信息：{exc}")
            return
        previewer = base.pick(entry)
        if previewer is None:
            self._fail("没有可用的预览器")
            return
        try:
            spec = previewer.prepare(entry)
        except Exception as exc:
            self._fail(f"预览准备失败：{exc}")
            return

        # 第一阶段：窗口立即出来（标题 + 类型/大小 + 加载态）
        self._cur = str(vpath)
        self._title = spec.title or entry.name
        self._meta = dict(spec.meta or {})
        self._kind = str(spec.kind)
        self._payload = {}
        self._error = ""
        self._state = "loading"
        self._set_open(True)
        self.specChanged.emit()
        self.payloadChanged.emit()
        self.stateChanged.emit()
        self.currentVpathChanged.emit()
        self.requestArrived.emit(vpath)

        # 第二阶段：后台 I/O，结果回主线程（runner.finished 是 QueuedConnection）
        self._job = self._backend._runner.submit(
            "preview", _view_job, previewer, entry, gen)

    @Slot()
    def close(self):
        if not self._open:
            return
        self._gen += 1
        self._cancel_pending()
        self._cur = ""
        self._title = ""
        self._meta = {}
        self._kind = ""
        self._payload = {}
        self._error = ""
        self._state = "idle"
        self._set_open(False)
        self.specChanged.emit()
        self.payloadChanged.emit()
        self.stateChanged.emit()
        self.currentVpathChanged.emit()
        # _set_open(False) 已经发过 closed，这里不要重复发

    @Slot(int)
    def step(self, delta):
        """浮窗打开时按 ↑↓ 预览同目录里的相邻项（QuickLook 习惯）。"""
        if not self._open or not self._cur:
            return
        vp = self._backend.active().adjacentVpath(self._cur, int(delta))
        if vp and vp != self._cur:      # 到列表边界时别重复加载同一个文件
            self.request(vp)

    @Slot(result=int)
    def generation(self):
        return self._gen

    # ---------------- 内部 ----------------
    @Slot(str, bool, "QVariantList")
    def _on_job_finished(self, job_id, ok, payload):
        """由 FileManagerBackend 把 runner.finished 接过来（QueuedConnection）。"""
        if job_id != self._job:
            return
        self._job = ""
        result = payload[0] if payload else {}
        if not isinstance(result, dict) or result.get("gen") != self._gen:
            return                                   # 过期结果直接丢弃
        if not ok or result.get("error"):
            self._fail(str(result.get("error") or "预览失败"))
            return
        self._payload = dict(result.get("data") or {})
        # payload.kind 允许覆盖 prepare 的判断（例如「小文件其实是二进制」）
        self._kind = str(result.get("kind") or self._kind)
        self._error = ""
        self._state = "ready"
        self.specChanged.emit()
        self.payloadChanged.emit()
        self.stateChanged.emit()

    def _fail(self, message):
        self._gen += 1
        self._cancel_pending()
        self._payload = {}
        self._error = message
        self._state = "error"
        self._set_open(True)
        self.payloadChanged.emit()
        self.stateChanged.emit()
        self.specChanged.emit()

    def _set_open(self, value):
        if value != self._open:
            self._open = value
            self.openChanged.emit()
            (self.opened if value else self.closed).emit()

    def _cancel_pending(self):
        if self._job:
            self._backend._runner.cancel(self._job)
            self._job = ""
