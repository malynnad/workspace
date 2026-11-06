import QtQuick

// 顶部居中浮动轻提示：淡入 + 上移，2.2 秒自动收起
Item {
    id: root
    property string text: ""
    property bool shown: false
    z: 900

    Connections {
        target: Toast
        function onMessage(t) {
            root.text = t
            root.shown = true
            hideTimer.restart()
        }
    }

    Timer {
        id: hideTimer
        interval: 2200
        onTriggered: root.shown = false
    }

    Item {
        id: bubble
        width: row.implicitWidth + 40
        height: 42
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.shown ? 16 : 4
        opacity: root.shown ? 1 : 0
        visible: opacity > 0

        Rectangle {
            anchors.fill: parent
            anchors.topMargin: 6
            radius: 13
            color: "#12000000"
        }

        Rectangle {
            anchors.fill: parent
            anchors.bottomMargin: 6
            radius: 12
            color: "#FFFFFF"
            border.width: 1
            border.color: "#E7E9F1"

            Row {
                id: row
                anchors.centerIn: parent
                spacing: 9
                Icon {
                    name: "check"
                    width: 15
                    height: 15
                    color: "#3FB984"
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: root.text
                    font.pixelSize: 13
                    color: Theme.text
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        Behavior on y { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: 180 } }
    }
}
