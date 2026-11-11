import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

ApplicationWindow {
    id: win
    visible: true
    // 默认尺寸按屏幕可用区算：Screen.* 返回的已是折算过缩放因子的逻辑像素，
    // 所以设了 QT_SCALE_FACTOR 也不会把窗口顶出屏幕。
    // 用 desktopAvailable*（扣掉任务栏）；availableWidth/Height 在 Qt6 的附加类型里不存在。
    readonly property int scrW: Screen.desktopAvailableWidth > 0 ? Screen.desktopAvailableWidth : 1920
    readonly property int scrH: Screen.desktopAvailableHeight > 0 ? Screen.desktopAvailableHeight : 1080

    width: Math.min(1400, Math.round(scrW * 0.86))
    height: Math.min(900, Math.round(scrH * 0.88))
    minimumWidth: Math.min(1040, scrW - 40)
    minimumHeight: Math.min(720, scrH - 40)
    title: "Workspace"
    color: Theme.bg

    property int currentPage: 0

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ---------- 侧边栏 ----------
        Rectangle {
            Layout.preferredWidth: 214
            Layout.fillHeight: true
            color: Theme.surface

            Rectangle {
                width: 1
                height: parent.height
                anchors.right: parent.right
                color: Theme.line
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 3

                Text {
                    text: "Workspace"
                    font.pixelSize: 19
                    font.weight: Font.DemiBold
                    color: Theme.text
                    Layout.leftMargin: 10
                    Layout.topMargin: 8
                    Layout.bottomMargin: 16
                }

                NavButton { label: "工作日志"; iconName: "log"; pageIndex: 0 }
                NavButton { label: "待办事项"; iconName: "todo"; pageIndex: 1 }
                NavButton { label: "脚本面板"; iconName: "script"; pageIndex: 2 }
                NavButton { label: "文件管理"; iconName: "folder"; pageIndex: 3 }

                Item { Layout.fillHeight: true }

                Text {
                    Layout.leftMargin: 10
                    Layout.bottomMargin: 6
                    text: "个人工作台 · v0.1"
                    color: Theme.textMuted
                    font.pixelSize: 11
                }
            }
        }

        // ---------- 主区 ----------
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.currentPage

            LogPage {}
            TodoPage {}
            ScriptsPage {}
            FileManagerPage {}
        }
    }

    // M1b 预览：独立无边框窗口（可拖出主窗口、始终置顶），
    // 不再挂在窗口里当浮层。mainWindow 只用来算初始位置。
    PreviewWindow {
        mainWindow: win
    }

    // 关掉预览后把焦点还给主窗口，接着操作列表（逐个审核时高频动作）
    Connections {
        target: files.preview
        function onClosed() {
            win.requestActivate()
        }
    }

    onClosing: files.preview.close()

    // 全局轻提示（顶部居中）
    ToastHost {
        anchors.fill: parent
        z: 900
    }

    component NavButton: Item {
        id: navBtn
        property string label: ""
        property string iconName: "log"
        property string badge: ""
        property int pageIndex: 0
        readonly property bool active: win.currentPage === pageIndex

        Layout.fillWidth: true
        Layout.preferredHeight: 38

        Rectangle {
            anchors.fill: parent
            radius: 9
            color: navBtn.active ? Theme.accentSoft
                                 : (navHover.containsMouse ? "#F5F6FA" : "transparent")
        }

        Row {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 11
            spacing: 9

            Icon {
                width: 15
                height: 15
                name: navBtn.iconName
                color: navBtn.active ? Theme.accent : Theme.textMuted
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: navBtn.label
                color: navBtn.active ? Theme.accent : Theme.text
                font.pixelSize: 13
                font.weight: navBtn.active ? Font.DemiBold : Font.Normal
                anchors.verticalCenter: parent.verticalCenter
            }
            Rectangle {
                visible: navBtn.badge !== ""
                width: badgeText.implicitWidth + 12
                height: 17
                radius: 8
                color: "#F1F2F6"
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: navBtn.badge
                    font.pixelSize: 10
                    color: Theme.textMuted
                }
            }
        }

        MouseArea {
            id: navHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: win.currentPage = navBtn.pageIndex
        }
    }
}
