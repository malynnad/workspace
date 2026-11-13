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

        # 预览偏好：默认档、白名单往返、手改 JSON 写的野值必须回退到默认档
        assert back["previewPrefs"]["pdfQuality"] == state.PDF_QUALITY_DEFAULT
        prefs = state.default_state(home)
        prefs["previewPrefs"] = {"pdfQuality": 2.0}
        assert state.save(cfg, prefs) is True
        assert state.load(cfg, home)["previewPrefs"]["pdfQuality"] == 2.0
        for bogus in (1.34, "高", None, 99, -1):
            cleaned = state._clean_preview_prefs({"pdfQuality": bogus})
            assert cleaned["pdfQuality"] == state.PDF_QUALITY_DEFAULT, (bogus, cleaned)
        assert state._clean_preview_prefs("nonsense")["pdfQuality"] == \
            state.PDF_QUALITY_DEFAULT

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
    try:
        resolve("zip+file:///a.zip!/")
    except UnknownScheme:
        pass
    else:
        raise AssertionError("M2 之前 zip+file 应当未注册")
    # M1b：五种内置预览器已注册，且按 priority 降序（兜底永远最后）
    assert len(preview_base.REGISTRY) == 5, preview_base.REGISTRY
    prios = [p.priority for p in preview_base.REGISTRY]
    assert prios == sorted(prios, reverse=True), prios
    assert prios[-1] == 0
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

        # 其余类型一律兜底信息卡
        put("plan.docx", b"not a real docx")
        e_doc = entry_of("plan.docx")
        p = pb.pick(e_doc)
        assert type(p).__name__ == "FallbackPreviewer"
        r = p.view(e_doc, pb.PreviewContext())
        assert r.kind == "fallback" and "办公文档" in r.data["hint"]
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
        ("等比布局数值 / 四档列宽契约", _layout_ratio),
        ("收藏项往返 / 旧配置兼容 / 常用位置 / 预览窗几何", _favorites_state),
        ("FileSource 注册表 / 四种预览器注册", _registry_and_preview),
        ("预览两阶段协议 / 文本编码 / 图片头 / 目录摘要", _preview_pipeline),
        ("零 Qt 依赖", _zero_qt),
    ]:
        check(label, fn)
    print(f"\n{'ALL OK' if _FAIL == 0 else 'FAILED'} ({_PASS} passed, {_FAIL} failed)")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
