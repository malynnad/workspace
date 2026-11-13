import QtQuick
import QtQuick.Layouts

// 阶段 2 · M1a 浏览 + M1b 浮窗预览：常驻收藏栏 + 等比双面板 + QuickLook 式浮层。
//
// 布局推导（wPage 里的 214 来自 Main.qml:23 全局侧栏宽、40 来自 Theme.pad × 2，
// 改这两处必须同步改下面的表达式）：
//   wPage   = Window.window.width - 214 - 40        默认 1240 -> 986
//   rAvail  = wPage - favW(收藏栏) - gapW(12) - splitW(20)
//   leftW   = round(ratio * rAvail)，rightW = rAvail - leftW
//   ratio   = files.leftWidth / 966（966 = 986 - 20，与窗口无关的比例）
// 旧版把左栏钉成固定 483、右栏 fillWidth 吃剩余，导致窗口 ≠1240 时必然不等宽。
// 底部两条槽位留给第 4 步写操作与第 5 步插件命令，本轮由 capabilities 门控隐藏。
Item {
    id: root
    objectName: "fileManagerPage"

    // 唯一绑定源 = 上层 Window 的 width（切页不改变窗口宽度，故切页不会闪）。
    // 严禁绑 root.width / RowLayout 自身尺寸，也不在 onWidthChanged 里命令式回写
    // setLeftWidth —— 那会形成绑定循环、污染持久化状态并与拖拽打架。
    // QML 属性名不能以大写字母开头（qmllint 直接判错）。
    readonly property real wPage: (Window.window ? Window.window.width : 1240) - 214 - 40
    readonly property int favW: wPage < 900 ? 132 : 168
    readonly property int gapW: 12
    readonly property int splitW: Theme.pad
    readonly property real rAvail: wPage - favW - gapW - splitW
    readonly property int baseW: 966
    readonly property real ratio: Math.max(290 / 966, Math.min(676 / 966, files.leftWidth / 966))
    readonly property int leftW: Math.round(ratio * rAvail)
    readonly property int panelMin: 180
    // 不用 files.active().cwd：Slot 调用不产生绑定依赖，Tab 切换后高亮不会重算。
    readonly property string activeVpath:
        files.activeSide === "left" ? files.left.cwd : files.right.cwd

    readonly property bool pageActive: StackLayout.isCurrentItem
    // 预览浮窗打开时，浏览用的 9 个快捷键全部让位给下面 4 个预览快捷键。
    readonly property bool shortcutOk: root.pageActive && !files.preview.open &&
        !leftPanel.addressFocused && !leftPanel.filterFocused &&
        !rightPanel.addressFocused && !rightPanel.filterFocused
    // 预览窗自己激活时不能让路：Qt 的 Shortcut 不是严格 per-active-window，
    // 预览窗激活时这条也会跟着激活，导致「面板刚关、窗口也被关了」。
    readonly property bool previewKeysOk: root.pageActive && files.preview.open &&
        !files.previewActive

    function activePanel() { return files.activeSide === "left" ? files.left : files.right }
    function activeFm() { return files.activeSide === "left" ? leftPanel : rightPanel }

    // 切到别的模块时把浮窗收掉，免得状态是 open 但页面不可见。
    onPageActiveChanged: { if (!root.pageActive) files.preview.close() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: 0

        RowLayout {
            objectName: "fmRow"
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            FavoritesBar {
                id: favoritesBar
                activeVpath: root.activeVpath
                Layout.preferredWidth: root.favW
                Layout.rightMargin: root.gapW
                Layout.fillHeight: true
            }

            FmPanel {
                id: leftPanel
                objectName: "fmLeft"
                panel: files.left
                side: "left"
                active: files.activeSide === "left"
                Layout.preferredWidth: root.leftW
                Layout.minimumWidth: root.panelMin
                Layout.fillHeight: true
                onActivated: files.setActiveSide("left")
            }

            Item {
                id: splitter
                objectName: "fmSplitter"
                Layout.preferredWidth: root.splitW
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
                        // 屏幕位移换算回 966 基准：任何窗口宽度下手柄都 1:1 跟手。
                        if (pressed)
                            files.setLeftWidth(startW +
                                Math.round(root.baseW * (mouse.x - startX) / root.rAvail))
                    }
                }
            }

            FmPanel {
                id: rightPanel
                objectName: "fmRight"
                panel: files.right
                side: "right"
                active: files.activeSide === "right"
                Layout.fillWidth: true              // 唯一 fillWidth 子项，吸收剩余宽度
                Layout.minimumWidth: root.panelMin
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

    // 后端 toast 的页面级单点监听（FmPanel.qml 里两份已移除，避免一次 emit 弹两遍）
    Connections {
        target: files
        function onToast(msg) { Toast.show(msg) }
    }

    // ---- M1b 预览 ----
    // PreviewWindow 是 Main.qml 里的独立窗口（可拖出主窗口、始终置顶），
    // 这里只负责开关联动、关闭后把焦点还给列表、以及主窗口侧的 4 个预览快捷键
    // （预览窗自己也有同套快捷键，靠 WindowShortcut context 各管各的）。
    Connections {
        target: files.preview
        function onClosed() { root.activeFm().focusList() }
        function onUnavailable() { Toast.show("当前不支持预览此项目") }
    }

    // ---------------- 全局快捷键（只在当前页且输入框未聚焦时生效） ----------------
    // 预留 Ctrl+D「收藏当前目录」，与现有 9 个快捷键不冲突，M1b 再启用。
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

    // ---------------- 预览窗关闭快捷键（主窗口侧） ----------------
    // 预览窗是独立窗口，↑↓ 归它自己管（Keys 处理）。主窗口这里只保留「关掉」两个字：
    // 预览还开着、用户点回主窗口按 Esc/空格能关掉；而 ↑↓ 留在列表里做选区移动。
    // 早先这里也绑了 ↑↓，结果预览窗和主窗口同时响应，一次按键跳两项。
    Shortcut {
        sequence: "Esc"
        enabled: root.previewKeysOk
        onActivated: files.preview.close()
    }
    Shortcut {
        sequence: "Space"
        enabled: root.previewKeysOk
        onActivated: files.preview.close()
    }
}
