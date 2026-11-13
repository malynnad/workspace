import QtQuick

// 设置面板里的「一组三档清晰度」。图片与 PDF 各用一个实例，值走
// filecore/state.py 的 QUALITY_CHOICES 白名单（1.0 / 1.5 / 2.0）。
// 面板宽度由外部给（Column 不拉伸子项），delegate 跟着 root.width。
Column {
    id: root
    property string title: ""
    property var choices: []
    property real current: 0
    signal pick(real value)

    spacing: 2

    Text {
        text: root.title
        topPadding: 6          // 两组之间留出呼吸，靠这个而不是外层 spacing
        font.pixelSize: 11
        color: Theme.textMuted
    }

    Repeater {
        model: root.choices
        delegate: Rectangle {
            required property var modelData
            width: root.width
            height: 34
            radius: 8
            readonly property bool picked:
                Math.abs(root.current - modelData.v) < 0.001
            color: picked ? Theme.accentSoft
                          : (rowMouse.containsMouse ? Theme.bg : "transparent")

            Rectangle {
                id: ring
                x: 10
                y: (parent.height - 14) / 2
                width: 14
                height: 14
                radius: 7
                color: "transparent"
                border.width: 1
                border.color: parent.picked ? Theme.accent : Theme.textMuted
            }
            Rectangle {
                anchors.centerIn: ring
                width: 6
                height: 6
                radius: 3
                color: Theme.accent
                visible: parent.picked
            }
            Text {
                x: 34
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.label
                font.pixelSize: 12
                color: parent.picked ? Theme.accent : Theme.text
            }
            Text {
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.desc
                font.pixelSize: 10
                color: Theme.textMuted
            }
            MouseArea {
                id: rowMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.pick(modelData.v)
            }
        }
    }
}
