import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 气泡确认（popconfirm）：贴在触发控件旁，不打断操作流。
// 用于「单条记录删除」这类可恢复/低后果的操作；
// 不可逆的大动作（清空全部日志等）再另外用模态 Dialog。
Popup {
    id: root

    property string message: "确定删除？"
    property string hint: "删除后不可恢复"
    property string okText: "删除"
    property string cancelText: "取消"

    signal accepted()
    signal canceled()

    width: 236
    padding: 14
    modal: false
    focus: true
    z: 950
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        radius: 12
        color: "#FFFFFF"
        border.width: 1
        border.color: "#E7E9F1"
    }

    // 在某个控件旁边弹出，空间不足时自动翻到上方
    // 默认以整个窗口内容区为坐标系，这样窗口底部的按钮也能算对位置
    function openAt(item, hostItem) {
        if (!item)
            return
        var host = hostItem
        if (!host && item.Window.window)
            host = item.Window.window.contentItem
        if (!host)
            host = item.parent
        parent = host
        var p = item.mapToItem(host, 0, 0)
        var belowY = p.y + item.height + 8
        var aboveY = p.y - implicitHeight - 8
        x = Math.max(8, Math.min(p.x + item.width - width, host.width - width - 8))
        y = (belowY + implicitHeight > host.height - 8)
                ? Math.max(8, aboveY) : belowY
        open()
    }

    contentItem: ColumnLayout {
        spacing: 10

        Text {
            Layout.fillWidth: true
            text: root.message
            font.pixelSize: 13
            font.weight: Font.Medium
            color: Theme.text
        }

        Text {
            Layout.fillWidth: true
            visible: root.hint !== ""
            text: root.hint
            font.pixelSize: 11
            color: Theme.textMuted
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 2
            spacing: 8

            Item { Layout.fillWidth: true }

            Btn {
                text: root.cancelText
                ghost: true
                boxHeight: 28
                onClicked: { root.canceled(); root.close() }
            }

            Btn {
                text: root.okText
                danger: true
                boxHeight: 28
                onClicked: { root.accepted(); root.close() }
            }
        }
    }
}
