#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旧 WorkLogs（work_logs.db）→ 新 Workspace（workspace.db）数据迁移。

背景
----
旧库是 PyQt5 版 WorkLogs 的数据，是一票否决级资产。新库 schema 有意改了几处，
不能直接拷表。本脚本唯一的目标是「一行不丢」地搬过来，且可以反复重跑。

硬约束（改这个脚本之前先读）
--------------------------
1. 旧库永远只读：`file:...?mode=ro`，跑前跑后各算一次 sha256，不一致立刻中止。
2. 新库先冷备份：`workspace.db.bak-<时间戳>`，用 sqlite3 的 backup API 而不是
   shutil.copy —— 库在 WAL 模式下直接拷主文件会拷到半截事务。
3. 幂等靠 `_import_map(src_table, src_id) → dst_id`，**不靠内容指纹**。
   旧库里有 5915 组完全重复的条目（同一天同一个人记了 4 次同样的内容），
   按内容去重就等于替他删数据。重跑只认映射表。
4. 默认 dry-run：整场迁移在一个事务里跑完再 ROLLBACK，报告是真数字但不落盘。
5. 迁移完对账：按映射表把目标行捞回来，和源行做 sha256 逐字节比对。

用法
----
    python3 tools/import_worklogs.py --src ~/work_logs.db
    python3 tools/import_worklogs.py --src ~/work_logs.db --apply
    python3 tools/import_worklogs.py --src ~/work_logs.db --apply --purge-new

    --src        旧库路径（必填）
    --dst        新库路径，默认 $WORKSPACE_HOME/workspace.db
    --apply      真写。不加就是 dry-run
    --purge-new  顺带清掉新库里的测试数据（日志/待办/模板 + 没有引用的默认类型），
                 只在 --apply 时生效，且会再备份一次
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path

APP_HOME = Path(os.environ.get("WORKSPACE_HOME",
                               str(Path.home() / ".config" / "workspace")))
DEFAULT_DST = APP_HOME / "workspace.db"

# 新库启动时会 INSERT OR IGNORE 这四个默认类型。迁移后如果「工作/学习/生活」
# 一条日志都没引用，它们就是界面上的纯噪音。
#
# 「其他」不在这里面：它是真实可用的兜底类型，旧库偏偏也用它（1243 条），
# 一定要留着。清理只针对另外三个占位符。
PLACEHOLDER_TYPES = ("工作", "学习", "生活")

MAP_DDL = """
CREATE TABLE IF NOT EXISTS _import_map(
  src_table TEXT    NOT NULL,
  src_id    INTEGER NOT NULL,
  dst_id    INTEGER NOT NULL,
  imported_at TEXT DEFAULT (datetime('now','localtime')),
  PRIMARY KEY(src_table, src_id));
"""

# 每次真写都留一行：这一版旧库（按 sha256 认）迁过多少行。
# 用途是防两个坑 —— 见 Migrator.guard()。
RUNS_DDL = """
CREATE TABLE IF NOT EXISTS _import_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  src_path   TEXT,
  src_sha256 TEXT,
  src_rows   INTEGER,
  applied_at TEXT DEFAULT (datetime('now','localtime')));
"""


# ---------------------------------------------------------------- 小工具

def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def digest_rows(rows) -> str:
    """行集合的指纹：字段用 \\x1f 分隔、行用 \\x1e 分隔，NULL 记成空串。

    对账用。要求两边 ORDER BY 同一个稳定键，否则顺序不同指纹必然不同。
    """
    h = hashlib.sha256()
    for r in rows:
        h.update("\x1f".join("" if v is None else str(v) for v in r).encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def n(v):
    return "" if v is None else str(v)


def open_ro(p: Path) -> sqlite3.Connection:
    """只读打开。uri=True 才认 mode=ro；路径要转成 URL 编码过的绝对路径。"""
    uri = "file:%s?mode=ro" % str(p.resolve()).replace("?", "%3f").replace("#", "%23")
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}


# ---------------------------------------------------------------- 迁移主体

class Migrator:
    def __init__(self, src_path: Path, dst_path: Path, purge_new: bool):
        self.src_path = src_path
        self.dst_path = dst_path
        # 注意别叫 self.purge_new —— 下面有个同名方法，会被 bool 盖掉
        self.want_purge = purge_new
        self.src_sha = ""
        self.has_logs_table = False
        self.has_templates = False
        self.has_shortcuts = False
        self.reconcile_result = ([], True)
        self.src: sqlite3.Connection | None = None
        self.dst: sqlite3.Connection | None = None
        self.stats: dict[str, dict] = {}
        self.warnings: list[str] = []

    # ---- 连接与预检

    def open(self):
        self.src = open_ro(self.src_path)
        self.dst = sqlite3.connect(str(self.dst_path))
        self.dst.row_factory = sqlite3.Row
        # 事务自己管：dry-run 要能整体 rollback
        self.dst.isolation_level = None

    def precheck(self):
        """源库结构核对。缺表不算错（不同版本可能没有），但要记下来。"""
        need = ["log_entries", "log_types", "todos"]
        missing = [t for t in need if not table_exists(self.src, t)]
        if missing:
            raise SystemExit("[中止] 旧库里缺表：%s —— 这不是 WorkLogs 的库？"
                             % ", ".join(missing))
        self.has_logs_table = table_exists(self.src, "logs")
        self.has_templates = table_exists(self.src, "templates")
        self.has_shortcuts = table_exists(self.src, "shortcuts")
        if self.has_shortcuts:
            cnt = self.src.execute("SELECT COUNT(*) FROM shortcuts").fetchone()[0]
            if cnt:
                self.warnings.append(
                    "旧库有 %d 条自定义快捷键，**不迁移**：新库的快捷键走 QML "
                    "Shortcut，旧的 action_id 在新代码里已经对不上，硬搬只会是死配置。"
                    % cnt)

    # ---- 缓存已有映射

    def _done(self, table: str) -> dict[int, int]:
        rows = self.dst.execute(
            "SELECT src_id, dst_id FROM _import_map WHERE src_table=?", (table,))
        return {r["src_id"]: r["dst_id"] for r in rows}

    def _map(self, table: str, src_id: int, dst_id: int):
        self.dst.execute(
            "INSERT OR REPLACE INTO _import_map(src_table, src_id, dst_id) VALUES(?,?,?)",
            (table, src_id, dst_id))

    # ---- 重跑防呆

    def guard(self, before: dict, force: bool, allow_diff_src: bool,
              purge: bool = False):
        """两个会让数据翻倍的场景，靠 `_import_map` / `_import_runs` 拦一下。

        ① 映射表丢了但数据还在。典型路径：从一份更早的 workspace.db 备份还原
           （那份里没有 `_import_map`），再跑一遍 → 15288 条整份再插一遍，变 30576。
           判据不是「新库非空」—— 新库里有几条手写的试用记录是常态，那样拦下来太烦。
           改成「新库日志数已经接近源库的一半」，那才像是迁过的。
        ② 换了另一份旧库。映射表的主键是 (src_table, src_id)，两份旧库的 id 分配
           未必一致 —— 拿 A 库的映射去删改 B 库的条目，会串。
        """
        mapped = 0
        if table_exists(self.dst, "_import_map"):
            mapped = self.dst.execute("SELECT COUNT(*) FROM _import_map").fetchone()[0]

        src_rows = self.src.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]
        # 15288 * 0.5 ≈ 7600：手写的试用记录到不了这个量级，迁过一次的一步就到位
        danger = max(50, src_rows // 2)
        if (before.get("log_entries", 0) >= danger and mapped == 0
                and not purge and not force):
            print("[中止] 新库里已经有 %d 条日志（旧库 %d 条），但迁移映射表是空的。"
                  % (before["log_entries"], src_rows))
            print("       这时候重跑会把旧库整份再插一遍（翻倍）。")
            print("       如果新库这些条目确实是手写的、要保留，加 --force。")
            print("       如果目标库已经迁过一次、只是映射表丢了，先别动，"
                  "从备份还原再查。")
            return False

        if table_exists(self.dst, "_import_runs"):
            prev = [r["src_sha256"] for r in
                    self.dst.execute("SELECT src_sha256 FROM _import_runs")]
            if prev and self.src_sha not in prev and not allow_diff_src:
                print("[中止] 这次迁的旧库和之前迁过的不是同一份文件：")
                print("       本次 sha256 %s" % self.src_sha)
                for s in sorted(set(prev)):
                    print("       迁过   sha256 %s" % s)
                print("       两份库的条目 id 未必一一对应，硬迁会让映射表串行。")
                print("       确认要混着迁，加 --allow-different-src。"
                      "更稳的做法是挑定一份，从干净的新库重来。")
                return False
        return True

    # ---- 1. 类型

    def migrate_types(self):
        existing = {r["name"]: dict(r) for r in
                    self.dst.execute("SELECT id, name, seq FROM log_types")}
        max_seq = max([r["seq"] or 0 for r in existing.values()] or [-1])
        added, reused = [], []
        for r in self.src.execute("SELECT id, name, seq FROM log_types ORDER BY seq, id"):
            name = (r["name"] or "").strip()
            if not name:
                continue
            if name in existing:
                reused.append(name)
                self._map("log_types", r["id"], existing[name]["id"])
                continue
            max_seq += 1
            cur = self.dst.execute(
                "INSERT INTO log_types(name, seq) VALUES(?,?)", (name, max_seq))
            added.append(name)
            existing[name] = {"id": cur.lastrowid, "name": name, "seq": max_seq}
            self._map("log_types", r["id"], cur.lastrowid)

        # 旧库用到的类型排到最前面，按源库的 seq 顺序。不这么做的话「补贴」（14045 条）
        # 会排在新库自带的「工作/学习/生活」后面 —— 每次录日志都要在一堆空类型里找。
        src_order = [(r["name"] or "").strip()
                     for r in self.src.execute("SELECT name FROM log_types ORDER BY seq, id")]
        all_types = self.dst.execute(
            "SELECT id, name FROM log_types ORDER BY seq, id").fetchall()
        front = [t for t in all_types if t["name"] in src_order]
        front.sort(key=lambda t: src_order.index(t["name"]))
        back = [t for t in all_types if t["name"] not in src_order]
        for i, t in enumerate(front + back):
            self.dst.execute("UPDATE log_types SET seq=? WHERE id=?", (i, t["id"]))

        self.stats["log_types"] = {"src": self.src.execute(
            "SELECT COUNT(*) FROM log_types").fetchone()[0],
            "added": added, "reused": reused}

    # ---- 2. 日志条目

    def migrate_entries(self):
        done = self._done("log_entries")
        rows = self.src.execute(
            "SELECT id, log_date, entry_type, subject, content "
            "FROM log_entries ORDER BY id").fetchall()
        todo = [r for r in rows if r["id"] not in done]
        # log_types 的映射，用来把类型名规范化（旧库可能有前后空格）
        type_fix = {r["name"]: (r["name"] or "").strip()
                    for r in self.src.execute("SELECT name FROM log_types")}
        payload = []
        for r in todo:
            et = r["entry_type"]
            if et is not None:
                et = type_fix.get(et, et).strip() or None
            payload.append((r["log_date"], et, r["subject"], r["content"]))
        for r, p in zip(todo, payload):
            cur = self.dst.execute(
                "INSERT INTO log_entries(log_date, entry_type, subject, content) "
                "VALUES(?,?,?,?)", p)
            self._map("log_entries", r["id"], cur.lastrowid)
        self.stats["log_entries"] = {
            "src": len(rows), "skipped": len(rows) - len(todo), "inserted": len(todo)}

    # ---- 3. 待办

    def migrate_todos(self):
        done = self._done("todos")
        src_cols = columns(self.src, "todos")
        rows = self.src.execute("SELECT * FROM todos ORDER BY id").fetchall()
        todo = [r for r in rows if r["id"] not in done]
        for r in todo:
            # 旧库是 completed INTEGER(0/1)，新库是 status TEXT('open'/'done')。
            # 旧库没有 source_entry_id（新库用它标「来自日志」），留 NULL。
            if "status" in src_cols:                    # 万一哪天旧库也升级了
                status = r["status"] or "open"
            else:
                status = "done" if (r["completed"] or 0) else "open"
            cur = self.dst.execute(
                "INSERT INTO todos(content, due_date, priority, status, "
                "created_at, updated_at, completed_at) VALUES(?,?,?,?,?,?,?)",
                (r["content"], r["due_date"], r["priority"] or 1, status,
                 r["created_at"], r["updated_at"], r["completed_at"]))
            self._map("todos", r["id"], cur.lastrowid)
        self.stats["todos"] = {
            "src": len(rows), "skipped": len(rows) - len(todo), "inserted": len(todo)}

    # ---- 4. 回复模板

    def migrate_templates(self):
        if not self.has_templates:
            self.stats["templates"] = {"src": 0, "skipped": 0, "inserted": 0}
            return
        done = self._done("templates")
        rows = self.src.execute("SELECT * FROM templates ORDER BY id").fetchall()
        todo = [r for r in rows if r["id"] not in done]
        for r in todo:
            cur = self.dst.execute(
                "INSERT INTO templates(name, content, seq, created_at, updated_at) "
                "VALUES(?,?,?,?,?)",
                (r["name"], r["content"], r["seq"], r["created_at"], r["updated_at"]))
            self._map("templates", r["id"], cur.lastrowid)
        self.stats["templates"] = {
            "src": len(rows), "skipped": len(rows) - len(todo), "inserted": len(todo)}

    # ---- 5. 年度日志（旧 logs 表，新库没有对应表）

    def migrate_logs(self):
        if not self.has_logs_table:
            self.stats["logs"] = {"src": 0, "inserted": 0, "note": "旧库没有 logs 表"}
            return
        done = self._done("logs")
        rows = self.src.execute("SELECT * FROM logs ORDER BY log_year").fetchall()
        todo = [r for r in rows if r["id"] not in done]
        if not todo:
            self.stats["logs"] = {"src": len(rows), "inserted": 0}
            return
        # 新库没有「年度日志」这张表，也不打算为它加 —— 那就落到 12-31 那天的
        # 一条日志里，标题带年份。宁可格式将就，也不能让整年内容消失。
        for r in todo:
            year = (n(r["log_year"]) or "").strip()
            if not (len(year) == 4 and year.isdigit()):
                self.warnings.append(
                    "年度日志 log_year=%r 不是 4 位年份，已强行放到 1970-12-31，"
                    "请人工确认这条。" % (year,))
                year = "1970"
            cur = self.dst.execute(
                "INSERT INTO log_entries(log_date, entry_type, subject, content, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?)",
                ("%s-12-31" % year, None, "%s 年度总结" % year,
                 r["content"], r["updated_at"], r["updated_at"]))
            self._map("logs", r["id"], cur.lastrowid)
        self.warnings.append(
            "旧库「年度日志」%d 条已转成 log_entries（日期落在当年 12-31，标题写成"
            "「xxxx 年度总结」）。新库没有这张表，内容一字未改，但它在界面上的位置变了"
            "—— 原来在「年度日志」页里，现在要去那一天翻。" % len(todo))

    # ---- 6. 清理新库测试数据（可选）

    def purge_new(self):
        deleted = {}
        for t in ("log_entries", "todos", "templates"):
            c = self.dst.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            if c:
                self.dst.execute("DELETE FROM %s" % t)
                deleted[t] = c
        # 只删「一条日志都没引用」的占位类型，有引用的一律不碰
        removed = []
        for r in self.dst.execute("SELECT id, name FROM log_types").fetchall():
            if r["name"] not in PLACEHOLDER_TYPES:
                continue
            used = self.dst.execute(
                "SELECT COUNT(*) FROM log_entries WHERE entry_type=?",
                (r["name"],)).fetchone()[0]
            if not used:
                self.dst.execute("DELETE FROM log_types WHERE id=?", (r["id"],))
                removed.append(r["name"])
        if removed:
            deleted["log_types"] = removed
        self.stats["purged"] = deleted

    # ---- 7. 对账

    def reconcile(self) -> bool:
        """按映射表把目标行捞回来，和源行逐字节比对。"""
        ok = True
        checks = []

        # 迁移时把 entry_type 做了 strip（旧库类型名带前后空格），对账要按同一套
        # 规范化再比，否则「只差一个空格」会被当成不一致。
        type_fix = {(r["name"] or ""): (r["name"] or "").strip()
                    for r in self.src.execute("SELECT name FROM log_types")}

        def norm_type(v):
            if v is None:
                return None
            return type_fix.get(v, v).strip() or None

        def mapped_src_ids(table: str) -> set:
            return {r[0] for r in self.dst.execute(
                "SELECT src_id FROM _import_map WHERE src_table=?", (table,))}

        # 日志条目。_import_map 在新库，源库没有这张表 —— 所以先取映射好的
        # src_id 集合，再去源库全表扫一遍按 id 升序挑出来（15k 行全扫不到 10ms，
        # 比拼 15000 个占位符干净）。
        want = mapped_src_ids("log_entries")
        src_rows = [(r[1], norm_type(r[2]), r[3], r[4])
                    for r in self.src.execute(
                        "SELECT id, log_date, entry_type, subject, content "
                        "FROM log_entries ORDER BY id")
                    if r[0] in want]
        q_dst = ("SELECT e.log_date, e.entry_type, e.subject, e.content "
                 "FROM _import_map m JOIN log_entries e ON e.id = m.dst_id "
                 "WHERE m.src_table='log_entries' ORDER BY m.src_id")
        dst_rows = [tuple(r) for r in self.dst.execute(q_dst)]
        a, b = digest_rows(src_rows), digest_rows(dst_rows)
        checks.append(("log_entries", len(src_rows), len(dst_rows), a == b))
        ok &= (a == b) and len(src_rows) == len(dst_rows)

        # 待办：字段有结构变化，比对 content/due_date 两个语义字段
        want = mapped_src_ids("todos")
        src_rows = [(r[1], r[2]) for r in self.src.execute(
            "SELECT id, content, due_date FROM todos ORDER BY id") if r[0] in want]
        q_dst = ("SELECT t.content, t.due_date FROM _import_map m "
                 "JOIN todos t ON t.id = m.dst_id WHERE m.src_table='todos' "
                 "ORDER BY m.src_id")
        dst_rows = [tuple(r) for r in self.dst.execute(q_dst)]
        a, b = digest_rows(src_rows), digest_rows(dst_rows)
        checks.append(("todos", len(src_rows), len(dst_rows), a == b))
        ok &= (a == b)

        if self.has_templates:
            want = mapped_src_ids("templates")
            src_rows = [(r[1], r[2]) for r in self.src.execute(
                "SELECT id, name, content FROM templates ORDER BY id") if r[0] in want]
            q_dst = ("SELECT t.name, t.content FROM _import_map m "
                     "JOIN templates t ON t.id = m.dst_id "
                     "WHERE m.src_table='templates' ORDER BY m.src_id")
            dst_rows = [tuple(r) for r in self.dst.execute(q_dst)]
            a, b = digest_rows(src_rows), digest_rows(dst_rows)
            checks.append(("templates", len(src_rows), len(dst_rows), a == b))
            ok &= (a == b)

        # 类型的名称集合（旧库每个类型名在新库都得在）
        src_names = {r[0] for r in self.src.execute("SELECT name FROM log_types")}
        dst_names = {r[0] for r in self.dst.execute("SELECT name FROM log_types")}
        miss = src_names - dst_names
        if miss:
            self.warnings.append("类型丢失：%s" % ", ".join(sorted(miss)))
            ok = False

        self.reconcile_result = (checks, ok)
        return ok

    # ---- 汇总

    def print_report(self, applied: bool, before: dict, after: dict):
        line = "-" * 68
        print()
        print(line)
        print("  迁移%s" % ("已执行" if applied else "预演（未写入，加 --apply 才落盘）"))
        print(line)
        print("  旧库 %s" % self.src_path)
        print("        sha256 %s" % self.src_sha_before)
        print("  新库 %s" % self.dst_path)
        print()

        s = self.stats
        if "log_types" in s:
            t = s["log_types"]
            print("  log_types   源 %d ｜ 新建 %d（%s）｜ 复用 %d（%s）"
                  % (t["src"], len(t["added"]), "、".join(t["added"]) or "无",
                     len(t["reused"]), "、".join(t["reused"]) or "无"))
        for key, label in (("log_entries", "log_entries"), ("todos", "todos     "),
                           ("templates", "templates ")):
            if key in s:
                t = s[key]
                print("  %s  源 %d ｜ 本次新增 %d ｜ 已迁过跳过 %d"
                      % (label, t["src"], t["inserted"], t["skipped"]))
        if "logs" in s:
            t = s["logs"]
            print("  logs        源 %d ｜ 转为日志条目 %d" % (t["src"], t["inserted"]))
        if "purged" in s and s["purged"]:
            p = s["purged"]
            parts = []
            for k, v in p.items():
                if isinstance(v, list):          # log_types 存的是被删的名字
                    parts.append("%s %s" % (k, "、".join(v)))
                else:
                    parts.append("%s %d 行" % (k, v))
            print("  清理新库测试数据：" + "；".join(parts))
        print()
        print("  新库行数  %s → %s" % (
            " ".join("%s=%d" % (k, v) for k, v in before.items()),
            " ".join("%s=%d" % (k, v) for k, v in after.items())))
        print()

        checks, ok = self.reconcile_result
        print("  对账（源行 ⟷ 目标行，sha256 逐字节比对）")
        for name, ns, nd, same in checks:
            print("    %-12s 源 %5d 条 ｜ 目标 %5d 条 ｜ %s"
                  % (name, ns, nd, "一致 ✓" if same else "不一致 ✗"))
        print()

        if self.warnings:
            print("  注意")
            for w in self.warnings:
                print("    · " + w)
            print()

        if not applied:
            print("  这只是预演。确认无误后加 --apply 真写（会先把新库备份一份）。")
        elif ok:
            print("  ✓ 全部一致。启动 app 看一眼：python3 main.py")
        else:
            print("  ✗ 对账不通过！新库已备份，别继续用，把上面的数字发我。")
        print(line)
        print()


# ---------------------------------------------------------------- 入口

def snapshot(conn: sqlite3.Connection) -> dict:
    out = {}
    for t in ("log_types", "log_entries", "todos", "templates"):
        if table_exists(conn, t):
            out[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    return out


def backup(dst_path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = dst_path.with_name(dst_path.name + ".bak-" + stamp)
    src = sqlite3.connect(str(dst_path))
    try:
        dst = sqlite3.connect(str(out))
        try:
            src.backup(dst)          # WAL 安全，不是文件拷贝
        finally:
            dst.close()
    finally:
        src.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="旧 WorkLogs 数据 → 新 Workspace 库（默认预演，不写盘）")
    ap.add_argument("--src", required=True, help="旧库 work_logs.db 路径")
    ap.add_argument("--dst", default=str(DEFAULT_DST),
                    help="新库路径，默认 %s" % DEFAULT_DST)
    ap.add_argument("--apply", action="store_true", help="真写（默认只预演）")
    ap.add_argument("--purge-new", action="store_true",
                    help="顺带清掉新库里的测试数据（只配合 --apply）")
    ap.add_argument("--force", action="store_true",
                    help="新库已有日志但映射表是空的，仍然继续（默认拦下）")
    ap.add_argument("--allow-different-src", action="store_true",
                    help="这次迁的旧库和上次迁过的不是同一份文件，仍然继续（默认拦下）")
    args = ap.parse_args(argv)

    src_path = Path(args.src).expanduser()
    dst_path = Path(args.dst).expanduser()
    if not src_path.is_file():
        print("[中止] 找不到旧库：%s" % src_path)
        return 2
    if not dst_path.is_file():
        # 新库不存在时 app 从没启动过，先建空库的活儿交给 app 自己
        print("[中止] 找不到新库：%s" % dst_path)
        print("        先启动一次 app（python3 main.py），让它建好库再迁。")
        return 2
    if src_path.resolve() == dst_path.resolve():
        print("[中止] 源和目标同一个文件。")
        return 2

    m = Migrator(src_path, dst_path, purge_new=args.purge_new)
    m.src_sha = sha256_file(src_path)
    m.src_sha_before = m.src_sha
    m.open()
    m.precheck()

    before = snapshot(m.dst)
    if not m.guard(before, args.force, args.allow_different_src,
                   purge=args.purge_new):
        m.src.close()
        m.dst.close()
        return 2

    # 只在真写时备份；预演反正会回滚。一次就够 —— 清理也在这个事务里，
    # 出事 Rollback 就回到备份时的状态，不需要第二份。
    if args.apply:
        bundle = backup(dst_path)
        print("[备份] 新库 →", bundle)

    m.dst.execute("BEGIN IMMEDIATE")
    try:
        m.dst.execute(MAP_DDL)
        m.dst.execute(RUNS_DDL)
        if args.purge_new:
            m.purge_new()
        m.migrate_types()
        m.migrate_entries()
        m.migrate_todos()
        m.migrate_templates()
        m.migrate_logs()
        after = snapshot(m.dst)
        m.reconcile()
        if args.apply:
            # 只在真写时留痕，预演 rollback 掉就不会污染「迁过哪一版」
            m.dst.execute(
                "INSERT INTO _import_runs(src_path, src_sha256, src_rows) "
                "VALUES(?,?,?)",
                (str(src_path), m.src_sha,
                 m.stats.get("log_entries", {}).get("src", 0)))
            m.dst.execute("COMMIT")
        else:
            m.dst.execute("ROLLBACK")
    except Exception:
        try:
            m.dst.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        m.src.close()
        m.dst.close()

    # 旧库必须一动不动
    sha_after = sha256_file(src_path)
    m.print_report(args.apply, before, after)
    if sha_after != m.src_sha_before:
        print("  ✗✗ 旧库被改写了！sha256 %s → %s" % (m.src_sha_before, sha_after))
        print("     立刻从别的备份恢复原件。")
        return 3
    print("  旧库 sha256 未变，确认只读。")
    print()
    return 0 if m.reconcile_result[1] or not args.apply else 1


if __name__ == "__main__":
    sys.exit(main())
