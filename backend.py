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
-- 类型改名要按名字批量回写、设置页要数「这个类型下有几条」，都靠它。
-- 老库没有这个索引，CREATE IF NOT EXISTS 每次启动跑一遍，会自动补上。
CREATE INDEX IF NOT EXISTS idx_log_entries_type ON log_entries(entry_type);
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
    typesChanged = Signal()

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
        self._types = []
        self._reload_types()
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

    @Property(list, notify=typesChanged)
    def logTypes(self):
        """日志类型选项，按 seq 升序。额外带 n = 该类型下有多少条记录。

        不能是 constant=True —— 设置页会增删改类型，QML 必须收到通知才会重画。
        """
        return self._types

    def _reload_types(self):
        """重算类型表 + 使用计数。只在类型变动时调，别放进属性 getter 里：
        QML 一次布局可能读它好几遍，每次都扫全表没必要。"""
        rows = self._conn.execute(
            "SELECT t.id, t.name, t.seq,"
            " (SELECT COUNT(*) FROM log_entries e WHERE e.entry_type = t.name) AS n"
            " FROM log_types t ORDER BY t.seq, t.id").fetchall()
        self._types = [dict(r) for r in rows]

    # ---------- 日志类型的增删改 ----------
    # 三个都返回 {ok, reason, affected}，QML 直接拿去弹 Toast / 判断。
    # 语义上「类型」只是 log_entries.entry_type 这个字符串的候选表，没有外键，
    # 所以任何一次写都必须在 Python 侧一次做完，不能让 QML 分两步调。

    MAX_TYPE_NAME = 12

    def _type_name_taken(self, name, exclude_id=0):
        row = self._conn.execute(
            "SELECT id FROM log_types WHERE name = ? AND id <> ?",
            (name, exclude_id)).fetchone()
        return row is not None

    @Slot(str, result="QVariantMap")
    def addLogType(self, name):
        n = (name or "").strip()
        if not n:
            return {"ok": False, "reason": "名称不能为空", "affected": 0}
        if len(n) > self.MAX_TYPE_NAME:
            return {"ok": False, "reason": "名称最多 %d 个字" % self.MAX_TYPE_NAME,
                    "affected": 0}
        if self._type_name_taken(n):
            return {"ok": False, "reason": "已经有叫「%s」的类型了" % n, "affected": 0}
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM log_types").fetchone()
        self._q("INSERT INTO log_types(name, seq) VALUES(?,?)", (n, row[0]))
        self._reload_types()
        self.typesChanged.emit()
        return {"ok": True, "reason": "", "affected": 0}

    @Slot(int, str, result="QVariantMap")
    def renameLogType(self, tid, name):
        """改名会**连带更新已有记录**。

        旧版 WorkLogs 只改 log_types 这一行，历史条目的 entry_type 还是老名字，
        结果下拉框里叫「补贴款」、列表里 14045 条却还显示「补贴」。类型是同一个，
        这里一次事务里把两边一起改掉。
        """
        n = (name or "").strip()
        if not n:
            return {"ok": False, "reason": "名称不能为空", "affected": 0}
        if len(n) > self.MAX_TYPE_NAME:
            return {"ok": False, "reason": "名称最多 %d 个字" % self.MAX_TYPE_NAME,
                    "affected": 0}
        row = self._conn.execute(
            "SELECT name FROM log_types WHERE id = ?", (tid,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "这个类型已经不在了", "affected": 0}
        old = row["name"]
        if old == n:
            return {"ok": True, "reason": "", "affected": 0}
        if self._type_name_taken(n, exclude_id=tid):
            return {"ok": False, "reason": "已经有叫「%s」的类型了" % n, "affected": 0}
        try:
            # 不显式 BEGIN：sqlite3 在第一条 DML 前自己开事务，两条 UPDATE 天然
            # 在同一个事务里。手写 BEGIN 反而可能撞上「transaction within a
            # transaction」。
            self._conn.execute("UPDATE log_types SET name = ? WHERE id = ?", (n, tid))
            cur = self._conn.execute(
                "UPDATE log_entries SET entry_type = ? WHERE entry_type = ?", (n, old))
            affected = cur.rowcount
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            return {"ok": False, "reason": "改名失败：%s" % e, "affected": 0}
        self._reload_types()
        # 记录内容变了，日历条数不变但列表要重画
        self._load_entries()
        self.typesChanged.emit()
        return {"ok": True, "reason": "", "affected": max(0, affected)}

    @Slot(int, result="QVariantMap")
    def deleteLogType(self, tid):
        """只允许删**空类型**。

        有记录在用的类型一律拒绝，并告诉用户还有多少条。理由：删掉之后那些条目的
        entry_type 就成了孤儿字符串 —— 下拉里没有、筛选里也没有，用户再也点不到它，
        想抢救只能改数据库。宁可让他先改名或换类型。
        """
        row = self._conn.execute(
            "SELECT name FROM log_types WHERE id = ?", (tid,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "这个类型已经不在了", "affected": 0}
        name = row["name"]
        used = self._conn.execute(
            "SELECT COUNT(*) FROM log_entries WHERE entry_type = ?", (name,)).fetchone()[0]
        if used:
            return {"ok": False, "affected": used,
                    "reason": "「%s」下还有 %d 条记录，删了这些记录就没类型了。"
                              "先改名、或把它们改成别的类型。" % (name, used)}
        self._q("DELETE FROM log_types WHERE id = ?", (tid,))
        self._reload_types()
        self.typesChanged.emit()
        return {"ok": True, "reason": "", "affected": 0}

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
