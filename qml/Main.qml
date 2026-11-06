import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    visible: true
    width: 1240
    height: 780
    minimumWidth: 1040
    minimumHeight: 720
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
