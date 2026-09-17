"""Визуальное встраивание EXE как borderless overlay поверх Qt-контейнера.

Окно остаётся нативным top-level окном своего процесса, поэтому TextBox,
ComboBox, модальные диалоги и аппаратный рендеринг продолжают работать.
"""
import ctypes
import subprocess
from ctypes import wintypes
from pathlib import Path
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QFrame, QVBoxLayout, QLabel

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
GWL_STYLE, GWL_EXSTYLE, GWLP_HWNDPARENT = -16, -20, -8
GW_HWNDPREV = 3
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = 0x00040000, 0x00000080
SW_HIDE, SW_SHOW = 0, 5
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_FRAMECHANGED, SWP_SHOWWINDOW = 0x0010, 0x0020, 0x0040
HWND_TOP = 0

user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL


class ExeHost(QFrame):
    def __init__(self, exe_path: Path, lang, parent=None):
        super().__init__(parent); self.exe_path = exe_path; self.lang = lang; self.process = None; self.child_hwnd = None; self.attempts = 0; self.last_state = None; self.overlay_visible = False
        # Сам контейнер остаётся обычным Qt-виджетом. EXE показывается отдельным
        # overlay-окном, поэтому HWND контейнера здесь не нужен и только нарушал
        # clipping внутри QScrollArea.
        self.setObjectName("exeHost"); self.setStyleSheet("QFrame#exeHost { background:#101216; border:0; }")
        layout = QVBoxLayout(self); self.status_key = "exe_starting"; self.status = QLabel(self.lang[self.status_key]); self.status.setObjectName("emptyHint"); layout.addWidget(self.status)
        self.attach_timer = QTimer(self); self.attach_timer.timeout.connect(self.find_and_prepare)
        self.overlay_timer = QTimer(self); self.overlay_timer.timeout.connect(self.sync_overlay)
        self.start()

    def start(self):
        try:
            startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startup.wShowWindow = SW_HIDE
            self.process = subprocess.Popen([str(self.exe_path)], cwd=str(self.exe_path.parent), startupinfo=startup); self.attach_timer.start(100)
        except OSError as error:
            self.status_key = None; self.status.setText(f"{self.lang['exe_failed']}: {error}")

    def find_and_prepare(self):
        self.attempts += 1
        if not self.process or self.process.poll() is not None:
            self.attach_timer.stop(); self.status_key = "exe_finished"; self.status.setText(self.lang[self.status_key]); return
        hwnd, width, height = self.find_main_window(self.process.pid)
        if hwnd:
            self.child_hwnd = hwnd; self.attach_timer.stop(); self.setMinimumSize(width, height); self.prepare_overlay(); self.status.hide(); self.status_key = None; self.sync_overlay(force=True); self.overlay_timer.start(30); return
        if self.attempts >= 150:
            self.attach_timer.stop(); self.status_key = "exe_window_not_found"; self.status.setText(self.lang[self.status_key])

    @staticmethod
    def find_main_window(pid):
        candidates = []; callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _):
            window_pid = wintypes.DWORD(); user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                rect = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect)); width, height = rect.right - rect.left, rect.bottom - rect.top
                if width >= 200 and height >= 120: candidates.append((width * height, hwnd, width, height))
            return True
        user32.EnumWindows(callback_type(callback), 0)
        best = max(candidates, default=(0, None, 0, 0)); return best[1], best[2], best[3]

    def prepare_overlay(self):
        hwnd = wintypes.HWND(self.child_hwnd)
        # Окно могло успеть зарегистрироваться в панели задач до обнаружения.
        # Hide + FRAMECHANGED гарантированно применяют TOOLWINDOW до показа.
        user32.ShowWindow(hwnd, SW_HIDE)
        style = user32.GetWindowLongW(hwnd, GWL_STYLE) & ~(WS_CAPTION | WS_THICKFRAME)
        exstyle = (user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(ctypes.c_int32(style).value))
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(ctypes.c_int32(exstyle).value))
        # Owner связывает минимизацию и Z-order с главным окном, не превращая EXE в child window.
        user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, ctypes.c_void_p(int(self.window().winId())))
        user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)

    def sync_overlay(self, force=False):
        self.sync_pending = False
        if not self.child_hwnd: return
        top = self.window(); should_show = self.isVisibleTo(top) and top.isVisible() and not bool(top.windowState() & Qt.WindowMinimized)
        if not should_show:
            if self.overlay_visible: user32.ShowWindow(wintypes.HWND(self.child_hwnd), SW_HIDE); self.overlay_visible = False
            return
        host_position = self.mapToGlobal(self.rect().topLeft())
        visible = self.visibleRegion().boundingRect()
        if visible.isEmpty():
            if self.overlay_visible: user32.ShowWindow(wintypes.HWND(self.child_hwnd), SW_HIDE); self.overlay_visible = False
            return
        state = (host_position.x(), host_position.y(), self.width(), self.height(), visible.x(), visible.y(), visible.width(), visible.height())
        if state == self.last_state and self.overlay_visible and not force:
            self.lower_above_manager(); return
        self.last_state = state; hwnd = wintypes.HWND(self.child_hwnd)
        # Меняем только геометрию и видимость. SWP_NOZORDER не позволяет EXE
        # повторно подниматься над диалогами и overlay-элементами при прокрутке.
        user32.SetWindowPos(hwnd, HWND_TOP, host_position.x(), host_position.y(), self.width(), self.height(), SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        region = gdi32.CreateRectRgn(visible.x(), visible.y(), visible.x() + visible.width(), visible.y() + visible.height())
        if region and not user32.SetWindowRgn(hwnd, region, True): gdi32.DeleteObject(region)
        self.overlay_visible = True; self.lower_above_manager()

    def lower_above_manager(self):
        """Держит EXE непосредственно над менеджером, но ниже остальных окон."""
        if not self.child_hwnd: return
        manager = wintypes.HWND(int(self.window().winId())); hwnd = wintypes.HWND(self.child_hwnd)
        window_above_manager = user32.GetWindow(manager, GW_HWNDPREV)
        if window_above_manager and int(window_above_manager) != int(self.child_hwnd):
            user32.SetWindowPos(hwnd, window_above_manager, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def moveEvent(self, event):
        super().moveEvent(event); self.sync_overlay(force=True)

    def resizeEvent(self, event):
        super().resizeEvent(event); self.sync_overlay(force=True)

    def showEvent(self, event):
        super().showEvent(event); self.sync_overlay(force=True)

    def hideEvent(self, event):
        self.hide_overlay(); super().hideEvent(event)

    def hide_overlay(self):
        if self.child_hwnd: user32.ShowWindow(wintypes.HWND(self.child_hwnd), SW_HIDE)
        self.overlay_visible = False

    def stop(self):
        if getattr(self, "stopped", False): return
        self.stopped = True
        self.attach_timer.stop(); self.overlay_timer.stop(); self.hide_overlay()
        if self.child_hwnd: user32.SetWindowLongPtrW(wintypes.HWND(self.child_hwnd), GWLP_HWNDPARENT, None)
        process = self.process; self.process = None
        if process:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try: process.wait(timeout=2)
                except subprocess.TimeoutExpired: pass
        self.child_hwnd = None; self.last_state = None; self.overlay_visible = False

    def closeEvent(self, event): self.stop(); super().closeEvent(event)
    def update_language(self, lang):
        self.lang = lang
        if self.status_key: self.status.setText(self.lang[self.status_key])
