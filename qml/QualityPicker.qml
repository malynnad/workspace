import QtQuick

// 设置面板里的「一组三档清晰度」，横向分段控件（一个胶囊轨道 + 一块会滑的
// 白块）。图片与 PDF 各用一个实例，值走 filecore/state.py 的 QUALITY_CHOICES
// 白名单（1.0 / 1.5 / 2.0）。
//
// 原来是纵向三行带单选圈：一组 6+11+3×34 ≈ 124px，两组连标题吃掉面板 260px，
// 窗口一小就把下面整节挤到折叠线以下，还得手动滚。改横向后一组 44px，两组
// 88px，省下约 170px —— 设置面板天生该在一屏里看完。
//
// 档位的倍数（1× / 1.5× / 2×）留在段内，因为麒麟机 8GB 内存，选清晰度本质是
// 换内存，这个数字比「标准 / 清晰 / 极高」三个形容词更有信息量。原来的长描述
// 缩成一句短语，跟着当前档走，放在轨道下方一行。
Item {
    id: root
    property string title: ""
    // 每项：{ v: real, label: string, mul: string, desc: string }
    property var choices: []
    property real current: 0
    signal pick(real value)

    readonly property int labelWidth: 36       // 「图片」「PDF」
    readonly property int trackHeight: 28
    readonly property int segCount: Math.max(1, choices.length)

    // 当前档在 choices 里的下标。后端存的可能是 1.0 / 1.5 / 2.0 中任意一个，
    // 也可能因为手改 state.json 是空值或越界值 —— 对不上就兜到第 0 档（与面板
    // 上方「按后端默认值显示」一致），绝不返回 -1：滑块会落到轨道外面。
    readonly property int selectedIndex: {
        for (var i = 0; i < choices.length; i++) {
            if (Math.abs(choices[i].v - current) < 0.001)
                return i
        }
        return 0
    }

    implicitHeight: trackHeight + 2 + hint.implicitHeight

    Text {
        id: titleLabel
        x: 0
        width: root.labelWidth
        anchors.verticalCenter: track.verticalCenter
        text: root.title
        font.pixelSize: 12
        color: Theme.text
        elide: Text.ElideRight
    }

    Rectangle {
        id: track
        x: root.labelWidth + 4
        y: 0
        width: Math.max(0, root.width - x)
        height: root.trackHeight
        radius: 9
        color: Theme.bg
        // 光靠 Theme.bg 在白面板上几乎看不出胶囊轮廓，白块会像「孤零零浮着」，
        // 描一圈细边把轨道收成一整块。
        border.width: 1
        border.color: Theme.line
        antialiasing: true            // Rectangle 圆角默认是锯齿多边形

        // 选中块。x 有绑定、又挂了 Behavior，所以换档时是滑过去而不是瞬移。
        Rectangle {
            id: thumb
            objectName: "qualityThumb"
            readonly property real segW:
                (track.width - 6) / root.segCount
            x: 3 + root.selectedIndex * segW
            y: 3
            width: segW
            height: track.height - 6
            radius: 6
            color: Theme.surface
            border.width: 1
            border.color: Theme.line
            antialiasing: true
            Behavior on x {
                NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
            }
        }

        Repeater {
            model: root.choices
            delegate: Item {
                id: seg
                required property int index
                required property var modelData
                readonly property real segW: (track.width - 6) / root.segCount
                readonly property bool picked: index === root.selectedIndex

                x: 3 + index * segW
                y: 0
                width: segW
                height: track.height

                Text {
                    anchors.centerIn: parent
                    text: seg.modelData.label + " " + seg.modelData.mul
                    font.pixelSize: 11
                    font.weight: seg.picked ? Font.DemiBold : Font.Normal
                    // 选中靠白块 + 字重表达，不给颜色：面板里只该有一个强调色
                    // （accent 留给「添加」这类有提交语义的按钮）。
                    color: seg.picked ? Theme.text
                                      : (segMouse.containsMouse ? Theme.text
                                                                : Theme.textMuted)
                }

                MouseArea {
                    id: segMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (!seg.picked) root.pick(seg.modelData.v)
                }
            }
        }
    }

    Text {
        id: hint
        x: track.x + 4
        y: track.y + track.height + 2
        text: root.choices[root.selectedIndex]
              ? root.choices[root.selectedIndex].desc : ""
        font.pixelSize: 10
        color: Theme.textMuted
    }
}
