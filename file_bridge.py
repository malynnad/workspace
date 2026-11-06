"""双面板浏览的 Qt 桥接层（QML 只认这一层）。

FileManagerBackend  ->  left / right 两个 FilePanel + preview + activeSide
FilePanel           ->  一侧的 cwd / history / 排序 / 过滤 / 选中 / 状态 / 列表模型

M1a 只读。写操作、预览器、压缩包源都通过「注册表 + capabilities 能力位」留白：
不写抛 NotImplementedError 的空 Slot，QML 把未来控件绑到 capabilities 上，
后续里程碑只翻位 + 加 Slot，布局零改动。
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, Qt, Signal, Slot

import filecore
from filecore import listing, locations, state, vpath
from filecore.entries import kind_label
from filecore.sources import UnknownScheme, capabilities_of, resolve
from filecore.sources.local import LocalSource
from file_model import FileListModel
from preview_provider import PreviewProvider
from task_runner import TaskRunner

HOME = str(Path.home())


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
        target = vpath.from_user(text, HOME)
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
        value = bool(value)
        if value == self._show_hidden:
            return
        self._show_hidden = value
        self.hiddenChanged.emit()
        self._backend._schedule_save()
        self._scan()

    @Slot()
    def toggleHidden(self):
        self.setShowHidden(not self._show_hidden)

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
        else:
            # M1a：available=False，无任何 UI；M1b 打开能力后这里直接生效
            self._backend._preview.request(vp)

    @Slot(int, result=str)
    def vpathAt(self, row):
        """键盘上下移动时，QML 用行号换回 vpath（模型在 Python 侧）。"""
        return self._model.vpath_at(int(row))

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
            self.countsChanged.emit()
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
            self.countsChanged.emit()

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

    def __init__(self, app_home, parent=None):
        super().__init__(parent)
        filecore.bootstrap()
        self._app_home = Path(app_home)
        self._state_path = self._app_home / "filemanager_state.json"

        self._runner = TaskRunner(self)
        self._preview = PreviewProvider(self)
        self._active_side = "left"
        self._left_width = state.DEFAULT_LEFT_WIDTH
        self._favorites: list[str] = []          # 存 vpath，不是绝对路径
        self._local_source = LocalSource()
        self._busy = False
        self._progress = 0

        self._left = FilePanel("left", self, self)
        self._right = FilePanel("right", self, self)
        self._panels = {"left": self._left, "right": self._right}

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.saveState)

        # 显式 QueuedConnection：finished 从工作线程发出，模型操作必须回主线程
        self._runner.finished.connect(self._on_job_finished, Qt.QueuedConnection)
        for panel in self._panels.values():
            for sig in (panel.cwdChanged, panel.historyChanged, panel.sortChanged,
                        panel.hiddenChanged):
                sig.connect(self._schedule_save)

        self._load_state()
        self._left.refresh()
        self._right.refresh()

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

    @Slot()
    def shutdown(self):
        """退出前排干后台扫描，避免工作线程在对象销毁后继续发信号。"""
        self._left._batch.stop()
        self._right._batch.stop()
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
            "favorites": list(self._favorites),
            "panels": panels,
        })

    # ---------------- 内部 ----------------
    def _load_state(self):
        data = state.load(self._state_path, HOME)
        self._active_side = data.get("activeSide", "left")
        self._left_width = state.clamp_width(data.get("leftWidth"))
        self._favorites = list(data.get("favorites", []))
        for side in ("left", "right"):
            cfg = data["panels"][side]
            cwd_value = state.validate(cfg["cwd"], HOME, self._local_source)
            self._panels[side].apply_initial(cwd_value, cfg)
        self.activeSideChanged.emit()
        self.leftWidthChanged.emit()
        self.favoritesChanged.emit()      # 保险：QML 若已挂载，同步一次收藏列表

    def _schedule_save(self):
        self._save_timer.start()

    def _on_busy_changed(self):
        value = any(p.busy for p in self._panels.values())
        if value != self._busy:
            self._busy = value
            self.busyChanged.emit()

    @Slot(str, bool, "QVariantList")
    def _on_job_finished(self, job_id, ok, payload):
        for panel in self._panels.values():
            if panel.handle_result(job_id, ok, payload):
                break
