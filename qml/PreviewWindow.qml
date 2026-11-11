import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// M1b · QuickLook 式预览 —— 独立无边框窗口（不是主窗口里的浮层）。
//
// 为什么要独立窗口：
//   1. 能拖出主窗口，放到 Excel 旁边边看边录；
//   2. 带 Qt.WindowStaysOnTopHint，可以浮在任何应用之上；
//   3. 主窗口最小化/被遮挡时预览仍在原位。
// 代价是拖动、缩放、快捷键都得自己实现（无边框窗口没有系统标题栏/边框）：
//   - 拖动 = 标题栏 MouseArea 改 window.x/y，用 mapToGlobal 换算的屏幕坐标
//     （窗口内局部坐标会让位移被自己吃掉一半，见 titleDrag 处的注释）；
//   - 缩放 = 四边四角 8 个热区，优先 startSystemResize() 交给窗口管理器，
//     平台不支持时退回自己改 width/height；
//   - 空格/Esc/↑↓ 走内容根的 Keys，只在本窗口获得焦点时生效，主窗口那套不受影响。
// 默认尺寸 = 屏幕右半边（1920×1080 → 960×972），配合「左边 Excel、右边预览」的用法。
//
// 两阶段不变：prepare 先把标题发出来让窗口立刻出现，内容在 payloadChanged 后填。
// 硬约束：颜色一律 Theme.*；字号整数；Python 的 Property(map/list) 不能当 model，
// 必须 property var + Connections 整体重赋值。
Window {
    id: root
    objectName: "previewWindow"

    // 由 Main.qml 传入，用来算初始位置（放到主窗口右侧，放不下就居中）。
    property var mainWindow: null

    visible: files.preview.open
    // 透明窗口背景才能给圆角/阴影留余地；X11 无合成器时会退化成黑底，
    // 麒麟 UKUI / macOS 默认都有合成器。
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    title: files.preview.title || "预览"

    // 位置状态：placed=false 表示还没定过位（首次显示时按主窗口算），
    // placed=true 后 x/y 由拖动决定，并回写 state.json。homeX/homeY 只用于「复位」判断。
    property bool placed: false
    property real homeX: 0
    property real homeY: 0

    property var pvData: ({})
    property var pvItems: []
    property var pvKinds: []
    property var pvSizes: []
    property string pvLastVpath: ""
    // 解锁请求在途：只让按钮变「解锁中…」，不切 loading —— 切了会把密码框盖住，
    // 密码输错时用户得从头再敲一遍。
    property bool unlocking: false
    readonly property string pvKind: files.preview.kind
    readonly property bool loading: files.preview.state === "loading"

    // 离屏运行时探针（只读）：Repeater 的委托项在 findChildren 里看不到，
    // 用 repeater.count 验证委托真的创建了。
    readonly property bool imageVisible: imageBox.visible
    readonly property bool textVisible: textFlick.visible
    readonly property bool pdfVisible: pdfBox.visible
    readonly property bool pdfListVisible: pdfList.visible
    readonly property int pdfPageCount: root.pvData.pageCount || 0
    // PDF 页图走 image://pdfpage/<token>/<页码>/<宽度>：后端在工作线程里直接
    // 把 QImage 交给 QML，不落盘也不编码（PNG 编码比渲染本身慢 4~14 倍）。
    // 宽度同样量化到 128 的整数倍，拖窗口时只有跨档才重渲染。
    readonly property int pdfDecodeW: {
        var dpr = (Screen.devicePixelRatio > 0) ? Screen.devicePixelRatio : 1
        var want = Math.ceil((pdfList.width - 32) * dpr / 128) * 128
        return Math.max(320, Math.min(2400, want))
    }
    readonly property string pdfToken: root.pvData.token || ""
    function pdfPageAspect(index) {
        if (index < 0 || index >= root.pvSizes.length)
            return 297 / 210
        var s = root.pvSizes[index]
        return (s && s[0] > 0) ? (s[1] / s[0]) : (297 / 210)
    }
    function pdfPageSource(index) {
        if (!root.pdfToken)
            return ""
        return "image://pdfpage/" + root.pdfToken + "/" + index + "/" + root.pdfDecodeW
    }
    // ListView 的委托不在 findChildren 里，itemAtIndex() 又不能写在属性绑定里
    // （函数调用不算依赖，绑定只算一次，永远拿到 null）。改成委托自己往这几个
    // 普通属性里回填：既能验证委托真的建了，也能读到真实高度与解码状态。
    property real pdfFirstItemH: -1
    property real pdfFirstSheetW: -1
    property int pdfFirstImageStatus: -1
    readonly property string pdfNoteText: pdfNote.text
    readonly property bool pdfPasswordVisible: pwPanel.visible
    readonly property string pdfPasswordMessage: pwMessage.text
    readonly property int pdfPasswordEchoMode: pwField.echoMode
    readonly property bool unlockingPdf: root.unlocking
    readonly property bool dirVisible: dirBox.visible
    readonly property bool fallbackVisible: fallbackBox.visible
    readonly property bool overlayVisible: loadingOverlay.visible
    readonly property int dirItemCount: dirRepeater.count
    readonly property int kindTagCount: kindRepeater.count
    readonly property string imageSource: previewImage.source
    readonly property int imageStatus: previewImage.status
    readonly property string shownTitle: titleText.text
    readonly property string shownPath: pathText.text
    readonly property real cardPosX: root.x
    readonly property real cardPosY: root.y
    readonly property bool onTop: (root.flags & Qt.WindowStaysOnTopHint) !== 0
    readonly property bool cardCentered: Math.abs(root.x - root.homeX) < 1 &&
                                         Math.abs(root.y - root.homeY) < 1
    readonly property bool contentFocused: contentRoot.activeFocus
    // 文本区尺寸探针（只读）：验证「按窗口宽度折行」用
    readonly property real textFlickWidth: textFlick.width
    readonly property real textBodyWidth: textBody.width
    readonly property real textContentWidth: textFlick.contentWidth
    readonly property real textContentHeight: textFlick.contentHeight
    readonly property real textImplicitHeight: textBody.implicitHeight
    readonly property real textEditHeight: textBody.height
    readonly property bool textWrapOn: textBody.wrapMode === TextEdit.Wrap
    // 缩放手感：边 6px、角 14px；再宽会盖住内容，再窄不好点。
    readonly property int gripSize: 6
    readonly property int cornerSize: 14
    // 图片解码尺寸按「宽度量化到 256 的整数倍」算：既跟着窗口放大（半屏下 1920 宽
    // 也能取到接近原图的像素），又不会因为拖动时每变 1px 就重新解码。
    readonly property int decodeW: {
        var dpr = (Screen.devicePixelRatio > 0) ? Screen.devicePixelRatio : 1
        var want = Math.ceil(imageBox.width * dpr / 256) * 256
        return Math.max(256, Math.min(2560, want))
    }
    readonly property int decodeH: {
        var iw = root.pvData.width || 0
        var ih = root.pvData.height || 0
        if (iw > 0 && ih > 0)
            return Math.max(256, Math.min(2560, Math.round(root.decodeW * ih / iw)))
        return root.decodeW
    }
    // 显示尺寸：宽 = 内容区宽度（-16 内边距），高按原图比例推 → 竖图会超出视口，
    // 由 Flickable 上下滚动；横图矮于视口时纵向居中。
    readonly property real imgW: Math.max(64, imageBox.width - 16)
    readonly property real imgH: {
        var iw = root.pvData.width || 0
        var ih = root.pvData.height || 0
        if (iw > 0 && ih > 0)
            return Math.max(32, root.imgW * ih / iw)
        return root.imgW
    }
    // 探针（只读）
    readonly property real imageDrawW: previewImage.width
    readonly property real imageDrawH: previewImage.height
    readonly property real imageViewportW: imageFlick.width
    readonly property real imageViewportH: imageFlick.height
    readonly property real imageContentH: imageFlick.contentHeight

    // ---------- 尺寸/位置 ----------
    // 屏幕尺寸优先取主窗口所在屏（副屏也能用），拿不到再退回主屏。
    // 注意：Qt 6 的 Screen 附加类型没有 desktopAvailableX/Y，别碰它们。
    function currentScreen() {        var s = (mainWindow && mainWindow.screen) ? mainWindow.screen : root.screen
        if (!s || !(s.width > 0))
            s = Screen
        return s
    }

    // 提交 PDF 密码。真正的「先 setPassword 再 load」在后端做（QtPdf 只认那一次），
    // 这里只负责把按钮置成在途状态、别让空格/Esc 抢走输入。
    function submitPassword() {
        if (!root.pvData.needsPassword || root.unlocking)
            return
        if (pwField.text.length === 0)
            return
        root.unlocking = true
        files.preview.unlock(pwField.text)
    }

    // 默认「半屏」：宽 = 屏幕一半，高 = 九成屏高。1920×1080 → 960×972。
    // 这是给「左边 Excel 录入、右边看预览」这个用法定的默认值，
    // 用户拖边角缩放后会记住自己的尺寸，不再回到半屏。
    function defaultSize() {
        var s = root.currentScreen()
        var sw = (s && s.width > 0) ? s.width : 1920
        var sh = (s && s.height > 0) ? s.height : 1080
        return {
            w: Math.max(520, Math.min(Math.round(sw * 0.5), sw - 32)),
            h: Math.max(420, Math.min(Math.round(sh * 0.9), sh - 48))
        }
    }

    function placeInitial() {
        var s = root.currentScreen()
        var sw = (s && s.width > 0) ? s.width : 1920
        var sh = (s && s.height > 0) ? s.height : 1080
        var d = root.defaultSize()
        root.width = d.w
        root.height = d.h
        // 贴屏幕右半边、纵向居中：和左边的 Excel/文档刚好拼成两块
        root.homeX = Math.max(8, sw - root.width - 16)
        root.homeY = Math.max(8, Math.round((sh - root.height) / 2))
        root.x = root.homeX
        root.y = root.homeY
        root.placed = true
    }

    // 恢复持久化几何时用：先把尺寸夹进当前屏幕（换了小屏不能比屏幕还大），
    // 再判断是否完全飞出可视区；只要还落在这块屏附近就认，飞出去才重新定位。
    function ensureVisible() {
        var s = root.currentScreen()
        var sw = (s && s.width > 0) ? s.width : 1920
        var sh = (s && s.height > 0) ? s.height : 1080
        root.width = root.clampW(Math.min(root.width, sw - 24))
        root.height = root.clampH(Math.min(root.height, sh - 24))
        var ok = isFinite(root.x) && isFinite(root.y) &&
                 root.x + root.width > -sw && root.x < sw * 2 &&
                 root.y + root.height > -sh && root.y < sh * 2
        if (!ok) {
            root.placed = false
            placeInitial()
        }
    }

    // ---------- 缩放 ----------
    // 最小 520×420：再小标题栏和内容就挤成一团了。最大化不设死上限，跟着屏幕走。
    function clampW(v) { return Math.max(520, Math.min(Math.round(v), 3200)) }
    function clampH(v) { return Math.max(420, Math.min(Math.round(v), 2400)) }

    property int resizeEdges: 0
    property bool resizeManual: false
    property real rsW: 0
    property real rsH: 0
    property real rsX: 0
    property real rsY: 0
    property real rsGX: 0
    property real rsGY: 0

    // gx/gy 是屏幕坐标（热区里用 mapToGlobal 先换好再传进来）。
    // 优先让窗口管理器接管：原生缩放最跟手，也天然遵守系统的最小/最大尺寸。
    function beginResize(edges, gx, gy) {
        root.resizeEdges = edges
        root.resizeManual = false
        var ok = false
        try {
            ok = root.startSystemResize(edges)
        } catch (e) {
            ok = false
        }
        if (ok)
            return
        // 兜底：少数平台/无合成器环境不认 _NET_WM_MOVERESIZE，自己算。
        // 用屏幕坐标而不是局部坐标 —— 左/上边缩放会同时移动窗口，
        // 局部坐标会跟着窗口跑，位移被吃掉（和拖动那条坑同源）。
        root.resizeManual = true
        root.rsW = root.width
        root.rsH = root.height
        root.rsX = root.x
        root.rsY = root.y
        root.rsGX = gx
        root.rsGY = gy
    }

    function dragResize(gx, gy) {
        if (!root.resizeManual || root.resizeEdges === 0)
            return
        var dx = gx - root.rsGX
        var dy = gy - root.rsGY
        if (root.resizeEdges & Qt.LeftEdge) {
            let w = root.clampW(root.rsW - dx)
            root.width = w
            root.x = root.rsX + (root.rsW - w)
        } else if (root.resizeEdges & Qt.RightEdge) {
            root.width = root.clampW(root.rsW + dx)
        }
        if (root.resizeEdges & Qt.TopEdge) {
            let h = root.clampH(root.rsH - dy)
            root.height = h
            root.y = root.rsY + (root.rsH - h)
        } else if (root.resizeEdges & Qt.BottomEdge) {
            root.height = root.clampH(root.rsH + dy)
        }
    }

    function endResize() {
        if (!root.resizeManual)
            return
        root.resizeManual = false
        root.resizeEdges = 0
        files.savePreviewWindow(root.x, root.y, root.width, root.height)
    }

    function resetPos() {
        root.placed = false
        placeInitial()
        files.savePreviewWindow(root.x, root.y, root.width, root.height)
    }

    // 原生缩放（startSystemResize）拿不到 released 事件，尺寸只能靠变化后防抖落盘；
    // 拖动同理。600ms 足够把一次拖拽合并成一次写盘。
    Timer {
        id: geoSave
        interval: 600
        repeat: false
        onTriggered: {
            if (root.visible && root.placed)
                files.savePreviewWindow(root.x, root.y, root.width, root.height)
        }
    }
    onWidthChanged: { if (root.visible && root.placed) geoSave.restart() }
    onHeightChanged: { if (root.visible && root.placed) geoSave.restart() }
    onXChanged: { if (root.visible && root.placed) geoSave.restart() }
    onYChanged: { if (root.visible && root.placed) geoSave.restart() }

    Component.onCompleted: {
        // 先按半屏给个初始尺寸（窗口不显示时不抢眼），真正定位在首次 visible 时做，
        // 那时主窗口几何才是最终的。
        var d = root.defaultSize()
        root.width = d.w
        root.height = d.h
        var s = files.previewWindow
        if (s && s.placed) {
            root.width = root.clampW(s.w)
            root.height = root.clampH(s.h)
            root.x = s.x
            root.y = s.y
            root.homeX = s.x
            root.homeY = s.y
            root.placed = true
            ensureVisible()
        }
    }

    // 无边框窗口点过标题栏/空白处之后 Qt 会把 activeFocusItem 清成 null，
    // ↑↓ 就没人接了。下面三处把焦点补回内容根：
    //   active 变化（切回本窗口）、visible 变化（每次打开）、以及焦点项被清空时。

    // 点过标题栏/内容空白后 Qt 会把 activeFocusItem 清成 null，↑↓ 就没人接了。
    // 只在「完全没有焦点项」时补回内容根，不动 TextEdit 那类真实焦点项。
    onActiveChanged: {
        if (active && root.visible)
            contentRoot.forceActiveFocus()
    }
    onActiveFocusItemChanged: {
        if (!activeFocusItem && root.visible)
            contentRoot.forceActiveFocus()
    }

    onVisibleChanged: {
        if (!visible)
            return
        if (!root.placed)
            root.placeInitial()
        else
            root.ensureVisible()
        // 让 ↑↓ / Esc 能直接用；用户随后点回 Excel 也不影响预览常驻
        root.requestActivate()
        contentRoot.forceActiveFocus()
    }


    Connections {
        target: files.preview
        function onPayloadChanged() {
            root.pvData = files.preview.payload
            root.pvItems = root.pvData.items ? root.pvData.items : []
            root.pvKinds = root.pvData.kinds ? root.pvData.kinds : []
            root.pvSizes = root.pvData.sizes ? root.pvData.sizes : []
            var vp = files.preview.currentVpath
            var isNew = (vp !== root.pvLastVpath)
            if (isNew) {
                root.pvLastVpath = vp
                pwField.text = ""          // 别把上一个文件的密码留在框里
                if (root.pdfPageCount > 0)
                    pdfList.positionViewAtBeginning()
            }
            root.unlocking = false
            // 加密 PDF：直接把焦点给密码框，省得用户再点一下
            if (root.pvData.needsPassword) {
                pwField.forceActiveFocus()
            } else if (pwField.activeFocus) {
                // 解锁成功 / 换到不需要密码的文件：把焦点还给内容根，
                // 否则空格、Esc 全被（已隐藏的）输入框吃掉。
                contentRoot.forceActiveFocus()
            }
        }
        function onClosed() {
            files.savePreviewWindow(root.x, root.y, root.width, root.height)
        }
    }

    Item {
        id: contentRoot
        anchors.fill: parent
        // 让窗口有 activeFocusItem：没有焦点项时按键事件走不到这里。
        focus: true
        // 快捷键用 Keys 而不是 Shortcut：独立窗口里 Shortcut 依赖 shortcutMap，
        // 没有焦点项时不触发；Keys 跟着焦点走，行为和主窗口那套一致，也便于离屏验证。
        // TextEdit 有焦点时未消费的键会冒泡上来，Esc/↑↓ 照样生效。
        Keys.onPressed: function(event) {
            if (event.key === Qt.Key_Escape || event.key === Qt.Key_Space) {
                files.preview.close()
                event.accepted = true
            } else if (event.key === Qt.Key_Up) {
                files.preview.step(-1)
                event.accepted = true
            } else if (event.key === Qt.Key_Down) {
                files.preview.step(1)
                event.accepted = true
            }
        }

        Card {
            id: previewCard
            objectName: "previewCard"
            anchors.fill: parent
            anchors.margins: 8

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 0

                // ---------- 标题栏（同时是拖动区） ----------
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

                    // 拖动区：右边界让给 closeBtn（closeBtn 声明在其后、层级更高，不抢点击）。
                    //
                    // 坐标必须用「屏幕坐标 + 按下快照」，原因是个反馈回路：
                    // MouseArea 给的 mouse.x 是「窗口内局部坐标」。窗口跟着鼠标一动，同一个
                    // 屏幕位置对应的局部坐标就变了 —— 按下后每移动一次，局部差值里已经扣掉了
                    // 上一次自己造成的窗口位移，于是每两步只走一步（实测跟随率正好 50%，
                    // 肉眼就是一顿一顿的抖）。mapToGlobal 换出来的是真实光标位置，与窗口
                    // 当前在哪无关，所以「基准位置 + (当前光标 - 按下光标)」是干净的一次求解，
                    // 也不会累积误差。缩放手柄用的是同一套算法。
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
                        property real pressGX: 0
                        property real pressGY: 0
                        property real baseX: 0
                        property real baseY: 0
                        onPressed: function(mouse) {
                            var g = titleDrag.mapToGlobal(mouse.x, mouse.y)
                            titleDrag.pressGX = g.x
                            titleDrag.pressGY = g.y
                            titleDrag.baseX = root.x
                            titleDrag.baseY = root.y
                        }
                        onPositionChanged: function(mouse) {
                            if (!titleDrag.pressed)
                                return
                            var g = titleDrag.mapToGlobal(mouse.x, mouse.y)
                            root.x = titleDrag.baseX + (g.x - titleDrag.pressGX)
                            root.y = titleDrag.baseY + (g.y - titleDrag.pressGY)
                        }
                        onReleased: files.savePreviewWindow(root.x, root.y, root.width, root.height)
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

                    // 图片：按「宽度铺满」显示，比视口高就上下滚动（竖版扫描件不会被缩成一条）。
                    // 解码尺寸跟着窗口宽度走（量化到 256，拖边角时不会疯狂重解码）。
                    Rectangle {
                        id: imageBox
                        anchors.fill: parent
                        radius: 9
                        color: Theme.bg
                        visible: root.pvKind === "image"

                        Flickable {
                            id: imageFlick
                            anchors.fill: parent
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            contentWidth: width
                            contentHeight: Math.max(height, root.imgH + 16)
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                            Image {
                                id: previewImage
                                x: 8
                                // 比视口矮就纵向居中，比视口高就从头看、往下滚
                                y: 8 + Math.max(0, (imageFlick.height - root.imgH) / 2)
                                width: root.imgW
                                height: root.imgH
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                                cache: false
                                sourceSize.width: root.decodeW
                                sourceSize.height: root.decodeH
                                source: (root.pvKind === "image" && root.pvData.source &&
                                         !root.pvData.tooLarge) ? root.pvData.source : ""
                            }
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
                        // 尺寸信息做成右下角的小药丸：图片一旦铺满，纯文字压在图上看不清
                        Rectangle {
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 14
                            width: sizeText.implicitWidth + 16
                            height: 22
                            radius: 6
                            color: Theme.surface
                            opacity: 0.9
                            visible: root.pvKind === "image" &&
                                     previewImage.status === Image.Ready &&
                                     root.pvData.width > 0
                            Text {
                                id: sizeText
                                anchors.centerIn: parent
                                text: root.pvData.width + " × " + root.pvData.height +
                                      " · " + root.pvData.format
                                font.pixelSize: 11
                                color: Theme.textMuted
                            }
                        }
                    }

                    // PDF：按页竖排、宽度铺满，可以整篇顺着往下看（逐个审核的用法）。
                    // ListView 只创建看得见的一两页；每页的位图由 image://pdfpage
                    // 在后台按需渲染，滚到哪渲到哪，打开文件时不做任何栅格化。
                    Item {
                        id: pdfBox
                        anchors.fill: parent
                        visible: root.pvKind === "pdf"

                        ListView {
                            id: pdfList
                            anchors.fill: parent
                            visible: root.pdfPageCount > 0 && !root.pvData.error
                            clip: true
                            model: root.pdfPageCount
                            spacing: 14
                            topMargin: 12
                            bottomMargin: 12
                            cacheBuffer: 200
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                            delegate: Item {
                                required property int index
                                width: pdfList.width
                                height: sheet.height + 26
                                // 只上报第一页，供离屏探针读（见文件头 pdfFirstItemH 说明）
                                onHeightChanged: {
                                    if (index === 0)
                                        root.pdfFirstItemH = height
                                }
                                Rectangle {
                                    id: sheet
                                    // 页宽 = 视口宽 - 两侧留白；高度按这一页的真实 pt
                                    // 比例算（后端把 /Rotate 也吃进去了），横版页不变形。
                                    x: Math.round((parent.width - width) / 2)
                                    width: Math.max(120, pdfList.width - 32)
                                    height: Math.max(60, width * root.pdfPageAspect(index))
                                    radius: 4
                                    color: "#FFFFFF"
                                    border.width: 1
                                    border.color: Theme.line
                                    onWidthChanged: {
                                        if (index === 0)
                                            root.pdfFirstSheetW = width
                                    }
                                    Image {
                                        anchors.fill: parent
                                        anchors.margins: 1
                                        source: root.pdfPageSource(index)
                                        asynchronous: true
                                        // 关掉 cache：滚走就把纹理放掉，内存只留
                                        // provider 里那几页 LRU，不会堆整本
                                        cache: false
                                        fillMode: Image.PreserveAspectFit
                                        onStatusChanged: {
                                            if (index === 0)
                                                root.pdfFirstImageStatus = status
                                        }
                                    }
                                }
                                Text {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    anchors.bottom: parent.bottom
                                    text: "第 " + (index + 1) + " / " + root.pdfPageCount + " 页"
                                    font.pixelSize: 11
                                    color: Theme.textMuted
                                }
                            }
                        }

                        // 页数提示：贴右下角，和图片预览的尺寸角标同一个位置约定；
                        // 放顶部会压住页面的第一行正文。
                        Rectangle {
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.rightMargin: 22          // 让开右侧滚动条
                            anchors.bottomMargin: 14
                            width: pdfNote.implicitWidth + 16
                            height: 22
                            radius: 6
                            color: Theme.surface
                            opacity: 0.92
                            visible: !root.pvData.error && root.pdfPageCount > 0 &&
                                     !!root.pvData.note
                            Text {
                                id: pdfNote
                                anchors.centerIn: parent
                                text: root.pvData.note || ""
                                font.pixelSize: 11
                                color: Theme.textMuted
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            visible: !!root.pvData.error
                            width: parent.width - 40
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.Wrap
                            text: root.pvData.error || ""
                            font.pixelSize: 12
                            color: Theme.textMuted
                        }

                        // 加密 PDF：内容区让给一张小卡，输密码解锁（Enter 直接提交）。
                        // 此时本来就没有页可看，不用担心挡内容。
                        Rectangle {
                            id: pwPanel
                            objectName: "pdfPasswordPanel"
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 56, 360)
                            height: pwCol.implicitHeight + 36
                            radius: 12
                            color: Theme.bg
                            border.width: 1
                            border.color: Theme.line
                            visible: !!root.pvData.needsPassword
                            z: 6

                            ColumnLayout {
                                id: pwCol
                                anchors.fill: parent
                                anchors.margins: 18
                                spacing: 10

                                Text {
                                    Layout.fillWidth: true
                                    text: "此 PDF 已加密"
                                    font.pixelSize: 13
                                    font.weight: Font.DemiBold
                                    color: Theme.text
                                }
                                Text {
                                    id: pwMessage
                                    Layout.fillWidth: true
                                    wrapMode: Text.Wrap
                                    text: root.pvData.passwordError
                                          ? root.pvData.passwordError
                                          : "输入打开密码后预览"
                                    font.pixelSize: 11
                                    color: root.pvData.passwordError ? Theme.danger
                                                                     : Theme.textMuted
                                }
                                Field {
                                    id: pwField
                                    objectName: "pdfPasswordField"
                                    Layout.fillWidth: true
                                    echoMode: TextInput.Password
                                    placeholderText: "PDF 密码"
                                    enabled: !root.unlocking
                                    onAccepted: root.submitPassword()
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Item { Layout.fillWidth: true }
                                    Btn {
                                        objectName: "pdfUnlockBtn"
                                        text: root.unlocking ? "解锁中…" : "解锁"
                                        primary: true
                                        enabled: !root.unlocking && pwField.text.length > 0
                                        onClicked: root.submitPassword()
                                    }
                                }
                            }
                        }
                    }

                    // 文本 / 代码：只读可选中，按窗口宽度自动折行（不再横向滚）。
                    // 半屏宽度下 40~80 字的记录一行就能看全，竖着滚比左右拖舒服。
                    Flickable {
                        id: textFlick
                        anchors.fill: parent
                        clip: true
                        visible: root.pvKind === "text"
                        boundsBehavior: Flickable.StopAtBounds
                        contentWidth: width
                        contentHeight: Math.max(height, textBody.implicitHeight + 24)
                        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                        TextEdit {
                            id: textBody
                            x: 12; y: 12
                            width: Math.max(80, textFlick.width - 24)
                            text: root.pvKind === "text" ? (root.pvData.text || "") : ""
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
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
                        // 宽了才带缩放/拖动说明；窄了优先保住「怎么关」，免得把路径挤没
                        text: root.width >= 860
                              ? "拖动边角缩放 · 双击标题栏复位 · ↑↓ 切换 · 空格 / Esc 关闭"
                              : (root.width >= 600
                                 ? "双击复位 · ↑↓ 切换 · 空格 / Esc 关闭"
                                 : "空格 / Esc 关闭")
                        font.pixelSize: 11
                        color: Theme.textMuted
                    }
                }
            }
        }

        // ---------- 缩放手柄：无边框窗口没有系统边框，四边四角自己造 ----------
        // 放在 Card 之后 → 层级更高，压住卡片的鼠标区；落在卡片 8px 外边距里，
        // 不挡内容。优先 startSystemResize（原生），失败才走 dragResize 自己算。
        MouseArea { // 上
            objectName: "gripTop"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.top: parent.top
            height: root.gripSize
            z: 30
            hoverEnabled: true
            cursorShape: Qt.SizeVerCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.TopEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 下
            objectName: "gripBottom"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.bottom: parent.bottom
            height: root.gripSize
            z: 30
            hoverEnabled: true
            cursorShape: Qt.SizeVerCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.BottomEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 左
            objectName: "gripLeft"
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 14
            anchors.bottomMargin: 14
            anchors.left: parent.left
            width: root.gripSize
            z: 30
            hoverEnabled: true
            cursorShape: Qt.SizeHorCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.LeftEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 右
            objectName: "gripRight"
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 14
            anchors.bottomMargin: 14
            anchors.right: parent.right
            width: root.gripSize
            z: 30
            hoverEnabled: true
            cursorShape: Qt.SizeHorCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.RightEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 左上
            objectName: "gripTopLeft"
            anchors.left: parent.left
            anchors.top: parent.top
            width: root.cornerSize
            height: root.cornerSize
            z: 31
            hoverEnabled: true
            cursorShape: Qt.SizeFDiagCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.LeftEdge | Qt.TopEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 右上
            objectName: "gripTopRight"
            anchors.right: parent.right
            anchors.top: parent.top
            width: root.cornerSize
            height: root.cornerSize
            z: 31
            hoverEnabled: true
            cursorShape: Qt.SizeBDiagCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.RightEdge | Qt.TopEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 左下
            objectName: "gripBottomLeft"
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            width: root.cornerSize
            height: root.cornerSize
            z: 31
            hoverEnabled: true
            cursorShape: Qt.SizeBDiagCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.LeftEdge | Qt.BottomEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
        MouseArea { // 右下
            objectName: "gripBottomRight"
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            width: root.cornerSize
            height: root.cornerSize
            z: 31
            hoverEnabled: true
            cursorShape: Qt.SizeFDiagCursor
            onPressed: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.beginResize(Qt.RightEdge | Qt.BottomEdge, g.x, g.y) }
            onPositionChanged: function(m) { var g = parent.mapToGlobal(m.x, m.y); root.dragResize(g.x, g.y) }
            onReleased: root.endResize()
        }
    }
}
