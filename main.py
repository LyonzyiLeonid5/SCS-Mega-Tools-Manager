import ctypes
import os
import sys
import tempfile
from pathlib import Path

# QtWebEngine's default GPU cache can remain locked after a previous process.
# Keep each manager process in its own writable temporary cache directory.
cache_dir = Path(tempfile.gettempdir()) / "SCS Tools Manager" / "QtWebEngine" / str(os.getpid())
cache_dir.mkdir(parents=True, exist_ok=True)
flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
extra_flags = []
if "--disk-cache-dir=" not in flags:
    extra_flags.append(f'--disk-cache-dir="{cache_dir}"')
if "--disable-gpu-shader-disk-cache" not in flags:
    extra_flags.append("--disable-gpu-shader-disk-cache")
if sys.platform == "darwin":
    if "--disable-gpu" not in flags:
        extra_flags.append("--disable-gpu")
    if "--disable-gpu-compositing" not in flags:
        extra_flags.append("--disable-gpu-compositing")
if extra_flags:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " " + " ".join(extra_flags)).strip()

from PyQt5.QtCore import Qt, QCoreApplication
from PyQt5.QtGui import QSurfaceFormat
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
if sys.platform == "darwin":
    surface = QSurfaceFormat(); surface.setRenderableType(QSurfaceFormat.OpenGL)
    surface.setVersion(2, 1); surface.setProfile(QSurfaceFormat.NoProfile)
    QSurfaceFormat.setDefaultFormat(surface)

from qt_app import run


if __name__ == "__main__":
    # Qt widget repaints inherit the Windows timer resolution. Request 1 ms so
    # the animated background stays smooth when the app is launched from CMD.
    try: ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception: pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SCS.Tools.Manager.2")
    except Exception:
        pass
    launcher = next((Path(arg) for arg in sys.argv[1:] if arg.lower().endswith('.scstool')), None)
    if launcher:
        try: ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception: pass
    try: raise SystemExit(run(launcher))
    finally:
        try: ctypes.windll.winmm.timeEndPeriod(1)
        except Exception: pass
