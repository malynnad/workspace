import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: root
    objectName: "logPage"

    property date selectedDate: new Date()
    readonly property string dateStr: Qt.formatDate(selectedDate, "yyyy-MM-dd")
    readonly property date today: new Date()

    property int viewMonth: selectedDate.getMonth()
    property int viewYear: selectedDate.getFullYear()

    property var dayCells: []
    property var counts: ({})
    property var monthStat: ({ "count": 0, "days": 0 })

    property int draftId: -1
    property int typeIndex: 0

    property var listModel: []
    property var entryModel: []

    // ---------- 日历尺寸（由日历卡自身宽高反推，随窗口等比放大）----------
    // 数据流必须单向：卡片宽高由 Layout 决定 → cellW → Grid 尺寸。
    // 绝不能读 dayGrid.width 或 Grid.implicitWidth，那会让宽度绑定成环（曾把整列撑爆）。
    readonly property int calPadX: 16
    readonly property int calContentW: Math.max(0, calendarCard.width - calPadX * 2)
    // 纵向预算 = 上下内边距 32 + 月份行 28 + 星期表头 22 + 底部统计 18 + 5 段间距 30。
    // 星期表头必须用固定 22：若让它跟着 cellH 变，就会形成 cellH → 表头 → 预算 → cellH 的回路。
    readonly property int calContentH: Math.max(0, calendarCard.height - 150)
    readonly property int cellW: Math.max(60, Math.min(120,
            Math.floor(calContentW / 7),
            Math.floor((calContentH - 10) / 6 * 1.7)))
    readonly property int cellH: Math.max(42, Math.min(96,
            Math.floor((calContentH - 10) / 6)))
    // 日期圆受格子高约束：宽屏下格子会被拉成长方形，只按 cellW 算会顶出格底
    readonly property int bubble: Math.min(Math.round(cellW * 0.47),
                                           Math.round(cellH * 0.65))        // 60x43 → 28
    readonly property int dayFont: Math.round(cellW * 0.20)               // 60 → 12
    readonly property int cntFont: Math.max(8, Math.round(cellW * 0.15))  // 60 → 9

    property int viewMode: 0                     // 0 = 日历  1 = 全部记录

    // 换月 / 切到列表时格子会重建，被悬停的那个 item 直接销毁、收不到 exited，
    // 卡片会留在原地，所以这两处变化时主动收掉
    onDayCellsChanged: hideDayTip()
    onShowingListChanged: hideDayTip()
    property string keyword: ""
    readonly property bool showingList: viewMode === 1 || keyword.length > 0

    // ---------- 日历网格 ----------
    function rebuildDays() {
        var first = new Date(viewYear, viewMonth, 1)
        var startDow = (first.getDay() + 6) % 7          // 周一为第一列
        var daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
        var total = Math.ceil((startDow + daysInMonth) / 7) * 7
        var cells = []
        for (var i = 0; i < total; i++) {
            var d = new Date(viewYear, viewMonth, i - startDow + 1)
            cells.push({
                day: d.getDate(),
                month: d.getMonth(),
                year: d.getFullYear(),
                inMonth: d.getMonth() === viewMonth
            })
        }
        dayCells = cells
    }

    function refreshCounts() {
        counts = backend.dayCounts(viewYear, viewMonth)
        monthStat = backend.monthStats(viewYear, viewMonth)
    }

    function refreshList() {
        backend.listEntries(keyword)
    }

    function prevM() {
        if (viewMonth === 0) { viewMonth = 11; viewYear-- } else viewMonth--
    }
    function nextM() {
        if (viewMonth === 11) { viewMonth = 0; viewYear++ } else viewMonth++
    }

    function clearDraft() {
        draftId = -1
        subjectField.text = ""
        contentArea.text = ""
        typeIndex = 0
    }
    function loadDraft(e) {
        draftId = e.id
        subjectField.text = e.subject || ""
        contentArea.text = e.content || ""
        typeIndex = 0
        for (var i = 0; i < backend.logTypes.length; i++)
            if (backend.logTypes[i].name === (e.entry_type || "工作")) typeIndex = i
    }
    function typeName() {
        var t = backend.logTypes[typeIndex]
        return t ? t.name : "工作"
    }

    // 从全部记录/搜索结果跳回日历当天
    function jumpToEntry(e) {
        var p = String(e.log_date || "").split("-")
        if (p.length === 3) {
            var d = new Date(parseInt(p[0]), parseInt(p[1]) - 1, parseInt(p[2]))
            selectedDate = d
            viewYear = d.getFullYear()
            viewMonth = d.getMonth()
        }
        viewMode = 0
        searchField.text = ""
        keyword = ""
        backend.selectDate(dateStr)
        refreshCounts()
        for (var i = 0; i < root.entryModel.length; i++) {
            if (root.entryModel[i].id === e.id) {
                loadDraft(root.entryModel[i])
                break
            }
        }
    }

    onViewMonthChanged: { rebuildDays(); refreshCounts() }
    onViewYearChanged: { rebuildDays(); refreshCounts() }
    Component.onCompleted: {
        rebuildDays()
        refreshCounts()
        refreshList()
        backend.selectDate(dateStr)
        root.entryModel = backend.entries
        root.listModel = backend.listItems
    }

    Connections {
        target: backend
        function onEntriesChanged() {
            root.entryModel = backend.entries
            root.refreshCounts()
            if (root.showingList)
                root.refreshList()
        }
        function onListChanged() {
            root.listModel = backend.listItems
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.pad
        spacing: 12

        // ---------- 工具栏：搜索 + 视图切换 + 统计 ----------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            RowLayout {
                spacing: 4
                Item {
                    Layout.preferredWidth: 236
                    Layout.preferredHeight: 34

                    Field {
                        id: searchField
                        objectName: "searchField"
                        anchors.fill: parent
                        leftPadding: 31
                        placeholderText: "搜索日志，主题或正文…"
                        onTextChanged: {
                            root.keyword = text.trim()
                            root.refreshList()
                        }
                    }
                    Icon {
                        name: "search"
                        width: 14
                        height: 14
                        color: searchField.activeFocus ? Theme.accent : Theme.textMuted
                        anchors.left: parent.left
                        anchors.leftMargin: 11
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Btn {
                    visible: searchField.text.length > 0
                    text: "✕"
                    ghost: true
                    boxHeight: 26
                    leftPadding: 8
                    rightPadding: 8
                    onClicked: { searchField.text = ""; searchField.forceActiveFocus() }
                }
            }

            // 分段控件：日历 / 全部记录
            Rectangle {
                height: 32
                width: segRow.width + 8
                radius: 9
                color: "#ECEEF4"

                Row {
                    id: segRow
                    anchors.centerIn: parent
                    spacing: 2
                    Repeater {
                        model: ["日历", "全部记录"]
                        delegate: Rectangle {
                            height: 26
                            width: segLabel.implicitWidth + 26
                            radius: 7
                            color: root.viewMode === index ? "#FFFFFF" : "transparent"
                            border.width: root.viewMode === index ? 1 : 0
                            border.color: "#E6E9F1"
                            Text {
                                id: segLabel
                                anchors.centerIn: parent
                                text: modelData
                                font.pixelSize: 12
                                font.weight: root.viewMode === index ? Font.DemiBold : Font.Normal
                                color: root.viewMode === index ? Theme.text : Theme.textMuted
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.viewMode = index
                            }
                        }
                    }
                }
            }

            Item { Layout.fillWidth: true }

            Text {
                text: "共 " + backend.overallStats.count + " 条 · 覆盖 "
                      + backend.overallStats.days + " 天"
                color: Theme.textMuted
                font.pixelSize: 11
            }

            // 竖线分隔，然后时钟
            Rectangle {
                width: 1
                height: 14
                color: Theme.line
            }

            Clock {}
        }

        // ---------- 主体 ----------
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.pad

            // ===== 左列：日历 + 录入 =====
            ColumnLayout {
                visible: !root.showingList
                Layout.fillWidth: true
                Layout.minimumWidth: 452
                Layout.fillHeight: true
                spacing: Theme.pad

                Card {
                    id: calendarCard
                    objectName: "calendarCard"
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            Btn {
                                text: "‹"
                                ghost: true
                                boxHeight: 28
                                leftPadding: 10
                                rightPadding: 10
                                onClicked: root.prevM()
                            }
                            Item { Layout.fillWidth: true }
                            Text {
                                text: root.viewYear + " 年 " + (root.viewMonth + 1) + " 月"
                                font.pixelSize: 15
                                font.weight: Font.DemiBold
                                color: Theme.text
                            }
                            Item { Layout.fillWidth: true }
                            Btn {
                                text: "›"
                                ghost: true
                                boxHeight: 28
                                leftPadding: 10
                                rightPadding: 10
                                onClicked: root.nextM()
                            }
                        }

                        // 上下两段弹性空白，让日历在卡片里垂直居中
                        Item { Layout.fillHeight: true }

                        Grid {
                            id: weekHead
                            columns: 7
                            Layout.alignment: Qt.AlignHCenter
                            Repeater {
                                model: ["一", "二", "三", "四", "五", "六", "日"]
                                delegate: Text {
                                    width: root.cellW
                                    height: 22
                                    text: modelData
                                    horizontalAlignment: Text.AlignHCenter
                                    verticalAlignment: Text.AlignVCenter
                                    color: Theme.textMuted
                                    font.pixelSize: 11
                                }
                            }
                        }

                        Grid {
                            id: dayGrid
                            objectName: "dayGrid"
                            columns: 7
                            rowSpacing: 2
                            Layout.alignment: Qt.AlignHCenter

                            Repeater {
                                model: root.dayCells
                                delegate: Item {
                                    id: dayCell
                                    width: root.cellW
                                    height: root.cellH

                                    readonly property bool inMonth: modelData ? modelData.inMonth : false
                                    readonly property int cnt: inMonth
                                        ? (root.counts[String(modelData.day)] || 0) : 0
                                    readonly property bool isSelected: modelData
                                        && root.selectedDate.getDate() === modelData.day
                                        && root.selectedDate.getMonth() === modelData.month
                                        && root.selectedDate.getFullYear() === modelData.year
                                    readonly property bool isToday: modelData
                                        && root.today.getDate() === modelData.day
                                        && root.today.getMonth() === modelData.month
                                        && root.today.getFullYear() === modelData.year

                                    Rectangle {
                                        id: bubble
                                        width: root.bubble
                                        height: root.bubble
                                        radius: root.bubble / 2
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        y: 1
                                        // 今天：淡蓝底 + 蓝描边（不是实心，避开「选中」的蓝实心）
                                        color: dayCell.isSelected ? Theme.accent
                                             : (dayCell.isToday
                                                ? (dayHover.containsMouse ? "#E3E9FF" : Theme.accentSoft)
                                             : (dayHover.containsMouse && dayCell.inMonth ? "#F0F2F8"
                                             : "transparent"))
                                        border.width: dayCell.isToday && !dayCell.isSelected ? 1.5 : 0
                                        border.color: Theme.accent
                                    }

                                    Text {
                                        anchors.centerIn: bubble
                                        text: modelData ? modelData.day : ""
                                        font.pixelSize: root.dayFont
                                        color: dayCell.isSelected ? "white"
                                             : (dayCell.isToday ? Theme.accent
                                             : (dayCell.inMonth ? Theme.text : "#C3C8D4"))
                                        font.weight: dayCell.isToday ? Font.DemiBold : Font.Normal
                                    }

                                    // 有记录的日期：数字下方直接显示条数（一天几十条也读得出来）
                                    Text {
                                        visible: dayCell.cnt > 0
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        y: root.bubble + 1
                                        text: dayCell.cnt > 99 ? "99+" : dayCell.cnt
                                        font.pixelSize: root.cntFont
                                        font.weight: Font.Medium
                                        color: dayCell.isSelected ? "white" : Theme.accent
                                        opacity: dayCell.isSelected ? 1.0 : 0.9
                                    }

                                    MouseArea {
                                        id: dayHover
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onEntered: {
                                            if (!dayCell.inMonth || dayCell.cnt <= 0)
                                                return
                                            root.tipCell = dayCell
                                            // 已经有卡片挂着时直接换内容，不再等 420ms
                                            if (dayTip.visible) {
                                                dayTipClose.stop()
                                                root.openDayTip(dayCell, dayCell.cnt)
                                            } else {
                                                dayTipOpen.restart()
                                            }
                                        }
                                        onExited: {
                                            dayTipOpen.stop()
                                            // 相邻格子之间移动时，新格子的 entered 可能先于
                                            // 旧格子的 exited 到达；此时留着定时器会把卡片误关
                                            if (root.tipCell === dayCell)
                                                dayTipClose.start()
                                        }
                                        onClicked: {
                                            if (!modelData) return
                                            root.selectedDate = new Date(modelData.year,
                                                                         modelData.month,
                                                                         modelData.day)
                                            if (modelData.month !== root.viewMonth
                                                    || modelData.year !== root.viewYear) {
                                                root.viewMonth = modelData.month
                                                root.viewYear = modelData.year
                                            }
                                            backend.selectDate(root.dateStr)
                                            root.clearDraft()
                                        }
                                    }
                                }
                            }
                        }

                        Item { Layout.fillHeight: true }

                        Text {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: root.monthStat.count > 0
                                  ? "本月 " + root.monthStat.count + " 条 · "
                                    + root.monthStat.days + " 天有记录"
                                  : "本月还没有记录"
                            color: Theme.textMuted
                            font.pixelSize: 11
                        }
                    }
                }

                Card {
                    objectName: "editorCard"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 196
                    Layout.minimumHeight: 190

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            Row {
                                spacing: 6
                                Repeater {
                                    model: backend.logTypes
                                    delegate: Rectangle {
                                        height: 32
                                        width: pillText.implicitWidth + 20
                                        radius: 8
                                        color: root.typeIndex === index ? Theme.accentSoft : "#F5F6FA"
                                        border.width: root.typeIndex === index ? 1 : 0
                                        border.color: Theme.accent
                                        Text {
                                            id: pillText
                                            anchors.centerIn: parent
                                            text: modelData.name
                                            font.pixelSize: 12
                                            color: root.typeIndex === index ? Theme.accent : Theme.textMuted
                                        }
                                        MouseArea {
                                            anchors.fill: parent
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: root.typeIndex = index
                                        }
                                    }
                                }
                            }

                            Field {
                                id: subjectField
                                Layout.fillWidth: true
                                placeholderText: "主题…"
                            }
                        }

                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            TextArea {
                                id: contentArea
                                placeholderText: "记录内容…"
                                wrapMode: TextArea.Wrap
                                background: null
                                padding: 2
                                font.pixelSize: 13
                                color: Theme.text
                                placeholderTextColor: Theme.textMuted
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            // 未选中条目时给出提示，说明按钮去哪了
                            Text {
                                visible: root.draftId === -1
                                text: "点右侧条目可编辑 / 转待办 / 删除"
                                color: Theme.textMuted
                                font.pixelSize: 11
                            }

                            // 按钮组靠右下
                            Item { Layout.fillWidth: true }

                            Btn {
                                text: root.draftId === -1 ? "保存" : "保存修改"
                                primary: true
                                onClicked: {
                                    var isNew = root.draftId === -1
                                    if (isNew)
                                        backend.addEntry(root.dateStr, root.typeName(),
                                                         subjectField.text, contentArea.text)
                                    else
                                        backend.updateEntry(root.draftId, root.typeName(),
                                                            subjectField.text, contentArea.text)
                                    root.clearDraft()
                                    Toast.show(isNew ? "新条目已保存" : "修改已保存")
                                }
                            }
                            Btn {
                                visible: root.draftId !== -1
                                text: "转为待办"
                                tint: Theme.todo
                                onClicked: {
                                    backend.entryToTodo(root.draftId)
                                    Toast.show("已转为待办事项")
                                }
                            }
                            Btn {
                                id: delEntryBtn
                                visible: root.draftId !== -1
                                text: "删除"
                                solidDanger: true
                                onClicked: delEntryConfirm.openAt(delEntryBtn)
                            }
                        }
                    }
                }
            }

            // ===== 全部记录 / 搜索结果（列表视图时显示）=====
            Card {
                objectName: "allListCard"
                visible: root.showingList
                Layout.fillWidth: true
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: root.keyword.length > 0
                                  ? "搜索结果 · " + Math.max(0, root.listModel.length
                                      - countHeaders(root.listModel)) + " 条"
                                  : "全部记录"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            visible: root.keyword.length > 0
                            text: "「" + root.keyword + "」"
                            font.pixelSize: 12
                            color: Theme.accent
                        }
                        Item { Layout.fillWidth: true }
                        Btn {
                            visible: root.keyword.length > 0
                            text: "清除搜索"
                            ghost: true
                            boxHeight: 28
                            onClicked: { searchField.text = "" }
                        }
                        Btn {
                            text: "回到日历"
                            ghost: true
                            boxHeight: 28
                            onClicked: { root.viewMode = 0; searchField.text = "" }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 1
                        color: Theme.line
                    }

                    ListView {
                        id: allList
                        objectName: "allList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: root.listModel

                        delegate: Item {
                            width: allList.width
                            height: modelData.kind === "header" ? 34 : (entryBox.height + 4)

                            // --- 日期分组头 ---
                            RowLayout {
                                visible: modelData.kind === "header"
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 8
                                Text {
                                    text: modelData.label
                                    font.pixelSize: 12
                                    font.weight: Font.DemiBold
                                    color: Theme.text
                                }
                                Rectangle {
                                    width: 1
                                    height: 10
                                    color: Theme.line
                                }
                                Text {
                                    text: modelData.count + " 条"
                                    font.pixelSize: 11
                                    color: Theme.textMuted
                                }
                                Item { Layout.fillWidth: true }
                            }

                            // --- 条目行 ---
                            Rectangle {
                                id: entryBox
                                visible: modelData.kind === "entry"
                                width: parent.width
                                height: entryCol.implicitHeight + 16
                                radius: 9
                                color: entryMouse.containsMouse ? "#F5F7FF" : "#F7F8FB"

                                ColumnLayout {
                                    id: entryCol
                                    x: 12
                                    y: 8
                                    width: parent.width - 24
                                    spacing: 3

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 7
                                        Rectangle {
                                            visible: modelData.entry_type !== undefined
                                                     && modelData.entry_type !== null
                                                     && modelData.entry_type !== ""
                                            height: 18
                                            width: typeTag.implicitWidth + 14
                                            radius: 6
                                            color: Theme.accentSoft
                                            Text {
                                                id: typeTag
                                                anchors.centerIn: parent
                                                text: modelData.entry_type || ""
                                                font.pixelSize: 10
                                                color: Theme.accent
                                            }
                                        }
                                        Text {
                                            text: modelData.subject || "(无标题)"
                                            font.pixelSize: 12
                                            font.weight: Font.Medium
                                            color: Theme.text
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                    Text {
                                        visible: (modelData.content || "").length > 0
                                        text: modelData.content || ""
                                        font.pixelSize: 11
                                        color: Theme.textMuted
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                }

                                MouseArea {
                                    id: entryMouse
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.jumpToEntry(modelData)
                                }
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            visible: root.listModel.length === 0
                            text: root.keyword.length > 0
                                  ? "没有匹配「" + root.keyword + "」的记录"
                                  : "还没有任何记录"
                            color: Theme.textMuted
                            font.pixelSize: 12
                        }
                    }
                }
            }

            // ===== 当天条目（仅日历视图显示）=====
            Card {
                objectName: "entryCard"
                visible: !root.showingList
                Layout.preferredWidth: 400
                Layout.maximumWidth: 400
                Layout.minimumWidth: 320
                Layout.fillHeight: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: root.dateStr + " · 当天条目"
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            text: root.entryModel.length > 0
                                  ? root.entryModel.length + " 条" : ""
                            font.pixelSize: 11
                            color: Theme.textMuted
                        }
                        Item { Layout.fillWidth: true }
                        Btn {
                            text: "＋ 新建条目"
                            boxHeight: 30
                            onClicked: root.clearDraft()
                        }
                    }

                    ListView {
                        id: entryList
                        objectName: "entryList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 6
                        model: root.entryModel

                        delegate: Rectangle {
                            width: entryList.width
                            height: entryCol2.implicitHeight + 18
                            radius: 9
                            readonly property bool picked: root.draftId === modelData.id
                            color: picked ? "#EAF0FF"
                                   : (entryMouse2.containsMouse ? "#F5F7FF" : "#F7F8FB")
                            border.width: 1
                            border.color: picked ? "#BFCFFF" : "transparent"

                            // 选中标记：左侧主色竖条
                            Rectangle {
                                visible: parent.picked
                                width: 3
                                height: parent.height - 14
                                radius: 2
                                x: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: Theme.accent
                            }

                            ColumnLayout {
                                id: entryCol2
                                x: 11
                                y: 9
                                width: parent.width - 22
                                spacing: 2

                                Text {
                                    text: (modelData.entry_type ? "[" + modelData.entry_type + "] " : "")
                                          + (modelData.subject || "(无标题)")
                                    font.pixelSize: 12
                                    font.weight: Font.Medium
                                    color: Theme.text
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: modelData.content || ""
                                    font.pixelSize: 11
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                            MouseArea {
                                id: entryMouse2
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.loadDraft(modelData)
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            visible: root.entryModel.length === 0
                            text: "这一天还没有记录"
                            color: Theme.textMuted
                            font.pixelSize: 12
                        }
                    }
                }
            }
        }
    }

    Confirm {
        id: delEntryConfirm
        message: "删除这条记录？"
        hint: "删除后无法恢复"
        onAccepted: {
            backend.deleteEntry(root.draftId)
            root.clearDraft()
            Toast.show("记录已删除")
        }
    }

    // ---------- 日期悬停小卡 ----------
    // ① 不用 Qt 自带 ToolTip（Basic 风格是深底黑框）
    // ② 也不再用 Popup：Popup 只要盖住某个格子，那格子的 MouseArea 就收不到 hover，
    //    会被误判成"移出"→ 收起 → 鼠标其实还在格子上却没有新的 enter 事件，
    //    于是"有时灵有时不灵"。而且读它自己的 width/height 还会触发 polish 死循环。
    // 现在用普通 Rectangle + enabled:false：既不吃鼠标，也没有 popup 的那堆定位逻辑。
    property Item tipCell: null
    property int tipCount: 0

    function openDayTip(cell, cnt) {
        if (!cell)
            return
        tipCount = cnt
        var p = cell.mapToItem(root, 0, 0)
        var g = dayGrid.mapToItem(root, 0, 0)
        // 贴在 7 列网格右侧、纵向跟着行；右边放不下就翻到左侧
        // 估算尺寸按格子缩放倍数走（60 → 88/32），与实际卡片高度保持一致
        var k = root.cellW / 60
        var estW = Math.round(88 * k)
        var estH = Math.round(32 * k)
        var x = g.x + dayGrid.width + 10
        if (x + estW > root.width - 8)
            x = g.x - estW - 10
        dayTip.x = Math.max(8, x)
        dayTip.y = Math.max(8, Math.min(p.y + (cell.height - estH) / 2,
                                        root.height - estH - 8))
        dayTip.opacity = 1
    }

    function hideDayTip() {
        dayTip.opacity = 0
    }

    Timer {
        id: dayTipOpen
        interval: 420                       // 停留 420ms 才弹，和时钟一致
        onTriggered: root.openDayTip(root.tipCell, root.tipCount)
    }
    Timer {
        id: dayTipClose
        interval: 220                       // 移开后不立即消失
        onTriggered: root.hideDayTip()
    }

    Rectangle {
        id: dayTip
        objectName: "dayTip"
        visible: opacity > 0
        opacity: 0
        enabled: false                      // 关键：完全不参与鼠标命中
        z: 930
        width: tipRow.implicitWidth + 20
        height: Math.round(32 * root.cellW / 60)
        radius: Math.round(10 * root.cellW / 60)
        color: "#FFFFFF"
        border.width: 1
        border.color: "#E7E9F1"

        Behavior on opacity {
            NumberAnimation {
                duration: 150
                easing.type: Easing.OutCubic
            }
        }

        Row {
            id: tipRow
            anchors.centerIn: parent
            spacing: 4
            Text {
                text: root.tipCount
                font.pixelSize: 12
                font.weight: Font.DemiBold
                color: Theme.accent
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: "条记录"
                font.pixelSize: 11
                color: Theme.textMuted
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    function countHeaders(items) {
        var n = 0
        for (var i = 0; i < items.length; i++)
            if (items[i].kind === "header") n++
        return n
    }
}
