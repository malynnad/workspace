"""纯逻辑层自检：`python -m filecore.selfcheck`。

零 Qt 依赖，可直接拷到麒麟上跑。任何一项 FAIL 都以 exit 1 结束。
跑之前建议 TMPDIR=$PWD/.tmp（本机沙箱对系统临时目录与文件删除有限制）。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

from . import bootstrap, state, vpath
from .cancel import CancelToken
from .entries import Entry, KIND_LABEL, human_size, human_time, kind_of, make_entry
from .listing import MAX_ITEMS, natural_key, scan_dir, sort_entries, visible_entries
from .preview import base as preview_base

_PASS = 0
_FAIL = 0


def check(label: str, fn):
    global _PASS, _FAIL
    try:
        fn()
        print(f"OK   {label}")
        _PASS += 1
    except Exception as exc:
        print(f"FAIL {label}: {type(exc).__name__}: {exc}")
        _FAIL += 1


def _vpath_roundtrip():
    v = "file:///Users/dannylam/Documents"
    assert vpath.parent(vpath.join(v, "子目录")) == v
    assert vpath.to_local(v) == "/Users/dannylam/Documents"
    assert vpath.parent("file:///") == "file:///"


def _vpath_encoding():
    p = "/tmp/我的 文件#1%2.txt"
    enc = vpath.encode_path(p)
    assert vpath.to_local(enc) == p, (enc, vpath.to_local(enc))
    assert "%23" in enc and "%25" in enc
    assert vpath.display(enc) == p
    assert vpath.from_user("~/Documents", "/Users/dannylam") == "file:///Users/dannylam/Documents"
    assert vpath.from_user("/var/tmp", "/Users/dannylam") == "file:///var/tmp"
    assert vpath.from_user("", "/Users/dannylam") == ""


def _vpath_archive():
    a = "zip+file:///Users/dannylam/a.zip!/dir/sub/"
    scheme, container, inner = vpath.parse(a)
    assert (scheme, container, inner) == ("zip+file", "/Users/dannylam/a.zip", "dir/sub/")
    p1 = vpath.parent(a)
    assert vpath.parse(p1)[2] == "dir", p1
    p2 = vpath.parent(p1)
    assert vpath.parse(p2)[2] == "", p2
    p3 = vpath.parent(p2)          # 退出压缩包
    assert vpath.parse(p3)[0] == "file" and vpath.to_local(p3) == "/Users/dannylam", p3
    assert vpath.parse(vpath.join(a, "x.txt"))[2] == "dir/sub/x.txt"
    for bad in ("..", "", "a/b"):
        try:
            vpath.join(a, bad)
        except ValueError:
            continue
        raise AssertionError(f"应当拒绝非法名称 {bad!r}")


def _vpath_hidden():
    assert vpath.is_hidden(".DS_Store")
    assert not vpath.is_hidden(".")
    assert not vpath.is_hidden("..")
    assert not vpath.is_hidden("normal.txt")


def _natural_key():
    names = ["f10", "f2", "a01", "a1", "中文", ""]
    ordered = sorted(names, key=natural_key)
    assert ordered.index("f2") < ordered.index("f10"), ordered
    assert ordered.index("a1") < ordered.index("a01"), ordered
    assert isinstance(natural_key("中文"), list)


def _sort_entries():
    items = [
        make_entry("zz.txt", "file:///zz.txt", False, 10, 1),
        make_entry("B", "file:///B", True, 0, 2),
        make_entry("aa.txt", "file:///aa.txt", False, 30, 3),
        make_entry("A", "file:///A", True, 0, 4),
    ]
    asc = sort_entries(items, 0, False)
    assert [e.name for e in asc] == ["A", "B", "aa.txt", "zz.txt"], [e.name for e in asc]
    desc = sort_entries(items, 0, True)
    assert [e.name for e in desc] == ["B", "A", "zz.txt", "aa.txt"], [e.name for e in desc]
    assert all(e.is_dir for e in desc[:2]), "降序时目录仍必须在前"
    by_size = sort_entries(items, 1, False)
    assert by_size[2].size <= by_size[3].size
    assert sort_entries([], 0, True) == []


def _visible_entries():
    items = [make_entry(n, "file:///" + n, False, 1, 1) for n in ("Alpha.txt", "beta.py", "中文.md")]
    assert len(visible_entries(items, "")) == 3
    assert [e.name for e in visible_entries(items, "ALPHA")] == ["Alpha.txt"]
    assert [e.name for e in visible_entries(items, "  .md ")] == ["中文.md"]
    assert visible_entries(items, "zzz") == []


def _kind_of():
    assert kind_of("file:///a.JPG") == "image"
    assert kind_of("file:///a.pdf") == "pdf"
    assert kind_of("file:///a.rar") == "archive"
    assert kind_of("file:///Makefile") == "code"
    assert kind_of("file:///.gitignore") == "code"
    assert kind_of("file:///.DS_Store") == "text"
    assert kind_of("file:///noext") == "other"
    assert kind_of("file:///x", True) == "folder"
    assert all(k in KIND_LABEL for k in ("folder", "image", "other"))


def _human():
    assert human_size(None) == ""
    assert human_size(0) == "0 B"
    assert human_size(1023) == "1023 B"
    assert human_size(1024) == "1.00 KB"
    assert "GB" in human_size(5 * 1024 ** 3)
    assert human_size(-1) == ""
    assert human_time(0) == ""
    assert human_time(-1) == ""
    assert human_time(1e20) == ""          # 越界不抛
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", human_time(1700000000))


def _entry_dict():
    d = make_entry("a.txt", "file:///a.txt", False, 12, 1700000000).to_dict()
    assert len(d) == 10, list(d)
    assert set(d) == {"name", "vpath", "isDir", "size", "mtime", "kind",
                      "sizeText", "timeText", "selected", "isCut"}
    assert all(v is not None for v in d.values())
    assert all(type(v) in (str, int, float, bool) for v in d.values())
    assert isinstance(d["isDir"], bool) and isinstance(d["selected"], bool)


def _scan_dir():
    # 默认只造 200 个文件：本机沙箱对短时间内批量创建/删除文件有速率限制，
    # 自检必须能在任何环境反复快速跑。5000 项的首屏性能压测在 Qt 层做
    # （FilePanel 分批填充，实测 5001 项 0.126s）；需要在这里跑重压测时
    # 设 FC_SELFCHECK_ITEMS=5000。
    count = int(os.environ.get("FC_SELFCHECK_ITEMS", "200"))
    tmp = tempfile.mkdtemp(prefix="fc_selfcheck_")
    try:
        for i in range(count):
            with open(os.path.join(tmp, f"f{i}.txt"), "wb"):
                pass
        with open(os.path.join(tmp, ".hidden1"), "wb"):
            pass
        with open(os.path.join(tmp, ".hidden2"), "wb"):
            pass
        os.mkdir(os.path.join(tmp, "A Folder"))
        try:
            os.symlink("/definitely/not/existing/target", os.path.join(tmp, "broken.lnk"))
        except OSError:
            pass
        base = vpath.encode_path(tmp)
        entries, err, truncated = scan_dir(tmp, base, False)
        assert err is None, err
        assert truncated is False
        assert len(entries) >= count + 1, len(entries)  # N 文件 + 目录 (+ 断链)
        assert all(not e.name.startswith(".") for e in entries)
        shown, _, _ = scan_dir(tmp, base, True)
        assert len(shown) >= len(entries) + 2, (len(shown), len(entries))
        names = [e.name for e in sort_entries(entries, 0, False)]
        assert names.index("f2.txt") < names.index("f10.txt")
        assert entries[0].is_dir or any(e.is_dir for e in sort_entries(entries, 0, False)[:1])
        token = CancelToken()
        token.cancel()
        cancelled, _, _ = scan_dir(tmp, base, False, token)
        assert len(cancelled) <= 1, len(cancelled)
        assert MAX_ITEMS == 20000
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _scan_errors():
    assert scan_dir("/definitely/not/existing/dir", "file:///definitely/not/existing/dir")[1] is not None
    tmp = tempfile.mkdtemp(prefix="fc_selfcheck_perm_")
    try:
        with open(os.path.join(tmp, "x.txt"), "wb"):
            pass
        entries, err, _ = scan_dir(os.path.join(tmp, "x.txt"), "file:///x", False)
        assert err is not None and entries == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _state():
    home = tempfile.mkdtemp(prefix="fc_selfcheck_home_")
    try:
        cfg = os.path.join(home, "sub", "filemanager_state.json")
        data = state.default_state(home)
        data["activeSide"] = "right"
        data["leftWidth"] = 9999
        assert state.save(cfg, data) is True
        assert not os.path.exists(cfg + ".tmp"), "原子写不应留 .tmp"
        loaded = state.load(cfg, home)
        assert loaded["activeSide"] == "right"
        assert loaded["leftWidth"] == state.clamp_width(9999)
        # 坏 JSON
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("{ broken")
        assert state.load(cfg, home)["activeSide"] == "left"
        # 版本不符
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('{"version": 99}')
        assert state.load(cfg, home)["version"] == state.VERSION
        # 路径回退
        from .sources.local import LocalSource
        src = LocalSource()
        good = state.validate(vpath.encode_path(home), home, src)
        assert good == vpath.encode_path(home), good
        bad = state.validate("file:///no/such/deep/path/at/all", home, src)
        assert bad == vpath.encode_path(home), bad
        unsupported = state.validate("zip+file:///a.zip!/", home, src)
        assert unsupported == vpath.encode_path(home), unsupported
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _registry_and_preview():
    from .sources import UnknownScheme, resolve
    bootstrap()
    assert resolve("file:///tmp").scheme == "file"
    try:
        resolve("zip+file:///a.zip!/")
    except UnknownScheme:
        pass
    else:
        raise AssertionError("M2 之前 zip+file 应当未注册")
    assert preview_base.REGISTRY == []
    assert preview_base.pick(None) is None
    spec = preview_base.PreviewSpec(kind="image", title="a.jpg")
    assert spec.kind == "image"


def _zero_qt():
    assert "PySide6" not in sys.modules
    assert "PyQt5" not in sys.modules


def main() -> int:
    for label, fn in [
        ("vpath round-trip", _vpath_roundtrip),
        ("vpath 编码 / 中文 / 空格 / # / %", _vpath_encoding),
        ("vpath 压缩包跨容器语义", _vpath_archive),
        ("vpath 隐藏文件判断", _vpath_hidden),
        ("natural_key 自然序", _natural_key),
        ("sort_entries 目录恒优先（含降序）", _sort_entries),
        ("visible_entries 过滤", _visible_entries),
        ("kind_of / KIND_LABEL", _kind_of),
        ("human_size / human_time 边界", _human),
        ("Entry.to_dict 10 role 无 None", _entry_dict),
        ("scan_dir 批量项 / 隐藏 / 断链 / 取消", _scan_dir),
        ("scan_dir 错误文案不抛异常", _scan_errors),
        ("state 读写 / 坏 JSON / 版本 / 路径回退", _state),
        ("FileSource 注册表 / 预览器空注册", _registry_and_preview),
        ("零 Qt 依赖", _zero_qt),
    ]:
        check(label, fn)
    print(f"\n{'ALL OK' if _FAIL == 0 else 'FAILED'} ({_PASS} passed, {_FAIL} failed)")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
