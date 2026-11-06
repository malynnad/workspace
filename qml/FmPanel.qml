import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 单面板：两行工具栏 + 四列列头 + ListView + 信息条 + 状态栏 + 三态覆盖层。
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
    signal activated()

    readonly property int colName: 223
    readonly property int colSize: 64
    readonly property int colKind: 60
    readonly property int colTime: 96
    readonly property int colGap: 4
    readonly property int rowH: 32

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

            // ---------- 行 2：过滤 + 隐藏文件 ----------
            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 30
                Layout.topMargin: 6
                spacing: 6

                Item {
                    Layout.preferredWidth: 200
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
                Btn {
                    Layout.preferredWidth: 84
                    text: root.panel && root.panel.showHidden ? "隐藏: 开" : "隐藏: 关"
                    ghost: true
                    boxHeight: 26
                    onClicked: root.panel.toggleHidden()
                }
                Item { Layout.fillWidth: true }
            }

            // ---------- 四列列头（固定像素，不用 grid.width / 列数） ----------
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 26
                Layout.topMargin: 8

                Row {
                    anchors.fill: parent
                    spacing: root.colGap
                    Repeater {
                        model: ["名称", "大小", "类型", "修改时间"]
                        delegate: Rectangle {
                            width: index === 0 ? root.colName
                                  : (index === 1 ? root.colSize
                                  : (index === 2 ? root.colKind : root.colTime))
                            height: 26
                            color: "transparent"
                            Text {
                                anchors.left: parent.left
                                anchors.leftMargin: index === 0 ? 18 : 0
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - (index === 0 ? 18 : 0) - 4
                                text: modelData + ((root.panel && root.panel.sortColumn === index)
                                      ? (root.panel.sortDesc ? " ↓" : " ↑") : "")
                                font.pixelSize: 11
                                font.weight: (root.panel && root.panel.sortColumn === index)
                                             ? Font.DemiBold : Font.Normal
                                color: (root.panel && root.panel.sortColumn === index)
                                       ? Theme.accent : Theme.textMuted
                                elide: Text.ElideRight
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { root.activated(); root.panel.sortBy(index) }
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
                        color: model.selected ? Theme.accentSoft
                               : (rowMouse.containsMouse ? Theme.line : "transparent")

                        Rectangle {
                            x: 0; y: 10; width: 12; height: 12; radius: 3
                            color: model.isDir ? Theme.accent
                                   : (model.kind === "image" ? Theme.todo : Theme.line)
                        }
                        Text {
                            x: 18; y: 0
                            width: root.colName - 18
                            height: root.rowH
                            verticalAlignment: Text.AlignVCenter
                            text: model.name
                            font.pixelSize: 12
                            font.weight: model.isDir ? Font.Medium : Font.Normal
                            color: model.selected ? Theme.accent : Theme.text
                            elide: Text.ElideRight
                        }
                        Text {
                            x: root.colName + root.colGap; y: 0
                            width: root.colSize; height: root.rowH
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: Text.AlignRight
                            text: model.sizeText
                            font.pixelSize: 11
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                        Text {
                            x: root.colName + root.colSize + root.colGap * 2; y: 0
                            width: root.colKind; height: root.rowH
                            verticalAlignment: Text.AlignVCenter
                            text: root.labelOf(model.kind)
                            font.pixelSize: 11
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                        Text {
                            x: root.colName + root.colSize + root.colKind + root.colGap * 3; y: 0
                            width: root.colTime; height: root.rowH
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
                        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                            if (root.currentVpath !== "") root.panel.activate(root.currentVpath)
                            event.accepted = true
                        } else if (event.key === Qt.Key_Backspace) {
                            root.panel.goUp(); event.accepted = true
                        } else if (event.key === Qt.Key_Space) {
                            // M1b 打开预览能力后这里自动生效，M1a 按空格无任何 UI
                            if (files.preview.available && root.currentVpath !== "")
                                files.preview.request(root.currentVpath)
                            event.accepted = true
                        }
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
                    anchors.fill: parent
                    spacing: 8
                    Text {
                        Layout.preferredWidth: 116
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
                    Item { Layout.fillWidth: true }
                    Text {
                        Layout.preferredWidth: 120
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideMiddle
                        text: (root.panel && root.panel.info.display) ? root.panel.info.display : ""
                        font.pixelSize: 10; color: Theme.textMuted
                    }
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
                        text: (root.panel && root.panel.showHidden ? "隐藏: 开" : "隐藏: 关") + " · F5 刷新"
                        font.pixelSize: 11
                        color: Theme.textMuted
                    }
                }
            }
        }
    }

    Connections {
        target: root.panel
        function onCwdChanged() { addressField.text = root.panel.displayPath }
    }
    Connections {
        target: files
        function onToast(msg) { Toast.show(msg) }
    }
    Component.onCompleted: addressField.text = root.panel.displayPath
}
