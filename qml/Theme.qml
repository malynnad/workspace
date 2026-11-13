pragma Singleton
import QtQuick

QtObject {
    // Workspace 设计 token —— 参照 macOS 风浅色主题
    readonly property color bg: "#F2F3F7"
    readonly property color surface: "#FFFFFF"
    readonly property color accent: "#6A8DFF"
    readonly property color accentSoft: "#EDF1FF"
    readonly property color text: "#1C1F26"
    readonly property color textMuted: "#8A90A0"
    readonly property color line: "#ECEEF4"
    readonly property color danger: "#E86A6A"
    readonly property color warn: "#E8A13A"
    readonly property color todo: "#7C6BF0"          // 待办专属色（紫）——与主色蓝区分
    readonly property color todoSoft: "#F1EEFF"
    readonly property color ok: "#3FB984"
    // 文件类型点：只有「能预览 / 有专门处理」的类型才上色，其余走 line 灰，
    // 一眼就能看出哪些文件点开有内容。值不与动作色共用意（danger 是删除）。
    readonly property color image: "#7C6BF0"         // 图片（与待办同紫，语义独立）
    readonly property color pdf: "#E2694F"           // PDF（珊瑚红，比 danger 偏暖）

    readonly property int radius: 16
    readonly property int radiusSm: 10
    readonly property int pad: 20

    // 文件类型点的统一配色：目录蓝、图片紫、PDF 珊瑚红，其余（含文本/压缩包等
    // 日常一大堆的类型）走 line 极浅灰 —— 全上色列表会花，只让需要一眼认出的跳出来。
    // 文件列表 delegate 不许用 Icon（软渲染约束），类型识别全靠这个小色块，
    // 所以两处（FmPanel 列表、文件夹预览）都走这里，别各写一套。
    function kindColor(kind) {
        switch (kind) {
        case "folder": return accent
        case "image": return image
        case "pdf": return pdf
        default: return line
        }
    }
}
