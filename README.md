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
- 文件管理状态：`~/.config/workspace/filemanager_state.json`（cwd / 排序 / 隐藏开关 / 面板比例 / 收藏夹 / 预览窗几何，不动 workspace.db）。`leftWidth` 是 966 基准的归一化像素（比例 = leftWidth / 966），不是屏幕像素；`previewWindow` 的 `x/y` 允许负数（副屏在主屏左侧），`ver` 是预览窗几何版本（1→2 时旧的 760×580 记录自动作废、回到半屏默认）
- PDF 页图缓存：`~/.cache/workspace/pdf/<sha1>/pNNN.png`（key = 路径+大小+mtime+渲染宽度，**不含密码**）。按 mtime 保留 7 天 / 最多 120 个目录，启动后第一次渲染时清一次；`WORKSPACE_CACHE_HOME` 可整体改目录（探针用它做隔离）
- 加密 PDF：密码**只在内存里记本次会话**（vpath → 密码，最多 16 份，退出即失），不落盘；缓存已经在的文件不再问密码
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
| 文件管理 | ✅ M1a + M1b | 常驻收藏栏（常用位置 + 可增删收藏夹）+ 等比双面板（窗口任何宽度左右等宽）+ 响应式列宽；导航 / 四列排序 / 过滤 / 隐藏文件 / 多选 / 快捷键 / 状态持久化。M1b 预览是**独立无边框窗口**：默认占屏幕右半边（1920×1080 → 960×972），可拖四边四角缩放、可拖出主窗口、始终置顶（能浮在 Excel 等其他应用上）、拖动标题栏移动、双击复位、几何记进 state.json；空格打开、Esc/空格关闭、↑↓ 切换相邻项并同步主窗口选中项；图片按宽度铺满（竖图/长页可上下滚动）、文本按宽度折行、**PDF 按页竖排滚动**（加密 PDF 弹密码框，Enter 解锁、错密码留在框里重试，同一文件本次会话只问一次）；五种预览器（图片/PDF/文本/文件夹摘要/兜底信息卡）。写操作(M1c)、压缩包(M2)、插件(M3) 未做，接口已留 |

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
    preview/                        两阶段预览器协议 + 五个内置预览器（零 Qt）
      base.py                       prepare(<10ms 出窗口) / view(后台加载) + 注册表
      textread.py / probe.py        安全文本读取与编码探测 / 纯 Python 图片尺寸探测
      image.py / pdf.py / text.py / directory.py / fallback.py
                                     PDF 只校验文件头，栅格化在 Qt 层（见 pdf_render.py）
    selfcheck.py                   python -m filecore.selfcheck
  pdf_render.py    PDF 页栅格化（QtPdf → PNG 缓存 $XDG_CACHE_HOME/workspace/pdf）
  file_model.py    FileListModel（QAbstractListModel，10 个 role）
  task_runner.py   后台任务三池（M1a 只实例化 IO 池）
  preview_provider.py / file_bridge.py   预览门面 + 双面板后端
  qml/Main.qml     壳：侧边栏 + StackLayout
  qml/Theme.qml    设计 token（颜色/圆角/间距）——调风格只改这个
  qml/Card.qml     圆角卡片（双层矩形假阴影，不用 DropShadow）
  qml/LogPage.qml  日历 + 条目 + 编辑器
  qml/TodoPage.qml 待办
  qml/ScriptsPage.qml  脚本面板
  qml/FileManagerPage.qml / FmPanel.qml / FavoritesBar.qml
                                    等比双面板布局 / 单面板 / 常驻收藏栏
  qml/PreviewWindow.qml            独立无边框预览窗口（Qt.Window + Frameless + StaysOnTop，四边四角可缩放）
  qml/PlaceholderPage.qml  旧占位页（保留，不再被引用）
```

## 验证

```bash
.venv/bin/pyside6-qmllint qml/*.qml 2>&1 | grep '^Error'   # 期望 0 行
TMPDIR=$PWD/.tmp .venv/bin/python -m filecore.selfcheck     # 18 项全 OK（含等比布局、收藏状态、五种预览器断言）
FC_SELFCHECK_ITEMS=5000 TMPDIR=$PWD/.tmp .venv/bin/python -m filecore.selfcheck  # 重压测（可选）
TMPDIR=$PWD/.tmp WORKSPACE_HOME=$PWD/.tmp/home QT_QPA_PLATFORM=offscreen \
  WS_SMOKE=1 .venv/bin/python main.py                     # 日志里不能有 QML 报错
```

注意：`WS_SMOKE` 的退出码**不能单独判活**（QML 组件加载失败时也可能返回 0），
必须同时检查日志里没有 `failed to load component` / `Cannot assign` / `ReferenceError`。

## 预览窗口的三个坑（改之前先读）

1. **拖动必须用 `mapToGlobal` 换算的屏幕坐标**，不能用 `mouse.x` 局部增量。无边框窗口自己
   跟着鼠标移动时，同一个屏幕位置对应的局部坐标会跟着变，「按下时的局部差值」里已经扣掉了
   上一次自己造成的位移 —— 实测每两步只走一步（跟随率 50%，肉眼就是一顿一顿地抖）。
   正确写法：按下时记 `(光标屏幕坐标, 窗口 x)` 快照，移动时 `x = 快照x + (当前光标 - 按下光标)`。
   和四边四角缩放手柄是同一套算法。
2. **验证拖动不能用一次大跳的合成事件**：`QTest.mouseMove` 走的是「显式局部坐标 + 显式全局
   坐标」，而真实硬件事件是投递时才把全局坐标换算成局部坐标。一次跳 60px 会被算成 120px，
   松手位置也会多算一次。要按真实事件流验证：每步小位移，局部坐标 = `虚拟光标 - 当前窗口位置`。
3. **QPdfDocument 的密码只能在 `load()` 之前 `setPassword()`**：load 失败后再设不生效，
   状态永远停在 `Error/IncorrectPassword`（改对了也不重试）。所以解锁 = 整份重开一次，
   不能“就地改密码接着用”。另外「没给密码」和「密码错」报的是同一个 `IncorrectPassword`，
   想区分只能看自己有没有给过密码。

## 麒麟部署

同一份源码 git 同步过去，麒麟上 `python3 -m venv .venv && pip install PySide6`
即可跑（无构建链）。会话用 X11。界面缩放默认 1.25，按 `WORKSPACE_SCALE` 现场调档。
