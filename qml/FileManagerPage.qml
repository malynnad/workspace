import QtQuick
import QtQuick.Layouts

// 阶段 2 · M1a：双面板浏览页。
// 986 = 483 + 20(拖拽手柄) + 483；底部两条槽位留给第 4 步写操作与第 5 步插件命令，
// 本轮由 capabilities 门控隐藏，后续只翻能力位，不重排布局。
Item {
    id: root
    objectName: "fileManagerPage"

    readonly property bool pageActive: StackLayout.isCurrentItem
    readonly property bool shortcutOk: root.pageActive &&
        !leftPanel.addressFocused && !leftPanel.filterFocused &&
        !rightPanel.addressFocused && !rightPanel.filterFocused

    function activePanel() { return files.activeSide === "left" ? files.left : files.right }
    function activeFm() { return files.activeSide === "left" ? leftPanel : rightPanel }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            FmPanel {
                id: leftPanel
                objectName: "fmLeft"
                panel: files.left
                side: "left"
                active: files.activeSide === "left"
                Layout.preferredWidth: files.leftWidth
                Layout.minimumWidth: 320
                Layout.fillHeight: true
                onActivated: files.setActiveSide("left")
            }

            Item {
                id: splitter
                Layout.preferredWidth: Theme.pad
                Layout.fillHeight: true

                Rectangle {
                    width: 6; height: 42; radius: 3
                    anchors.centerIn: parent
                    color: splitMouse.containsMouse ? Theme.accentSoft : Theme.line
                }
                MouseArea {
                    id: splitMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.SplitHCursor
                    property real startX: 0
                    property int startW: 0
                    onPressed: function(mouse) {
                        startX = mouse.x
                        startW = files.leftWidth
                    }
                    onPositionChanged: function(mouse) {
                        if (pressed) files.setLeftWidth(startW + Math.round(mouse.x - startX))
                    }
                }
            }

            FmPanel {
                id: rightPanel
                objectName: "fmRight"
                panel: files.right
                side: "right"
                active: files.activeSide === "right"
                Layout.fillWidth: true
                Layout.minimumWidth: 320
                Layout.fillHeight: true
                onActivated: files.setActiveSide("right")
            }
        }

        // ---- 槽位 1：M1c 写操作条（Marta 的 F5 复制 / F6 移动 / F7 新建） ----
        RowLayout {
            id: opsBar
            Layout.fillWidth: true
            Layout.preferredHeight: 34
            Layout.topMargin: 8
            spacing: 8
            visible: files.left.capabilities.canWrite
            Text {
                text: "文件操作"
                font.pixelSize: 11
                color: Theme.textMuted
            }
            Item { Layout.fillWidth: true }
        }

        // ---- 槽位 2：M3 插件 / 命令行（Marta 的底部命令输入） ----
        Rectangle {
            id: commandBar
            Layout.fillWidth: true
            Layout.preferredHeight: 30
            Layout.topMargin: 6
            radius: 9
            color: Theme.surface
            border.width: 1
            border.color: Theme.line
            visible: files.left.capabilities.canArchive
            Text {
                anchors.left: parent.left
                anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: "命令"
                font.pixelSize: 11
                color: Theme.textMuted
            }
        }
    }

    // ---------------- 全局快捷键（只在当前页且输入框未聚焦时生效） ----------------
    Shortcut {
        sequence: "F5"
        enabled: root.shortcutOk
        onActivated: root.activePanel().refresh()
    }
    Shortcut {
        sequence: "Alt+Left"
        enabled: root.shortcutOk
        onActivated: root.activePanel().goBack()
    }
    Shortcut {
        sequence: "Alt+Right"
        enabled: root.shortcutOk
        onActivated: root.activePanel().goForward()
    }
    Shortcut {
        sequence: "Alt+Up"
        enabled: root.shortcutOk
        onActivated: root.activePanel().goUp()
    }
    Shortcut {
        sequence: "Ctrl+H"
        enabled: root.shortcutOk
        onActivated: root.activePanel().toggleHidden()
    }
    Shortcut {
        sequence: "Ctrl+L"
        enabled: root.shortcutOk
        onActivated: root.activeFm().focusAddress()
    }
    Shortcut {
        sequence: "Ctrl+F"
        enabled: root.shortcutOk
        onActivated: root.activeFm().focusFilter()
    }
    Shortcut {
        sequence: "Ctrl+A"
        enabled: root.shortcutOk
        onActivated: root.activePanel().selectAll()
    }
    Shortcut {
        sequence: "Tab"
        enabled: root.shortcutOk
        onActivated: files.toggleActiveSide()
    }
}
