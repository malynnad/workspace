"""Workspace 工作台 Python 后端：SQLite 数据层 + 脚本运行器，暴露给 QML。"""
import datetime
import os
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Property, QProcess, Signal, Slot

APP_HOME = Path(os.environ.get(
    "WORKSPACE_HOME", str(Path.home() / ".config" / "workspace")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS log_types(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  seq INTEGER);
CREATE TABLE IF NOT EXISTS log_entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  log_date TEXT NOT NULL,
  entry_type TEXT,
  subject TEXT,
  content TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_log_entries_date ON log_entries(log_date);
CREATE TABLE IF NOT EXISTS todos(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  due_date TEXT,
  priority INTEGER DEFAULT 1,
  status TEXT DEFAULT 'open',
  source_entry_id INTEGER,
  created_at TEXT DEFAULT (datetime('now','localtime')),
  updated_at TEXT DEFAULT (datetime('now','localtime')),
  completed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  content TEXT,
  seq INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
-- 默认类型只在**表为空时**播一次种。原来是 INSERT OR IGNORE 四条，等于每次
-- 启动都把「工作/学习/生活」补回去 —— 用户（或迁移脚本）删掉的类型活不过一次
-- 重启。加 NOT EXISTS 之后：全新库照样四条，删过的不会被复活。
INSERT INTO log_types(name, seq)
SELECT name, seq FROM (
  SELECT '工作' AS name, 0 AS seq
  UNION ALL SELECT '学习', 1
  UNION ALL SELECT '生活', 2
  UNION ALL SELECT '其他', 3
) WHERE NOT EXISTS (SELECT 1 FROM log_types);
"""


class WorkspaceDB(QObject):
    """日志 / 待办 / 模板的数据层。QML 里叫 backend。"""

    entriesChanged = Signal()
    todosChanged = Signal()
    listChanged = Signal()

    WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

    def __init__(self, db_path: Path):
        super().__init__()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._selected_date = ""
        self._entries = []
        self._todos = []
        self._list_items = []
        self._load_todos()

    # ---------- properties ----------
    @Property(list, notify=entriesChanged)
    def entries(self):
        return self._entries

    @Property(list, notify=entriesChanged)
    def listItems(self):
        """全部记录视图的扁平模型：header 项 + entry 项交替。"""
        return self._list_items

    @Property("QVariantMap", notify=entriesChanged)
    def overallStats(self):
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT log_date) AS d FROM log_entries"
        ).fetchone()
        return {"count": row["n"] or 0, "days": row["d"] or 0}

    @Property(list, notify=todosChanged)
    def todos(self):
        return self._todos

    @Property(int, notify=todosChanged)
    def openCount(self):
        return sum(1 for t in self._todos if t["status"] == "open")

    @Property(list, constant=True)
    def logTypes(self):
        rows = self._conn.execute("SELECT * FROM log_types ORDER BY seq").fetchall()
        return [dict(r) for r in rows]

    # ---------- 内部 ----------
    def _q(self, sql, args=()):
        cur = self._conn.execute(sql, args)
        self._conn.commit()
        return cur

    def _load_entries(self):
        rows = self._conn.execute(
            "SELECT * FROM log_entries WHERE log_date=? ORDER BY id",
            (self._selected_date,)).fetchall()
        self._entries = [dict(r) for r in rows]
        self.entriesChanged.emit()

    def _load_todos(self):
        rows = self._conn.execute(
            "SELECT * FROM todos ORDER BY status='done', due_date IS NULL, due_date, id DESC"
        ).fetchall()
        self._todos = [dict(r) for r in rows]
        self.todosChanged.emit()

    # ---------- 日志 ----------
    @Slot(str)
    def selectDate(self, d: str):
        self._selected_date = d
        self._load_entries()

    @Slot(str, str, str, str)
    def addEntry(self, d, etype, subject, content):
        self._q("INSERT INTO log_entries(log_date, entry_type, subject, content) "
                "VALUES(?,?,?,?)",
                (d, etype or None, subject or None, content or None))
        self._load_entries()

    @Slot(int, str, str, str)
    def updateEntry(self, eid, etype, subject, content):
        self._q("UPDATE log_entries SET entry_type=?, subject=?, content=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (etype or None, subject or None, content or None, eid))
        self._load_entries()

    @Slot(int)
    def deleteEntry(self, eid):
        self._q("DELETE FROM log_entries WHERE id=?", (eid,))
        self._load_entries()

    # ---------- 日历标记 / 统计 / 检索 ----------
    @Slot(int, int, result="QVariantMap")
    def dayCounts(self, year, month):
        """返回 {日: 条数}，供日历格子上的小圆点。键用字符串，QML 侧 obj["3"]。"""
        prefix = "%04d-%02d-" % (year, month + 1)
        rows = self._conn.execute(
            "SELECT log_date, COUNT(*) AS n FROM log_entries "
            "WHERE log_date LIKE ? GROUP BY log_date", (prefix + "%",)).fetchall()
        return {str(int(r["log_date"][8:10])): r["n"] for r in rows}

    @Slot(int, int, result="QVariantMap")
    def monthStats(self, year, month):
        prefix = "%04d-%02d-" % (year, month + 1)
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT log_date) AS d FROM log_entries "
            "WHERE log_date LIKE ?", (prefix + "%",)).fetchone()
        return {"count": row["n"] or 0, "days": row["d"] or 0}

    @classmethod
    def _date_label(cls, d):
        try:
            dt = datetime.datetime.strptime(d, "%Y-%m-%d")
        except (ValueError, TypeError):
            return str(d)
        return "%d 月 %d 日 · %s" % (dt.month, dt.day, cls.WEEK_CN[dt.weekday()])

    @Slot(str)
    def listEntries(self, keyword=""):
        """全部记录 / 搜索结果，按日期倒序分组，产出 header + entry 扁平列表。"""
        kw = (keyword or "").strip()
        sql = "SELECT * FROM log_entries"
        args = ()
        if kw:
            sql += (" WHERE subject LIKE ? OR content LIKE ?"
                    " OR entry_type LIKE ? OR log_date LIKE ?")
            like = "%" + kw + "%"
            args = (like, like, like, like)
        sql += " ORDER BY log_date DESC, id DESC"
        rows = [dict(r) for r in self._conn.execute(sql, args).fetchall()]

        items = []
        i = 0
        while i < len(rows):
            d = rows[i]["log_date"]
            group = []
            while i < len(rows) and rows[i]["log_date"] == d:
                group.append(rows[i])
                i += 1
            items.append({"kind": "header", "date": d, "count": len(group),
                          "label": self._date_label(d)})
            for e in group:
                e = dict(e)
                e["kind"] = "entry"
                items.append(e)
        self._list_items = items
        self.listChanged.emit()

    # ---------- 待办 ----------
    @Slot(str, str)
    def addTodo(self, content, due):
        self._q("INSERT INTO todos(content, due_date) VALUES(?,?)",
                (content, due or None))
        self._load_todos()

    @Slot(int)
    def toggleTodo(self, tid):
        row = self._conn.execute("SELECT status FROM todos WHERE id=?", (tid,)).fetchone()
        if not row:
            return
        if row["status"] == "done":
            self._q("UPDATE todos SET status='open', completed_at=NULL, "
                    "updated_at=datetime('now','localtime') WHERE id=?", (tid,))
        else:
            self._q("UPDATE todos SET status='done', "
                    "completed_at=datetime('now','localtime'), "
                    "updated_at=datetime('now','localtime') WHERE id=?", (tid,))
        self._load_todos()

    @Slot(int)
    def deleteTodo(self, tid):
        self._q("DELETE FROM todos WHERE id=?", (tid,))
        self._load_todos()

    @Slot(int)
    def entryToTodo(self, eid):
        """日志条目一键转待办：content 取主题（无主题取正文首行），带 source_entry_id。"""
        row = self._conn.execute("SELECT * FROM log_entries WHERE id=?", (eid,)).fetchone()
        if not row:
            return
        subject = row["subject"]
        if not subject:
            first = (row["content"] or "").strip().split("\n")[0]
            subject = first[:50] if first else "来自日志的待办"
        self._q("INSERT INTO todos(content, source_entry_id) VALUES(?,?)", (subject, eid))
        self._load_todos()


class ScriptRunner(QObject):
    """脚本面板：扫描 scripts 目录，QProcess 子进程运行，stdout/stderr 回显。"""

    scriptsChanged = Signal()
    outputArrived = Signal(str)
    runStateChanged = Signal(bool)

    EXT_ALLOW = (".py", ".sh", ".bash", ".command")
    SAMPLE = ('#!/usr/bin/env python3\n'
              '# 示例脚本：把任意 .py / .sh 丢进这个目录就会出现在面板里\n'
              '# 首行 # 注释会显示为脚本描述\n'
              'import sys, datetime\n'
              'print("你好，Workspace！现在时间：",\n'
              '      datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))\n'
              'print("脚本路径:", __file__)\n')

    def __init__(self, scripts_dir: Path):
        super().__init__()
        self._dir = scripts_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        sample = self._dir / "hello.py"
        if not sample.exists():
            sample.write_text(self.SAMPLE, encoding="utf-8")
        self._scripts = []
        self._proc = None
        self._scan()

    @Property(list, notify=scriptsChanged)
    def scripts(self):
        return self._scripts

    @Property(bool, notify=runStateChanged)
    def running(self):
        return self._proc is not None

    @Slot()
    def rescan(self):
        self._scan()

    def _scan(self):
        items = []
        for p in sorted(self._dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            if p.suffix.lower() not in self.EXT_ALLOW:
                continue
            desc = ""
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[:6]:
                    if line.startswith("#") and len(line.strip()) > 1:
                        desc = line.lstrip("# ").strip()
                        break
            except OSError:
                pass
            items.append({"name": p.name, "path": str(p), "desc": desc})
        self._scripts = items
        self.scriptsChanged.emit()

    @Slot(str)
    def run(self, path: str):
        if self._proc is not None:
            self.outputArrived.emit("[已有脚本在运行，先停止再试]\n")
            return
        p = Path(path)
        if not p.exists():
            self.outputArrived.emit(f"[脚本不存在: {path}]\n")
            return
        if p.suffix.lower() == ".py":
            cmd = [sys.executable, str(p)]
        else:
            cmd = ["bash", str(p)]
        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(str(self._dir))
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.readyReadStandardError.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self.runStateChanged.emit(True)
        self.outputArrived.emit(f"$ {' '.join(cmd)}\n")
        self._proc.start(cmd[0], cmd[1:])

    def _on_output(self):
        proc = self.sender()
        data = bytes(proc.readAllStandardOutput()) + bytes(proc.readAllStandardError())
        self.outputArrived.emit(data.decode("utf-8", errors="replace"))

    def _on_finished(self, code, _status):
        self.outputArrived.emit(f"\n[退出码 {code}]\n")
        self._proc = None
        self.runStateChanged.emit(False)

    @Slot()
    def stop(self):
        if self._proc is not None:
            self._proc.kill()
