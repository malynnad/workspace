import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 单面板：两行工具栏 + 响应式列头 + ListView + 信息条 + 状态栏 + 三态覆盖层。
// 界面方案 A（Marta 式等宽双栏 + ForkLift 式顶部激活短横），浅色主题全部走 Theme.*。
// 软渲染硬约束：delegate 内零 Layout / 零 Canvas / 零动画 / 零 Icon / 零 Check，
// 固定行高 32，全部用绝对定位的 Rectangle + Text。
Item {
    id: root
    property var panel: null
    property bool active: false
    property string side: "left"
    property bool addressFocused: false
    property bool filterFocused: false
    property string currentVpath: ""
    // 页面是否拥有键盘：文件管理页不可见、或设置面板展开时为 false。
    // Qt 不保证 item 变不可见就把键盘焦点交还（离屏会清、麒麟/X11 上会留在原地），
    // 隐藏的列表继续吃键 —— 表现就是「按空格关了预览、切到待办再按空格，预览又弹出来」。
    property bool keysEnabled: true
    signal activated()

    // 右键菜单挂在页面根上（只有那儿能盖住左右两栏），所以由面板算好菜单项、
    // 把场景坐标上抛。永久删除同理：确认框是页面级的一个，不左右各来一份。
    signal menuRequested(var source, var items, real sceneX, real sceneY)
    signal permanentDeleteRequested(var vpaths)

    // 行内重命名：只存 vpath，delegate 里按 model.vpath 决定显示文本还是输入框。
    property string renamingVpath: ""
    property string renameDraft: ""
    // 新建目录的 vpath：等这一轮刷新回来、行真的存在了再进行内改名。
    // 直接在 newFolder() 之后 beginRename 会被刷新重建委托打断（编辑框刚拿到
    // 焦点就被顶掉，被当成「点了别处」自动提交），见 onFocusRequested。
    property string pendingNewFolder: ""

    // 右键菜单的目标快照（菜单弹出那一刻的选中项）。菜单项点击发生在几百毫秒后，
    // 期间任何一次重扫/聚焦变化都不该改变这次操作的对象 —— 详见 showMenu。
    property var menuTargets: []
    property string menuTarget: ""

    readonly property int colSize: 64
    readonly property int colKind: 60
    readonly property int colTime: 96
    readonly property int colGap: 4
    readonly property int rowH: 32
    readonly property int panelPad: 14       // Card 内 ColumnLayout anchors.margins
    readonly property int minNameW: 80

    // 面板变宽时名称列吃掉多余宽度（最大化下右侧不再留白）；
    // 面板变窄时依次隐藏「类型」「修改时间」，信息仍在下方选中项信息条里。
    readonly property int contentW: Math.max(0, Math.round(root.width - 2 * panelPad))
    readonly property bool showKind: root.width >= 430
    readonly property bool showTime: root.width >= 380

    // 列头与数据行共用这一套列定义，绝不各算各的。
    readonly property var cols: {
        var a = [
            { key: "name", label: "名称", sortCol: 0, w: 0 },
            { key: "size", label: "大小", sortCol: 1, w: colSize }
        ]
        if (showKind) a.push({ key: "kind", label: "类型", sortCol: 2, w: colKind })
        if (showTime) a.push({ key: "time", label: "修改时间", sortCol: 3, w: colTime })
        var reserved = colSize + (showKind ? colKind : 0) + (showTime ? colTime : 0)
                       + colGap * (a.length - 1)
        a[0].w = Math.max(minNameW, contentW - reserved)
        return a
    }

    function xOf(key) {
        var x = 0
        for (var i = 0; i < cols.length; ++i) {
            if (cols[i].key === key) return x
            x += cols[i].w + colGap
        }
        return 0
    }

    function wOf(key) {
        for (var i = 0; i < cols.length; ++i)
            if (cols[i].key === key) return cols[i].w
        return 0
    }

    function labelOf(kind) {
        switch (kind) {
        case "folder": return "文件夹"
        case "image": return "图片"
        case "pdf": return "PDF"
        case "office": return "文档"
        case "archive": return "压缩包"
        case "video": return "视频"
        case "audio": return "音频"
        case "font": return "字体"
        case "code": return "代码"
        case "text": return "文本"
        default: return "其他"
        }
    }

    function isError(text) {
        if (!text) return false
        return text.indexOf("权限") >= 0 || text.indexOf("不存在") >= 0 ||
               text.indexOf("不是文件夹") >= 0 || text.indexOf("失败") >= 0 ||
               text.indexOf("暂不支持") >= 0 || text.indexOf("路径无效") >= 0
    }

    function focusAddress() {
        addressField.forceActiveFocus()
        addressField.selectAll()
    }

    function focusFilter() {
        filterField.forceActiveFocus()
        filterField.selectAll()
    }

    function focusList() {
        // 页面不可见时别把焦点硬塞回隐藏的列表：预览关掉（onClosed）可能就发生在
        // 刚切页的那一刻，focusList 会把焦点从新页面抢回来。
        if (!root.keysEnabled) return
        list.forceActiveFocus()
    }

    // 失去键盘资格时主动放开列表焦点。不在恢复时抢焦点：切回页面由页面自己决定
    // （FileManagerPage.onVisibleChanged 里 callLater 调 focusList），免得跟地址栏/过滤框打架。
    onKeysEnabledChanged: {
        if (!root.keysEnabled) list.focus = false
    }

    // ---------------- 写操作：菜单 / 行内重命名 ----------------
    function hasSelection() {
        return root.panel && root.panel.selectedCount > 0
    }

    function isSelected(vp) {
        return root.panel ? root.panel.selectedVpaths().indexOf(vp) >= 0 : false
    }

    function selectedVpaths() {
        return root.panel ? root.panel.selectedVpaths() : []
    }

    function showMenu(kind, sceneX, sceneY) {
        root.activated()
        // 把「这次操作的对象」在菜单弹出的一刻冻下来。菜单是异步的（人要移动鼠标、
        // 中间可能来一次重扫），点菜单项时再去读 panel.selection 就未必还是右键的那批了。
        if (kind === "item") {
            root.menuTargets = root.selectedVpaths()
            root.menuTarget = root.menuTargets.length === 1 ? root.menuTargets[0] : ""
        } else {
            root.menuTargets = []
            root.menuTarget = ""
        }
        var items = kind === "item" ? root.buildItemMenu() : root.buildEmptyMenu()
        if (items.length > 0)
            root.menuRequested(root, items, sceneX, sceneY)
    }

    // 菜单只传 action 字符串（函数过不了 QVariant），实际动作在这里分派
    function performMenuAction(id) {
        if (!root.panel)
            return
        switch (id) {
        case "preview":
            if (root.menuTarget !== "")
                files.preview.request(root.menuTarget)
            break
        case "open":
            if (root.menuTarget !== "")
                files.openDefault(root.menuTarget)
            break
        case "copy": files.clipboardCopy(root.menuTargets, false); break
        case "cut": files.clipboardCopy(root.menuTargets, true); break
        case "paste": root.panel.paste(); break
        case "rename": root.beginRename(root.menuTarget); break
        case "trash": files.trashPaths(root.menuTargets); break
        case "copyPath": files.copyPathText(root.menuTargets); break
        case "newFolder": root.createFolder(); break
        case "selectAll": root.panel.selectAll(); break
        case "refresh": root.panel.refresh(); break
        case "terminal": files.openTerminal(root.panel.cwd); break
        case "copyCwd": files.copyPathText([root.panel.cwd]); break
        }
    }

    function buildItemMenu() {
        var count = root.panel ? root.panel.selectedCount : 0
        var single = count === 1
        var title = single && root.panel.info ? root.panel.info.name :
                    (count > 1 ? ("已选 " + count + " 项") : "未选择")
        var clipCount = files.clipboard.count
        return [
            { header: true, label: title },
            { label: "预览", shortcut: "Space", enabled: single, action: "preview" },
            { label: "打开", shortcut: "Ctrl+Enter", enabled: single, action: "open" },
            { separator: true },
            { label: "复制", shortcut: "Ctrl+C", action: "copy" },
            { label: "剪切", shortcut: "Ctrl+X", action: "cut" },
            { label: "粘贴", shortcut: "Ctrl+V", enabled: clipCount > 0, action: "paste" },
            { separator: true },
            { label: "重命名", shortcut: "F2", enabled: single, action: "rename" },
            { label: "移到回收站", shortcut: "Delete", danger: true, action: "trash" },
            { separator: true },
            { label: "复制路径", action: "copyPath" },
            { label: "新建文件夹", shortcut: "Ctrl+Shift+N", action: "newFolder" }
        ]
    }

    function buildEmptyMenu() {
        return [
            { label: "粘贴", shortcut: "Ctrl+V", enabled: files.clipboard.count > 0,
              action: "paste" },
            { label: "新建文件夹", shortcut: "Ctrl+Shift+N", action: "newFolder" },
            { separator: true },
            { label: "全选", shortcut: "Ctrl+A", action: "selectAll" },
            { label: "刷新", shortcut: "F5", action: "refresh" },
            { separator: true },
            { label: "在终端中打开", action: "terminal" },
            { label: "复制当前路径", action: "copyCwd" }
        ]
    }

    function beginRename(vp) {
        var target = vp || root.currentVpath
        if (!target || !root.panel)
            return
        root.renameDraft = root.panel.nameOf(target)
        if (root.renameDraft === "")
            return
        root.renamingVpath = target
    }

    function renameSelected() {
        if (!root.panel || root.panel.selectedCount !== 1)
            return
        var picked = root.selectedVpaths()
        if (picked.length === 1)
            root.beginRename(picked[0])
    }

    function cancelRename() {
        root.renamingVpath = ""
        root.renameDraft = ""
        if (root.keysEnabled) list.forceActiveFocus()
    }

    function commitRename() {
        var vp = root.renamingVpath
        if (vp === "" || !root.panel)
            return
        var text = root.renameDraft
        root.renamingVpath = ""
        var err = root.panel.renameItem(vp, text)
        if (err !== "" && err !== undefined)
            Toast.show(err)
        if (root.keysEnabled) list.forceActiveFocus()
    }

    function createFolder() {
        if (!root.panel)
            return
        var vp = root.panel.newFolder()
        // 这里只登记，不立刻进改名：newFolder 会触发一次重扫，ListView 复用/重建
        // 委托，编辑框瞬间失焦 → 自动提交 → 人一个字都没输上就退出了。
        // 等重扫把「选中新目录 + 滚到可见」做完（focusRequested）再进去。
        if (vp !== "")
            root.pendingNewFolder = vp
    }

    function requestPermanentDelete() {
        if (!root.panel || root.panel.selectedCount === 0)
            return
        root.permanentDeleteRequested(root.selectedVpaths())
    }

    function statusText() {
        var n = root.panel ? root.panel.totalCount : 0
        var m = root.panel ? root.panel.visibleCount : 0
        var k = root.panel ? root.panel.selectedCount : 0
        var head = (root.panel && root.panel.filter !== "")
                   ? ("显示 " + m + " / 共 " + n + " 项")
                   : ("共 " + n + " 项")
        return head + " · 已选 " + k + " 项"
    }

    // 点击面板空白处也能激活（放在最底层，其它 MouseArea 优先）
    MouseArea {
        anchors.fill: parent
        z: -1
        acceptedButtons: Qt.LeftButton
        onPressed: root.activated()
    }

    // 工具栏用的线描图标按钮（只放工具栏，绝不进 delegate）
    component ToolBtn: Item {
        id: tool
        property string iconName: "folder"
        property bool toolEnabled: true
        signal clicked()
        Layout.preferredWidth: 28
        Layout.preferredHeight: 28
        opacity: tool.toolEnabled ? 1 : 0.38

        Rectangle {
            anchors.fill: parent
            radius: 7
            color: tool.toolEnabled && toolMouse.containsMouse ? Theme.accentSoft : "transparent"
        }
        Icon {
            anchors.centerIn: parent
            width: 15; height: 15
            name: tool.iconName
            color: Theme.textMuted
        }
        MouseArea {
            id: toolMouse
            anchors.fill: parent
            enabled: tool.toolEnabled
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: { root.activated(); tool.clicked() }
        }
    }

    Card {
        id: card
        anchors.fill: parent

        // 激活指示：居中 56×3 短横，避开 Card 的 16px 圆角
        Rectangle {
            z: 6
            width: 56; height: 3; radius: 2
            color: Theme.accent
            visible: root.active
            anchors.top: parent.top
            anchors.topMargin: 6
            anchors.horizontalCenter: parent.horizontalCenter
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 0

            // ---------- 行 1：导航 + 地址栏 ----------
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                spacing: 4

                ToolBtn {
                    iconName: "back"
                    toolEnabled: root.panel ? root.panel.canGoBack : false
                    onClicked: root.panel.goBack()
                }
                ToolBtn {
                    iconName: "forward"
                    toolEnabled: root.panel ? root.panel.canGoForward : false
                    onClicked: root.panel.goForward()
                }
                ToolBtn {
                    iconName: "up"
                    toolEnabled: root.panel ? (root.panel.cwd !== "file:///") : false
                    onClicked: root.panel.goUp()
                }
                ToolBtn { iconName: "home"; onClicked: root.panel.goHome() }
                ToolBtn { iconName: "refresh"; onClicked: root.panel.refresh() }

                Item { width: 4; height: 1 }

                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    Field {
                        id: addressField
                        anchors.fill: parent
                        leftPadding: 10
                        font.pixelSize: 12
                        font.weight: root.active ? Font.DemiBold : Font.Normal
                        color: root.active ? Theme.accent : Theme.text
                        placeholderText: "输入路径后回车…"
                        onAccepted: { root.panel.openUri(text); focus = false }
                        onActiveFocusChanged: root.addressFocused = activeFocus
                    }
                }
            }

            // ---------- 行 2：过滤 ----------
            // 「显示隐藏文件」已挪进全局设置面板（唯一一处开关，两栏同步），
            // 这行只剩过滤框，于是让它吃满整行，不再留右侧空档。
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                Layout.topMargin: 6
                spacing: 6

                Item {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 120
                    Layout.preferredHeight: 30
                    Field {
                        id: filterField
                        anchors.fill: parent
                        leftPadding: 28
                        font.pixelSize: 12
                        placeholderText: "过滤当前目录…"
                        onTextChanged: root.panel.applyFilter(text)
                        onActiveFocusChanged: root.filterFocused = activeFocus
                    }
                    Icon {
                        x: 9
                        anchors.verticalCenter: parent.verticalCenter
                        width: 13; height: 13
                        name: "search"
                        color: filterField.activeFocus ? Theme.accent : Theme.textMuted
                    }
                }
            }

            // ---------- 响应式列头（列宽从 root.cols 取，不用 grid.width / 列数） ----------
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 26
                Layout.topMargin: 8

                Row {
                    anchors.fill: parent
                    spacing: root.colGap
                    Repeater {
                        model: root.cols
                        delegate: Rectangle {
                            id: headerCell
                            readonly property bool isName: modelData.key === "name"
                            readonly property bool sorted: root.panel &&
                                root.panel.sortColumn === modelData.sortCol
                            width: modelData.w
                            height: 26
                            color: "transparent"
                            Text {
                                anchors.left: parent.left
                                anchors.leftMargin: headerCell.isName ? 18 : 0
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - (headerCell.isName ? 18 : 0) - 4
                                // 隐藏列后 index 不再等于排序列，必须用 sortCol 映射
                                text: modelData.label + (headerCell.sorted
                                      ? (root.panel.sortDesc ? " ↓" : " ↑") : "")
                                font.pixelSize: 11
                                font.weight: headerCell.sorted ? Font.DemiBold : Font.Normal
                                color: headerCell.sorted ? Theme.accent : Theme.textMuted
                                elide: Text.ElideRight
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { root.activated(); root.panel.sortBy(modelData.sortCol) }
                            }
                        }
                    }
                }
            }

            // ---------- 列表 + 三态覆盖层 ----------
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                ListView {
                    id: list
                    objectName: "fmList"
                    anchors.fill: parent
                    clip: true
                    reuseItems: true
                    focus: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: root.panel ? root.panel.listModel : null
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        id: rowBox
                        width: ListView.view.width
                        height: root.rowH
                        radius: 6
                        // 剪切中的项半透明：一眼看出「待会儿会被移走的是这几项」
                        opacity: model.isCut ? 0.45 : 1.0
                        color: model.selected ? Theme.accentSoft
                               : (rowMouse.containsMouse ? Theme.line : "transparent")

                        Rectangle {
                            x: 0; y: 10; width: 12; height: 12; radius: 3
                            color: Theme.kindColor(model.isDir ? "folder" : model.kind)
                        }
                        Text {
                            visible: root.renamingVpath !== model.vpath
                            x: 18; y: 0
                            width: Math.max(0, root.wOf("name") - 18)
                            height: root.rowH
                            verticalAlignment: Text.AlignVCenter
                            text: model.name
                            font.pixelSize: 12
                            font.weight: model.isDir ? Font.Medium : Font.Normal
                            color: model.selected ? Theme.accent : Theme.text
                            elide: Text.ElideRight
                        }

                        // 行内重命名：白底 + 1px 蓝框。Enter 提交、Esc 取消、失焦提交。
                        // 软渲染约束里只禁 Layout/Canvas/动画/Icon，TextInput 可以用；
                        // 只在正在改名的那一行创建（visible 绑定），平时零开销。
                        Rectangle {
                            x: 14
                            width: Math.max(80, root.wOf("name") - 14)
                            height: 24
                            anchors.verticalCenter: parent.verticalCenter
                            radius: 6
                            color: Theme.surface
                            border.width: 1
                            border.color: Theme.accent
                            visible: root.renamingVpath === model.vpath
                            z: 3

                            TextInput {
                                id: nameEdit
                                objectName: "fmRenameEdit"
                                anchors.fill: parent
                                anchors.leftMargin: 5
                                anchors.rightMargin: 5
                                verticalAlignment: TextInput.AlignVCenter
                                font.pixelSize: 12
                                color: Theme.text
                                clip: true
                                selectByMouse: true

                                // 有没有真正拿到过焦点。列表刷新会让输入框「假失焦」
                                // （委托刚建出来就被 ListView 拿走焦点），那种失焦不能算提交，
                                // 否则新建文件夹后刚进改名就被自动提交掉，人还没输一个字。
                                property bool heldFocus: false

                                // text 不能绑 renameDraft：用户一输入绑定就断了，
                                // 下一次改名会填进旧名字。改成「每次进入编辑时赋值」。
                                function startEditing() {
                                    text = root.renameDraft
                                    heldFocus = false
                                    forceActiveFocus()
                                    // 预选主干不选扩展名（.bashrc 这种整体选）
                                    var dot = text.lastIndexOf(".")
                                    if (dot > 0) select(0, dot)
                                    else selectAll()
                                }

                                onVisibleChanged: if (visible) startEditing()
                                Component.onCompleted: if (visible) startEditing()

                                onTextChanged: {
                                    if (root.renamingVpath === model.vpath)
                                        root.renameDraft = text
                                }
                                // 回车必须在这里吃掉再提交。TextInput 的 onAccepted 不会把
                                // 事件标成 accepted，回车会继续冒泡到列表的 Keys.onPressed，
                                // 那边把当前项「打开」——表现是改完名顺手弹出预览窗。
                                Keys.onReturnPressed: function(event) {
                                    event.accepted = true
                                    root.commitRename()
                                }
                                Keys.onEnterPressed: function(event) {
                                    event.accepted = true
                                    root.commitRename()
                                }
                                Keys.onEscapePressed: function(event) {
                                    event.accepted = true
                                    root.cancelRename()
                                }
                                // 点到别处（失焦）也算提交，跟桌面文件管理器一致。
                                // 但只有「拿到过焦点再失焦」才算 —— 见 heldFocus 的注释。
                                onActiveFocusChanged: {
                                    if (activeFocus) {
                                        heldFocus = true
                                        return
                                    }
                                    if (!heldFocus)
                                        return
                                    if (root.renamingVpath === model.vpath)
                                        root.commitRename()
                                }
                            }
                        }
                        Text {
                            x: root.xOf("size"); y: 0
                            width: root.wOf("size"); height: root.rowH
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: Text.AlignRight
                            text: model.sizeText
                            font.pixelSize: 11
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                        Text {
                            x: root.xOf("kind"); y: 0
                            width: root.wOf("kind"); height: root.rowH
                            visible: root.showKind
                            verticalAlignment: Text.AlignVCenter
                            text: root.labelOf(model.kind)
                            font.pixelSize: 11
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                        Text {
                            x: root.xOf("time"); y: 0
                            width: root.wOf("time"); height: root.rowH
                            visible: root.showTime
                            verticalAlignment: Text.AlignVCenter
                            text: model.timeText
                            font.pixelSize: 11
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }

                        MouseArea {
                            id: rowMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            acceptedButtons: Qt.LeftButton
                            onClicked: function(mouse) {
                                root.activated()
                                var mode = "single"
                                if (mouse.modifiers & Qt.ControlModifier) mode = "toggle"
                                else if (mouse.modifiers & Qt.ShiftModifier) mode = "range"
                                root.panel.select(model.vpath, mode)
                                root.currentVpath = model.vpath
                                list.currentIndex = index
                                list.forceActiveFocus()
                            }
                            onDoubleClicked: function(mouse) {
                                root.panel.select(model.vpath, "single")
                                root.currentVpath = model.vpath
                                root.panel.activate(model.vpath)
                            }
                        }
                    }

                    Keys.onPressed: function(event) {
                        // 页面不拥有键盘（切到别的模块 / 设置面板展开）时一个键都不接。
                        // 这是兜底：正常情况下 keysEnabled 变化会把 list.focus 放掉。
                        if (!root.keysEnabled) return
                        // 浮窗打开时所有键交给页面级预览 Shortcut，
                        // 否则 ↑↓ / 空格 / 回车会同时改列表选中或导航目录。
                        if (files.preview.open) return
                        var delta = 0
                        var next = 0
                        var vp = ""
                        if (event.key === Qt.Key_Up) delta = -1
                        else if (event.key === Qt.Key_Down) delta = 1
                        if (delta !== 0) {
                            next = Math.max(0, Math.min(list.count - 1, list.currentIndex + delta))
                            list.currentIndex = next
                            list.positionViewAtIndex(next, ListView.Contain)
                            vp = root.panel.vpathAt(next)
                            if (vp !== "") {
                                root.panel.select(vp, "single")
                                root.currentVpath = vp
                            }
                            event.accepted = true
                            return
                        }
                        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                && !(event.modifiers & Qt.ControlModifier)) {
                            if (root.currentVpath !== "") root.panel.activate(root.currentVpath)
                            event.accepted = true
                        } else if (event.key === Qt.Key_Backspace) {
                            root.panel.goUp(); event.accepted = true
                        } else if (event.key === Qt.Key_Space) {
                            // M1b：空格打开当前选中项的浮窗预览（文件夹也可看摘要）。
                            // 先同步激活面板，否则浮窗里按 ↑↓ 会去另一端面板找相邻项。
                            root.activated()
                            if (files.preview.available && root.currentVpath !== "")
                                files.preview.request(root.currentVpath)
                            event.accepted = true
                        }
                    }
                }

                // 右键统一走这一层（只接右键，左键照样穿到 delegate）。
                // 为什么不在 delegate 里接：空白处没有 delegate，而 ListView 本身不吃右键，
                // 同级的 MouseArea 又收不到冒泡 —— 只能盖一层，再用 indexAt 区分行与空白。
                MouseArea {
                    anchors.fill: parent
                    z: 20
                    acceptedButtons: Qt.RightButton
                    onPressed: function(mouse) {
                        // indexAt 吃的是**内容坐标**，而 mouse.x/y 是视口坐标：列表滚过
                        // 之后两者差一个 contentY，直接喂会让命中行整体上移
                        // contentY/rowH 行（麒麟机上的表现：右键第 14 个，复制到的是
                        // 第 8、9 个）。先映射到 contentItem 再取行号。
                        var pos = mapToItem(list.contentItem, mouse.x, mouse.y)
                        var row = list.indexAt(pos.x, pos.y)
                        var p = mapToItem(null, mouse.x, mouse.y)
                        if (row >= 0) {
                            var vp = root.panel ? root.panel.vpathAt(row) : ""
                            if (vp !== "") {
                                if (!root.isSelected(vp))
                                    root.panel.select(vp, "single")
                                root.currentVpath = vp
                                list.currentIndex = row
                                root.showMenu("item", p.x, p.y)
                                return
                            }
                        }
                        root.showMenu("empty", p.x, p.y)
                    }
                }

                // 加载中（静态文字 + 几何进度条，不用动画 / BusyIndicator）
                Rectangle {
                    anchors.fill: parent
                    z: 5
                    visible: root.panel ? root.panel.busy : false
                    color: Theme.surface
                    enabled: false
                    Text {
                        anchors.centerIn: parent
                        text: "正在读取…"
                        font.pixelSize: 12
                        color: Theme.textMuted
                    }
                    Rectangle {
                        anchors.top: parent.top
                        width: parent.width * (files.progress / 100)
                        height: 2
                        color: Theme.accent
                    }
                }

                // 空目录
                Rectangle {
                    anchors.fill: parent
                    z: 5
                    color: "transparent"
                    enabled: false
                    visible: root.panel && !root.panel.busy &&
                             !root.isError(root.panel.status) && root.panel.visibleCount === 0
                    Text {
                        anchors.centerIn: parent
                        text: "此文件夹为空"
                        font.pixelSize: 12
                        color: Theme.textMuted
                    }
                }

                // 权限不足 / 路径错误（用普通 Rectangle，不用 Popup）
                Rectangle {
                    anchors.fill: parent
                    z: 5
                    color: "transparent"
                    enabled: false
                    visible: root.panel && !root.panel.busy && root.isError(root.panel.status)
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 24
                        horizontalAlignment: Text.AlignHCenter
                        text: root.panel ? root.panel.status : ""
                        font.pixelSize: 12
                        color: Theme.danger
                        wrapMode: Text.Wrap
                    }
                }
            }

            // ---------- 选中项信息条 ----------
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                color: "transparent"
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.line }
                RowLayout {
                    id: infoRow
                    objectName: "fmInfoRow"
                    anchors.fill: parent
                    spacing: 8
                    // 名称吃掉剩余宽度并在窄面板下自动收缩，后三列按内容定宽：
                    // 这样整行永远排在卡片内，不会把「修改时间」挤出框外。
                    Text {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 72
                        Layout.maximumWidth: 200
                        elide: Text.ElideRight
                        text: (root.panel && root.panel.info.name) ? root.panel.info.name : "未选中文件"
                        font.pixelSize: 12
                        color: Theme.text
                    }
                    Text {
                        text: (root.panel && root.panel.info.kindLabel) ? root.panel.info.kindLabel : ""
                        font.pixelSize: 11; color: Theme.textMuted
                    }
                    Text {
                        text: (root.panel && root.panel.info.sizeText) ? root.panel.info.sizeText : ""
                        font.pixelSize: 11; color: Theme.textMuted
                    }
                    Text {
                        text: (root.panel && root.panel.info.timeText) ? root.panel.info.timeText : ""
                        font.pixelSize: 11; color: Theme.textMuted
                    }
                    // 路径不再放在这里：上方地址栏已是完整路径，重复一份既占宽度，
                    // 又会在窄面板下把整行撑出卡片。info.display 保留供 M1b 预览标题用。
                }
            }

            // ---------- 状态栏 ----------
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 26
                color: "transparent"
                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.line }
                RowLayout {
                    anchors.fill: parent
                    spacing: 6
                    Rectangle {
                        width: 6; height: 6; radius: 3
                        color: root.active ? Theme.accent : Theme.line
                    }
                    Text {
                        text: root.statusText()
                        font.pixelSize: 11
                        color: Theme.textMuted
                    }
                    Item { Layout.fillWidth: true }
                    Btn {
                        visible: root.panel ? root.isError(root.panel.status) : false
                        text: "返回上级"
                        ghost: true
                        boxHeight: 20
                        onClicked: root.panel.goUp()
                    }
                    Text {
                        // 开关本体在全局设置面板里，这行只做「当前是否显示」的状态回显，
                        // 顺手把快捷键写出来（Ctrl+H 也能切，效果同样全局）。
                        text: (files.showHidden ? "显示隐藏 · Ctrl+H" : "隐藏已收起 · Ctrl+H")
                              + " · F5 刷新"
                        font.pixelSize: 11
                        color: files.showHidden ? Theme.accent : Theme.textMuted
                    }
                }
            }
        }
    }

    Connections {
        target: root.panel
        function onCwdChanged() { addressField.text = root.panel.displayPath }
        // 写操作完成后面板会请求「选中并定位到这一项」（粘贴结果、新建的文件夹）
        function onFocusRequested(vp, row) {
            if (!root.keysEnabled)
                return
            root.currentVpath = vp
            list.currentIndex = row
            list.positionViewAtIndex(row, ListView.Contain)
            // 新建目录：这一轮刷新已经把行摆好了，此刻再进改名，编辑框建出来
            // 就不会再被重建打断。
            if (root.pendingNewFolder !== "" && vp === root.pendingNewFolder) {
                var fresh = root.pendingNewFolder
                root.pendingNewFolder = ""
                root.beginRename(fresh)
                return
            }
            // 正在行内改名时不能抢焦点，否则编辑焦点被顶掉、当成失焦提交
            if (root.renamingVpath !== "")
                return
            list.forceActiveFocus()
        }
    }
    // toast 由 FileManagerPage.qml 页面级单点接住，两个面板实例不各弹一次。
    Component.onCompleted: addressField.text = root.panel.displayPath
}
