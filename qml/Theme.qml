pragma Singleton
import QtQuick

QtObject {
    // Workspace 设计 token —— 参照 macOS 风浅色主题
    readonly property color bg: "#F2F3F7"
    readonly property color surface: "#FFFFFF"
    readonly property color accent: "#6A8DFF"
    readonly property color accentSoft: "#EDF1FF"
    readonly property color text: "#1C1F26"
    readonly property color textMuted: "#8A90A0"
    readonly property color line: "#ECEEF4"
    readonly property color danger: "#E86A6A"
    readonly property color warn: "#E8A13A"
    readonly property color todo: "#7C6BF0"          // 待办专属色（紫）——与主色蓝区分
    readonly property color todoSoft: "#F1EEFF"
    readonly property color ok: "#3FB984"
    // 文件类型点：一色一义 —— 颜色本身要能联想到文件类型（跟 Windows / macOS 的
    // 图标配色是同一套语言，用户不用学）。类型色统一压在同一档感知亮度
    // （Y≈0.19，白底对比度 4.3:1）上只靠色相区分，所以列表滚动时不会有某个类型
    // 特别扎眼。
    //
    // 文件夹靠「三件事」跳出来，不靠亮度（第一版用 #6A8DFF / Y=0.293，白底只有
    // 3.06:1 —— 越亮越贴背景，看着就"不鲜艳"，Danny 当场投诉过）：
    //   · 彩度 C*≈79，是全场最高（其余类型平均 31，最高的 video 才 44）
    //   · 形状是方（radius≈边长 29%），文件是正圆 —— 方块有体量，圆点显轻
    //   · 尺寸大 4px（16 vs 12，面积差 78%）—— 叠在上面两条之上
    //
    // 色相分区（H 从大到小排过，新加类型先看哪个空档还没人占）：
    //   洋红 323 image / 紫 265 video·248 todo / 蓝 229 folder·215 word·222 textFile
    //   青 192 code / 绿 130 excel·154 ok / 橙黄 36 warn·38 audio / 橙红 20 ppt
    //   砖红 8 pdf / 红 0 danger
    //
    // 下面四处是「故意拉开」的，改色之前先看，别把距离抹平：
    //   · pdf 砖红 H8° 和 danger 红 H0° 同色相，靠亮度硬分：Y 0.150 vs 0.285，
    //     暗 47% —— 看着是「印刷红」，不是「危险红」。
    //   · audio 琥珀 H38° 和 warn 橙 H36° 同理，Y 0.160 比 warn 暗 63%。
    //   · word 蓝 H215° 和文件夹蓝 H229° 只差 14°，靠彩度：C* 34 vs 79。
    //   · code 深青 H192° 压到 Y 0.094 —— 全表最暗的一个，故意「沉」下去。
    //     不往绿区凑（那是 excel 的地盘），也不跟 textFile 灰靠亮度相近：
    //     Y 0.094 vs 0.194，py 和 txt 并排也一眼能分。
    //
    // folder 是独立 token，**不是 accent**：accent 还要给按钮、选中文字、链接用，
    // 那些地方需要 #6A8DFF 这种亮蓝才好看，而这个蓝当方块太淡。两者同色相（229°），
    // 深浅相差一档，放在一起仍是同一套。
    readonly property color folder: "#4F6EF7"    // 文件夹：浓蓝（唯一高彩度）
    readonly property color image: "#B26E93"     // 图片：洋红（相册、胶片）
    readonly property color video: "#8B6CB6"     // 视频：紫（播放器、影院）
    readonly property color audio: "#876B3A"     // 音频：琥珀（声音、木质音箱）
    readonly property color pdf: "#A75447"       // PDF：砖红（Adobe、印刷）
    readonly property color word: "#5E87C0"      // Word：蓝
    readonly property color excel: "#43884F"     // Excel：草绿（表格、单元格）
    readonly property color ppt: "#BD724D"       // PPT：橙红
    readonly property color archive: "#897767"   // 压缩包：低饱和牛皮（纸箱）
    readonly property color code: "#1F5E6E"      // 代码：深青（终端、脚本）
    // 文本文件的中性灰。别命名为 text —— 上面的 text 是正文文字色，语义无关。
    readonly property color textFile: "#767A83"

    // 类型块的边长与圆角。列表用 16（文件夹）/ 12（文件），预览窗的目录列表
    // 小一号 12 / 8。两处都走这里，别各写一套硬编码。
    //
    // 边长必须能被 4 整除 —— 界面缩放 QT_SCALE_FACTOR=1.25 时，16/12 正好落在
    // 20/15 个物理像素上；换成 15/13 就是 18.75/16.25，圆的四条边各差半像素，
    // 软件渲染下会冒出四个小凸起（放大 20 倍能看见）。
    readonly property int kindBox: 16
    readonly property int kindBoxSm: 12

    // 方 = 文件夹，圆 = 文件。文件固定比文件夹小 4px —— 面积差 78%，加上形状
    // 和彩度，文件夹在列表里三重叠加地跳出来。size/2 正好是正圆；folder 取边长
    // 29% 的圆角，在 16px 上就是 radius 5 —— 一眼是方的，边又不扎手。
    function kindRadius(kind, size) {
        return kind === "folder" ? Math.round(size * 0.29) : size / 2
    }

    readonly property int radius: 16
    readonly property int radiusSm: 10
    readonly property int pad: 20

    // 类型 → 色块。文件列表 delegate 不许用 Icon（软渲染约束），类型识别全靠这
    // 个小色块，所以两处（FmPanel 列表、文件夹预览）都走这里，别各写一套。
    function kindColor(kind) {
        switch (kind) {
        case "folder": return folder
        case "image": return image
        case "video": return video
        case "audio": return audio
        case "pdf": return pdf
        case "word": return word
        case "excel": return excel
        case "ppt": return ppt
        case "archive": return archive
        case "code": return code
        case "text": return textFile
        default: return line
        }
    }

    // 同上，但「没专门颜色」时回落到 accent 而不是 line。
    // 给预览窗标题栏图标用：图标本身是线描，走 line 就看不见了。
    function kindAccent(kind) {
        // 预览窗的 sourceKind 仍是 "office"（OfficePreviewer 把三类合成一个预览器，
        // 版面路径还会返回 kind="pdf"），所以这里单独给它 Word 蓝。
        if (kind === "office") return word
        var c = kindColor(kind)
        return c === line ? accent : c
    }

    // 「认不出类型」的那些（字体、其他、未知扩展名）画成空心圆：实心 = 有类型色，
    // 空心 = 没配色。不能拿 line 当实心填充 —— 那层极浅灰在白底上 13px 圆圈里
    // 基本看不见，行首会像缺了一块。
    function kindHollow(kind) {
        return kindColor(kind) === line
    }
}
