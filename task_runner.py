"""后台任务调度（Qt 层）。

三池常量按前序定稿一次写全：IO_POOL=3 / PREVIEW_POOL=1 / OP_POOL=1。
M1a 只实例化 io 池（8GB 内存吃紧，不预建空池）；M1b 加了 "preview" 单线程池
（预览任务串行，连按方向键时旧任务直接丢弃），M1c 再加 "op"。

worker 只回传纯数据（list/dict/str），绝不回传自定义对象或 QImage；
finished 信号用 QueuedConnection 接回主线程，模型操作一律在主线程做。
"""

from __future__ import annotations

import itertools
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from filecore.cancel import CancelToken

IO_POOL = 3
PREVIEW_POOL = 1
OP_POOL = 1


class _Task(QRunnable):
    def __init__(self, job_id, fn, args, token, runner):
        super().__init__()
        self._job_id = job_id
        self._fn = fn
        self._args = args
        self._token = token
        self._runner = runner

    def run(self):
        try:
            result = self._fn(*self._args, cancel=self._token)
            self._runner._on_done(self._job_id, True, [result])
        except Exception as exc:                      # 异常一律转成失败信号
            try:
                self._runner._on_done(self._job_id, False,
                                      [f"{type(exc).__name__}: {exc}"])
            except RuntimeError:
                pass                                  # 应用正在退出，runner 已销毁


class TaskRunner(QObject):
    finished = Signal(str, bool, "QVariantList")     # jobId, ok, [result] / [errmsg]
    progress = Signal(str, int)                     # jobId, 0..100（M1c 传输用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pools = {"io": QThreadPool(self), "preview": QThreadPool(self)}
        self._pools["io"].setMaxThreadCount(IO_POOL)
        self._pools["preview"].setMaxThreadCount(PREVIEW_POOL)
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._tokens: dict[str, CancelToken] = {}
        self._tasks: dict[str, _Task] = {}          # 持有 Python wrapper，防 GC
        self._done: list[str] = []

    def submit(self, kind: str, fn, *args) -> str:
        job_id = f"j{next(self._counter)}"
        token = CancelToken()
        task = _Task(job_id, fn, args, token, self)
        with self._lock:
            self._tokens[job_id] = token
            self._tasks[job_id] = task
        self._pools.get(kind, self._pools["io"]).start(task)
        return job_id

    def cancel(self, job_id: str):
        with self._lock:
            token = self._tokens.get(job_id)
        if token:
            token.cancel()

    def _on_done(self, job_id, ok, payload):
        # 从工作线程调用；finished 由接收方用 QueuedConnection 接回主线程
        with self._lock:
            self._tokens.pop(job_id, None)
            # 不在这里 pop _tasks：此刻 run() 还在栈上，删掉最后一个 Python
            # 引用会让 QRunnable wrapper 在执行中途被回收。只记下已完成的 id，
            # 等攒够一批再清理旧任务（那时 run() 早已返回）。
            self._done.append(job_id)
            if len(self._done) > 256:
                for old in self._done[:-64]:
                    self._tasks.pop(old, None)
                self._done = self._done[-64:]
        try:
            self.finished.emit(job_id, ok, payload)
        except RuntimeError:
            pass                                      # 退出过程中 QObject 已销毁

    def shutdown(self):
        """应用退出前调用：取消在途任务并等线程池收工，避免 emit 到已删除对象。"""
        with self._lock:
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()
        for pool in self._pools.values():
            try:
                pool.waitForDone(2000)
            except RuntimeError:
                pass
