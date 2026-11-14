"""文件条目数据类与类型识别。

字段与 QML 侧 FileListModel 的 10 个 role 一一对应（to_dict 用 camelCase）。
旧版来源：filemanager.py:521-573（扩展名集合）、:679-730（kind_of / KIND_LABEL）、
:594-637（human_size / human_time）。KIND_COLOR 是硬编码色值，不迁入。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
             ".heic", ".heif", ".svg", ".ico", ".avif"}
PDF_EXT = {".pdf"}
# 办公文档拆成三族：列表里的类型点要能靠颜色认出来，Word 蓝 / Excel 绿 /
# PowerPoint 橙是行业惯例，比统一一个"办公文档"有用得多。三者仍是同一套
# 预览器（OfficePreviewer 按扩展名分派读法），拆的只是 kind。
WORD_EXT = {".doc", ".docx", ".odt", ".rtf"}
SHEET_EXT = {".xls", ".xlsx", ".ods", ".csv"}
SLIDE_EXT = {".ppt", ".pptx", ".odp"}
OFFICE_EXT = WORD_EXT | SHEET_EXT | SLIDE_EXT
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz",
               ".tbz", ".txz", ".deb", ".rpm", ".iso"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".mpg"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".aiff"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2", ".ttc"}
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".htm", ".css",
            ".scss", ".less", ".xml", ".yaml", ".yml", ".toml", ".ini", ".sh",
            ".bash", ".zsh", ".c", ".h", ".cpp", ".hpp", ".java", ".go", ".rs",
            ".rb", ".php", ".sql", ".qml", ".pas", ".cs", ".swift", ".kt", ".vue"}
TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".cfg", ".conf", ".tex", ".rst"}
NAMED_CODE = {"makefile", "dockerfile", "cmakelists.txt", ".gitignore", ".env"}

KIND_LABEL = {
    "folder": "文件夹",
    "image": "图片",
    "pdf": "PDF 文档",
    "office": "办公文档",       # 旧 kind，M2 起拆成 word/excel/ppt，仅留作兜底
    "word": "Word 文档",
    "excel": "Excel 表格",
    "ppt": "演示文稿",
    "archive": "压缩包",
    "video": "视频",
    "audio": "音频",
    "font": "字体",
    "code": "代码",
    "text": "文本",
    "other": "其他",
}


def kind_of(vpath: str, is_dir: bool | None = None) -> str:
    if is_dir:
        return "folder"
    name = (vpath or "").split("/")[-1].lower()
    ext = os.path.splitext(name)[1]
    if name in NAMED_CODE:
        return "code"
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in WORD_EXT:
        return "word"
    if ext in SHEET_EXT:
        return "excel"
    if ext in SLIDE_EXT:
        return "ppt"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in FONT_EXT:
        return "font"
    if ext in CODE_EXT:
        return "code"
    if ext in TEXT_EXT:
        return "text"
    if name.startswith("."):
        return "text"
    return "other"


def kind_label(kind: str) -> str:
    return KIND_LABEL.get(kind, KIND_LABEL["other"])


def human_size(n) -> str:
    if n is None:
        return ""
    try:
        val = float(n)
    except (TypeError, ValueError):
        return ""
    if val < 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while val >= 1024 and i < len(units) - 1:
        val /= 1024.0
        i += 1
    if i == 0:
        return f"{int(val)} B"
    if val >= 100:
        return f"{val:.0f} {units[i]}"
    if val >= 10:
        return f"{val:.1f} {units[i]}"
    return f"{val:.2f} {units[i]}"


def human_time(ts) -> str:
    try:
        value = float(ts)
        if value <= 0:
            return ""
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError, TypeError):
        return ""


@dataclass(frozen=True)
class Entry:
    name: str
    vpath: str
    is_dir: bool
    size: int
    mtime: float
    kind: str
    size_text: str
    time_text: str

    def to_dict(self) -> dict:
        """10 个 role 的值只含 str/int/float/bool，绝不含 None。"""
        return {
            "name": str(self.name),
            "vpath": str(self.vpath),
            "isDir": bool(self.is_dir),
            "size": int(self.size),
            "mtime": float(self.mtime),
            "kind": str(self.kind),
            "sizeText": str(self.size_text),
            "timeText": str(self.time_text),
            "selected": False,
            "isCut": False,
        }


def make_entry(name: str, vpath: str, is_dir: bool, size: int, mtime: float) -> Entry:
    kind = "folder" if is_dir else kind_of(vpath)
    size_text = "--" if is_dir else human_size(size)
    return Entry(
        name=str(name),
        vpath=str(vpath),
        is_dir=bool(is_dir),
        size=int(size or 0),
        mtime=float(mtime or 0),
        kind=kind,
        size_text=size_text,
        time_text=human_time(mtime),
    )
