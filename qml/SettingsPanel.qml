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
        editingTypeId = -1          // 每次打开都退出改名态，别留着上次的输入框
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

    // ---------- 日志类型：加 / 改名 / 删 ----------
    // 校验和「有记录在用就不给删」全部在 backend 里做，这里只负责把结果说出来。
    // 两处各写一套判断迟早会分叉。
    property int editingTypeId: -1
    property string editingText: ""

    function beginRename(id, name) {
        editingTypeId = id
        editingText = name
    }

    function commitRename() {
        if (editingTypeId < 0)
            return
        var id = editingTypeId
        editingTypeId = -1
        var r = backend.renameLogType(id, editingText)
        if (!r.ok) {
            Toast.show(r.reason)
            return
        }
        Toast.show(r.affected > 0 ? "已改名，同步更新 " + r.affected + " 条记录" : "已改名")
    }

    function addType() {
        var r = backend.addLogType(newTypeField.text)
        if (!r.ok) {
            Toast.show(r.reason)
            return
        }
        newTypeField.text = ""
        Toast.show("类型已添加")
    }

    function removeType(id) {
        var r = backend.deleteLogType(id)
        Toast.show(r.ok ? "类型已删除" : r.reason)
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
        // 内容超过窗口高度就封顶，改由面板内部滚动 —— 设置项会越来越多
        // （日志类型这一节加完就顶到 863px，窗口才 860），不封顶会被窗口底边切掉。
        height: Math.min(col.implicitHeight + 28, root.height - 16)
        radius: 12
        color: Theme.surface
        border.width: 1
        border.color: Theme.line
        visible: root.visible

        Flickable {
            id: panelScroll
            x: 14
            y: 14
            width: panel.width - 28
            height: panel.height - 28
            contentWidth: width
            contentHeight: col.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            // 内容一超高就得让人知道下面还有东西，否则「预览清晰度」那几档
            // 会静悄悄藏在折叠线以下。
            ScrollBar.vertical: ScrollBar {
                id: panelBar
                policy: ScrollBar.AsNeeded
                width: 6
                contentItem: Rectangle {
                    implicitWidth: 4
                    radius: 2
                    color: panelBar.pressed ? Theme.textMuted : "#D9DCE6"
                }
            }

            Column {
                id: col
                // 留 8px 给滚动条，别让它压在「删」按钮上
                width: panelScroll.width - 8
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

                // ---- 目录自动刷新（M2）----
                Item {
                    width: col.width
                    height: 34
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "目录自动刷新"
                        font.pixelSize: 12
                        color: Theme.text
                    }
                    Toggle {
                        objectName: "autoRefreshToggle"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: files.autoRefresh
                        onToggled: function(v) { files.setAutoRefresh(v) }
                    }
                }

                Text {
                    width: col.width
                    text: "别的程序往当前目录里增删文件时自动更新列表；关掉就按 F5 手动刷新"
                    font.pixelSize: 10
                    color: Theme.textMuted
                    wrapMode: Text.Wrap
                    bottomPadding: 4
                }

                // ---- Office 版面（M2）----
                // 开：LibreOffice 转 PDF，版式/表格/图片都对，代价是每份文档 1~8 秒
                //     （转一次落盘缓存，同一个文件再开是秒开）。
                // 关：纯 Python 拆包抽文字，30 毫秒出结果，但只有文字。
                Item {
                    width: col.width
                    height: 34
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Office 版面"
                        font.pixelSize: 12
                        color: Theme.text
                    }
                    Toggle {
                        objectName: "officeLayoutToggle"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        checked: files.officeLayout
                        onToggled: function(v) { files.setOfficeLayout(v) }
                    }
                }

                Text {
                    objectName: "officeLayoutHint"
                    width: col.width
                    // 没装 LibreOffice 时这句要显眼：用户以为打开了开关就有版面，
                    // 结果还是文字，会以为是 bug。改用 warn 色把原因说清楚。
                    text: files.officeLayoutStatus.hint
                    font.pixelSize: 10
                    color: files.officeLayoutStatus.available ? Theme.textMuted : Theme.warn
                    wrapMode: Text.Wrap
                    bottomPadding: 4
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

                // ---- 日志类型（自定义录入页那一排）----
                // 旧 WorkLogs 把这块放在设置页左侧卡片里，新工作台的设置是唯一的入口，
                // 就放这儿。三件事：加 / 改名 / 删。语义上有两条是后端定的：
                //   · 改名会连带更新历史记录（不然「补贴款」底下一条记录都没有）
                //   · 有记录在用的类型不给删（删了那些记录的 entry_type 就成了孤儿字符串）
                SectionLabel { text: "日志类型" }

                ListView {
                    id: typeList
                    objectName: "logTypeList"
                    width: col.width
                    // 最多露出 4 行，多了自己滚 —— 面板高度是跟着内容长出来的，
                    // 不能因为类型多就把整个设置面板顶出屏幕
                    height: Math.min(contentHeight, 4 * 32)
                    clip: true
                    spacing: 2
                    model: backend.logTypes
                    boundsBehavior: Flickable.StopAtBounds

                    delegate: Item {
                        id: typeRowItem
                        width: typeList.width
                        height: 30
                        readonly property bool editing: root.editingTypeId === modelData.id

                        Text {
                            visible: !typeRowItem.editing
                            anchors.left: parent.left
                            anchors.leftMargin: 8
                            anchors.right: typeCount.left
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.name
                            font.pixelSize: 12
                            color: Theme.text
                            elide: Text.ElideRight
                        }

                        // 就地改名：点「改」这一行的名字变成输入框，回车提交、Esc 放弃
                        Field {
                            objectName: "logTypeRenameField"
                            visible: typeRowItem.editing
                            anchors.left: parent.left
                            anchors.leftMargin: 4
                            anchors.right: typeCount.left
                            anchors.rightMargin: 4
                            anchors.verticalCenter: parent.verticalCenter
                            implicitHeight: 26
                            leftPadding: 6
                            rightPadding: 6
                        font.pixelSize: 12
                        // 双向：显示跟 editingText 走，打字再推回 editingText。
                        // 不能用 onVisibleChanged 里赋初值 —— 进入编辑时
                        // editingTypeId 先变、editingText 后变，初值会拿到空串。
                        text: root.editingText
                        onTextChanged: root.editingText = text
                        onVisibleChanged: if (visible) Qt.callLater(forceActiveFocus)
                        onAccepted: root.commitRename()
                        Keys.onEscapePressed: root.editingTypeId = -1
                        }

                        Text {
                            id: typeCount
                            anchors.right: renameBtn.left
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            width: 44
                            horizontalAlignment: Text.AlignRight
                            text: (modelData.n || 0) + " 条"
                            font.pixelSize: 10
                            color: Theme.textMuted
                        }

                        Btn {
                            id: renameBtn
                            objectName: "logTypeRenameBtn"
                            anchors.right: deleteBtn.left
                            anchors.rightMargin: 2
                            anchors.verticalCenter: parent.verticalCenter
                            width: 26
                            boxHeight: 22
                            leftPadding: 0
                            rightPadding: 0
                            ghost: true
                            text: "改"
                            font.pixelSize: 11
                            onClicked: root.beginRename(modelData.id, modelData.name)
                        }

                        Btn {
                            id: deleteBtn
                            objectName: "logTypeDeleteBtn"
                            anchors.right: parent.right
                            anchors.rightMargin: 2
                            anchors.verticalCenter: parent.verticalCenter
                            width: 26
                            boxHeight: 22
                            leftPadding: 0
                            rightPadding: 0
                            ghost: true
                            danger: true
                            text: "删"
                            font.pixelSize: 11
                            onClicked: root.removeType(modelData.id)
                        }
                    }
                }

                // 一条类型都没有时给句话，别让列表位空着看起来像坏了
                Text {
                    visible: backend.logTypes.length === 0
                    width: col.width
                    text: "还没有类型。加一个才能记日志。"
                    font.pixelSize: 10
                    color: Theme.warn
                    wrapMode: Text.Wrap
                }

                Item {
                    width: col.width
                    height: 32

                    Field {
                        id: newTypeField
                        objectName: "newLogTypeField"
                        anchors.left: parent.left
                        anchors.right: addTypeBtn.left
                        anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        implicitHeight: 28
                        leftPadding: 8
                        rightPadding: 8
                        font.pixelSize: 12
                        placeholderText: "新类型，如 来件 / 回访"
                        onAccepted: root.addType()
                    }

                    Btn {
                        id: addTypeBtn
                        objectName: "addLogTypeBtn"
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: 48
                        boxHeight: 28
                        leftPadding: 0
                        rightPadding: 0
                        primary: true
                        text: "添加"
                        font.pixelSize: 12
                        onClicked: root.addType()
                    }
                }

                Text {
                    width: col.width
                    text: "改名会同步更新已有记录；还有记录在用的类型不能删"
                    font.pixelSize: 10
                    color: Theme.textMuted
                    wrapMode: Text.Wrap
                    bottomPadding: 4
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
    }

    // 分组标题：小号灰字 + 上方留白。以后加「外观」「日志」等分组照抄一行即可。
    // 宽度不能引用 col（内联 component 的作用域里看不到兄弟项的 id），只能由根对象算。
    component SectionLabel: Text {
        width: root.panelWidth - 36          // 28 内边距 + 8 给滚动条
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
