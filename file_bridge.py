"""双面板浏览的 Qt 桥接层（QML 只认这一层）。

FileManagerBackend  ->  left / right 两个 FilePanel + preview + activeSide
FilePanel           ->  一侧的 cwd / history / 排序 / 过滤 / 选中 / 状态 / 列表模型

M1a 只读。写操作、预览器、压缩包源都通过「注册表 + capabilities 能力位」留白：
不写抛 NotImplementedError 的空 Slot，QML 把未来控件绑到 capabilities 上，
后续里程碑只翻位 + 加 Slot，布局零改动。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (QByteArray, QFileSystemWatcher, QMimeData, QObject,
                            Property, QProcess, QTimer, Qt, QUrl, Signal, Slot)
from PySide6.QtGui import QDesktopServices, QGuiApplication

import filecore
from filecore import archives, listing, locations, state, vpath
from filecore import ops
from filecore.entries import kind_label
from filecore.sources import UnknownScheme, capabilities_of, resolve
from filecore.sources.local import LocalSource
from file_model import FileListModel
from preview_provider import PreviewProvider
from task_runner import TaskRunner

HOME = str(Path.home())

# 「在终端中打开」按顺序试这几个。`{dir}` 会被替换成工作目录；
# 没有占位符的（x-terminal-emulator / xterm）靠 Popen 的 cwd 参数进去。
_TERMINALS = [
    ("x-terminal-emulator", []),
    ("konsole", ["--workdir", "{dir}"]),
    ("mate-terminal", ["--working-directory", "{dir}"]),
    ("ukui-terminal", ["--working-directory", "{dir}"]),
    ("gnome-terminal", ["--working-directory", "{dir}"]),
    ("xfce4-terminal", ["--working-directory", "{dir}"]),
    ("terminator", ["--working-directory", "{dir}"]),
    ("alacritty", ["--working-directory", "{dir}"]),
    ("kitty", ["--directory", "{dir}"]),
    ("xterm", []),
]


class CwdWatcher(QObject):
    """监听若干目录的「条目变化」（M2 目录自动刷新）。

    Linux 下 QFileSystemWatcher 走 inotify，对目录只报**条目增删改名**（往里写文件内容
    不报），正好是重扫列表需要的信号，成本是每个目录一个 watch。

    为什么自己再包一层防抖 + 最小间隔：
    * 解压、批量拷入这类操作会连着打出几十个 directoryChanged，不合并就是几十次全目录
      重扫 —— 8GB 的麒麟机会直接卡住；
    * 扫描本身是异步的，间隔太短会不停 cancel 上一轮扫描，大目录永远扫不完。所以
      两次刷新之间强制留 `MIN_GAP_MS`，并且调用方还会在 busy 时跳过。

    只监听当前目录、**不递归**：递归 watch 一个几万文件的工程目录会吃光 inotify 配额。
    """

    changed = Signal(str)          # 参数：发生变化的本地绝对路径

    DEBOUNCE_MS = 350
    MIN_GAP_MS = 800

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._paths: dict[str, str] = {}       # 本地路径 -> 原样的 vpath（回调要还回去）
        self._pending: set[str] = set()
        self._last_flush = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)

    def set_paths(self, mapping):
        """把监听的目录整体换掉。mapping: {本地路径: vpath}。

        逐个 diff 而不是 clear + 重加：clear 会造成一小段监听真空，正好错过
        「切目录的同时别人往里写了东西」。
        """
        want = {os.path.abspath(p): v for p, v in (mapping or {}).items() if p}
        for path in list(self._paths):
            if path not in want:
                self._watcher.removePath(path)
                del self._paths[path]
                self._pending.discard(path)
        for path, vp in want.items():
            self._paths[path] = vp
            if path not in self._watcher.directories():
                self._watcher.addPath(path)

    def clear(self):
        self.set_paths({})
        self._pending.clear()
        self._timer.stop()

    def watched(self):
        return sorted(self._paths)

    # ---------------- 内部 ----------------
    def _on_dir_changed(self, path):
        if path not in self._paths:
            return
        self._pending.add(path)
        # 按下葫芦浮起瓢式地续期：持续有变化就一直往后推，直到安静 350ms 才刷一次
        self._timer.start()

    def _flush(self):
        if not self._pending:
            return
        now = time.monotonic()
        wait = (self._last_flush + self.MIN_GAP_MS / 1000.0) - now
        if wait > 0:
            # 还在冷却期：把这一批留着，冷却结束再来一次（不是丢弃）
            self._timer.start(int(wait * 1000) + 10)
            return
        batch = sorted(self._pending)
        self._pending.clear()
        self._last_flush = now
        for path in batch:
            vp = self._paths.get(path)
            if vp:
                self.changed.emit(vp)


class FilePanel(QObject):
    cwdChanged = Signal()
    historyChanged = Signal()
    sortChanged = Signal()
    filterChanged = Signal()
    hiddenChanged = Signal()
    selectedCountChanged = Signal()
    statusChanged = Signal()
    busyChanged = Signal()
    capabilitiesChanged = Signal()
    infoChanged = Signal()
    countsChanged = Signal()
    # 写操作后要「选中刚粘贴/刚新建的那一项」：扫描是异步的，所以由面板自己
    # 在结果回来后落地选中，再用这个信号让 QML 把行滚到可见区并给回键盘焦点。
    focusRequested = Signal(str, int)

    def __init__(self, side: str, backend, parent=None):
        super().__init__(parent)
        self._side = side
        self._backend = backend
        self._model = FileListModel(self)

        self._cwd = vpath.encode_path(HOME)
        self._history = [self._cwd]
        self._hist_pos = 0
        self._sort_col = 0
        self._sort_desc = False
        self._filter = ""
        self._show_hidden = False
        self._selected: set[str] = set()
        self._anchor = ""

        self._all_rows: list[dict] = []          # 原始扫描结果（未过滤未排序）
        self._by_vpath: dict[str, dict] = {}
        self._total = 0
        self._truncated = False
        self._busy = False
        self._status = ""
        self._info: dict = {}
        self._job = ""
        self._pending: list[list[dict]] = []
        self._pending_select: list[str] = []

        self._batch = QTimer(self)
        self._batch.setSingleShot(True)
        self._batch.setInterval(0)
        self._batch.timeout.connect(self._flush)

    # ---------------- Properties（QML 绑定） ----------------
    @Property(str, notify=cwdChanged)
    def cwd(self):
        return self._cwd

    @Property(str, notify=cwdChanged)
    def displayPath(self):
        return vpath.display(self._cwd)

    @Property(str, notify=cwdChanged)
    def currentName(self):
        text = vpath.display(self._cwd).rstrip("/")
        return text.split("/")[-1] or "/"

    @Property(str, constant=True)
    def side(self):
        """'left' / 'right'。QML 收到全局信号（如自动刷新）时用它对号入座。"""
        return self._side

    @Property(list, notify=historyChanged)
    def history(self):
        return list(self._history)

    @Property(int, notify=historyChanged)
    def histPos(self):
        return self._hist_pos

    @Property(bool, notify=historyChanged)
    def canGoBack(self):
        return self._hist_pos > 0

    @Property(bool, notify=historyChanged)
    def canGoForward(self):
        return self._hist_pos < len(self._history) - 1

    @Property(int, notify=sortChanged)
    def sortColumn(self):
        return self._sort_col

    @Property(bool, notify=sortChanged)
    def sortDesc(self):
        return self._sort_desc

    @Property(str, notify=filterChanged)
    def filter(self):
        return self._filter

    @Property(bool, notify=hiddenChanged)
    def showHidden(self):
        return self._show_hidden

    @Property(int, notify=selectedCountChanged)
    def selectedCount(self):
        return len(self._selected)

    @Property(int, notify=countsChanged)
    def totalCount(self):
        return self._total

    @Property(int, notify=countsChanged)
    def visibleCount(self):
        return self._model.rowCount()

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property("QVariantMap", notify=capabilitiesChanged)
    def capabilities(self):
        try:
            return dict(resolve(self._cwd).capabilities(self._cwd))
        except UnknownScheme:
            return dict(LocalSource().capabilities(self._cwd))

    @Property("QVariantMap", notify=infoChanged)
    def info(self):
        return dict(self._info)

    @Property(QObject, constant=True)
    def listModel(self):
        return self._model

    # ---------------- Slots：导航 ----------------
    @Slot(str)
    def openUri(self, text):
        # 地址栏里显示的是「/a/b.zip!/dir」这种可读形式（vpath.display 的输出），
        # 用户原地回车时必须先还原回 zip+ scheme，否则会被当成本地绝对路径。
        raw = (text or "").strip()
        target = vpath.from_display(raw) or vpath.from_user(raw, HOME)
        if not target:
            self._set_status("路径无效")
            return
        try:
            source = resolve(target)
        except UnknownScheme:
            self._set_status("暂不支持该路径类型")
            return
        if not source.exists(target) or not source.is_dir(target):
            self._set_status("路径不存在或不是文件夹")
            return
        if target == self._cwd:
            self.refresh()
            return
        self._history = self._history[:self._hist_pos + 1]
        self._history.append(target)
        if len(self._history) > 100:
            self._history = self._history[-100:]
        self._hist_pos = len(self._history) - 1
        self._set_cwd(target)

    @Slot()
    def goUp(self):
        self.openUri(vpath.parent(self._cwd))

    @Slot()
    def goHome(self):
        self.openUri(HOME)

    @Slot()
    def goBack(self):
        if self._hist_pos <= 0:
            return
        self._hist_pos -= 1
        self._set_cwd(self._history[self._hist_pos])

    @Slot()
    def goForward(self):
        if self._hist_pos >= len(self._history) - 1:
            return
        self._hist_pos += 1
        self._set_cwd(self._history[self._hist_pos])

    @Slot()
    def refresh(self):
        self._scan()

    # ---------------- Slots：视图 ----------------
    @Slot(str)
    def applyFilter(self, text):
        text = text or ""
        if text == self._filter:
            return
        self._filter = text
        self.filterChanged.emit()
        self._apply_view()

    @Slot(int)
    def sortBy(self, col):
        if col not in (0, 1, 2, 3):
            return
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self.sortChanged.emit()
        self._backend._schedule_save()
        self._apply_view()

    @Slot(bool)
    def setShowHidden(self, value):
        """兼容旧调用：面板不再是这份状态的拥有者，一律转给后端。"""
        self._backend.setShowHidden(value)

    @Slot()
    def toggleHidden(self):
        self._backend.toggleHidden()

    def apply_hidden(self, value):
        """由 Backend 统一下发：改值 + 通知 + 重扫，不落盘（后端会统一存）。"""
        value = bool(value)
        if value == self._show_hidden:
            return
        self._show_hidden = value
        self.hiddenChanged.emit()
        self._scan()

    # ---------------- Slots：选中与激活 ----------------
    @Slot(str, str)
    def select(self, vp, mode):
        if not vp:
            self.clearSelection()
            return
        if mode == "single":
            self._selected = {vp}
            self._anchor = vp
        elif mode == "toggle":
            if vp in self._selected:
                self._selected.discard(vp)
            else:
                self._selected.add(vp)
            self._anchor = vp
        elif mode == "range":
            start = self._model.row_of(self._anchor)
            end = self._model.row_of(vp)
            if start >= 0 and end >= 0:
                lo, hi = sorted((start, end))
                for row in range(lo, hi + 1):
                    path = self._model.vpath_at(row)
                    if path:
                        self._selected.add(path)
            else:
                self._selected = {vp}
        else:
            self._selected = {vp}
        self._model.set_selected(self._selected)
        self.selectedCountChanged.emit()
        self._update_info()

    @Slot()
    def clearSelection(self):
        self._selected = set()
        self._model.clear_selection()
        self.selectedCountChanged.emit()
        self._update_info()

    @Slot()
    def selectAll(self):
        self._selected = {r["vpath"] for r in self._all_rows if r.get("vpath")}
        self._model.set_selected(self._selected)
        self.selectedCountChanged.emit()
        self._update_info()

    @Slot(str)
    def activate(self, vp):
        item = self._by_vpath.get(vp)
        if not item:
            return
        if item.get("isDir"):
            self.openUri(vp)
            return
        if item.get("kind") == "archive":
            # M2：双击压缩包 = 进去逛，跟双击文件夹同一手感。
            # 进不去（rar/7z 没有外部工具）就退回浮窗摘要，让用户知道缺什么。
            try:
                local = vpath.to_local(vp)
            except (ValueError, TypeError):
                local = ""
            if local and archives.can_browse(local):
                self.openUri(vpath.encode_archive(local, ""))
                return
        # M1b：双击文件直接开浮窗预览（目录仍然是进入，见上一个分支）
        self._backend._preview.request(vp)

    @Slot(str, result=str)
    def kindOf(self, vp):
        """按 vpath 取类型（右键菜单要据此决定「解压 / 压缩」是否出现）。"""
        item = self._by_vpath.get(str(vp))
        return item.get("kind", "") if item else ""

    @Slot(int, result=str)
    def vpathAt(self, row):
        """键盘上下移动时，QML 用行号换回 vpath（模型在 Python 侧）。"""
        return self._model.vpath_at(int(row))

    @Slot(str, int, result=str)
    def adjacentVpath(self, vp, delta):
        """浮窗预览里按 ↑↓ 时取同目录可见列表的相邻项，越界返回空串。"""
        row = self._model.row_of(vp)
        if row < 0:
            return ""
        nxt = max(0, min(self._model.rowCount() - 1, row + int(delta)))
        return self._model.vpath_at(nxt) or ""

    # ---------------- Slots：写操作（M1c） ----------------
    # 分层约定：这里只做「取选中 → 交后端」，真正的文件系统操作、剪贴板、
    # 线程池、Toast 全在 FileManagerBackend 上；面板不碰 QClipboard 也不碰线程。
    def selectionVpaths(self) -> list[str]:
        """按列表显示顺序返回选中项（复制/删除都照用户看到的顺序走）。"""
        if not self._selected:
            return []
        rows = [self._model.vpath_at(i) for i in range(self._model.rowCount())]
        picked = [vp for vp in rows if vp in self._selected]
        return picked or sorted(self._selected)

    @Slot()
    def copySelection(self):
        self._backend.clipboardCopy(self.selectionVpaths(), cut=False)

    @Slot()
    def cutSelection(self):
        self._backend.clipboardCopy(self.selectionVpaths(), cut=True)

    @Slot()
    def paste(self):
        self._backend.pasteInto(self)

    @Slot(str, str, result=str)
    def renameItem(self, vp, new_name):
        """返回空串表示成功，否则是错误文案（QML 据此把输入框留着让用户改）。"""
        new_vp, err = ops.rename_item(vp, new_name)
        if err:
            return err
        self._backend.after_change(select=[new_vp], target=self)
        return ""

    @Slot(result=str)
    def newFolder(self):
        new_vp, err = ops.make_directory(self._cwd, "新建文件夹")
        if err:
            self._backend.toast.emit(err)
            return ""
        self._backend.after_change(select=[new_vp], target=self)
        return new_vp

    @Slot()
    def trashSelection(self):
        self._backend.trashPaths(self.selectionVpaths())

    @Slot()
    def deleteSelection(self):
        self._backend.deletePaths(self.selectionVpaths())

    @Slot()
    def apply_cut(self):
        """把「正在剪切」的路径同步给模型（半透明显示）。"""
        self._model.set_cut(sorted(self._backend.cutVpaths()))

    @Slot(result="QVariantList")
    def selectedVpaths(self):
        """QML 侧（右键菜单、复制路径）取选中项用。"""
        return self.selectionVpaths()

    @Slot(str, result=str)
    def nameOf(self, vp):
        """按 vpath 取当前显示名（行内重命名要预填）。"""
        info = self._by_vpath.get(vp)
        if info:
            return info.get("name", "")
        # 扫描还没回来（刚新建的目录、刚搬进来的文件）时按 vpath 末段兜底，
        # 否则改名会拿到空串直接放弃，表现是「新建后不进入改名」。
        return vpath.basename(vp)

    @Slot("QVariantList")
    def selectPaths(self, vpaths):
        """写操作完成后选中一批路径；扫描还没回来就先记着。"""
        self._pending_select = [str(v) for v in (vpaths or [])]
        if not self._busy:
            self._apply_pending_select()

    def _apply_pending_select(self):
        wanted = [vp for vp in self._pending_select if self._model.row_of(vp) >= 0]
        self._pending_select = []
        if not wanted:
            return
        self._selected = set(wanted)
        self._model.set_selected(self._selected)
        self.selectedCountChanged.emit()
        self._update_info()
        self.focusRequested.emit(wanted[-1], self._model.row_of(wanted[-1]))

    def _prune_selection(self):
        """删除/移动后把已经不存在的选中项剔掉，别让信息条挂着幽灵条目。"""
        if not self._selected:
            return
        alive = {vp for vp in self._selected if vp in self._by_vpath}
        if alive == self._selected:
            return
        self._selected = alive
        self._model.set_selected(alive)
        self.selectedCountChanged.emit()
        self._update_info()

    # 常用位置已上移到 FileManagerBackend.standardLocations() -> filecore/locations.py
    # （收藏栏是页面级的，不该左右面板各算一份）。

    # ---------------- 内部：扫描与视图 ----------------
    def apply_initial(self, cwd_value, data):
        """启动时套用持久化状态（在首次 refresh 之前调用）。"""
        self._cwd = cwd_value
        raw_hist = [x for x in data.get("history", []) if isinstance(x, str)]
        hist = []
        for item in raw_hist:
            valid = state.validate(item, HOME, LocalSource())
            if valid and valid != (hist[-1] if hist else None):
                hist.append(valid)
        hist = hist or [cwd_value]
        self._sort_col = data.get("sortColumn", 0)
        self._sort_desc = bool(data.get("sortDesc", False))
        self._show_hidden = bool(data.get("showHidden", False))
        self._hist_pos = max(0, min(len(hist) - 1, int(data.get("histPos", 0))))
        if hist[self._hist_pos] != cwd_value:
            hist = hist[:self._hist_pos + 1] + [cwd_value]
            self._hist_pos = len(hist) - 1
        self._history = hist
        self.cwdChanged.emit()
        self.historyChanged.emit()
        self.sortChanged.emit()
        self.hiddenChanged.emit()
        self.capabilitiesChanged.emit()

    def _set_cwd(self, target):
        self._cwd = target
        self._selected = set()
        self._anchor = ""
        self._filter = ""
        self.cwdChanged.emit()
        self.historyChanged.emit()
        self.filterChanged.emit()
        self.capabilitiesChanged.emit()
        self.selectedCountChanged.emit()
        self._update_info()
        self._backend._schedule_save()
        self._scan()
        # 换了目录就把 watch 挪过去（只挂当前这一层，不递归）
        self._backend._sync_watch()

    def _scan(self):
        if self._job:
            self._backend._runner.cancel(self._job)
        self._batch.stop()
        self._pending = []
        self._set_busy(True)
        self._set_status("正在读取…")
        self._job = self._backend._runner.submit(
            "io", self._scan_job, self._cwd, self._show_hidden)

    def _scan_job(self, cwd_value, show_hidden, cancel=None):
        """工作线程：零 Qt 调用，只回纯数据。排序/过滤留给主线程。"""
        source = resolve(cwd_value)
        entries, err, truncated = source.list_dir(cwd_value, show_hidden, cancel)
        return {
            "vpath": cwd_value,
            "rows": [e.to_dict() for e in entries],
            "err": err,
            "truncated": bool(truncated),
        }

    def handle_result(self, job_id, ok, payload):
        if job_id != self._job:
            return False
        self._job = ""
        self._set_busy(False)
        result = payload[0] if payload else {}
        if not ok:
            self._set_status(str(result) or "读取失败")
            return True
        if not isinstance(result, dict) or result.get("vpath") != self._cwd:
            return True                                  # 过期结果直接丢弃
        err = result.get("err")
        rows = result.get("rows") or []
        # 与上一轮一字不差（自动刷新最常撞上这种空转：自己写盘引起的二次事件）。
        # 判定要在覆盖 _all_rows 之前做。
        same = (rows == self._all_rows) and not self._pending
        self._all_rows = rows
        self._by_vpath = {r["vpath"]: r for r in rows if r.get("vpath")}
        self._total = len(rows)
        self._truncated = bool(result.get("truncated"))
        self.countsChanged.emit()
        if err:
            self._set_status(err)
        elif self._truncated:
            self._set_status(f"仅显示前 {listing.MAX_ITEMS} 项")
        else:
            self._set_status("")
        self._prune_selection()
        if same and not err:
            # 内容没变就别 reset 模型：set_entries 会让 ListView 重建全部委托，
            # 视觉上是「列表闪一下」，滚动位置也可能抖。这里只同步选中态。
            self._model.set_selected(self._selected)
            self._apply_pending_select()
            self._update_info()
            return True
        self._apply_view()
        return True

    def _apply_view(self):
        rows = listing.visible_dicts(self._all_rows, self._filter)
        rows = listing.sort_dicts(rows, self._sort_col, self._sort_desc)
        self._batch.stop()
        self._pending = []
        if len(rows) <= listing.CHUNK_THRESHOLD:
            self._model.set_entries(rows)
            self._model.set_selected(self._selected)
            self._model.set_cut(sorted(self._backend.cutVpaths()))
            self.countsChanged.emit()
            self._apply_pending_select()
        else:
            self._model.set_entries([])
            self._pending = [rows[i:i + listing.CHUNK_SIZE]
                             for i in range(0, len(rows), listing.CHUNK_SIZE)]
            self._batch.start(0)
        self._update_info()

    def _flush(self):
        if self._pending:
            self._model.append_batch(self._pending.pop(0))
        if self._pending:
            self._batch.start(0)
        else:
            self._model.set_selected(self._selected)
            self._model.set_cut(sorted(self._backend.cutVpaths()))
            self.countsChanged.emit()
            self._apply_pending_select()

    def _update_info(self):
        if len(self._selected) != 1:
            self._info = {}
        else:
            vp = next(iter(self._selected))
            item = self._by_vpath.get(vp, {})
            self._info = {
                "name": item.get("name", ""),
                "vpath": vp,
                "display": vpath.display(vp),
                "kind": item.get("kind", "other"),
                "kindLabel": kind_label(item.get("kind", "other")),
                "sizeText": item.get("sizeText", ""),
                "timeText": item.get("timeText", ""),
                "isDir": bool(item.get("isDir", False)),
            }
        self.infoChanged.emit()

    def _set_status(self, text):
        if text != self._status:
            self._status = text
            self.statusChanged.emit()

    def _set_busy(self, value):
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()
            self._backend._on_busy_changed()


class FileManagerBackend(QObject):
    activeSideChanged = Signal()
    leftWidthChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal()
    toast = Signal(str)
    favoritesChanged = Signal()
    previewWindowChanged = Signal()
    previewPrefsChanged = Signal()
    previewActiveChanged = Signal()
    showHiddenChanged = Signal()
    workScheduleChanged = Signal()
    clipboardChanged = Signal()
    opChanged = Signal()
    autoRefreshChanged = Signal()
    autoRefreshRequested = Signal(str)      # 参数是 side：'left' / 'right'

    def __init__(self, app_home, parent=None):
        super().__init__(parent)
        filecore.bootstrap()
        self._app_home = Path(app_home)
        self._state_path = self._app_home / "filemanager_state.json"

        self._runner = TaskRunner(self)
        self._preview = PreviewProvider(self, self)
        self._active_side = "left"
        self._left_width = state.DEFAULT_LEFT_WIDTH
        self._favorites: list[str] = []          # 存 vpath，不是绝对路径
        self._preview_window = state._clean_preview_window(None)
        self._preview_prefs = state._clean_preview_prefs(None)
        self._preview_active = False
        # 显示隐藏文件是全局开关（设置面板里只有一处），左右面板同步生效。
        # 之前是每个面板各存一份，工具栏上两个「隐藏: 开/关」按钮，语义上说不通。
        self._show_hidden = False
        self._work_schedule = state._clean_work(None)
        self._local_source = LocalSource()
        self._busy = False
        self._progress = 0
        # ---- M1c 写操作状态 ----
        self._clip: dict = {"operation": "copy", "vpaths": [], "ignored": 0}
        self._clip_cut: set[str] = set()
        self._op_job = ""
        self._op_busy = False
        self._op_text = ""
        self._op_select: list[str] = []
        self._op_target = None
        self._op_on_done = None
        self._tick_job = ""
        self._clip_ignore = 0.0
        self._last_tick = 0.0
        # ---- M2 目录自动刷新 ----
        self._auto_refresh = True
        # 自己写盘时 watcher 也会触发：复制/删除/新建之后 after_change 已经刷过一次，
        # 这时候再来一轮就会把刚选中的结果冲掉。用这个时间戳压掉自产噪音。
        self._watch_ignore_until = 0.0
        self._watch_retry: set[str] = set()    # 被 suppress 窗口压住、等会儿要补刷的目录
        self._deferred_side: set[str] = set()  # 面板正在扫描，等它闲下来再刷
        self._watcher = CwdWatcher(self)
        self._watcher.changed.connect(self._on_dir_changed)

        self._left = FilePanel("left", self, self)
        self._right = FilePanel("right", self, self)
        self._panels = {"left": self._left, "right": self._right}

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.saveState)

        # 显式 QueuedConnection：finished 从工作线程发出，模型/属性操作必须回主线程。
        # 预览与列表各自只认自己的 job id，互不干扰。
        self._runner.finished.connect(self._on_job_finished, Qt.QueuedConnection)
        self._runner.finished.connect(self._preview._on_job_finished,
                                      Qt.QueuedConnection)
        for panel in self._panels.values():
            for sig in (panel.cwdChanged, panel.historyChanged, panel.sortChanged,
                        panel.hiddenChanged):
                sig.connect(self._schedule_save)
            # 扫描结束时把因为 busy 压下来的自动刷新补上
            panel.busyChanged.connect(self._flush_deferred_refresh)

        self._load_state()
        self._left.refresh()
        self._right.refresh()
        self._sync_watch()

        # 剪贴板是全局资源：别的程序一改，「剪切」语义就失效了，必须重新读一遍。
        # X11 下 dataChanged 由我们自己写入时也会触发，_on_clipboard_changed 会
        # 用内容比对避免把刚设好的 cut 状态又冲掉。
        QGuiApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        self._runner.progress.connect(self._on_op_progress, Qt.QueuedConnection)
        self._clip = self._read_clipboard()

    # ---------------- Properties ----------------
    @Property(QObject, constant=True)
    def left(self):
        return self._left

    @Property(QObject, constant=True)
    def right(self):
        return self._right

    @Property(QObject, constant=True)
    def preview(self):
        return self._preview

    @Property(str, notify=activeSideChanged)
    def activeSide(self):
        return self._active_side

    @Property(int, notify=leftWidthChanged)
    def leftWidth(self):
        # 966 基准下的归一化像素（比例 = leftWidth / 966），不是屏幕像素。
        # QML 用 FileManagerPage.ratio 换算成实际宽度，窗口任何尺寸下比例恒定。
        return self._left_width

    @Property("QVariantList", notify=favoritesChanged)
    def favorites(self):
        """收藏夹。QML 严禁直接 model: files.favorites，
        必须 property var + Connections 整体重新赋值（PySide6 的已知坑）。"""
        out = []
        for v in self._favorites:
            try:
                local = vpath.to_local(v)
                name = os.path.basename(local.rstrip("/")) or "/"
                disp = vpath.display(v)
                missing = not self._local_source.is_dir(v)
            except (ValueError, OSError, TypeError):
                # M2 之前遇到 zip+file:// 等未知方案，保守判为失效
                name, disp, missing = v, v, True
            out.append({"vpath": v, "name": name, "display": disp,
                        "missing": bool(missing)})
        return out

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(int, notify=progressChanged)
    def progress(self):
        return self._progress

    @Property(bool, notify=showHiddenChanged)
    def showHidden(self):
        """是否显示点开头的隐藏文件。全局一份，左右面板同步。"""
        return self._show_hidden

    @Property(bool, notify=autoRefreshChanged)
    def autoRefresh(self):
        """目录自动刷新：监听当前目录，外部有增删改名就重扫一次。"""
        return self._auto_refresh

    @Property("QVariantMap", notify=workScheduleChanged)
    def workSchedule(self):
        """工作时段：{"startMin": 720, "endMin": 1080} 加两段显示文本。

        存「当天第几分钟」而不是 "18:00"：清洗简单，QML 侧不用反复解析。
        文本由后端格式化，避免 QML 里再写一份补零逻辑。
        """
        d = dict(self._work_schedule)
        d["startText"] = state.format_hhmm(d["startMin"])
        d["endText"] = state.format_hhmm(d["endMin"])
        return d

    @Property("QVariantMap", notify=previewWindowChanged)
    def previewWindow(self):
        """预览窗上次的位置/尺寸。placed=False 时由 QML 按主窗口右侧算初始位置。"""
        return dict(self._preview_window)

    @Property(bool, notify=previewActiveChanged)
    def previewActive(self):
        """预览窗是否是当前活动窗口。主窗口的 Esc/空格 Shortcut 要看它：
        Qt 的 Shortcut 不是严格 per-active-window，预览窗激活时主窗口那条照样会
        被激活 —— 结果在预览窗里按 Esc，面板刚关掉、窗口也被关了。"""
        return self._preview_active

    @Property("QVariantMap", notify=previewPrefsChanged)
    def previewPrefs(self):
        """预览偏好（目前只有 pdfQuality）。QML 只读，改档走 setPdfQuality。"""
        return dict(self._preview_prefs)

    @Property("QVariantMap", notify=clipboardChanged)
    def clipboard(self):
        """剪贴板里我们认得的文件项。QML 用它决定「粘贴」是否可点、文案写几项。"""
        names = []
        for v in self._clip.get("vpaths", [])[:3]:
            try:
                names.append(os.path.basename(vpath.to_local(v).rstrip("/")) or "/")
            except (ValueError, TypeError):
                continue
        return {
            "operation": self._clip.get("operation", "copy"),
            "count": len(self._clip.get("vpaths", [])),
            "names": names,
            "ignored": int(self._clip.get("ignored", 0)),
        }

    @Property(bool, notify=opChanged)
    def opBusy(self):
        """写操作进行中（复制/移动/删除）。与面板自己的 busy 分开：
        面板 busy 会盖住列表显示「正在读取」，复制时不该盖。"""
        return self._op_busy

    @Property(str, notify=opChanged)
    def opText(self):
        return self._op_text

    # ---------------- Slots ----------------
    @Slot(str)
    def setActiveSide(self, side):
        if side in self._panels and side != self._active_side:
            self._active_side = side
            self.activeSideChanged.emit()
            self._schedule_save()

    @Slot()
    def toggleActiveSide(self):
        self.setActiveSide("right" if self._active_side == "left" else "left")

    # ---------------- 写操作（M1c） ----------------
    # 剪贴板是「系统级」的：同时写 text/uri-list 与 x-special/gnome-copied-files，
    # 麒麟的 Peony / Nautilus 都能互传；粘贴时反过来优先读 gnome 格式（它带 cut 语义）。
    def cutVpaths(self) -> set[str]:
        """正在剪切的路径（面板据此把行画成半透明）。QML 不直接调。"""
        return set(self._clip_cut)

    def _read_clipboard(self) -> dict:
        md = QGuiApplication.clipboard().mimeData()
        data = {}
        if md is not None:
            for mime in (ops.MIME_GNOME, ops.MIME_URI, ops.MIME_TEXT):
                try:
                    if md.hasFormat(mime):
                        data[mime] = bytes(md.data(mime))
                except (RuntimeError, TypeError):
                    continue
        return ops.parse(data)

    def _on_clipboard_changed(self):
        # 自己刚写进去的那一下会同步发 dataChanged，X11 上此刻读回来还可能是空的，
        # 用时间窗挡掉，否则「剪切」的半透明状态会瞬间消失。
        if time.monotonic() < self._clip_ignore:
            return
        clip = self._read_clipboard()
        if (clip["operation"] == self._clip.get("operation") and
                clip["vpaths"] == self._clip.get("vpaths")):
            return
        self._clip = clip
        self._clip_cut = set(clip["vpaths"]) if clip["operation"] == "cut" else set()
        self.clipboardChanged.emit()
        for panel in self._panels.values():
            panel.apply_cut()

    @Slot("QVariantList", bool)
    def clipboardCopy(self, vpaths, cut):
        picked = [str(v) for v in (vpaths or []) if v]
        if not picked:
            self.toast.emit("没有选中项目")
            return
        blob = ops.encode("cut" if cut else "copy", picked)
        if not blob:
            self.toast.emit("这些项目无法复制（压缩包内条目暂不支持）")
            return
        mime = QMimeData()
        for key, data in blob.items():
            mime.setData(key, QByteArray(data))
        self._clip_ignore = time.monotonic() + 0.6
        QGuiApplication.clipboard().setMimeData(mime)
        self._clip = {"operation": "cut" if cut else "copy",
                      "vpaths": list(picked), "ignored": 0}
        self._clip_cut = set(picked) if cut else set()
        self.clipboardChanged.emit()
        for panel in self._panels.values():
            panel.apply_cut()
        self.toast.emit(("已剪切 %d 项" if cut else "已复制 %d 项") % len(picked))

    @Slot()
    def pasteInto(self, panel):
        if self._op_busy:
            self.toast.emit("正在处理上一个操作，请稍候")
            return
        try:
            vpath.to_local(panel.cwd)
        except (ValueError, TypeError):
            self.toast.emit("压缩包内不能粘贴，先解压到本地")
            return
        clip = self._read_clipboard()
        if not clip["vpaths"]:
            self.toast.emit("剪贴板里没有可粘贴的文件")
            return
        move = clip["operation"] == "cut"
        src = list(clip["vpaths"])
        dest = panel.cwd

        def job(cancel=None):
            return ops.run_transfer(src, dest, move=move, cancel=cancel,
                                    tick=self._make_tick())

        self._start_op("正在移动…" if move else "正在复制…", panel, job,
                       lambda result: self._after_transfer(
                           result, panel, move, verb="已移动" if move else "已粘贴"))

    @Slot("QVariantList")
    def trashPaths(self, vpaths):
        local = self._local_paths(vpaths)
        if not local:
            if vpaths:
                self.toast.emit("压缩包内不能删除文件，先解压到本地")
            return
        if self._op_busy:
            self.toast.emit("正在处理上一个操作，请稍候")
            return

        def job(cancel=None):
            return {"trash": ops.trash_paths(local)}

        self._start_op("正在移到回收站…", None, job, self._after_trash)

    @Slot("QVariantList")
    def deletePaths(self, vpaths):
        local = self._local_paths(vpaths)
        if not local:
            if vpaths:
                self.toast.emit("压缩包内不能删除文件，先解压到本地")
            return
        if self._op_busy:
            self.toast.emit("正在处理上一个操作，请稍候")
            return

        def job(cancel=None):
            result = {"ok": 0, "failed": []}
            for path in local:
                if cancel is not None and cancel.is_cancelled():
                    result["cancelled"] = True
                    break
                item = ops.permanent_delete(path)
                if item.get("ok"):
                    result["ok"] += 1
                else:
                    result["failed"].append({"name": os.path.basename(path),
                                             "error": item.get("error", "删除失败")})
            return result

        self._start_op("正在删除…", None, job, self._after_delete)

    @Slot()
    def cancelOp(self):
        if not self._op_job:
            return
        self._runner.cancel(self._op_job)
        self.toast.emit("正在取消…")

    # ---------------- M2：压缩包 ----------------
    @Slot("QVariantList", bool)
    def extractPaths(self, vpaths, into_folder):
        """解压：本地压缩包（整包）或压缩包内选中的条目。

        两条路都收在这一处，因为「目标目录」的推导是同一件事：
        本地包 → 它所在目录；包内条目 → 容器所在目录（包里的东西总得落到包外面）。
        into_folder=True 时先建「包名」文件夹再解进去（同名自动加序号）。
        """
        if self._op_busy:
            self.toast.emit("正在处理上一个操作，请稍候")
            return
        jobs = []
        seen = set()
        for raw in (vpaths or []):
            try:
                scheme, container, inner = vpath.parse(str(raw))
            except (ValueError, TypeError):
                continue
            if scheme == vpath.ARCHIVE:
                if not container:
                    continue
                names = [inner] if inner else None
                job = (container, os.path.dirname(container), tuple(names or ()))
                label = inner or ""
            else:
                try:
                    local = vpath.to_local(str(raw))
                except (ValueError, TypeError):
                    continue
                if not archives.format_of(local) or not os.path.isfile(local):
                    continue
                job = (local, os.path.dirname(local), ())
                label = ""
            if job in seen:
                continue
            seen.add(job)
            jobs.append((job[0], job[1], job[2], label))
        if not jobs:
            self.toast.emit("选中的项目里没有可解压的压缩包")
            return

        def run(cancel=None):
            ok = 0
            failed = []
            skipped = 0
            exists = 0
            cancelled = False
            opaque = False
            paths = []
            for container, dest, names, _label in jobs:
                if cancel is not None and cancel.is_cancelled():
                    cancelled = True
                    break
                res = archives.extract(container, dest, names=list(names) or None,
                                       into_folder=into_folder, cancel=cancel,
                                       tick=self._make_tick())
                ok += int(res.get("ok", 0))
                failed.extend(res.get("failed") or [])
                skipped += int(res.get("skipped", 0))
                exists += int(res.get("exists", 0))
                opaque = opaque or bool(res.get("opaque"))
                if res.get("path"):
                    paths.append(res["path"])
                if res.get("cancelled"):
                    cancelled = True
                    break
            return {"ok": ok, "failed": failed, "skipped": skipped,
                    "exists": exists, "cancelled": cancelled,
                    "paths": paths, "opaque": opaque}

        # 解压是写盘，watcher 会连打几十个事件；_start_op 已经压了时间窗，
        # tick 里还会续窗（见 _make_tick），不会在解压过程中反复重扫。
        self._start_op("正在解压…", None, run, self._after_extract)

    @Slot("QVariantList")
    def compressPaths(self, vpaths):
        """把选中项压成一个 zip，落在活动面板的当前目录。"""
        if self._op_busy:
            self.toast.emit("正在处理上一个操作，请稍候")
            return
        panel = self._panels.get(self._active_side) or self._left
        try:
            dest_dir = vpath.to_local(panel.cwd)
        except (ValueError, TypeError):
            self.toast.emit("压缩包内不能新建压缩包，先解压到本地再压")
            return
        if not os.path.isdir(dest_dir):
            self.toast.emit("当前目录不可写")
            return
        local = self._local_paths(vpaths)
        if not local:
            self.toast.emit("选中的项目不在本地磁盘上")
            return
        try:
            name = ops.unique_name(dest_dir, archives.default_zip_name(local) + ".zip")
        except OSError as exc:
            self.toast.emit(str(exc))
            return
        dest = os.path.join(dest_dir, name)

        def run(cancel=None):
            return archives.compress_zip(local, dest, cancel=cancel,
                                         tick=self._make_tick())

        self._start_op("正在压缩…", panel, run,
                       lambda result: self._after_compress(result, dest))

    def _after_extract(self, result):
        if not isinstance(result, dict):
            self.toast.emit("解压失败")
            return
        for p in self._panels.values():
            p.refresh()
        parts = []
        if result.get("ok"):
            parts.append("已解压 %d 个文件" % int(result["ok"]))
        elif result.get("opaque"):
            parts.append("已解压")
        if result.get("skipped"):
            parts.append("%d 项已跳过（越界路径或符号链接）" % int(result["skipped"]))
        if result.get("exists"):
            # 「解压到此处」撞上同名文件：跳过、不覆盖。这条必须说出来，
            # 否则用户会以为文件没解出来。
            parts.append("%d 项同名已存在，未覆盖（改用「解压到独立文件夹」）"
                         % int(result["exists"]))
        failed = result.get("failed") or []
        if failed:
            parts.append("%d 项失败：%s" % (len(failed), failed[0].get("error", "")))
        if result.get("cancelled"):
            parts.append("已取消")
        paths = [p for p in (result.get("paths") or []) if p]
        if paths:
            owner = self._panels.get(self._active_side) or self._left
            owner.selectPaths(paths)
        self.toast.emit("，".join(parts) or "没有解压出任何文件")

    def _after_compress(self, result, dest):
        if not isinstance(result, dict):
            self.toast.emit("压缩失败")
            return
        for p in self._panels.values():
            p.refresh()
        failed = result.get("failed") or []
        if failed:
            self.toast.emit("压缩失败：%s" % failed[0].get("error", ""))
            return
        if result.get("cancelled"):
            self.toast.emit("已取消压缩")
            return
        # 压缩落的目录未必是活动面板 —— 找真正装着它那一栏去选中。
        target_vp = ""
        try:
            target_vp = vpath.encode_path(dest)
        except (ValueError, TypeError):
            pass
        owner = None
        for p in self._panels.values():
            if target_vp and p.cwd == vpath.encode_path(os.path.dirname(dest)):
                owner = p
                break
        if owner is not None and target_vp:
            owner.selectPaths([target_vp])
        self.toast.emit("已压缩 %d 个文件到 %s" % (int(result.get("ok", 0)),
                                                 os.path.basename(dest)))

    def after_change(self, select=None, target=None):
        """同步操作（改名 / 新建文件夹）完成后：刷新两端并选中结果。"""
        # 这次刷新是我们自己写盘引起的，watcher 随后一定还会为同一批改动报一次。
        # 不压掉它就会出现「刚选中新建的文件夹，第二轮重扫把选中冲掉」。
        self._suppress_watch()
        for panel in self._panels.values():
            panel.refresh()
        if select:
            owner = target or self._panels.get(self._active_side) or self._left
            owner.selectPaths(select)
        self._sync_watch()

    # ---------------- M2：目录自动刷新 ----------------
    def _suppress_watch(self, seconds=1.5):
        """压掉自产的文件系统事件窗口。写盘期间与之后的一小段时间内忽略 watcher。"""
        self._watch_ignore_until = time.monotonic() + seconds

    def _sync_watch(self):
        """把两个面板的当前目录交给 watcher。

        只挂 file:// 的目录：压缩包内条目（M2 的 zip:// 等）没有可 watch 的物理路径，
        它们的「变化」只能靠重新打开压缩包，不在这套机制里。
        """
        if not self._auto_refresh:
            self._watcher.clear()
            return
        mapping = {}
        for panel in self._panels.values():
            try:
                local = vpath.to_local(panel.cwd)
            except (ValueError, TypeError):
                continue
            if local and os.path.isdir(local):
                mapping[local] = panel.cwd
        self._watcher.set_paths(mapping)

    def _on_dir_changed(self, cwd_vpath):
        """当前目录有增删改名：请求重扫那个面板。

        真正刷不刷、什么时候刷由 QML 决定（它才知道是不是正在行内改名、是不是正在
        往地址栏打字）—— 那几个状态下列表一旦重建委托，编辑框会被顶掉。

        两条「先压后补」的路径，都**不丢事件**：
        * 自己刚写过盘的抑制窗口内 → 窗口结束后补一次（压掉的是自产噪音，
          但窗口里万一有别人的改动，补这一次才不会漏）；
        * 面板正在扫 → 记下来，等 busy 变 false 再发（扫到一半取消重来永远扫不完）。
        """
        if not self._auto_refresh:
            return
        delay = self._watch_ignore_until - time.monotonic()
        if delay > 0:
            if cwd_vpath in self._watch_retry:
                return
            self._watch_retry.add(cwd_vpath)
            QTimer.singleShot(int(delay * 1000) + 20,
                              lambda: self._retry_dir(cwd_vpath))
            return
        for panel in self._panels.values():
            if panel.cwd != cwd_vpath:
                continue
            if panel.busy:
                self._deferred_side.add(panel.side)
                return
            self.autoRefreshRequested.emit(panel.side)

    def _retry_dir(self, cwd_vpath):
        self._watch_retry.discard(cwd_vpath)
        self._on_dir_changed(cwd_vpath)

    def _flush_deferred_refresh(self):
        """某个面板扫完了：把刚才因为 busy 压下来的刷新补发出去。"""
        if not self._deferred_side:
            return
        for side in list(self._deferred_side):
            panel = self._panels.get(side)
            if panel is None or panel.busy:
                continue
            self._deferred_side.discard(side)
            self.autoRefreshRequested.emit(side)

    def _local_paths(self, vpaths) -> list[str]:
        out = []
        for v in vpaths or []:
            try:
                out.append(vpath.to_local(str(v)))
            except (ValueError, TypeError):
                continue
        return out

    def _make_tick(self):
        """给工作线程用的进度节流器：最多 10Hz，且只在百分比变化时发。"""
        def tick(done, total):
            now = time.monotonic()
            # 还在写盘就给 watcher 抑制窗口续期：解压/压缩动辄几十秒、几万个事件，
            # 只在 _start_op 里压 3 秒的话，中途会被 inotify 拽着反复全目录重扫。
            self._watch_ignore_until = max(self._watch_ignore_until, now + 1.5)
            if total > 0:
                pct = int(done * 100 / total)
            else:
                pct = 100 if done else 0
            if pct < 100 and now - self._last_tick < 0.1:
                return
            self._last_tick = now
            job_id = self._tick_job
            if not job_id:
                return
            try:
                self._runner.progress.emit(job_id, max(0, min(100, pct)))
            except RuntimeError:
                pass
        return tick

    @Slot(str, int)
    def _on_op_progress(self, job_id, pct):
        if not job_id or job_id != self._op_job:
            return
        pct = max(0, min(100, int(pct)))
        if pct != self._progress:
            self._progress = pct
            self.progressChanged.emit()

    def _start_op(self, label, target, job_fn, on_done):
        if self._op_busy:
            self.toast.emit("正在处理上一个操作，请稍候")
            return ""
        self._op_text = label
        self._op_busy = True
        # 分块复制会在目标目录里反复建临时文件（.wspart），watcher 每隔几百毫秒就会
        # 报一次；这些过程态没有刷新价值，只会跟进度条抢主线程。
        self._suppress_watch(seconds=3.0)
        self._op_target = target
        self._op_on_done = on_done
        self._progress = 0
        self.opChanged.emit()
        self.progressChanged.emit()
        # job id 只有 submit 之后才知道，而工作线程可能立刻开始刷进度：
        # tick 里读 self._tick_job，早于赋值的那一帧会被丢掉（都是 0%，无影响）。
        self._tick_job = ""
        job = self._runner.submit("op", job_fn)
        self._op_job = job
        self._tick_job = job
        return job

    def _reset_progress(self):
        if self._op_busy:
            return
        if self._progress != 0:
            self._progress = 0
            self.progressChanged.emit()

    def _after_transfer(self, result, panel, move, verb=""):
        if not isinstance(result, dict):
            self.toast.emit("操作失败")
            return
        ok = int(result.get("ok", 0))
        failed = result.get("failed") or []
        paths = result.get("paths") or []
        for p in self._panels.values():
            p.refresh()
        if paths and not move:
            # 复制：选中目标面板里的新项，方便接着操作
            panel.selectPaths(paths)
        if move and ok:
            # 剪切粘贴后源已经不在原位，剪贴板状态得重读（parse 会过滤掉不存在的）
            self._clip_ignore = 0.0
            self._clip = self._read_clipboard()
            self._clip_cut = {v for v in self._clip_cut
                              if os.path.lexists(self._safe_local(v))}
            if self._clip.get("operation") != "cut":
                self._clip_cut = set()
            self.clipboardChanged.emit()
            for p in self._panels.values():
                p.apply_cut()
        parts = []
        if ok:
            # verb 由调用方给：粘贴就是「已粘贴」，别一律说「已复制」
            parts.append(("%s %d 项" % (verb or ("已移动" if move else "已复制"), ok)))
        renamed = int(result.get("renamed", 0))
        if renamed:
            parts.append("%d 项同名已加序号" % renamed)
        if failed:
            parts.append("%d 项失败：%s" % (len(failed), failed[0].get("error", "")))
        if result.get("cancelled"):
            parts.append("已取消")
        self.toast.emit("，".join(parts) or "没有变化")

    def _after_trash(self, result):
        info = (result or {}).get("trash") or {}
        self._after_delete({"ok": info.get("ok", 0), "failed": info.get("failed") or []},
                           verb="移到回收站")

    def _after_delete(self, result, verb="删除"):
        ok = int((result or {}).get("ok", 0))
        failed = (result or {}).get("failed") or []
        for p in self._panels.values():
            p.refresh()
        parts = []
        if ok:
            parts.append("已%s %d 项" % (verb, ok))
        if failed:
            parts.append("%d 项失败：%s" % (len(failed), failed[0].get("error", "")))
        if (result or {}).get("cancelled"):
            parts.append("已取消")
        self.toast.emit("，".join(parts) or "没有可处理的项")

    @staticmethod
    def _safe_local(vp) -> str:
        try:
            return vpath.to_local(str(vp))
        except (ValueError, TypeError):
            return ""

    # ---------------- Slots：系统集成 ----------------
    @Slot(str, result=bool)
    def openDefault(self, vp):
        """用桌面默认程序打开。目录也走这条（交给文件管理器）。"""
        local = self._safe_local(vp)
        if not local or not os.path.lexists(local):
            self.toast.emit("文件不存在")
            return False
        if QDesktopServices.openUrl(QUrl.fromLocalFile(local)):
            return True
        self.toast.emit("没有找到能打开它的程序")
        return False

    @Slot(str, result=bool)
    def openTerminal(self, cwd_vpath):
        """在终端里打开这个目录。优先系统终端，逐个试，找不到给明确的 Toast。"""
        local = self._safe_local(cwd_vpath) or HOME
        if not os.path.isdir(local):
            local = os.path.dirname(local)
        if sys.platform == "darwin":
            try:
                subprocess.Popen(["open", "-a", "Terminal", local],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return True
            except OSError:
                pass
        for exe, tail in _TERMINALS:
            path = shutil.which(exe)
            if not path:
                continue
            argv = [arg.format(dir=local) for arg in tail]
            try:
                subprocess.Popen([path, *argv], cwd=local, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except OSError:
                continue
        self.toast.emit("没有找到可用的终端程序")
        return False

    @Slot("QVariantList", result=int)
    def copyPathText(self, vpaths):
        texts = self._local_paths(vpaths)
        if not texts:
            self.toast.emit("没有选中项目")
            return 0
        self._clip_ignore = time.monotonic() + 0.6
        QGuiApplication.clipboard().setText("\n".join(texts))
        # 剪贴板现在只有纯文本，文件语义要跟着清掉，否则菜单还写着「粘贴 3 项」
        self._clip = {"operation": "copy", "vpaths": [], "ignored": 0}
        self._clip_cut = set()
        self.clipboardChanged.emit()
        for panel in self._panels.values():
            panel.apply_cut()
        self.toast.emit("已复制 %d 条路径" % len(texts))
        return len(texts)

    @Slot(result=QObject)
    def active(self):
        return self._panels[self._active_side]

    # ---------------- Slots：收藏栏 ----------------
    @Slot(result="QVariantList")
    def standardLocations(self):
        """常用位置（只读分组，页面级一份）。Linux 走 XDG user-dirs.dirs。"""
        return locations.standard_locations(HOME)

    @Slot()
    def addFavorite(self):
        """把当前激活面板所在的目录加入收藏夹（无参，避开 QML 传值时机问题）。"""
        try:
            n = vpath.normalize(self.active().cwd)
        except (ValueError, TypeError, OSError):
            n = ""
        if not n or n in self._favorites:
            self.toast.emit("目录无效或已在收藏夹里")
            return
        if not self._local_source.is_dir(n):
            self.toast.emit("只能收藏本地文件夹")
            return
        self._favorites.append(n)
        self.favoritesChanged.emit()
        self.toast.emit("已收藏：" + vpath.display(n))
        self._schedule_save()

    @Slot(str)
    def removeFavorite(self, vp):
        try:
            n = vpath.normalize(vp) if isinstance(vp, str) else ""
        except (ValueError, TypeError, OSError):
            n = ""
        if not n or n not in self._favorites:
            self.toast.emit("收藏项不存在")
            return
        self._favorites.remove(n)
        self.favoritesChanged.emit()
        self.toast.emit("已移除收藏：" + vpath.display(n))
        self._schedule_save()

    @Slot(int)
    def setLeftWidth(self, width):
        width = state.clamp_width(width)
        if width != self._left_width:
            self._left_width = width
            self.leftWidthChanged.emit()
            self._schedule_save()

    # ---------------- Slots：预览窗几何（独立窗口，可拖出主窗口） ----------------
    @Slot(int, int, int, int)
    def savePreviewWindow(self, x, y, w, h):
        """QML 在拖动结束 / 关闭时回写。写盘走 400ms 防抖，拖动过程中不会疯狂落盘。"""
        new = state._clean_preview_window({
            "ver": state.PREVIEW_GEOM_VERSION,
            "placed": True, "x": x, "y": y, "w": w, "h": h})
        if new == self._preview_window:
            return
        self._preview_window = new
        self.previewWindowChanged.emit()
        self._schedule_save()

    @Slot(bool)
    def setShowHidden(self, value):
        """全局「显示隐藏文件」。左右面板一起重扫，状态只存一份。

        以前是每个面板各存一份（工具栏上两个「隐藏: 开/关」），放进设置面板后
        必须单向化，否则会出现「左栏开了右栏没开」而设置里只有一个开关的矛盾。
        """
        value = bool(value)
        if value == self._show_hidden:
            return
        self._show_hidden = value
        for panel in self._panels.values():
            panel.apply_hidden(value)
        self.showHiddenChanged.emit()
        self._schedule_save()

    @Slot()
    def toggleHidden(self):
        self.setShowHidden(not self._show_hidden)

    @Slot(bool)
    def setAutoRefresh(self, value):
        """开关目录自动刷新。关掉立即释放 watch（不占 inotify 配额），打开就重新挂上。"""
        value = bool(value)
        if value == self._auto_refresh:
            return
        self._auto_refresh = value
        if value:
            self._sync_watch()
        else:
            self._watcher.clear()
        self.autoRefreshChanged.emit()
        self._schedule_save()

    @Slot(str, str, result=bool)
    def setWorkTime(self, which, text):
        """改工作时段。返回 False 表示没接受（格式不对或早于开始），QML 据此回填。

        为什么不交给 _clean_work 兜底：它遇到 start >= end 会整体回默认值，
        用户输错一次就把另一个时段也悄悄改掉，不如明确拒绝。
        """
        key = {"start": "startMin", "end": "endMin"}.get(which)
        if not key:
            return False
        value = state.parse_hhmm(text, -1)
        if value < 0:
            return False
        if which == "start" and value >= self._work_schedule["endMin"]:
            return False
        if which == "end" and value <= self._work_schedule["startMin"]:
            return False
        if value != self._work_schedule[key]:
            self._work_schedule = dict(self._work_schedule)
            self._work_schedule[key] = value
            self.workScheduleChanged.emit()
            self._schedule_save()
        return True

    @Slot(bool)
    def setPreviewActive(self, active):
        active = bool(active)
        if active != self._preview_active:
            self._preview_active = active
            self.previewActiveChanged.emit()

    def _set_quality(self, key, quality):
        """把单项清晰度并进现有 prefs 再整体清洗：不走「只传一项」的路子，
        否则清洗函数会把另一项按默认值覆盖掉。"""
        merged = dict(self._preview_prefs)
        merged[key] = quality
        cleaned = state._clean_preview_prefs(merged)
        if cleaned == self._preview_prefs:
            return
        self._preview_prefs = cleaned
        self.previewPrefsChanged.emit()
        self._schedule_save()

    @Slot(float)
    def setPdfQuality(self, quality):
        """PDF 清晰度档位（1.0 / 1.5 / 2.0）。非法值由清洗函数回退到默认档。

        不需要清页图缓存：缓存 key 是 (token, 页, 量化后的渲染宽度)，
        倍率乘进宽度里，换档 key 自然不同，等于自动失效。
        """
        self._set_quality("pdfQuality", quality)

    @Slot(float)
    def setImageQuality(self, quality):
        """图片清晰度档位（1.0 / 1.5 / 2.0），语义是解码尺寸倍率。

        QML 那边会把结果 cap 到原图尺寸，所以调高对小于窗口的图没有副作用，
        只会让「原图比显示区大」的图多解码一些像素。
        """
        self._set_quality("imageQuality", quality)

    @Slot()
    def shutdown(self):
        """退出前排干后台扫描与预览，避免工作线程在对象销毁后继续发信号。"""
        self._watcher.clear()
        self._left._batch.stop()
        self._right._batch.stop()
        self._preview._cancel_pending()
        self._runner.shutdown()

    @Slot()
    def saveState(self):
        panels = {}
        for side, panel in self._panels.items():
            panels[side] = {
                "cwd": panel.cwd,
                "sortColumn": panel.sortColumn,
                "sortDesc": panel.sortDesc,
                "showHidden": panel.showHidden,
                "history": panel.history,
                "histPos": panel.histPos,
            }
        state.save(self._state_path, {
            "version": state.VERSION,
            "activeSide": self._active_side,
            "leftWidth": self._left_width,
            "showHidden": self._show_hidden,
            "autoRefresh": self._auto_refresh,
            "workSchedule": dict(self._work_schedule),
            "favorites": list(self._favorites),
            "previewWindow": dict(self._preview_window),
            "previewPrefs": dict(self._preview_prefs),
            "panels": panels,
        })

    # ---------------- 内部 ----------------
    def _load_state(self):
        data = state.load(self._state_path, HOME)
        self._active_side = data.get("activeSide", "left")
        self._left_width = state.clamp_width(data.get("leftWidth"))
        self._favorites = list(data.get("favorites", []))
        self._preview_window = dict(data.get("previewWindow")
                                    or state._clean_preview_window(None))
        self._preview_prefs = dict(data.get("previewPrefs")
                                   or state._clean_preview_prefs(None))
        self._show_hidden = bool(data.get("showHidden", False))
        self._auto_refresh = bool(data.get("autoRefresh", True))
        self._work_schedule = dict(data.get("workSchedule")
                                   or state._clean_work(None))
        for side in ("left", "right"):
            cfg = data["panels"][side]
            cwd_value = state.validate(cfg["cwd"], HOME, self._local_source)
            self._panels[side].apply_initial(cwd_value, cfg)
        self.activeSideChanged.emit()
        self.leftWidthChanged.emit()
        self.showHiddenChanged.emit()
        self.workScheduleChanged.emit()
        self.favoritesChanged.emit()      # 保险：QML 若已挂载，同步一次收藏列表
        self.previewPrefsChanged.emit()

    def _schedule_save(self):
        self._save_timer.start()

    def _on_busy_changed(self):
        value = any(p.busy for p in self._panels.values())
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()

    @Slot(str, bool, "QVariantList")
    def _on_job_finished(self, job_id, ok, payload):
        if job_id and job_id == self._op_job:
            result = payload[0] if payload else {}
            self._op_job = ""
            self._tick_job = ""
            self._op_busy = False
            self._op_text = ""
            self._op_target = None
            self._progress = 100
            self.opChanged.emit()
            self.progressChanged.emit()
            on_done = self._op_on_done
            self._op_on_done = None
            try:
                if ok:
                    if on_done is not None:
                        on_done(result if isinstance(result, dict) else {})
                else:
                    self.toast.emit("操作失败：%s" % (result or "未知错误"))
            finally:
                # 进度条别一直顶着 100%，收尾后归零（下一次操作会重新从 0 开始）
                QTimer.singleShot(700, self._reset_progress)
            return
        for panel in self._panels.values():
            if panel.handle_result(job_id, ok, payload):
                break
