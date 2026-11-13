import QtQuick

// 拨动开关（36×20）。设置面板里「显示隐藏文件」这类布尔项用它。
// 不用 QtQuick.Controls 的 Switch：Basic 风格下是方头、带阴影，跟这套浅色圆角
// 设计对不上；自绘只有两个矩形 + 一个 Behavior，成本比调样式低。
Item {
    id: root
    property bool checked: false
    signal toggled(bool value)

    implicitWidth: 36
    implicitHeight: 20

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: root.checked ? Theme.accent : "#D8DBE4"
        Behavior on color { ColorAnimation { duration: 110 } }
    }

    Rectangle {
        width: 16
        height: 16
        radius: 8
        y: 2
        x: root.checked ? parent.width - width - 2 : 2
        color: "#FFFFFF"
        // 关态给一点描边，否则纯白圆点在浅灰底上边缘发虚
        border.width: root.checked ? 0 : 1
        border.color: "#E3E6EE"
        Behavior on x { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled(!root.checked)
    }
}
