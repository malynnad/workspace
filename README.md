# Workspace · 个人工作台

PySide6 + QML 的工作台壳：工作日志（日历）/ 待办 / 脚本面板 / 文件管理（双面板浏览）。
设计语言：macOS 风浅色主题、圆角卡片、柔和阴影。

## 在 Mac 上跑

```bash
cd /Users/dannylam/WorkBuddy/Workspace/workspace
.venv/bin/python main.py        # 虚拟环境已建好，PySide6 6.12 已装
```

VSCode 里直接按运行按钮 / `python3 main.py` 也行：main.py 会发现自己没 PySide6，
自动切到 `.venv/bin/python` 重跑。项目已带 `.vscode/settings.json`，把解释器和
Code Runner 都指向 `.venv`。

**解释器固定为 Python 3.13**：3.14 下 PySide6 6.12 跑本项目会出现
`Cannot read property 'xxx' of null`（backend/scripts 两个上下文属性在 QML 里取到 null），
页面数据全空，别升 3.14。

## 界面缩放

`QT_SCALE_FACTOR` 是 Qt 逻辑像素的全局乘数，设了它字号 / 控件高 / 内边距 / 图标一起等比放大，
QML 代码不用改。**Linux 默认 1.25**（目标机 1920×1080 系统缩放 100% 下 11px 正文明细偏小），
Mac 默认 1.0（Retina 的 DPR 已是 2.0，再放大反而过大）。

```bash
WORKSPACE_SCALE=1.15 .venv/bin/python main.py   # 觉得 1.25 偏大
WORKSPACE_SCALE=1.0  .venv/bin/python main.py   # 还原
```

必须在 `QGuiApplication` 构造前设置，运行中改无效；启动会打印一行 `[workspace] 界面缩放 1.25`。
副作用是渲染像素量按平方增长（1.25² ≈ 1.56 倍），软渲染下若滚动掉帧就退到 1.15。

若在新机器：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

## 数据与目录

- 数据库：`~/.config/workspace/workspace.db`（阶段 1 schema：log_types / log_entries / todos / templates）
- 脚本目录：`~/.config/workspace/scripts/`（已有示例 hello.py，扔 .py/.sh 进去就出现在面板）
- 文件管理状态：`~/.config/workspace/filemanager_state.json`（cwd / 排序 / 面板比例 / 收藏夹 / 预览窗几何 / **全局设置**：`showHidden`、`workSchedule`、`previewPrefs`，不动 workspace.db）。`showHidden` 是**全局唯一一份**（老版本按面板各存一份，读到没有顶层字段时按「任一为开」迁移），`workSchedule` 存 `{startMin,endMin}`（当天第几分钟，比 "18:00" 字符串好清洗）。`leftWidth` 是 966 基准的归一化像素（比例 = leftWidth / 966），不是屏幕像素；`previewWindow` 的 `x/y` 允许负数（副屏在主屏左侧），`ver` 是预览窗几何版本（1→2 时旧的 760×580 记录自动作废、回到半屏默认）
- PDF 页图**不落盘**：由 `image://pdfpage/<token>/<页>/<宽>` 在工作线程里现渲、现交给 QML，内存里只留最近 1~3 页（LRU 96MB，按清晰度档位不同）。旧版本渲成 PNG 写 `~/.cache/workspace/pdf/`，已废弃（PNG 编码比渲染慢 4~14 倍，就是「打开慢」的根因）
- 设置面板：**全应用只有一处入口** —— 主窗口左下角侧栏底部的「设置」。文件浏览的显示隐藏文件、
  顶栏倒计时的上下班时间、预览的图片/PDF 清晰度都在这里改，读写的都是 `files` 上的同一份状态。
  预览窗里不再有第二个齿轮（曾经有，两处面板容易各存一份，已合并）
- 清晰度设置：面板里**图片与 PDF 各一组三档**，写进 state.json 的
  `previewPrefs.{imageQuality,pdfQuality}`（1.0 / 1.5 / 2.0）。PDF 默认 1.5（超采样，边缘更实），
  图片默认 1.0（解码尺寸已等于显示物理像素，再高只烧内存）。改完即时生效，重启保留
- 图片解码宽 = 视口宽 × dpr × 档位倍率，量化到 256，且**封顶到原图尺寸**：超过原图的解码只会让
  Qt 先解原图再放大，白烧内存；长条图按总像素预算（12M）等比回收宽高，避免高得离谱
- 加密 PDF：密码**只在内存里记本次会话**（vpath → 密码，最多 16 份，退出即失），不落盘；同一份文件再打开不再问密码
- 测试隔离跑：`WORKSPACE_HOME=./testdata .venv/bin/python main.py`

## 模块现状

| 模块 | 状态 | 说明 |
|------|------|------|
| 壳（侧边栏四模块） | ✅ | QML StackLayout，主题 token 在 qml/Theme.qml |
| 工作日志 | ✅ 骨架 | 月历选日期 + 当天条目 + 编辑器（增删改），条目类型可配 |
| 待办 | ✅ | 添加 / 完成 / 删除 / 到期日；`来自日志`徽标 = source_entry_id |
| 日志→待办 | ✅ | 日志编辑器里「转为待办」按钮，content 取主题或正文首行 |
| 脚本面板 | ✅ | 扫描 scripts 目录、QProcess 运行、输出回显、双击即跑 |
| 模板回复 | 未做 | 表已建（templates），UI 阶段 1 后期加，可开关 |
| 文件管理 | ✅ M1a + M1b | 常驻收藏栏（常用位置 + 可增删收藏夹）+ 等比双面板（窗口任何宽度左右等宽）+ 响应式列宽；导航 / 四列排序 / 过滤 / 隐藏文件 / 多选 / 快捷键 / 状态持久化。M1b 预览是**独立无边框窗口**：默认占屏幕右半边（1920×1080 → 960×972），可拖四边四角缩放、可拖出主窗口、始终置顶（能浮在 Excel 等其他应用上）、拖动标题栏移动、双击复位、几何记进 state.json；空格打开、Esc/空格关闭、↑↓ 切换相邻项并同步主窗口选中项；图片按宽度铺满（竖图/长页可上下滚动）、文本按宽度折行、**PDF 按页竖排滚动**（加密 PDF 弹密码框，Enter 解锁、错密码留在框里重试，同一文件本次会话只问一次）；五种预览器（图片/PDF/文本/文件夹摘要/兜底信息卡）；**设置统一到主窗口左下角**（全应用唯一入口，分「文件浏览 / 工作时段 / 预览清晰度」三组）：显示隐藏文件（全局一份，两栏同步）、顶栏上下班时间（原来硬编码 12:00/18:00）、图片与 PDF 清晰度（标准 / 清晰 / 极高），改完即时生效并记住；文件列表的类型点按类型着色（目录蓝、图片紫、PDF 珊瑚红、其余浅灰），一眼看出哪些能预览。**写操作 M1c**：跟系统剪贴板双向互通（同时写 `text/uri-list` 和 `x-special/gnome-copied-files`，能和文件管理器互拷）、Ctrl+C/X/V、粘贴同名自动加序号（`报告.pdf` → `报告 (1).pdf`，从不覆盖）、F2 / Ctrl+Shift+N 行内改名（预选主干不选扩展名，Enter 提交、Esc 取消、点到别处也算提交）、Delete 移回收站（freedesktop 规范，跨分区落 `.Trash-<uid>`）、Shift+Delete 永久删除（必过确认框）、**自绘右键菜单**（样式走 Theme，不进系统）；后台分块复制、带进度、可取消。压缩包(M2)、插件(M3) 未做，接口已留 |

## 旧数据迁移（重装麒麟后）

`tools/import_worklogs.py`（待写）：从旧 `work_logs.db` 只读导入
logs/log_entries/log_types/templates，幂等可重跑，迁移后对账。旧 todos 未完成项
转新结构并标注来源。**旧库只读、先冷备份，这是硬约束。**

## 结构

```
workspace/
  main.py          入口（注册 backend / scripts / files 三个上下文属性）
  backend.py       WorkspaceDB（SQLite）+ ScriptRunner（QProcess）
  filecore/        文件管理纯逻辑层（零 Qt 依赖）
    vpath.py / entries.py / listing.py / cancel.py / state.py
    locations.py                    常用位置（XDG user-dirs 感知，零 Qt）
    sources/{__init__,local}.py     FileSource 抽象与本地实现
    ops/                            写操作（零 Qt）：transfer 复制移动 / trash 回收站
                                    / naming 唯一命名 / clipboard dual-MIME
    preview/                        两阶段预览器协议 + 五个内置预览器（零 Qt）
      base.py                       prepare(<10ms 出窗口) / view(后台加载) + 注册表
      textread.py / probe.py        安全文本读取与编码探测 / 纯 Python 图片尺寸探测
      image.py / pdf.py / text.py / directory.py / fallback.py
                                     PDF 只校验文件头，栅格化在 pdf_pages.py
    selfcheck.py                   python -m filecore.selfcheck
  pdf_pages.py     PDF 页栅格化（PyMuPDF 优先、QtPdf 兜底；只出 QImage，不写盘）
  pdf_image_provider.py  QML 的 image://pdfpage —— 工作线程渲染 + 内存 LRU
  file_model.py    FileListModel（QAbstractListModel，10 个 role）
  task_runner.py   后台任务四池（scan / preview / op，op 单线程避免写盘打架）
  preview_provider.py / file_bridge.py   预览门面 + 双面板后端
  qml/Main.qml     壳：侧边栏 + StackLayout
  qml/Theme.qml    设计 token（颜色/圆角/间距）——调风格只改这个
  qml/Card.qml     圆角卡片（双层矩形假阴影，不用 DropShadow）
  qml/LogPage.qml  日历 + 条目 + 编辑器
  qml/TodoPage.qml 待办
  qml/ScriptsPage.qml  脚本面板
  qml/FileManagerPage.qml / FmPanel.qml / FavoritesBar.qml
                                    等比双面板布局 / 单面板 / 常驻收藏栏
  qml/ContextMenu.qml              自绘右键菜单（普通 Item + 全屏遮罩，不用 Popup）
  qml/PreviewWindow.qml            独立无边框预览窗口（Qt.Window + Frameless + StaysOnTop，四边四角可缩放）
  qml/PlaceholderPage.qml  旧占位页（保留，不再被引用）
```

## 验证

```bash
.venv/bin/pyside6-qmllint qml/*.qml 2>&1 | grep '^Error'   # 期望 0 行
TMPDIR=$PWD/.tmp .venv/bin/python -m filecore.selfcheck     # 23 项全 OK（含等比布局、收藏状态、五种预览器、写操作）
FC_SELFCHECK_ITEMS=5000 TMPDIR=$PWD/.tmp .venv/bin/python -m filecore.selfcheck  # 重压测（可选）
TMPDIR=$PWD/.tmp WORKSPACE_HOME=$PWD/.tmp/home QT_QPA_PLATFORM=offscreen \
  WS_SMOKE=1 .venv/bin/python main.py                     # 日志里不能有 QML 报错
```

注意：`WS_SMOKE` 的退出码**不能单独判活**（QML 组件加载失败时也可能返回 0），
必须同时检查日志里没有 `failed to load component` / `Cannot assign` / `ReferenceError`。

## 预览窗口的六个坑（改之前先读）

1. **拖动必须用 `mapToGlobal` 换算的屏幕坐标**，不能用 `mouse.x` 局部增量。无边框窗口自己
   跟着鼠标移动时，同一个屏幕位置对应的局部坐标会跟着变，「按下时的局部差值」里已经扣掉了
   上一次自己造成的位移 —— 实测每两步只走一步（跟随率 50%，肉眼就是一顿一顿地抖）。
   正确写法：按下时记 `(光标屏幕坐标, 窗口 x)` 快照，移动时 `x = 快照x + (当前光标 - 按下光标)`。
   和四边四角缩放手柄是同一套算法。
2. **验证拖动不能用一次大跳的合成事件**：`QTest.mouseMove` 走的是「显式局部坐标 + 显式全局
   坐标」，而真实硬件事件是投递时才把全局坐标换算成局部坐标。一次跳 60px 会被算成 120px，
   松手位置也会多算一次。要按真实事件流验证：每步小位移，局部坐标 = `虚拟光标 - 当前窗口位置`。
3. **主窗口的 Esc/空格 Shortcut 要在预览窗激活时让路**：Qt 的 Shortcut 不是严格
   per-active-window —— 预览窗是活动窗口时，主窗口那句 `Shortcut { sequence: "Esc" }`
   照样会被激活。表现是「在预览窗里按 Esc，设置面板刚关掉、整个预览窗也跟着关了」。
   所以预览窗把 `onActiveChanged` 回写给 `files.previewActive`，主窗口的
   `previewKeysOk` 里带上 `!files.previewActive`。
   **这个标记必须在预览窗隐藏时归零**：窗口 `hide()` 不等于 `active` 变化，`onActiveChanged`
   不会补发，而某些平台在隐藏后还会把 `active` 再置回 true。所以回写要写成
   `setPreviewActive(active && visible)`，并额外在 `onVisibleChanged` 的不可见分支里复位 ——
   否则关掉预览后 `previewActive` 永久卡在 true，带 `!previewActive` 的 Shortcut 从此全失效
   （表现：设置面板开着按 Esc 没反应）。
4. **设置面板用普通 Rectangle，不用 Popup**：Popup 一开就建自己的 focusScope（父链在
   Overlay 层），Esc 被它按 closePolicy 吃掉之后，同一个事件还会继续冒泡到父链的处理链，
   那时面板已经关了、被判成「面板没开」→ 顺手把底下的窗口也关了。Keys / Shortcut /
   event.accepted 三种拦法都试过，都挡不住。`qml/SettingsPanel.qml` 用普通 Rectangle +
   全窗透明遮罩，按键完全由调用方掌控。**遮罩的 `enabled` 才能绑面板 visible，父项的
   `visible` 不能绑子项的**（互相依赖 → 面板打不开）。
5. **PDF 密码必须在 `load()` 之前给**（QtPdf 是 `setPassword`，PyMuPDF 是 `authenticate`）：
   加载失败后再设不重试，状态永远停在错误上。所以解锁 = 整份重开一次，不能“就地改密码接着用”。
   QtPdf 对「没给密码」和「密码错」报同一个 `IncorrectPassword`，想区分只能看自己有没有给过密码。
6. **item 隐藏不等于交出键盘焦点**：StackLayout 切页只改子项的 `visible`，Qt 不保证把
   `activeFocus` 从隐藏的列表上收回（离屏平台会收回，麒麟/X11 上会留在原地）。表现是
   「按空格关掉预览 → 点侧栏切到待办 → 再按空格，预览又弹出来」，只有点到某个输入框才恢复。
   修法：`FmPanel` 加一个 `keysEnabled`（= `pageActive && !settingsOpen`），`Keys.onPressed`
   第一行就挡，`keysEnabled` 变 false 时 `list.focus = false`。切回页面要重新聚焦，但**不能
   在 `onPageActiveChanged` 里直接 `forceActiveFocus()`** —— StackLayout 的 `visible` 是布局
   阶段才置位的，那会儿 item 还不可见，调用会被丢掉（实测切回来 `focus` 仍是 false，空格没反应）。
   要写在 `onVisibleChanged` 里并套一层 `Qt.callLater`。

## PDF 渲染（换渲染器之前先读）

- **默认渲染器是 PyMuPDF（`pymupdf`），QtPdf 只是兜底**。原因是 QtPdf 的注释渲染必须显式传
  `QPdfDocumentRenderOptions.RenderFlag.Annotations`，而且**对 `/Widget`（数字签名字段）一律不画**
  —— 电子合同/发票上的电子签章大量是这种形态，这就是「签章不显示」的根因。PyMuPDF 默认画注释，
  `/Stamp` 与已签 `/Widget`（含图片型印章外观流）都能出来；未签的 `/Widget` 它会画一个自己的
  「SIGN」占位，这是它的既定行为，不算 bug。
- 想退回 QtPdf：`WORKSPACE_PDF_ENGINE=qtpdf`。代价就是 `/Widget` 签章看不见。
- **不要在 Qt 之后再 `import pymupdf`**：实测 PySide6 6.12 已加载时这次 import 要 1170ms，
  反过来只要 120ms。`main.py` 特意把它放在 PySide6 模块之前，就是为这 1 秒。
- **清晰度靠超采样，不靠降精度换速度**：渲染宽度 = (视口宽 - 32) × dpr × 清晰度倍率，
  量化到 128 的倍数。默认 1.5 倍（渲 1.5 倍再缩回显示，字边更实，像素量 2.25 倍），
  三档在主窗口左下角的设置面板里选。上限 `RENDER_MAX=4096` 与 QML 的 `pdfDecodeW` 必须同步，
  否则「切到极高没反应」。单页像素上限 `MAX_PX=20M`（2.0 档 A4 约 18.2M，再压就会被
  `_clamp` 反向缩小，反而更糊）。
- **PNG 编码是这条链路上最贵的一步**：一页扫描件渲染 34ms / PNG 编码 265ms，一页矢量文字
  7ms / 120ms。所以现在的做法是直接交 `QImage`（`QQuickAsyncImageProvider`），不编码不落盘；
  页面按「滚到哪渲到哪」按需渲染，打开文件时不做任何栅格化。

## 写操作的坑（M1c，改之前先读）

1. **右键菜单不用 Popup**。理由同设置面板：Popup 的 focusScope 在 Overlay 层，Esc 被它吃掉
   还继续冒泡。`qml/ContextMenu.qml` = 普通 `Item` + 全屏透明遮罩 + 卡片。
2. **卡片自己要吃点击**。`Rectangle` 不吃鼠标事件，点在 6px 内边距（行间空白）上会穿透到
   遮罩，表现是「点菜单自己的空白处，菜单就关了」。所以在卡片里放一个吃点击的 MouseArea，
   且要放在 `Column` 之前，让行的 MouseArea 仍然在上面。
3. **先 `picked(action)` 再 `close()`**。`close()` 会清空 `items`，把当前这个 delegate 一起
   销毁，销毁后继续往下走会被打断 —— 表现是「点了没反应」。顺序反了很难查。
4. **菜单项的 `action` 是字符串 id，不是函数**。菜单数组要经过 QML 信号与 `var` 属性传递，
   函数会在 QVariant 转换里丢掉类型，点下去静默无反应。由面板的 `performMenuAction(id)` 分派。
5. **行内改名的回车要在 TextInput 的 `Keys` 里吃掉**。`TextInput.onAccepted` 不把事件标成
   accepted，回车会冒泡到列表的 `Keys.onPressed`，那边把当前项「打开」—— 表现为「改完名顺手
   弹出预览窗」。用 `Keys.onReturnPressed { event.accepted = true; commitRename() }`。
6. **失焦提交要判「拿到过焦点」**（`heldFocus`）。否则列表刷新重建委托导致的假失焦会被当成
   「点了别处」，直接提交掉。
7. **新建目录不要紧跟 `newFolder()` 就 `beginRename()`**。`newFolder()` 内部会触发一次重扫，
   ListView 复用/重建委托，编辑框刚拿到焦点就被顶掉 → 自动提交 → 人一个字都没输上就退出。
   改成在 `focusRequested`（重扫落地、行真的存在了）里再进改名，见 `pendingNewFolder`。
   同理 `onFocusRequested` 里正在改名时**不能** `list.forceActiveFocus()`。
8. **改名预填不能只靠模型**。刚新建的项还没进列表，`nameOf()` 会拿到空串，于是「预填为空 →
   直接放弃改名」。`nameOf` 现在兜底 `vpath.basename()`。
9. **剪贴板 dual-MIME**：同时写 `text/uri-list`（标准兜底）和 `x-special/gnome-copied-files`
   （第一行 `copy`/`cut`，Peony/Nautilus 认这个才能保留「剪切」语义）。自己写剪贴板会触发
   `clipboardChanged`，用 `_clip_ignore` 0.6s 窗口屏蔽，别把自己的写入当成外部来源。
10. **离屏探针必须 `QTest.qWait`**。菜单行由 `Column` 在 polish 阶段摆位，离屏下
    `processEvents()` 不产生帧，行还叠在 y=0，点击会穿透到遮罩（现象：菜单关了但没执行）。
    `QTest.qWait` 走定时器，能让渲染循环真的转起来。

## 麒麟部署

同一份源码 git 同步过去，麒麟上 `python3 -m venv .venv && pip install -r requirements.txt`
（含 PyMuPDF；aarch64 有官方 wheel。装不上也不影响启动，会自动退回 QtPdf，只是 `/Widget` 签章不显示）
即可跑（无构建链）。会话用 X11。界面缩放默认 1.25，按 `WORKSPACE_SCALE` 现场调档。
#111