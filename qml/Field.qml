import QtQuick
import QtQuick.Controls

// 统一输入框：浅灰底、圆角、聚焦时蓝描边
TextField {
    id: root
    implicitHeight: 34
    leftPadding: 12
    rightPadding: 12
    font.pixelSize: 13
    color: Theme.text
    placeholderTextColor: Theme.textMuted
    selectByMouse: true

    background: Rectangle {
        radius: 9
        color: "#F5F6FA"
        border.width: root.activeFocus ? 1 : 0
        border.color: Theme.accent
    }
}
