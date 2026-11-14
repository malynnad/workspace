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
    // 全部压在同一档低饱和上（HSL S≈0.24 / L≈0.44），色相拉开、亮度齐平 ——
    // accent 蓝是唯一的「亮」色（S=1.0 / L=0.71），类型点饱和度一上来就会跟文件夹抢视线。
    readonly property color image: "#67558A"         // 图片（低饱和紫，比 todo 的 #7C6BF0 暗一档）
    readonly property color pdf: "#557E8A"           // PDF（灰青，别用红系抢 danger）
    readonly property color archive: "#8A6F55"       // 压缩包（灰褐，纸箱色）
    // 文本 / 代码：能就地预览的这两类共用一色。注意别命名为 text ——
    // 上面的 text 是正文文字色，两者语义无关。
    // 取色自 2026 款理想 i6「风信紫冷光漆」官方色卡（H=312°）。官方色卡中点实测
    // #908B8F，饱和度只有 2.2% —— 那是车漆在漫射光下的样子，搬到 12px 色块上就是
    // 纯灰、看不出紫。这里保持 312° 色相不动，把饱和度提到 18%，才是同一种紫。
    readonly property color readable: "#A07798"

    readonly property int radius: 16
    readonly property int radiusSm: 10
    readonly property int pad: 20

    // 文件类型点的统一配色：目录蓝、图片紫、PDF 灰青、压缩包灰褐、文本/代码风信紫，
    // 其余（办公/视频/音频/字体/其他）走 line 极浅灰 —— 全上色列表会花，只让需要
    // 一眼认出的跳出来。文件列表 delegate 不许用 Icon（软渲染约束），类型识别全靠
    // 这个小色块，所以两处（FmPanel 列表、文件夹预览）都走这里，别各写一套。
    function kindColor(kind) {
        switch (kind) {
        case "folder": return accent
        case "image": return image
        case "pdf": return pdf
        case "archive": return archive
        case "text": return readable
        case "code": return readable
        default: return line
        }
    }

    // 同上，但「没专门颜色」时回落到 accent 而不是 line。
    // 给预览窗标题栏图标用：图标本身是线描，走 line 就看不见了。
    function kindAccent(kind) {
        var c = kindColor(kind)
        return c === line ? accent : c
    }
}
