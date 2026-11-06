import QtQuick

// 线描图标（Canvas 手绘，不依赖任何图标库）
Item {
    id: root
    property string name: "log"
    property color color: Theme.textMuted
    implicitWidth: 16
    implicitHeight: 16

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = root.color
            ctx.lineWidth = 1.6
            ctx.lineCap = "round"
            ctx.lineJoin = "round"

            var w = width
            var h = height

            if (root.name === "log") {
                // 日历
                ctx.strokeRect(2.0, 4.0, w - 4.0, h - 6.0)
                ctx.beginPath(); ctx.moveTo(2.0, 7.6); ctx.lineTo(w - 2.0, 7.6); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(5.6, 2.0); ctx.lineTo(5.6, 5.2); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(w - 5.6, 2.0); ctx.lineTo(w - 5.6, 5.2); ctx.stroke()
            } else if (root.name === "todo") {
                ctx.beginPath()
                ctx.arc(w / 2, h / 2, w / 2 - 1.4, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.30, h * 0.52)
                ctx.lineTo(w * 0.44, h * 0.66)
                ctx.lineTo(w * 0.71, h * 0.35)
                ctx.stroke()
            } else if (root.name === "script") {
                ctx.strokeRect(1.6, 3.0, w - 3.2, h - 6.0)
                ctx.beginPath()
                ctx.moveTo(5.0, h * 0.38)
                ctx.lineTo(7.4, h * 0.50)
                ctx.lineTo(5.0, h * 0.62)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(9.0, h * 0.64)
                ctx.lineTo(11.8, h * 0.64)
                ctx.stroke()
            } else if (root.name === "check") {
                ctx.beginPath()
                ctx.moveTo(w * 0.20, h * 0.52)
                ctx.lineTo(w * 0.42, h * 0.74)
                ctx.lineTo(w * 0.80, h * 0.26)
                ctx.stroke()
            } else if (root.name === "clock") {
                ctx.beginPath()
                ctx.arc(w / 2, h / 2, w / 2 - 1.2, 0, Math.PI * 2)
                ctx.stroke()
                // 时针指向 12，分针指向 4 点方向
                ctx.beginPath()
                ctx.moveTo(w / 2, h / 2)
                ctx.lineTo(w / 2, h * 0.26)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w / 2, h / 2)
                ctx.lineTo(w * 0.70, h * 0.58)
                ctx.stroke()
            } else if (root.name === "search") {
                ctx.beginPath()
                ctx.arc(w * 0.42, h * 0.42, w * 0.28, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.63, h * 0.63)
                ctx.lineTo(w * 0.86, h * 0.86)
                ctx.stroke()
            } else if (root.name === "back") {
                ctx.beginPath()
                ctx.moveTo(w * 0.80, h * 0.50)
                ctx.lineTo(w * 0.20, h * 0.50)
                ctx.moveTo(w * 0.42, h * 0.28)
                ctx.lineTo(w * 0.20, h * 0.50)
                ctx.lineTo(w * 0.42, h * 0.72)
                ctx.stroke()
            } else if (root.name === "forward") {
                ctx.beginPath()
                ctx.moveTo(w * 0.20, h * 0.50)
                ctx.lineTo(w * 0.80, h * 0.50)
                ctx.moveTo(w * 0.58, h * 0.28)
                ctx.lineTo(w * 0.80, h * 0.50)
                ctx.lineTo(w * 0.58, h * 0.72)
                ctx.stroke()
            } else if (root.name === "up") {
                ctx.beginPath()
                ctx.moveTo(w * 0.50, h * 0.82)
                ctx.lineTo(w * 0.50, h * 0.20)
                ctx.moveTo(w * 0.28, h * 0.42)
                ctx.lineTo(w * 0.50, h * 0.20)
                ctx.lineTo(w * 0.72, h * 0.42)
                ctx.stroke()
            } else if (root.name === "home") {
                ctx.beginPath()
                ctx.moveTo(w * 0.10, h * 0.48)
                ctx.lineTo(w * 0.50, h * 0.16)
                ctx.lineTo(w * 0.90, h * 0.48)
                ctx.moveTo(w * 0.24, h * 0.44)
                ctx.lineTo(w * 0.24, h * 0.84)
                ctx.lineTo(w * 0.76, h * 0.84)
                ctx.lineTo(w * 0.76, h * 0.44)
                ctx.stroke()
            } else if (root.name === "refresh") {
                ctx.beginPath()
                ctx.arc(w * 0.50, h * 0.50, w * 0.30, -Math.PI * 0.65, Math.PI * 1.05)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.76, h * 0.12)
                ctx.lineTo(w * 0.80, h * 0.36)
                ctx.lineTo(w * 0.56, h * 0.36)
                ctx.stroke()
            } else if (root.name === "hidden") {
                ctx.beginPath()
                ctx.arc(w * 0.50, h * 0.50, w * 0.26, 0, Math.PI * 2)
                ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.16, h * 0.84)
                ctx.lineTo(w * 0.84, h * 0.16)
                ctx.stroke()
            } else if (root.name === "sort") {
                ctx.beginPath()
                ctx.moveTo(w * 0.18, h * 0.30); ctx.lineTo(w * 0.82, h * 0.30); ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.18, h * 0.52); ctx.lineTo(w * 0.66, h * 0.52); ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.18, h * 0.74); ctx.lineTo(w * 0.50, h * 0.74); ctx.stroke()
            } else if (root.name === "star") {
                // 收藏：五角星（十点交替外径/内径）
                ctx.beginPath()
                var cx = w * 0.50
                var cy = h * 0.50
                var ro = Math.min(w, h) * 0.44
                var ri = ro * 0.43
                for (var i = 0; i < 10; ++i) {
                    var ang = -Math.PI / 2 + i * Math.PI / 5
                    var rad = (i % 2 === 0) ? ro : ri
                    var px = cx + Math.cos(ang) * rad
                    var py = cy + Math.sin(ang) * rad
                    if (i === 0) ctx.moveTo(px, py)
                    else ctx.lineTo(px, py)
                }
                ctx.closePath()
                ctx.stroke()
            } else if (root.name === "plus") {
                // 添加收藏
                ctx.beginPath()
                ctx.moveTo(w * 0.50, h * 0.20); ctx.lineTo(w * 0.50, h * 0.80); ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.20, h * 0.50); ctx.lineTo(w * 0.80, h * 0.50); ctx.stroke()
            } else if (root.name === "close") {
                // 移除收藏
                ctx.beginPath()
                ctx.moveTo(w * 0.28, h * 0.28); ctx.lineTo(w * 0.72, h * 0.72); ctx.stroke()
                ctx.beginPath()
                ctx.moveTo(w * 0.72, h * 0.28); ctx.lineTo(w * 0.28, h * 0.72); ctx.stroke()
            } else { // folder（兜底：未知 name 一律画文件夹，新图标必须插在本分支之前）
                ctx.beginPath()
                ctx.moveTo(1.8, h - 3.4)
                ctx.lineTo(1.8, 3.4)
                ctx.lineTo(w * 0.40, 3.4)
                ctx.lineTo(w * 0.50, 5.6)
                ctx.lineTo(w - 1.8, 5.6)
                ctx.lineTo(w - 1.8, h - 3.4)
                ctx.closePath()
                ctx.stroke()
            }
        }
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
    }

    onColorChanged: canvas.requestPaint()
    onNameChanged: canvas.requestPaint()
}
