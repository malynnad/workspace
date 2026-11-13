"""文件列表模型（QAbstractListModel）。

项目里已知坑：PySide6 的 @Property(list) 不能直接当 ListView.model 用
（后端变了、信号也发了，前端仍显示旧数据）。文件列表数据量大且会分批插入，
所以这里走 C++ 模型；待办/日志/脚本等小集合仍保留 Property(list) + Connections 惯例。

10 个 role 与 filecore.entries.Entry.to_dict() 的键一一对应。
所有 beginResetModel / beginInsertRows 必须在主线程调用。
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Slot


class FileListModel(QAbstractListModel):
    NameRole = Qt.UserRole + 1
    VpathRole = Qt.UserRole + 2
    IsDirRole = Qt.UserRole + 3
    SizeRole = Qt.UserRole + 4
    MtimeRole = Qt.UserRole + 5
    KindRole = Qt.UserRole + 6
    SizeTextRole = Qt.UserRole + 7
    TimeTextRole = Qt.UserRole + 8
    SelectedRole = Qt.UserRole + 9
    IsCutRole = Qt.UserRole + 10

    _ROLE_NAMES = {
        NameRole: b"name",
        VpathRole: b"vpath",
        IsDirRole: b"isDir",
        SizeRole: b"size",
        MtimeRole: b"mtime",
        KindRole: b"kind",
        SizeTextRole: b"sizeText",
        TimeTextRole: b"timeText",
        SelectedRole: b"selected",
        IsCutRole: b"isCut",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._index: dict[str, int] = {}
        self._selected: set[str] = set()
        # 剪切中的路径（Qt 只认这个集合，不信行里的 isCut 字段）：
        # 重扫/重新排序后模型重建，但「正在剪切」是跨扫描存在的状态，
        # 所以放在模型里由后端统一推，不跟着每行 dict 走。
        self._cut: set[str] = set()

    # ---- QAbstractItemModel 必需实现 ----
    def roleNames(self):
        return dict(self._ROLE_NAMES)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._rows):
            return None
        item = self._rows[row]
        if role in (Qt.DisplayRole, self.NameRole):
            return item.get("name", "")
        if role == self.VpathRole:
            return item.get("vpath", "")
        if role == self.IsDirRole:
            return bool(item.get("isDir", False))
        if role == self.SizeRole:
            return int(item.get("size", 0))
        if role == self.MtimeRole:
            return float(item.get("mtime", 0))
        if role == self.KindRole:
            return item.get("kind", "other")
        if role == self.SizeTextRole:
            return item.get("sizeText", "")
        if role == self.TimeTextRole:
            return item.get("timeText", "")
        if role == self.SelectedRole:
            return item.get("vpath", "") in self._selected
        if role == self.IsCutRole:
            return item.get("vpath", "") in self._cut
        return None

    # ---- 数据装载（仅主线程） ----
    @Slot(list)
    def set_entries(self, rows: list[dict]):
        self.beginResetModel()
        self._rows = [dict(r) for r in rows]
        self._index = {r.get("vpath", ""): i for i, r in enumerate(self._rows)}
        self.endResetModel()

    @Slot(list)
    def append_batch(self, rows: list[dict]):
        if not rows:
            return
        first = len(self._rows)
        last = first + len(rows) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        for item in rows:
            item = dict(item)
            self._index[item.get("vpath", "")] = len(self._rows)
            self._rows.append(item)
        self.endInsertRows()

    @Slot("QVariantList")
    def set_selected(self, vpaths):
        new = set(vpaths or [])
        changed = self._selected ^ new
        self._selected = new
        for vp in changed:
            row = self._index.get(vp)
            if row is not None:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [self.SelectedRole])

    @Slot()
    def clear_selection(self):
        self.set_selected(set())

    @Slot("QVariantList")
    def set_cut(self, vpaths):
        """只重绘 isCut role，不动选中、不重扫。"""
        new = set(vpaths or [])
        changed = self._cut ^ new
        self._cut = new
        for vp in changed:
            row = self._index.get(vp)
            if row is not None:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [self.IsCutRole])

    def row_of(self, vpath: str) -> int:
        return self._index.get(vpath, -1)

    def vpath_at(self, row: int) -> str:
        if 0 <= row < len(self._rows):
            return self._rows[row].get("vpath", "")
        return ""

    def selected_vpaths(self) -> list[str]:
        return sorted(self._selected)
