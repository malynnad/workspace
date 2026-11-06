import QtQuick

// 圆角卡片 + 极轻投影（层次用两层矩形叠，不用 effect，软渲染也快）
Item {
    id: root
    default property alias content: body.data
    property alias fillColor: body.color
    property alias bodyRadius: body.radius
    property int lift: 4          // 投影下沉像素

    Rectangle {
        anchors.fill: body
        anchors.topMargin: root.lift
        radius: body.radius
        color: "#0B000000"
    }

    Rectangle {
        id: body
        anchors.fill: parent
        anchors.bottomMargin: root.lift
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: "#EFF1F7"
    }
}
