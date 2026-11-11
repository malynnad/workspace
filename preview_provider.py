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


def _view_job(previewer, entry, gen, password="", cancel=None):
    """工作线程：只调零 Qt 的 previewer.view，异常一律转成中文结果。

    PDF 是唯一需要「Qt 侧补一刀」的类型：真正的栅格化要用 PyMuPDF / QPdfDocument，
    而 filecore 里不能出现 Qt 与第三方渲染库。这里只做**探测**（页数、每页尺寸、
    密码状态、token），页面本身由 QML 的 image://pdfpage/... 按需取 —— 所以
    「打开一个 PDF」是毫秒级的，不再等整篇渲完。
    password 只对加密 PDF 有用，其余类型忽略。
    """
    try:
        payload = previewer.view(entry, base.PreviewContext(cancel=cancel))
        data = payload.data if isinstance(payload.data, dict) else {}
        if payload.kind == "pdf" and not data.get("error"):
            data = _pdf_payload(data, password)
        return {"gen": gen, "kind": payload.kind, "data": data}
    except Exception as exc:
        return {"gen": gen, "error": f"预览失败：{type(exc).__name__}: {exc}"}


def _pdf_payload(data, password=""):
    """探测 PDF：把「能看多少页 / 每页多大 / 要不要密码」回传，不渲染任何页。"""
    merged = dict(data)
    local = merged.get("localPath") or ""
    if not local:
        return merged
    try:
        import pdf_pages
        merged.update(pdf_pages.probe(local, password=password))
    except Exception as exc:                     # 后端缺失 / 未打包时的兜底
        merged["error"] = f"PDF 渲染不可用：{type(exc).__name__}: {exc}"
    merged.pop("localPath", None)                # 内部路径不往 QML 递
    merged.pop("ok", None)
    return merged


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
        self._pv = None            # 当前预览器 / entry：PDF 第二段续渲要用
        self._entry = None
        self._pdf_rounds = 0
        self._password = ""        # 当前 PDF 的打开密码（换文件即清空）
        # 本次会话里解锁过的 PDF：vpath -> 密码。作用有两个：
        #   1. 反复打开同一份加密文件（逐个审核）不用一遍遍输密码；
        #   2. 上次渲染只渲了一部分就关掉时，重开能接着渲完 —— 没有密码就没法续渲。
        # 只存在内存里，关程序即失，不落盘。
        self._known: dict[str, str] = {}

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
        self._pv = previewer
        self._entry = entry
        self._password = self._known.get(str(vpath), "")
        self._job = self._backend._runner.submit(
            "preview", _view_job, previewer, entry, gen, self._password)

    @Slot(str)
    def unlock(self, password: str):
        """给加密 PDF 输密码后重开。

        为什么不「就地 setPassword 再接着用」：后端只在打开时用一次密码，
        load 失败后再设不会重试（状态永远停在 Error），所以整份重开一次。
        探测是毫秒级的，重开对用户是无感的。
        故意不切到 loading：留着密码框（按钮变「解锁中…」），密码错时不用重新敲。
        """
        if not self._open or self._kind != "pdf" or self._entry is None:
            return
        self._password = password or ""
        self._gen += 1
        self._cancel_pending()
        self._job = self._backend._runner.submit(
            "preview", _view_job, self._pv, self._entry, self._gen, self._password)

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
        self._pv = None
        self._entry = None
        self._password = ""
        self._release_pdf()
        self._set_open(False)
        self.specChanged.emit()
        self.payloadChanged.emit()
        self.stateChanged.emit()
        self.currentVpathChanged.emit()
        # _set_open(False) 已经发过 closed，这里不要重复发

    @Slot(int)
    def step(self, delta):
        """预览窗里按 ↑↓ 切换同目录相邻项（QuickLook 习惯）。

        同步把面板选中项挪过去：预览窗是独立窗口，用户在上面翻完一个再翻下一个，
        主窗口列表就得跟着高亮，否则关掉预览后不知道看的是哪个文件。
        """
        if not self._open or not self._cur:
            return
        panel = self._backend.active()
        vp = panel.adjacentVpath(self._cur, int(delta))
        if vp and vp != self._cur:      # 到列表边界时别重复加载同一个文件
            panel.select(vp, "single")
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
        if (self._kind == "pdf" and self._password and self._cur
                and not self._payload.get("needsPassword")):
            self._remember_password(self._cur, self._password)
        self.specChanged.emit()
        self.payloadChanged.emit()
        self.stateChanged.emit()

    def _release_pdf(self):
        """关预览时把页图 LRU 放掉（8GB 机器不养懒汉）。文档句柄留在
        pdf_pages 的登记表里（最多 6 份），再打开同一份文件就不用重解析。"""
        try:
            import pdf_image_provider
            pdf_image_provider.clear_cache()
        except Exception:
            pass

    def _remember_password(self, vpath: str, password: str):
        """记住本次会话里解锁成功的密码（最多 16 份，超了丢最早那条）。"""
        self._known.pop(vpath, None)
        self._known[vpath] = password
        while len(self._known) > 16:
            self._known.pop(next(iter(self._known)))

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
