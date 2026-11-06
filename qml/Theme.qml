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

    readonly property int radius: 16
    readonly property int radiusSm: 10
    readonly property int pad: 20
}
