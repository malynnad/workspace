"""纯逻辑层自检：`python -m filecore.selfcheck`。

零 Qt 依赖，可直接拷到麒麟上跑。任何一项 FAIL 都以 exit 1 结束。
跑之前建议 TMPDIR=$PWD/.tmp（本机沙箱对系统临时目录与文件删除有限制）。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tarfile
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


def _vpath_basename():
    # 行内重命名的预填名：列表里还没有这一项时按 vpath 末段取
    assert vpath.basename(vpath.encode_path("/a/b/报告 2024.pdf")) == "报告 2024.pdf"
    assert vpath.basename(vpath.encode_path("/a/b/dir/")) == "dir"
    assert vpath.basename(vpath.encode_path("/")) == ""
    assert vpath.basename(vpath.encode_archive("/tmp/x.zip", "sub/dir")) == "dir"
    assert vpath.basename("") == ""


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

        # 目录自动刷新（M2 开关）：默认开；老状态文件没这个字段时不能被升级悄悄关掉；
        # 手改成 "true" 这种脏值回退默认（不真值转换，见 _clean_flag）
        fresh = state.load(os.path.join(home, "absent.json"), home)
        assert fresh["autoRefresh"] is True, fresh["autoRefresh"]
        import json as _json
        for raw, want in (({"version": state.VERSION}, True),
                          ({"version": state.VERSION, "autoRefresh": "true"}, True),
                          ({"version": state.VERSION, "autoRefresh": False}, False),
                          ({"version": state.VERSION, "autoRefresh": True}, True)):
            with open(cfg, "w", encoding="utf-8") as f:
                f.write(_json.dumps(raw))
            got = state.load(cfg, home)["autoRefresh"]
            assert got is want, (raw, got, want)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _layout_ratio():
    """等比布局的纯数值契约，与 qml/FileManagerPage.qml / FmPanel.qml 一一对应。

    qmllint 查不出「两栏不等宽」「列头溢出」这类运行时几何问题，
    所以把公式在这里钉死；QML 侧另用 offscreen 探针实测四档窗口宽度。
    """
    base = state.BASE_WIDTH
    lo, hi = state.WIDTH_LOW, state.WIDTH_HIGH
    assert base == 966 and lo == 290 and hi == 676
    assert state.clamp_width(0) == lo and state.clamp_width(9999) == hi
    assert state.DEFAULT_LEFT_WIDTH / base == 0.5

    # (窗口宽, 收藏栏宽, 面板宽, 显示类型列, 显示时间列, 名称列宽)
    cases = ((1040, 132, 311, False, False, 215),
             (1240, 168, 393, False, True, 197),
             (1440, 168, 493, True, True, 233),
             (1920, 168, 733, True, True, 473))
    for win, fav_w, panel_w, show_kind, show_time, name_w in cases:
        w_page = win - 214 - 40
        assert w_page == win - 254
        assert (132 if w_page < 900 else 168) == fav_w
        r_avail = w_page - fav_w - 12 - 20
        ratio = max(lo / base, min(hi / base, state.DEFAULT_LEFT_WIDTH / base))
        lw = round(ratio * r_avail)
        assert lw == r_avail - lw, (win, lw, r_avail - lw)   # 严格等宽
        assert lw == panel_w, (win, lw, panel_w)
        # 拖到比例两端时，较窄的一栏仍不能小于 QML 的 minimumWidth
        for raw in (lo, hi):
            rr = max(lo / base, min(hi / base, raw / base))
            a = round(rr * r_avail)
            assert min(a, r_avail - a) >= state.PANEL_MIN_WIDTH, (win, raw, a)
        # 列宽：名称列吃掉剩余宽度，隐藏的类型/时间信息仍在选中项信息条
        content = panel_w - 2 * 14
        visible = 2 + int(show_kind) + int(show_time)
        reserved = 64 + (60 if show_kind else 0) + (96 if show_time else 0) \
                   + 4 * (visible - 1)
        assert max(80, content - reserved) == name_w, (win, content, reserved)


def _favorites_state():
    """收藏项往返 / 旧配置兼容 / 僵尸路径保留 / 坏值丢弃。"""
    from . import locations
    home = tempfile.mkdtemp(prefix="fc_selfcheck_fav_")
    try:
        cfg = os.path.join(home, "filemanager_state.json")
        home_vp = vpath.encode_path(home)
        chinese = vpath.encode_path(os.path.join(home, "小说 集"))
        zombie = "file:///no/such/zombie/dir"

        data = state.default_state(home)
        data["leftWidth"] = 575
        data["favorites"] = [home_vp, chinese, chinese, zombie,
                             "bogus://x", 123, None, ""]
        assert state.save(cfg, data) is True
        loaded = state.load(cfg, home)
        assert loaded["version"] == 1
        assert loaded["leftWidth"] == 575
        assert loaded["favorites"][0] == home_vp
        assert loaded["favorites"].count(chinese) == 1, loaded["favorites"]
        assert zombie in loaded["favorites"], "僵尸项不能静默删"
        assert len(loaded["favorites"]) == 3, loaded["favorites"]
        assert not os.path.exists(cfg + ".tmp"), "原子写不应留 .tmp"

        # 旧配置（没有 favorites 字段）必须照常读出 cwd / 宽度
        old = state.default_state(home)
        old.pop("favorites")
        assert state.save(cfg, old) is True
        back = state.load(cfg, home)
        assert back["favorites"] == [] and back["leftWidth"] == old["leftWidth"]
        # 旧配置也没有 previewWindow：默认「没挪过」，交给 QML 定位
        assert back["previewWindow"]["placed"] is False, back["previewWindow"]

        # 预览窗几何：负坐标（副屏在左）必须原样保留，尺寸越界要夹紧
        geo = state.default_state(home)
        geo["previewWindow"] = {"ver": state.PREVIEW_GEOM_VERSION, "placed": True,
                                "x": -1580, "y": 120, "w": 99999, "h": 10}
        assert state.save(cfg, geo) is True
        pg = state.load(cfg, home)["previewWindow"]
        assert pg["placed"] is True and pg["x"] == -1580 and pg["y"] == 120, pg
        assert pg["w"] == state.PREVIEW_MAX_W, pg
        assert pg["h"] == state.PREVIEW_MIN_H, pg
        # 半截数据（只给 placed 没有 x/y）不能认成「挪过」，否则窗口会掉到 0,0
        broken = state._clean_preview_window({"ver": state.PREVIEW_GEOM_VERSION,
                                              "placed": True})
        assert broken["placed"] is False, broken
        # 旧版（760×580 固定小窗，没有 ver）必须作废，回到半屏默认尺寸
        legacy = state._clean_preview_window({"placed": True, "x": 640, "y": 200,
                                              "w": 760, "h": 580})
        assert legacy["placed"] is False, legacy
        assert (legacy["w"], legacy["h"]) == (state.PREVIEW_DEFAULT_W,
                                              state.PREVIEW_DEFAULT_H), legacy

        # 预览偏好：两个独立档位（图片 / PDF），默认值不同、互不影响
        assert back["previewPrefs"] == {
            "pdfQuality": state.PDF_QUALITY_DEFAULT,
            "imageQuality": state.IMAGE_QUALITY_DEFAULT,
        }, back["previewPrefs"]
        prefs = state.default_state(home)
        prefs["previewPrefs"] = {"pdfQuality": 2.0, "imageQuality": 1.5}
        assert state.save(cfg, prefs) is True
        loaded = state.load(cfg, home)["previewPrefs"]
        assert (loaded["pdfQuality"], loaded["imageQuality"]) == (2.0, 1.5), loaded
        # 只写一项时另一项回默认（后端 _set_quality 负责先合并再清洗，这里是裸清洗的行为）
        assert state._clean_preview_prefs({"pdfQuality": 1.0})["imageQuality"] == \
            state.IMAGE_QUALITY_DEFAULT
        for bogus in (1.34, "高", None, 99, -1):
            cleaned = state._clean_preview_prefs({"pdfQuality": bogus,
                                                  "imageQuality": bogus})
            assert cleaned["pdfQuality"] == state.PDF_QUALITY_DEFAULT, (bogus, cleaned)
            assert cleaned["imageQuality"] == state.IMAGE_QUALITY_DEFAULT, (bogus, cleaned)
        assert state._clean_preview_prefs("nonsense")["pdfQuality"] == \
            state.PDF_QUALITY_DEFAULT

        # ---- 全局「显示隐藏文件」：顶层唯一真源 + 老配置按面板迁移 ----
        assert back["showHidden"] is False, back["showHidden"]
        hidden = state.default_state(home)
        hidden["showHidden"] = True
        assert state.save(cfg, hidden) is True
        assert state.load(cfg, home)["showHidden"] is True
        # 只认真正的 bool：手改 JSON 写成 "false" / 1 / null 一律回默认（False）
        for bogus in ("false", "true", 1, 0, None, [], {}):
            assert state._clean_flag(bogus) is False, bogus
        assert state._clean_flag(True) is True
        # 老文件：没有顶层字段，从面板遗留值迁移（任一为开即开）
        legacy_hidden = state.default_state(home)
        legacy_hidden.pop("showHidden")
        legacy_hidden["panels"]["right"]["showHidden"] = True
        assert state.save(cfg, legacy_hidden) is True
        migrated = state.load(cfg, home)
        assert migrated["showHidden"] is True, migrated["showHidden"]
        # 迁移后面板里的副本也统一成全局值，写回去不会再分叉
        assert migrated["panels"]["left"]["showHidden"] is True
        # 顶层有值时以顶层为准（面板里的旧值不再参与）
        override = state.default_state(home)
        override["showHidden"] = False
        override["panels"]["left"]["showHidden"] = True
        assert state.save(cfg, override) is True
        assert state.load(cfg, home)["showHidden"] is False

        # ---- 工作时段：解析 / 清洗 / 顺序约束 ----
        assert state.parse_hhmm("9:00", -1) == 540
        assert state.parse_hhmm("09:00", -1) == 540
        assert state.parse_hhmm("18:00", -1) == 1080
        assert state.parse_hhmm(" 18:30 ", -1) == 1110
        assert state.parse_hhmm("18点", -1) == 1080
        assert state.parse_hhmm("1830", -1) == 1110
        assert state.parse_hhmm("900", -1) == 540
        for bogus in ("", "abc", "25:00", "12:75", "10:xx", None, [], -5, 1440):
            assert state.parse_hhmm(bogus, -1) == -1, bogus
        assert state.format_hhmm(720) == "12:00"
        assert state.format_hhmm(1110) == "18:30"
        assert state.format_hhmm(-10) == "00:00"
        assert state._clean_work(None) == {"startMin": state.WORK_START_DEFAULT,
                                           "endMin": state.WORK_END_DEFAULT}
        assert state._clean_work({"startMin": 540, "endMin": 1080}) == \
            {"startMin": 540, "endMin": 1080}
        # 反序 / 相等：整组回默认，绝不留负数倒计时；越界项单独回默认，另一项保留
        for bad in ({"startMin": 1200, "endMin": 600},
                    {"startMin": 600, "endMin": 600},
                    {"startMin": -1, "endMin": 1080}):
            assert state._clean_work(bad) == {"startMin": state.WORK_START_DEFAULT,
                                              "endMin": state.WORK_END_DEFAULT}, bad
        assert state._clean_work({"startMin": 540, "endMin": 5000}) == {
            "startMin": 540, "endMin": state.WORK_END_DEFAULT}
        # 时段往返
        work = state.default_state(home)
        work["workSchedule"] = {"startMin": 510, "endMin": 1140}
        assert state.save(cfg, work) is True
        assert state.load(cfg, home)["workSchedule"] == {"startMin": 510,
                                                         "endMin": 1140}

        # 常用位置：结构固定、路径真实存在
        items = locations.standard_locations(os.path.expanduser("~"))
        assert items and items[0]["name"] == "主目录"
        for it in items:
            assert set(it) == {"name", "vpath"}
            assert it["vpath"].startswith("file://")
            assert os.path.isdir(vpath.to_local(it["vpath"]))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _registry_and_preview():
    from .sources import UnknownScheme, resolve
    bootstrap()
    assert resolve("file:///tmp").scheme == "file"
    # M2：压缩包源已注册，同一个 vpath 能解析到 ArchiveSource
    assert resolve("zip+file:///a.zip!/").scheme == vpath.ARCHIVE
    # M1b 五种 + M2 压缩包 + M2 Office，共 7 个，按 priority 降序（兜底永远最后）
    assert len(preview_base.REGISTRY) == 7, preview_base.REGISTRY
    prios = [p.priority for p in preview_base.REGISTRY]
    assert prios == sorted(prios, reverse=True), prios
    assert prios[-1] == 0
    # Office 必须夹在 PDF(35) 和文本(30) 之间：高了会抢 PDF，低了会被文本抢走 docx
    from .preview.office import OfficePreviewer
    assert OfficePreviewer.priority == 33
    spec = preview_base.PreviewSpec(kind="image", title="a.jpg")
    assert spec.kind == "image"


def _preview_pipeline():
    """两阶段协议：prepare 只读元信息、view 回纯数据、四种分发与降级。"""
    import struct
    from .preview import base as pb
    from .preview import probe
    from .sources.local import LocalSource

    bootstrap()
    tmp = tempfile.mkdtemp(prefix="fc_selfcheck_preview_")
    src = LocalSource()

    def put(name, data):
        path = os.path.join(tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def entry_of(name, forced_size=None):
        path = os.path.join(tmp, name)
        size = forced_size if forced_size is not None else os.stat(path).st_size
        return make_entry(name, vpath.encode_path(path), False, size, 1700000000)

    def plain(v):
        assert type(v) in (str, int, float, bool, dict, list), (type(v), v)
        if isinstance(v, dict):
            for x in v.values():
                plain(x)
        elif isinstance(v, list):
            for x in v:
                plain(x)

    try:
        # 最小 PNG 头：signature + IHDR(16x9)
        put("pic.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", 16, 9) + b"\x08\x06\x00\x00\x00" + b"\x00" * 8)
        put("note.txt", "第一行\n中文内容\n".encode("utf-8"))
        put("gb.txt", "简体中文\n".encode("gb18030"))
        put("bin.dat", b"\x00\x01\x02\xff" * 4)
        put("doc.pdf", b"%PDF-1.4\n")
        sub = os.path.join(tmp, "子目录")
        os.mkdir(sub)
        for n in ("a.txt", "b.txt"):
            put(os.path.join("子目录", n), b"x")

        # prepare 阶段只返回元信息（不读文件内容，不需要文件真的可读）
        pic = entry_of("pic.png")
        picker = pb.pick(pic)
        assert type(picker).__name__ == "ImagePreviewer"
        spec = picker.prepare(pic)
        assert spec.kind == "image" and spec.title == "pic.png"
        assert set(spec.meta) >= {"kindLabel", "sizeText", "timeText", "display"}
        payload = picker.view(pic, pb.PreviewContext())
        assert payload.kind == "image"
        assert payload.data["width"] == 16 and payload.data["height"] == 9
        assert payload.data["format"] == "PNG"
        assert payload.data["source"] == pic.vpath and not payload.data["tooLarge"]
        plain(payload.data)

        # 超大图：view 必须直接放弃解码，绝不读文件
        huge = entry_of("pic.png", pb.MAX_IMAGE_BYTES + 1)
        p = pb.pick(huge).view(huge, pb.PreviewContext())
        assert p.data["tooLarge"] and "跳过" in p.data["error"]

        # 文本：UTF-8 / GB18030 / 二进制降级 / 截断
        e_txt = entry_of("note.txt")
        p = pb.pick(e_txt)
        assert type(p).__name__ == "TextPreviewer"
        assert p.prepare(e_txt).kind == "text"
        r = p.view(e_txt, pb.PreviewContext())
        assert r.kind == "text" and "中文内容" in r.data["text"]
        assert r.data["encoding"] in ("utf-8", "utf-8-sig") and r.data["lines"] == 2
        plain(r.data)
        r = pb.pick(entry_of("gb.txt")).view(entry_of("gb.txt"), pb.PreviewContext())
        assert "简体中文" in r.data["text"], r.data
        assert r.data["encoding"] == "gb18030", r.data["encoding"]
        r = pb.pick(entry_of("bin.dat")).view(entry_of("bin.dat"), pb.PreviewContext())
        assert r.kind == "fallback", r.data       # 二进制不显示乱码
        long_text = put("long.txt", ("行\n" * 100).encode("utf-8"))
        e_long = entry_of("long.txt")
        r = pb.pick(e_long).view(e_long, pb.PreviewContext(max_text_bytes=32))
        assert r.data["truncated"] and r.data["lines"] <= 100

        # SVG 不看图、按源码走文本预览
        put("icon.svg", b"<svg></svg>")
        e_svg = entry_of("icon.svg")
        assert type(pb.pick(e_svg)).__name__ == "TextPreviewer"

        # 目录摘要：只扫一层、目录恒优先、条目数受限额
        dir_entry = src.entry(vpath.encode_path(sub))
        p = pb.pick(dir_entry)
        assert type(p).__name__ == "DirectoryPreviewer"
        assert p.prepare(dir_entry).kind == "directory"
        r = p.view(dir_entry, pb.PreviewContext(max_list_items=1))
        assert r.data["count"] == 2 and r.data["fileCount"] == 2
        assert len(r.data["items"]) == 1
        plain(r.data)
        token = CancelToken()
        token.cancel()
        assert p.view(dir_entry, pb.PreviewContext(cancel=token)).data["count"] <= 1

        # PDF：filecore 这半边只校验文件头 + 交出本地路径，栅格化在 Qt 层做
        e_pdf = entry_of("doc.pdf")
        p = pb.pick(e_pdf)
        assert type(p).__name__ == "PdfPreviewer"
        spec = p.prepare(e_pdf)
        assert spec.kind == "pdf" and spec.title == "doc.pdf"
        r = p.view(e_pdf, pb.PreviewContext())
        assert r.kind == "pdf" and not r.data["error"], r.data
        assert r.data["localPath"] == os.path.join(tmp, "doc.pdf"), r.data
        assert r.data["pages"] == [] and r.data["pageCount"] == 0
        plain(r.data)

        # 只改了后缀的假 PDF：必须被文件头拦下，不能交给 PDF 引擎
        put("fake.pdf", b"hello, i am not a pdf\n")
        r = pb.pick(entry_of("fake.pdf")).view(entry_of("fake.pdf"), pb.PreviewContext())
        assert "文件头" in r.data["error"], r.data

        # docx 从 M2 起由 OfficePreviewer 接管（不再是兜底卡）；坏包也得给提示
        put("plan.docx", b"not a real docx")
        e_doc = entry_of("plan.docx")
        assert type(pb.pick(e_doc)).__name__ == "OfficePreviewer", type(pb.pick(e_doc)).__name__
        r = pb.pick(e_doc).view(e_doc, pb.PreviewContext(office_layout=False))
        assert r.kind == "office" and r.data["hint"], r.data

        # 仍然没人管的类型才走兜底信息卡
        put("clip.mkv", b"\x1a\x45\xdf\xa3" + b"\x00" * 32)
        e_mkv = entry_of("clip.mkv")
        p = pb.pick(e_mkv)
        assert type(p).__name__ == "FallbackPreviewer", type(p).__name__
        r = p.view(e_mkv, pb.PreviewContext())
        assert r.kind == "fallback" and r.data["hint"], r.data
        assert pb.pick(None).__class__.__name__ == "FallbackPreviewer"

        # 图片头探测的其它格式
        put("a.gif", b"GIF89a" + struct.pack("<HH", 7, 11) + b"\x00" * 10)
        put("a.bmp", b"BM" + b"\x00" * 16 + struct.pack("<ii", 5, -9) + b"\x00" * 10)
        put("a.jpg", b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00"
            + b"\x00" * 9 + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
            + struct.pack(">HH", 21, 13) + b"\x00" * 9)
        assert probe.image_info(os.path.join(tmp, "a.gif"))["width"] == 7
        assert probe.image_info(os.path.join(tmp, "a.bmp"))["height"] == 9
        assert probe.image_info(os.path.join(tmp, "a.jpg"))["width"] == 13
        assert probe.image_info(os.path.join(tmp, "doc.pdf")) is None
        assert probe.image_info(os.path.join(tmp, "不存在.png")) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ops_naming():
    from .ops.naming import split_ext, unique_name, validate_name

    assert split_ext(".bashrc") == (".bashrc", "")
    assert split_ext("a.tar.gz") == ("a.tar", ".gz")
    assert split_ext("Makefile") == ("Makefile", "")
    assert split_ext("dir/") == ("dir/", "")
    assert validate_name("ok.txt") == ""
    assert validate_name("") and validate_name("a/b") and validate_name("..")

    tmp = tempfile.mkdtemp()
    try:
        open(os.path.join(tmp, "a.txt"), "w").close()
        assert unique_name(tmp, "a.txt") == "a (1).txt"
        open(os.path.join(tmp, "a (1).txt"), "w").close()
        assert unique_name(tmp, "a.txt") == "a (2).txt"
        assert unique_name(tmp, "b.txt") == "b.txt"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ops_transfer():
    from .ops.transfer import make_directory, rename_item, run_transfer

    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp, "src")
        os.makedirs(os.path.join(src, "sub"))
        with open(os.path.join(src, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(src, "sub", "b.txt"), "w") as f:
            f.write("x" * 3000)
        os.symlink(os.path.join(src, "a.txt"), os.path.join(src, "link"))
        os.utime(os.path.join(src, "a.txt"), (1_600_000_000, 1_600_000_000))

        dest = os.path.join(tmp, "dest")
        os.makedirs(dest)
        ticks = []
        r = run_transfer([vpath.encode_path(src)], vpath.encode_path(dest),
                         tick=lambda done, total: ticks.append((done, total)))
        assert r["ok"] == 1 and not r["failed"], r
        copied = os.path.join(dest, "src")
        assert os.path.exists(os.path.join(copied, "a.txt"))
        assert os.path.islink(os.path.join(copied, "link"))
        assert os.path.getsize(os.path.join(copied, "sub", "b.txt")) == 3000
        assert int(os.path.getmtime(os.path.join(copied, "a.txt"))) == 1_600_000_000
        assert ticks and ticks[-1][0] == ticks[-1][1] == 3005, ticks[-3:]

        # 同名自动重命名，不覆盖
        r2 = run_transfer([vpath.encode_path(src)], vpath.encode_path(dest))
        assert r2["ok"] == 1 and r2["renamed"] == 1, r2
        assert r2["paths"][0].endswith("src%20%281%29"), r2["paths"]

        # 移动：同一分区走 rename，源消失，落到 dest 顶层（那里没有同名项）
        r3 = run_transfer([vpath.encode_path(os.path.join(dest, "src", "a.txt"))],
                          vpath.encode_path(dest), move=True)
        assert r3["ok"] == 1, r3
        assert not os.path.exists(os.path.join(dest, "src", "a.txt"))
        assert os.path.exists(os.path.join(dest, "a.txt"))

        # 不能放进它自己里面
        r4 = run_transfer([vpath.encode_path(copied)], vpath.encode_path(copied))
        assert r4["ok"] == 0 and "自己" in r4["failed"][0]["error"], r4

        # 取消：不留下半成品
        token = CancelToken()
        token.cancel()
        dest2 = os.path.join(tmp, "dest2")
        os.makedirs(dest2)
        r5 = run_transfer([vpath.encode_path(src)], vpath.encode_path(dest2), cancel=token)
        assert r5["cancelled"] is True and r5["ok"] == 0, r5
        assert os.listdir(dest2) == [], os.listdir(dest2)

        # 改名 / 新建文件夹
        open(os.path.join(dest, "b.txt"), "w").close()
        new_vp, err = rename_item(vpath.encode_path(os.path.join(dest, "a.txt")), "b.txt")
        assert err and new_vp == "", "同名改名必须被拒"
        new_vp, err = rename_item(vpath.encode_path(os.path.join(dest, "a.txt")), "c.txt")
        assert not err and os.path.exists(os.path.join(dest, "c.txt")), err
        nv, err = make_directory(vpath.encode_path(dest), "新建文件夹")
        assert not err and os.path.isdir(vpath.to_local(nv)), err
        nv2, err2 = make_directory(vpath.encode_path(dest), "新建文件夹")
        assert not err2 and vpath.to_local(nv2) != vpath.to_local(nv), "同名新建必须加序号"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _ops_trash():
    from .ops import trash

    tmp = tempfile.mkdtemp()
    old = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "xdg")
    try:
        f = os.path.join(tmp, "x.txt")
        with open(f, "w") as fh:
            fh.write("bye")
        res = trash.trash_path(f)
        assert res["ok"] and res["name"] == "x.txt", res
        assert not os.path.exists(f), "移到回收站后源文件还在"
        info_path = os.path.join(tmp, "xdg", "Trash", "info", "x.txt.trashinfo")
        text = open(info_path, encoding="utf-8").read()
        assert text.startswith("[Trash Info]"), text
        assert "Path=" in text and "DeletionDate=" in text
        assert "x.txt" in text and text.count("Path=") == 1
        assert os.path.exists(os.path.join(tmp, "xdg", "Trash", "files", "x.txt"))

        with open(f, "w") as fh:
            fh.write("again")
        res2 = trash.trash_path(f)
        assert res2["ok"] and res2["name"] == "x (1).txt", res2

        assert trash.trash_path(os.path.join(tmp, "不存在")).get("ok") is False
        d = os.path.join(tmp, "dir")
        os.makedirs(d)
        open(os.path.join(d, "in.txt"), "w").close()
        res3 = trash.trash_path(d)
        assert res3["ok"] and os.path.isdir(os.path.join(tmp, "xdg", "Trash", "files", "dir"))
    finally:
        if old is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def _ops_clipboard():
    from .ops import clipboard

    tmp = tempfile.mkdtemp()
    try:
        f = os.path.join(tmp, "中文 名.txt")
        with open(f, "w") as fh:
            fh.write("x")
        blob = clipboard.encode("cut", [vpath.encode_path(f)])
        assert set(blob) == {clipboard.MIME_URI, clipboard.MIME_GNOME}, set(blob)
        assert blob[clipboard.MIME_GNOME].startswith(b"cut"), blob[clipboard.MIME_GNOME]
        assert b"%20" in blob[clipboard.MIME_GNOME]

        parsed = clipboard.parse(blob)
        assert parsed["operation"] == "cut", parsed
        assert parsed["vpaths"] == [vpath.encode_path(f)], parsed
        assert parsed["ignored"] == 0

        # 只给 uri-list 时必须退化成 copy（没有剪切语义来源）
        assert clipboard.parse({clipboard.MIME_URI: blob[clipboard.MIME_URI]})["operation"] == "copy"
        # http / 不存在的路径要忽略，而不是当成待粘贴项
        assert clipboard.parse({clipboard.MIME_URI: b"http://a/b\r\n"})["vpaths"] == []
        assert clipboard.parse({clipboard.MIME_URI: b"file:///nope/nope\r\n"})["ignored"] == 1
        # 空剪贴板
        assert clipboard.parse({})["vpaths"] == []
        assert clipboard.encode("copy", []) == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _archives_format():
    from . import archives

    assert archives.format_of("a.zip") == "zip"
    assert archives.format_of("A.JAR") == "zip"
    assert archives.format_of("a.tar") == "tar"
    # .tar.gz 必须先于 .gz 命中，否则会被当成裸 gzip
    assert archives.format_of("a.tar.gz") == "tar"
    assert archives.format_of("a.tar.bz2") == "tar"
    assert archives.format_of("a.txz") == "tar"
    assert archives.format_of("a.rar") == "rar"
    assert archives.format_of("a.7z") == "7z"
    assert archives.format_of("a.gz") == "gzip"
    assert archives.format_of("a.txt") == ""
    assert archives.format_of("") == ""
    assert archives.stem_of("a.tar.gz") == "a"
    assert archives.stem_of("x.zip") == "x"
    assert archives.stem_of("noext") == "noext"
    assert archives.default_zip_name(["/tmp/报告.docx"]) == "报告"
    assert archives.default_zip_name(["/tmp/a", "/tmp/b"]) == "压缩包"


def _archives_roundtrip():
    """压缩 → 列目录 → 解压 → 不覆盖，一条链走完。"""
    import gzip
    from . import archives

    tmp = tempfile.mkdtemp(prefix="fc_selfcheck_arc_")
    try:
        src = os.path.join(tmp, "素材")
        os.makedirs(os.path.join(src, "子目录"))
        with open(os.path.join(src, "说明.txt"), "w", encoding="utf-8") as f:
            f.write("压缩包里的说明\n第二行\n")
        with open(os.path.join(src, "子目录", "深.bin"), "wb") as f:
            f.write(b"x" * 5000)
        os.symlink(os.path.join(src, "说明.txt"), os.path.join(src, "链接"))

        z = os.path.join(tmp, "包.zip")
        # 符号链接跳过、不写进包（与解压侧的跳过策略对称）
        r = archives.compress_zip([src], z)
        assert r["ok"] == 2 and r["skipped"] == 1 and not r["failed"], r

        base = vpath.encode_archive(z, "")
        items, err, trunc = archives.list_dir(z, "", base)
        assert err is None and not trunc
        assert [e.name for e in items] == ["素材"], [e.name for e in items]
        assert items[0].is_dir and items[0].vpath.startswith("zip+file://")

        sub = vpath.encode_archive(z, "素材")
        kids = {e.name: e for e in archives.list_dir(z, "素材", sub)[0]}
        assert set(kids) == {"子目录", "说明.txt"}, set(kids)
        assert kids["说明.txt"].size == len("压缩包里的说明\n第二行\n".encode("utf-8"))
        assert kids["说明.txt"].kind == "text" and kids["子目录"].is_dir

        out = os.path.join(tmp, "out")
        os.makedirs(out)
        r1 = archives.extract(z, out)
        assert r1["ok"] == 2 and r1["exists"] == 0, r1
        assert open(os.path.join(out, "素材", "说明.txt"), encoding="utf-8").read().startswith("压缩包里的说明")

        # 同名不覆盖：本地改过的文件必须原样保留
        with open(os.path.join(out, "素材", "说明.txt"), "w", encoding="utf-8") as f:
            f.write("本地改过")
        r2 = archives.extract(z, out)
        assert r2["ok"] == 0 and r2["exists"] == 2, r2
        assert open(os.path.join(out, "素材", "说明.txt"), encoding="utf-8").read() == "本地改过"

        # 独立文件夹：名字取包名，撞名加序号
        r3 = archives.extract(z, out, into_folder=True)
        assert r3["ok"] == 2 and os.path.basename(r3["dir"]) == "包", r3
        r4 = archives.extract(z, out, into_folder=True)
        assert os.path.basename(r4["dir"]) == "包 (1)", r4

        # 只解选中条目
        r5 = archives.extract(z, out, names=["素材/说明.txt"])
        assert r5["ok"] == 0 and r5["exists"] == 1, r5

        # tar.gz：同一套语义
        tgz = os.path.join(tmp, "包.tar.gz")
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(src, arcname="素材")
        t_items = archives.list_dir(tgz, "", vpath.encode_archive(tgz, ""))[0]
        assert [e.name for e in t_items] == ["素材"], [e.name for e in t_items]
        o2 = os.path.join(tmp, "o2")
        os.makedirs(o2)
        assert archives.extract(tgz, o2)["ok"] == 2
        assert os.path.getsize(os.path.join(o2, "素材", "子目录", "深.bin")) == 5000

        # 裸 gzip：单成员，名字去后缀
        gz = os.path.join(tmp, "note.txt.gz")
        with gzip.open(gz, "wb") as f:
            f.write("gz 内容".encode("utf-8"))
        assert archives.format_of(gz) == "gzip"
        assert [e.name for e in archives.list_dir(gz, "", vpath.encode_archive(gz, ""))[0]] == ["note.txt"]
        o3 = os.path.join(tmp, "o3")
        os.makedirs(o3)
        assert archives.extract(gz, o3)["ok"] == 1
        assert open(os.path.join(o3, "note.txt"), encoding="utf-8").read() == "gz 内容"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _archives_safety():
    """zip slip / 损坏包 / 加密包：都必须「不写出去、给出中文错误」。"""
    import zipfile as zf_mod
    from . import archives

    tmp = tempfile.mkdtemp(prefix="fc_selfcheck_arcbad_")
    try:
        evil = os.path.join(tmp, "evil.zip")
        with zf_mod.ZipFile(evil, "w") as zf:
            zf.writestr("../../逃出去.txt", "bad")
            zf.writestr("/绝对路径.txt", "bad")
            zf.writestr("正常.txt", "ok")
        out = os.path.join(tmp, "out")
        os.makedirs(out)
        r = archives.extract(evil, out)
        assert not os.path.exists(os.path.join(tmp, "逃出去.txt")), "zip slip 没挡住"
        assert r["ok"] == 2 and r["skipped"] == 1, r       # ../ 被丢，绝对路径被剥成相对
        assert not os.path.exists(os.path.join(out, "..", "..", "逃出去.txt"))

        bad = os.path.join(tmp, "bad.zip")
        with open(bad, "wb") as f:
            f.write(b"not a zip")
        items, err, _tr = archives.list_dir(bad, "", vpath.encode_archive(bad, ""))
        assert items == [] and err and "损坏" in err, (items, err)
        summary = archives.summary(bad)
        assert summary["error"] and summary["count"] == 0

        # 不认识的扩展名：不假装能处理
        plain = os.path.join(tmp, "a.txt")
        with open(plain, "w") as f:
            f.write("x")
        assert archives.format_of(plain) == ""
        assert archives.summary(plain)["error"]

        # 压缩只写进 .wspart 再改名：失败（没有可压内容）不能留下垃圾
        empty = archives.compress_zip([], os.path.join(tmp, "空.zip"))
        assert empty["ok"] == 0 and empty["failed"], empty
        assert not any(n.endswith(".wspart") for n in os.listdir(tmp)), os.listdir(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _archives_text_and_source():
    """包内文本预览 + ArchiveSource 的目录语义（这两块是「压缩包当目录逛」的地基）。"""
    from . import archives
    from .preview import base as pb
    from .sources import SOURCE_REGISTRY, resolve

    bootstrap()
    assert vpath.ARCHIVE in SOURCE_REGISTRY, list(SOURCE_REGISTRY)

    tmp = tempfile.mkdtemp(prefix="fc_selfcheck_arcsrc_")
    try:
        src = os.path.join(tmp, "资料")
        os.makedirs(os.path.join(src, "子"))
        with open(os.path.join(src, "说明.md"), "w", encoding="utf-8") as f:
            f.write("# 标题\n正文\n")
        with open(os.path.join(src, "子", "图.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        z = os.path.join(tmp, "资料.zip")
        archives.compress_zip([src], z)

        root = vpath.encode_archive(z, "")
        source = resolve(root)
        assert type(source).__name__ == "ArchiveSource"
        assert source.exists(root) and source.is_dir(root)
        entries = source.list_dir(root, False)[0]
        assert [e.name for e in entries] == ["资料"]
        inner_dir = vpath.join(root, "资料")
        assert source.is_dir(inner_dir) and source.exists(inner_dir)
        md = vpath.join(inner_dir, "说明.md")
        assert source.exists(md) and not source.is_dir(md)
        e = source.entry(md)
        assert e.name == "说明.md" and e.size > 0 and e.kind in ("text", "code")

        caps = source.capabilities(root)
        assert caps["archiveView"] is True and caps["canWrite"] is False
        assert caps["canExtract"] is True and caps["canCompress"] is False

        # 地址栏可读形式能还原回 vpath（否则 Ctrl+L 回车就报「路径不存在」）
        assert vpath.from_display(vpath.display(md)) == md
        assert vpath.from_display("相对/路径.zip!/x") == ""

        # 包内文本：直接从包里读，不解压
        text = archives.read_text_member(md, 64 * 1024, 4000)
        assert text["text"].startswith("# 标题"), text
        payload = pb.pick(source.entry(md)).view(source.entry(md),
                                                  pb.PreviewContext())
        assert payload.kind == "text" and "标题" in payload.data["text"]

        # 包内图片：没有物理路径，必须落到兜底卡并说清楚要先解压
        png = vpath.join(inner_dir, "图.png")
        picker = pb.pick(source.entry(png))
        assert type(picker).__name__ == "FallbackPreviewer", type(picker).__name__
        assert "解压" in picker.view(source.entry(png), pb.PreviewContext()).data["hint"]

        # 压缩包本身的浮窗摘要
        zp = vpath.encode_path(z)
        zentry = resolve(zp).entry(zp)
        summary = pb.pick(zentry).view(zentry, pb.PreviewContext())
        assert summary.kind == "archive"
        assert summary.data["count"] == 4 and summary.data["fileCount"] == 2, summary.data
        assert summary.data["items"] and summary.data["items"][0]["name"] == "资料"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_S_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_A_NS = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def _make_docx(path, n_para=40):
    """造一份最小 docx：n_para 个段落 + 一张 2×2 表 + core.xml 元信息 + 一张图。"""
    import zipfile
    paras = "".join(
        f"<w:p><w:r><w:t>段落 {i}</w:t></w:r></w:p>" for i in range(1, n_para + 1))
    rows = "".join(
        "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>格{r}{c}</w:t></w:r></w:p></w:tc>"
                           for c in range(2)) + "</w:tr>"
        for r in range(2))
    doc = (f'<?xml version="1.0" encoding="UTF-8"?><w:document {_W_NS}>'
           f"<w:body>{paras}<w:tbl>{rows}</w:tbl></w:body></w:document>")
    core = ('<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.'
            'openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>季度报告</dc:title><dc:creator>Danny</dc:creator>"
            "</cp:coreProperties>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", doc)
        z.writestr("docProps/core.xml", core)
        z.writestr("word/media/a.png", b"\x89PNG")


def _office_text():
    """docx / xlsx / pptx 的纯 Python 抽文字：正文、表格、统计、元信息。"""
    import zipfile

    from . import office

    tmp = tempfile.mkdtemp(prefix="ws-doc-")
    try:
        d = os.path.join(tmp, "报告.docx")
        _make_docx(d)
        r = office.read_text(d)
        assert "段落 1" in r["text"] and "段落 40" in r["text"], r["text"][:80]
        assert "\t" in r["text"], "表格行应当用制表符分隔"
        assert r["stats"]["paragraphs"] >= 40, r["stats"]
        assert r["stats"]["tables"] == 1, r["stats"]
        assert r["stats"]["title"] == "季度报告", r["stats"]
        assert r["stats"]["author"] == "Danny", r["stats"]
        assert r["encrypted"] is False

        x = os.path.join(tmp, "表.xlsx")
        shared = "".join(f"<si><t>{t}</t></si>" for t in ("姓名", "部门", "张三"))
        rows = ('<row r="1"><c r="A1" t="s"><v>0</v></c>'
                '<c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>2</v></c>'
                '<c r="B2"><v>42</v></c></row>')
        with zipfile.ZipFile(x, "w") as z:
            z.writestr("xl/sharedStrings.xml",
                       f'<?xml version="1.0"?><sst {_S_NS} count="3">{shared}</sst>')
            z.writestr("xl/worksheets/sheet1.xml",
                       f'<?xml version="1.0"?><worksheet {_S_NS}>'
                       f"<sheetData>{rows}</sheetData></worksheet>")
        r = office.read_text(x)
        assert "姓名" in r["text"] and "张三" in r["text"], r["text"]
        assert "42" in r["text"], "数值单元格也要抽出来"
        assert r["stats"]["sheets"] == 1 and r["stats"]["rows"] == 2, r["stats"]

        p = os.path.join(tmp, "演示.pptx")
        with zipfile.ZipFile(p, "w") as z:
            for i in (1, 2):
                # 根元素别写 <p:sld> —— 没声明 p 前缀会直接 ParseError（unbound prefix），
                # 抽文字只认 <a:t>，根叫什么无所谓。
                z.writestr(f"ppt/slides/slide{i}.xml",
                           f'<?xml version="1.0"?><sld {_A_NS}><a:t>第 {i} 页</a:t></sld>')
        r = office.read_text(p)
        assert "第 1 页" in r["text"] and "第 2 页" in r["text"], r["text"]
        assert r["stats"]["slides"] == 2, r["stats"]

        assert office.format_label(d) == "Word 文档"
        assert office.is_office(d) and not office.is_office("a.txt")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _office_safety():
    """加密 / 内嵌实体 / 旧格式 / 不存在：一律给中文提示，绝不抛。"""
    import zipfile

    from . import office

    tmp = tempfile.mkdtemp(prefix="ws-doc-")
    saved = office._TOOL_ENV
    try:
        enc = os.path.join(tmp, "加密.docx")
        with open(enc, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        r = office.read_text(enc)
        assert r["encrypted"] is True, r
        assert "加密" in r["hint"], r["hint"]

        ent = os.path.join(tmp, "实体.docx")
        with zipfile.ZipFile(ent, "w") as z:
            z.writestr("word/document.xml",
                       '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaa">]>'
                       f"<w:document {_W_NS}><w:body/></w:document>")
        r = office.read_text(ent)
        assert "实体" in r["hint"], r["hint"]

        office._TOOL_ENV = {"soffice": "", "antiword": "", "catdoc": "", "unrtf": ""}
        old = os.path.join(tmp, "旧.doc")
        with open(old, "wb") as f:
            f.write(b"\xd0\xcf\x11\xe0" + b"\x00" * 128)
        r = office.read_text(old)
        assert "antiword" in r["hint"] or "catdoc" in r["hint"], r["hint"]

        r = office.read_text(os.path.join(tmp, "没有.docx"))
        assert r["text"] == "" and r["hint"], r
    finally:
        office._TOOL_ENV = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _office_previewer():
    """开关分叉 / 假 soffice 真转一次 / 缓存命中 / 失败回落文字。"""
    from . import office
    from .preview import base as pb
    from .sources import resolve

    tmp = tempfile.mkdtemp(prefix="ws-doc-")
    cache = os.path.join(tmp, "cache")
    saved_env = os.environ.get("WORKSPACE_CACHE")
    saved_tools = office._TOOL_ENV
    os.environ["WORKSPACE_CACHE"] = cache
    try:
        path = os.path.join(tmp, "报告.docx")
        _make_docx(path)
        bootstrap()
        vp = vpath.encode_path(path)
        entry = resolve(vp).entry(vp)
        pv = pb.pick(entry)
        assert type(pv).__name__ == "OfficePreviewer", type(pv).__name__

        # 开关关闭：不启动 LibreOffice，只给文字
        out = pv.view(entry, pb.PreviewContext(office_layout=False))
        assert out.kind == "office", out.kind
        assert "段落 1" in out.data["text"]
        assert "版面预览已关闭" in out.data["hint"], out.data["hint"]
        assert out.data["sourceKind"] == "office"
        assert any(row[0] == "段落" for row in out.data["detail"]), out.data["detail"]

        # 开关开着但没装：提示装什么，同时仍然给文字（不能白屏）
        office._TOOL_ENV = {"soffice": "", "antiword": "", "catdoc": "", "unrtf": ""}
        out = pv.view(entry, pb.PreviewContext(office_layout=True))
        assert out.kind == "office", out.kind
        assert "LibreOffice" in out.data["hint"], out.data["hint"]
        assert "段落 1" in out.data["text"]

        # 假 soffice：真 Popen 一次，顺带验命令行参数（profile 隔离在这里）
        fake = os.path.join(tmp, "fake-soffice")
        args_file = os.path.join(tmp, "args.txt")
        with open(fake, "w") as f:
            f.write(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "a = sys.argv[1:]\n"
                "out = a[a.index('--outdir') + 1]\n"
                "src = a[-1]\n"
                "open(" + repr(args_file) + ", 'w').write(chr(10).join(a))\n"
                "stem = os.path.splitext(os.path.basename(src))[0]\n"
                "open(os.path.join(out, stem + '.pdf'), 'wb').write(b'%PDF-1.4' + b'x' * 200)\n"
            )
        os.chmod(fake, 0o755)
        office._TOOL_ENV = {"soffice": fake, "antiword": "", "catdoc": "", "unrtf": ""}
        office.clear_cache()

        out = pv.view(entry, pb.PreviewContext(office_layout=True))
        assert out.kind == "pdf", out.kind
        pdf = out.data["localPath"]
        assert os.path.isfile(pdf) and os.path.getsize(pdf) > 0, pdf
        assert out.data["sourceKind"] == "office"
        assert cache in pdf, pdf
        args = open(args_file, encoding="utf-8").read()
        assert "--headless" in args and "--norestore" in args, args
        assert "--convert-to" in args and "pdf" in args, args
        assert "-env:UserInstallation=" in args, "必须隔离 profile：" + args
        # profile 必须落在自己的缓存目录里：用默认的 ~/.config/libreoffice 时，
        # 用户开着 Writer 就会因为 profile 被锁而转换失败。
        profile = args.split("-env:UserInstallation=")[1].split("\n")[0]
        assert cache in profile, "profile 应在自己的缓存目录：" + profile

        # 缓存命中：把假程序删了也照样出 PDF —— 证明没再起进程
        os.unlink(fake)
        out2 = pv.view(entry, pb.PreviewContext(office_layout=True))
        assert out2.kind == "pdf", out2.kind
        assert out2.data["localPath"] == pdf, out2.data["localPath"]

        # 文件改过（mtime 变）→ 缓存必须失效
        st = os.stat(path)
        os.utime(path, (st.st_atime + 5, st.st_mtime + 5))
        with open(fake + "2", "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
        os.chmod(fake + "2", 0o755)
        office._TOOL_ENV = {"soffice": fake + "2", "antiword": "", "catdoc": "", "unrtf": ""}
        out3 = pv.view(entry, pb.PreviewContext(office_layout=True))
        assert out3.kind == "office", out3.kind
        assert "段落 1" in out3.data["text"], "转换失败必须回落到文字，不能白屏"
    finally:
        office._TOOL_ENV = saved_tools
        if saved_env is None:
            os.environ.pop("WORKSPACE_CACHE", None)
        else:
            os.environ["WORKSPACE_CACHE"] = saved_env
        shutil.rmtree(tmp, ignore_errors=True)


def _office_state():
    """officeLayout 的默认值与清洗：手改 JSON 写成 "true" 要回默认而不是静默翻真。"""
    tmp = tempfile.mkdtemp(prefix="ws-doc-")
    try:
        cfg = os.path.join(tmp, "s.json")
        assert state.default_state(tmp)["officeLayout"] is True
        for raw, want in ((False, False), (True, True), ("true", True), (None, True)):
            data = {"version": state.VERSION}
            if raw is not None:
                data["officeLayout"] = raw
            state.save(cfg, data)
            got = state.load(cfg, tmp)["officeLayout"]
            assert got is want, (raw, got, want)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _zero_qt():
    assert "PySide6" not in sys.modules
    assert "PyQt5" not in sys.modules

def main() -> int:
    for label, fn in [
        ("vpath round-trip", _vpath_roundtrip),
        ("vpath 编码 / 中文 / 空格 / # / %", _vpath_encoding),
        ("vpath 压缩包跨容器语义", _vpath_archive),
        ("vpath 隐藏文件判断", _vpath_hidden),
        ("vpath 末段取名（改名预填兜底）", _vpath_basename),
        ("natural_key 自然序", _natural_key),
        ("sort_entries 目录恒优先（含降序）", _sort_entries),
        ("visible_entries 过滤", _visible_entries),
        ("kind_of / KIND_LABEL", _kind_of),
        ("human_size / human_time 边界", _human),
        ("Entry.to_dict 10 role 无 None", _entry_dict),
        ("scan_dir 批量项 / 隐藏 / 断链 / 取消", _scan_dir),
        ("scan_dir 错误文案不抛异常", _scan_errors),
        ("state 读写 / 坏 JSON / 版本 / 路径回退", _state),
        ("等比布局数值 / 四档列宽契约", _layout_ratio),
        ("收藏项往返 / 旧配置兼容 / 常用位置 / 预览窗几何", _favorites_state),
        ("FileSource 注册表 / 四种预览器注册", _registry_and_preview),
        ("预览两阶段协议 / 文本编码 / 图片头 / 目录摘要", _preview_pipeline),
        ("ops 唯一命名 / 文件名校验", _ops_naming),
        ("ops 复制移动 / 同名重命名 / 取消不留残渣", _ops_transfer),
        ("ops 回收站规范 / 同名序号 / 目录", _ops_trash),
        ("ops 剪贴板 dual-MIME 往返 / cut 语义", _ops_clipboard),
        ("archives 格式识别 / 包名主干", _archives_format),
        ("archives 压缩→列目录→解压→不覆盖", _archives_roundtrip),
        ("archives zip slip / 损坏包 / 无残留", _archives_safety),
        ("archives 包内文本 / ArchiveSource / 摘要预览", _archives_text_and_source),
        ("office 抽文字 docx/xlsx/pptx", _office_text),
        ("office 加密 / 实体 / 旧格式 / 不存在", _office_safety),
        ("office 开关分叉 / 转换 / 缓存 / 回落", _office_previewer),
        ("office officeLayout 持久化", _office_state),
        ("零 Qt 依赖", _zero_qt),
    ]:
        check(label, fn)
    print(f"\n{'ALL OK' if _FAIL == 0 else 'FAILED'} ({_PASS} passed, {_FAIL} failed)")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
