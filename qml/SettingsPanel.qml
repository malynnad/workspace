import QtQuick
import QtQuick.Controls

// 全局设置面板（唯一一处）。挂在主窗口左下角齿轮上，覆盖整个窗口。
//
// 为什么全应用只有一个：设置是「一次改、到处生效」的东西，散在各个页面里必然
// 出现两份状态。文件浏览的「显示隐藏文件」、顶栏时钟的上下班时间、预览的清晰度
// 现在都从 files（FileManagerBackend）读同一份，改哪都是改这一份。
//
// 不用 Popup：Popup 会建自己的 focusScope（父链在 Overlay 层），Esc 被它按
// closePolicy 吃掉后同一事件还会冒泡到父项 Keyboard 处理链，出现「面板关了、
// 底下的东西也被关了」。预览窗那次踩过，这里沿用普通 Rectangle + 全窗遮罩。
Item {
    id: root
    objectName: "settingsLayer"
    visible: false
    z: 80

    property int panelWidth: 300
    signal closed()

    // 以某个控件的左下角为锚点弹到它上方（左下角齿轮的常规弹出方向）。
    // 坐标一律走 mapToItem：齿轮在侧栏 ColumnLayout 里，直接拿 parent 会算错。
    function openAtItem(item) {
        var p = item.mapToItem(root, 0, 0)
        // 横向优先落在齿轮右侧：左侧栏露出来，切模块还能点；只有窗口窄到放不下
        // 才退回贴左（左侧栏 214 宽，最小窗口 1040 一定放得下，这里只是兜底）。
        var preferX = p.x + item.width + 8
        panel.x = (preferX + panel.width + 8 <= root.width)
                  ? preferX
                  : Math.max(8, Math.min(p.x, root.width - panel.width - 8))
        panel.y = Math.max(8, Math.min(p.y - panel.height - 8,
                                       root.height - panel.height - 8))
        syncTimeFields()
        root.visible = true
    }

    function close() {
        if (!root.visible)
            return
        root.visible = false
        root.closed()
    }

    function toggleAtItem(item) {
        if (root.visible)
            close()
        else
            openAtItem(item)
    }

    // 时间框是命令式的（不做 text 绑定）：用户编辑时若被绑定回写会跳光标。
    // 所以只在打开面板与提交之后显式同步。
    function syncTimeFields() {
        startField.text = files.workSchedule.startText
        endField.text = files.workSchedule.endText
    }

    function submitTime(which) {
        var field = (which === "start") ? startField : endField
        if (!files.setWorkTime(which, field.text)) {
            Toast.show(which === "start" ? "上午结束时间需早于下班时间（HH:MM）"
                                         : "下班时间需晚于上午结束时间（HH:MM）")
        }
        syncTimeFields()
    }

    // ---------- 遮罩：点面板以外收起 ----------
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        visible: root.visible
        MouseArea {
            anchors.fill: parent
            onClicked: root.close()
        }
    }

    // ---------- 假投影（和 Card 同一套做法：叠两层浅矩形，不用 DropShadow） ----------
    Rectangle {
        x: panel.x
        y: panel.y + 6
        width: panel.width
        height: panel.height
        radius: panel.radius
        color: "#0B000000"
        visible: root.visible
    }
    Rectangle {
        x: panel.x
        y: panel.y + 2
        width: panel.width
        height: panel.height
        radius: panel.radius
        color: "#0A000000"
        visible: root.visible
    }

    Rectangle {
        id: panel
        objectName: "settingsPanel"
        width: root.panelWidth
        height: col.implicitHeight + 28
        radius: 12
        color: Theme.surface
        border.width: 1
        border.color: Theme.line
        visible: root.visible

        Column {
            id: col
            x: 14
            y: 14
            width: panel.width - 28
            spacing: 2

            Text {
                text: "设置"
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: Theme.text
                bottomPadding: 2
            }

            // ---- 分组标题（新增分组照这个写） ----
            SectionLabel { text: "文件浏览" }

            Item {
                width: col.width
                height: 34
                Text {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: "显示隐藏文件"
                    font.pixelSize: 12
                    color: Theme.text
                }
                Toggle {
                    objectName: "hiddenToggle"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    checked: files.showHidden
                    onToggled: function(v) { files.setShowHidden(v) }
                }
            }

            // ---- 顶栏时钟 ----
            SectionLabel { text: "工作时段（顶栏倒计时）" }

            Item {
                width: col.width
                height: 34
                Text {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: "上午班结束"
                    font.pixelSize: 12
                    color: Theme.text
                }
                Field {
                    id: startField
                    objectName: "workStartField"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    implicitHeight: 26
                    width: 74
                    leftPadding: 8
                    rightPadding: 8
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    placeholderText: "12:00"
                    onEditingFinished: root.submitTime("start")
                }
            }

            Item {
                width: col.width
                height: 34
                Text {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: "下午班结束"
                    font.pixelSize: 12
                    color: Theme.text
                }
                Field {
                    id: endField
                    objectName: "workEndField"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    implicitHeight: 26
                    width: 74
                    leftPadding: 8
                    rightPadding: 8
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    placeholderText: "18:00"
                    onEditingFinished: root.submitTime("end")
                }
            }

            // ---- 预览清晰度 ----
            SectionLabel { text: "预览清晰度" }

            QualityPicker {
                width: col.width
                title: "图片"
                choices: root.qualityChoices
                current: root.imageQuality
                onPick: function(v) { files.setImageQuality(v) }
            }

            QualityPicker {
                width: col.width
                title: "PDF"
                choices: root.qualityChoices
                current: root.pdfQuality
                onPick: function(v) { files.setPdfQuality(v) }
            }

            Rectangle {
                width: col.width
                height: 1
                color: Theme.line
            }

            Text {
                width: col.width
                text: "以上改动立即生效，并记住到下次启动"
                font.pixelSize: 10
                color: Theme.textMuted
                wrapMode: Text.Wrap
            }
        }
    }

    // 分组标题：小号灰字 + 上方留白。以后加「外观」「日志」等分组照抄一行即可。
    // 宽度不能引用 col（内联 component 的作用域里看不到兄弟项的 id），只能由根对象算。
    component SectionLabel: Text {
        width: root.panelWidth - 28
        topPadding: 8
        font.pixelSize: 11
        color: Theme.textMuted
    }

    // 档位表必须与 filecore/state.py 的 QUALITY_CHOICES 一一对应（后端还会过一遍
    // 白名单清洗），三处的 v 值写死同一个语义：1.0 / 1.5 / 2.0。
    readonly property var qualityChoices: [
        { "v": 1.0, "label": "标准", "desc": "1 倍 · 最省内存" },
        { "v": 1.5, "label": "清晰", "desc": "1.5 倍 · 边缘更实" },
        { "v": 2.0, "label": "极高", "desc": "2 倍 · 最费内存" }
    ]
    readonly property real pdfQuality: {
        var q = (files && files.previewPrefs) ? files.previewPrefs.pdfQuality : 0
        return (q > 0) ? q : 1.5
    }
    readonly property real imageQuality: {
        var q = (files && files.previewPrefs) ? files.previewPrefs.imageQuality : 0
        return (q > 0) ? q : 1.0
    }
}
