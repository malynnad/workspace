import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root

    property var todoModel: []
    property int pendingDeleteId: -1

    Component.onCompleted: todoModel = backend.todos

    Connections {
        target: backend
        function onTodosChanged() {
            root.todoModel = backend.todos
        }
    }

    function addTodo() {
        var t = newTodo.text.trim()
        if (t === "")
            return
        backend.addTodo(t, dueField.text.trim())
        newTodo.text = ""
        dueField.text = ""
        Toast.show("待办已添加")
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: 14

        Card {
            Layout.fillWidth: true
            Layout.preferredHeight: 100

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "待办事项"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Text {
                        text: "未完成 " + backend.openCount + " 项"
                        color: Theme.textMuted
                        font.pixelSize: 11
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: "从日志条目一键转换的待办会带蓝色标记"
                        color: Theme.textMuted
                        font.pixelSize: 11
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Field {
                        id: newTodo
                        Layout.fillWidth: true
                        placeholderText: "添加新待办…"
                        onAccepted: root.addTodo()
                    }
                    Field {
                        id: dueField
                        Layout.preferredWidth: 170
                        placeholderText: "到期 yyyy-MM-dd"
                    }
                    Btn {
                        text: "添加"
                        primary: true
                        onClicked: root.addTodo()
                    }
                }
            }
        }

        ListView {
            id: todoList
            objectName: "todoList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            model: root.todoModel

            delegate: Rectangle {
                width: todoList.width
                height: 52
                radius: 10
                color: Theme.surface
                border.width: 1
                border.color: Theme.line

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 10
                    spacing: 12

                    Check {
                        checked: modelData.status === "done"
                        Layout.alignment: Qt.AlignVCenter
                        onToggled: backend.toggleTodo(modelData.id)
                    }

                    Text {
                        Layout.fillWidth: true
                        text: modelData.content
                        font.pixelSize: 13
                        font.strikeout: modelData.status === "done"
                        color: modelData.status === "done" ? Theme.textMuted : Theme.text
                        elide: Text.ElideRight
                    }

                    Rectangle {
                        visible: modelData.source_entry_id !== null
                        radius: 7
                        color: Theme.accentSoft
                        width: srcLabel.implicitWidth + 14
                        height: 20
                        Text {
                            id: srcLabel
                            anchors.centerIn: parent
                            text: "来自日志"
                            font.pixelSize: 10
                            color: Theme.accent
                        }
                    }

                    Text {
                        visible: !!modelData.due_date
                        text: modelData.due_date || ""
                        color: Theme.warn
                        font.pixelSize: 11
                    }

                    Btn {
                        id: delTodoBtn
                        text: "✕"
                        ghost: true
                        boxHeight: 26
                        leftPadding: 8
                        rightPadding: 8
                        onClicked: {
                            root.pendingDeleteId = modelData.id
                            delTodoConfirm.openAt(delTodoBtn)
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: root.todoModel.length === 0
                text: "没有待办。\n从日志条目一键转换，或在上面直接添加。"
                horizontalAlignment: Text.AlignHCenter
                color: Theme.textMuted
                font.pixelSize: 12
            }
        }
    }

    Confirm {
        id: delTodoConfirm
        message: "删除这条待办？"
        hint: "删除后无法恢复"
        onAccepted: {
            if (root.pendingDeleteId !== -1) {
                backend.deleteTodo(root.pendingDeleteId)
                root.pendingDeleteId = -1
                Toast.show("待办已删除")
            }
        }
    }
}
