import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 顶栏时钟：显示当前时间（HH:mm），悬停弹出「还有多久下班」气泡卡。
// 下班节点固定两段：上午班到 12:00、下午班到 18:00。
Item {
    id: root
    objectName: "clock"

    // 用 Timer 驱动，避免每秒重建绑定
    property var time: new Date()

    readonly property int nowMin: time.getHours() * 60 + time.getMinutes()
    readonly property int noonMin: 12 * 60
    readonly property int offMin: 18 * 60
    readonly property bool beforeNoon: nowMin < noonMin
    readonly property bool afterWork: nowMin >= offMin
    readonly property int leftMin: afterWork ? 0
                              : (beforeNoon ? noonMin - nowMin : offMin - nowMin)

    readonly property string clockText: pad2(time.getHours()) + ":" + pad2(time.getMinutes())
    readonly property string tipTitle: afterWork ? "今天已下班"
                                     : (beforeNoon ? "距午休还有" : "距下班还有")
    readonly property string tipBig: {
        if (afterWork)
            return "收工了"
        var h = Math.floor(leftMin / 60)
        var m = leftMin % 60
        if (h === 0)
            return m + " 分"
        return m === 0 ? h + " 小时" : h + " 小时 " + m + " 分"
    }
    readonly property string tipSub: afterWork ? "早点休息，明天见"
                                   : (beforeNoon ? "上午班 12:00 结束" : "下午班 18:00 收工")

    function pad2(n) {
        return n < 10 ? "0" + n : "" + n
    }

    implicitWidth: clockRow.implicitWidth
    implicitHeight: 24

    Timer {
        interval: 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.time = new Date()
    }

    Row {
        id: clockRow
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6

        Icon {
            name: "clock"
            width: 15
            height: 15
            color: hover.containsMouse ? Theme.accent : Theme.textMuted
            anchors.verticalCenter: parent.verticalCenter
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.clockText
            font.pixelSize: 12
            font.weight: Font.Medium
            color: hover.containsMouse ? Theme.accent : Theme.text
        }
    }

    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        onEntered: openTimer.start()
        onExited: {
            openTimer.stop()
            closeTimer.start()
        }
    }

    // 悬停意图判定：鼠标停留 420ms 才弹，避免划过顶栏时闪一下
    Timer {
        id: openTimer
        interval: 420
        onTriggered: root.showTip()
    }
    // 移出后留 200ms，方便鼠标从时钟滑进气泡时不用重新等待
    Timer {
        id: closeTimer
        interval: 200
        onTriggered: tip.close()
    }

    function showTip() {
        var win = root.Window.window
        if (!win)
            return
        var host = win.contentItem
        tip.parent = host
        var p = root.mapToItem(host, 0, 0)
        tip.x = Math.max(8, Math.min(p.x + root.width - tip.width,
                                     host.width - tip.width - 8))
        tip.y = p.y + root.height + 8
        tip.open()
    }

    Popup {
        id: tip
        objectName: "clockTip"
        width: 196
        padding: 14
        modal: false
        z: 940
        closePolicy: Popup.NoAutoClose

        enter: Transition {
            NumberAnimation {
                property: "opacity"
                from: 0
                to: 1
                duration: 150
                easing.type: Easing.OutCubic
            }
        }
        exit: Transition {
            NumberAnimation {
                property: "opacity"
                from: 1
                to: 0
                duration: 120
                easing.type: Easing.InCubic
            }
        }

        background: Rectangle {
            radius: 12
            color: "#FFFFFF"
            border.width: 1
            border.color: "#E7E9F1"

            // 鼠标滑进气泡时保持打开，移出再收起
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onEntered: closeTimer.stop()
                onExited: closeTimer.start()
            }
        }

        contentItem: ColumnLayout {
            spacing: 3

            Text {
                text: root.tipTitle
                font.pixelSize: 11
                color: Theme.textMuted
            }

            Text {
                text: root.tipBig
                font.pixelSize: 20
                font.weight: Font.DemiBold
                color: root.afterWork ? Theme.ok : Theme.accent
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 7
                Layout.bottomMargin: 5
                height: 1
                color: Theme.line
            }

            Text {
                Layout.fillWidth: true
                text: root.tipSub
                font.pixelSize: 11
                color: Theme.textMuted
            }
        }
    }
}
