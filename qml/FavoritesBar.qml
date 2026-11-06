import QtQuick
import QtQuick.Controls

// 页面内常驻收藏栏：常用位置（只读）+ 收藏夹（可增删）。
// 点击作用于「当前激活面板」files.active()，与旧版 _goto_location 一致。
// 硬约束：Property(list) 不能直接当 Repeater model —— 用 property var + Connections
// 整体重新赋值；悬停态用普通 Rectangle，不用 Popup（Confirm 是唯一例外）。
// delegate 零 Layout / 零动画，软渲染友好。收藏项多了靠 Flickable 滚动。
Item {
    id: bar
    objectName: "favoritesBar"

    property string activeVpath: ""      // 由 FileManagerPage 传入（依赖 notify property）
    property var locModel: []
    property var favModel: []
    property string pendingVpath: ""

    readonly property int rowH: 30
    readonly property int padX: 12

    // 只读渲染探针：离屏验证委托确实被创建、宽度确实跟着收藏栏走
    readonly property int locCount: locRepeater.count
    readonly property int favCount: favRepeater.count
    readonly property real firstRowWidth:
        locRepeater.count > 0 && locRepeater.itemAt(0) ? locRepeater.itemAt(0).width : 0
    readonly property real contentH: col.implicitHeight

    Connections {
        target: files
        function onFavoritesChanged() { bar.favModel = files.favorites }
    }
    Component.onCompleted: {
        bar.locModel = files.standardLocations()
        bar.favModel = files.favorites
    }

    // 一行的公共外观：选中 / 悬停 / 图标 / 文字 / 可选移除按钮
    component RowItem: Item {
        id: row
        objectName: "favRow"
        width: col.width
        height: bar.rowH

        property var item: null
        property string iconName: "folder"
        property bool isCurrent: false
        property bool removable: false
        property string itemVpath: ""

        signal chosen(string vp)
        signal removeRequested(string vp, var btn)

        readonly property bool missing: row.item ? !!row.item.missing : false

        Rectangle {
            anchors.fill: parent
            radius: 7
            color: row.isCurrent ? Theme.accentSoft
                 : (rowMouse.containsMouse ? Theme.line : "transparent")
        }
        Icon {
            x: 8
            anchors.verticalCenter: parent.verticalCenter
            width: 14; height: 14
            name: row.iconName
            color: row.missing ? Theme.textMuted
                 : (row.isCurrent ? Theme.accent : Theme.textMuted)
        }
        Text {
            x: 28
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(20, parent.width - 28 - (row.removable ? 24 : 8))
            text: row.item ? row.item.name : ""
            font.pixelSize: 12
            color: row.missing ? Theme.textMuted : Theme.text
            elide: Text.ElideRight
        }
        Rectangle {
            id: removeBtn
            x: parent.width - 22
            anchors.verticalCenter: parent.verticalCenter
            width: 18; height: 18; radius: 5
            z: 2
            visible: row.removable &&
                     (rowMouse.containsMouse || removeMouse.containsMouse)
            color: removeMouse.containsMouse ? Theme.danger : "transparent"
            Icon {
                anchors.centerIn: parent
                width: 11; height: 11
                name: "close"
                color: removeMouse.containsMouse ? "white" : Theme.textMuted
            }
            MouseArea {
                id: removeMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: row.removeRequested(row.itemVpath, removeBtn)
            }
        }
        MouseArea {
            id: rowMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            acceptedButtons: Qt.LeftButton
            onClicked: row.chosen(row.itemVpath)
        }
    }

    Card {
        anchors.fill: parent
        fillColor: Theme.surface

        Flickable {
            id: flick
            objectName: "favFlick"
            anchors.fill: parent
            anchors.margins: bar.padX
            clip: true
            contentWidth: width
            contentHeight: col.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            Column {
                id: col
                objectName: "favCol"
                width: flick.width
                spacing: 4

                Text {
                    width: col.width
                    text: "常用位置"
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    color: Theme.textMuted
                }

                Repeater {
                    id: locRepeater
                    model: bar.locModel
                    RowItem {
                        item: modelData
                        iconName: "folder"
                        itemVpath: modelData.vpath
                        isCurrent: modelData.vpath === bar.activeVpath
                        onChosen: function(vp) {
                            if (vp) files.active().openUri(vp)
                        }
                    }
                }

                Item { width: col.width; height: 8 }

                Item {
                    width: col.width
                    height: 26
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "收藏夹"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        color: Theme.textMuted
                    }
                    Rectangle {
                        id: addBtn
                        width: 24; height: 22; radius: 6
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        color: addMouse.containsMouse ? Theme.accentSoft : "transparent"
                        Icon {
                            anchors.centerIn: parent
                            width: 12; height: 12
                            name: "plus"
                            color: addMouse.containsMouse ? Theme.accent : Theme.textMuted
                        }
                        MouseArea {
                            id: addMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: files.addFavorite()
                        }
                    }
                }

                Text {
                    width: col.width
                    visible: bar.favModel.length === 0
                    text: "暂无收藏 · 点 ＋ 添加当前目录"
                    font.pixelSize: 11
                    color: Theme.textMuted
                    wrapMode: Text.Wrap
                }

                Repeater {
                    id: favRepeater
                    model: bar.favModel
                    RowItem {
                        item: modelData
                        iconName: "star"
                        removable: true
                        itemVpath: modelData.vpath
                        isCurrent: modelData.vpath === bar.activeVpath
                        onChosen: function(vp) {
                            if (!vp) return
                            if (modelData.missing) {
                                Toast.show("该收藏路径已不存在，可点 × 移除")
                                return
                            }
                            files.active().openUri(vp)
                        }
                        onRemoveRequested: function(vp, btn) {
                            bar.pendingVpath = vp
                            removeConfirm.hint = modelData.display
                            removeConfirm.openAt(btn)
                        }
                    }
                }
            }
        }
    }

    // 单例移除确认：Confirm 是 Popup，属硬约束例外；不每条收藏建一个。
    // hint 顺带展示完整路径（不做 tooltip，ToolTip 本质也是 Popup）。
    Confirm {
        id: removeConfirm
        message: "从收藏夹移除？"
        hint: ""
        okText: "移除"
        cancelText: "取消"
        onAccepted: files.removeFavorite(bar.pendingVpath)
    }
}
