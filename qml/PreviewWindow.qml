import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// M1b · QuickLook 式浮窗预览（页面内浮层，不是独立 Window）。
//
// 两阶段：prepare 已经在 Python 侧把标题/类型/大小发出来，所以窗口先出现；
// 内容区在 payloadChanged 之后才填。大图交给 Image 异步解码（sourceSize 限尺寸），
// 工作线程绝不碰 QImage。按空格 / Esc 关闭、↑↓ 切换同目录相邻项由页面级 Shortcut 处理。
//
// 硬约束：颜色一律 Theme.*；字号整数；不用 Popup / 不用动画；
// Python 的 Property(map/list) 不能当 model，必须 property var + Connections 整体重赋值。
Item {
    id: root
    objectName: "previewWindow"
    anchors.fill: parent
    z: 800
    visible: files.preview.open
    focus: files.preview.open

    property var pvData: ({})
    property var pvItems: []
    property var pvKinds: []
    readonly property string pvKind: files.preview.kind
    readonly property bool loading: files.preview.state === "loading"

    // ---- 浮窗位置（M1b 补充：可拖动）----
    // cardX/cardY = -1 表示「还没手动挪过」，此时用居中算出的 homeX/homeY。
    // 不用 anchors.centerIn：anchors 与 x/y 混用会互相打架，拖拽时 x 被绑定覆盖。
    // 位置跨 open/close 保留、切换文件时不动（QuickLook 行为），窗口缩放由 moveTo 的
    // clamp 保底；最小值 8px 保证卡片不会顶到窗口边缘拖不回来。
    property real cardX: -1
    property real cardY: -1
    readonly property real cardW: Math.min(760, Math.max(320, root.width - 80))
    readonly property real cardH: Math.min(580, Math.max(240, root.height - 80))
    readonly property real homeX: Math.round((root.width - cardW) / 2)
    readonly property real homeY: Math.round((root.height - cardH) / 2)

    function moveTo(nx, ny) {
        root.cardX = Math.max(8, Math.min(nx, root.width - cardW - 8))
        root.cardY = Math.max(8, Math.min(ny, root.height - cardH - 8))
    }
    function resetPos() {
        root.cardX = -1
        root.cardY = -1
    }

    // 实际绘制位置：没拖过走居中；拖过则再夹一次边界。
    // 窗口被缩小时 cardX 可能已在可视区外，这里兜住，避免卡片整块跑出屏幕；
    // 夹紧只作用于显示，cardX 本身不改，窗口再放大能回到原位。
    readonly property real shownX: root.cardX < 0
        ? root.homeX
        : Math.max(8, Math.min(root.cardX, Math.max(8, root.width - root.cardW - 8)))
    readonly property real shownY: root.cardY < 0
        ? root.homeY
        : Math.max(8, Math.min(root.cardY, Math.max(8, root.height - root.cardH - 8)))

    // 离屏运行时探针（只读）：Repeater 的委托项在 findChildren 里看不到，
    // 用 repeater.count 验证委托真的创建了。
    readonly property bool imageVisible: imageBox.visible
    readonly property bool textVisible: textFlick.visible
    readonly property bool dirVisible: dirBox.visible
    readonly property bool fallbackVisible: fallbackBox.visible
    readonly property bool overlayVisible: loadingOverlay.visible
    readonly property int dirItemCount: dirRepeater.count
    readonly property int kindTagCount: kindRepeater.count
    readonly property string imageSource: previewImage.source
    readonly property int imageStatus: previewImage.status
    readonly property string shownTitle: titleText.text
    readonly property string shownPath: pathText.text
    readonly property real cardPosX: previewCard.x
    readonly property real cardPosY: previewCard.y
    readonly property bool cardCentered: Math.abs(previewCard.x - root.homeX) < 1 &&
                                         Math.abs(previewCard.y - root.homeY) < 1

    Connections {
        target: files.preview
        function onPayloadChanged() {
            root.pvData = files.preview.payload
            root.pvItems = root.pvData.items ? root.pvData.items : []
            root.pvKinds = root.pvData.kinds ? root.pvData.kinds : []
        }
    }

    // 遮罩：单独一个 Rectangle 承担半透明，卡片是兄弟节点，不受 opacity 影响。
    Rectangle {
        anchors.fill: parent
        color: Theme.text
        opacity: 0.32
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            onClicked: files.preview.close()
        }
    }

    Card {
        id: previewCard
        objectName: "previewCard"
        width: root.cardW
        height: root.cardH
        // 手动挪过就跟着 cardX/cardY，没挪过则居中（见文件头 cardX < 0 的约定）
        x: root.shownX
        y: root.shownY
        // 吃掉卡片上的点击，别让遮罩把窗口关掉
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.LeftButton }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 0

            // ---------- 标题栏 ----------
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 46
                Icon {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    width: 20; height: 20
                    name: root.pvKind === "directory" ? "folder" : "file"
                    color: Theme.accent
                }
                Column {
                    anchors.left: parent.left
                    anchors.leftMargin: 30
                    anchors.right: closeBtn.left
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        id: titleText
                        width: parent.width
                        text: files.preview.title
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: Theme.text
                        elide: Text.ElideMiddle
                    }
                    Text {
                        width: parent.width
                        text: files.preview.subtitle
                        font.pixelSize: 11
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }
                }
                // 拖动标题栏移动浮窗。必须用 mapToItem(root) 取坐标：
                // 卡片自己在动，用 mouse.x/y 这种相对坐标会「自激」（鼠标相对卡片没动，
                // 位移恒为 0，浮窗反而拖不走）。closeBtn 声明在本块之后，层级更高，
                // 所以拖拽区不会抢关闭按钮的点击。
                MouseArea {
                    id: titleDrag
                    objectName: "previewTitleDrag"
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.right: closeBtn.left
                    anchors.rightMargin: 4
                    hoverEnabled: true
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    property real pressPx: 0
                    property real pressPy: 0
                    property real baseX: 0
                    property real baseY: 0
                    onPressed: function(mouse) {
                        var p = titleDrag.mapToItem(root, mouse.x, mouse.y)
                        titleDrag.pressPx = p.x
                        titleDrag.pressPy = p.y
                        titleDrag.baseX = previewCard.x
                        titleDrag.baseY = previewCard.y
                    }
                    onPositionChanged: function(mouse) {
                        if (!titleDrag.pressed)
                            return
                        var p = titleDrag.mapToItem(root, mouse.x, mouse.y)
                        root.moveTo(titleDrag.baseX + (p.x - titleDrag.pressPx),
                                    titleDrag.baseY + (p.y - titleDrag.pressPy))
                    }
                    onDoubleClicked: root.resetPos()
                }

                Item {
                    id: closeBtn
                    width: 28; height: 28
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    Rectangle {
                        anchors.fill: parent
                        radius: 7
                        color: closeMouse.containsMouse ? Theme.accentSoft : "transparent"
                    }
                    Icon {
                        anchors.centerIn: parent
                        width: 13; height: 13
                        name: "close"
                        color: Theme.textMuted
                    }
                    MouseArea {
                        id: closeMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: files.preview.close()
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line }

            // ---------- 内容区：四种预览器各一块，按 kind 显隐 ----------
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.topMargin: 12

                // 图片：source 为 vpath（已是合法 file:// URL），异步 + 限解码尺寸
                Rectangle {
                    id: imageBox
                    anchors.fill: parent
                    radius: 9
                    color: Theme.bg
                    visible: root.pvKind === "image"
                    Image {
                        id: previewImage
                        anchors.fill: parent
                        anchors.margins: 8
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        cache: false
                        sourceSize.width: 1200
                        sourceSize.height: 860
                        source: (root.pvKind === "image" && root.pvData.source &&
                                 !root.pvData.tooLarge) ? root.pvData.source : ""
                    }
                    Text {
                        anchors.centerIn: parent
                        visible: root.pvData.tooLarge ||
                                 previewImage.status === Image.Error
                        width: parent.width - 40
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: root.pvData.tooLarge ? root.pvData.error
                              : "此图片无法解码（系统可能缺少对应的图片插件）"
                        font.pixelSize: 12
                        color: Theme.textMuted
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 8
                        visible: root.pvKind === "image" &&
                                 previewImage.status === Image.Ready &&
                                 root.pvData.width > 0
                        text: root.pvData.width + " × " + root.pvData.height +
                              " · " + root.pvData.format
                        font.pixelSize: 11
                        color: Theme.textMuted
                    }
                }

                // 文本 / 代码：只读可选中，横向可滚，不用动画
                Flickable {
                    id: textFlick
                    anchors.fill: parent
                    clip: true
                    visible: root.pvKind === "text"
                    boundsBehavior: Flickable.StopAtBounds
                    contentWidth: Math.max(width, textBody.implicitWidth + 24)
                    contentHeight: Math.max(height, textBody.implicitHeight + 24)
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
                    TextEdit {
                        id: textBody
                        x: 12; y: 12
                        width: Math.max(textFlick.width - 24, implicitWidth)
                        text: root.pvKind === "text" ? (root.pvData.text || "") : ""
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.NoWrap
                        font.pixelSize: 12
                        color: Theme.text
                        selectionColor: Theme.accentSoft
                    }
                }

                // 文件夹摘要：统计 + 类型分布 + 前 12 项
                Item {
                    id: dirBox
                    anchors.fill: parent
                    visible: root.pvKind === "directory"
                    Column {
                        anchors.fill: parent
                        spacing: 8
                        Text {
                            text: root.pvData.error ? root.pvData.error :
                                  ("共 " + (root.pvData.count || 0) + " 项 · " +
                                   (root.pvData.dirCount || 0) + " 个文件夹 · " +
                                   (root.pvData.fileCount || 0) + " 个文件 · 合计 " +
                                   (root.pvData.totalText || "0 B"))
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            color: root.pvData.error ? Theme.danger : Theme.text
                        }
                        Row {
                            spacing: 6
                            Repeater {
                                id: kindRepeater
                                model: root.pvKinds
                                delegate: Rectangle {
                                    width: tagText.implicitWidth + 14
                                    height: 22
                                    radius: 6
                                    color: Theme.accentSoft
                                    Text {
                                        id: tagText
                                        anchors.centerIn: parent
                                        text: modelData.label + " " + modelData.count
                                        font.pixelSize: 11
                                        color: Theme.accent
                                    }
                                }
                            }
                        }
                        Rectangle { width: parent.width; height: 1; color: Theme.line }
                        Repeater {
                            id: dirRepeater
                            model: root.pvItems
                            delegate: Item {
                                width: parent.width
                                height: 26
                                Rectangle {
                                    x: 2; y: 10; width: 8; height: 8; radius: 2
                                    color: modelData.isDir ? Theme.accent : Theme.todo
                                }
                                Text {
                                    x: 18; y: 0
                                    width: Math.max(0, parent.width - 90)
                                    height: 26
                                    verticalAlignment: Text.AlignVCenter
                                    text: modelData.name
                                    font.pixelSize: 12
                                    color: Theme.text
                                    elide: Text.ElideRight
                                }
                                Text {
                                    anchors.right: parent.right
                                    y: 0; height: 26
                                    verticalAlignment: Text.AlignVCenter
                                    text: modelData.isDir ? "文件夹" : modelData.sizeText
                                    font.pixelSize: 11
                                    color: Theme.textMuted
                                }
                            }
                        }
                        Text {
                            visible: root.pvItems.length > 0
                            text: "仅列出前 " + root.pvItems.length + " 项"
                            font.pixelSize: 11
                            color: Theme.textMuted
                        }
                    }
                }

                // 兜底信息卡
                Item {
                    id: fallbackBox
                    anchors.fill: parent
                    visible: root.pvKind === "fallback"
                    Column {
                        anchors.centerIn: parent
                        spacing: 10
                        Icon {
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: 48; height: 48
                            name: "file"
                            color: Theme.accent
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.pvData.name || files.preview.title
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: (root.pvData.kindLabel || "") + " · " +
                                  (root.pvData.sizeText || "") + " · " +
                                  (root.pvData.timeText || "")
                            font.pixelSize: 11
                            color: Theme.textMuted
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            text: root.pvData.hint || "此类型暂不支持预览内容"
                            font.pixelSize: 12
                            color: Theme.textMuted
                        }
                    }
                }

                // 加载态 / 错误态（静态文字，盖在内容区之上）
                Rectangle {
                    id: loadingOverlay
                    anchors.fill: parent
                    z: 5
                    visible: root.loading || files.preview.state === "error"
                    color: Theme.surface
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 40
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: files.preview.state === "error" ? files.preview.errorText
                              : "正在预览…"
                        font.pixelSize: 12
                        color: files.preview.state === "error" ? Theme.danger : Theme.textMuted
                    }
                }
            }

            // ---------- 底栏：完整路径（ElideMiddle）+ 快捷键提示 ----------
            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.line }
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 32
                Text {
                    id: pathText
                    anchors.left: parent.left
                    anchors.right: hintText.left
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: files.preview.displayPath
                    font.pixelSize: 11
                    color: Theme.textMuted
                    elide: Text.ElideMiddle
                }
                Text {
                    id: hintText
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    // 窄卡片优先保住「怎么关」，宽了才带拖动说明，免得把路径挤没
                    text: previewCard.width >= 620
                          ? "拖动标题栏移动 · 双击复位 · ↑↓ 切换 · 空格 / Esc 关闭"
                          : "空格 / Esc 关闭 · ↑↓ 切换"
                    font.pixelSize: 11
                    color: Theme.textMuted
                }
            }
        }
    }
}
