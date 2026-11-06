import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    property string selectedPath: ""
    property var scriptModel: []

    Component.onCompleted: root.scriptModel = scripts.scripts

    Connections {
        target: scripts
        function onScriptsChanged() {
            root.scriptModel = scripts.scripts
        }
    }

    function currentName() {
        for (var i = 0; i < root.scriptModel.length; i++)
            if (root.scriptModel[i].path === root.selectedPath)
                return root.scriptModel[i].name
        return "输出"
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: Theme.pad

        // ---------- 脚本列表 ----------
        Card {
            Layout.preferredWidth: 292
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "脚本目录"
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Item { Layout.fillWidth: true }
                    Btn {
                        text: "刷新"
                        ghost: true
                        boxHeight: 26
                        leftPadding: 10
                        rightPadding: 10
                        onClicked: scripts.rescan()
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: "把 .py / .sh 丢进\n~/.config/workspace/scripts/\n双击列表项即可运行"
                    color: Theme.textMuted
                    font.pixelSize: 10
                    lineHeight: 1.3
                }

                ListView {
                    id: scriptList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 6
                    model: root.scriptModel

                    delegate: Rectangle {
                        width: scriptList.width
                        height: scriptCol.implicitHeight + 18
                        radius: 9
                        color: root.selectedPath === modelData.path
                               ? Theme.accentSoft
                               : (scriptMouse.containsMouse ? "#F5F6FA" : "#F7F8FB")

                        ColumnLayout {
                            id: scriptCol
                            x: 11
                            y: 9
                            width: parent.width - 22
                            spacing: 2

                            Text {
                                text: modelData.name
                                font.pixelSize: 12
                                font.weight: Font.Medium
                                color: root.selectedPath === modelData.path ? Theme.accent : Theme.text
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                visible: modelData.desc !== ""
                                text: modelData.desc
                                font.pixelSize: 11
                                color: Theme.textMuted
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        MouseArea {
                            id: scriptMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.selectedPath = modelData.path
                            onDoubleClicked: {
                                root.selectedPath = modelData.path
                                scripts.run(modelData.path)
                            }
                        }
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: scripts.scripts.length === 0
                        text: "目录里还没有脚本"
                        color: Theme.textMuted
                        font.pixelSize: 12
                    }
                }
            }
        }

        // ---------- 运行输出 ----------
        Card {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: root.currentName()
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Item { Layout.fillWidth: true }
                    Btn {
                        text: "运行"
                        primary: true
                        boxHeight: 30
                        enabled: root.selectedPath !== "" && !scripts.running
                        onClicked: {
                            out.clear()
                            scripts.run(root.selectedPath)
                        }
                    }
                    Btn {
                        text: "停止"
                        boxHeight: 30
                        enabled: scripts.running
                        onClicked: scripts.stop()
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    radius: 10
                    color: "#1E222B"

                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 12

                        TextArea {
                            id: out
                            readOnly: true
                            wrapMode: TextArea.Wrap
                            color: "#D6E2FF"
                            font.family: "Menlo"
                            font.pixelSize: 11
                            background: null
                        }
                    }
                }
            }
        }
    }

    Connections {
        target: scripts
        function onOutputArrived(text) { out.append(text) }
    }
}
