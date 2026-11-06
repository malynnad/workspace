import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    Card {
        anchors.centerIn: parent
        width: 440
        height: 236

        ColumnLayout {
            anchors.centerIn: parent
            spacing: 14

            Rectangle {
                width: 56
                height: 56
                radius: 16
                color: Theme.accentSoft
                Layout.alignment: Qt.AlignHCenter

                Icon {
                    anchors.centerIn: parent
                    width: 24
                    height: 24
                    name: "folder"
                    color: Theme.accent
                }
            }

            Text {
                text: "文件管理器 · 建设中"
                font.pixelSize: 15
                font.weight: Font.DemiBold
                color: Theme.text
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: "双面板 + Quick Look 预览 + 插件体系\nPyQt5 版的 FileSource / 预览器协议将整体迁入"
                horizontalAlignment: Text.AlignHCenter
                color: Theme.textMuted
                font.pixelSize: 11
                lineHeight: 1.4
                Layout.alignment: Qt.AlignHCenter
            }
        }
    }
}
