import QtQuick

// 自绘右键菜单（不用 Popup）。
//
// 为什么不用 QtQuick.Controls 的 Menu/Popup：项目已经踩过两次 —— Popup 自带 focusScope，
// 会把 Esc 吃掉再冒泡到父链，导致「关菜单顺手把别的东西也关了」。这里用普通
// Item + 全屏透明遮罩，按键与点击完全由调用方掌控，也能保证样式走 Theme。
//
// 用法（挂在页面根，保证盖住两个面板）：
//     contextMenu.openAt(x, y, [
//         { label: "打开", shortcut: "Enter", action: "open" },
//         { separator: true },
//         { label: "删除", danger: true, enabled: false, action: "trash" }
//     ])
// header 项用来显示「对谁操作」，不可点。
//
// **action 是字符串 id，不是函数**：菜单项数组要经过 QML 信号与 var 属性传递，
// 函数会在 QVariant 转换里丢掉（点下去静默无反应），所以由调用方按 id 分派。
Item {
    id: root
    objectName: "contextMenu"
    visible: false
    z: 900

    property var items: []

    signal closed()
    signal picked(string actionId)

    readonly property int rowH: 30
    readonly property int sepH: 9
    readonly property int headH: 26
    readonly property int menuW: 218

    function itemHeight(data) {
        if (!data)
            return 0
        if (data.separator)
            return root.sepH
        if (data.header)
            return root.headH
        return root.rowH
    }

    function contentHeight(list) {
        var h = 12       // 上下 padding
        for (var i = 0; i < list.length; ++i)
            h += root.itemHeight(list[i])
        return h
    }

    // px/py 是相对 root 的坐标（页面自己换算）。超出右/下边界时往里翻，
    // 保证右键点窗口右下角时菜单也能完整显示。
    function openAt(px, py, list) {
        var data = list || []
        if (data.length === 0)
            return
        root.items = data
        var h = root.contentHeight(data)
        var x = px
        var y = py
        if (x + root.menuW > root.width - 8)
            x = Math.max(8, root.width - root.menuW - 8)
        if (y + h > root.height - 8)
            y = Math.max(8, root.height - h - 8)
        menu.x = Math.round(x)
        menu.y = Math.round(y)
        menu.height = h
        root.visible = true
    }

    function close() {
        if (!root.visible)
            return
        root.visible = false
        root.items = []
        root.closed()
    }

    // 点外面 / 点右键都收起（右键在别处按下时，调用方会紧接着 openAt 新菜单）
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        onPressed: root.close()
        onWheel: root.close()
    }

    Rectangle {
        id: menu
        objectName: "contextMenuCard"
        width: root.menuW
        radius: 10
        color: Theme.surface
        border.width: 1
        border.color: "#E7E9F1"

        // 双层假投影，和 Card 一个套路（不用 DropShadow，软渲染下 effect 很贵）
        Rectangle {
            anchors.fill: parent
            anchors.topMargin: 3
            anchors.leftMargin: 1
            anchors.rightMargin: -1
            radius: 11
            color: "#12000000"
            z: -1
        }

        // 卡片本身也要吃掉点击：Rectangle 不吃鼠标事件，点在内边距（行之间的空白、
        // 上下 6px）上会穿透到下面的遮罩，菜单「点一下自己就关了」。放在 Column 之前，
        // 行的 MouseArea 仍然在上面。
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
            onWheel: root.close()
        }

        Column {
            id: itemsColumn
            x: 6
            y: 6
            width: parent.width - 12
            spacing: 0

            Repeater {
                model: root.items

                delegate: Item {
                    id: row
                    objectName: "ctxRow"
                    width: itemsColumn.width
                    height: root.itemHeight(modelData)

                    // 分隔线
                    Rectangle {
                        visible: !!(modelData && modelData.separator)
                        anchors.centerIn: parent
                        width: parent.width - 6
                        height: 1
                        color: Theme.line
                    }

                    // 小标题（「对谁操作」）
                    Text {
                        visible: !!(modelData && modelData.header)
                        anchors.fill: parent
                        anchors.leftMargin: 4
                        verticalAlignment: Text.AlignVCenter
                        text: (modelData && modelData.label) ? modelData.label : ""
                        font.pixelSize: 11
                        color: Theme.textMuted
                        elide: Text.ElideMiddle
                    }

                    readonly property bool clickable: !!modelData && !modelData.separator &&
                        !modelData.header && modelData.enabled !== false && !!modelData.action

                    Rectangle {
                        anchors.fill: parent
                        radius: 6
                        color: row.clickable && rowMouse.containsMouse ? Theme.accentSoft
                                                                      : "transparent"
                    }

                    Text {
                        visible: !!(modelData && !modelData.separator && !modelData.header)
                        anchors.left: parent.left
                        anchors.leftMargin: 8
                        anchors.right: keyText.left
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData && modelData.label ? modelData.label : ""
                        font.pixelSize: 12
                        color: {
                            if (!modelData)
                                return Theme.textMuted
                            if (modelData.enabled === false)
                                return Theme.textMuted
                            return modelData.danger ? Theme.danger : Theme.text
                        }
                        elide: Text.ElideRight
                    }

                    Text {
                        id: keyText
                        visible: !!(modelData && !modelData.separator && !modelData.header &&
                                    modelData.shortcut)
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: (modelData && modelData.shortcut) ? modelData.shortcut : ""
                        font.pixelSize: 10
                        color: Theme.textMuted
                    }

                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: row.clickable
                        cursorShape: row.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            var act = modelData ? modelData.action : ""
                            // 先 emit 再 close：close() 会清空 items，把当前这个 delegate
                            // 一起销毁，销毁后继续往下走会被打断（实测点了没反应）。
                            if (act)
                                root.picked(act)
                            root.close()
                        }
                    }
                }
            }
        }
    }

    // 点/滚外面关闭；窗口失焦也关掉（避免菜单挂在已失活的窗口上）
    Connections {
        target: root.Window.window
        ignoreUnknownSignals: true
        function onActiveChanged() {
            if (!root.Window.window.active) root.close()
        }
    }
}
