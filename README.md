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

若在新机器：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

## 数据与目录

- 数据库：`~/.config/workspace/workspace.db`（阶段 1 schema：log_types / log_entries / todos / templates）
- 脚本目录：`~/.config/workspace/scripts/`（已有示例 hello.py，扔 .py/.sh 进去就出现在面板）
- 文件管理状态：`~/.config/workspace/filemanager_state.json`（cwd / 排序 / 隐藏开关 / 面板比例 / 收藏夹，不动 workspace.db）。`leftWidth` 是 966 基准的归一化像素（比例 = leftWidth / 966），不是屏幕像素
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
| 文件管理 | ✅ M1a + M1b | 常驻收藏栏（常用位置 + 可增删收藏夹）+ 等比双面板（窗口任何宽度左右等宽）+ 响应式列宽；导航 / 四列排序 / 过滤 / 隐藏文件 / 多选 / 快捷键 / 状态持久化。M1b 浮窗预览：空格打开、Esc/空格关闭、↑↓ 切换相邻项、**拖动标题栏移动 + 双击复位**；四种预览器（图片/文本/文件夹摘要/兜底信息卡）。写操作(M1c)、压缩包(M2)、插件(M3) 未做，接口已留 |

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
    preview/                        两阶段预览器协议 + 四个内置预览器（零 Qt）
      base.py                       prepare(<10ms 出窗口) / view(后台加载) + 注册表
      textread.py / probe.py        安全文本读取与编码探测 / 纯 Python 图片尺寸探测
      image.py / text.py / directory.py / fallback.py
    selfcheck.py                   python -m filecore.selfcheck
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
  qml/PreviewWindow.qml            QuickLook 式浮窗预览（挂在 Main.qml 窗口级，遮罩盖住侧栏）
  qml/PlaceholderPage.qml  旧占位页（保留，不再被引用）
```

## 验证

```bash
.venv/bin/pyside6-qmllint qml/*.qml 2>&1 | grep '^Error'   # 期望 0 行
TMPDIR=$PWD/.tmp .venv/bin/python -m filecore.selfcheck     # 18 项全 OK（含等比布局、收藏状态、四种预览器断言）
FC_SELFCHECK_ITEMS=5000 TMPDIR=$PWD/.tmp .venv/bin/python -m filecore.selfcheck  # 重压测（可选）
TMPDIR=$PWD/.tmp WORKSPACE_HOME=$PWD/.tmp/home QT_QPA_PLATFORM=offscreen \
  WS_SMOKE=1 .venv/bin/python main.py                     # 日志里不能有 QML 报错
```

注意：`WS_SMOKE` 的退出码**不能单独判活**（QML 组件加载失败时也可能返回 0），
必须同时检查日志里没有 `failed to load component` / `Cannot assign` / `ReferenceError`。

## 麒麟部署

同一份源码 git 同步过去，麒麟上 `python3 -m venv .venv && pip install PySide6`
即可跑（无构建链）。会话用 X11。
