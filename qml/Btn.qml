import QtQuick
import QtQuick.Controls

// 统一按钮
//   primary     实心主色蓝
//   （默认）     浅灰底
//   ghost       无底
//   danger      淡红描边（危险但次要，如单条删除的次级入口）
//   solidDanger 红底白字（明确的破坏性动作）
//   tint        任意实心色（如「转为待办」用 Theme.todo 紫）
Button {
    id: root
    property bool primary: false
    property bool danger: false
    property bool ghost: false
    property bool solidDanger: false
    property color tint: "transparent"
    property int boxHeight: 32

    readonly property color _fill: solidDanger ? Theme.danger
                                 : tint.a > 0 ? tint
                                 : primary ? Theme.accent
                                 : "transparent"
    readonly property bool _solid: _fill.a > 0

    implicitHeight: boxHeight
    leftPadding: 14
    rightPadding: 14
    font.pixelSize: 13
    font.weight: _solid ? Font.DemiBold : Font.Normal

    contentItem: Text {
        text: root.text
        font: root.font
        color: !root.enabled ? Theme.textMuted
             : root._solid ? "white"
             : root.danger ? Theme.danger
             : Theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        radius: 9
        color: {
            if (root._solid)
                return root.down ? Qt.darker(root._fill, 1.10)
                     : root.hovered ? Qt.lighter(root._fill, 1.06)
                     : root._fill
            if (root.danger)
                return root.hovered ? "#FDEEEE" : "transparent"
            if (root.ghost)
                return root.hovered ? "#F1F2F6" : "transparent"
            return root.hovered ? "#EAECF2" : "#F1F2F6"
        }
        // ghost 是「无底的文字按钮」，再给它描边就变成小胶囊了 —— 一排里混着
        // 有框的和没框的，视觉重量对不上。danger + ghost 只保留红色文字。
        border.width: root.danger && !root._solid && !root.ghost ? 1 : 0
        border.color: "#F2C9C9"
        opacity: root.enabled ? 1 : 0.45
    }
}
