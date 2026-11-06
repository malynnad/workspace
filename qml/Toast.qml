pragma Singleton
import QtQuick

// 全局轻提示：任意页面调用 Toast.show("已保存")，
// 由 Main.qml 里的 ToastHost 统一渲染，不打断操作流。
QtObject {
    id: root

    signal message(string text)

    function show(text) {
        root.message(text)
    }
}
