"""协作式取消令牌（纯 Python，零 Qt）。

M1a 只用到目录扫描的中途取消；M1c 传输任务会 attach 子进程句柄，
取消时 terminate()，由调用方在 2 秒后升级为 kill()。
"""

from __future__ import annotations

import threading


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()
        self._procs: list = []
        self._lock = threading.Lock()

    def cancel(self):
        self._ev.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass

    def is_cancelled(self) -> bool:
        return self._ev.is_set()

    def attach(self, proc):
        with self._lock:
            self._procs.append(proc)
        if self._ev.is_set():
            try:
                proc.terminate()
            except Exception:
                pass

    def detach(self, proc):
        """子进程正常结束后摘掉句柄，别让 token 一直捏着已经退出的 Popen 对象。"""
        with self._lock:
            try:
                self._procs.remove(proc)
            except ValueError:
                pass
