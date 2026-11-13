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

                // ---------- 设置入口（全应用唯一一个） ----------
                // 放侧栏底部：桌面软件的通行位置（VS Code / Figma / Slack 都在左下角），
                // 也不占任何页面自己的工具栏。所有模块的设置项都进这一个面板 ——
                // 文件浏览的隐藏文件、顶栏的工作时段、预览的清晰度，读写的都是 files
                // 上的同一份状态，不存在「两个地方各存一份」。
                Item {
                    id: settingsBtn
                    objectName: "settingsButton"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36

                    Rectangle {
                        anchors.fill: parent
                        radius: 9
                        color: settingsPanel.visible ? Theme.accentSoft
                               : (settingsMouse.containsMouse ? "#F5F6FA" : "transparent")
                    }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 11
                        spacing: 9
                        Icon {
                            width: 15
                            height: 15
                            name: "gear"
                            anchors.verticalCenter: parent.verticalCenter
                            color: settingsPanel.visible ? Theme.accent : Theme.textMuted
                        }
                        Text {
                            text: "设置"
                            font.pixelSize: 13
                            anchors.verticalCenter: parent.verticalCenter
                            color: settingsPanel.visible ? Theme.accent : Theme.text
                            font.weight: settingsPanel.visible ? Font.DemiBold : Font.Normal
                        }
                    }
                    MouseArea {
                        id: settingsMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: settingsPanel.toggleAtItem(settingsBtn)
                    }
                }

                Text {
                    Layout.leftMargin: 10
                    Layout.bottomMargin: 6
                    Layout.topMargin: 4
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
            FileManagerPage {
                // 设置面板开着时，页面自己的快捷键要让路（Esc、F5、Tab…）
                settingsOpen: settingsPanel.visible
            }
        }
    }

    // 全局设置面板：铺满窗口，锚在左下角齿轮上往右上弹
    SettingsPanel {
        id: settingsPanel
        anchors.fill: parent
    }

    // Esc 关面板。要排除预览窗激活的时刻：Qt 的 Shortcut 不是严格按活动窗口隔离的，
    // 预览窗激活时这条照样会响应，会把用户在主窗口里的面板莫名关掉。
    Shortcut {
        sequence: "Escape"
        enabled: settingsPanel.visible && !files.previewActive
        onActivated: settingsPanel.close()
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
