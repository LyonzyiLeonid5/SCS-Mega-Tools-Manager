import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def apply_portable_update(arguments):
    """Replace a portable Windows executable after its old process exits."""
    if len(arguments) != 2 or sys.platform != "win32": return 2
    destination = Path(arguments[0]).resolve()
    if destination.name.casefold() != "scs_tools_manager.exe": return 2
    try: previous_pid = int(arguments[1])
    except ValueError: return 2
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, previous_pid)
    if handle:
        try:
            if ctypes.windll.kernel32.WaitForSingleObject(handle, 120000) == 0x00000102: return 3
        finally: ctypes.windll.kernel32.CloseHandle(handle)
    source = Path(sys.executable).resolve(); replacement = destination.with_name(destination.name + ".new")
    try:
        shutil.copy2(source, replacement)
        for attempt in range(40):
            try: os.replace(replacement, destination); break
            except PermissionError:
                if attempt == 39: raise
                time.sleep(.25)
        subprocess.Popen([str(destination)], cwd=str(destination.parent), close_fds=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        return 0
    except OSError as error:
        try: ctypes.windll.user32.MessageBoxW(None, f"Unable to install the portable update:\n{error}", "SCS Tools Manager", 0x10)
        except Exception: pass
        return 4
    finally:
        try: replacement.unlink(missing_ok=True)
        except OSError: pass


if __name__ == "__main__" and "--apply-portable-update" in sys.argv:
    index = sys.argv.index("--apply-portable-update")
    raise SystemExit(apply_portable_update(sys.argv[index + 1:index + 3]))

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
# A QWebEngineView embedded in a stacked Qt interface can incorrectly be
# treated by Chromium as an occluded/background window. Chromium then limits
# its compositor and requestAnimationFrame to a very low rate even though the
# tool is visibly active. Keep the active tool renderer at foreground pace.
if "--disable-backgrounding-occluded-windows" not in flags:
    extra_flags.append("--disable-backgrounding-occluded-windows")
if "--disable-renderer-backgrounding" not in flags:
    extra_flags.append("--disable-renderer-backgrounding")
if "--disable-background-timer-throttling" not in flags:
    extra_flags.append("--disable-background-timer-throttling")
# Avoid noisy glCopySubTexture failures from Chromium's GPU raster stage while
# retaining GPU compositing for a responsive embedded-tool interface.
if sys.platform.startswith("win") and "--disable-gpu-rasterization" not in flags:
    extra_flags.append("--disable-gpu-rasterization")
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
