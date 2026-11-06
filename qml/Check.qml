import QtQuick

// 圆形勾选框
Item {
    id: root
    property bool checked: false
    signal toggled()

    implicitWidth: 20
    implicitHeight: 20

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.checked ? Theme.accent : "transparent"
        border.width: root.checked ? 0 : 1.4
        border.color: "#CFD4E0"
    }

    Canvas {
        anchors.fill: parent
        visible: root.checked
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = "white"
            ctx.lineWidth = 1.8
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.beginPath()
            ctx.moveTo(width * 0.27, height * 0.52)
            ctx.lineTo(width * 0.44, height * 0.68)
            ctx.lineTo(width * 0.74, height * 0.34)
            ctx.stroke()
        }
        onVisibleChanged: if (visible) requestPaint()
    }

    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled()
    }
}
