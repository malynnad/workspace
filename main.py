"""Workspace 个人工作台入口。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _ensure_pyside6():
    """当前解释器没装 PySide6 时，自动换成项目自带的 .venv 重新启动。

    IDE 的「运行」按钮默认走系统的 python3，那里没有 PySide6；
    有了这段就不用每次手动敲 .venv/bin/python main.py。
    """
    try:
        import PySide6  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    venv_dir = ROOT / ".venv"
    if Path(sys.prefix).resolve() != venv_dir.resolve():
        for rel in ("bin/python", "Scripts/python.exe"):
            py = venv_dir / rel
            if py.exists():
                sys.stderr.write(f"[workspace] 用虚拟环境解释器启动：{py}\n")
                os.execv(str(py), [str(py), str(ROOT / "main.py"), *sys.argv[1:]])

    sys.exit(
        "缺少 PySide6。先装好依赖再跑：\n"
        f"  cd {ROOT}\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/pip install -r requirements.txt\n"
    )


_ensure_pyside6()

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

# 界面缩放：QT_SCALE_FACTOR 是 Qt 逻辑像素的全局乘数，设了它字号/控件高/内边距/图标
# 会一起等比放大，业务代码不用动。目标机 1920×1080 系统缩放 100%，11px 正文明细偏小，
# 所以 Linux 默认放大 25%；Mac 保持原生 Retina 行为（DPR 已是 2.0，再放大反而过大）。
# 必须在 QGuiApplication 构造之前设置，之后再改无效。
# 现场调档：WORKSPACE_SCALE=1.15 .venv/bin/python main.py
_UI_SCALE = os.environ.get("WORKSPACE_SCALE") or (
    "1.25" if sys.platform.startswith("linux") else "1.0")
os.environ.setdefault("QT_SCALE_FACTOR", _UI_SCALE)
# 允许 1.25 这类非整数倍；策略是 Round 时会被取整成 1 或 2（Qt6 默认 PassThrough，显式声明防回归）
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from backend import APP_HOME, ScriptRunner, WorkspaceDB
from file_bridge import FileManagerBackend

# QQmlEngine 不接管 contextProperty 对象的所有权，也不保证 Python wrapper 存活。
# 必须显式持有引用，否则 QML 里的 files.* 会在 GC 后变成 null（3.14 下尤其明显）。
_KEEP_ALIVE = []


def main():
    print(f"[workspace] 界面缩放 {os.environ.get('QT_SCALE_FACTOR', '1')}"
          f"（WORKSPACE_SCALE 可覆盖）", file=sys.stderr)
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()

    db = WorkspaceDB(APP_HOME / "workspace.db")
    runner = ScriptRunner(APP_HOME / "scripts")
    files = FileManagerBackend(APP_HOME)
    engine.rootContext().setContextProperty("backend", db)
    engine.rootContext().setContextProperty("scripts", runner)
    engine.rootContext().setContextProperty("files", files)
    _KEEP_ALIVE.extend([db, runner, files])
    app.aboutToQuit.connect(files.shutdown)
    app.aboutToQuit.connect(files.saveState)
    engine.addImportPath(str(ROOT / "qml"))
    engine.load(QUrl.fromLocalFile(str(ROOT / "qml" / "Main.qml")))

    if not engine.rootObjects:
        sys.exit(1)

    # WS_SMOKE=1 时 3 秒自动退出，用于无头冒烟测试
    if os.environ.get("WS_SMOKE"):
        QTimer.singleShot(3000, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
