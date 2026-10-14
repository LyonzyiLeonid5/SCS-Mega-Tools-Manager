import gc
import ctypes
import html
import hashlib
import math
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from collections import OrderedDict
import json
import logging
import os
import platform
import re
import sys
import time
import threading
import uuid
import zipfile
import shutil
import ssl
import subprocess
import tempfile
from urllib.error import URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, urlopen
from pathlib import Path
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QObject, pyqtSlot, pyqtSignal, pyqtProperty, QTimer, QUrl, QEvent, QRectF, QRect, QElapsedTimer
from PyQt5.QtGui import QIcon, QPixmap, QCursor, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QColor, QFont, QKeySequence, QPainter, QPainterPath, QLinearGradient, QRadialGradient, QPen, QBrush, QDesktopServices
from PyQt5.QtWidgets import (QApplication, QComboBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel as QtLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QLayout,
    QStackedWidget, QVBoxLayout, QWidget, QSizePolicy, QTabWidget, QTabBar, QDialog, QToolButton,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QListWidget, QListWidgetItem, QAbstractItemView, QAbstractScrollArea, QScrollArea, QFileDialog, QPlainTextEdit, QSplitter, QInputDialog, QMenu, QShortcut, QTextBrowser, QCheckBox, QProgressBar)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
import languages
from app_logging import ActionLogger, configure_logging, configure_detailed_tracing, set_log_language, logger as application_logger
from settings_store import DEFAULT_SETTINGS, backup_text_resources, load_settings, save_settings, settings_path
from tool_manager import ToolRegistry, Tool, app_data_path, flatten
from installation_manager import ensure_manager_shortcut, install_tool, installation_details, is_installed, launcher_path, launcher_uid, read_launcher, register_file_association, restore_embedded_archive, uninstall_tool, update_installed_tool
if sys.platform == "win32":
    from win_embed import ExeHost
else:
    ExeHost = None
from html_workspace import AuthoringWorkspace, basic_document, format_html

APP_VERSION = "1.1.0.1"
UPDATE_RELEASE_API = "https://api.github.com/repos/LyonzyiLeonid5/SCS-Mega-Tools-Manager/releases/latest"
UPDATE_RELEASES_URL = "https://github.com/LyonzyiLeonid5/SCS-Mega-Tools-Manager/releases"

try:
    import certifi
    REMOTE_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except (ImportError, OSError):
    # Development installations can use the operating system certificate store.
    REMOTE_SSL_CONTEXT = ssl.create_default_context()


def open_remote(request, timeout):
    """Open HTTPS resources with a bundled CA set on every supported OS."""
    return urlopen(request, timeout=timeout, context=REMOTE_SSL_CONTEXT)


def version_numbers(value):
    """Return a comparable numeric version tuple."""
    return tuple(int(item) for item in re.findall(r"\d+", str(value)))


def detect_update_channel(executable=None):
    """Identify which release artifact can update the running distribution."""
    if sys.platform == "darwin": return "pkg"
    if sys.platform.startswith("linux"):
        for command, channel in ((["dpkg-query", "-W", "scs-tools-manager"], "deb"), (["rpm", "-q", "scs-tools-manager"], "rpm")):
            if not shutil.which(command[0]): continue
            try:
                if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode == 0: return channel
            except (OSError, subprocess.SubprocessError): pass
        return "deb" if shutil.which("dpkg") else "rpm"
    if sys.platform != "win32": return ""
    running = Path(executable or sys.executable).resolve()
    if (running.parent / "unins000.exe").is_file(): return "setup"
    try:
        import winreg
        entries = []
        roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
        views = (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
        for root in roots:
            for view in views:
                try: uninstall = winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view)
                except OSError: continue
                with uninstall:
                    for index in range(winreg.QueryInfoKey(uninstall)[0]):
                        try:
                            with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as key:
                                name = str(winreg.QueryValueEx(key, "DisplayName")[0]).strip()
                                if name != "SCS Tools Manager": continue
                                values = {}
                                for field in ("InstallLocation", "UninstallString", "WindowsInstaller"):
                                    try: values[field] = winreg.QueryValueEx(key, field)[0]
                                    except OSError: pass
                                entries.append(values)
                        except OSError: continue
        matching = []
        for entry in entries:
            location = str(entry.get("InstallLocation", "")).strip().strip('"')
            try: same_location = bool(location) and Path(location).resolve() == running.parent
            except OSError: same_location = False
            if same_location: matching.append(entry)
        candidates = matching or (entries if running.parent.name.casefold() == "scs tools manager" and "program files" in str(running.parent).casefold() else [])
        for entry in candidates:
            uninstall = str(entry.get("UninstallString", "")).casefold()
            if int(entry.get("WindowsInstaller", 0) or 0) == 1 or "msiexec" in uninstall: return "msi"
        if candidates: return "setup"
    except (ImportError, OSError, ValueError): pass
    return "portable"


def select_update_asset(release, channel=None):
    """Select the native installer produced by build.py for this platform."""
    assets = [item for item in release.get("assets", []) if isinstance(item, dict)] if isinstance(release, dict) else []
    channel = channel or detect_update_channel()
    expected = {"portable": "scs_tools_manager.exe", "setup": "scs_tools_manager_setup.exe", "msi": "scs_tools_manager.msi"}
    for asset in assets:
        name = str(asset.get("name", "")).lower(); url = str(asset.get("browser_download_url", ""))
        matched = name == expected[channel] if channel in expected else name.endswith("." + channel) if channel else False
        if matched and url.startswith("https://"): return asset
    return None


def application_integrity_issues(registry):
    """Check source files in development and bundled resources in frozen builds."""
    issues = []
    if getattr(sys, "frozen", False):
        if not Path(sys.executable).is_file(): issues.append(str(sys.executable))
        for relative in (("assets", "icon.ico"), ("LICENSE",)):
            path = Path(resource_path(*relative))
            if not path.is_file(): issues.append(str(path))
    else:
        for name in ("main.py", "qt_app.py", "tool_manager.py", "settings_store.py", "installation_manager.py"):
            if not Path(__file__).with_name(name).is_file(): issues.append(name)
    for folder in (registry.tools_dir, registry.resources_dir, registry.cache_dir):
        try: folder.mkdir(parents=True, exist_ok=True)
        except OSError: issues.append(str(folder))
    return issues


class QLabel(QtLabel):
    """A label whose text can be selected and copied anywhere in the app."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.setTextInteractionFlags(Qt.TextSelectableByMouse)


class AnimatedBackground(QWidget):
    """An intentionally subtle moving glow behind the application content."""
    FALLING_LIGHTS = (
        (.04, .13, .045, .62, 5, QColor(255, 139, 84, 115)),
        (.10, .21, .030, .54, 3, QColor(255, 165, 85, 80)),
        (.16, .29, .035, .45, 4, QColor(255, 94, 112, 95)),
        (.24, .36, .025, .58, 3, QColor(142, 109, 255, 74)),
        (.31, .44, .055, .70, 4, QColor(103, 183, 255, 92)),
        (.39, .52, .030, .50, 3, QColor(99, 215, 255, 76)),
        (.48, .61, .040, .52, 6, QColor(220, 105, 255, 88)),
        (.55, .69, .030, .64, 3, QColor(255, 116, 94, 78)),
        (.63, .76, .050, .66, 4, QColor(255, 191, 91, 100)),
        (.71, .82, .028, .56, 3, QColor(185, 124, 255, 76)),
        (.79, .88, .030, .48, 5, QColor(101, 230, 205, 82)),
        (.88, .94, .024, .60, 3, QColor(255, 130, 102, 70)),
    )
    def __init__(self):
        super().__init__(); self.theme = "dark"; self.animated_effects_visible = True; self._phase = 0.0; self.clock = QElapsedTimer(); self.clock.start()
        self.last_glow_rect = QRect()
        self.frame_timer = QTimer(self); self.frame_timer.setTimerType(Qt.PreciseTimer); self.frame_timer.setInterval(16); self.frame_timer.timeout.connect(self.advance_frame); self.frame_timer.start()
    def set_animation_enabled(self, enabled):
        """Do not repaint beneath a visible native Chromium child window."""
        if self.animated_effects_visible != bool(enabled):
            self.animated_effects_visible = bool(enabled); self.update()
        if enabled:
            if not self.frame_timer.isActive():
                self.clock.restart(); self.frame_timer.start()
        elif self.frame_timer.isActive():
            self.frame_timer.stop()
    def set_theme(self, theme):
        self.theme = theme if theme in ("dark", "light", "ember", "ocean", "lavender", "fire", "spectrum_dark", "spectrum_light") else "dark"; self.update()
    def advance_frame(self):
        old_rects = self.moving_rects()
        self._phase = (self.clock.elapsed() % 24000) / 24000.0
        new_rects = self.moving_rects(); self.last_glow_rect = new_rects[0] if new_rects else QRect()
        # The particles repaint independently, so the animation stays inexpensive at 60 FPS.
        for old_rect, new_rect in zip(old_rects, new_rects): self.update(old_rect.united(new_rect).adjusted(-7, -14, 7, 7))
    def get_phase(self): return self._phase
    def set_phase(self, value): self._phase = float(value); self.update()
    phase = pyqtProperty(float, fget=get_phase, fset=set_phase)
    def glow_rect(self):
        radius = min(260, max(150, min(self.width(), self.height()) // 4))
        angle = self._phase * math.tau
        x = int(self.width() * (.48 + .28 * math.sin(angle)))
        y = int(self.height() * (.45 + .20 * math.cos(angle * .8)))
        return QRect(x - radius, y - radius, radius * 2, radius * 2)
    def ambient_rect(self, offset, scale, x_base, y_base, x_range, y_range):
        radius = max(100, int(min(self.width(), self.height()) * scale)); angle = (self._phase + offset) * math.tau
        x = int(self.width() * (x_base + x_range * math.sin(angle)))
        y = int(self.height() * (y_base + y_range * math.cos(angle * .73)))
        return QRect(x - radius, y - radius, radius * 2, radius * 2)
    def falling_rect(self, light):
        offset, x_base, sway, speed, radius, _color = light; fall = (self._phase * speed + offset) % 1.0
        x = int(self.width() * (x_base + sway * math.sin((self._phase + offset) * math.tau * 1.7)))
        y = int(-radius + fall * (self.height() + radius * 2))
        return QRect(x - radius, y - radius * 3, radius * 2, radius * 6)
    def moving_rects(self):
        return [self.glow_rect(), self.ambient_rect(.31, .15, .18, .72, .10, .08), self.ambient_rect(.67, .11, .82, .26, .07, .10)] + [self.falling_rect(light) for light in self.FALLING_LIGHTS]
    def effect_rect(self):
        rects = self.moving_rects(); result = rects[0] if rects else QRect()
        for rect in rects[1:]: result = result.united(rect)
        return result
    def resizeEvent(self, event):
        self.last_glow_rect = self.effect_rect(); self.update()
        super().resizeEvent(event)
    def paintEvent(self, event):
        base = "#edf1f5" if self.theme == "light" else "#17110f" if self.theme == "ember" else "#0c1724" if self.theme == "ocean" else "#f5f2fb" if self.theme == "lavender" else "#1b0f0d" if self.theme == "fire" else "#12101f" if self.theme == "spectrum_dark" else "#f7f5ff" if self.theme == "spectrum_light" else "#101216"
        painter = QPainter(self); dirty = event.rect(); painter.fillRect(dirty, QColor(base))
        painter.setClipRect(dirty)
        top_radius = max(260, min(self.width(), self.height()) // 3); bottom_radius = max(300, min(self.width(), self.height()) // 2)
        if self.theme == "ocean": palette = ((QColor(53,188,223,42), QColor(21,109,161,14)), (QColor(71,137,255,38), QColor(36,76,169,13)), (QColor(33,105,176,38), QColor(16,55,112,13)), (QColor(45,145,189,36), QColor(19,72,126,12)))
        elif self.theme == "lavender": palette = ((QColor(145,98,237,36), QColor(104,71,182,12)), (QColor(113,142,245,32), QColor(74,95,190,11)), (QColor(188,101,182,31), QColor(129,65,129,10)), (QColor(139,103,229,29), QColor(90,72,169,10)))
        elif self.theme == "fire": palette = ((QColor(255,87,42,52), QColor(190,43,24,17)), (QColor(255,161,46,43), QColor(194,84,18,14)), (QColor(221,36,48,45), QColor(128,17,27,15)), (QColor(242,81,28,40), QColor(150,34,18,13)))
        elif self.theme == "spectrum_dark": palette = ((QColor(96,109,255,38), QColor(52,43,158,13)), (QColor(229,72,198,37), QColor(142,35,128,12)), (QColor(39,207,193,34), QColor(15,105,105,11)), (QColor(248,177,65,34), QColor(154,88,21,11)))
        elif self.theme == "spectrum_light": palette = ((QColor(111,111,255,28), QColor(78,62,190,9)), (QColor(234,95,196,27), QColor(174,50,142,9)), (QColor(56,195,180,26), QColor(23,125,116,8)), (QColor(246,176,68,27), QColor(179,107,19,9)))
        else: palette = ((QColor(255,119,62,42), QColor(219,76,36,14)), (QColor(255,137,72,38), QColor(209,74,41,13)), (QColor(218,52,64,38), QColor(132,26,44,13)), (QColor(208,45,58,36), QColor(126,24,41,12)))
        static_glows = ((QRect(-top_radius, -top_radius, top_radius * 2, top_radius * 2), palette[0]), (QRect(self.width() - top_radius, -top_radius, top_radius * 2, top_radius * 2), palette[1]), (QRect(-bottom_radius, self.height() - bottom_radius, bottom_radius * 2, bottom_radius * 2), palette[2]), (QRect(self.width() - bottom_radius, self.height() - bottom_radius, bottom_radius * 2, bottom_radius * 2), palette[3]))
        moving_glows = ((self.glow_rect(), (QColor(238, 107, 47, 28), QColor(76, 122, 183, 13))), (self.ambient_rect(.31, .15, .18, .72, .10, .08), (QColor(65, 126, 195, 15), QColor(38, 80, 135, 7))), (self.ambient_rect(.67, .11, .82, .26, .07, .10), (QColor(238, 137, 77, 12), QColor(109, 69, 136, 6))))
        for rect, colors in static_glows + (moving_glows if self.animated_effects_visible else ()):
            gradient = QRadialGradient(rect.center(), rect.width() / 2); gradient.setColorAt(0, colors[0]); gradient.setColorAt(.54, colors[1]); gradient.setColorAt(1, QColor(16, 18, 22, 0)); painter.fillRect(rect, gradient)
        if self.animated_effects_visible:
            painter.setRenderHint(QPainter.Antialiasing)
            for light in self.FALLING_LIGHTS:
                rect = self.falling_rect(light); color = light[-1]; center_x = rect.center().x(); head_y = rect.center().y() + rect.height() // 3
                trail = QLinearGradient(center_x, head_y - rect.height() // 2, center_x, head_y + 5); trail.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 0)); trail.setColorAt(1, QColor(color.red(), color.green(), color.blue(), max(1, color.alpha() // 3))); painter.setPen(QPen(QBrush(trail), 1)); painter.drawLine(center_x, head_y - rect.height() // 2, center_x, head_y)
                painter.setBrush(color); painter.setPen(Qt.NoPen); painter.drawEllipse(QRect(center_x - 2, head_y - 2, 4, 4))
            glow = self.glow_rect(); painter.setPen(QPen(QColor(255, 141, 85, 32), 1)); painter.setBrush(Qt.NoBrush); painter.drawEllipse(glow.center(), 3, 3)


def resource_path(*parts):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), *parts)


# Fill in the URLs when the author's public pages are ready.
AUTHOR_SOCIAL_LINKS = (
    ("Discord", "https://discord.com/invite/trAftSH7gk", False),
    ("GitHub", "https://github.com/LyonzyiLeonid5/", False),
    ("Website", "https://lyonzyileonid5.website.yandexcloud.net/", False),
    ("Telegram", "https://t.me/Lyonzyi_Leonid5", True),
)


def release_unused_memory():
    """Освобождает циклические ссылки уже удалённых Qt/Python-объектов."""
    gc.collect()


class NoWheelComboBox(QComboBox):
    """Keeps a selection stable while the page itself is being scrolled."""
    def wheelEvent(self, event): event.ignore()


class CodeHighlighter(QSyntaxHighlighter):
    """Small dependency-free highlighter for the supported text tool types."""
    def __init__(self, document):
        super().__init__(document); self.mode = ""
        self.formats = {}
        for name, color, bold in (("keyword", "#ff7b72", True), ("string", "#a5d6ff", False), ("comment", "#8b949e", False), ("tag", "#79c0ff", True), ("attribute", "#d2a8ff", False), ("variable", "#7ee787", False), ("number", "#f2cc60", False)):
            fmt = QTextCharFormat(); fmt.setForeground(QColor(color)); fmt.setFontWeight(QFont.Bold if bold else QFont.Normal); self.formats[name] = fmt
    def set_mode(self, filename): self.mode = Path(filename).suffix.lower(); self.rehighlight()
    def apply(self, pattern, fmt, text):
        import re
        for match in re.finditer(pattern, text): self.setFormat(match.start(), match.end() - match.start(), self.formats[fmt])
    def highlightBlock(self, text):
        if self.mode in (".py",):
            self.apply(r"\b(and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|if|import|in|is|lambda|None|not|or|pass|raise|return|True|try|while|with|yield)\b", "keyword", text); self.apply(r"\b[A-Za-z_]\w*(?=\s*=)", "variable", text); self.apply(r"\b\d+(?:\.\d+)?\b", "number", text); self.apply(r"(?:'[^'\n]*'|\"[^\"\n]*\")", "string", text); self.apply(r"(?:^|\s)#.*$", "comment", text)
        elif self.mode in (".js", ".mjs", ".ts"):
            self.apply(r"\b(async|await|break|case|catch|class|const|continue|default|delete|else|export|false|finally|for|function|if|import|in|let|new|null|return|switch|this|throw|true|try|typeof|undefined|var|while)\b", "keyword", text); self.apply(r"\b[A-Za-z_$]\w*(?=\s*=)", "variable", text); self.apply(r"\b\d+(?:\.\d+)?\b", "number", text); self.apply(r"(?:'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`)", "string", text); self.apply(r"//.*$", "comment", text)
        elif self.mode in (".html", ".htm", ".xml"):
            self.apply(r"</?[A-Za-z][^>]*>", "tag", text); self.apply(r"\b[A-Za-z-]+(?=\=)", "attribute", text); self.apply(r"(?:'[^'\n]*'|\"[^\"\n]*\")", "string", text); self.apply(r"<!--.*?-->", "comment", text)
        elif self.mode == ".json":
            self.apply(r"\"(?:[^\"\\]|\\.)*\"", "string", text); self.apply(r"\"(?:[^\"\\]|\\.)*\"(?=\s*:)", "attribute", text); self.apply(r"\b(true|false|null)\b", "keyword", text); self.apply(r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b", "number", text)
        elif self.mode == '.css':
            self.apply(r'[^{}]+(?=\{)', 'tag', text)
            self.apply(r'[\w-]+(?=\s*:)', 'attribute', text)
            self.apply(r'#[0-9a-fA-F]{3,8}\b|\b\d+(?:\.\d+)?(?:px|em|rem|vh|vw|%)?', 'number', text)
            self.apply(r'/\*.*?\*/', 'comment', text)


ACTIONS_BRIDGE_MARKER = "<!-- ACTIONS_BRIDGE -->"
LANGUAGE_BRIDGE_MARKER = "<!-- LANGUAGE_BRIDGE -->"
THEME_BRIDGE_MARKER = "<!-- THEME_BRIDGE -->"
DENSITY_BRIDGE_MARKER = "<!-- DENSITY_BRIDGE -->"
LOGGING_BRIDGE_MARKER = "<!-- LOGGING_BRIDGE -->"
# Compatibility aliases for editor extensions that imported the old constants.
BRIDGE_MARKER = ACTIONS_BRIDGE_MARKER
LANG_BRIDGE_MARKER = LANGUAGE_BRIDGE_MARKER


def inject_body_bridge(html, marker, bridge):
    """Insert one generated bridge immediately before the closing document body."""
    if marker in html:
        return html
    closing = re.search(r'</body\s*>', html, re.I)
    if closing:
        return html[:closing.start()].rstrip() + '\n' + bridge + '\n' + html[closing.start():]
    html_close = re.search(r'</html\s*>', html, re.I)
    body = '\n<body>\n' + bridge + '\n</body>\n'
    return html[:html_close.start()] + body + html[html_close.start():] if html_close else html.rstrip() + body


def inject_language_bridge(html):
    """Add the optional declarative language bridge without changing authored code."""
    bridge = '''<!-- LANGUAGE_BRIDGE -->
<script name="LANGUAGE_BRIDGE">
(() => {
  // Add translations to window.scsTranslations, for example:
  // {ru: {title: 'Привет'}, en: {title: 'Hello'}}.
  // Mark text nodes with data-scs-i18n="title".
  const applyLanguage = language => {
    const code = language === 'ru' ? 'ru' : 'en';
    const baseLanguage = document.documentElement.dataset.scsBaseLanguage || document.documentElement.lang || 'en';
    document.documentElement.lang = code;
    const translations = window.scsTranslations || {};
    document.querySelectorAll('[data-scs-i18n]').forEach(node => {
      const source = node.dataset.scsI18nSource ?? node.textContent;
      node.dataset.scsI18nSource = source;
      const value = code === baseLanguage ? source : translations[code] && translations[code][node.dataset.scsI18n];
      node.textContent = value != null ? value : source;
    });
  };
  const previous = window.setLanguage;
  window.setLanguage = language => {
    if (typeof previous === 'function') previous(language);
    applyLanguage(language);
  };
  window.setLanguage(document.documentElement.lang || 'ru');
})();
</script>'''
    return inject_body_bridge(html, LANGUAGE_BRIDGE_MARKER, bridge)


def inject_theme_bridge(html):
    """Add a small default theme hook that a tool can extend in its own script."""
    bridge = '''<!-- THEME_BRIDGE -->
<script name="THEME_BRIDGE">
(() => {
  // The manager calls window.setManagerTheme(theme). Use
  // [data-scs-theme="dark"] (or another theme name) in your CSS.
  const applyTheme = theme => {
    const value = String(theme || 'dark');
    document.documentElement.dataset.scsTheme = value;
    document.documentElement.style.colorScheme =
      ['light', 'lavender', 'spectrum_light'].includes(value) ? 'light' : 'dark';
  };
  const previous = window.setManagerTheme;
  window.setManagerTheme = theme => {
    if (typeof previous === 'function') previous(theme);
    applyTheme(theme);
  };
  applyTheme(document.documentElement.dataset.scsTheme || 'dark');
})();
</script>'''
    return inject_body_bridge(html, THEME_BRIDGE_MARKER, bridge)


def inject_density_bridge(html, author_scale=100):
    """Add an interface-density hook with an optional author multiplier."""
    try: author_scale = max(25, min(200, int(author_scale)))
    except (TypeError, ValueError): author_scale = 100
    bridge = '''<!-- DENSITY_BRIDGE -->
<script name="DENSITY_BRIDGE">
(() => {
  const authorScale = %s / 100;
  const previous = window.setInterfaceDensity;
  window.setInterfaceDensity = percent => {
    if (typeof previous === 'function') previous(percent);
    const value = Math.max(25, Math.min(200, Number(percent) || 100)) * authorScale;
    document.documentElement.style.setProperty('--scs-interface-density', value / 100);
    document.documentElement.style.fontSize = value + '%%';
  };
  window.setInterfaceDensity(100);
})();
</script>''' % author_scale
    pattern = re.compile(r'<!--\s*DENSITY_BRIDGE\s*-->.*?</script\s*>', re.I | re.S)
    return pattern.sub(bridge, html, count=1) if pattern.search(html) else inject_body_bridge(html, DENSITY_BRIDGE_MARKER, bridge)


def inject_logging_bridge(html):
    """Capture tool-side console messages and uncaught JavaScript failures."""
    if LOGGING_BRIDGE_MARKER in html: return html
    bridge = '''<!-- LOGGING_BRIDGE -->
<script name="LOGGING_BRIDGE">
(() => {
  if (window.toolDebugLog) return;
  const stringify = value => {
    if (typeof value === 'string') return value;
    try { return JSON.stringify(value); } catch (_) { return String(value); }
  };
  const pending = [];
  const deliver = () => {
    if (!window.toolBridge || typeof window.toolBridge.log !== 'function') return;
    while (pending.length) window.toolBridge.log(pending.shift());
  };
  window.toolDebugLog = (level, ...details) => {
    pending.push(JSON.stringify({level, message: details.map(stringify).join(' ').slice(0, 8192), time: new Date().toISOString()}));
    if (pending.length > 100) pending.shift();
    deliver();
  };
  for (const level of ['debug', 'info', 'warn', 'error']) {
    const original = console[level].bind(console);
    console[level] = (...args) => { original(...args); window.toolDebugLog(level, ...args); };
  }
  window.addEventListener('error', event => window.toolDebugLog('error', event.message, event.filename + ':' + event.lineno));
  window.addEventListener('unhandledrejection', event => window.toolDebugLog('error', 'Unhandled promise:', event.reason));
  const timer = setInterval(() => { deliver(); if (!pending.length && window.toolBridge) clearInterval(timer); }, 250);
  setTimeout(() => clearInterval(timer), 30000);
})();
</script>'''
    return inject_body_bridge(html, LOGGING_BRIDGE_MARKER, bridge)


PYTHON_LOGGING_BRIDGE = '''"""Forward tool logs to the manager without changing the JSON action response."""
import json
import logging
import sys

class _ManagerLogHandler(logging.Handler):
    def emit(self, record):
        try:
            message = self.format(record)
            sys.stderr.write("__MANAGER_TOOL_LOG__:" + json.dumps({
                "level": record.levelname, "message": message[:8192]
            }, ensure_ascii=False) + "\\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)

logger = logging.getLogger("manager_tool")
logger.setLevel(logging.DEBUG)
logger.propagate = False
if not any(isinstance(item, _ManagerLogHandler) for item in logger.handlers):
    logger.addHandler(_ManagerLogHandler())
'''


def inject_python_logging_import(source):
    """Keep Python's module docstring and future imports in their legal positions."""
    if re.search(r'^\s*(?:from\s+tool_logging_bridge\s+import\s+logger|import\s+tool_logging_bridge)\b', source, re.M):
        return source
    import ast
    try:
        module = ast.parse(source)
        before = 0
        for node in module.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and before == 0:
                before = node.end_lineno
            elif isinstance(node, ast.ImportFrom) and node.module == '__future__':
                before = node.end_lineno
            else:
                break
        lines = source.splitlines(keepends=True)
        lines.insert(before, 'from tool_logging_bridge import logger\n')
        return ''.join(lines)
    except SyntaxError:
        return 'from tool_logging_bridge import logger\n' + source


def inject_tool_bridge(html):
    """Adds the editor bridge inside body, never as document-level markup."""
    bridge = '''<!-- ACTIONS_BRIDGE -->
<script name="QT_WEBCHANNEL" src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script name="ACTIONS_BRIDGE">
(() => { let scsBridge;
  // Available in both the editor preview and a launched tool.
  window.renderTool = window.renderTool || (result => {
    const changes = result && (result.dom || result.ui || result.updates || []);
    (Array.isArray(changes) ? changes : [changes]).forEach(change => {
      if (!change || typeof change !== 'object') return;
      document.querySelectorAll(change.selector || change.target || '').forEach(node => {
        if ('text' in change) node.textContent = String(change.text ?? '');
        if ('html' in change) node.innerHTML = String(change.html ?? '');
        if ('value' in change) node.value = change.value ?? '';
        if ('visible' in change) node.hidden = !change.visible;
        if (change.attributes && typeof change.attributes === 'object') Object.entries(change.attributes).forEach(([key, value]) => value == null ? node.removeAttribute(key) : node.setAttribute(key, String(value)));
      });
    });
  });
  if (typeof qt === 'undefined' || !qt.webChannelTransport) return;
  // A custom tool may already initialize QWebChannel.  Give it time to expose
  // its bridge before creating the generic fallback: two clients on the same
  // transport can corrupt the callback table.
  let bridgeAttempts=0;
  function connectBridge(){
    if(window.toolBridge){scsBridge=window.toolBridge;return;}
    if(bridgeAttempts++<20){setTimeout(connectBridge,25);return;}
    new QWebChannel(qt.webChannelTransport, channel => { scsBridge = channel.objects.toolBridge; window.toolBridge=scsBridge; });
  }
  connectBridge();
  document.addEventListener('click', event => {
    const control = event.target.closest('[data-scs-action]');
    if (!control || !scsBridge) return;
    const fields = {};
    document.querySelectorAll('input[name], select[name], textarea[name]').forEach(item => fields[item.name] = item.type === 'checkbox' ? item.checked : item.value);
    scsBridge.run(JSON.stringify({ action: control.dataset.scsAction, fields }));
  });
  // Standard result protocol for logic.py and external JavaScript logic.
  // A result may contain {"dom":[{"selector":"#id","text":"..."}]}.
  window.renderTool = window.renderTool || (result => {
    const changes = result && (result.dom || result.ui || result.updates || []);
    const items = Array.isArray(changes) ? changes : [changes];
    items.forEach(change => {
      if (!change || typeof change !== 'object') return;
      const nodes = document.querySelectorAll(change.selector || change.target || '');
      nodes.forEach(node => {
        if ('text' in change) node.textContent = String(change.text ?? '');
        if ('html' in change) node.innerHTML = String(change.html ?? '');
        if ('value' in change) node.value = change.value ?? '';
        if ('visible' in change) node.hidden = !change.visible;
        if (change.attributes && typeof change.attributes === 'object') Object.entries(change.attributes).forEach(([key, value]) => value == null ? node.removeAttribute(key) : node.setAttribute(key, String(value)));
        if (change.addClass) node.classList.add(...String(change.addClass).split(/\\s+/).filter(Boolean));
        if (change.removeClass) node.classList.remove(...String(change.removeClass).split(/\\s+/).filter(Boolean));
      });
    });
  });
})();
</script>'''
    # Older previews may have the bridge after </body>. Remove that generated copy
    # and insert one canonical copy in the document body.
    generated_bridge = re.compile(
        r'<!--\s*(?:SCS_TOOL_BRIDGE|ACTIONS_BRIDGE)\s*-->\s*'
        r'<script\b[^>]*qrc:///qtwebchannel/qwebchannel\.js[^>]*>\s*</script>\s*'
        r'<script\b[^>]*>.*?</script\s*>', re.I | re.S)
    html = generated_bridge.sub('', html)
    html = re.sub(r'<!--\s*(?:SCS_TOOL_BRIDGE|ACTIONS_BRIDGE)\s*-->\s*', '', html, flags=re.I)
    # A fragment or a malformed document still gets a valid body for bridge code.
    return inject_body_bridge(html, BRIDGE_MARKER, bridge)


class Page(QWidget):
    def __init__(self, app, key):
        super().__init__(); self.app, self.key = app, key
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0, 0, 0, 0); self.layout.setSpacing(10); self.layout.setAlignment(Qt.AlignTop)
    def heading(self, title, subtitle):
        title_label = QLabel(title); title_label.setObjectName("pageTitle"); title_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        subtitle_label = QLabel(subtitle); subtitle_label.setObjectName("pageSubtitle"); subtitle_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.layout.addWidget(title_label); self.layout.addWidget(subtitle_label)
        return title_label, subtitle_label
    def card(self):
        card = QFrame(); card.setObjectName("card"); return card
    def update_text(self, lang): pass


class ManagerUpdateDialog(QDialog):
    download_progress = pyqtSignal(int, int)
    download_finished = pyqtSignal(str, str)
    def __init__(self, app, release):
        super().__init__(app)
        self.app, self.release = app, release
        self.update_channel = detect_update_channel()
        self.asset = select_update_asset(release, self.update_channel)
        self.downloaded_path = None
        self.setModal(True); self.resize(600, 430)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 20); layout.setSpacing(12)
        self.title = QLabel(); self.title.setObjectName('pageTitle'); layout.addWidget(self.title)
        self.summary = QLabel(); self.summary.setObjectName('pageSubtitle'); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        self.notes = QTextBrowser(); self.notes.setObjectName('projectEditor'); self.notes.setOpenExternalLinks(True); layout.addWidget(self.notes, 1)
        self.status = QLabel(); self.status.setObjectName('pageSubtitle'); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.hide(); layout.addWidget(self.progress)
        buttons = QHBoxLayout(); self.release_button = QPushButton(); self.release_button.setObjectName('outlineButton'); self.release_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(str(release.get('html_url') or UPDATE_RELEASES_URL)))); self.cancel_button = QPushButton(); self.cancel_button.setObjectName('outlineButton'); self.cancel_button.clicked.connect(self.reject); self.install_button = QPushButton(); self.install_button.setObjectName('primaryButton'); self.install_button.clicked.connect(self.start_update); buttons.addWidget(self.release_button); buttons.addStretch(); buttons.addWidget(self.cancel_button); buttons.addWidget(self.install_button); layout.addLayout(buttons)
        self.download_progress.connect(self.show_progress); self.download_finished.connect(self.finish_download)
        self.update_text()
    def text(self, ru, en): return ru if self.app.lang['_lang_code'] == 'ru' else en
    def update_text(self):
        version = str(self.release.get('version', ''))
        self.setWindowTitle(self.text('Обновление SCS Tools Manager', 'SCS Tools Manager update'))
        self.title.setText(self.text('Доступно обновление', 'Update available'))
        self.summary.setText(self.text(f'Установлена версия {APP_VERSION}. Доступна версия {version}.', f'Version {APP_VERSION} is installed. Version {version} is available.'))
        notes = str(self.release.get('body') or self.text('Описание обновления не указано.', 'No release notes were provided.'))
        self.notes.setPlainText(notes)
        self.release_button.setText(self.text('Страница релиза', 'Release page')); self.cancel_button.setText(self.text('Позже', 'Later')); self.install_button.setText(self.text('Обновить', 'Update'))
        if not self.asset:
            self.status.setText(self.text('Для этой системы установщик в релизе не найден.', 'No installer for this system was found in the release.'))
            self.install_button.setEnabled(False)
        else:
            size = int(self.asset.get('size') or 0)
            self.status.setText(self.text(f'Тип установки: {self.update_channel}. Будет загружен {self.asset.get("name", "установщик")} ({human_size(size)}).', f'Installation type: {self.update_channel}. {self.asset.get("name", "Installer")} will be downloaded ({human_size(size)}).'))
    def start_update(self):
        if not self.asset or not str(self.asset.get('browser_download_url', '')).startswith('https://'): return
        self.install_button.setEnabled(False); self.cancel_button.setEnabled(False); self.progress.show(); self.progress.setRange(0, 0)
        self.status.setText(self.text('Загрузка обновления…', 'Downloading update…'))
        def worker():
            temporary = None
            try:
                folder = app_data_path() / 'updates'; folder.mkdir(parents=True, exist_ok=True)
                name = Path(str(self.asset.get('name') or 'SCS_Tools_Manager_Setup.exe')).name
                target = folder / name; temporary = target.with_suffix(target.suffix + '.part')
                request = Request(str(self.asset['browser_download_url']), headers={'User-Agent': f'SCS-Mega-Manager/{APP_VERSION}'})
                digest = hashlib.sha256(); received = 0
                with open_remote(request, timeout=30) as source, open(temporary, 'wb') as output:
                    total = int(source.headers.get('Content-Length') or self.asset.get('size') or 0)
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk: break
                        output.write(chunk); digest.update(chunk); received += len(chunk); self.download_progress.emit(received, total)
                expected_size = int(self.asset.get('size') or 0)
                if expected_size and received != expected_size: raise ValueError('Downloaded installer size does not match the GitHub release asset')
                expected_digest = str(self.asset.get('digest') or '')
                if expected_digest.startswith('sha256:') and digest.hexdigest().lower() != expected_digest.split(':', 1)[1].lower(): raise ValueError('Downloaded installer checksum does not match the GitHub release asset')
                os.replace(temporary, target); temporary = None; self.download_finished.emit(str(target), '')
            except (OSError, URLError, ValueError) as error:
                self.download_finished.emit('', str(error))
            finally:
                if temporary:
                    try: temporary.unlink(missing_ok=True)
                    except OSError: pass
        threading.Thread(target=worker, daemon=True).start()
    def show_progress(self, received, total):
        if total > 0: self.progress.setRange(0, total); self.progress.setValue(received)
        else: self.progress.setRange(0, 0)
        self.status.setText(self.text(f'Загружено {human_size(received)} из {human_size(total)}' if total else f'Загружено {human_size(received)}', f'Downloaded {human_size(received)} of {human_size(total)}' if total else f'Downloaded {human_size(received)}'))
    def finish_download(self, path, error):
        if error:
            self.progress.hide(); self.install_button.setEnabled(True); self.cancel_button.setEnabled(True)
            self.status.setText(self.text('Не удалось загрузить обновление: ', 'Unable to download the update: ') + error); return
        self.downloaded_path = Path(path); self.progress.setRange(0, 100); self.progress.setValue(100)
        self.status.setText(self.text('Обновление загружено. Запускаю установку…', 'The update is downloaded. Starting installation…'))
        QTimer.singleShot(250, self.launch_installer)
    def launch_installer(self):
        path = self.downloaded_path
        try:
            if not path or not path.is_file(): raise FileNotFoundError(str(path or ''))
            options = {'cwd': str(path.parent), 'close_fds': True}
            if sys.platform == 'win32': options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == 'win32' and self.update_channel == 'portable':
                subprocess.Popen([str(path), '--apply-portable-update', str(Path(sys.executable).resolve()), str(os.getpid())], **options)
            elif sys.platform == 'win32' and path.suffix.lower() == '.exe':
                subprocess.Popen([str(path), '/SILENT', '/SUPPRESSMSGBOXES', '/CLOSEAPPLICATIONS', '/RESTARTAPPLICATIONS'], **options)
            elif sys.platform == 'win32' and path.suffix.lower() == '.msi':
                subprocess.Popen(['msiexec.exe', '/i', str(path), '/passive', '/norestart'], **options)
            elif sys.platform == 'darwin' and self.update_channel == 'pkg':
                script = 'on run argv\n do shell script "/usr/sbin/installer -pkg " & quoted form of item 1 of argv & " -target /" with administrator privileges\nend run'
                subprocess.Popen(['osascript', '-e', script, str(path)], **options)
            elif sys.platform.startswith('linux') and self.update_channel in ('deb', 'rpm') and shutil.which('pkexec'):
                command = ['dpkg', '-i', str(path)] if self.update_channel == 'deb' else ['rpm', '-U', str(path)]
                subprocess.Popen(['pkexec', *command], **options)
            else:
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))): raise OSError('Unable to open the installer')
        except OSError as error:
            self.install_button.setEnabled(True); self.cancel_button.setEnabled(True); self.status.setText(self.text('Не удалось запустить установщик: ', 'Unable to start the installer: ') + str(error)); return
        application_logger().info('Manager update installer started: %s', path)
        self.accept(); QTimer.singleShot(100, self.app.close)


class HomePage(Page):
    connectivity_ready = pyqtSignal(bool)
    manager_update_ready = pyqtSignal(object)
    def __init__(self, app):
        super().__init__(app, "dashboard")
        self.latest_release = None; self.prompted_update_versions = set()
        self.title, self.subtitle = self.heading("", "")
        self.scroll = QScrollArea(); self.scroll.setObjectName('homeScroll'); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame); self.content = QWidget(); self.content.setObjectName('homeContent'); self.content_layout = QVBoxLayout(self.content); self.content_layout.setContentsMargins(0, 0, 0, 4); self.content_layout.setSpacing(10); self.scroll.setWidget(self.content); self.layout.addWidget(self.scroll, 1)
        hero = self.card(); hero.setObjectName("hero"); hero.setMaximumHeight(155)
        l = QVBoxLayout(hero); l.setContentsMargins(30, 25, 30, 25); l.setSpacing(2)
        self.hero_tag = QLabel("SCS / TOOLBOX"); self.hero_tag.setObjectName("eyebrow")
        self.hero_title = QLabel(); self.hero_title.setObjectName("heroTitle")
        self.hero_text = QLabel(); self.hero_text.setWordWrap(True); self.hero_text.setObjectName("heroText")
        l.addWidget(self.hero_tag); l.addWidget(self.hero_title); l.addWidget(self.hero_text); self.content_layout.addWidget(hero)
        stats = QHBoxLayout(); stats.setSpacing(10); self.stats = []
        for _ in range(4):
            box = self.card(); box.setObjectName("statCard"); box.setMaximumHeight(94); b = QVBoxLayout(box); b.setContentsMargins(20, 14, 20, 14)
            number = QLabel("—"); number.setObjectName("statValue"); label = QLabel(); label.setObjectName("statLabel")
            b.addWidget(number); b.addWidget(label); stats.addWidget(box); self.stats.append((number, label))
        self.content_layout.addLayout(stats)
        continue_card = self.card(); continue_layout = QHBoxLayout(continue_card); continue_layout.setContentsMargins(22, 14, 22, 14)
        continue_text = QVBoxLayout(); self.continue_title = QLabel(); self.continue_title.setObjectName('dashboardSectionTitle'); self.recent_tools = QLabel(); self.recent_tools.setObjectName('pageSubtitle'); self.recent_tools.setWordWrap(True); continue_text.addWidget(self.continue_title); continue_text.addWidget(self.recent_tools); continue_layout.addLayout(continue_text, 1)
        self.online_status = QLabel(); self.online_status.setObjectName('onlineStatus'); continue_layout.addWidget(self.online_status); self.continue_button = QPushButton(); self.continue_button.setObjectName('primaryButton'); self.continue_button.clicked.connect(app.continue_work); continue_layout.addWidget(self.continue_button); self.content_layout.addWidget(continue_card)
        update_card = self.card(); update_layout = QHBoxLayout(update_card); update_layout.setContentsMargins(22, 12, 22, 12); self.update_status = QLabel(); self.update_status.setObjectName('pageSubtitle'); self.update_status.setWordWrap(True); self.update_button = QPushButton(); self.update_button.setObjectName('outlineButton'); self.update_button.clicked.connect(self.update_action); update_layout.addWidget(self.update_status, 1); update_layout.addWidget(self.update_button); self.content_layout.addWidget(update_card)
        activity_row = QHBoxLayout(); activity_row.setSpacing(10)
        activity_card = self.card(); activity_layout = QVBoxLayout(activity_card); activity_layout.setContentsMargins(22, 14, 22, 14); activity_layout.setSpacing(4); self.activity_title = QLabel(); self.activity_title.setObjectName('dashboardSectionTitle'); self.activity_text = QLabel(); self.activity_text.setObjectName('pageSubtitle'); self.activity_text.setWordWrap(True); activity_layout.addWidget(self.activity_title); activity_layout.addWidget(self.activity_text); activity_row.addWidget(activity_card, 3)
        tool_updates_card = self.card(); tool_updates_layout = QVBoxLayout(tool_updates_card); tool_updates_layout.setContentsMargins(22, 14, 22, 14); tool_updates_layout.setSpacing(4); self.tool_updates_title = QLabel(); self.tool_updates_title.setObjectName('dashboardSectionTitle'); self.tool_updates_text = QLabel(); self.tool_updates_text.setObjectName('pageSubtitle'); self.tool_updates_text.setWordWrap(True); self.tool_updates_button = QPushButton(); self.tool_updates_button.setObjectName('outlineButton'); self.tool_updates_button.clicked.connect(self.open_tool_updates); tool_updates_layout.addWidget(self.tool_updates_title); tool_updates_layout.addWidget(self.tool_updates_text); tool_updates_layout.addWidget(self.tool_updates_button, alignment=Qt.AlignRight); activity_row.addWidget(tool_updates_card, 2); self.content_layout.addLayout(activity_row)
        lower = QHBoxLayout(); lower.setSpacing(10)
        overview = self.card(); overview_layout = QVBoxLayout(overview); overview_layout.setContentsMargins(22, 18, 22, 18); overview_layout.setSpacing(2)
        self.about_title = QLabel(); self.about_title.setObjectName("dashboardSectionTitle"); self.about_text = QLabel(); self.about_text.setObjectName("dashboardBody"); self.about_text.setWordWrap(True)
        overview_layout.addWidget(self.about_title); overview_layout.addWidget(self.about_text); lower.addWidget(overview, 3)
        socials = self.card(); socials_layout = QVBoxLayout(socials); socials_layout.setContentsMargins(22, 18, 22, 18); socials_layout.setSpacing(8)
        self.socials_title = QLabel(); self.socials_title.setObjectName("dashboardSectionTitle"); self.socials_text = QLabel(); self.socials_text.setObjectName("dashboardBody"); self.socials_text.setWordWrap(True); socials_layout.addWidget(self.socials_title); socials_layout.addWidget(self.socials_text)
        self.social_buttons = []
        for name, url, russian_only in AUTHOR_SOCIAL_LINKS:
            button = QPushButton(); button.setObjectName("outlineButton"); button.setEnabled(bool(url)); button.clicked.connect(lambda _, address=url: address and QDesktopServices.openUrl(QUrl(address))); socials_layout.addWidget(button); self.social_buttons.append((button, name, url, russian_only))
        socials_layout.addStretch(); lower.addWidget(socials, 2); self.content_layout.addLayout(lower)
        how_to = self.card(); how_to_layout = QVBoxLayout(how_to); how_to_layout.setContentsMargins(22, 18, 22, 22); how_to_layout.setSpacing(4)
        self.features_title = QLabel(); self.features_title.setObjectName("dashboardSectionTitle"); self.features_text = QLabel(); self.features_text.setObjectName("howToText"); self.features_text.setWordWrap(True); self.features_text.setTextFormat(Qt.RichText); how_to_layout.addWidget(self.features_title); how_to_layout.addWidget(self.features_text); self.content_layout.addWidget(how_to); self.content_layout.addStretch()
        self.connectivity_ready.connect(self.set_connectivity); self.manager_update_ready.connect(self.set_manager_update)
        self.update_text(app.lang); self.check_connectivity()
        if app.settings.get('auto_update_check', True): self.check_manager_update()
    def check_connectivity(self):
        def worker():
            online = False
            for _attempt in range(5):
                try:
                    with open_remote(Request(STORE_CATALOG_URL, headers={'User-Agent': 'SCS-Mega-Manager/1.0'}), timeout=4) as response:
                        response.read(1); online = True; break
                except (OSError, URLError): continue
            try: self.connectivity_ready.emit(online)
            except RuntimeError: pass
        threading.Thread(target=worker, daemon=True).start()
    def set_connectivity(self, online):
        self.online_status.setText('● Online' if online else '● Offline')
        self.online_status.setStyleSheet('color:#48c774;font-weight:700;' if online else 'color:#ff5b61;font-weight:700;')
    def open_tool_updates(self):
        self.app.show_page('store'); store = getattr(self.app, 'pages', {}).get('store')
        if store and hasattr(store, 'state_filter'):
            index = store.state_filter.findData('update')
            if index >= 0: store.state_filter.setCurrentIndex(index)
    def check_manager_update(self, force=False):
        self.update_status.setText('Проверка обновлений…' if self.app.lang['_lang_code'] == 'ru' else 'Checking for updates…')
        def worker():
            release = {'status': 'empty', 'force': force}
            try:
                request = Request(UPDATE_RELEASE_API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': f'SCS-Mega-Manager/{APP_VERSION}'})
                with open_remote(request, timeout=8) as response: data = json.loads(response.read().decode('utf-8'))
                release = {'status': 'ok', 'force': force, 'version': str(data.get('tag_name') or data.get('name') or '').lstrip('vV'), 'html_url': str(data.get('html_url') or UPDATE_RELEASES_URL), 'body': str(data.get('body') or ''), 'assets': data.get('assets') if isinstance(data.get('assets'), list) else []}
            except (OSError, URLError, ValueError, UnicodeError, json.JSONDecodeError): release = {'status': 'error' if force else 'empty', 'force': force}
            try: self.manager_update_ready.emit(release)
            except RuntimeError: pass
        threading.Thread(target=worker, daemon=True).start()
    def update_action(self):
        if self.latest_release: self.show_update_dialog()
        else: self.check_manager_update(force=True)
    def show_update_dialog(self):
        if not self.latest_release: return
        ManagerUpdateDialog(self.app, self.latest_release).exec_()
    def set_manager_update(self, release):
        ru = self.app.lang['_lang_code'] == 'ru'
        status = release.get('status') if isinstance(release, dict) else 'error'
        if status == 'error': self.latest_release = None; self.update_status.setText('Не удалось проверить обновления.' if ru else 'Unable to check for updates.'); self.update_button.setText('Проверить обновление' if ru else 'Check for updates'); return
        if status != 'ok': self.latest_release = None; self.update_status.setText('Автоматическая проверка обновлений включена.' if ru else 'Automatic update checks are enabled.'); return
        latest = str(release.get('version') or '')
        if version_numbers(latest) > version_numbers(APP_VERSION):
            self.latest_release = release
            self.update_status.setText((f'Доступна версия {latest}.' if ru else f'Version {latest} is available.'))
            self.update_button.setText('Обновить' if ru else 'Update')
            self.app.add_notification(self.update_status.text(), 'info', 'dashboard', key='manager-update-' + latest)
            if latest not in self.prompted_update_versions:
                self.prompted_update_versions.add(latest); QTimer.singleShot(0, self.show_update_dialog)
        else:
            self.latest_release = None; self.update_status.setText('Установлена последняя версия.' if ru else 'The latest version is installed.'); self.update_button.setText('Проверить обновление' if ru else 'Check for updates')
    def refresh_overview(self):
        store = getattr(self.app, "pages", {}).get("store")
        verified = len(store.tools) if getattr(store, "verified_loaded", False) else "—"
        downloaded = len(self.app.registry.scan())
        catalog_state = self.app.lang["dashboard_catalog_ready"] if getattr(store, "verified_loaded", False) else self.app.lang["dashboard_catalog_unavailable"]
        values = (str(verified), str(downloaded), "3", "v" + APP_VERSION)
        labels = (self.app.lang["dashboard_verified"], self.app.lang["dashboard_downloaded_count"], self.app.lang["dashboard_runtimes"], self.app.lang["dashboard_version"])
        for (number, label), value, text in zip(self.stats, values, labels): number.setText(value); label.setText(text)
        self.subtitle.setText(f"{self.app.lang['dashboard_subtitle']} · {catalog_state}")
        recent = self.app.settings.get('tool_last_opened', {})
        tools = {tool.uid: tool.name for tool in self.app.registry.scan()}
        if isinstance(recent, dict): names = [tools[uid] for uid, _ in sorted(recent.items(), key=lambda item: item[1] if isinstance(item[1], (int, float)) else 0, reverse=True) if uid in tools][:3]
        else: names = []
        self.recent_tools.setText(' · '.join(names) if names else ('Недавно открытых инструментов пока нет.' if self.app.lang['_lang_code'] == 'ru' else 'No recently opened tools yet.'))
        self.continue_button.setEnabled(bool(recent))
        notifications = [item for item in self.app.notification_items() if isinstance(item, dict)][-3:]
        self.activity_text.setText('\n'.join('• ' + str(item.get('message', '')) for item in reversed(notifications)) if notifications else ('Событий пока нет.' if self.app.lang['_lang_code'] == 'ru' else 'No activity yet.'))
        updates = [str(item.get('name', '')) for item in getattr(store, 'tools', []) if isinstance(item, dict) and item.get('_store_state') == 'update']
        self.tool_updates_text.setText(('Требуют обновления: ' if self.app.lang['_lang_code'] == 'ru' else 'Updates required: ') + ', '.join(updates[:4]) + (f' +{len(updates) - 4}' if len(updates) > 4 else '') if updates else ('Все инструменты актуальны.' if getattr(store, 'verified_loaded', False) and self.app.lang['_lang_code'] == 'ru' else 'All tools are up to date.' if getattr(store, 'verified_loaded', False) else ('Каталог ещё не загружен.' if self.app.lang['_lang_code'] == 'ru' else 'The catalog has not loaded yet.')))
        self.tool_updates_button.setEnabled(bool(updates)); self.tool_updates_button.setProperty('hasUpdates', bool(updates))
    def update_text(self, lang):
        self.title.setText(lang["tab_dashboard"]); self.hero_title.setText(lang["dashboard_title"]); self.hero_text.setText(lang["dashboard_info"])
        self.continue_title.setText('Продолжить работу' if lang['_lang_code'] == 'ru' else 'Continue working')
        self.continue_button.setText('Открыть все' if lang['_lang_code'] == 'ru' else 'Open all')
        self.update_button.setText(('Обновить' if lang['_lang_code'] == 'ru' else 'Update') if self.latest_release else ('Проверить обновление' if lang['_lang_code'] == 'ru' else 'Check for updates'))
        self.activity_title.setText('Недавняя активность' if lang['_lang_code'] == 'ru' else 'Recent activity'); self.tool_updates_title.setText('Обновления инструментов' if lang['_lang_code'] == 'ru' else 'Tool updates'); self.tool_updates_button.setText('Открыть магазин' if lang['_lang_code'] == 'ru' else 'Open Store')
        self.about_title.setText(lang["dashboard_about_title"]); self.about_text.setText(lang["dashboard_about_text"]); self.features_title.setText(lang["dashboard_features_title"]); self.features_text.setText(lang["dashboard_features_text"])
        self.socials_title.setText(lang["dashboard_socials_title"]); self.socials_text.setText(lang["dashboard_socials_text"])
        for button, name, url, russian_only in self.social_buttons:
            button.setVisible(not russian_only or lang['_lang_code'] == 'ru'); button.setText(name if url else f"{name} · {lang['dashboard_social_empty']}")
        self.refresh_overview()


class LegacyStorePage(Page):
    tools = [("SCS Extractor", "2.1.0", "4.2 MB"), ("Mod Studio", "1.8.3", "12.7 MB"), ("Map Editor", "3.0.1", "8.5 MB"), ("Sound Converter", "1.2.0", "3.1 MB")]
    def __init__(self, app):
        super().__init__(app, "store"); self.title, self.subtitle = self.heading("", "")
        head = QHBoxLayout(); head.addStretch(); self.refresh = QPushButton(); self.refresh.setObjectName("primaryButton"); self.refresh.clicked.connect(self.populate); head.addWidget(self.refresh); self.layout.addLayout(head)
        self.list = QVBoxLayout(); self.list.setSpacing(9); self.layout.addLayout(self.list); self.layout.addStretch(); self.populate(); self.update_text(app.lang)
    def populate(self):
        while self.list.count():
            item = self.list.takeAt(0); w = item.widget(); w and w.deleteLater()
        for name, ver, size in self.tools:
            row = self.card(); row.setObjectName("toolCard"); r = QHBoxLayout(row); r.setContentsMargins(20, 14, 20, 14)
            icon = QLabel("◆"); icon.setObjectName("toolIcon"); text = QVBoxLayout(); n = QLabel(name); n.setObjectName("toolName"); meta = QLabel(f"{self.app.lang['store_version']} {ver}  •  {size}"); meta.setObjectName("toolMeta"); text.addWidget(n); text.addWidget(meta)
            btn = QPushButton("↓  " + self.app.lang["store_download"]); btn.setObjectName("primaryButton"); btn.clicked.connect(lambda _, x=name: self.download(x))
            r.addWidget(icon); r.addLayout(text); r.addStretch(); r.addWidget(btn); self.list.addWidget(row)
    def download(self, name): QMessageBox.information(self, self.app.lang["download_ready"], f"{name} {self.app.lang['download_success']}")
    def update_text(self, lang): self.title.setText(lang["download_header"]); self.subtitle.setText(lang["dashboard_subtitle"]); self.refresh.setText("↻  " + lang["download_refresh_btn"]); self.populate()


STORE_CATALOG_URL = "https://lyonzyileonid5.website.yandexcloud.net/tools/verified_tools.json"


def human_size(value):
    try: value = int(value)
    except (TypeError, ValueError): return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB": return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024


def markdown_to_html(markdown):
    """Small safe Markdown renderer for published tool README files."""
    def inline(value):
        value = html.escape(value)
        value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
        value = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', value)
        value = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'<a href="\2">\1</a>', value)
        return value
    output, in_list = [], False
    for raw in str(markdown or '').splitlines():
        line = raw.strip()
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        bullet = re.match(r'^(?:[-*]|\d+\.)\s+(.+)$', line)
        if heading:
            if in_list: output.append('</ul>'); in_list = False
            output.append(f'<h{len(heading.group(1))}>{inline(heading.group(2))}</h{len(heading.group(1))}>')
        elif bullet:
            if not in_list: output.append('<ul>'); in_list = True
            output.append(f'<li>{inline(bullet.group(1))}</li>')
        elif line:
            if in_list: output.append('</ul>'); in_list = False
            output.append(f'<p>{inline(line)}</p>')
        elif in_list:
            output.append('</ul>'); in_list = False
    if in_list: output.append('</ul>')
    return ''.join(output)


class InstallationDialog(QDialog):
    def __init__(self, app, tool):
        super().__init__(app); self.app, self.tool = app, tool; self.setObjectName('installationDialog'); self.setMinimumWidth(510); self.setModal(True)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 22); layout.setSpacing(11)
        self.title = QLabel(); self.title.setObjectName('storeDetailTitle'); self.title.setWordWrap(True); layout.addWidget(self.title)
        self.description = QLabel(); self.description.setObjectName('pageSubtitle'); self.description.setWordWrap(True); layout.addWidget(self.description)
        mandatory = QFrame(); mandatory.setObjectName('card'); mandatory_layout = QVBoxLayout(mandatory); mandatory_layout.setContentsMargins(15, 12, 15, 12)
        self.launcher_label = QLabel(); self.launcher_label.setObjectName('dashboardSectionTitle'); self.launcher_path_label = QLabel(str(launcher_path(tool))); self.launcher_path_label.setObjectName('dashboardBody'); self.launcher_path_label.setWordWrap(True)
        self.launcher_browse = QPushButton(); self.launcher_browse.setObjectName('outlineButton'); self.launcher_browse.clicked.connect(self.choose_launcher_folder); launcher_row = QHBoxLayout(); launcher_row.addWidget(self.launcher_path_label, 1); launcher_row.addWidget(self.launcher_browse); mandatory_layout.addWidget(self.launcher_label); mandatory_layout.addLayout(launcher_row); layout.addWidget(mandatory)
        self.one_file = QCheckBox(); self.start_menu = QCheckBox(); self.desktop = QCheckBox(); layout.addWidget(self.one_file); layout.addWidget(self.start_menu); layout.addWidget(self.desktop)
        self.desktop_path = QLabel(str(Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop")); self.desktop_path.setObjectName('pageSubtitle'); self.desktop_browse = QPushButton(); self.desktop_browse.setObjectName('outlineButton'); self.desktop_browse.clicked.connect(self.choose_desktop_folder); desktop_row = QHBoxLayout(); desktop_row.addWidget(self.desktop_path, 1); desktop_row.addWidget(self.desktop_browse); layout.addLayout(desktop_row)
        buttons = QHBoxLayout(); buttons.addStretch(); self.cancel = QPushButton(); self.cancel.setObjectName('outlineButton'); self.cancel.clicked.connect(self.reject); self.install = QPushButton(); self.install.setObjectName('primaryButton'); self.install.clicked.connect(self.perform_install); buttons.addWidget(self.cancel); buttons.addWidget(self.install); layout.addLayout(buttons)
        self.update_text()
    def text(self, ru, en): return ru if self.app.lang['_lang_code'] == 'ru' else en
    def update_text(self):
        self.setWindowTitle(self.text('Установка инструмента', 'Install tool')); self.title.setText(self.text(f'Установить: {self.tool.name}', f'Install: {self.tool.name}'))
        self.description.setText(self.text('Файл запуска создаётся всегда. Его можно открывать двойным кликом: SCS Tools Manager найдёт инструмент по UID.', 'A launch file is always created. Double-clicking it opens SCS Tools Manager, which finds the tool by UID.'))
        self.launcher_label.setText(self.text('Обязательный файл запуска (.scstool)', 'Required launch file (.scstool)'))
        self.launcher_browse.setText(self.text('Папка…', 'Folder…')); self.desktop_browse.setText(self.text('Папка ярлыка…', 'Shortcut folder…'))
        self.one_file.setText(self.text('Сохранить инструмент внутри одного файла .scstool', 'Store the tool inside one .scstool file'))
        self.start_menu.setText(self.text('Создать ярлык в Пуск → SCS Tools', 'Create a shortcut in Start → SCS Tools'))
        self.desktop.setText(self.text('Создать ярлык на рабочем столе', 'Create a desktop shortcut'))
        self.cancel.setText(self.text('Отмена', 'Cancel')); self.install.setText(self.text('Установить', 'Install'))
    def choose_launcher_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.text('Папка файла .scstool', '.scstool file folder'), str(Path(self.launcher_path_label.text()).parent))
        if folder: self.launcher_path_label.setText(str(Path(folder) / launcher_path(self.tool).name))
    def choose_desktop_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.text('Папка ярлыка', 'Shortcut folder'), self.desktop_path.text())
        if folder: self.desktop_path.setText(folder); self.desktop.setChecked(True)
    def perform_install(self):
        try: result = install_tool(self.tool, one_file=self.one_file.isChecked(), start_menu=self.start_menu.isChecked(), desktop=self.desktop.isChecked(), launcher_directory=Path(self.launcher_path_label.text()).parent, desktop_directory=self.desktop_path.text())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, self.app.lang['error'], self.text('Не удалось установить инструмент.', 'Unable to install the tool.') + f'\n{error}'); return
        self.app.tool_installed(self.tool.uid); QMessageBox.information(self, self.text('Установка завершена', 'Installation complete'), self.text('Инструмент установлен. Файл запуска:', 'The tool is installed. Launch file:') + f'\n{result["launcher"]}'); self.accept()


def draw_cover_pixmap(painter, rect, pixmap):
    """Draw a cropped cover image without rebuilding a large scaled pixmap."""
    if pixmap.isNull() or rect.width() <= 0 or rect.height() <= 0: return False
    source = QRectF(pixmap.rect()); target_ratio = rect.width() / rect.height(); source_ratio = source.width() / source.height()
    if source_ratio > target_ratio:
        width = source.height() * target_ratio; source.setLeft((source.width() - width) / 2); source.setWidth(width)
    else:
        height = source.width() / target_ratio; source.setTop((source.height() - height) / 2); source.setHeight(height)
    painter.drawPixmap(QRectF(rect), pixmap, source)
    return True


class StoreBanner(QFrame):
    def __init__(self, height=360):
        super().__init__(); self.banner = QPixmap(); self.setObjectName('storeDetailBanner'); self.setFixedHeight(height)
    def set_banner(self, data):
        image = QPixmap()
        if image.loadFromData(data): self.banner = image; self.update()
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing); rect = self.rect(); radius = 18; path = QPainterPath(); path.moveTo(0, radius); path.quadTo(0, 0, radius, 0); path.lineTo(rect.width() - radius, 0); path.quadTo(rect.width(), 0, rect.width(), radius); path.lineTo(rect.width(), rect.height()); path.lineTo(0, rect.height()); path.closeSubpath(); painter.setClipPath(path)
        if not draw_cover_pixmap(painter, rect, self.banner): painter.fillRect(rect, QColor('#151b24'))
        transition = QLinearGradient(0, max(0, rect.height() - 86), 0, rect.height())
        transition.setColorAt(0, QColor(25, 29, 37, 0)); transition.setColorAt(1, QColor('#191d25'))
        painter.fillRect(rect, transition)


class StoreToolPagePanel(QWidget):
    text_ready = pyqtSignal(object, object)
    images_ready = pyqtSignal(object, object)
    def __init__(self, page, tool):
        super().__init__(page); self.page, self.tool = page, tool; self.manifest = {}; self.readme_sources = None; self.setMinimumSize(680, 520); self.text_ready.connect(self.apply_text_resources); self.images_ready.connect(self.apply_image_resources)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        card = QFrame(); card.setObjectName('storeDetailCard'); card_layout = QVBoxLayout(card); card_layout.setContentsMargins(0, 0, 0, 0); card_layout.setSpacing(0); outer.addWidget(card)
        scroll = QScrollArea(); scroll.setObjectName('storeDetailScroll'); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); card_layout.addWidget(scroll)
        content = QWidget(); content.setObjectName('storeDetailContent'); body = QVBoxLayout(content); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0); scroll.setWidget(content)
        self.banner = StoreBanner(); body.addWidget(self.banner)
        banner_layout = QVBoxLayout(self.banner); banner_layout.setContentsMargins(24, 250, 24, 18); banner_layout.setSpacing(8)
        details = QWidget(); details_layout = QVBoxLayout(details); details_layout.setContentsMargins(24, 20, 24, 24); details_layout.setSpacing(14); body.addWidget(details)
        self.back = QPushButton(); self.back.setObjectName('outlineButton'); self.back.clicked.connect(self.page.close_tool_page); banner_layout.addWidget(self.back, alignment=Qt.AlignLeft)
        self.name = QLabel(); self.name.setObjectName('storeDetailTitle'); banner_layout.addWidget(self.name)
        information = QHBoxLayout(); information.setSpacing(18); self.icon = QLabel('◆'); self.icon.setObjectName('storeDetailIcon'); self.icon.setAlignment(Qt.AlignCenter); self.icon.setFixedSize(104, 104); information.addWidget(self.icon, alignment=Qt.AlignTop)
        self.readme = QTextBrowser(); self.readme.setObjectName('storeReadme'); self.readme.setOpenExternalLinks(True); self.readme.setMinimumHeight(260); information.addWidget(self.readme, 1); details_layout.addLayout(information, 1)
        footer = QHBoxLayout(); self.socials = QHBoxLayout(); footer.addLayout(self.socials); footer.addStretch(); self.download = QPushButton(); self.download.setObjectName('primaryButton'); self.download.clicked.connect(lambda: self.page.action(self.tool)); footer.addWidget(self.download); details_layout.addLayout(footer)
        self.update_language(); self.load_resources()
    def text(self, ru, en): return ru if self.page.app.lang['_lang_code'] == 'ru' else en
    def update_language(self):
        state = self.tool.get('_store_state', 'download')
        labels = {'download': '↓  ' + self.page.app.lang['store_download'], 'downloading': self.text('Загрузка…', 'Downloading…'), 'update': self.text('Обновить', 'Update'), 'open': self.page.app.lang['open'] if sys.platform != 'win32' else self.text('Установить', 'Install'), 'installed': self.text('Открыть', 'Open')}
        self.back.setText(self.text('← К магазину', '← Back to store')); self.name.setText(str(self.manifest.get('name') or self.tool.get('name', ''))); self.download.setText(labels.get(state, labels['download']))
        self.download.setDisabled(state == 'downloading' or not self.page.runtime_available(self.tool))
        readme_source = (self.readme_sources or {}).get(self.page.app.lang['_lang_code']) or (self.readme_sources or {}).get('en')
        readme_html = markdown_to_html(readme_source) if readme_source else '<p>' + (self.text('README.md не найден.', 'README.md was not found.') if self.readme_sources is not None else self.text('Загрузка описания…', 'Loading description…')) + '</p>'
        self.readme.setHtml(readme_html)
        self.render_socials()
    def load_resources(self):
        manifest_url = self.page.tool_url(self.tool, self.tool.get('manifest') or 'manifest.json')
        def fetch():
            try:
                request = Request(manifest_url, headers={'User-Agent': 'SCS-Mega-Manager/1.0'})
                with open_remote(request, timeout=15) as response: manifest = json.loads(response.read().decode('utf-8-sig'))
                if not isinstance(manifest, dict): raise ValueError
                def read_asset(value, binary=True):
                    if not isinstance(value, str) or not value: return b'' if binary else ''
                    with open_remote(Request(urljoin(manifest_url, value), headers={'User-Agent': 'SCS-Mega-Manager/1.0'}), timeout=15) as response:
                        data = response.read()
                    return data if binary else data.decode('utf-8-sig')
                declared = manifest.get('readmes') if isinstance(manifest.get('readmes'), dict) else {'en': manifest.get('readme', '')}
                readmes = {str(code): read_asset(filename, False) for code, filename in declared.items() if isinstance(filename, str)}
                try: self.text_ready.emit(manifest, readmes)
                except RuntimeError: pass
                banner = read_asset(manifest.get('page_banner') or manifest.get('banner')); icon = read_asset(manifest.get('icon'))
                try: self.images_ready.emit(banner, icon)
                except RuntimeError: pass
            except (OSError, URLError, ValueError, UnicodeError, json.JSONDecodeError):
                try: self.text_ready.emit({}, {})
                except RuntimeError: pass
        threading.Thread(target=fetch, daemon=True).start()
    def apply_text_resources(self, manifest, readmes):
        self.manifest = manifest; self.readme_sources = readmes if isinstance(readmes, dict) else {}
        self.update_language()
    def apply_image_resources(self, banner, icon):
        if banner: self.banner.set_banner(banner)
        image = QPixmap()
        if icon and image.loadFromData(icon): self.icon.setPixmap(image.scaled(88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def render_socials(self):
        while self.socials.count():
            item = self.socials.takeAt(0); item.widget() and item.widget().deleteLater()
        labels = {'github': 'GitHub', 'discord': 'Discord', 'website': self.text('Личный сайт', 'Website'), 'youtube': 'YouTube'}
        links = self.manifest.get('socials', {})
        if not isinstance(links, dict) or not any(isinstance(value, str) and value.strip() for value in links.values()):
            note = QLabel(self.text('Ссылки автора пока не указаны.', 'Author links are not specified yet.')); note.setObjectName('storeDetailLinks'); self.socials.addWidget(note); return
        for key, value in links.items():
            if not isinstance(value, str) or not value.strip(): continue
            button = QPushButton(labels.get(key, key)); button.setObjectName('outlineButton'); button.setToolTip(key); button.clicked.connect(lambda _=False, link=value: QDesktopServices.openUrl(QUrl(link))); self.socials.addWidget(button)


class StoreCardSlot(QWidget):
    """Reserves a small margin so the card can grow in every direction on hover."""
    def __init__(self, card):
        super().__init__(); self.card = card; self.card.setParent(self); self.hovered = False; self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.animation_start = QRect(); self.animation_end = QRect(); self.animation_clock = QElapsedTimer()
        self.animation_timer = QTimer(self); self.animation_timer.setTimerType(Qt.PreciseTimer); self.animation_timer.setInterval(16); self.animation_timer.timeout.connect(self.advance_animation)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self.card.heightForWidth(max(1, width - 12)) + 12
    def sizeHint(self):
        size = self.card.sizeHint(); size.setWidth(size.width() + 12); size.setHeight(size.height() + 12); return size
    def card_rect(self, expanded):
        return self.rect() if expanded else self.rect().adjusted(6, 6, -6, -6)
    def resizeEvent(self, event):
        self.card.setGeometry(self.card_rect(self.hovered)); super().resizeEvent(event)
    def set_hovered(self, hovered):
        if self.hovered == hovered: return
        self.hovered = hovered; self.animation_start = self.card.geometry(); self.animation_end = self.card_rect(hovered)
        if self.animation_start == self.animation_end: return
        self.animation_clock.restart(); self.animation_timer.start()
    def advance_animation(self):
        progress = min(1.0, self.animation_clock.elapsed() / 190.0)
        # InOutCubic, evaluated by a precise local 60 Hz timer instead of the
        # shared Qt property-animation clock that can be throttled to 30 FPS.
        eased = 4 * progress ** 3 if progress < .5 else 1 - (-2 * progress + 2) ** 3 / 2
        start, end = self.animation_start, self.animation_end
        rect = QRect(round(start.x() + (end.x() - start.x()) * eased), round(start.y() + (end.y() - start.y()) * eased), round(start.width() + (end.width() - start.width()) * eased), round(start.height() + (end.height() - start.height()) * eased))
        self.card.setGeometry(rect)
        # Geometry updates are normally coalesced by QWidget and can render
        # only every second timer tick.  The card paint is now inexpensive
        # (the banner is no longer rescaled here), so force this short hover
        # animation to present every 16 ms frame.
        self.card.repaint()
        if progress >= 1.0: self.animation_timer.stop(); self.card.setGeometry(self.animation_end)


class StoreToolCard(QFrame):
    image_ready = pyqtSignal(str, object)
    def __init__(self, page, tool):
        super().__init__(); self.page, self.tool, self.banner = page, tool, QPixmap(); self.setObjectName("storeToolCard"); self.setMinimumHeight(118); self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.image_ready.connect(self.set_image)
        layout = QHBoxLayout(self); layout.setContentsMargins(22, 14, 22, 14); layout.setSpacing(14)
        self.icon = QLabel("◆"); self.icon.setObjectName("storeBannerIcon"); self.icon.setAlignment(Qt.AlignCenter); self.icon.setFixedSize(58, 58)
        text_block = QVBoxLayout(); self.name = QLabel(); self.name.setObjectName("storeBannerTitle"); self.description = QLabel(); self.description.setObjectName("storeBannerDescription"); self.description.setWordWrap(True); self.system_notice = QLabel(); self.system_notice.setObjectName("systemUnavailable"); self.system_notice.setWordWrap(True); self.meta = QLabel(); self.meta.setObjectName("storeBannerMeta"); self.meta.setWordWrap(True); self.uid = QLabel(); self.uid.setObjectName("storeBannerUid"); self.uid.setWordWrap(True)
        text_block.addWidget(self.name); text_block.addWidget(self.description); text_block.addWidget(self.system_notice); text_block.addSpacing(4); text_block.addWidget(self.meta); text_block.addWidget(self.uid); text_block.addStretch()
        self.open_page = QPushButton(); self.open_page.setObjectName("outlineButton"); self.open_page.clicked.connect(lambda: self.page.open_tool_page(self.tool))
        self.download = QPushButton(); self.download.setObjectName("primaryButton"); self.download.clicked.connect(lambda: self.page.action(self.tool))
        self.collection = QPushButton(); self.collection.setObjectName("outlineButton"); self.collection.clicked.connect(lambda: self.page.assign_collection(str(self.tool.get('uid', ''))))
        self.delete = QPushButton(); self.delete.setObjectName("dangerButton"); self.delete.clicked.connect(lambda: self.page.delete_tool(self.tool))
        actions = QVBoxLayout(); actions.addStretch(); actions.addWidget(self.open_page); actions.addWidget(self.download); actions.addWidget(self.collection); actions.addWidget(self.delete); actions.addStretch(); layout.addWidget(self.icon); layout.addLayout(text_block, 1); layout.addLayout(actions)
        self.update_language(); self.load_image("banner", page.tool_url(tool, tool.get("banner"))); self.load_image("icon", page.tool_url(tool, tool.get("icon")))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width):
        layout = self.layout()
        if layout is None: return self.minimumHeight()
        return max(self.minimumHeight(), layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height())
    def sizeHint(self):
        size = super().sizeHint(); size.setHeight(self.heightForWidth(size.width())); return size
    def load_image(self, kind, url):
        if not url: return
        def fetch():
            try:
                request = Request(url, headers={"User-Agent": "SCS-Mega-Manager/1.0"})
                with open_remote(request, timeout=15) as response: data = response.read()
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    try: self.image_ready.emit(kind, pixmap)
                    except RuntimeError: pass
            except (OSError, URLError, ValueError): pass
        threading.Thread(target=fetch, daemon=True).start()
    def set_image(self, kind, pixmap):
        if kind == "banner": self.banner = pixmap; self.update()
        elif kind == "icon": self.icon.setPixmap(pixmap.scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def update_language(self):
        lang_code = self.page.app.lang["_lang_code"]; descriptions = self.tool.get("descriptions", {}); description = (descriptions.get(lang_code) or descriptions.get("en")) if isinstance(descriptions, dict) else ""
        self.name.setText(str(self.tool.get("name", ""))); self.description.setText(str(description or self.page.text("Описание не указано.", "Description not provided.")))
        updated = str(self.tool.get("updated_at", ""))[:10] or "—"; runtime = str(self.tool.get("runtime_type", "—")); size = human_size(self.tool.get("size_bytes")); category = str(self.tool.get("category", "")).strip()
        category_meta = self.page.text(f"  •  Категория: {category}", f"  •  Category: {category}") if category else ""
        systems = self.tool.get('systems', self.tool.get('platforms'))
        systems_meta = f"  •  OS: {', '.join(map(str, systems))}" if isinstance(systems, list) and systems else ""
        self.meta.setText(self.page.text(f"Обновлено: {updated}  •  Runtime: {runtime}  •  Размер: {size}", f"Updated: {updated}  •  Runtime: {runtime}  •  Size: {size}") + category_meta + systems_meta)
        self.uid.setText("UID: " + str(self.tool.get("uid", "—"))); self.open_page.setText(self.page.text("Открыть страницу", "Open Page"))
        self.collection.setText(self.page.text("Коллекция…", "Collection…"))
        state = self.tool.get('_store_state', 'download')
        labels = {'download': "↓  " + self.page.app.lang["store_download"], 'downloading': self.page.text("Загрузка…", "Downloading…"), 'update': self.page.text("Обновить", "Update"), 'open': self.page.app.lang['open'] if sys.platform != 'win32' else self.page.text("Установить", "Install"), 'installed': self.page.text("Открыть", "Open")}
        available = self.page.runtime_available(self.tool)
        self.system_notice.setText(self.page.text("Недоступно на Вашей системе", "Unavailable on your system")); self.system_notice.setVisible(not available)
        self.download.setText(labels.get(state, labels['download']))
        self.download.setDisabled(state == 'downloading' or not available)
        version = str(self.tool.get("version", "")).strip()
        if version:
            self.meta.setText(f"{self.page.app.lang['store_version']}: {version}  •  " + self.meta.text())
        local = self.page.local_tool(self.tool)
        self.delete.setText(self.page.text("Удалить", "Delete")); self.delete.setVisible(local is not None)
        self.description.updateGeometry(); self.updateGeometry()
        parent = self.parentWidget()
        if isinstance(parent, StoreCardSlot): parent.updateGeometry()
    def enterEvent(self, event):
        if isinstance(self.parentWidget(), StoreCardSlot): self.parentWidget().set_hovered(True)
        super().enterEvent(event)
    def leaveEvent(self, event):
        if isinstance(self.parentWidget(), StoreCardSlot): self.parentWidget().set_hovered(False)
        super().leaveEvent(event)
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing); rect = self.rect().adjusted(0, 0, -1, -1); path = QPainterPath(); path.addRoundedRect(QRectF(rect), 18, 18); painter.setClipPath(path)
        if not draw_cover_pixmap(painter, rect, self.banner): painter.fillRect(rect, QColor("#151b24"))
        gradient = QLinearGradient(0, 0, rect.width(), 0); gradient.setColorAt(0, QColor(8, 11, 16, 245)); gradient.setColorAt(.55, QColor(9, 12, 18, 205)); gradient.setColorAt(1, QColor(9, 12, 18, 125)); painter.fillRect(rect, gradient); painter.setClipping(False); painter.setPen(QColor("#ee6b2f") if isinstance(self.parentWidget(), StoreCardSlot) and self.parentWidget().hovered else QColor("#3a4657")); painter.drawRoundedRect(rect, 18, 18)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def download_archive_name(value):
    """Return a safe local archive name from a catalog URL or file name."""
    if not isinstance(value, str) or not value.strip():
        return ""
    parsed = urlparse(value.strip())
    path = parsed.path if parsed.scheme in {"http", "https"} and parsed.netloc else value.strip()
    name = Path(unquote(path)).name
    return name if name and Path(name).suffix.lower() == ".zip" else ""


def catalog_tool_url(tool, filename):
    """Resolve a catalog file name, preserving an explicitly supplied web URL."""
    if not isinstance(filename, str):
        return ""
    value = filename.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    if parsed.scheme or parsed.netloc or Path(value).name != value:
        return ""
    uid = str(tool.get('uid', '')).strip() if isinstance(tool, dict) else ""
    return urljoin(STORE_CATALOG_URL, f"verified_tools/{uid}/{value}") if uid else ""


class PublishToolDialog(QDialog):
    DISCORD_URL = "https://discord.com/channels/1425865850535149660/1477644209497571338"
    def __init__(self, app):
        super().__init__(app); self.app = app; self.setObjectName('installationDialog'); self.setMinimumSize(650, 500)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 22); layout.setSpacing(12)
        self.title = QLabel(); self.title.setObjectName('storeDetailTitle'); layout.addWidget(self.title)
        self.text = QTextBrowser(); self.text.setObjectName('publishText'); self.text.setOpenExternalLinks(True); layout.addWidget(self.text, 1)
        buttons = QHBoxLayout(); buttons.addStretch(); self.discord = QPushButton(); self.discord.setObjectName('primaryButton'); self.discord.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.DISCORD_URL))); self.close = QPushButton(); self.close.setObjectName('outlineButton'); self.close.clicked.connect(self.accept); buttons.addWidget(self.discord); buttons.addWidget(self.close); layout.addLayout(buttons)
        self.update_text()
    def update_text(self):
        ru = self.app.lang['_lang_code'] == 'ru'; self.setWindowTitle('Публикация инструмента' if ru else 'Publish a tool'); self.title.setText('Опубликовать свой инструмент' if ru else 'Publish your tool')
        if ru:
            text = '<h3>Подготовьте структуру проекта</h3><p>В архиве с информацией об инструменте должны находиться:</p><pre>manifest.json\nREADME.md\nREADME_ru.md\nicon.png\nbanner.png\npage_banner.png</pre><p><b>UID обязателен.</b> <b>README_ru.md</b> — необязательный файл: если его нет, будет показан английский README.md.</p><h3>Структура manifest.json</h3><pre>{\n  &quot;schema_version&quot;: 1,\n  &quot;uid&quot;: &quot;ваш-UID&quot;,\n  &quot;name&quot;: &quot;Название инструмента&quot;,\n  &quot;category&quot;: &quot;Категория&quot;,\n  &quot;banner&quot;: &quot;banner.png&quot;,\n  &quot;page_banner&quot;: &quot;page_banner.png&quot;,\n  &quot;icon&quot;: &quot;icon.png&quot;,\n  &quot;readme&quot;: &quot;README.md&quot;,\n  &quot;readmes&quot;: {\n    &quot;en&quot;: &quot;README.md&quot;,\n    &quot;ru&quot;: &quot;README_ru.md&quot;\n  },\n  &quot;socials&quot;: {\n    &quot;Github&quot;: &quot;&quot;,\n    &quot;discord&quot;: &quot;&quot;,\n    &quot;website&quot;: &quot;&quot;,\n    &quot;Telegram&quot;: &quot;&quot;\n  }\n}</pre><h3>Отправьте на проверку</h3><p>Отправьте ZIP-архив с информацией, ZIP-архив с инструментом или ссылки на них в Discord-канал:<br><a href="' + self.DISCORD_URL + '">' + self.DISCORD_URL + '</a></p>'
        else:
            text = '<h3>Prepare the project structure</h3><p>The tool-information archive must contain:</p><pre>manifest.json\nREADME.md\nREADME_ru.md\nicon.png\nbanner.png\npage_banner.png</pre><p><b>UID is required.</b> <b>README_ru.md</b> is optional: if it is absent, the English README.md is shown.</p><h3>manifest.json structure</h3><pre>{\n  &quot;schema_version&quot;: 1,\n  &quot;uid&quot;: &quot;your-UID&quot;,\n  &quot;name&quot;: &quot;Tool name&quot;,\n  &quot;category&quot;: &quot;Category&quot;,\n  &quot;banner&quot;: &quot;banner.png&quot;,\n  &quot;page_banner&quot;: &quot;page_banner.png&quot;,\n  &quot;icon&quot;: &quot;icon.png&quot;,\n  &quot;readme&quot;: &quot;README.md&quot;,\n  &quot;readmes&quot;: {\n    &quot;en&quot;: &quot;README.md&quot;,\n    &quot;ru&quot;: &quot;README_ru.md&quot;\n  },\n  &quot;socials&quot;: {\n    &quot;Github&quot;: &quot;&quot;,\n    &quot;discord&quot;: &quot;&quot;,\n    &quot;website&quot;: &quot;&quot;,\n    &quot;Telegram&quot;: &quot;&quot;\n  }\n}</pre><h3>Send it for review</h3><p>Send the information ZIP archive, the tool ZIP archive, or links to them in this Discord channel:<br><a href="' + self.DISCORD_URL + '">' + self.DISCORD_URL + '</a></p>'
        self.text.setHtml(text); self.discord.setText('Открыть Discord' if ru else 'Open Discord'); self.close.setText('Закрыть' if ru else 'Close')


class StorePage(Page):
    catalog_ready = pyqtSignal(object, str)
    download_finished = pyqtSignal(object, str)
    download_progress = pyqtSignal(str, int, int)
    def __init__(self, app):
        if not hasattr(app, 'settings') or not isinstance(app.settings, dict): app.settings = {}
        super().__init__(app, "store"); self.title, self.subtitle = self.heading("", ""); self.tools = []; self.catalog_metadata = {}; self.tool_page = None; self.page_cache = OrderedDict(); self.catalog_unavailable_notified = False; self.catalog_error = False; self.verified_loaded = False; self.installed_hashes = None; self.status_state = ("catalog_loading", {}); self.active_downloads = set(); self.download_jobs = {}; self.last_failed_tool = None
        self.catalog_panel = QWidget(); self.catalog_panel.setObjectName('storeContent'); catalog_layout = QVBoxLayout(self.catalog_panel); catalog_layout.setContentsMargins(0, 0, 0, 4); catalog_layout.setSpacing(10); catalog_layout.setAlignment(Qt.AlignTop)
        head = QHBoxLayout(); self.status = QLabel(); self.status.setObjectName("pageSubtitle"); head.addWidget(self.status); head.addStretch(); self.publish = QPushButton(); self.publish.setObjectName("outlineButton"); self.publish.clicked.connect(lambda: PublishToolDialog(self.app).exec_()); self.refresh = QPushButton(); self.refresh.setObjectName("primaryButton"); self.refresh.clicked.connect(lambda: self.refresh_catalog(clear_page_cache=True)); head.addWidget(self.publish); head.addWidget(self.refresh); catalog_layout.addLayout(head)
        filters = QHBoxLayout(); self.search = QLineEdit(); self.search.setObjectName('input'); self.search.textChanged.connect(self.apply_filters); self.sort = QComboBox(); self.sort.setObjectName('input'); [self.sort.addItem('', key) for key in ('updated', 'size', 'name')]; self.sort.currentIndexChanged.connect(self.apply_filters); filters.addWidget(self.search, 1); filters.addWidget(self.sort); catalog_layout.addLayout(filters)
        filters_primary = QHBoxLayout(); filters_secondary = QHBoxLayout(); self.runtime_filter = QComboBox(); self.state_filter = QComboBox(); self.category_filter = QComboBox(); self.author_filter = QComboBox(); self.version_filter = QComboBox(); self.system_filter = QComboBox(); self.tag_filter = QComboBox(); self.collection_filter = QComboBox()
        for combo in (self.runtime_filter, self.state_filter, self.category_filter, self.author_filter, self.version_filter, self.system_filter, self.tag_filter, self.collection_filter): combo.setObjectName('input'); combo.currentIndexChanged.connect(self.apply_filters)
        for combo in (self.runtime_filter, self.state_filter, self.category_filter, self.author_filter): filters_primary.addWidget(combo, 1)
        for combo in (self.version_filter, self.system_filter, self.tag_filter, self.collection_filter): filters_secondary.addWidget(combo, 1)
        catalog_layout.addLayout(filters_primary); catalog_layout.addLayout(filters_secondary)
        self.download_panel = QFrame(); self.download_panel.setObjectName('card'); progress_layout = QHBoxLayout(self.download_panel); progress_layout.setContentsMargins(12, 8, 12, 8); self.download_label = QLabel(); self.download_label.setObjectName('pageSubtitle'); self.progress = QProgressBar(); self.progress.setTextVisible(True); self.pause_download = QPushButton(); self.pause_download.setObjectName('outlineButton'); self.pause_download.clicked.connect(self.toggle_download_pause); self.cancel_download = QPushButton(); self.cancel_download.setObjectName('dangerButton'); self.cancel_download.clicked.connect(self.cancel_active_download); self.retry_download = QPushButton(); self.retry_download.setObjectName('outlineButton'); self.retry_download.clicked.connect(self.retry_last_download); progress_layout.addWidget(self.download_label); progress_layout.addWidget(self.progress, 1); progress_layout.addWidget(self.pause_download); progress_layout.addWidget(self.cancel_download); progress_layout.addWidget(self.retry_download); self.download_panel.hide(); catalog_layout.addWidget(self.download_panel)
        self.list = QVBoxLayout(); self.list.setSpacing(0); catalog_layout.addLayout(self.list); catalog_layout.addStretch()
        self.catalog_scroll = QScrollArea(); self.catalog_scroll.setObjectName('storeScroll'); self.catalog_scroll.setWidgetResizable(True); self.catalog_scroll.setFrameShape(QFrame.NoFrame); self.catalog_scroll.setWidget(self.catalog_panel); self.layout.addWidget(self.catalog_scroll, 1)
        self.catalog_ready.connect(self.receive_catalog); self.download_finished.connect(self.finish_download); self.download_progress.connect(self.update_download_progress); self.update_text(app.lang); self.refresh_catalog()
    def active_download_job(self):
        return next(iter(self.download_jobs.values()), None)
    def toggle_download_pause(self):
        job = self.active_download_job()
        if not job: return
        pause = job['pause']
        if pause.is_set(): pause.clear()
        else: pause.set()
        self.pause_download.setText(self.text('Продолжить', 'Resume') if pause.is_set() else self.text('Пауза', 'Pause'))
    def cancel_active_download(self):
        job = self.active_download_job()
        if job: job['cancel'].set(); job['pause'].clear()
    def retry_last_download(self):
        if self.last_failed_tool and not self.active_downloads: self.download(self.last_failed_tool)
    def update_download_progress(self, uid, received, total):
        job = self.download_jobs.get(uid)
        if not job: return
        self.download_panel.show(); self.download_label.setText(str(job['tool'].get('name', '')))
        if total > 0: self.progress.setRange(0, total); self.progress.setValue(received)
        else: self.progress.setRange(0, 0)
    def text(self, ru, en): return ru if self.app.lang["_lang_code"] == "ru" else en
    def runtime_available(self, tool):
        runtime = tool.get("runtime") if isinstance(tool.get("runtime"), dict) else tool.get("runtime_type", "")
        kind = runtime.get("type", "") if isinstance(runtime, dict) else str(runtime)
        if kind == "exe" and sys.platform != "win32": return False
        systems = tool.get('systems', tool.get('platforms'))
        if not isinstance(systems, list) or not systems: return True
        current = 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'linux'
        aliases = {str(value).strip().lower().replace('win32', 'windows').replace('darwin', 'macos') for value in systems}
        return current in aliases
    def rebuild_filter_choices(self):
        combos = (self.runtime_filter, self.state_filter, self.category_filter, self.author_filter, self.version_filter, self.system_filter, self.tag_filter, self.collection_filter); current = tuple(combo.currentData() for combo in combos); groups = self.app.settings.get('tool_collections', {}) if isinstance(self.app.settings.get('tool_collections'), dict) else {}; collection_names = sorted({str(value) for values in groups.values() if isinstance(values, list) for value in values if value})
        choices = (
            (self.runtime_filter, [('', self.text('Все Runtime', 'All runtimes'))] + [(value, value) for value in sorted({str(tool.get('runtime_type', '')).strip() for tool in self.tools if tool.get('runtime_type')})]),
            (self.state_filter, [('', self.text('Все состояния', 'All states')), ('download', self.text('Не скачан', 'Not downloaded')), ('update', self.text('Требует обновления', 'Update required')), ('open', self.text('Скачан', 'Downloaded')), ('installed', self.text('Установлен', 'Installed'))]),
            (self.category_filter, [('', self.text('Все категории', 'All categories'))] + [(value, value) for value in sorted({str(tool.get('category', '')).strip() for tool in self.tools if tool.get('category')})]),
            (self.author_filter, [('', self.text('Все авторы', 'All authors'))] + [(value, value) for value in sorted({str(tool.get('author', '')).strip() for tool in self.tools if tool.get('author')})]),
            (self.version_filter, [('', self.text('Все версии', 'All versions'))] + [(value, value) for value in sorted({str(tool.get('version', '')).strip() for tool in self.tools if tool.get('version')})]),
            (self.system_filter, [('', self.text('Все ОС', 'All systems'))] + [(value, value) for value in sorted({str(value).strip().lower() for tool in self.tools for value in ((tool.get('systems') if isinstance(tool.get('systems'), list) else tool.get('platforms') if isinstance(tool.get('platforms'), list) else [])) if value})]),
            (self.tag_filter, [('', self.text('Все теги', 'All tags'))] + [(value, value) for value in sorted({str(value).strip() for tool in self.tools for value in (tool.get('tags', []) if isinstance(tool.get('tags'), list) else []) if value})]),
            (self.collection_filter, [('', self.text('Все коллекции', 'All collections'))] + [(value, value) for value in collection_names]),
        )
        for index, (combo, values) in enumerate(choices):
            combo.blockSignals(True); combo.clear()
            for value, label in values: combo.addItem(label, value)
            found = combo.findData(current[index]); combo.setCurrentIndex(found if found >= 0 else 0); combo.blockSignals(False)
    def apply_filters(self, *_):
        words = self.search.text().casefold().split(); runtime = self.runtime_filter.currentData() or ''; state = self.state_filter.currentData() or ''; category = self.category_filter.currentData() or ''; author = self.author_filter.currentData() or ''; version = self.version_filter.currentData() or ''; system = self.system_filter.currentData() or ''; tag = self.tag_filter.currentData() or ''; collection = self.collection_filter.currentData() or ''; groups = self.app.settings.get('tool_collections', {}) if isinstance(self.app.settings.get('tool_collections'), dict) else {}
        slots = []
        while self.list.count():
            item = self.list.takeAt(0); widget = item.widget()
            if isinstance(widget, StoreCardSlot): slots.append(widget)
            elif widget: self.list.addWidget(widget); break
        mode = self.sort.currentData() or 'updated'
        def key(slot):
            tool = slot.card.tool
            if mode == 'size':
                try: return -int(tool.get('size_bytes') or 0)
                except (TypeError, ValueError): return 0
            if mode == 'name': return str(tool.get('name', '')).casefold()
            return str(tool.get('updated_at', ''))
        slots.sort(key=key, reverse=mode == 'updated')
        for slot in slots:
            tool = slot.card.tool; descriptions = tool.get('descriptions', {}); searchable = ' '.join((str(tool.get('name', '')), str(descriptions), str(tool.get('category', '')), str(tool.get('author', '')), str(tool.get('runtime_type', '')), str(tool.get('version', '')), str(tool.get('tags', '')))).casefold()
            raw_systems = tool.get('systems') if isinstance(tool.get('systems'), list) else tool.get('platforms') if isinstance(tool.get('platforms'), list) else []; systems = [str(value).strip().lower() for value in raw_systems]; tags = [str(value) for value in (tool.get('tags', []) if isinstance(tool.get('tags'), list) else [])]
            visible = all(word in searchable for word in words) and (not runtime or str(tool.get('runtime_type', '')) == runtime) and (not state or str(tool.get('_store_state', '')) == state) and (not category or str(tool.get('category', '')) == category) and (not author or str(tool.get('author', '')) == author) and (not version or str(tool.get('version', '')) == version) and (not system or system in systems) and (not tag or tag in tags) and (not collection or collection in groups.get(str(tool.get('uid', '')), []))
            slot.setVisible(visible); self.list.addWidget(slot)
    def assign_collection(self, uid):
        ru = self.app.lang['_lang_code'] == 'ru'; groups = self.app.settings.get('tool_collections', {}) if isinstance(self.app.settings.get('tool_collections'), dict) else {}; existing = ', '.join(groups.get(uid, [])); value, ok = QInputDialog.getText(self, 'Коллекции' if ru else 'Collections', 'Названия через запятую (пусто — убрать из всех):' if ru else 'Comma-separated names (empty removes from all):', text=existing)
        if not ok: return
        groups[uid] = list(dict.fromkeys(item.strip() for item in value.split(',') if item.strip())); self.app.settings['tool_collections'] = groups; save_settings(self.app.settings); self.rebuild_filter_choices(); self.refresh_cards(); self.apply_filters()
    def set_status(self, key, **data):
        self.status_state = (key, data); self.render_status()
    def render_status(self):
        key, data = getattr(self, 'status_state', ('catalog_loading', {}))
        messages = {
            'catalog_loading': self.text('Загрузка каталога…', 'Loading catalog…'),
            'catalog_error': self.text('Не удалось загрузить каталог.', 'Unable to load the catalog.'),
            'catalog_count': self.text(f"Инструментов: {data.get('count', 0)}", f"Tools: {data.get('count', 0)}"),
            'download_loading': self.text(f"Загрузка: {data.get('name', '')}…", f"Downloading: {data.get('name', '')}…"),
            'download_complete': self.text(f"{data.get('name', '')}: загрузка завершена.", f"{data.get('name', '')}: download complete."),
            'download_failed': self.text('Не удалось скачать или проверить инструмент.', 'Unable to download or verify the tool.'),
            'download_cancelled': self.text('Загрузка отменена.', 'Download cancelled.'),
        }
        self.status.setText(messages.get(key, ''))
    def remote_url(self, value): return urljoin(STORE_CATALOG_URL, str(value)) if isinstance(value, str) and value.strip() else ""
    def tool_url(self, tool, filename):
        return catalog_tool_url(tool, filename)
    def clear_page_cache(self):
        active = self.tool_page
        for page in self.page_cache.values():
            if page is not active: page.deleteLater()
        self.page_cache = OrderedDict((uid, page) for uid, page in self.page_cache.items() if page is active)
    def refresh_catalog(self, clear_page_cache=False):
        if clear_page_cache: self.clear_page_cache()
        self.catalog_unavailable_notified = False
        self.refresh.setEnabled(False); self.set_status('catalog_loading')
        def fetch():
            try:
                request = Request(STORE_CATALOG_URL, headers={"User-Agent": "SCS-Mega-Manager/1.0"})
                with open_remote(request, timeout=15) as response: data = json.loads(response.read().decode("utf-8-sig"))
                tools = data.get("verified_tools") if isinstance(data, dict) else None
                if not isinstance(tools, list): raise ValueError
                self.catalog_metadata = {key: value for key, value in data.items() if key != 'verified_tools'} if isinstance(data, dict) else {}
                installed_hashes = self.installed_hashes
                if installed_hashes is None:
                    installed_hashes = {}
                    for local_tool in self.app.registry.scan():
                        try: installed_hashes[local_tool.uid] = (file_sha256(local_tool.archive), local_tool.archive.name)
                        except OSError: pass
                    self.installed_hashes = installed_hashes
                valid = []
                for item in tools:
                    if not (isinstance(item, dict) and isinstance(item.get("uid"), str) and isinstance(item.get("name"), str) and isinstance(item.get("descriptions", {}).get("en"), str)): continue
                    entry = dict(item); expected = str(entry.get('sha256', '')).lower(); expected_name = download_archive_name(entry.get('download_url', '')); actual = installed_hashes.get(entry['uid'])
                    # A byte mismatch always wins: do not offer installation
                    # or opening until the verified archive has been updated.
                    entry['_store_state'] = 'update' if actual and actual[0] != expected else 'installed' if actual and actual[1] == expected_name and is_installed(entry['uid']) else 'open' if actual and actual[1] == expected_name else 'download'
                    valid.append(entry)
                try: self.catalog_ready.emit(valid, "")
                except RuntimeError: pass
            except (OSError, URLError, ValueError, UnicodeError, json.JSONDecodeError):
                self.catalog_metadata = {}
                try: self.catalog_ready.emit([], "catalog_unavailable")
                except RuntimeError: pass
        threading.Thread(target=fetch, daemon=True).start()
    def receive_catalog(self, tools, error):
        self.refresh.setEnabled(True); self.tools = tools; self.catalog_error = bool(error); self.verified_loaded = not self.catalog_error; self.set_status('catalog_error' if self.catalog_error else 'catalog_count', count=len(tools)); self.rebuild_filter_choices(); self.populate()
        pages = getattr(self.app, 'pages', {})
        if isinstance(pages, dict) and pages.get('dashboard'): pages['dashboard'].refresh_overview()
        if isinstance(pages, dict) and pages.get('library'): pages['library'].refresh()
        if error and not self.catalog_unavailable_notified:
            self.catalog_unavailable_notified = True; message = self.text('Магазин недоступен. Проверьте подключение к интернету.', 'The store is unavailable. Check your internet connection.'); self.app.add_notification(message, 'error', 'store', 'catalog-unavailable', modal=True)
        elif not error:
            updates = sum(1 for tool in tools if tool.get('_store_state') == 'update')
            if updates: self.app.add_notification(self.text(f'Доступно обновлений инструментов: {updates}.', f'Tool updates available: {updates}.'), 'info', 'store', 'tool-updates-' + str(updates))
    def populate(self):
        if getattr(self, '_preserve_card_art', False):
            self._preserve_card_art = False
            self.refresh_cards()
            return
        while self.list.count():
            item = self.list.takeAt(0); widget = item.widget(); widget and widget.deleteLater()
        if not self.tools:
            empty = QLabel(self.text("В каталоге пока нет доступных инструментов.", "No tools are currently available in the catalog.")); empty.setObjectName("emptyHint"); self.list.addWidget(empty); return
        for tool in self.tools: self.list.addWidget(StoreCardSlot(StoreToolCard(self, tool)))
        self.apply_filters()
    def action(self, tool):
        state = tool.get('_store_state')
        if state in ('open', 'installed'):
            expected_name = download_archive_name(tool.get('download_url', ''))
            local = next((item for item in self.app.registry.scan() if item.uid == tool.get('uid') and item.archive.name == expected_name), None)
            if local:
                if state == 'installed' or sys.platform != 'win32': self.app.open_tool(local)
                else: self.app.show_install_dialog(local)
                return
            tool['_store_state'] = 'update'; self.refresh_cards()
        self.download(tool)
    def local_tool(self, tool):
        expected_name = download_archive_name(tool.get('download_url', ''))
        return next((item for item in self.app.registry.scan() if item.uid == tool.get('uid') and item.archive.name == expected_name), None)
    def delete_tool(self, tool):
        local = self.local_tool(tool)
        if local: self.app.delete_tool(local)
    def download(self, tool):
        url = self.tool_url(tool, tool.get("download_url")); uid = str(tool.get("uid", ""))
        if not url or not uid or uid in self.active_downloads: return
        pause_event, cancel_event = threading.Event(), threading.Event(); self.download_jobs[uid] = {'tool': tool, 'pause': pause_event, 'cancel': cancel_event}; self.active_downloads.add(uid); self.last_failed_tool = None; tool['_store_state'] = 'downloading'; self.download_panel.show(); self.progress.setRange(0, 0); self.pause_download.setText(self.text('Пауза', 'Pause')); self.refresh_cards()
        application_logger().info("Store download started: name=%r uid=%s url=%s", tool.get('name', ''), uid, url)
        self.set_status('download_loading', name=str(tool.get('name', '')))
        def fetch():
            temporary = None
            try:
                request = Request(url, headers={"User-Agent": "SCS-Mega-Manager/1.0"})
                with open_remote(request, timeout=30) as response:
                    try: total = int(response.headers.get('Content-Length') or 0)
                    except (TypeError, ValueError): total = 0
                    suffix = Path(urlparse(url).path).suffix or ".zip"; handle, temporary = tempfile.mkstemp(prefix="scs_download_", suffix=suffix, dir=str(self.app.registry.tools_dir)); os.close(handle)
                    with open(temporary, "wb") as output:
                        received = 0
                        while True:
                            if cancel_event.is_set(): raise InterruptedError('download cancelled')
                            while pause_event.is_set() and not cancel_event.wait(.1): pass
                            if cancel_event.is_set(): raise InterruptedError('download cancelled')
                            chunk = response.read(1024 * 1024)
                            if not chunk: break
                            output.write(chunk); received += len(chunk)
                            try: self.download_progress.emit(uid, received, total)
                            except RuntimeError: pass
                with zipfile.ZipFile(temporary) as archive:
                    if archive.testzip() is not None: raise zipfile.BadZipFile
                    info = json.loads(archive.read("info.json").decode("utf-8-sig"))
                if info.get("uid") != uid: raise ValueError
                expected_hash = str(tool.get("sha256", "")).lower()
                if not re.fullmatch(r"[0-9a-f]{64}", expected_hash): raise ValueError
                digest = file_sha256(temporary)
                if digest != expected_hash: raise ValueError
                expected_name = download_archive_name(tool.get('download_url', ''))
                if not expected_name: raise ValueError
                target = self.app.registry.tools_dir / expected_name
                occupied = next((item for item in self.app.registry.scan() if item.archive == target), None)
                if occupied and occupied.uid != uid: raise ValueError
                os.replace(temporary, target); temporary = None
                application_logger().info("Store download verified: name=%r uid=%s", tool.get('name', ''), uid)
                try: self.download_finished.emit(tool, "")
                except RuntimeError: pass
            except InterruptedError as error:
                application_logger().info("Store download cancelled: name=%r uid=%s", tool.get('name', ''), uid)
                try: self.download_finished.emit(tool, 'download_cancelled')
                except RuntimeError: pass
            except (OSError, URLError, ValueError, KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
                application_logger().warning("Store download failed: name=%r uid=%s error=%s", tool.get('name', ''), uid, error)
                try: self.download_finished.emit(tool, 'download_failed')
                except RuntimeError: pass
            finally:
                if temporary:
                    try: os.unlink(temporary)
                    except OSError: pass
        threading.Thread(target=fetch, daemon=True).start()
    def finish_download(self, tool, error):
        uid = str(tool.get('uid', '')); self.active_downloads.discard(uid); self.download_jobs.pop(uid, None)
        if error:
            tool['_store_state'] = 'download'; self.set_status(error); self.last_failed_tool = tool if error != 'download_cancelled' else None; self.retry_download.setVisible(self.last_failed_tool is not None); self.refresh_cards()
            if error != 'download_cancelled': self.app.add_notification(self.text('Не удалось скачать инструмент. Можно повторить загрузку.', 'Tool download failed. You can retry it.'), 'error', 'store', 'download-failed-' + uid + '-' + time.strftime('%Y%m%d%H'), modal=True)
            if not self.active_downloads and self.last_failed_tool is None: self.download_panel.hide()
            return
        name = str(tool.get('name', '')); installed = is_installed(str(tool.get('uid', ''))); tool['_store_state'] = 'installed' if installed else 'open'; self.installed_hashes = self.installed_hashes or {}; self.installed_hashes[str(tool.get('uid', ''))] = (str(tool.get('sha256', '')).lower(), download_archive_name(tool.get('download_url', '')))
        if installed:
            refreshed = self.local_tool(tool)
            if refreshed:
                try: update_installed_tool(refreshed)
                except (OSError, ValueError): application_logger().exception('Unable to refresh installed launcher for %s', refreshed.uid)
        downloaded = self.app.settings.get('downloaded', []) if isinstance(self.app.settings.get('downloaded'), list) else []
        if uid not in downloaded: downloaded.append(uid); self.app.settings['downloaded'] = downloaded; save_settings(self.app.settings)
        self.app.pages["library"].refresh(); self.set_status('download_complete', name=name)
        self.refresh_cards()
        if not self.active_downloads: QTimer.singleShot(700, self.download_panel.hide)
        if self.tool_page and str(self.tool_page.tool.get('uid', '')) == str(tool.get('uid', '')): self.tool_page.update_language()
        self.app.add_notification(f"{name} {self.app.lang['download_success']}", 'info', 'store', 'download-' + str(tool.get('uid', '')), modal=True)
    def open_tool_page(self, tool):
        uid = str(tool.get('uid', ''))
        if self.tool_page and self.tool_page.tool.get('uid') == uid: return
        if self.tool_page: self.close_tool_page()
        page = self.page_cache.pop(uid, None)
        if page is None: page = StoreToolPagePanel(self, tool)
        self.page_cache[uid] = page
        while len(self.page_cache) > 5:
            _, outdated = self.page_cache.popitem(last=False); outdated.deleteLater()
        self.tool_page = page; self.catalog_scroll.hide(); self.layout.addWidget(page, 1); page.show(); page.update_language()
    def close_tool_page(self):
        if not self.tool_page: return
        self.layout.removeWidget(self.tool_page); self.tool_page.hide(); self.tool_page = None; self.catalog_scroll.show()
    def refresh_cards(self):
        """Refresh state and text without replacing cards or reloading images."""
        for index in range(self.list.count()):
            slot = self.list.itemAt(index).widget(); card = slot.card if isinstance(slot, StoreCardSlot) else slot
            if isinstance(card, StoreToolCard): card.update_language()
    def update_text(self, lang):
        self.title.setText(lang["download_header"]); self.subtitle.setText(lang["dashboard_subtitle"]); self.publish.setText("Опубликовать свой" if lang['_lang_code'] == 'ru' else "Publish your tool"); self.refresh.setText("↻  " + lang["download_refresh_btn"])
        ru = lang['_lang_code'] == 'ru'; self.search.setPlaceholderText('Поиск по магазину…' if ru else 'Search Store…'); sort_labels = {'updated': ('Сначала новые', 'Newest'), 'size': ('По размеру', 'By size'), 'name': ('По названию', 'By name')}
        self.pause_download.setText('Пауза' if ru else 'Pause'); self.cancel_download.setText('Отмена' if ru else 'Cancel'); self.retry_download.setText('Повторить' if ru else 'Retry')
        for index in range(self.sort.count()): self.sort.setItemText(index, sort_labels[self.sort.itemData(index)][0 if ru else 1])
        self.rebuild_filter_choices()
        self.render_status()
        if not self.tools:
            self.populate()
        else: self.refresh_cards()
        if self.tool_page: self.tool_page.update_language()


class ToolInfoDialog(QDialog):
    def __init__(self, tool, parent):
        super().__init__(parent); self.setObjectName("toolInfoDialog"); lang = parent.app.lang; self.setWindowTitle(lang["tool_info_title"]); self.resize(720, 500)
        layout = QVBoxLayout(self); tree = QTreeWidget(); tree.setHeaderLabels(["Parameter", "Value"]); tree.setSelectionMode(QAbstractItemView.ExtendedSelection); tree.setColumnWidth(0, 240)
        rows = [("UID", tool.uid), (lang["field_archive"], tool.archive.name), (lang["field_archive_path"], str(tool.archive)), (lang["field_downloaded_at"], tool.downloaded_at.strftime("%d.%m.%Y %H:%M")), (lang["field_updated_at"], tool.updated_at.strftime("%d.%m.%Y %H:%M"))] + list(flatten(tool.data))
        for key, value in rows: tree.addTopLevelItem(QTreeWidgetItem([key, value]))
        copy = QPushButton(lang["copy_selected"]); copy.setObjectName("primaryButton")
        def copy_selected(): QApplication.clipboard().setText("\n".join(f"{x.text(0)}: {x.text(1)}" for x in tree.selectedItems()))
        copy.clicked.connect(copy_selected); layout.addWidget(tree); layout.addWidget(copy, alignment=Qt.AlignRight)


class ProjectEditorPanel(QWidget):
    """Second creation step: edits an archive through a temporary project tree."""
    def __init__(self, archive_path, parent):
        super().__init__(parent); self.archive_path = Path(archive_path); self.lang_code = parent.app.lang["_lang_code"]; self.current_file = None; self.owner = parent
        self.work_dir = Path(tempfile.mkdtemp(prefix="scs_tool_project_"))
        with zipfile.ZipFile(self.archive_path) as archive: archive.extractall(self.work_dir)
        self.excluded_store = self.owner.development_dir / self.archive_path.stem / '.excluded'
        self.exclusions_file = self.owner.development_dir / self.archive_path.stem / 'editor_exclusions.json'
        try:
            values = json.loads(self.exclusions_file.read_text(encoding='utf-8'))
            self.excluded_files = {str(value) for value in values if isinstance(value, str)} if isinstance(values, list) else set()
        except (OSError, ValueError, TypeError): self.excluded_files = set()
        for relative in list(self.excluded_files):
            saved = self.excluded_store / relative; target = self.work_dir / relative
            if saved.is_file() and not target.exists(): target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(saved, target)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(10)
        header = QFrame(); header.setObjectName("card"); header_layout = QVBoxLayout(header); self.badge = QLabel(self.text("ЭТАП 2  •  СТРУКТУРА ПРОЕКТА", "STEP 2  •  PROJECT STRUCTURE")); self.badge.setObjectName("eyebrow"); self.hint = QLabel(self.text("Щёлкните правой кнопкой мыши по дереву, чтобы создать папку, файл или загрузить файл. Изменения сохраняются в ZIP.", "Right-click the tree to create a folder, file or upload a file. Changes are saved to the ZIP.")); self.hint.setObjectName("pageSubtitle"); self.hint.setWordWrap(True); header_layout.addWidget(self.badge); header_layout.addWidget(self.hint); layout.addWidget(header)
        self.stage_note = QLabel(self.text("* Удобно отредактировать HTML Вы сможете на 3 этапе. Здесь рекомендуется создать только скрипты.", "* You will be able to edit HTML conveniently in step 3. It is recommended to create scripts only here.")); self.stage_note.setObjectName("stageNote"); self.stage_note.setWordWrap(True); layout.addWidget(self.stage_note)
        tools = QHBoxLayout(); tools.setSpacing(7)
        self.project_search = QLineEdit(); self.project_search.setObjectName("input"); self.project_search.setClearButtonEnabled(True); self.project_search.textChanged.connect(self.search_project); self.project_search.returnPressed.connect(self.find_next)
        self.symbols = QComboBox(); self.symbols.setObjectName("input"); self.symbols.setMinimumWidth(175); self.symbols.currentIndexChanged.connect(self.go_to_symbol)
        self.format_button = QPushButton(); self.format_button.setObjectName("outlineButton"); self.format_button.clicked.connect(self.format_current)
        self.docs_button = QPushButton(); self.docs_button.setObjectName("outlineButton"); self.docs_button.clicked.connect(self.create_project_docs)
        self.test_action_button = QPushButton(); self.test_action_button.setObjectName("outlineButton"); self.test_action_button.clicked.connect(self.test_action)
        tools.addWidget(self.project_search, 1); tools.addWidget(self.symbols); tools.addWidget(self.format_button); tools.addWidget(self.docs_button); tools.addWidget(self.test_action_button); layout.addLayout(tools)
        splitter = QSplitter(Qt.Horizontal); self.tree = QTreeWidget(); self.tree.setObjectName("projectTree"); self.tree.setHeaderLabel(self.text("Структура проекта", "Project structure")); self.tree.itemClicked.connect(self.open_item); self.tree.setContextMenuPolicy(Qt.CustomContextMenu); self.tree.customContextMenuRequested.connect(self.show_context_menu); splitter.addWidget(self.tree)
        editor_panel = QFrame(); editor_panel.setObjectName("card"); editor_layout = QVBoxLayout(editor_panel); self.file_name = QLabel(self.text("Выберите текстовый файл", "Select a text file")); self.file_name.setObjectName("fieldLabel"); self.content_stack = QStackedWidget(); self.editor = QPlainTextEdit(); self.editor.setObjectName("projectEditor"); self.editor.setPlaceholderText(self.text("Содержимое файла", "File contents")); self.editor.setReadOnly(True); self.highlighter = CodeHighlighter(self.editor.document()); self.image_preview = QLabel(self.text("Выберите изображение", "Select an image")); self.image_preview.setObjectName("imagePreview"); self.image_preview.setAlignment(Qt.AlignCenter); self.image_preview.setWordWrap(True); self.content_stack.addWidget(self.editor); self.content_stack.addWidget(self.image_preview); editor_layout.addWidget(self.file_name); editor_layout.addWidget(self.content_stack, 1); splitter.addWidget(editor_panel); splitter.setSizes([310, 650]); layout.addWidget(splitter, 1)
        buttons = QHBoxLayout(); self.save_state = QLabel(); self.save_state.setObjectName("pageSubtitle"); self.back = QPushButton(self.text("← К этапу 1", "← Back to step 1")); self.back.setObjectName("outlineButton"); self.back.clicked.connect(self.owner.close_project_editor); self.next = QPushButton(self.text("К этапу 3 →", "To step 3 →")); self.next.setObjectName("primaryButton"); self.next.clicked.connect(self.open_html_editor); self.save = QPushButton(self.text("Сохранить в ZIP", "Save to ZIP")); self.save.setObjectName("outlineButton"); self.save.clicked.connect(self.save_project); buttons.addWidget(self.save_state); buttons.addWidget(self.back); buttons.addStretch(); buttons.addWidget(self.save); buttons.addWidget(self.next); layout.addLayout(buttons)
        self.autosave_timer = QTimer(self); self.autosave_timer.setSingleShot(True); self.autosave_timer.timeout.connect(self.save_project); self.editor.textChanged.connect(self.mark_unsaved); self.is_saved = True
        self.refresh_tree()
        self.set_saved()
        # The editor is often created after the parent page has already been
        # translated.  Populate every toolbar caption immediately.
        self.update_language(self.lang_code)
    def set_saved(self): self.is_saved = True; self.save_state.setText(self.text("● Сохранено", "● Saved"))
    def mark_unsaved(self):
        if self.current_file and not self.editor.isReadOnly(): self.is_saved = False; self.save_state.setText(self.text("● Не сохранено", "● Unsaved")); self.autosave_timer.start(450)
    def logic_options(self):
        """Read declared action names and output IDs from the bundled Python/JS logic."""
        actions, output_ids = set(), set()
        for path in self.work_dir.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in ('.py', '.js', '.mjs'): continue
            try: source = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError): continue
            # Explicit declarations are the dependable, author-controlled option.
            for block in re.findall(r'(?is)SCS_ACTIONS\s*=\s*[\[(](.*?)[\])]', source): actions.update(re.findall(r"['\"]([\w.-]+)['\"]", block))
            for block in re.findall(r'(?is)SCS_OUTPUT_IDS\s*=\s*[\[(](.*?)[\])]', source): output_ids.update(re.findall(r"['\"]([\w.-]+)['\"]", block))
            # Also recognise the normal action comparison and DOM result protocol.
            actions.update(re.findall(r"(?:action\s*==|action\s*===|\.get\(\s*['\"]action['\"]\s*\)\s*==)\s*['\"]([\w.-]+)['\"]", source))
            output_ids.update(re.findall(r"(?:['\"]?(?:selector|target)['\"]?\s*[:=])\s*['\"]#([\w.-]+)['\"]", source))
        return sorted(actions), sorted(output_ids)
    def text(self, ru, en): return ru if self.lang_code == "ru" else en
    def update_language(self, lang_code):
        self.lang_code = lang_code
        self.badge.setText(self.text("ЭТАП 2  •  СТРУКТУРА ПРОЕКТА", "STEP 2  •  PROJECT STRUCTURE"))
        self.hint.setText(self.text("Щёлкните правой кнопкой мыши по дереву, чтобы создать папку, файл или загрузить файл. Изменения сохраняются в ZIP.", "Right-click the tree to create a folder, file or upload a file. Changes are saved to the ZIP."))
        self.tree.setHeaderLabel(self.text("Структура проекта", "Project structure")); self.stage_note.setText(self.text("* Удобно отредактировать HTML Вы сможете на 3 этапе. Здесь рекомендуется создать только скрипты.", "* You will be able to edit HTML conveniently in step 3. It is recommended to create scripts only here.")); self.editor.setPlaceholderText(self.text("Содержимое файла", "File contents")); self.back.setText(self.text("← К этапу 1", "← Back to step 1")); self.save.setText(self.text("Сохранить в ZIP", "Save to ZIP")); self.next.setText(self.text("К этапу 3 →", "To step 3 →"))
        self.project_search.setPlaceholderText(self.text("Поиск по проекту…", "Search project…")); self.format_button.setText(self.text("Форматировать", "Format")); self.docs_button.setText(self.text("Документы", "Docs")); self.test_action_button.setText(self.text("Тест action", "Test action"))
        self.symbols.setToolTip(self.text('Переход к функции или классу в открытом файле', 'Jump to a function or class in the open file')); self.update_symbols()
        self.save_state.setText(self.text("● Сохранено", "● Saved") if getattr(self, 'is_saved', True) else self.text("● Не сохранено", "● Unsaved"))
        if self.current_file is None: self.file_name.setText(self.text("Выберите текстовый файл", "Select a text file"))
    def refresh_tree(self):
        selected = str(self.current_file) if self.current_file else ""; self.tree.clear(); root = QTreeWidgetItem([self.archive_path.stem]); root.setData(0, Qt.UserRole, str(self.work_dir)); self.tree.addTopLevelItem(root)
        def add(parent, folder):
            for path in sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
                relative = path.relative_to(self.work_dir).as_posix(); excluded = relative in self.excluded_files
                item = QTreeWidgetItem([("⊘  " if excluded else "") + path.name]); item.setData(0, Qt.UserRole, str(path)); parent.addChild(item)
                if excluded: item.setForeground(0, QColor('#8993a3'))
                if path.is_dir(): add(item, path)
                if str(path) == selected: self.tree.setCurrentItem(item)
        add(root, self.work_dir); root.setExpanded(True)
    def selected_directory(self):
        item = self.tree.currentItem(); path = Path(item.data(0, Qt.UserRole)) if item else self.work_dir
        return path if path.is_dir() else path.parent
    def show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item: self.tree.setCurrentItem(item)
        menu = QMenu(self)
        file_action = menu.addAction(self.text("Создать файл", "Create file")); folder_action = menu.addAction(self.text("Создать папку", "Create folder")); upload_action = menu.addAction(self.text("Загрузить файл", "Upload file")); rename_action = None; duplicate_action = None; copy_action = None; cut_action = None; paste_action = None; delete_action = None; exclude_action = None
        current = self.tree.currentItem()
        if current and Path(current.data(0, Qt.UserRole)) != self.work_dir:
            menu.addSeparator(); rename_action = menu.addAction(self.text("Переименовать", "Rename")); duplicate_action = menu.addAction(self.text("Дублировать", "Duplicate")); copy_action = menu.addAction(self.text("Копировать", "Copy")); cut_action = menu.addAction(self.text("Вырезать", "Cut")); delete_action = menu.addAction(self.text("Удалить", "Delete"))
            selected_path = Path(current.data(0, Qt.UserRole))
            if selected_path.is_file():
                relative = selected_path.relative_to(self.work_dir).as_posix(); exclude_action = menu.addAction(self.text("Вернуть в итоговый ZIP", "Include in final ZIP") if relative in self.excluded_files else self.text("Не включать в итоговый ZIP", "Exclude from final ZIP"))
        if getattr(self, "file_clipboard", None): paste_action = menu.addAction(self.text("Вставить", "Paste"))
        action = menu.exec_(self.tree.viewport().mapToGlobal(position))
        if action == file_action: self.create_file()
        elif action == folder_action: self.create_folder()
        elif action == upload_action: self.upload_file()
        elif rename_action and action == rename_action: self.rename_item()
        elif duplicate_action and action == duplicate_action: self.duplicate_item()
        elif copy_action and action == copy_action: self.copy_item(False)
        elif cut_action and action == cut_action: self.copy_item(True)
        elif paste_action and action == paste_action: self.paste_item()
        elif delete_action and action == delete_action: self.delete_item()
        elif exclude_action and action == exclude_action: self.toggle_excluded()
    def toggle_excluded(self):
        item = self.tree.currentItem()
        if not item: return
        path = Path(item.data(0, Qt.UserRole))
        if not path.is_file(): return
        relative = path.relative_to(self.work_dir).as_posix()
        if relative in self.excluded_files: self.excluded_files.remove(relative)
        else:
            self.excluded_files.add(relative); saved = self.excluded_store / relative; saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, saved)
        self.exclusions_file.parent.mkdir(parents=True, exist_ok=True); self.exclusions_file.write_text(json.dumps(sorted(self.excluded_files), ensure_ascii=False, indent=2), encoding='utf-8')
        self.is_saved = False; self.save_state.setText(self.text("● Не сохранено", "● Unsaved")); self.refresh_tree()
    def search_project(self, value):
        words = value.casefold().split()
        iterator = QTreeWidgetItemIterator(self.tree)
        first = None
        while iterator.value():
            item = iterator.value(); path = Path(item.data(0, Qt.UserRole) or '')
            haystack = path.name.casefold()
            if path.is_file() and words and path.suffix.lower() in ('.py', '.js', '.mjs', '.html', '.htm', '.css', '.json', '.md', '.txt'):
                try: haystack += '\n' + path.read_text(encoding='utf-8', errors='ignore').casefold()
                except OSError: pass
            matched = not words or all(word in haystack for word in words)
            item.setHidden(bool(words) and not matched and item.parent() is not None)
            if matched and first is None and path.is_file(): first = item
            iterator += 1
        self._search_first_item = first
    def find_next(self):
        value = self.project_search.text().strip()
        if not value: return
        if self.current_file and not self.editor.isReadOnly() and self.editor.find(value): return
        item = getattr(self, '_search_first_item', None)
        if item: self.tree.setCurrentItem(item); self.open_item(item)
    def update_symbols(self):
        self.symbols.blockSignals(True); self.symbols.clear(); self.symbols.addItem(self.text("Функции и классы", "Functions and classes"), -1)
        if self.current_file and not self.editor.isReadOnly():
            source = self.editor.toPlainText(); suffix = self.current_file.suffix.lower()
            pattern = r'^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_$][\w$]*)' if suffix == '.py' else r'^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)'
            for match in re.finditer(pattern, source, re.M): self.symbols.addItem(match.group(1), source[:match.start()].count('\n') + 1)
        self.symbols.blockSignals(False)
    def go_to_symbol(self, index):
        line = self.symbols.itemData(index)
        if not isinstance(line, int) or line < 1: return
        cursor = self.editor.textCursor(); cursor.movePosition(QTextCursor.Start)
        for _ in range(line - 1): cursor.movePosition(QTextCursor.Down)
        self.editor.setTextCursor(cursor); self.editor.centerCursor(); self.editor.setFocus()
    def validate_project(self):
        self.store_current()
        errors = []
        for path in self.work_dir.rglob('*'):
            if not path.is_file(): continue
            try:
                source = path.read_text(encoding='utf-8')
                if path.suffix.lower() == '.py': compile(source, str(path), 'exec')
                elif path.suffix.lower() == '.json': json.loads(source)
                elif path.suffix.lower() in ('.html', '.htm'):
                    ids = re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', source, re.I)
                    duplicates = sorted({value for value in ids if ids.count(value) > 1})
                    if duplicates: errors.append(f'{path.name}: duplicate ID: {", ".join(duplicates)}')
                    if re.search(r'<img\b(?![^>]*\balt\s*=)[^>]*>', source, re.I): errors.append(f'{path.name}: image without alt')
                    if re.search(r'<button\b[^>]*>\s*(?:<[^>]+>\s*)*</button\s*>', source, re.I): errors.append(f'{path.name}: button without text or aria-label')
                    input_ids = re.findall(r'<(?:input|select|textarea)\b[^>]*\bid\s*=\s*["\']([^"\']+)["\'][^>]*>', source, re.I)
                    labels = set(re.findall(r'<label\b[^>]*\bfor\s*=\s*["\']([^"\']+)["\']', source, re.I))
                    unlabeled = [value for value in input_ids if value not in labels and not re.search(r'<(?:input|select|textarea)\b(?=[^>]*\bid\s*=\s*["\']' + re.escape(value) + r'["\'])(?![^>]*\baria-label\s*=)[^>]*>', source, re.I)]
                    if unlabeled: errors.append(f'{path.name}: field without label: {", ".join(unlabeled)}')
                elif path.suffix.lower() in ('.js', '.mjs'):
                    node = shutil.which('node')
                    if node:
                        options = {'capture_output': True, 'text': True, 'timeout': 12}
                        if os.name == 'nt': options['creationflags'] = subprocess.CREATE_NO_WINDOW
                        checked = subprocess.run([node, '--check', str(path)], **options)
                        if checked.returncode: raise SyntaxError((checked.stderr or checked.stdout or 'JavaScript syntax error').strip())
                    else:
                        cleaned = re.sub(r'(?s)/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\x60(?:\\.|\x60\x60|[^\x60\\])*\x60', '', source)
                        pairs = {')': '(', ']': '[', '}': '{'}; stack = []
                        for char in cleaned:
                            if char in '([{': stack.append(char)
                            elif char in pairs and (not stack or stack.pop() != pairs[char]): raise SyntaxError(f'unbalanced {char}')
                        if stack: raise SyntaxError('unclosed bracket')
            except (SyntaxError, ValueError, UnicodeError) as error: errors.append(f'{path.name}: {error}')
        QMessageBox.information(self, self.text("Проверка проекта", "Project validation"), '\n'.join(errors) if errors else self.text("Ошибок не найдено.", "No errors found."))
    def format_current(self):
        if not self.current_file or self.editor.isReadOnly(): return
        source = self.editor.toPlainText(); suffix = self.current_file.suffix.lower()
        try:
            if suffix == '.json': source = json.dumps(json.loads(source), ensure_ascii=False, indent=2) + '\n'
            elif suffix in ('.html', '.htm'): source = format_html(source)
            else: source = '\n'.join(line.rstrip().replace('\t', '    ') for line in source.splitlines()) + ('\n' if source.endswith('\n') else '')
        except (ValueError, TypeError) as error: QMessageBox.warning(self, self.text("Форматирование", "Format"), str(error)); return
        self.editor.setPlainText(source); self.save_project()
    def compare_saved(self):
        if not self.current_file: return
        self.store_current(); relative = self.current_file.relative_to(self.work_dir).as_posix()
        try:
            with zipfile.ZipFile(self.archive_path) as archive: old = archive.read(relative).decode('utf-8', errors='replace').splitlines()
        except (OSError, KeyError, zipfile.BadZipFile): old = []
        new = self.current_file.read_text(encoding='utf-8', errors='replace').splitlines()
        import difflib
        diff = '\n'.join(difflib.unified_diff(old, new, fromfile='saved/' + relative, tofile='current/' + relative, lineterm='')) or self.text("Изменений нет.", "No changes.")
        dialog = QDialog(self); dialog.setWindowTitle(self.text("Сравнение", "Compare")); dialog.resize(820, 560); box = QVBoxLayout(dialog); view = QPlainTextEdit(diff); view.setObjectName('projectEditor'); view.setReadOnly(True); box.addWidget(view); close = QPushButton(self.text("Закрыть", "Close")); close.clicked.connect(dialog.accept); box.addWidget(close, alignment=Qt.AlignRight); dialog.exec_()
    def create_project_docs(self):
        templates = {'README.md': '# Tool name\n\nDescribe the tool, installation and usage here.\n', 'CHANGELOG.md': '# Changelog\n\n## 1.0.0\n\n- Initial release.\n', 'manifest.json': json.dumps({'name': self.archive_path.stem, 'readme': 'README.md', 'socials': {'github': '', 'discord': '', 'website': ''}}, indent=2)}
        created = []
        for name, content in templates.items():
            path = self.work_dir / name
            if not path.exists(): path.write_text(content, encoding='utf-8'); created.append(name)
        self.refresh_tree(); self.save_project(); QMessageBox.information(self, self.text("Документы", "Docs"), self.text("Созданы: ", "Created: ") + (', '.join(created) if created else self.text("ничего — файлы уже существуют", "nothing — files already exist")))
    def execute_test_action(self, action, fields):
        """Run the current project logic through the normal protected backend."""
        self.store_current(); self.save_project()
        try: data = json.loads((self.work_dir / 'info.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError, UnicodeError) as error: return {'status': 'error', 'message': f'info.json: {error}'}
        if not isinstance(data, dict): return {'status': 'error', 'message': 'info.json must contain an object'}
        uid = str(data.get('uid') or hashlib.sha256(str(self.archive_path).encode('utf-8')).hexdigest())
        tool = Tool(uid, self.archive_path, data, None, None)
        self.owner.app.registry.cleanup_tool(tool)
        return self.owner.app.registry.run_logic(tool, json.dumps({'action': action, 'fields': fields}, ensure_ascii=False))
    def test_action(self):
        dialog = QDialog(self); dialog.setWindowTitle(self.text('Тест action', 'Test action')); dialog.resize(620, 510)
        layout = QVBoxLayout(dialog); hint = QLabel(self.text('Вызов логики без открытия HTML-интерфейса.', 'Call tool logic without opening its HTML interface.')); hint.setObjectName('pageSubtitle'); layout.addWidget(hint)
        layout.addWidget(QLabel('Action')); action = QComboBox(); action.setObjectName('input'); action.setEditable(True); available_actions, _output_ids = self.logic_options(); action.addItems(available_actions); layout.addWidget(action)
        layout.addWidget(QLabel(self.text('Поля (JSON-объект)', 'Fields (JSON object)'))); fields = QPlainTextEdit('{}'); fields.setObjectName('projectEditor'); fields.setMaximumHeight(120); layout.addWidget(fields)
        layout.addWidget(QLabel(self.text('Ответ', 'Response'))); response = QPlainTextEdit(); response.setObjectName('projectEditor'); response.setReadOnly(True); layout.addWidget(response, 1)
        buttons = QHBoxLayout(); run = QPushButton(self.text('Запустить', 'Run')); run.setObjectName('primaryButton'); close = QPushButton(self.text('Закрыть', 'Close')); close.setObjectName('outlineButton'); buttons.addStretch(); buttons.addWidget(close); buttons.addWidget(run); layout.addLayout(buttons)
        def invoke():
            try:
                values = json.loads(fields.toPlainText() or '{}')
                if not isinstance(values, dict): raise ValueError(self.text('Поля должны быть JSON-объектом.', 'Fields must be a JSON object.'))
                result = self.execute_test_action(action.currentText().strip(), values); response.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
            except (ValueError, TypeError) as error: response.setPlainText(str(error))
        run.clicked.connect(invoke); close.clicked.connect(dialog.accept); dialog.exec_()
    def rename_item(self):
        item = self.tree.currentItem()
        if not item: return
        path = Path(item.data(0, Qt.UserRole)); name, ok = QInputDialog.getText(self, self.text("Переименовать", "Rename"), self.text("Новое имя:", "New name:"), text=path.name)
        if not ok or not name.strip() or Path(name).name != name.strip(): return
        try: path.rename(path.with_name(name.strip())); self.current_file = None; self.refresh_tree(); self.save_project()
        except OSError as exc: QMessageBox.warning(self, self.text("Ошибка", "Error"), str(exc))
    def duplicate_item(self):
        item = self.tree.currentItem()
        if not item: return
        source = Path(item.data(0, Qt.UserRole)); target = self.unique_copy_path(source.parent, source.name)
        try:
            if source.is_dir(): shutil.copytree(source, target)
            else: shutil.copy2(source, target)
            self.refresh_tree(); self.save_project()
        except OSError as exc: QMessageBox.warning(self, self.text("Ошибка", "Error"), str(exc))
    def unique_copy_path(self, folder, name):
        stem, suffix = Path(name).stem, Path(name).suffix; target = folder / f"{stem}_copy{suffix}"; number = 2
        while target.exists(): target = folder / f"{stem}_copy_{number}{suffix}"; number += 1
        return target
    def copy_item(self, cut):
        item = self.tree.currentItem()
        if item: self.file_clipboard = (Path(item.data(0, Qt.UserRole)), cut)
    def paste_item(self):
        if not getattr(self, "file_clipboard", None): return
        source, cut = self.file_clipboard; target_dir = self.selected_directory()
        if not source.exists() or source == target_dir or (source.is_dir() and source in target_dir.parents): return
        target = target_dir / source.name if cut else self.unique_copy_path(target_dir, source.name)
        try:
            if cut: shutil.move(str(source), str(target)); self.file_clipboard = None
            elif source.is_dir(): shutil.copytree(source, target)
            else: shutil.copy2(source, target)
            self.current_file = None; self.refresh_tree(); self.save_project()
        except OSError as exc: QMessageBox.warning(self, self.text("Ошибка", "Error"), str(exc))
    def delete_item(self):
        item = self.tree.currentItem()
        if not item: return
        path = Path(item.data(0, Qt.UserRole))
        if path == self.work_dir: return
        answer = QMessageBox.question(self, self.text("Удалить", "Delete"), self.text(f"Удалить {path.name}?", f"Delete {path.name}?"), QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes: return
        try:
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
            self.current_file = None; self.editor.clear(); self.editor.setReadOnly(True); self.refresh_tree(); self.save_project()
        except OSError as exc: QMessageBox.warning(self, self.text("Ошибка", "Error"), str(exc))
    def store_current(self):
        if self.current_file and not self.editor.isReadOnly(): self.current_file.write_text(self.editor.toPlainText(), encoding="utf-8")
    def open_item(self, item, _column=0):
        self.store_current(); path = Path(item.data(0, Qt.UserRole))
        if path.is_dir(): self.current_file = None; self.editor.clear(); self.editor.setReadOnly(True); self.content_stack.setCurrentWidget(self.editor); self.file_name.setText(self.text("Папка: ", "Folder: ") + path.name); return
        self.current_file = path; self.file_name.setText(path.name)
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"):
            pixmap = QPixmap(str(path)); self.content_stack.setCurrentWidget(self.image_preview)
            if pixmap.isNull(): self.image_preview.setText(self.text("Не удалось прочитать изображение.", "Unable to read the image."))
            else: self.image_preview.setPixmap(pixmap.scaled(680, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.editor.setReadOnly(True); return
        self.content_stack.setCurrentWidget(self.editor); self.highlighter.set_mode(path.name)
        try: self.editor.setPlainText(path.read_text(encoding="utf-8")); self.editor.setReadOnly(False)
        except UnicodeDecodeError: self.editor.setPlainText(self.text("Двоичный файл нельзя редактировать как текст.", "A binary file cannot be edited as text.")); self.editor.setReadOnly(True)
        except OSError as exc: self.editor.setPlainText(str(exc)); self.editor.setReadOnly(True)
        self.update_symbols()
    def create_file(self):
        name, ok = QInputDialog.getText(self, self.text("Создать файл", "Create file"), self.text("Имя файла:", "File name:"))
        if not ok or not name.strip() or Path(name).name != name.strip(): return
        path = self.selected_directory() / name.strip()
        if path.exists(): QMessageBox.warning(self, self.text("Ошибка", "Error"), self.text("Файл уже существует.", "The file already exists.")); return
        path.touch(); self.refresh_tree(); self.save_project()
    def create_folder(self):
        name, ok = QInputDialog.getText(self, self.text("Создать папку", "Create folder"), self.text("Имя папки:", "Folder name:"))
        if not ok or not name.strip() or Path(name).name != name.strip(): return
        path = self.selected_directory() / name.strip()
        if path.exists(): QMessageBox.warning(self, self.text("Ошибка", "Error"), self.text("Папка уже существует.", "The folder already exists.")); return
        path.mkdir(); self.refresh_tree(); self.save_project()
    def upload_file(self):
        source, _ = QFileDialog.getOpenFileName(self, self.text("Загрузить файл", "Upload file"))
        if not source: return
        target = self.selected_directory() / Path(source).name
        if target.exists() and QMessageBox.question(self, self.text('Заменить файл?', 'Replace file?'), self.text(f'Файл {target.name} уже есть в проекте. Заменить его?', f'{target.name} already exists in the project. Replace it?'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        try: shutil.copy2(source, target)
        except OSError as exc: QMessageBox.warning(self, self.text("Ошибка", "Error"), str(exc)); return
        self.refresh_tree(); self.save_project()
    def save_project(self):
        try:
            self.store_current()
            with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in self.work_dir.rglob("*"):
                    relative = path.relative_to(self.work_dir).as_posix()
                    if path.is_file() and path.name != 'html_history.json' and relative not in self.excluded_files: archive.write(path, relative)
            self.exclusions_file.parent.mkdir(parents=True, exist_ok=True); self.exclusions_file.write_text(json.dumps(sorted(self.excluded_files), ensure_ascii=False, indent=2), encoding='utf-8')
            self.refresh_tree(); self.owner.persist_workspace(self.archive_path, self.work_dir, 2); self.set_saved()
        except OSError as exc: QMessageBox.warning(self, self.text("Ошибка", "Error"), str(exc))
    def open_html_editor(self):
        self.save_project(); self.owner.open_html_editor(self.archive_path)
    def cleanup(self):
        self.save_project(); shutil.rmtree(self.work_dir, ignore_errors=True)


class HtmlEditorPanel(QWidget):
    logic_finished = pyqtSignal(str)

    def __init__(self, archive_path, parent):
        super().__init__(parent); self.setObjectName("authoringEditor"); self.owner = parent; self.archive_path = Path(archive_path); self.lang_code = parent.app.lang["_lang_code"]; self.work_dir = Path(tempfile.mkdtemp(prefix="scs_html_editor_")); self.preview_timer = QTimer(self); self.preview_timer.setSingleShot(True); self.preview_timer.timeout.connect(self.refresh_preview)
        with zipfile.ZipFile(self.archive_path) as archive: archive.extractall(self.work_dir)
        for legacy_history in self.work_dir.rglob('html_history.json'):
            legacy_history.unlink(missing_ok=True)
        self.html_files = sorted(path for path in self.work_dir.rglob("*") if path.is_file() and path.suffix.lower() in (".html", ".htm"))
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        card = QFrame(); card.setObjectName("card"); card_layout = QVBoxLayout(card); layout.addWidget(card, 1)
        self.heading = QLabel(); self.heading.setObjectName("toolName"); self.editor_help = QToolButton(); self.editor_help.setObjectName("editorHelp"); self.editor_help.setText("i"); self.editor_help.setToolTip("Editor guide"); self.editor_help.setFixedSize(30, 30); self.editor_help.clicked.connect(self.show_editor_help)
        header = QHBoxLayout(); header.setContentsMargins(0, 0, 0, 0); header.addWidget(self.heading); header.addStretch(); header.addWidget(self.editor_help)
        self.hint = QLabel(); self.hint.setObjectName("pageSubtitle"); self.hint.setWordWrap(True); card_layout.addLayout(header); card_layout.addWidget(self.hint)
        if len(self.html_files) != 1:
            self.message = QLabel(); self.message.setObjectName("emptyTitle"); self.message.setAlignment(Qt.AlignCenter); self.message.setWordWrap(True); card_layout.addWidget(self.message, 1); self.update_language(self.lang_code); return
        self.file_path = self.html_files[0]; self.visual_mode = False; self.source = QPlainTextEdit(); self.source.setObjectName("projectEditor"); self.source.installEventFilter(self); self.source.setPlainText(self.file_path.read_text(encoding="utf-8")); self.highlighter = CodeHighlighter(self.source.document()); self.highlighter.set_mode(self.file_path.name); self.source.textChanged.connect(lambda: self.preview_timer.start(550)); self.autosave_timer = QTimer(self); self.autosave_timer.setSingleShot(True); self.autosave_timer.timeout.connect(self.save_html); self.source.textChanged.connect(self.mark_unsaved)
        self.undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self); self.undo_shortcut.setContext(Qt.ApplicationShortcut); self.undo_shortcut.activated.connect(self.undo_source)
        self.redo_shortcut = QShortcut(QKeySequence('Ctrl+Shift+Z'), self); self.redo_shortcut.setContext(Qt.ApplicationShortcut); self.redo_shortcut.activated.connect(self.redo_source)
        # Remove any legacy preview-selection marker before a saved document is
        # displayed. Selection belongs to the editor session, never to the page.
        initial_html = self.source.toPlainText()
        self.source.setPlainText(format_html(inject_tool_bridge(initial_html if initial_html.strip() else basic_document(self.lang_code))))
        self.source_history = [self.source.toPlainText()]; self.source_history_index = 0; self.restoring_source_history = False
        if hasattr(self.owner, 'load_authoring_history'):
            self.source_history, self.source_history_index = self.owner.load_authoring_history(self.archive_path, self.source.toPlainText())
        self.history_timer = QTimer(self); self.history_timer.setSingleShot(True); self.history_timer.timeout.connect(self.persist_source_history)
        self.source.textChanged.connect(self.schedule_source_history)
        self.source.setTabStopDistance(self.source.fontMetrics().horizontalAdvance(' ') * 4)
        self.preview_theme_override = None
        self.preview = QWebEngineView(); self.preview_channel = QWebChannel(self.preview.page()); self.preview_bridge = EditorPreviewBridge(); self.preview_channel.registerObject("toolBridge", self.preview_bridge); self.preview.page().setWebChannel(self.preview_channel); self.preview.loadFinished.connect(lambda _ok: self.apply_preview_theme()); self.logic_running = False; self.logic_finished.connect(self.render_logic_result)
        splitter = QSplitter(Qt.Horizontal); self.authoring = AuthoringWorkspace(self, CodeHighlighter); splitter.addWidget(self.authoring); splitter.addWidget(self.preview); splitter.setSizes([520, 520]); card_layout.addWidget(splitter, 1); self.save_state = QLabel(); self.save_state.setObjectName("pageSubtitle"); card_layout.addWidget(self.save_state)
        self.copy_style_shortcut = QShortcut(QKeySequence('Ctrl+C'), self.preview); self.copy_style_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.copy_style_shortcut.activated.connect(self.authoring.copy_editor_selection)
        self.paste_element_shortcut = QShortcut(QKeySequence('Ctrl+V'), self.preview); self.paste_element_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.paste_element_shortcut.activated.connect(self.authoring.paste_copied_element)
        self.duplicate_element_shortcut = QShortcut(QKeySequence('Ctrl+D'), self.preview); self.duplicate_element_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.duplicate_element_shortcut.activated.connect(self.authoring.duplicate_selected)
        self.delete_element_shortcut = QShortcut(QKeySequence('Delete'), self.preview); self.delete_element_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.delete_element_shortcut.activated.connect(self.delete_selected_element)
        self.paste_style_shortcut = QShortcut(QKeySequence('Alt+V'), self.preview); self.paste_style_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.paste_style_shortcut.activated.connect(self.authoring.paste_selected_styles)
        self.save_shortcut = QShortcut(QKeySequence('Ctrl+S'), self); self.save_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.save_shortcut.activated.connect(self.save_html)
        self.find_shortcut = QShortcut(QKeySequence('Ctrl+F'), self); self.find_shortcut.setContext(Qt.WidgetWithChildrenShortcut); self.find_shortcut.activated.connect(self.find_text)
        controls = QHBoxLayout(); self.back = QPushButton(); self.back.setObjectName("outlineButton"); self.back.clicked.connect(self.owner.close_html_editor); self.undo = QPushButton("↶"); self.undo.setObjectName("historyButton"); self.undo.setToolTip(self.text("Отменить", "Undo")); self.undo.clicked.connect(self.undo_source); self.redo = QPushButton("↷"); self.redo.setObjectName("historyButton"); self.redo.setToolTip(self.text("Повторить", "Redo")); self.redo.clicked.connect(self.redo_source); self.find = QPushButton(); self.find.setObjectName("outlineButton"); self.find.clicked.connect(self.find_text); self.create_element = QPushButton(); self.create_element.setObjectName("outlineButton"); self.create_menu = QMenu(self.create_element); self.create_menu.setStyleSheet("QMenu { menu-scrollable: 1; }"); self.create_element.clicked.connect(lambda: self.create_menu.popup(self.create_element.mapToGlobal(self.create_element.rect().bottomLeft()))); self.delete_element = QPushButton(); self.delete_element.setObjectName("dangerButton"); self.delete_element.clicked.connect(self.delete_selected_element); self.bridge = QPushButton(); self.bridge.setObjectName("outlineButton"); self.bridge.clicked.connect(self.enable_bridge); self.visual = QPushButton(); self.visual.setObjectName("outlineButton"); self.visual.clicked.connect(self.toggle_visual_edit); self.apply_visual = QPushButton(); self.apply_visual.setObjectName("outlineButton"); self.apply_visual.clicked.connect(self.apply_visual_changes); self.full = QPushButton(); self.full.setObjectName("outlineButton"); self.full.clicked.connect(self.toggle_fullscreen); self.save = QPushButton(); self.save.setObjectName("primaryButton"); self.save.clicked.connect(self.save_html); controls.addWidget(self.back); controls.addWidget(self.undo); controls.addWidget(self.redo); controls.addWidget(self.find); controls.addWidget(self.create_element); controls.addWidget(self.delete_element); controls.addWidget(self.bridge); controls.addWidget(self.visual); controls.addWidget(self.apply_visual); controls.addStretch(); controls.addWidget(self.full); controls.addWidget(self.save); card_layout.addLayout(controls); self.update_language(self.lang_code); self.update_theme(); self.refresh_preview()
    def logic_options(self):
        actions, output_ids = set(), set()
        try:
            runtime = json.loads((self.work_dir / 'info.json').read_text(encoding='utf-8')).get('runtime', {})
            script_name = runtime.get('logic') if runtime.get('type') == 'html_python' else runtime.get('script') if runtime.get('type') == 'html_script' else None
            path = self.work_dir / script_name if isinstance(script_name, str) else None
            expected = '.py' if runtime.get('type') == 'html_python' else '.js' if runtime.get('type') == 'html_script' else ''
            if path is None or not path.is_file() or path.suffix.lower() not in (expected, '.mjs' if expected == '.js' else expected): return [], []
            source = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError, ValueError, TypeError): return [], []
        for block in re.findall(r'(?is)SCS_ACTIONS\s*=\s*[\[(](.*?)[\])]', source): actions.update(re.findall(r"['\"]([\w.-]+)['\"]", block))
        for block in re.findall(r'(?is)SCS_OUTPUT_IDS\s*=\s*[\[(](.*?)[\])]', source): output_ids.update(re.findall(r"['\"]([\w.-]+)['\"]", block))
        actions.update(re.findall(r"(?:action\s*==|action\s*===|\.get\(\s*['\"]action['\"]\s*\)\s*==)\s*['\"]([\w.-]+)['\"]", source))
        output_ids.update(re.findall(r"(?:['\"]?(?:selector|target)['\"]?\s*[:=])\s*['\"]#([\w.-]+)['\"]", source))
        # Existing HTML bindings are valid choices too.  In particular, a
        # custom renderTool implementation may update DOM IDs without naming
        # them in the Python response protocol.
        html = self.source.toPlainText()
        actions.update(re.findall(r'\bdata-scs-action\s*=\s*["\']([\w.-]+)["\']', html, re.I))
        for tag in re.findall(r'<[A-Za-z][^>]*>', html, re.I):
            match = re.search(r'\bid\s*=\s*["\']([\w.-]+)["\']', tag, re.I)
            if match and not match.group(1).startswith('scs-language-') and not re.search(r'\bdata-scs-action\s*=', tag, re.I): output_ids.add(match.group(1))
        return sorted(actions), sorted(output_ids)
    def commit_source_history(self):
        if self.restoring_source_history: return
        value = self.source.toPlainText()
        if value == self.source_history[self.source_history_index]: return
        del self.source_history[self.source_history_index + 1:]
        self.source_history.append(value); self.source_history_index += 1
        self.trim_source_history()
    def trim_source_history(self):
        if len(self.source_history) <= 100: return
        extra = len(self.source_history) - 100
        self.source_history = self.source_history[extra:]; self.source_history_index = max(0, self.source_history_index - extra)
    def schedule_source_history(self):
        if not getattr(self, 'restoring_source_history', False): self.history_timer.start(500)
    def persist_source_history(self):
        self.commit_source_history()
        if hasattr(self.owner, 'save_authoring_history'):
            self.owner.save_authoring_history(self.archive_path, self.source_history, self.source_history_index)
    def replace_source(self, value):
        if value == self.source.toPlainText(): return
        self.commit_source_history(); del self.source_history[self.source_history_index + 1:]
        self.source_history.append(value); self.source_history_index += 1; self.trim_source_history()
        self.restoring_source_history = True
        try: self.source.setPlainText(value)
        finally: self.restoring_source_history = False
        self.persist_source_history()
    def undo_source(self):
        self.commit_source_history()
        if self.source_history_index <= 0: return
        self.source_history_index -= 1; self.restoring_source_history = True
        try: self.source.setPlainText(self.source_history[self.source_history_index])
        finally: self.restoring_source_history = False
        self.persist_source_history()
    def redo_source(self):
        self.commit_source_history()
        if self.source_history_index >= len(self.source_history) - 1: return
        self.source_history_index += 1; self.restoring_source_history = True
        try: self.source.setPlainText(self.source_history[self.source_history_index])
        finally: self.restoring_source_history = False
        self.persist_source_history()
    def eventFilter(self, watched, event):
        if watched is getattr(self, 'source', None) and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            if event.modifiers() & Qt.ShiftModifier: self.redo_source()
            else: self.undo_source()
            return True
        return super().eventFilter(watched, event)
    def text(self, ru, en): return ru if self.lang_code == "ru" else en
    def update_theme(self, _theme=None):
        # The source/preview split has its own local stylesheet, unlike the
        # rest of the application.  Refresh it after a theme switch.
        if hasattr(self, 'authoring'): self.authoring.update_theme()
        # The right-hand HTML preview is a real tool surface, so it must
        # receive the same theme callback as opened tools do.
        if hasattr(self, 'preview'):
            app = getattr(self.owner, 'app', None)
            self.preview_theme_override = getattr(app, 'settings', {}).get('theme', 'dark') if app else 'dark'
            self.apply_preview_theme()
        if hasattr(self, 'editor_help'):
            theme = getattr(getattr(self.owner, 'app', None), 'settings', {}).get('theme', 'dark')
            colors = {
                'light': ('#ffffff', '#202733', '#cbd5e1', '#e9eef4'), 'ember': ('#281c19', '#ffe4d9', '#614139', '#38241f'),
                'ocean': ('#142b3d', '#e4f8ff', '#315770', '#1c3b51'), 'lavender': ('#ffffff', '#342c4a', '#d9d0e9', '#f0ecf8'),
                'fire': ('#351a16', '#ffe4d9', '#71342b', '#49231c'), 'spectrum_dark': ('#222450', '#f0efff', '#4c4ca1', '#30346d'),
                'spectrum_light': ('#ffffff', '#342c4a', '#c3c4e9', '#f0ecfa'),
            }.get(theme, ('#1d2129', '#f4f6f8', '#394150', '#262d38'))
            self.editor_help.setStyleSheet(f'QToolButton {{ background:{colors[0]}; color:{colors[1]}; border:1px solid {colors[2]}; border-radius:15px; font-weight:700; }} QToolButton:hover {{ background:{colors[3]}; }}')

    def show_editor_help(self):
        ru = self.lang_code == 'ru'
        dialog = QDialog(self); dialog.setObjectName('editorHelpDialog'); dialog.setWindowTitle('Инструкция по редактору' if ru else 'Editor guide'); dialog.resize(720, 610)
        theme = getattr(getattr(self.owner, 'app', None), 'settings', {}).get('theme', 'dark')
        palette = {
            'light': ('#f8fafc', '#202733', '#ffffff', '#cbd5e1', '#b64b23'), 'ember': ('#211816', '#ffe4d9', '#17110f', '#5a3a32', '#ff9a70'),
            'ocean': ('#102233', '#e4f8ff', '#0c1724', '#315770', '#67d6f5'), 'lavender': ('#f8f5fd', '#342c4a', '#ffffff', '#d9d0e9', '#7d5bd6'),
            'fire': ('#281513', '#ffe4d9', '#1b0f0d', '#71342b', '#ff8d69'), 'spectrum_dark': ('#181b38', '#f0efff', '#101126', '#4c4ca1', '#ee8ddd'),
            'spectrum_light': ('#f3fbff', '#342c4a', '#ffffff', '#c3c4e9', '#c441a7'),
        }.get(theme, ('#191d25', '#d8e0ea', '#11141a', '#384150', '#ff8b5a'))
        dialog.setStyleSheet(f'QDialog {{ background:{palette[0]}; color:{palette[1]}; }} QLabel, QTextBrowser {{ color:{palette[1]}; }} QTextBrowser {{ background:{palette[2]}; border:1px solid {palette[3]}; border-radius:12px; padding:12px; }} QTextBrowser h3 {{ color:{palette[4]}; }} QPushButton {{ background:{palette[2]}; color:{palette[1]}; border:1px solid {palette[3]}; border-radius:8px; padding:7px 14px; }}')
        layout = QVBoxLayout(dialog); browser = QTextBrowser(); browser.setOpenExternalLinks(False)
        browser.setHtml(('''<h3>HTML-редактор</h3>
<p><b>HTML</b> — редактирование структуры страницы. Справа отображается живой результат; щелчок по элементу выбирает его.</p>
<p><b>CSS / JS</b> — создание и редактирование встроенных блоков стилей и JavaScript.</p>
<p><b>Классы и стили</b> — выбор CSS-селектора, быстрые параметры и полный список CSS-свойств.</p>
<p><b>Actions</b> — привязка выбранного элемента к Python или JavaScript-логике. Action Bridge передаёт <code>data-scs-action</code> и значения полей с атрибутом <code>name</code>. В разделе «Переводы» выберите язык, исходный и новый текст — редактор сам создаст привязку <code>data-scs-i18n</code>. В разделе «Тема» выберите тему и элемент, класс или тип элемента. Density Bridge задаёт процент масштаба и синхронизирует его с плотностью интерфейса менеджера.</p>
<p><b>Нижняя панель:</b> создавайте элементы, ищите текст, включайте визуальное редактирование, применяйте изменения и сохраняйте ZIP. Ctrl+Z и Ctrl+Shift+Z отменяют и повторяют изменения исходного кода.</p>
<p>Мосты добавляются только один раз и всегда помещаются в <code>&lt;body&gt;</code>.</p>''' if ru else '''<h3>HTML editor</h3>
<p><b>HTML</b> edits the page structure. The live preview is on the right; click an element to select it.</p>
<p><b>CSS / JS</b> creates and edits inline style and JavaScript blocks.</p>
<p><b>Classes and styles</b> lets you select a CSS selector, edit quick properties, and access the complete CSS property catalogue.</p>
<p><b>Actions</b> binds the selected element to Python or JavaScript logic. Action Bridge sends <code>data-scs-action</code> and values from fields with a <code>name</code> attribute. In «Translations», choose the language, source text, and replacement text. In «Theme», choose a theme and an element, class, or element type. Density Bridge sets an author scale percentage and synchronizes it with manager interface density.</p>
<p><b>Bottom toolbar:</b> create elements, find text, enable visual editing, apply changes, and save the ZIP. Ctrl+Z and Ctrl+Shift+Z undo and redo source changes.</p>
<p>Every bridge is inserted only once and always inside <code>&lt;body&gt;</code>.</p>'''))
        browser.append(('<p><b>Logging Bridge:</b> После нажатия кнопки в Python-скрипт добавляется импорт <code>from tool_logging_bridge import logger</code>. Используйте <code>logger.error("ERROR")</code>, <code>logger.warning(...)</code>, <code>logger.info(...)</code> или <code>logger.debug(...)</code>. Сообщения появятся в журнале менеджера и не нарушат JSON-ответ действия.</p>' if ru else '<p><b>Logging Bridge:</b> The button adds <code>from tool_logging_bridge import logger</code> to the Python script. Use <code>logger.error("ERROR")</code>, <code>logger.warning(...)</code>, <code>logger.info(...)</code>, or <code>logger.debug(...)</code>. Messages appear in the manager log without changing the action\'s JSON response.</p>'))
        layout.addWidget(browser, 1); close = QPushButton('Закрыть' if ru else 'Close'); close.clicked.connect(dialog.accept); layout.addWidget(close)
        dialog.exec_()
    def update_language(self, code):
        self.lang_code = code
        if hasattr(self, 'authoring'): self.authoring.translate()
        if hasattr(self, 'back') and not hasattr(self, 'secondary_controls'):
            controls = self.back.parentWidget().layout().itemAt(self.back.parentWidget().layout().count()-1).layout()
            self.secondary_controls = QHBoxLayout()
            for button in (self.undo, self.redo, self.find, self.create_element, self.delete_element):
                controls.removeWidget(button); self.secondary_controls.addWidget(button)
            self.back.parentWidget().layout().insertLayout(2, self.secondary_controls)
            for button in (self.visual, self.apply_visual):
                button.hide()
        self.lang_code = code; self.heading.setText(self.text("HTML редактор", "HTML editor")); self.hint.setText(self.text("Редактор кода слева, живой визуальный результат справа.", "Source editor on the left, live visual result on the right."))
        if len(self.html_files) != 1:
            self.message.setText(self.text("Редактирование нескольких HTML файлов невозможно." if self.html_files else "HTML-файл в проекте не найден.", "Editing multiple HTML files is not possible." if self.html_files else "No HTML file was found in the project.")); return
        self.back.setText(self.text("← К этапу 2", "← Back to step 2")); self.undo.setToolTip(self.text("Отменить", "Undo")); self.redo.setToolTip(self.text("Повторить", "Redo")); self.find.setText(self.text("Найти", "Find")); self.create_element.setText(self.text("+ Создать элемент", "+ Create element")); self.delete_element.setText(self.text("Удалить элемент", "Delete element")); self.bridge.setText(self.text("Добавить связь со скриптом", "Enable script bridge")); self.visual.setText(self.text("Визуальное редактирование" if not self.visual_mode else "Завершить визуальное редактирование", "Visual editing" if not self.visual_mode else "Finish visual editing")); self.apply_visual.setText(self.text("Применить визуальные изменения", "Apply visual changes")); self.full.setText(self.text("Полный экран", "Full screen")); self.save.setText(self.text("Сохранить в ZIP", "Save to ZIP")); self.save_state.setText(self.text("● Сохранено", "● Saved") if getattr(self, 'is_saved', True) else self.text("● Не сохранено", "● Unsaved")); self.build_element_menu()
        self.bridge.setText(self.text('Тест', 'Test'))
        if not hasattr(self, 'test_connected'):
            self.bridge.clicked.connect(self.test_tool); self.test_connected = True
    def refresh_preview(self):
        if len(self.html_files) != 1: return
        if not self.save_state.text(): self.set_saved()
        self.file_path.write_text(self.source.toPlainText(), encoding="utf-8"); self.preview.load(QUrl.fromLocalFile(str(self.file_path)))
    def apply_preview_theme(self, theme=None):
        """Keep the authoring preview on the selected manager/theme-editor variant."""
        if not hasattr(self, 'preview'): return
        if theme:
            self.preview_theme_override = theme
        app = getattr(self.owner, 'app', None)
        fallback = getattr(app, 'settings', {}).get('theme', 'dark') if app else 'dark'
        selected = self.preview_theme_override or fallback
        payload = json.dumps(selected)
        self.preview.page().runJavaScript(
            f"if (window.setManagerTheme) window.setManagerTheme({payload});"
            f"else {{ document.documentElement.dataset.scsTheme={payload}; "
            f"document.documentElement.style.colorScheme=['light','lavender','spectrum_light'].includes({payload})?'light':'dark'; }}"
        )
    def build_element_menu(self):
        self.create_menu.clear()
        elements = [
            ("button", self.text("Кнопка действия", "Action button"), '<button data-scs-action="action_name">Button</button>'),
            ("input", self.text("Поле ввода", "Text input"), '<input name="value" type="text" placeholder="Enter value">'),
            ("select", self.text("Список выбора", "Select list"), '<select name="choice">\n  <option value="one">Option one</option>\n  <option value="two">Option two</option>\n</select>'),
            ("textarea", self.text("Многострочное поле", "Text area"), '<textarea name="message" placeholder="Enter text"></textarea>'),
            ("heading", self.text("Заголовок", "Heading"), '<h2>Heading</h2>'),
            ("paragraph", self.text("Текст", "Paragraph"), '<p>Text</p>'),
            ("span", self.text("Строчный текст", "Inline text"), '<span>Inline text</span>'),
            ("container", self.text("Контейнер", "Container"), '<div class="container">\n  Content\n</div>'),
            ("link", self.text("Ссылка", "Link"), '<a href="https://example.com">Link</a>'),
            ("image", self.text("Изображение", "Image"), '<img src="image.png" alt="Description">'),
            ("divider", self.text("Разделитель", "Divider"), '<hr>'),
        ]
        for _, title, snippet in elements:
            action = self.create_menu.addAction(title); action.triggered.connect(lambda _=False, value=snippet: self.insert_snippet(value))
        components = self.create_menu.addMenu(self.text('Компоненты', 'Components'))
        component_items = [
            (self.text('Карточка', 'Card'), '<section class="card"><h2>Card title</h2><p>Card content</p></section>'),
            (self.text('Форма', 'Form'), '<form class="form"><label>Name<input name="name" type="text" autocomplete="name"></label><button type="button" data-scs-action="submit">Submit</button></form>'),
            (self.text('Вкладки', 'Tabs'), '<section class="tabs"><nav aria-label="Tabs"><button type="button">Tab 1</button><button type="button">Tab 2</button></nav><div class="tab-content">Tab content</div></section>'),
            (self.text('Модальное окно', 'Modal'), '<dialog class="modal"><h2>Dialog title</h2><p>Dialog content</p><button type="button">Close</button></dialog>'),
            (self.text('Уведомление', 'Notification'), '<aside class="notification" role="status"><strong>Notification</strong><span>Message</span></aside>'),
        ]
        for title, snippet in component_items:
            action = components.addAction(title); action.triggered.connect(lambda _=False, value=snippet: self.insert_snippet(value))
    def insert_snippet(self, snippet):
        script = '''(() => {const selected=window.__scsSelectedSelector&&document.querySelector(window.__scsSelectedSelector);
          const parent=selected||document.body; if(!parent)return null; const template=document.createElement('template'); template.innerHTML=%s;
          const element=Array.from(template.content.children)[0]; if(!element)return null;
          const bridgeMarker=parent===document.body&&Array.from(parent.childNodes).find(node=>node.nodeType===Node.COMMENT_NODE&&['ACTIONS_BRIDGE','SCS_TOOL_BRIDGE'].includes(node.nodeValue.trim()));
          const firstScript=parent===document.body&&Array.from(parent.children).find(node=>node.tagName==='SCRIPT');
          parent.insertBefore(element, bridgeMarker||firstScript||null);
          let node=element, parts=[]; while(node&&node.nodeType===1){let index=1,previous=node.previousElementSibling;while(previous){if(previous.tagName===node.tagName)index++;previous=previous.previousElementSibling;}parts.unshift(node.tagName.toLowerCase()+':nth-of-type('+index+')');node=node.parentElement;}
          const selector=parts.join(' > ');window.__scsSelectedSelector=selector;window.__scsSelectedSelectors=[selector];return {selector,html:'<!doctype html>\\n'+document.documentElement.outerHTML};})()''' % json.dumps(snippet)
        def done(result):
            if not result: return
            self.authoring.last_selection=result['selector']; self.authoring.pending_highlight=True; self.replace_source(format_html(result['html']))
            self.authoring.tabs.setCurrentIndex(0); self.source.setFocus()
        self.preview.page().runJavaScript(script, done)
    def delete_selected_element(self):
        script = '''(() => {const selectors=window.__scsSelectedSelectors||[window.__scsSelectedSelector],locks=window.__scsEditorLocks||{};let changed=0;for(const selector of [...selectors].reverse()){const e=selector&&document.querySelector(selector);if(!e||e===document.body||e===document.documentElement||locks[selector]==='all')continue;e.remove();changed++;}window.__scsSelectedSelector=null;window.__scsSelectedSelectors=[];return changed?'<!doctype html>\\n'+document.documentElement.outerHTML:null;})()'''
        def done(html):
            if html: self.replace_source(format_html(html))
        self.preview.page().runJavaScript(script, done)
    def find_text(self):
        value, ok = QInputDialog.getText(self, self.text("Найти", "Find"), self.text("Текст для поиска:", "Text to find:"))
        if value and ok and not self.source.find(value):
            cursor = self.source.textCursor(); cursor.movePosition(cursor.Start); self.source.setTextCursor(cursor); self.source.find(value)
    def toggle_visual_edit(self):
        self.visual_mode = not self.visual_mode
        mode = "on" if self.visual_mode else "off"
        self.preview.page().runJavaScript(f"document.designMode = '{mode}';")
        self.update_language(self.lang_code)
    def apply_visual_changes(self):
        def receive(html):
            if html: self.replace_source(format_html(html)); self.visual_mode = False; self.update_language(self.lang_code)
        self.preview.page().toHtml(receive)
    def enable_bridge(self):
        self.replace_source(inject_tool_bridge(self.source.toPlainText()))
    def enable_language_bridge(self):
        self.replace_source(inject_language_bridge(self.source.toPlainText()))
    def enable_theme_bridge(self):
        self.replace_source(inject_theme_bridge(self.source.toPlainText()))
    def enable_density_bridge(self, percent=100):
        self.replace_source(inject_density_bridge(self.source.toPlainText(), percent))
    def enable_logging_bridge(self):
        self.replace_source(inject_logging_bridge(inject_tool_bridge(self.source.toPlainText())))
        try:
            runtime = json.loads((self.work_dir / 'info.json').read_text(encoding='utf-8')).get('runtime', {})
            if runtime.get('type') != 'html_python': return
            relative = Path(str(runtime.get('logic', '')))
            if relative.is_absolute() or '..' in relative.parts or relative.suffix.lower() != '.py': return
            logic = self.work_dir / relative
            if not logic.is_file(): return
            helper = logic.parent / 'tool_logging_bridge.py'
            if not helper.exists(): helper.write_text(PYTHON_LOGGING_BRIDGE, encoding='utf-8')
            original = logic.read_text(encoding='utf-8')
            updated = inject_python_logging_import(original)
            if updated != original: logic.write_text(updated, encoding='utf-8')
            self.save_html()
        except (OSError, UnicodeError, ValueError) as error:
            QMessageBox.warning(self, self.text('Ошибка моста', 'Bridge error'), str(error))
    def set_saved(self): self.is_saved = True; self.save_state.setText(self.text("● Сохранено", "● Saved"))
    def mark_unsaved(self):
        if hasattr(self, 'save_state'):
            self.is_saved = False; self.save_state.setText(self.text("● Не сохранено", "● Unsaved")); self.autosave_timer.start(650)
    def save_html(self):
        self.persist_source_history()
        self.file_path.write_text(self.source.toPlainText(), encoding="utf-8")
        with zipfile.ZipFile(self.archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in self.work_dir.rglob("*"):
                if path.is_file() and path.name != 'html_history.json': archive.write(path, path.relative_to(self.work_dir).as_posix())
        if hasattr(self.owner, 'persist_workspace'): self.owner.persist_workspace(self.archive_path, self.work_dir, 3)
        self.set_saved()
    def run_logic(self, payload=""):
        """Run the edited tool through its real runtime inside the preview."""
        if self.logic_running or not hasattr(self.owner.app, 'registry'): return
        tool = next((item for item in self.owner.app.registry.scan() if Path(item.archive) == self.archive_path), None)
        if not tool: return
        self.logic_running = True
        def execute():
            data = json.dumps(self.owner.app.registry.run_logic(tool, payload), ensure_ascii=False)
            try: self.logic_finished.emit(data)
            except RuntimeError: pass
        threading.Thread(target=execute, daemon=True).start()
    def render_logic_result(self, data):
        self.logic_running = False
        if hasattr(self, 'preview'):
            self.preview.page().runJavaScript(f"if (window.renderTool) window.renderTool({data});")
    def test_tool(self):
        """Save the current editor state and launch this archive through the normal runtime."""
        self.save_html()
        tool = next((item for item in self.owner.app.registry.scan() if Path(item.archive) == self.archive_path), None)
        if tool:
            running = self.owner.app.pages["running"]
            opened = next((running.tool_pages.widget(index) for index in range(running.tool_pages.count())
                           if running.tool_pages.widget(index).tool.uid == tool.uid), None)
            if opened:
                running.reload_tool(opened, tool)
                self.owner.app.show_page("running")
            else:
                self.owner.app.open_tool(tool)
        else: QMessageBox.warning(self, self.text('Ошибка', 'Error'), self.text('Не удалось найти ZIP-инструмент для теста.', 'The ZIP tool for testing was not found.'))
    def toggle_fullscreen(self):
        app = self.owner.app
        app.set_creation_fullscreen(self, app.full_view is not self)
    def cleanup(self):
        self.preview_timer.stop()
        if hasattr(self, 'authoring'): self.authoring.selection_timer.stop()
        if self.owner.app.full_view is self: self.owner.app.set_creation_fullscreen(self, False)
        if len(self.html_files) == 1: self.save_html(); self.preview.stop()
        shutil.rmtree(self.work_dir, ignore_errors=True)


class ToolBridge(QObject):
    """API, доступный JavaScript-коду HTML-инструмента как window.toolBridge."""
    def __init__(self, view): super().__init__(); self.view = view
    @pyqtSlot()
    @pyqtSlot(str)
    def run(self, payload=""): self.view.run_logic(payload)
    @pyqtSlot(str, str, result=str)
    def browse(self, kind, title):
        if kind == "log_file":
            path, _ = QFileDialog.getOpenFileName(self.view, title, "", "Log files (*.log *.txt);;All files (*)")
            return path
        if kind == "html_file":
            path, _ = QFileDialog.getOpenFileName(self.view, title, "", "HTML files (*.html *.htm);;All files (*)")
            return path
        if kind in ("source_folder", "output_folder"):
            return QFileDialog.getExistingDirectory(self.view, title)
        return ""
    @pyqtSlot(str, str, str, str, result=bool)
    def confirm(self, title, message, accept_text, cancel_text):
        """Show a real application-modal confirmation window for an HTML tool."""
        dialog = QMessageBox(self.view)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setIcon(QMessageBox.Question)
        accept = dialog.addButton(accept_text, QMessageBox.AcceptRole)
        cancel = dialog.addButton(cancel_text, QMessageBox.RejectRole)
        dialog.setDefaultButton(accept)
        dialog.setEscapeButton(cancel)
        dialog.exec_()
        return dialog.clickedButton() is accept

    @pyqtSlot(str)
    def log(self, payload):
        try:
            entry = json.loads(payload)
            level = str(entry.get('level', 'info')).lower()
            message = str(entry.get('message', ''))[:8192]
            level = {'debug': 10, 'info': 20, 'warn': 30, 'error': 40}.get(level, 20)
            application_logger().log(level, 'Tool JS uid=%s name=%r: %s', self.view.tool.uid, self.view.tool.name, message)
        except (TypeError, ValueError, AttributeError):
            application_logger().warning('Tool JS log payload unreadable: uid=%s', self.view.tool.uid)



class BrowserToolServer:
    """Local browser host that preserves the HTML tool -> Python action bridge."""
    def __init__(self, registry, tool, language):
        self.registry, self.tool, self.language = registry, tool, language
        self.root = registry.extract(tool).resolve()
        entry = str(tool.runtime.get("entry", "")).replace("\\", "/").lstrip("/")
        if not entry or ".." in Path(entry).parts or not (self.root / entry).is_file():
            raise ValueError("HTML entry file is unavailable")
        self.entry = entry
        owner = self
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(owner.root), **kwargs)
            def log_message(self, *_args): pass
            def do_GET(self):
                requested = unquote(urlparse(self.path).path).lstrip("/")
                if requested == owner.entry:
                    try:
                        source = (owner.root / owner.entry).read_text(encoding="utf-8-sig")
                    except UnicodeDecodeError:
                        source = (owner.root / owner.entry).read_text(encoding="utf-8", errors="replace")
                    bridge = owner.bridge_script()
                    # The tool's own scripts construct QWebChannel during page
                    # parsing.  Its browser-compatible replacement must exist
                    # before those scripts, rather than after </body>.
                    payload = (re.sub(r"(<head[^>]*>)", r"\1" + bridge, source, count=1, flags=re.IGNORECASE) if re.search(r"<head[^>]*>", source, re.IGNORECASE) else bridge + source).encode("utf-8")
                    self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
                super().do_GET()
            def do_POST(self):
                route = urlparse(self.path).path
                if route not in ("/__scs_bridge__", "/__scs_log__"): self.send_error(404); return
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 1024 * 1024)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict): raise ValueError
                    if route == "/__scs_log__":
                        level = {'debug': 10, 'info': 20, 'warn': 30, 'error': 40}.get(str(payload.get('level', 'info')).lower(), 20)
                        application_logger().log(level, 'Tool JS uid=%s name=%r: %s', owner.tool.uid, owner.tool.name, str(payload.get('message', ''))[:8192])
                        result = {"status": "ok"}
                    else:
                        result = owner.registry.run_logic(owner.tool, json.dumps(payload, ensure_ascii=False))
                except Exception:
                    result = {"status": "error", "message": "Tool action could not be run"}
                data = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler); self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.thread.start()
    @property
    def url(self): return QUrl(f"http://127.0.0.1:{self.httpd.server_port}/{self.entry}")
    def bridge_script(self):
        language = json.dumps(self.language)
        return f'''<script>(()=>{{
const run=p=>fetch('/__scs_bridge__',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(p)}}).then(r=>r.json()).then(x=>{{if(window.renderTool)window.renderTool(x);return x;}});
const toolBridge={{
  run,
  browse:(_kind,title,done)=>{{const value=window.prompt(title,'')||'';if(done)done(value);return Promise.resolve(value);}},
  confirm:(title,message,accept,cancel)=>Promise.resolve(window.confirm(title+'\\n\\n'+message)),
  log:payload=>fetch('/__scs_log__',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:payload}}).catch(()=>{{}})
}};
window.qt=window.qt||{{}};window.qt.webChannelTransport=window.qt.webChannelTransport||{{}};
window.QWebChannel=(_transport,ready)=>ready({{objects:{{toolBridge}}}});
queueMicrotask(()=>{{if({str(self.tool.supports_manager_feature('language')).lower()} && window.setLanguage)window.setLanguage({language});}});
}})();</script>'''
    def close(self): self.httpd.shutdown(); self.httpd.server_close()


class EditorPreviewBridge(QObject):
    """Inert WebChannel bridge used only by the HTML editor preview.

    Tool HTML commonly expects ``toolBridge`` to exist.  Keeping the same API
    lets its layout render normally, but no preview click can browse files,
    show native dialogs, or invoke the tool's Python/JavaScript logic.
    """
    @pyqtSlot()
    @pyqtSlot(str)
    def run(self, payload=""):
        return None

    @pyqtSlot(str, str, result=str)
    def browse(self, kind, title):
        return ""

    @pyqtSlot(str, str, str, str, result=bool)
    def confirm(self, title, message, accept_text, cancel_text):
        return False

    @pyqtSlot(str)
    def log(self, payload):
        return None


class ToolView(QWidget):
    logic_finished = pyqtSignal(str)
    def __init__(self, app, tool, detached=False):
        super().__init__(); self.setObjectName('runningToolView'); self.setContentsMargins(0, 0, 0, 0); self.app, self.tool, self.detached, self.logic_running, self.pending_logic_result = app, tool, detached, False, None; self.logic_queue = []; self.started_at = time.monotonic(); self.logic_finished.connect(self.render_logic_result); self.view_layout = QVBoxLayout(self); self.view_layout.setContentsMargins(0, 0, 0, 0); self.view_layout.setSpacing(0)
        info = QFrame(); info.setObjectName("toolInfoCard"); self.info_card = info; self.info_card.setProperty("roundedTopRight", True); row = QHBoxLayout(info); row.setContentsMargins(22, 18, 22, 18)
        icon = QLabel("◆"); icon.setObjectName("largeToolIcon"); path = app.registry.icon(tool)
        if path:
            icon.setPixmap(QPixmap(str(path)).scaled(94, 94, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else: icon.setFixedSize(94, 94); icon.setAlignment(Qt.AlignCenter)
        text = QVBoxLayout(); name = QLabel(tool.name); name.setObjectName("toolName"); self.description = QLabel(tool.description_for(app.lang["_lang_code"], app.lang["tool_description_missing"])); self.description.setObjectName("toolMeta"); self.description.setWordWrap(True); self.version = QLabel(f"{app.lang['store_version']}: {tool.version or app.lang['tool_version_missing']}"); self.version.setObjectName("toolMeta")
        text.addWidget(name); text.addWidget(self.description); text.addWidget(self.version); self.more = QPushButton(app.lang["more_info"]); self.more.setObjectName("outlineButton"); self.more.clicked.connect(lambda: ToolInfoDialog(tool, self).exec_())
        self.duration = QLabel(); self.duration.setObjectName('toolMeta'); self.duration.setAlignment(Qt.AlignRight); self.activity = QLabel('◷'); self.activity.setObjectName('eyebrow'); self.activity.setAlignment(Qt.AlignRight); self.activity.hide(); self.runtime_timer = QTimer(self); self.runtime_timer.setInterval(1000); self.runtime_timer.timeout.connect(self.update_runtime_duration); self.runtime_timer.start(); self.update_runtime_duration()
        self.reload_button = QPushButton("↻  " + app.lang["download_refresh_btn"]); self.reload_button.setObjectName("outlineButton"); self.reload_button.clicked.connect(lambda: self.app.pages["running"].reload_tool(self) if not self.detached else self.reload_detached())
        self.detach_button = QPushButton(); self.detach_button.setObjectName('outlineButton'); self.detach_button.clicked.connect(self.open_detached); self.detach_button.setVisible(not detached)
        actions = QVBoxLayout(); actions.addWidget(self.duration); actions.addWidget(self.activity); actions.addStretch(); actions.addWidget(self.reload_button); actions.addWidget(self.detach_button); actions.addWidget(self.more); actions.addStretch(); row.addWidget(icon); row.addLayout(text, 1); row.addLayout(actions); self.view_layout.addWidget(info)
        app_card = QFrame(); app_card.setObjectName("applicationCard"); self.app_card = app_card; app_layout = QVBoxLayout(app_card); self.app_layout = app_layout; caption_row = QHBoxLayout(); self.caption = QLabel(app.lang["application"]); self.caption.setObjectName("eyebrow"); self.full_button = QPushButton(app.lang["full_screen"]); self.full_button.setObjectName("outlineButton"); self.full_button.clicked.connect(lambda: self.app.set_application_fullscreen(self, True)); caption_row.addWidget(self.caption); caption_row.addStretch(); caption_row.addWidget(self.full_button); app_layout.addLayout(caption_row)
        url = app.registry.html_url(tool)
        if url:
            self.web_url = url
            self.web_placeholder = QLabel("Loading tool…"); self.web_placeholder.setObjectName("emptyHint"); self.web_placeholder.setAlignment(Qt.AlignCenter)
            app_layout.addWidget(self.web_placeholder, 1)
            # QWebEngine is created only after its parent is part of the stacked
            # interface. This is also required on macOS, where early creation
            # while a page is hidden may abort Chromium's render process.
            QTimer.singleShot(0, self.create_web_view)
        elif app.registry.exe_path(tool) and ExeHost is not None:
            self.exe_scroll = QScrollArea(); self.exe_scroll.setObjectName("exeScroll"); self.exe_scroll.setWidgetResizable(True); self.exe_scroll.setFrameShape(QFrame.NoFrame)
            # Родитель задаётся сразу, чтобы геометрия и clipping прокрутки были
            # корректны ещё до появления окна инструмента.
            self.exe_host = ExeHost(app.registry.exe_path(tool), app.lang, self.exe_scroll.viewport(), theme=app.settings.get('theme', 'dark')); self.exe_scroll.setWidget(self.exe_host)
            def sync_after_scroll():
                self.exe_host.sync_overlay()
            self.exe_scroll.horizontalScrollBar().valueChanged.connect(sync_after_scroll)
            self.exe_scroll.verticalScrollBar().valueChanged.connect(sync_after_scroll)
            app_layout.addWidget(self.exe_scroll, 1)
        else:
            note = QLabel(app.lang["runtime_missing"]); note.setObjectName("emptyHint"); app_layout.addWidget(note, 1)
        self.view_layout.addWidget(app_card, 1)
        self.update_language(app.lang)
        if detached:
            # A detached tool is the application surface itself.  The manager
            # information card and its duplicate caption waste screen space.
            self.set_application_fullscreen(True)
    def create_web_view(self):
        if not hasattr(self, "web_url") or hasattr(self, "web"): return
        # Give WebEngine its final native parent at construction time.  On
        # macOS, constructing it parentless and reparenting through a layout
        # can make Chromium abort while changing the view visibility.
        self.web = QWebEngineView(self.app_card)
        # The page is fully opaque. This avoids an extra parent-composition
        # pass for every Chromium frame.
        self.web.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.channel = QWebChannel(self.web.page()); self.bridge = ToolBridge(self); self.channel.registerObject("toolBridge", self.bridge); self.web.page().setWebChannel(self.channel)
        self.web.loadFinished.connect(lambda _ok: (self.apply_tool_language(), self.apply_tool_theme(), self.apply_tool_density()))
        index = self.app_layout.indexOf(self.web_placeholder); self.app_layout.insertWidget(max(0, index), self.web, 1)
        self.web_placeholder.deleteLater(); del self.web_placeholder
        self.web.load(self.web_url)
        self.set_runtime_active(self.is_current_tool_view())
    def set_info_top_right_rounded(self, rounded):
        """Стыкует верхний угол карточки с последней вкладкой инструмента."""
        if self.info_card.property("roundedTopRight") == rounded: return
        self.info_card.setProperty("roundedTopRight", rounded)
        self.info_card.style().unpolish(self.info_card); self.info_card.style().polish(self.info_card); self.info_card.update()
    def set_application_fullscreen(self, enabled):
        self.info_card.setVisible(not enabled); self.caption.setVisible(not enabled); self.full_button.setVisible(not enabled)
        self.view_layout.setSpacing(0 if enabled else 10)
        self.app_card.setProperty("fullscreen", enabled); self.app_card.style().unpolish(self.app_card); self.app_card.style().polish(self.app_card); self.app_card.update()
    def open_external_tool(self):
        if hasattr(self, "external_tool_url"): QDesktopServices.openUrl(self.external_tool_url)
    def is_current_tool_view(self):
        if self.detached: return self.isVisible()
        running = self.app.pages.get("running") if hasattr(self.app, "pages") else None
        return bool(running and self.app.current_key == "running" and running.tool_pages.currentWidget() is self)
    def update_runtime_duration(self):
        elapsed = max(0, int(time.monotonic() - self.started_at)); hours, rest = divmod(elapsed, 3600); minutes, seconds = divmod(rest, 60); prefix = 'Работает' if self.app.lang['_lang_code'] == 'ru' else 'Running'; self.duration.setText(f'{prefix}: {hours:02d}:{minutes:02d}:{seconds:02d}')
    def open_detached(self):
        self.app.open_detached_tool(self.tool, source_view=self)
    def reload_detached(self):
        if hasattr(self, 'web'): self.web.reload()
        elif hasattr(self, 'exe_host') and hasattr(self.exe_host, 'restart'): self.exe_host.restart()
    def set_runtime_active(self, active):
        """Keep native tool surfaces from overlaying a different application tab."""
        if hasattr(self, "web"):
            self.web.setVisible(active)
            if active and self.pending_logic_result is not None:
                data, self.pending_logic_result = self.pending_logic_result, None
                QTimer.singleShot(0, lambda: self.apply_logic_result(data))
        if hasattr(self, "exe_host"):
            self.exe_host.setVisible(active)
            if active: QTimer.singleShot(0, lambda: self.exe_host.sync_overlay(force=True))
    def apply_logic_result(self, data):
        if hasattr(self, "web") and self.is_current_tool_view():
            self.web.page().runJavaScript(f"if (window.renderTool) window.renderTool({data});")
    def apply_tool_theme(self, theme=None):
        if hasattr(self, 'web') and self.tool.supports_manager_feature('theme'):
            selected = theme or self.app.settings.get('theme', 'dark')
            self.web.page().runJavaScript(f"if (window.setManagerTheme) window.setManagerTheme({json.dumps(selected)});")
    def apply_tool_language(self):
        if hasattr(self, 'web') and self.tool.supports_manager_feature('language'):
            self.web.page().runJavaScript(f"if (window.setLanguage) window.setLanguage({json.dumps(self.app.lang['_lang_code'])});")
    def apply_tool_density(self):
        if hasattr(self, 'web') and self.tool.supports_manager_feature('density'):
            percent = {'compact': 90, 'normal': 100, 'large': 112}.get(self.app.settings.get('ui_density', 'normal'), 100)
            self.web.page().runJavaScript(f"if (window.setInterfaceDensity) window.setInterfaceDensity({percent});")
    def run_logic(self, payload=""):
        # Tool pages can request persisted settings while a user presses an
        # action button. Keep requests in order instead of silently losing the
        # user's request and leaving its HTML loader active indefinitely.
        if self.logic_running:
            self.logic_queue.append(payload)
            return
        self.logic_running = True
        self.activity.show()
        def execute():
            import json
            data = json.dumps(self.app.registry.run_logic(self.tool, payload), ensure_ascii=False)
            try: self.logic_finished.emit(data)
            except RuntimeError: pass  # Виджет мог быть закрыт до завершения фоновой логики.
        threading.Thread(target=execute, daemon=True).start()
    def render_logic_result(self, data):
        self.logic_running = False
        self.activity.hide()
        try: result = json.loads(data)
        except (TypeError, ValueError): result = {}
        if result.get("status") == "security_violation":
            self.show_security_warning(); return
        if result.get('status') == 'error':
            message = str(result.get('message') or ('Ошибка логики инструмента' if self.app.lang['_lang_code'] == 'ru' else 'Tool logic error'))
            QMessageBox.warning(self, self.app.lang['error'], message)
            if not self.detached: QTimer.singleShot(0, lambda: self.app.pages['running'].reload_tool(self) if self.app.pages['running'].tool_pages.indexOf(self) >= 0 else None)
            return
        if self.is_current_tool_view(): self.apply_logic_result(data)
        else: self.pending_logic_result = data
        if self.logic_queue and not self.logic_running:
            next_payload = self.logic_queue.pop(0)
            QTimer.singleShot(0, lambda: self.run_logic(next_payload))
    def show_security_warning(self):
        if hasattr(self, "security_warning"): return
        if hasattr(self, "web"): self.web.hide()
        self.security_warning = QLabel()
        self.security_warning.setWordWrap(True); self.security_warning.setAlignment(Qt.AlignCenter)
        self.apply_security_warning_theme()
        self.security_warning.setText(self.security_warning_text())
        self.app_layout.addWidget(self.security_warning, 1)
    def apply_security_warning_theme(self):
        if not hasattr(self, 'security_warning'): return
        theme = self.app.settings.get('theme', 'dark')
        colors = ("#fff1f1", "#d55a64", "#8e2530") if theme in ('light', 'lavender', 'spectrum_light') else ("#2b1517", "#ff9ba2", "#8d313a") if theme in ('ember', 'ocean', 'fire', 'spectrum_dark') else ("#32171b", "#ffdde0", "#ff4f5e")
        self.security_warning.setStyleSheet(f"background:{colors[0]};border:2px solid {colors[2]};border-radius:14px;color:{colors[1]};font-size:18px;font-weight:800;padding:32px;")
    def update_theme(self, theme=None):
        self.apply_security_warning_theme()
        self.apply_tool_theme(theme)
        self.apply_tool_density()
        if hasattr(self, 'exe_host') and hasattr(self.exe_host, 'set_theme'): self.exe_host.set_theme(theme or self.app.settings.get('theme', 'dark'))
    def security_warning_text(self):
        if self.app.lang["_lang_code"] == "ru":
            return "ВНИМАНИЕ! ИНСТРУМЕНТ ПЫТАЕТСЯ ВЫЙТИ ЗА ПРЕДЕЛЫ ЗАЩИТЫ.\nЗапуск остановлен.\n\nЕсли вы создатель, пожалуйста, записывайте файлы в папку ./tools_resources/ или в несистемную папку."
        return "WARNING! THE TOOL ATTEMPTED TO BREAK OUTSIDE THE PROTECTION.\nLaunch stopped.\n\nIf you are the author, please write files to ./tools_resources/ or to a non-system folder."
    def update_language(self, lang):
        self.description.setText(self.tool.description_for(lang["_lang_code"], lang["tool_description_missing"]))
        self.version.setText(f"{lang['store_version']}: {self.tool.version or lang['tool_version_missing']}")
        self.more.setText(lang["more_info"]); self.reload_button.setText("↻  " + lang["download_refresh_btn"]); self.detach_button.setText('Открыть отдельно' if lang['_lang_code'] == 'ru' else 'Open separately'); self.caption.setText(lang["application"]); self.full_button.setText(lang["full_screen"]); self.update_runtime_duration()
        if hasattr(self, "security_warning"): self.security_warning.setText(self.security_warning_text())
        if hasattr(self, "exe_host"): self.exe_host.update_language(lang)
        self.apply_tool_language()
        if hasattr(self, "mac_web_notice"):
            russian = lang['_lang_code'] == 'ru'
            self.mac_web_notice.setText("На этой версии macOS HTML-инструмент открыт в системном браузере. Встроенный браузер Qt аварийно завершается в виртуальной машине." if russian else "On this macOS version, the HTML tool is opened in the system browser. The embedded Qt browser crashes in this virtual machine.")
            self.mac_web_open.setText("Открыть в браузере" if russian else "Open in browser")


class RunningPage(Page):
    def __init__(self, app):
        super().__init__(app, "running"); self.title, self.subtitle = self.heading("", "")
        self.layout.removeWidget(self.subtitle)
        header = QHBoxLayout(); header.addWidget(self.subtitle); header.addStretch()
        self.restart_all_button = QPushButton(); self.restart_all_button.setObjectName('outlineButton'); self.restart_all_button.clicked.connect(self.restart_all); header.addWidget(self.restart_all_button)
        self.layout.insertLayout(1, header)
        self.tab_bar = QTabBar(); self.tab_bar.setObjectName("runningTabs"); self.tab_bar.setTabsClosable(True); self.tab_bar.setUsesScrollButtons(False); self.tab_bar.setExpanding(False); self.tab_bar.setElideMode(Qt.ElideNone); self.tab_bar.setDrawBase(False); self.tab_bar.setFixedHeight(34)
        self.tab_bar.tabCloseRequested.connect(self.close_index); self.tab_bar.currentChanged.connect(self.activate_current)
        self.tab_scroll = QScrollArea(); self.tab_scroll.setObjectName("runningTabScroll"); self.tab_scroll.setFrameShape(QFrame.NoFrame); self.tab_scroll.setWidgetResizable(False); self.tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.tab_scroll.setWidget(self.tab_bar)
        self.tool_pages = QStackedWidget(); self.tool_pages.setObjectName('runningToolPages'); self.tool_pages.setContentsMargins(0, 0, 0, 0); self.tool_pages.layout().setContentsMargins(0, 0, 0, 0); self.tool_pages.layout().setSpacing(0)
        workspace = QWidget(); workspace_layout = QVBoxLayout(workspace); workspace_layout.setContentsMargins(0, 0, 0, 0); workspace_layout.setSpacing(0); workspace_layout.addWidget(self.tab_scroll); workspace_layout.addWidget(self.tool_pages, 1)
        self.layout.addWidget(workspace, 1); self.update_text(app.lang); self.update_tab_scrollbar()
    def update_tab_scrollbar(self):
        self.tab_bar.setFixedWidth(max(1, self.tab_bar.sizeHint().width() - 3))
        available = self.tab_scroll.viewport().width()
        overflow = self.tab_bar.width() > available
        scroll_bar = self.tab_scroll.horizontalScrollBar(); scroll_bar.setVisible(overflow)
        bar_height = scroll_bar.sizeHint().height() if overflow else 0
        self.tab_scroll.setFixedHeight(34 + bar_height)
    def ensure_current_tab_visible(self):
        index = self.tab_bar.currentIndex()
        if index < 0: return
        rect = self.tab_bar.tabRect(index); viewport = self.tab_scroll.viewport().width(); scroll = self.tab_scroll.horizontalScrollBar()
        if rect.left() < scroll.value(): scroll.setValue(rect.left())
        elif rect.right() >= scroll.value() + viewport: scroll.setValue(rect.right() - viewport + 1)
    def update_info_corner(self):
        view = self.tool_pages.currentWidget()
        if not isinstance(view, ToolView): return
        last = self.tab_bar.count() - 1
        if last < 0: return
        last_right = self.tab_bar.tabRect(last).right() + 1 - self.tab_scroll.horizontalScrollBar().value()
        view.set_info_top_right_rounded(last_right < view.info_card.width())
    def resizeEvent(self, event):
        super().resizeEvent(event); QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.update_info_corner)
    def open_tool(self, tool):
        for i in range(self.tool_pages.count()):
            if self.tool_pages.widget(i).tool.uid == tool.uid: self.tab_bar.setCurrentIndex(i); return
        view = ToolView(self.app, tool); self.tool_pages.addWidget(view); self.tab_bar.addTab(tool.name); self.tab_bar.setCurrentIndex(self.tab_bar.count() - 1); self.app.sync_full_tabs(); self.app.remember_open_tools(); QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.ensure_current_tab_visible); QTimer.singleShot(0, self.update_info_corner)
    def reload_tool(self, view, refreshed_tool=None):
        """Replace an opened tool with a fresh instance of its saved archive."""
        index = self.tool_pages.indexOf(view)
        if index < 0: return
        tool = refreshed_tool or next((item for item in self.app.registry.scan() if item.uid == view.tool.uid), view.tool)
        was_fullscreen = self.app.full_view is view
        if was_fullscreen: self.app.set_application_fullscreen(view, False)
        old_tool = view.tool
        view.hide()
        if hasattr(view, "exe_host"): view.exe_host.stop()
        if hasattr(view, "web"): view.web.setUrl(QUrl("about:blank"))
        self.tool_pages.removeWidget(view)
        view.deleteLater()
        # Chromium may still hold the old extraction folder for a few event
        # loop turns. The registry uses one cache folder per ZIP revision, so
        # the replacement can safely load without deleting it here.
        replacement = ToolView(self.app, tool)
        self.tool_pages.insertWidget(index, replacement)
        self.tab_bar.setTabText(index, tool.name)
        self.tab_bar.setCurrentIndex(index)
        # Replacing the current stacked widget can make QStackedWidget select
        # index 0 internally.  QTabBar emits no signal when its index did not
        # change, so synchronise the content explicitly.
        self.tool_pages.setCurrentIndex(index)
        self.app.sync_full_tabs()
        QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.ensure_current_tab_visible); QTimer.singleShot(0, self.update_info_corner)
        if was_fullscreen: self.app.set_application_fullscreen(replacement, True)
        return replacement
    def restart_all(self):
        selected = self.tab_bar.currentIndex()
        views = [self.tool_pages.widget(index) for index in range(self.tool_pages.count())]
        for view in views:
            if self.tool_pages.indexOf(view) >= 0: self.reload_tool(view)
        if 0 <= selected < self.tab_bar.count(): self.tab_bar.setCurrentIndex(selected); self.activate_current()
    def close_tool(self, view):
        index = self.tool_pages.indexOf(view)
        if index >= 0:
            tool = view.tool; is_exe = hasattr(view, "exe_host")
            view.hide()
            if is_exe: view.exe_host.stop()
            if hasattr(view, "browser_server"): view.browser_server.close()
            if hasattr(view, "web"): view.web.setUrl(QUrl("about:blank"))
            self.tab_bar.removeTab(index); self.tool_pages.removeWidget(view); self.app.sync_full_tabs(); view.deleteLater(); QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.update_info_corner)
            self.app.remember_open_tools()
            if not self.tool_pages.count():
                if self.app.full_view is view: self.app.set_application_fullscreen(view, False)
                self.app.buttons["running"][0].hide()
                QTimer.singleShot(0, lambda: self.app.show_page("library"))
            QTimer.singleShot(250, lambda: self.finish_cleanup(tool, is_exe))
    def remove_for_detach(self, view):
        index = self.tool_pages.indexOf(view)
        if index < 0: return
        view.hide()
        if hasattr(view, 'exe_host'): view.exe_host.stop()
        if hasattr(view, 'browser_server'): view.browser_server.close()
        if hasattr(view, 'web'): view.web.stop(); view.web.setUrl(QUrl('about:blank'))
        self.tab_bar.removeTab(index); self.tool_pages.removeWidget(view); view.deleteLater()
        self.app.sync_full_tabs(); self.app.remember_open_tools()
        QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.update_info_corner)
        if not self.tool_pages.count():
            self.app.buttons['running'][0].hide()
            self.app.show_page('library')
    def finish_cleanup(self, tool, release_memory):
        self.app.registry.cleanup_tool(tool)
        if release_memory: release_unused_memory()
    def close_index(self, index):
        if index >= 0: self.close_tool(self.tool_pages.widget(index))
    def stop_all(self):
        for index in range(self.tool_pages.count()):
            view = self.tool_pages.widget(index)
            if hasattr(view, "exe_host"): view.exe_host.stop()
            if hasattr(view, "browser_server"): view.browser_server.close()
            if hasattr(view, "web"):
                # Chromium must receive a normal stop/navigation cycle before
                # the QApplication is destroyed, particularly on macOS.
                view.web.stop()
                view.web.hide()
                view.web.setUrl(QUrl("about:blank"))
    def activate_current(self, _index=None):
        self.tool_pages.setCurrentIndex(self.tab_bar.currentIndex()); QTimer.singleShot(0, self.ensure_current_tab_visible); QTimer.singleShot(0, self.update_info_corner)
        view = self.tool_pages.currentWidget()
        running_is_visible = self.app.current_key == "running"
        for index in range(self.tool_pages.count()):
            candidate = self.tool_pages.widget(index)
            if isinstance(candidate, ToolView): candidate.set_runtime_active(running_is_visible and candidate is view)
        if view and hasattr(view, "exe_host"): QTimer.singleShot(0, lambda: view.exe_host.sync_overlay(force=True))
    def set_runtime_active(self, active):
        current = self.tool_pages.currentWidget() if active else None
        for index in range(self.tool_pages.count()):
            view = self.tool_pages.widget(index)
            if isinstance(view, ToolView): view.set_runtime_active(view is current)
    def update_text(self, lang):
        self.title.setText(lang["tab_running"]); self.subtitle.setText(lang["running_subtitle"])
        self.restart_all_button.setText('Перезапустить все' if lang['_lang_code'] == 'ru' else 'Restart all')
        for index in range(self.tool_pages.count()): self.tool_pages.widget(index).update_language(lang)
    def update_theme(self, theme):
        for index in range(self.tool_pages.count()):
            view = self.tool_pages.widget(index)
            if isinstance(view, ToolView): view.update_theme(theme)


class DownloadedToolCard(QFrame):
    """A local-tool card that uses the banner packaged in its ZIP."""
    def __init__(self, banner_path=None):
        super().__init__(); self.banner = QPixmap(str(banner_path)) if banner_path else QPixmap()
        self.setObjectName("downloadedToolCard"); self.setMinimumHeight(118); self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def hasHeightForWidth(self): return True
    def heightForWidth(self, width):
        layout = self.layout()
        if layout is None: return self.minimumHeight()
        return max(self.minimumHeight(), layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height())
    def sizeHint(self):
        size = super().sizeHint(); size.setHeight(self.heightForWidth(size.width())); return size

    def enterEvent(self, event):
        if isinstance(self.parentWidget(), StoreCardSlot): self.parentWidget().set_hovered(True)
        super().enterEvent(event)
    def leaveEvent(self, event):
        if isinstance(self.parentWidget(), StoreCardSlot): self.parentWidget().set_hovered(False)
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath(); path.addRoundedRect(QRectF(rect), 18, 18); painter.setClipPath(path)
        if not draw_cover_pixmap(painter, rect, self.banner): painter.fillRect(rect, QColor("#191d25"))
        shade = QLinearGradient(0, 0, rect.width(), 0)
        shade.setColorAt(0, QColor(8, 11, 16, 238)); shade.setColorAt(.58, QColor(10, 14, 21, 220)); shade.setColorAt(1, QColor(12, 17, 25, 150))
        painter.fillRect(rect, shade); painter.setClipping(False)
        hovered = isinstance(self.parentWidget(), StoreCardSlot) and self.parentWidget().hovered
        painter.setPen(QColor("#ee6b2f") if hovered else QColor("#303744")); painter.drawRoundedRect(rect, 18, 18)


class LibraryPage(Page):
    def __init__(self, app):
        super().__init__(app, "library"); self.setAcceptDrops(True); self.title, self.subtitle = self.heading("", ""); bar = QHBoxLayout(); self.search = QLineEdit(); self.search.setObjectName('input'); self.search.textChanged.connect(self.filter_cards); bar.addWidget(self.search, 1); self.sort = QComboBox(); self.sort.setObjectName('input'); self.sort.setMinimumWidth(150); [self.sort.addItem('', key) for key in ('name', 'recent', 'downloaded', 'size')]; self.sort.setCurrentIndex(max(0, self.sort.findData(app.settings.get('library_sort', 'name')))); self.sort.currentIndexChanged.connect(self.change_sort); bar.addWidget(self.sort); self.refresh_button = QPushButton(); self.refresh_button.setObjectName("outlineButton"); self.refresh_button.clicked.connect(self.refresh); bar.addWidget(self.refresh_button); self.layout.addLayout(bar)
        filters = QHBoxLayout(); filters_more = QHBoxLayout(); filters.setSpacing(7); filters_more.setSpacing(7); self.category_filter = QComboBox(); self.runtime_filter = QComboBox(); self.system_filter = QComboBox(); self.collection_filter = QComboBox(); self.state_filter = QComboBox(); self.author_filter = QComboBox(); self.version_filter = QComboBox(); self.tag_filter = QComboBox()
        for combo in (self.category_filter, self.runtime_filter, self.system_filter, self.collection_filter, self.state_filter, self.author_filter, self.version_filter, self.tag_filter): combo.setObjectName('input'); combo.currentIndexChanged.connect(self.filter_cards)
        for combo in (self.category_filter, self.runtime_filter, self.system_filter, self.state_filter): filters.addWidget(combo, 1)
        for combo in (self.author_filter, self.version_filter, self.tag_filter, self.collection_filter): filters_more.addWidget(combo, 1)
        self.open_collection_button = QPushButton(); self.open_collection_button.setObjectName('outlineButton'); self.open_collection_button.clicked.connect(self.open_current_collection); filters_more.addWidget(self.open_collection_button); self.installed_button = QPushButton(); self.installed_button.setObjectName('outlineButton'); self.installed_button.clicked.connect(self.show_installed); self.state_filter.currentIndexChanged.connect(self.update_installed_button_text); filters_more.addWidget(self.installed_button); self.layout.addLayout(filters); self.layout.addLayout(filters_more)
        batch = QHBoxLayout(); self.update_selected_button = QPushButton(); self.update_selected_button.setObjectName('outlineButton'); self.update_selected_button.clicked.connect(self.update_selected); self.check_selected_button = QPushButton(); self.check_selected_button.setObjectName('outlineButton'); self.check_selected_button.clicked.connect(self.check_selected); self.check_library_button = QPushButton(); self.check_library_button.setObjectName('outlineButton'); self.check_library_button.clicked.connect(self.check_library); self.delete_selected_button = QPushButton(); self.delete_selected_button.setObjectName('dangerButton'); self.delete_selected_button.clicked.connect(self.delete_selected); self.export_list_button = QPushButton(); self.export_list_button.setObjectName('outlineButton'); self.export_list_button.clicked.connect(self.export_tool_list); self.restore_trash_button = QPushButton(); self.restore_trash_button.setObjectName('outlineButton'); self.restore_trash_button.clicked.connect(self.restore_from_trash); self.clear_selection_button = QPushButton('×'); self.clear_selection_button.setObjectName('historyButton'); self.clear_selection_button.clicked.connect(self.clear_selection); batch.addWidget(self.update_selected_button); batch.addWidget(self.check_selected_button); batch.addWidget(self.check_library_button); batch.addWidget(self.delete_selected_button); batch.addWidget(self.export_list_button); batch.addWidget(self.restore_trash_button); batch.addStretch(); batch.addWidget(self.clear_selection_button); self.layout.addLayout(batch)
        self.scroll = QScrollArea(); self.scroll.setObjectName("libraryScroll"); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame); self.content = QWidget(); self.content.setObjectName("libraryContent"); self.list = QVBoxLayout(self.content); self.list.setContentsMargins(0, 0, 0, 4); self.list.setSpacing(9); self.list.setAlignment(Qt.AlignTop); self.scroll.setWidget(self.content); self.layout.addWidget(self.scroll, 1); self.cards = []; self.selected_uids = set(); self.update_text(app.lang)
    def dragEnterEvent(self, event):
        if any(url.isLocalFile() and url.toLocalFile().lower().endswith('.scstool') for url in event.mimeData().urls()): event.acceptProposedAction()
    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith('.scstool'): self.app.open_launcher_file(url.toLocalFile()); event.acceptProposedAction(); return
    def change_sort(self, *_):
        self.app.settings['library_sort'] = self.sort.currentData()
        save_settings(self.app.settings)
        self.refresh()
    def show_installed(self):
        target = '' if self.state_filter.currentData() == 'installed' else 'installed'
        index = self.state_filter.findData(target)
        if index >= 0: self.state_filter.setCurrentIndex(index)
        self.update_installed_button_text()
    def update_installed_button_text(self):
        ru = self.app.lang['_lang_code'] == 'ru'; showing = self.state_filter.currentData() == 'installed'
        self.installed_button.setText(('Показать все' if ru else 'View all') if showing else ('Показать установленные' if ru else 'View installed'))
    def clear_selection(self):
        self.selected_uids.clear()
        for checkbox in self.content.findChildren(QCheckBox):
            checkbox.blockSignals(True); checkbox.setChecked(False); checkbox.blockSignals(False)
    def toggle_favorite(self, uid):
        favorites = set(self.app.settings.get('favorite_tools', []))
        if uid in favorites: favorites.remove(uid)
        else: favorites.add(uid)
        self.app.settings['favorite_tools'] = sorted(favorites)
        save_settings(self.app.settings)
        self.refresh()
    def show_tool_folders(self, tool, card, position):
        ru = self.app.lang['_lang_code'] == 'ru'
        menu = QMenu(card)
        archive_action = menu.addAction('Открыть папку ZIP' if ru else 'Open ZIP folder')
        resources_action = menu.addAction('Открыть папку ресурсов' if ru else 'Open resources folder')
        selected = menu.exec_(card.mapToGlobal(position))
        if selected is archive_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(tool.archive.parent)))
        elif selected is resources_action:
            path = self.app.registry.resources_path(tool)
            if path.is_dir(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            else: QMessageBox.information(self, 'Ресурсы' if ru else 'Resources', 'У этого инструмента пока нет папки ресурсов.' if ru else 'This tool has no resources folder yet.')
    def filter_cards(self, *_):
        words = self.search.text().casefold().split()
        category = self.category_filter.currentData(); runtime = self.runtime_filter.currentData(); system = self.system_filter.currentData(); collection = self.collection_filter.currentData(); state = self.state_filter.currentData(); author = self.author_filter.currentData(); version = self.version_filter.currentData(); tag = self.tag_filter.currentData(); groups = self.app.settings.get('tool_collections', {}) if isinstance(self.app.settings.get('tool_collections'), dict) else {}
        for card, searchable, tool in self.cards:
            tool_systems = tool.data.get('systems', tool.data.get('platforms', [])); tool_systems = tool_systems if isinstance(tool_systems, list) else []
            tags = [str(value) for value in tool.data.get('tags', [])] if isinstance(tool.data.get('tags'), list) else []; categories = [str(tool.data.get('category', '')).casefold(), *[value.casefold() for value in tags]]
            installed = is_installed(tool.uid)
            state_ok = not state or (state == 'installed' and installed) or (state == 'favorite' and tool.uid in set(self.app.settings.get('favorite_tools', []))) or (state == 'unavailable' and not self.runtime_available(tool))
            visible = all(word in searchable for word in words) and (not category or category.casefold() in categories) and (not runtime or runtime == str(tool.runtime.get('type', ''))) and (not system or system in [str(value).lower() for value in tool_systems]) and (not author or author == str(tool.data.get('author', ''))) and (not version or version == str(tool.data.get('version', ''))) and (not tag or tag in tags) and (not collection or collection in groups.get(tool.uid, [])) and state_ok
            card.setVisible(visible)
    def rebuild_filters(self, tools):
        filter_combos = (self.category_filter, self.runtime_filter, self.system_filter, self.state_filter, self.author_filter, self.version_filter, self.tag_filter, self.collection_filter); current = [combo.currentData() for combo in filter_combos]; groups = self.app.settings.get('tool_collections', {}) if isinstance(self.app.settings.get('tool_collections'), dict) else {}
        categories = sorted({str(tool.data.get('category')) for tool in tools if tool.data.get('category')}); runtimes = sorted({str(tool.runtime.get('type', '')) for tool in tools if tool.runtime.get('type')}); systems = sorted({str(value).lower() for tool in tools for value in (tool.data.get('systems') if isinstance(tool.data.get('systems'), list) else tool.data.get('platforms') if isinstance(tool.data.get('platforms'), list) else [])}); authors = sorted({str(tool.data.get('author')) for tool in tools if tool.data.get('author')}); versions = sorted({str(tool.data.get('version')) for tool in tools if tool.data.get('version')}); tags = sorted({str(value) for tool in tools for value in (tool.data.get('tags', []) if isinstance(tool.data.get('tags'), list) else []) if value}); collections = sorted({str(value) for values in groups.values() if isinstance(values, list) for value in values if value})
        values = [(self.category_filter, categories), (self.runtime_filter, runtimes), (self.system_filter, systems), (self.state_filter, ['installed', 'favorite', 'unavailable']), (self.author_filter, authors), (self.version_filter, versions), (self.tag_filter, tags), (self.collection_filter, collections)]
        for index, (combo, choices) in enumerate(values):
            combo.blockSignals(True); combo.clear(); combo.addItem('', '')
            for value in choices: combo.addItem(value, value)
            selected = combo.findData(current[index]); combo.setCurrentIndex(max(0, selected)); combo.blockSignals(False)
        self.update_filter_labels()
    def update_filter_labels(self):
        ru = self.app.lang['_lang_code'] == 'ru'; labels = ((self.category_filter, 'Все категории' if ru else 'All categories'), (self.runtime_filter, 'Все Runtime' if ru else 'All runtimes'), (self.system_filter, 'Все ОС' if ru else 'All systems'), (self.collection_filter, 'Все коллекции' if ru else 'All collections'), (self.state_filter, 'Все состояния' if ru else 'All states'), (self.author_filter, 'Все авторы' if ru else 'All authors'), (self.version_filter, 'Все версии' if ru else 'All versions'), (self.tag_filter, 'Все теги' if ru else 'All tags'))
        for combo, text in labels:
            if combo.count(): combo.setItemText(0, text)
        state_names = {'installed': ('Установлен', 'Installed'), 'favorite': ('Избранное', 'Favorites'), 'unavailable': ('Недоступен', 'Unavailable')}
        for index in range(1, self.state_filter.count()): self.state_filter.setItemText(index, state_names[self.state_filter.itemData(index)][0 if ru else 1])
    def assign_collection(self, uid):
        ru = self.app.lang['_lang_code'] == 'ru'; groups = self.app.settings.get('tool_collections', {}) if isinstance(self.app.settings.get('tool_collections'), dict) else {}; current = ', '.join(groups.get(uid, [])); value, ok = QInputDialog.getText(self, 'Коллекции' if ru else 'Collections', 'Названия через запятую (пусто — убрать из всех):' if ru else 'Comma-separated names (empty removes from all):', text=current)
        if not ok: return
        groups[uid] = list(dict.fromkeys(item.strip() for item in value.split(',') if item.strip())); self.app.settings['tool_collections'] = groups; save_settings(self.app.settings); self.refresh()
    def open_current_collection(self):
        group = self.collection_filter.currentData()
        if not group: return
        groups = self.app.settings.get('tool_collections', {}); tools = {tool.uid: tool for tool in self.app.registry.scan()}
        for uid, values in groups.items():
            if group in values and uid in tools: self.app.open_tool(tools[uid])
        if self.app.pages['running'].tool_pages.count(): self.app.show_page('running')
    def selected_tools(self): return [tool for _card, _text, tool in self.cards if tool.uid in self.selected_uids]
    def update_selected(self):
        store = self.app.pages.get('store'); entries = {str(item.get('uid')): item for item in getattr(store, 'tools', []) if isinstance(item, dict)}
        for tool in self.selected_tools():
            item = entries.get(tool.uid)
            if item and item.get('_store_state') == 'update': store.action(item)
    def check_selected(self):
        tools = self.selected_tools()
        if not tools:
            QMessageBox.information(self, 'Проверка' if self.app.lang['_lang_code'] == 'ru' else 'Check', 'Сначала выберите инструменты.' if self.app.lang['_lang_code'] == 'ru' else 'Select tools first.'); return
        self.check_tools(tools)
    def check_library(self): self.check_tools(self.app.registry.scan())
    def check_tools(self, tools):
        issues = []
        store = self.app.pages.get('store'); expected_hashes = {str(item.get('uid')).lower(): str(item.get('sha256', '')).lower() for item in getattr(store, 'tools', []) if isinstance(item, dict) and item.get('sha256')}
        for tool in tools:
            try:
                with zipfile.ZipFile(tool.archive) as archive:
                    if archive.testzip(): raise zipfile.BadZipFile('damaged member')
                    runtime = tool.runtime; required = [runtime.get('entry'), runtime.get('logic') or runtime.get('script')]
                    names = set(archive.namelist())
                    for name in required:
                        if name and name not in names: issues.append(f'{tool.name}: missing {name}')
                expected = expected_hashes.get(tool.uid.lower())
                if expected and file_sha256(tool.archive).lower() != expected: issues.append(f'{tool.name}: SHA-256 mismatch')
            except (OSError, zipfile.BadZipFile): issues.append(f'{tool.name}: damaged ZIP')
        QMessageBox.information(self, 'Проверка' if self.app.lang['_lang_code'] == 'ru' else 'Check', '\n'.join(issues) if issues else ('Проблем не найдено.' if self.app.lang['_lang_code'] == 'ru' else 'No problems found.'))
    def delete_selected(self):
        tools = self.selected_tools(); ru = self.app.lang['_lang_code'] == 'ru'
        if not tools:
            QMessageBox.information(self, 'Удаление' if ru else 'Delete', 'Сначала выберите инструменты.' if ru else 'Select tools first.'); return
        box = QMessageBox(QMessageBox.Warning, 'Корзина' if ru else 'Trash', ('Переместить выбранные ZIP-архивы в корзину (%d)?' if ru else 'Move selected ZIP archives to Trash (%d)?') % len(tools), QMessageBox.Yes | QMessageBox.Cancel, self)
        box.button(QMessageBox.Yes).setText('Удалить' if ru else 'Delete'); box.button(QMessageBox.Cancel).setText('Отмена' if ru else 'Cancel')
        uninstall = QCheckBox('Также удалить .scstool и ярлыки' if ru else 'Also remove .scstool files and shortcuts'); box.setCheckBox(uninstall)
        if box.exec_() != QMessageBox.Yes: return
        failed = []
        for tool in tools:
            try:
                self.app.move_tool_to_trash(tool)
                if uninstall.isChecked() and is_installed(tool.uid): uninstall_tool(tool.uid)
                self.app.registry.cleanup_tool(tool)
            except OSError as error: failed.append(f'{tool.name}: {error}')
        self.selected_uids.clear(); store = self.app.pages.get('store')
        if store: store.installed_hashes = None; store.refresh_catalog()
        self.refresh(); self.app.pages['dashboard'].refresh_overview()
        if failed: QMessageBox.warning(self, self.app.lang['error'], '\n'.join(failed))
    def export_tool_list(self):
        path, selected = QFileDialog.getSaveFileName(self, 'Экспорт списка' if self.app.lang['_lang_code'] == 'ru' else 'Export list', str(Path.home() / 'scs_tools.md'), 'Markdown (*.md);;HTML (*.html)')
        if not path: return
        tools = self.app.registry.scan(); lines = [f'- **{tool.name}** — {tool.version or "—"} (`{tool.archive.name}`)' for tool in tools]
        content = '<!doctype html><meta charset="utf-8"><title>SCS Tools</title><h1>SCS Tools</h1><ul>' + ''.join(f'<li><b>{html.escape(tool.name)}</b> — {html.escape(tool.version or "—")} <code>{html.escape(tool.archive.name)}</code></li>' for tool in tools) + '</ul>' if path.lower().endswith('.html') else '# SCS Tools\n\n' + '\n'.join(lines) + '\n'
        try: Path(path).write_text(content, encoding='utf-8')
        except OSError as error: QMessageBox.warning(self, self.app.lang['error'], str(error))
    def clear_cache(self):
        self.app.registry.cleanup_all(); self.app.registry.cache_dir.mkdir(parents=True, exist_ok=True); self.app.add_notification('Кэш инструментов очищен.' if self.app.lang['_lang_code'] == 'ru' else 'Tool cache cleared.', 'info', 'library')
    def restore_from_trash(self):
        trash = self.app.registry.tools_dir / '.trash'; files = sorted(trash.glob('*.zip'), key=lambda path: path.stat().st_mtime, reverse=True) if trash.is_dir() else []
        if not files: QMessageBox.information(self, 'Корзина' if self.app.lang['_lang_code'] == 'ru' else 'Trash', 'Корзина пуста.' if self.app.lang['_lang_code'] == 'ru' else 'Trash is empty.'); return
        ru = self.app.lang['_lang_code'] == 'ru'; dialog = QDialog(self); dialog.setWindowTitle('Корзина' if ru else 'Trash'); dialog.resize(610, 430)
        layout = QVBoxLayout(dialog); hint = QLabel('Выберите ZIP-архив для восстановления.' if ru else 'Select a ZIP archive to restore.'); hint.setObjectName('pageSubtitle'); layout.addWidget(hint)
        entries = QListWidget(); entries.setObjectName('trashList')
        for source in files:
            item = QListWidgetItem(source.name.split('__', 1)[-1]); item.setData(Qt.UserRole, str(source)); entries.addItem(item)
        entries.setCurrentRow(0); layout.addWidget(entries, 1)
        buttons = QHBoxLayout(); clear = QPushButton('Очистить корзину' if ru else 'Clear trash'); clear.setObjectName('dangerButton'); close = QPushButton('Закрыть' if ru else 'Close'); close.setObjectName('outlineButton'); restore = QPushButton('Восстановить' if ru else 'Restore'); restore.setObjectName('primaryButton'); buttons.addWidget(clear); buttons.addStretch(); buttons.addWidget(close); buttons.addWidget(restore); layout.addLayout(buttons)
        def restore_selected():
            item = entries.currentItem()
            if not item: return
            source = Path(item.data(Qt.UserRole)); target = self.app.registry.tools_dir / item.text(); number = 2
            while target.exists(): target = target.with_name(f'{target.stem}_{number}{target.suffix}'); number += 1
            try: shutil.move(str(source), str(target))
            except OSError as error: QMessageBox.warning(dialog, self.app.lang['error'], str(error)); return
            restored_name = target.stem
            message = (f'Инструмент «{restored_name}» восстановлен из корзины.' if ru
                       else f'Tool “{restored_name}” was restored from Trash.')
            self.app.add_notification(message, 'info', 'library', 'trash-restored-' + target.name)
            dialog.accept()
        def clear_all():
            question = QMessageBox.question(dialog, 'Корзина' if ru else 'Trash', 'Очистить корзину без возможности восстановления?' if ru else 'Clear Trash permanently?', QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if question != QMessageBox.Yes: return
            failures = []
            for source in files:
                try: source.unlink(missing_ok=True)
                except OSError as error: failures.append(f'{source.name}: {error}')
            if failures: QMessageBox.warning(dialog, self.app.lang['error'], '\n'.join(failures))
            else: dialog.accept()
        restore.clicked.connect(restore_selected); entries.itemDoubleClicked.connect(lambda _item: restore_selected()); clear.clicked.connect(clear_all); close.clicked.connect(dialog.reject); dialog.exec_(); self.refresh()
    def toggle_selected(self, uid, checked):
        if checked: self.selected_uids.add(uid)
        else: self.selected_uids.discard(uid)
    def runtime_available(self, tool):
        runtime_type = str(tool.runtime.get("type", ""))
        if runtime_type == "exe" and sys.platform != "win32": return False
        systems = tool.data.get('systems', tool.data.get('platforms'))
        if not isinstance(systems, list) or not systems: return True
        current = 'windows' if sys.platform == 'win32' else 'macos' if sys.platform == 'darwin' else 'linux'
        aliases = {str(value).strip().lower().replace('win32', 'windows').replace('darwin', 'macos') for value in systems}
        return current in aliases
    def verified_original_archives(self, tools):
        groups = {}
        for tool in tools: groups.setdefault(tool.uid, []).append(tool)
        pages = getattr(self.app, 'pages', {})
        store = pages.get('store') if isinstance(pages, dict) else None
        if not getattr(store, 'verified_loaded', False): return set()
        verified_tools = {str(item.get('uid')): (str(item.get('sha256')).lower(), download_archive_name(item.get('download_url', ''))) for item in getattr(store, 'tools', []) if isinstance(item, dict) and re.fullmatch(r'[0-9a-fA-F]{64}', str(item.get('sha256', '')))}
        originals = set()
        for uid, items in groups.items():
            if len(items) < 2: continue
            verified = verified_tools.get(uid)
            for tool in items:
                try: matches_verified = verified and file_sha256(tool.archive) == verified[0] and tool.archive.name == verified[1]
                except OSError: matches_verified = False
                if matches_verified: originals.add(tool.archive)
        return originals
    def duplicate_uid_blocked_archives(self, tools):
        groups = {}
        for tool in tools: groups.setdefault(tool.uid, []).append(tool)
        pages = getattr(self.app, 'pages', {})
        store = pages.get('store') if isinstance(pages, dict) else None
        if not getattr(store, 'verified_loaded', False): return set()
        originals = self.verified_original_archives(tools); blocked = set()
        for items in groups.values():
            if len(items) > 1:
                blocked.update(tool.archive for tool in items if tool.archive not in originals)
        return blocked
    def duplicate_uid_warnings(self, tools):
        return self.duplicate_uid_blocked_archives(tools)
    def sha_mismatch_archives(self, tools):
        """Archives whose UID is verified but whose bytes differ from the catalog."""
        pages = getattr(self.app, 'pages', {})
        store = pages.get('store') if isinstance(pages, dict) else None
        if not getattr(store, 'verified_loaded', False): return set()
        expected = {
            str(item.get('uid')): str(item.get('sha256', '')).lower()
            for item in getattr(store, 'tools', [])
            if isinstance(item, dict) and re.fullmatch(r'[0-9a-fA-F]{64}', str(item.get('sha256', '')))
        }
        mismatches = set()
        for tool in tools:
            wanted = expected.get(tool.uid)
            if not wanted: continue
            try:
                if file_sha256(tool.archive).lower() != wanted: mismatches.add(tool.archive)
            except OSError: pass
        return mismatches
    def update_from_sha_warning(self, tool):
        store = getattr(self.app, 'pages', {}).get('store')
        if not getattr(store, 'verified_loaded', False): return
        entry = next((item for item in store.tools if str(item.get('uid', '')) == tool.uid), None)
        if not isinstance(entry, dict): return
        entry['_store_state'] = 'update'
        store.refresh_cards(); store.action(entry)
    def refresh(self):
        self.cards = []
        while self.list.count():
            item = self.list.takeAt(0); item.widget() and item.widget().deleteLater()
        tools = self.app.registry.scan()
        self.selected_uids.intersection_update(tool.uid for tool in tools); self.rebuild_filters(tools)
        if not tools:
            empty = self.card(); empty.setObjectName("emptyCard"); label = QLabel(self.app.lang["library_empty"]); label.setObjectName("emptyTitle"); l = QVBoxLayout(empty); l.addWidget(label, alignment=Qt.AlignCenter); self.list.addWidget(empty); self.list.addStretch(); return
        uid_warnings = self.duplicate_uid_warnings(tools); blocked_archives = self.duplicate_uid_blocked_archives(tools); hash_warnings = self.sha_mismatch_archives(tools)
        for archive in hash_warnings:
            self.app.add_notification(("SHA-256 не совпадает: " if self.app.lang['_lang_code'] == 'ru' else "SHA-256 mismatch: ") + archive.name, 'warning', 'library', 'sha-' + str(archive))
        favorites = set(self.app.settings.get('favorite_tools', []))
        sort_mode = self.sort.currentData()
        recent = self.app.settings.get('tool_last_opened', {})
        if not isinstance(recent, dict): recent = {}
        def sort_key(tool):
            if sort_mode == 'recent':
                try: value = float(recent.get(tool.uid, 0))
                except (TypeError, ValueError): value = 0
            elif sort_mode == 'downloaded': value = tool.downloaded_at.timestamp()
            elif sort_mode == 'size': value = tool.archive.stat().st_size
            else: value = 0
            return (tool.uid not in favorites, -value, tool.name.casefold())
        tools.sort(key=sort_key)
        for tool in tools:
            row = DownloadedToolCard(self.app.registry.banner(tool)); r = QHBoxLayout(row); r.setContentsMargins(20, 14, 20, 14); selector = QCheckBox(); selector.setChecked(tool.uid in self.selected_uids); selector.toggled.connect(lambda checked, uid=tool.uid: self.toggle_selected(uid, checked)); icon = QLabel("◆"); icon.setObjectName("toolIcon")
            row.setContextMenuPolicy(Qt.CustomContextMenu)
            row.customContextMenuRequested.connect(lambda position, item=tool, card=row: self.show_tool_folders(item, card, position))
            path = self.app.registry.icon(tool)
            if path: icon.setPixmap(QPixmap(str(path)).scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            text = QVBoxLayout(); text.setSpacing(0); text.setAlignment(Qt.AlignVCenter); name = QLabel(tool.name); name.setObjectName("downloadedToolName"); name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed); desc = QLabel(tool.description_for(self.app.lang["_lang_code"], self.app.lang["tool_description_missing"])); desc.setObjectName("downloadedToolDescription"); desc.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed); desc.setWordWrap(True)
            name_row = QHBoxLayout(); name_row.setContentsMargins(0, 0, 0, 0); name_row.addWidget(name); name_row.addStretch()
            favorite = QPushButton('★' if tool.uid in favorites else '☆'); favorite.setObjectName('favoriteButton'); favorite.setFixedSize(34, 30); favorite.setStyleSheet('QPushButton { color:#f2cc60; font:18pt "Segoe UI Symbol"; background:transparent; border:0; } QPushButton:hover { color:#ffd977; }'); favorite.setToolTip('Убрать из избранного' if tool.uid in favorites and self.app.lang['_lang_code'] == 'ru' else 'Добавить в избранное' if self.app.lang['_lang_code'] == 'ru' else 'Remove from favorites' if tool.uid in favorites else 'Add to favorites'); favorite.clicked.connect(lambda _=False, uid=tool.uid: self.toggle_favorite(uid)); name_row.addWidget(favorite)
            text.addLayout(name_row); text.addWidget(desc)
            installation = installation_details(tool.uid)
            if installation:
                paths = [installation.get('launcher'), *(installation.get('shortcuts') or [])]; relation = QLabel(("Установка: " if self.app.lang['_lang_code'] == 'ru' else "Installation: ") + f"1 .scstool, {max(0, len(paths) - 1)} shortcut(s)"); relation.setObjectName('toolMeta'); relation.setToolTip('\n'.join(str(value) for value in paths if value)); text.addWidget(relation)
            if tool.archive in uid_warnings:
                warning = QLabel("⚠ " + ("Повторяющийся UID: этот ZIP конфликтует с другим инструментом." if self.app.lang['_lang_code'] == 'ru' else "Duplicate UID: this ZIP conflicts with another tool.")); warning.setObjectName('duplicateUidWarning'); warning.setWordWrap(True); text.addWidget(warning)
            if tool.archive in hash_warnings:
                warning = QWidget(); warning.setObjectName('hashMismatchWarning'); warning_layout = QHBoxLayout(warning); warning_layout.setContentsMargins(8, 4, 5, 4); warning_layout.setSpacing(8)
                warning_text = QLabel("⚠ " + ("SHA-256 этого локального ZIP не совпадает с версией инструмента в Магазине." if self.app.lang['_lang_code'] == 'ru' else "This local ZIP's SHA-256 does not match the tool version in Store.")); warning_text.setWordWrap(True)
                update = QPushButton("Обновить" if self.app.lang['_lang_code'] == 'ru' else "Update"); update.setObjectName('hashWarningUpdate'); update.clicked.connect(lambda _=False, t=tool: self.update_from_sha_warning(t)); warning_layout.addWidget(warning_text, 1); warning_layout.addWidget(update); text.addWidget(warning)
            blocked = tool.archive in blocked_archives
            unavailable = not self.runtime_available(tool)
            if unavailable:
                system_notice = QLabel("Недоступно на Вашей системе" if self.app.lang['_lang_code'] == 'ru' else "Unavailable on your system"); system_notice.setObjectName('systemUnavailable'); system_notice.setWordWrap(True); text.addWidget(system_notice)
            blocked = blocked or unavailable
            installed = is_installed(tool.uid)
            install_button = QPushButton("Деинсталлировать" if self.app.lang['_lang_code'] == 'ru' else "Uninstall") if installed else QPushButton("Установить" if self.app.lang['_lang_code'] == 'ru' else "Install")
            install_button.setObjectName(('dangerButton' if installed else 'outlineButton') if sys.platform == 'win32' else 'disabledButton'); install_button.setMinimumWidth(150); install_button.setEnabled(sys.platform == 'win32' and (installed or not blocked))
            install_button.clicked.connect((lambda _, t=tool: self.app.uninstall_downloaded_tool(t.uid)) if installed else (lambda _, t=tool: self.app.show_install_dialog(t)))
            btn = QPushButton("▶  " + self.app.lang["open"]); btn.setObjectName("disabledButton" if blocked else "primaryButton"); btn.setMinimumWidth(150); btn.setEnabled(not blocked); btn.setToolTip("Инструмент нельзя запустить: этот ZIP — дубликат UID, а оригинал уже установлен." if self.app.lang['_lang_code'] == 'ru' and blocked else "This tool cannot run: this ZIP duplicates a UID and the original is already installed." if blocked else ""); btn.clicked.connect(lambda _, t=tool: self.app.open_tool(t)); collection = QPushButton('Коллекция…' if self.app.lang['_lang_code'] == 'ru' else 'Collection…'); collection.setObjectName('outlineButton'); collection.setMinimumWidth(150); collection.clicked.connect(lambda _=False, uid=tool.uid: self.assign_collection(uid)); delete = QPushButton("Удалить" if self.app.lang['_lang_code'] == 'ru' else "Delete"); delete.setObjectName('dangerButton'); delete.setMinimumWidth(150); delete.clicked.connect(lambda _, t=tool: self.app.delete_tool(t)); actions = QVBoxLayout(); actions.addWidget(install_button); actions.addWidget(btn); actions.addWidget(collection); actions.addWidget(delete); r.addWidget(selector); r.addWidget(icon); r.addLayout(text, 1); r.addLayout(actions); slot = StoreCardSlot(row); self.list.addWidget(slot)
            searchable = ' '.join((tool.name, tool.archive.name, tool.description_for('ru', ''), tool.description_for('en', ''), str(tool.data.get('category', '')), str(tool.data.get('tags', '')), str(tool.data.get('author', '')), str(tool.data.get('version', '')), str(tool.data.get('systems', '')), str(tool.runtime.get('type', '')))).casefold()
            self.cards.append((slot, searchable, tool))
        self.filter_cards()
    def update_text(self, lang):
        self.title.setText(lang["library_header"]); self.subtitle.setText(lang["downloaded_subtitle"])
        ru = lang['_lang_code'] == 'ru'
        self.search.setPlaceholderText('Поиск по скачанным инструментам…' if ru else 'Search downloaded tools…')
        labels = {'name': ('По названию', 'By name'), 'recent': ('По последнему запуску', 'By last launch'),
                  'downloaded': ('По дате скачивания', 'By download date'), 'size': ('По размеру', 'By size')}
        for index in range(self.sort.count()): self.sort.setItemText(index, labels[self.sort.itemData(index)][0 if ru else 1])
        self.open_collection_button.setText('Открыть коллекцию' if ru else 'Open collection'); self.update_installed_button_text(); self.update_selected_button.setText('Обновить выбранные' if ru else 'Update selected'); self.check_selected_button.setText('Проверить выбранные' if ru else 'Check selected'); self.check_library_button.setText('Проверить библиотеку' if ru else 'Check library'); self.delete_selected_button.setText('Удалить выбранные' if ru else 'Delete selected'); self.export_list_button.setText('Экспорт списка' if ru else 'Export list'); self.restore_trash_button.setText('Корзина' if ru else 'Trash'); self.clear_selection_button.setToolTip('Снять всё выделение' if ru else 'Clear selection'); self.update_filter_labels(); self.refresh_button.setText("↻  " + lang["download_refresh_btn"]); self.refresh()


class CreatePage(Page):
    def __init__(self, app):
        super().__init__(app, "create"); self.title, self.subtitle = self.heading("", ""); self.fields = {}; self.restoring_draft = False; self.draft_enabled = False
        self.development_dir = self.app.registry.tools_dir / "development"; self.development_dir.mkdir(parents=True, exist_ok=True)
        self.draft_file = self.development_dir / "create_draft.json"; self.session_file = self.development_dir / "authoring_session.json"
        self.stage_stack = QStackedWidget(); self.stage_stack.setObjectName("createStage"); self.stage_one = QWidget(); self.stage_one.setObjectName("createStageOne"); self.stage_layout = QVBoxLayout(self.stage_one); self.stage_layout.setContentsMargins(0, 0, 0, 0); self.stage_layout.setSpacing(10); self.project_editor = None; self.html_editor = None
        self.step = QLabel(); self.step.setObjectName("eyebrow"); self.save_state = QLabel("● Сохранено"); self.save_state.setObjectName("pageSubtitle"); top = QHBoxLayout(); top.addWidget(self.step); top.addWidget(self.save_state); top.addStretch(); self.new_button = QPushButton(); self.new_button.setObjectName("outlineButton"); self.new_button.clicked.connect(self.start_new_tool); self.load_button = QPushButton(); self.load_button.setObjectName("outlineButton"); self.load_button.clicked.connect(self.load_archive); top.addWidget(self.new_button); top.addWidget(self.load_button); self.stage_layout.addLayout(top)
        form = self.card(); l = QVBoxLayout(form); self.form_layout = l; l.setContentsMargins(28, 22, 28, 24); l.setSpacing(7)
        self.section_main = QLabel(); self.section_main.setObjectName("toolName"); l.addWidget(self.section_main)
        self.add_field(l, "name")
        self.descriptions_title = QLabel(); self.descriptions_title.setObjectName("fieldLabel"); l.addWidget(self.descriptions_title)
        self.description_fields = {}; self.descriptions_layout = QVBoxLayout(); self.descriptions_layout.setSpacing(7); l.addLayout(self.descriptions_layout)
        self.add_description("en", required=True)
        self.description_language = NoWheelComboBox(); self.description_language.setObjectName("input")
        self.add_description_button = QPushButton(); self.add_description_button.setObjectName("outlineButton"); self.add_description_button.clicked.connect(self.add_selected_description)
        descriptions_row = QHBoxLayout(); descriptions_row.addWidget(self.description_language, 1); descriptions_row.addWidget(self.add_description_button); l.addLayout(descriptions_row)
        self.generate_descriptions_button = QPushButton(); self.generate_descriptions_button.setObjectName('outlineButton'); self.generate_descriptions_button.clicked.connect(self.generate_descriptions); l.addWidget(self.generate_descriptions_button, 0, Qt.AlignLeft)
        for key in ("version", "author", "category", "tags"):
            self.add_field(l, key)
        self.section_tech = QLabel(); self.section_tech.setObjectName("toolName"); l.addWidget(self.section_tech)
        self.uid_label = QLabel(); self.uid_label.setObjectName("fieldLabel"); self.uid = QLineEdit(self.generate_unique_uid()); self.uid.setObjectName("input"); self.uid.textChanged.connect(self.update_action_text)
        self.uid_regenerate = QPushButton(); self.uid_regenerate.setObjectName("outlineButton"); self.uid_regenerate.clicked.connect(lambda: self.uid.setText(self.generate_unique_uid()))
        uid_row = QHBoxLayout(); uid_row.addWidget(self.uid, 1); uid_row.addWidget(self.uid_regenerate); l.addWidget(self.uid_label); l.addLayout(uid_row)
        self.runtime_label = QLabel(); self.runtime_label.setObjectName("fieldLabel"); self.runtime_combo = NoWheelComboBox(); self.runtime_combo.setObjectName("input")
        self.runtime_combo.addItem("HTML + Python", "html_python"); self.runtime_combo.addItem("HTML + JavaScript", "html_script"); self.runtime_combo.addItem("EXE", "exe"); self.runtime_combo.addItem("Not selected", ""); self.runtime_combo.currentIndexChanged.connect(self.update_runtime_inputs); self.runtime_combo.currentIndexChanged.connect(lambda _=None: self.update_text(self.app.lang))
        l.addWidget(self.runtime_label); l.addWidget(self.runtime_combo)
        self.language_compatibility = QCheckBox(); self.language_compatibility.setChecked(True); l.addWidget(self.language_compatibility)
        self.theme_compatibility = QCheckBox(); self.theme_compatibility.setChecked(True); l.addWidget(self.theme_compatibility)
        self.density_compatibility = QCheckBox(); self.density_compatibility.setChecked(True); l.addWidget(self.density_compatibility)
        self.systems_label = QLabel(); self.systems_label.setObjectName("fieldLabel"); l.addWidget(self.systems_label)
        self.system_checks = {}
        systems_row = QHBoxLayout(); systems_row.setSpacing(18)
        for system_id, caption in (("windows", "Windows"), ("linux", "Linux"), ("macos", "macOS")):
            check = QCheckBox(caption); check.setChecked(True); self.system_checks[system_id] = check; systems_row.addWidget(check)
        systems_row.addStretch(); l.addLayout(systems_row)
        self.entry_label = QLabel(); self.entry_label.setObjectName("fieldLabel"); self.entry_path = QLineEdit(); self.entry_path.setObjectName("input"); self.entry_browse = QPushButton(); self.entry_browse.setObjectName("outlineButton"); self.entry_browse.clicked.connect(lambda: self.pick_runtime_file("entry")); entry_row = QHBoxLayout(); entry_row.addWidget(self.entry_path, 1); entry_row.addWidget(self.entry_browse); l.addWidget(self.entry_label); l.addLayout(entry_row)
        self.logic_label = QLabel(); self.logic_label.setObjectName("fieldLabel"); self.logic_path = QLineEdit(); self.logic_path.setObjectName("input"); l.addWidget(self.logic_label); l.addWidget(self.logic_path)
        self.icon_label = QLabel(); self.icon_label.setObjectName("fieldLabel"); self.icon_path = QLineEdit(); self.icon_path.setObjectName("input"); self.icon_path.setReadOnly(True); self.icon_browse = QPushButton(); self.icon_browse.setObjectName("outlineButton"); self.icon_browse.clicked.connect(self.pick_icon); self.icon_generate = QPushButton(); self.icon_generate.setObjectName("outlineButton"); self.icon_generate.clicked.connect(self.generate_placeholder_icon)
        icon_row = QHBoxLayout(); icon_row.addWidget(self.icon_path, 1); icon_row.addWidget(self.icon_generate); icon_row.addWidget(self.icon_browse); l.addWidget(self.icon_label); l.addLayout(icon_row)
        self.banner_label = QLabel(); self.banner_label.setObjectName("fieldLabel"); self.banner_path = QLineEdit(); self.banner_path.setObjectName("input"); self.banner_path.setReadOnly(True); self.banner_browse = QPushButton(); self.banner_browse.setObjectName("outlineButton"); self.banner_browse.clicked.connect(self.pick_banner)
        banner_row = QHBoxLayout(); banner_row.addWidget(self.banner_path, 1); banner_row.addWidget(self.banner_browse); l.addWidget(self.banner_label); l.addLayout(banner_row)
        self.create = QPushButton(); self.create.setObjectName("primaryButton"); self.create.clicked.connect(self.submit); self.delete_existing = QPushButton(); self.delete_existing.setObjectName("dangerButton"); self.delete_existing.clicked.connect(lambda: self.app.delete_tool_by_uid(self.uid.text().strip())); final_actions = QHBoxLayout(); final_actions.addStretch(); final_actions.addWidget(self.delete_existing); final_actions.addWidget(self.create); l.addLayout(final_actions)
        self.form_scroll = QScrollArea(); self.form_scroll.setObjectName("createScroll"); self.form_scroll.setWidgetResizable(True); self.form_scroll.setFrameShape(QFrame.NoFrame); self.form_scroll.setWidget(form)
        self.stage_layout.addWidget(self.form_scroll, 1); self.stage_stack.addWidget(self.stage_one); self.layout.addWidget(self.stage_stack, 1); self.update_text(app.lang); self.update_runtime_inputs(); self.restore_draft(); self.draft_enabled = True; self.connect_draft_signals(); QTimer.singleShot(0, self.restore_authoring_session)
    def add_field(self, layout, key):
        label = QLabel(); label.setObjectName("fieldLabel"); edit = QLineEdit(); edit.setObjectName("input")
        self.fields[key] = (label, edit); layout.addWidget(label); layout.addWidget(edit)
        if key == "version": edit.setText("1.0.0")
    def add_description(self, code, required=False):
        if code in self.description_fields: return
        label = QLabel(); label.setObjectName("fieldLabel"); edit = QLineEdit(); edit.setObjectName("input")
        self.description_fields[code] = (label, edit, required); self.descriptions_layout.addWidget(label); self.descriptions_layout.addWidget(edit)
        edit.textChanged.connect(self.save_draft)
    def add_selected_description(self):
        code = self.description_language.currentData()
        if code: self.add_description(code); self.update_text(self.app.lang); self.save_draft()
    def generate_descriptions(self):
        name = self.fields['name'][1].text().strip() or 'This tool'; category = self.fields['category'][1].text().strip()
        purpose_en = f' for {category.lower()}' if category else ''
        english = f'{name} is a convenient tool{purpose_en} in SCS Tools Manager. It provides a clear interface and helps automate everyday tasks.'
        russian = f'{name} — удобный инструмент в SCS Tools Manager' + (f' для категории «{category}»' if category else '') + '. Он предоставляет понятный интерфейс и помогает автоматизировать повседневные задачи.'
        if not self.description_fields['en'][1].text().strip(): self.description_fields['en'][1].setText(english)
        if 'ru' not in self.description_fields: self.add_description('ru')
        if not self.description_fields['ru'][1].text().strip(): self.description_fields['ru'][1].setText(russian)
        self.update_text(self.app.lang); self.save_draft()
    def text(self, ru, en): return ru if self.app.lang["_lang_code"] == "ru" else en
    def pick_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, self.text("Выберите иконку", "Choose an icon"), "", "Images (*.png *.ico *.jpg *.jpeg *.webp);;All files (*)")
        if path: self.icon_path.setText(path); self.save_draft()
    def generate_placeholder_icon(self):
        name = self.fields['name'][1].text().strip() or 'Tool'; initials = ''.join(part[0] for part in re.findall(r'[\w]+', name, re.UNICODE)[:2]).upper() or 'T'
        target = self.development_dir / 'generated_assets' / (self.uid.text().strip()[:16] or 'tool'); target.mkdir(parents=True, exist_ok=True); path = target / 'icon.png'
        pixmap = QPixmap(512, 512); pixmap.fill(Qt.transparent); painter = QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing); gradient = QLinearGradient(0, 0, 512, 512); gradient.setColorAt(0, QColor('#ff8a42')); gradient.setColorAt(1, QColor('#d83a34')); painter.setBrush(gradient); painter.setPen(Qt.NoPen); painter.drawRoundedRect(QRectF(8, 8, 496, 496), 112, 112); font = QFont('Segoe UI', 138, QFont.Bold); painter.setFont(font); painter.setPen(QColor('white')); painter.drawText(pixmap.rect(), Qt.AlignCenter, initials); painter.end(); pixmap.save(str(path), 'PNG'); self.icon_path.setText(str(path)); self.save_draft()
    def pick_banner(self):
        path, _ = QFileDialog.getOpenFileName(self, self.text("Выберите шапку", "Choose a banner"), "", "Images (*.png *.jpg *.jpeg *.webp);;All files (*)")
        if path: self.banner_path.setText(path); self.save_draft()
    def pick_runtime_file(self, kind):
        runtime = self.runtime_combo.currentData()
        if kind == "entry":
            filter_text = "HTML files (*.html *.htm)" if runtime != "exe" else "Executable files (*.exe)"
        else: filter_text = "Python files (*.py)" if runtime == "html_python" else "JavaScript files (*.js *.mjs)"
        path, _ = QFileDialog.getOpenFileName(self, self.text("Укажите файл запуска", "Choose the runtime file"), "", filter_text + ";;All files (*)")
        if path: (self.entry_path if kind == "entry" else self.logic_path).setText(path)
    def update_runtime_inputs(self):
        runtime = self.runtime_combo.currentData(); is_exe = runtime == "exe"; has_runtime = bool(runtime)
        self.entry_label.setVisible(has_runtime); self.entry_path.setVisible(has_runtime); self.entry_path.setReadOnly(is_exe); self.entry_browse.setVisible(is_exe)
        self.logic_label.setVisible(has_runtime and not is_exe); self.logic_path.setVisible(has_runtime and not is_exe)
        self.save_draft()
    def existing_tool(self):
        uid = self.uid.text().strip()
        return next((tool for tool in self.app.registry.scan() if tool.uid == uid), None)
    def generate_unique_uid(self):
        known = {tool.uid.lower() for tool in self.app.registry.scan()}
        known.update(str(uid).lower() for uid in self.app.settings.get('created_tools', []) if isinstance(uid, str))
        store = getattr(self.app, 'pages', {}).get('store')
        known.update(str(tool.get('uid', '')).lower() for tool in getattr(store, 'tools', []) if isinstance(tool, dict))
        while True:
            uid = uuid.uuid4().hex * 2
            if uid.lower() not in known: return uid
    def update_action_text(self):
        if not hasattr(self, "create"): return
        self.create.setText(self.text("Модифицировать ZIP-архив" if self.existing_tool() else "Создать ZIP-архив", "Modify ZIP archive" if self.existing_tool() else "Create ZIP archive"))
    def connect_draft_signals(self):
        self.uid.textChanged.connect(self.save_draft); self.entry_path.textChanged.connect(self.save_draft); self.logic_path.textChanged.connect(self.save_draft); self.icon_path.textChanged.connect(self.save_draft); self.banner_path.textChanged.connect(self.save_draft); self.runtime_combo.currentIndexChanged.connect(self.save_draft)
        self.language_compatibility.toggled.connect(self.save_draft); self.theme_compatibility.toggled.connect(self.save_draft); self.density_compatibility.toggled.connect(self.save_draft)
        for check in self.system_checks.values(): check.toggled.connect(self.save_draft)
        for _, edit in self.fields.values(): edit.textChanged.connect(self.save_draft)
    def draft_data(self):
        return {"fields": {key: edit.text() for key, (_, edit) in self.fields.items()}, "descriptions": {code: edit.text() for code, (_, edit, _) in self.description_fields.items()}, "uid": self.uid.text(), "runtime": self.runtime_combo.currentData(), "entry": self.entry_path.text(), "logic": self.logic_path.text(), "icon": self.icon_path.text(), "banner": self.banner_path.text(), "compatibility": {"language": self.language_compatibility.isChecked(), "theme": self.theme_compatibility.isChecked(), "density": self.density_compatibility.isChecked()}, "systems": [key for key, check in self.system_checks.items() if check.isChecked()]}
    def save_draft(self, *_):
        if self.restoring_draft or not self.draft_enabled: return
        try:
            self.draft_file.parent.mkdir(parents=True, exist_ok=True); self.draft_file.write_text(json.dumps(self.draft_data(), ensure_ascii=False, indent=2), encoding="utf-8"); self.session_file.write_text(json.dumps({'stage': 1}), encoding='utf-8'); self.set_stage_state(True)
        except OSError: pass
    def clear_descriptions(self):
        while self.descriptions_layout.count():
            item = self.descriptions_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.description_fields = {}
    def apply_data(self, data):
        self.restoring_draft = True
        try:
            for key, value in data.get("fields", {}).items():
                if key in self.fields: self.fields[key][1].setText(str(value))
            self.uid.setText(str(data.get("uid", self.uid.text())))
            runtime_index = self.runtime_combo.findData(data.get("runtime", "html_python"))
            if runtime_index >= 0: self.runtime_combo.setCurrentIndex(runtime_index)
            self.entry_path.setText(str(data.get("entry", ""))); self.logic_path.setText(str(data.get("logic", ""))); self.icon_path.setText(str(data.get("icon", ""))); self.banner_path.setText(str(data.get("banner", "")))
            compatibility = data.get('compatibility') if isinstance(data.get('compatibility'), dict) else {}
            self.language_compatibility.setChecked(compatibility.get('language', True) is not False)
            self.theme_compatibility.setChecked(compatibility.get('theme', True) is not False)
            self.density_compatibility.setChecked(compatibility.get('density', True) is not False)
            systems = data.get('systems') if isinstance(data.get('systems'), list) else list(self.system_checks)
            normalized = {str(value).strip().lower() for value in systems}
            for key, check in self.system_checks.items(): check.setChecked(key in normalized)
            self.clear_descriptions(); descriptions = data.get("descriptions", {}); self.add_description("en", required=True); self.description_fields["en"][1].setText(str(descriptions.get("en", "")))
            for code, value in descriptions.items():
                if code != "en": self.add_description(str(code)); self.description_fields[str(code)][1].setText(str(value))
        finally:
            self.restoring_draft = False
        self.update_text(self.app.lang); self.update_runtime_inputs(); self.update_action_text()
    def restore_draft(self):
        try:
            if self.draft_file.is_file(): self.apply_data(json.loads(self.draft_file.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError): pass
    def discard_editor(self, editor):
        """Close a temporary authoring stage without saving it into the previous tool."""
        if editor is None: return
        if self.app.full_view is editor: self.app.set_creation_fullscreen(editor, False)
        if hasattr(editor, 'preview_timer'): editor.preview_timer.stop()
        if hasattr(editor, 'autosave_timer'): editor.autosave_timer.stop()
        if hasattr(editor, 'authoring') and hasattr(editor.authoring, 'selection_timer'): editor.authoring.selection_timer.stop()
        if hasattr(editor, 'preview'): editor.preview.stop()
        self.stage_stack.removeWidget(editor)
        shutil.rmtree(editor.work_dir, ignore_errors=True)
        editor.deleteLater()
    def start_new_tool(self):
        confirmation = QMessageBox.question(
            self, self.text("Создать новый инструмент", "Create a new tool"),
            self.text("Текущие данные редактора будут очищены. Сохранённые ZIP-инструменты не будут затронуты.", "The current editor data will be cleared. Saved ZIP tools will not be changed."),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirmation != QMessageBox.Yes: return
        self.discard_editor(self.html_editor); self.html_editor = None
        self.discard_editor(self.project_editor); self.project_editor = None
        self.stage_stack.setCurrentWidget(self.stage_one)
        fresh = {
            "fields": {"name": "", "version": "1.0.0", "author": "", "category": ""},
            "descriptions": {"en": ""}, "uid": self.generate_unique_uid(),
            "runtime": "html_python", "entry": "", "logic": "", "icon": "", "banner": "", "compatibility": {"language": True, "theme": True, "density": True}, "systems": ["windows", "linux", "macos"]
        }
        self.apply_data(fresh)
        self.save_draft()
        self.update_text(self.app.lang)
    def load_archive(self):
        path, _ = QFileDialog.getOpenFileName(self, self.text("Загрузить аддон", "Load add-on"), str(self.app.registry.tools_dir), "Tool archives (*.zip)")
        if not path: return
        try:
            with zipfile.ZipFile(path) as archive: info = json.loads(archive.read("info.json").decode("utf-8-sig"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            QMessageBox.warning(self, self.app.lang["error"], self.text("Не удалось прочитать info.json.", "Unable to read info.json.")); return
        descriptions = {key.removeprefix("desc_"): value for key, value in info.items() if key.startswith("desc_") and isinstance(value, str)}
        runtime = info.get("runtime") if isinstance(info.get("runtime"), dict) else {}
        data = {"fields": {key: ', '.join(info.get(key, [])) if key == 'tags' and isinstance(info.get(key), list) else info.get(key, "") for key in self.fields}, "descriptions": descriptions, "uid": info.get("uid", ""), "runtime": runtime.get("type", "html_python"), "entry": runtime.get("entry", ""), "logic": runtime.get("logic", runtime.get("script", "")), "icon": "", "banner": "", "compatibility": info.get("compatibility", {}), "systems": info.get("systems", ["windows", "linux", "macos"])}
        self.apply_data(data); self.save_draft()
    def set_stage_state(self, saved):
        self.stage_is_saved = saved
        self.save_state.setText(self.text("● Сохранено", "● Saved") if saved else self.text("● Не сохранено", "● Unsaved"))
    def persist_workspace(self, archive_path, work_dir, stage):
        archive_path = Path(archive_path); work_dir = Path(work_dir); target = self.development_dir / archive_path.stem
        try:
            for path in work_dir.rglob('*'):
                if path.is_file() and path.name != 'html_history.json':
                    destination = target / path.relative_to(work_dir); destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, destination)
            self.session_file.write_text(json.dumps({'archive': str(archive_path), 'stage': stage}, ensure_ascii=False), encoding='utf-8')
        except OSError: pass
    def authoring_history_file(self, archive_path):
        return self.development_dir / Path(archive_path).stem / 'html_history.json'
    def save_authoring_history(self, archive_path, entries, index):
        try:
            history = list(entries)[-100:]
            index = max(0, min(index - max(0, len(entries) - len(history)), len(history) - 1))
            target = self.authoring_history_file(archive_path); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({'entries': history, 'index': index}, ensure_ascii=False), encoding='utf-8')
        except (OSError, TypeError): pass
    def load_authoring_history(self, archive_path, current_html):
        try:
            data = json.loads(self.authoring_history_file(archive_path).read_text(encoding='utf-8'))
            entries = data.get('entries'); index = data.get('index')
            if not isinstance(entries, list) or not entries or not all(isinstance(value, str) for value in entries): raise ValueError
            index = int(index)
            if not 0 <= index < len(entries) or entries[index] != current_html: raise ValueError
            return entries[-100:], max(0, index - max(0, len(entries) - 100))
        except (OSError, ValueError, TypeError):
            return [current_html], 0
    def restore_authoring_session(self):
        if self.app.settings.get('last_page') != 'create': return
        try:
            data = json.loads(self.session_file.read_text(encoding='utf-8')); archive = Path(data.get('archive', '')); stage = data.get('stage')
            if stage == 1: self.app.show_page('create'); return
            if not archive.is_file() or stage not in (2, 3): return
            if stage == 2 and self.project_editor and self.project_editor.archive_path == archive: return
            if stage == 3 and self.html_editor and self.html_editor.archive_path == archive: return
            (self.open_html_editor if stage == 3 else self.open_project_editor)(archive); self.app.show_page('create')
        except (OSError, ValueError, TypeError): pass
    def open_project_editor(self, archive_path):
        if self.html_editor: self.html_editor.cleanup(); self.stage_stack.removeWidget(self.html_editor); self.html_editor.deleteLater(); self.html_editor = None
        if self.project_editor: self.project_editor.cleanup(); self.stage_stack.removeWidget(self.project_editor); self.project_editor.deleteLater()
        self.project_editor = ProjectEditorPanel(archive_path, self); self.stage_stack.addWidget(self.project_editor); self.stage_stack.setCurrentWidget(self.project_editor); self.persist_workspace(archive_path, self.project_editor.work_dir, 2)
        self.title.setText(self.text("Структура инструмента", "Tool structure")); self.subtitle.setText(self.text("Этап 2 из 3 — добавьте и отредактируйте файлы проекта.", "Step 2 of 3 — add and edit project files."))
    def open_html_editor(self, archive_path):
        if self.project_editor: self.project_editor.cleanup(); self.stage_stack.removeWidget(self.project_editor); self.project_editor.deleteLater(); self.project_editor = None
        self.html_editor = HtmlEditorPanel(archive_path, self); self.stage_stack.addWidget(self.html_editor); self.stage_stack.setCurrentWidget(self.html_editor); self.persist_workspace(archive_path, self.html_editor.work_dir, 3)
        self.title.setText(self.text("HTML редактор", "HTML editor")); self.subtitle.setText(self.text("Этап 3 из 3 — настройте внешний вид и связь элементов со скриптом.", "Step 3 of 3 — refine the appearance and connect controls to script logic."))
    def close_html_editor(self):
        if not self.html_editor: return
        archive = self.html_editor.archive_path; self.html_editor.cleanup(); self.stage_stack.removeWidget(self.html_editor); self.html_editor.deleteLater(); self.html_editor = None; self.open_project_editor(archive)
    def close_project_editor(self):
        if not self.project_editor: return
        self.project_editor.cleanup(); self.stage_stack.removeWidget(self.project_editor); self.project_editor.deleteLater(); self.project_editor = None; self.stage_stack.setCurrentWidget(self.stage_one); self.update_text(self.app.lang)
    def submit(self):
        name = self.fields["name"][1].text().strip()
        english_description = self.description_fields["en"][1].text().strip()
        if not name: QMessageBox.warning(self, self.app.lang["error"], self.text("Введите название инструмента.", "Enter the tool name.")); return
        if not english_description: QMessageBox.warning(self, self.app.lang["error"], self.text("Введите описание на английском.", "Enter the English description.")); return
        if len(self.uid.text().strip()) != 64: QMessageBox.warning(self, self.app.lang["error"], self.text("UID должен состоять из 64 символов.", "UID must contain 64 characters.")); return
        runtime_type = self.runtime_combo.currentData()
        if not runtime_type or not self.entry_path.text().strip() or (runtime_type != "exe" and not self.logic_path.text().strip()):
            QMessageBox.warning(self, self.app.lang["error"], self.text("Укажите все файлы будущего запуска. Они не будут добавлены в архив на этом этапе.", "Choose every future runtime file. They will not be added to the archive at this stage.")); return
        existing = self.existing_tool()
        store = getattr(self.app, 'pages', {}).get('store')
        if not existing and any(str(item.get('uid', '')).lower() == self.uid.text().strip().lower() for item in getattr(store, 'tools', []) if isinstance(item, dict)):
            QMessageBox.warning(self, self.app.lang["error"], self.text("Этот UID уже используется опубликованным инструментом.", "This UID is already used by a published tool.")); return
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "new_tool"
        target = existing.archive if existing else self.app.registry.tools_dir / f"{safe}.zip"; number = 2
        while not existing and target.exists(): target = self.app.registry.tools_dir / f"{safe}_{number}.zip"; number += 1
        selected_systems = [key for key, check in self.system_checks.items() if check.isChecked()]
        if not selected_systems:
            QMessageBox.warning(self, self.app.lang["error"], self.text("Выберите хотя бы одну совместимую операционную систему.", "Select at least one compatible operating system.")); return
        info = {"uid": self.uid.text(), "name": name, "compatibility": {"language": self.language_compatibility.isChecked(), "theme": self.theme_compatibility.isChecked(), "density": self.density_compatibility.isChecked()}, "systems": selected_systems}
        for code, (_, edit, _) in self.description_fields.items():
            value = edit.text().strip()
            if value: info[f"desc_{code}"] = value
        for key in ("version", "author", "category"):
            value = self.fields[key][1].text().strip()
            if value: info[key] = value
        tags = [value.strip() for value in self.fields['tags'][1].text().split(',') if value.strip()]
        if tags: info['tags'] = list(dict.fromkeys(tags))
        entry_source = self.entry_path.text().strip(); exe_source_exists = os.path.isfile(entry_source)
        if runtime_type == "exe" and not exe_source_exists and not existing:
            QMessageBox.warning(self, self.app.lang["error"], self.text("Укажите существующий EXE-файл.", "Choose an existing EXE file.")); return
        entry_name = os.path.basename(entry_source)
        logic_name = os.path.basename(self.logic_path.text().strip())
        if runtime_type != "exe" and (entry_name != entry_source or logic_name != self.logic_path.text().strip()):
            QMessageBox.warning(self, self.app.lang["error"], self.text("Укажите только имена файлов, без пути.", "Enter file names only, without a path.")); return
        runtime = {"type": runtime_type, "entry": entry_name}
        if runtime_type == "html_python": runtime["logic"] = logic_name
        elif runtime_type == "html_script": runtime["script"] = logic_name
        info["runtime"] = runtime
        icon = self.icon_path.text().strip()
        banner = self.banner_path.text().strip()
        try:
            preserved = {}
            if existing:
                with zipfile.ZipFile(target) as old: preserved = {item.filename: old.read(item.filename) for item in old.infolist() if item.filename != "info.json"}
                if not icon:
                    old_info = existing.data
                    if old_info.get("icon"): info["icon"] = old_info["icon"]
                if not banner:
                    old_info = existing.data
                    if old_info.get("banner"): info["banner"] = old_info["banner"]
            existing_names = set(preserved)
            old_runtime = existing.runtime if existing else {}
            replace_names = set()
            if not existing:
                replace_names.add(entry_name)
                if runtime_type != "exe": replace_names.add(logic_name)
            elif runtime_type == "exe" and exe_source_exists:
                replace_names.add(entry_name)
                if old_runtime.get("type") == "exe" and old_runtime.get("entry"): replace_names.add(str(old_runtime["entry"]))
            elif old_runtime.get("type") == "exe" and runtime_type != "exe" and old_runtime.get("entry"):
                replace_names.add(str(old_runtime["entry"]))
            if runtime_type == "exe" and not exe_source_exists and entry_name not in existing_names:
                QMessageBox.warning(self, self.app.lang["error"], self.text("EXE не найден в архиве. Выберите файл заново.", "The EXE is not in the archive. Choose it again.")); return
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for filename, content in preserved.items():
                    replacing_image = (icon and filename == os.path.basename(icon)) or (banner and filename == os.path.basename(banner))
                    if filename not in replace_names and not replacing_image: archive.writestr(filename, content)
                if icon and os.path.isfile(icon): info["icon"] = os.path.basename(icon); archive.write(icon, info["icon"])
                if banner and os.path.isfile(banner): info["banner"] = os.path.basename(banner); archive.write(banner, info["banner"])
                if runtime_type == "exe" and exe_source_exists: archive.write(entry_source, entry_name)
                elif runtime_type != "exe":
                    if entry_name not in existing_names: archive.writestr(entry_name, b"")
                    if logic_name not in existing_names: archive.writestr(logic_name, b"")
                archive.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=2))
        except OSError as exc:
            QMessageBox.warning(self, self.app.lang["error"], str(exc)); return
        if not existing: self.app.register_created_tool(self.uid.text().strip())
        self.app.pages["library"].refresh(); self.update_action_text()
        if runtime_type == "exe":
            QMessageBox.information(self, self.app.lang["success"], self.text("Инструмент успешно создан.", "Tool created successfully."))
        else:
            self.open_project_editor(target)
    def update_text(self, lang):
        self.title.setText(lang["create_header"]); self.subtitle.setText(self.text("Этап 1 из 3 — подготовьте паспорт будущего инструмента.", "Step 1 of 3 — prepare the future tool passport."))
        self.save_state.setText(self.text("● Сохранено", "● Saved") if getattr(self, 'stage_is_saved', True) else self.text("● Не сохранено", "● Unsaved"))
        self.step.setText(self.text("ЭТАП 1  •  INFO.JSON", "STEP 1  •  INFO.JSON")); self.new_button.setText(self.text("Создать новый", "Create new")); self.load_button.setText(self.text("Загрузить инструмент", "Load Tool")); self.section_main.setText(self.text("Основная информация", "Main information")); self.section_tech.setText(self.text("Техническая информация", "Technical information"))
        self.language_compatibility.setText(self.text("Инструмент поддерживает смену языка", "Tool supports language changes"))
        self.theme_compatibility.setText(self.text("Инструмент поддерживает смену темы", "Tool supports theme changes"))
        self.density_compatibility.setText(self.text("Инструмент поддерживает плотность интерфейса", "Tool supports interface density"))
        self.systems_label.setText(self.text("Совместимые операционные системы", "Compatible operating systems"))
        labels = {"name":("Название инструмента *", "Tool name *"), "version":("Версия", "Version"), "author":("Автор", "Author"), "category":("Категория", "Category"), "tags":("Теги (через запятую)", "Tags (comma-separated)")}
        for key, (label, _) in self.fields.items(): label.setText(self.text(*labels[key]))
        self.descriptions_title.setText(self.text("Описания", "Descriptions"))
        self.generate_descriptions_button.setText(self.text('Создать описания-заглушки', 'Generate placeholder descriptions'))
        for code, (label, _, required) in self.description_fields.items():
            names = dict(languages.available_languages()); language_name = names.get(code, code.upper())
            label.setText(self.text(f"Описание — {language_name}" + (" *" if required else ""), f"Description — {language_name}" + (" *" if required else "")))
        current = self.description_language.currentData(); self.description_language.blockSignals(True); self.description_language.clear()
        for code, name in languages.available_languages():
            if code != "en" and code not in self.description_fields: self.description_language.addItem(name, code)
        index = self.description_language.findData(current)
        if index >= 0: self.description_language.setCurrentIndex(index)
        self.description_language.blockSignals(False); self.description_language.setEnabled(self.description_language.count() > 0); self.add_description_button.setEnabled(self.description_language.count() > 0); self.add_description_button.setText("+  " + self.text("Добавить язык", "Add language"))
        self.uid_label.setText(self.text("UID (64 символа)", "UID (64 characters)")); self.uid_regenerate.setText(self.text("Новый UID", "New UID")); self.runtime_label.setText(self.text("Тип запуска *", "Runtime type *")); self.entry_label.setText(self.text("Название HTML-файла *" if self.runtime_combo.currentData() != "exe" else "EXE-файл *", "HTML file name *" if self.runtime_combo.currentData() != "exe" else "EXE file *")); self.logic_label.setText(self.text("Название Python-файла *" if self.runtime_combo.currentData() == "html_python" else "Название JavaScript-файла *", "Python file name *" if self.runtime_combo.currentData() == "html_python" else "JavaScript file name *")); self.entry_path.setPlaceholderText("app.html" if self.runtime_combo.currentData() != "exe" else "Choose an EXE…"); self.logic_path.setPlaceholderText("logic.py" if self.runtime_combo.currentData() == "html_python" else "logic.js"); self.entry_browse.setText(self.text("Обзор…", "Browse…")); self.icon_label.setText(self.text("Иконка (необязательно)", "Icon (optional)")); self.icon_generate.setText(self.text("Создать заглушку", "Generate placeholder")); self.icon_browse.setText(self.text("Обзор…", "Browse…")); self.delete_existing.setText(self.text("Удалить инструмент по UID", "Delete tool by UID")); self.update_action_text()
        self.banner_label.setText(self.text("Шапка (необязательно)", "Banner (optional)")); self.banner_browse.setText(self.text("Обзор…", "Browse…"))
        if self.stage_stack.currentWidget() is not self.stage_one:
            is_html_stage = self.html_editor is not None and self.stage_stack.currentWidget() is self.html_editor
            self.title.setText(self.text("HTML редактор", "HTML editor") if is_html_stage else self.text("Структура инструмента", "Tool structure")); self.subtitle.setText(self.text("Этап 3 из 3 — настройте внешний вид и связь элементов со скриптом.", "Step 3 of 3 — refine the appearance and connect controls to script logic.") if is_html_stage else self.text("Этап 2 из 3 — добавьте и отредактируйте файлы проекта.", "Step 2 of 3 — add and edit project files."))
            if self.project_editor: self.project_editor.update_language(lang["_lang_code"])
            if self.html_editor: self.html_editor.update_language(lang["_lang_code"])


class DebugPage(Page):
    def __init__(self, app):
        super().__init__(app, 'debug')
        self.title, self.subtitle = self.heading('', '')
        controls = QHBoxLayout()
        self.filter = QComboBox(); self.filter.setObjectName('input')
        for key in ('all', 'error', 'download', 'editor', 'bridge'):
            self.filter.addItem('', key)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.detailed = QCheckBox(); self.detailed.setChecked(bool(app.settings.get('detailed_logging', False)))
        self.detailed.toggled.connect(self.set_detailed)
        self.refresh_button = QPushButton(); self.refresh_button.setObjectName('outlineButton'); self.refresh_button.clicked.connect(self.refresh)
        self.copy_button = QPushButton(); self.copy_button.setObjectName('outlineButton'); self.copy_button.clicked.connect(self.copy_diagnostics)
        self.folder_button = QPushButton(); self.folder_button.setObjectName('outlineButton'); self.folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_path() / 'logs'))))
        for widget in (self.filter, self.detailed, self.refresh_button, self.copy_button, self.folder_button): controls.addWidget(widget)
        controls.addStretch(); self.layout.addLayout(controls)
        actions = QHBoxLayout(); self.integrity_button = QPushButton(); self.integrity_button.setObjectName('outlineButton'); self.integrity_button.clicked.connect(self.check_integrity); self.report_button = QPushButton(); self.report_button.setObjectName('outlineButton'); self.report_button.clicked.connect(self.create_report); self.cache_button = QPushButton(); self.cache_button.setObjectName('outlineButton'); self.cache_button.clicked.connect(self.clear_cache); self.association_button = QPushButton(); self.association_button.setObjectName('outlineButton'); self.association_button.clicked.connect(self.restore_association); self.shortcuts_button = QPushButton(); self.shortcuts_button.setObjectName('outlineButton'); self.shortcuts_button.clicked.connect(self.configure_shortcuts); [actions.addWidget(widget) for widget in (self.integrity_button, self.report_button, self.cache_button, self.association_button, self.shortcuts_button)]; actions.addStretch(); self.layout.addLayout(actions)
        self.summary = QLabel(); self.summary.setObjectName('pageSubtitle'); self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse); self.summary.setWordWrap(True); self.layout.addWidget(self.summary)
        self.log_view = QPlainTextEdit(); self.log_view.setObjectName('projectEditor'); self.log_view.setReadOnly(True); self.layout.addWidget(self.log_view, 1)
        self.shortcuts = QLabel(); self.shortcuts.setObjectName('pageSubtitle'); self.shortcuts.setTextInteractionFlags(Qt.TextSelectableByMouse); self.layout.addWidget(self.shortcuts)
        self.update_text(app.lang)
    def set_detailed(self, enabled):
        self.app.settings['detailed_logging'] = bool(enabled)
        application_logger().setLevel(logging.DEBUG if enabled else logging.INFO)
        configure_detailed_tracing(enabled)
        save_settings(self.app.settings)
        self.refresh()
    def diagnostics_text(self):
        from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
        pages = getattr(self.app, 'pages', {})
        store = pages.get('store') if isinstance(pages, dict) else None
        state = 'online' if getattr(store, 'verified_loaded', False) else 'unavailable'
        metadata = getattr(store, 'catalog_metadata', {}) if store else {}; catalog_version = metadata.get('version') or metadata.get('catalog_version') or metadata.get('updated_at') or 'not specified'
        cache_size = self.app.registry.cache_size() if hasattr(self.app.registry, 'cache_size') else 0
        tools_dir = Path(getattr(self.app.registry, 'tools_dir', Path.cwd() / 'tools'))
        try: free_space = shutil.disk_usage(tools_dir).free / (1024 ** 3)
        except OSError: free_space = 0
        installed_count = len(list((tools_dir / "installed").glob("*.scstool")))
        if self.app.lang['_lang_code'] == 'ru':
            state = 'доступен' if state == 'online' else 'недоступен'
            return (f'SCS Tools Manager {APP_VERSION}\nОС: {platform.platform()}\n'
                    f'Python: {sys.version.split()[0]} | Qt: {QT_VERSION_STR} | PyQt: {PYQT_VERSION_STR}\n'
                    f'Инструменты: {tools_dir}\nКаталог: {state} | Версия: {catalog_version}\nСвободно: {free_space:.1f} ГиБ\n'
                    f'Кэш: {cache_size / (1024 * 1024):.1f} МиБ / 100 МиБ\n'
                    f'Установленные файлы запуска: {installed_count}\nЛог: {app_data_path() / "logs" / "application.log"}')
        return (f'SCS Tools Manager {APP_VERSION}\nOS: {platform.platform()}\n'
                f'Python: {sys.version.split()[0]} | Qt: {QT_VERSION_STR} | PyQt: {PYQT_VERSION_STR}\n'
                f'Tools: {tools_dir}\nCatalog: {state} | Version: {catalog_version}\nFree space: {free_space:.1f} GiB\n'
                f'Cache: {cache_size / (1024 * 1024):.1f} MiB / 100 MiB\nInstalled launchers: {installed_count}\n'
                f'Log: {app_data_path() / "logs" / "application.log"}')
    def copy_diagnostics(self):
        QApplication.clipboard().setText(self.diagnostics_text() + '\n\n' + self.log_view.toPlainText())
    def check_integrity(self):
        issues = application_integrity_issues(self.app.registry)
        message = ('Проблем не найдено.' if self.app.lang['_lang_code'] == 'ru' else 'No problems found.') if not issues else ('Не найдены или недоступны:\n' if self.app.lang['_lang_code'] == 'ru' else 'Missing or unavailable:\n') + '\n'.join(issues)
        QMessageBox.information(self, 'Проверка целостности' if self.app.lang['_lang_code'] == 'ru' else 'Integrity check', message)
    def create_report(self):
        folder = app_data_path() / 'logs' / 'reports'; folder.mkdir(parents=True, exist_ok=True); path = folder / ('report_' + time.strftime('%Y%m%d_%H%M%S') + '.txt')
        try: path.write_text(self.diagnostics_text() + '\n\n=== LOG ===\n' + self.log_view.toPlainText(), encoding='utf-8')
        except OSError as error: QMessageBox.warning(self, self.app.lang['error'], str(error)); return
        QMessageBox.information(self, 'Отчёт' if self.app.lang['_lang_code'] == 'ru' else 'Report', ('Отчёт сохранён:\n' if self.app.lang['_lang_code'] == 'ru' else 'Report saved:\n') + str(path))
    def clear_cache(self):
        self.app.registry.cleanup_all(); self.app.registry.cache_dir.mkdir(parents=True, exist_ok=True); self.refresh()
    def restore_association(self):
        try: register_file_association(); message = 'Ассоциация .scstool восстановлена.' if self.app.lang['_lang_code'] == 'ru' else '.scstool association restored.'
        except OSError as error: message = str(error)
        QMessageBox.information(self, 'SCS Tool', message)
    def configure_shortcuts(self):
        ru = self.app.lang['_lang_code'] == 'ru'; dialog = QDialog(self); dialog.setWindowTitle('Горячие клавиши' if ru else 'Keyboard shortcuts'); dialog.resize(610, 470); layout = QVBoxLayout(dialog)
        tree = QTreeWidget(); tree.setObjectName('projectTree'); tree.setHeaderLabels(['Действие' if ru else 'Action', 'Сочетание' if ru else 'Shortcut']); tree.setColumnWidth(0, 350)
        names = {'command_palette': ('Поиск и команды', 'Search and commands'), 'dashboard': ('Главная', 'Home'), 'store': ('Магазин', 'Store'), 'library': ('Скачанные', 'Downloaded'), 'running': ('Запущенные', 'Running'), 'create': ('Создание', 'Create'), 'debug': ('Дебаг', 'Debug'), 'settings': ('Настройки', 'Settings')}
        values = {**DEFAULT_SETTINGS['shortcuts'], **(self.app.settings.get('shortcuts', {}) if isinstance(self.app.settings.get('shortcuts'), dict) else {})}
        for key, pair in names.items(): item = QTreeWidgetItem([pair[0 if ru else 1], values.get(key, '')]); item.setData(0, Qt.UserRole, key); tree.addTopLevelItem(item)
        def edit(item=None, _column=0):
            item = item or tree.currentItem()
            if not item: return
            value, ok = QInputDialog.getText(dialog, 'Горячая клавиша' if ru else 'Shortcut', 'Сочетание:' if ru else 'Key sequence:', text=item.text(1))
            if ok and (not value.strip() or not QKeySequence(value.strip()).isEmpty()): item.setText(1, value.strip())
        tree.itemDoubleClicked.connect(edit); layout.addWidget(tree, 1); buttons = QHBoxLayout(); reset = QPushButton('Сбросить' if ru else 'Reset'); apply = QPushButton('Применить' if ru else 'Apply'); apply.setObjectName('primaryButton'); cancel = QPushButton(self.app.lang['close']); reset.clicked.connect(lambda: [tree.topLevelItem(i).setText(1, DEFAULT_SETTINGS['shortcuts'].get(tree.topLevelItem(i).data(0, Qt.UserRole), '')) for i in range(tree.topLevelItemCount())]); cancel.clicked.connect(dialog.reject)
        def save():
            self.app.settings['shortcuts'] = {tree.topLevelItem(i).data(0, Qt.UserRole): tree.topLevelItem(i).text(1) for i in range(tree.topLevelItemCount())}; save_settings(self.app.settings); self.app.apply_shortcuts(); dialog.accept()
        apply.clicked.connect(save); buttons.addWidget(reset); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(apply); layout.addLayout(buttons); dialog.exec_()
    def refresh(self, *_):
        self.summary.setText(self.diagnostics_text())
        path = app_data_path() / 'logs' / 'application.log'
        try: lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-1000:]
        except OSError: lines = []
        key = self.filter.currentData()
        tokens = {'error': ('| ERROR |', '| CRITICAL |', '| WARNING |'),
                  'download': ('download', 'catalog', 'store'),
                  'editor': ('editor', 'authoring', 'create'),
                  'bridge': ('Tool JS', 'Tool Python', 'bridge', 'logic')}
        if key in tokens: lines = [line for line in lines if any(token.casefold() in line.casefold() for token in tokens[key])]
        self.log_view.setPlainText('\n'.join(lines))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
    def update_text(self, lang):
        ru = lang['_lang_code'] == 'ru'
        self.title.setText('Дебаг' if ru else 'Debug')
        self.subtitle.setText('Состояние приложения и журнал событий' if ru else 'Application status and event log')
        labels = {'all': ('Все события', 'All events'), 'error': ('Ошибки', 'Errors'),
                  'download': ('Загрузки', 'Downloads'), 'editor': ('Редактор', 'Editor'),
                  'bridge': ('Мосты и логика', 'Bridges and logic')}
        for index in range(self.filter.count()): self.filter.setItemText(index, labels[self.filter.itemData(index)][0 if ru else 1])
        self.detailed.setText('Подробный лог' if ru else 'Detailed logging')
        self.refresh_button.setText('Обновить' if ru else 'Refresh')
        self.copy_button.setText('Скопировать диагностику' if ru else 'Copy diagnostics')
        self.folder_button.setText('Открыть папку логов' if ru else 'Open log folder')
        self.integrity_button.setText('Проверить приложение' if ru else 'Check application')
        self.report_button.setText('Создать отчёт' if ru else 'Create report')
        self.cache_button.setText('Очистить кэш' if ru else 'Clear cache')
        self.association_button.setText('Восстановить .scstool' if ru else 'Restore .scstool')
        self.shortcuts_button.setText('Горячие клавиши' if ru else 'Shortcuts')
        self.shortcuts.setText('Клавиши: Ctrl+K — поиск и команды; Ctrl+Z / Ctrl+Shift+Z — отмена / повтор в редакторе.' if ru else 'Shortcuts: Ctrl+K — search and commands; Ctrl+Z / Ctrl+Shift+Z — undo / redo in the editor.')
        self.refresh()


class SettingsPage(Page):
    def __init__(self, app):
        super().__init__(app, "settings"); self.title, self.subtitle = self.heading("", "")
        card = self.card(); l = QVBoxLayout(card); l.setContentsMargins(28, 24, 28, 24)
        self.lang_label = QLabel(); self.lang_label.setObjectName("fieldLabel"); self.combo = NoWheelComboBox(); self.combo.setObjectName("input"); self.combo.currentIndexChanged.connect(self.changed)
        l.addWidget(self.lang_label); l.addWidget(self.combo); self.theme_label = QLabel(); self.theme_label.setObjectName("fieldLabel"); self.theme_combo = NoWheelComboBox(); self.theme_combo.setObjectName("input"); self.theme_combo.currentIndexChanged.connect(self.changed_theme)
        l.addWidget(self.theme_label); l.addWidget(self.theme_combo)
        self.density_label = QLabel(); self.density_label.setObjectName('fieldLabel'); self.density_combo = NoWheelComboBox(); self.density_combo.setObjectName('input'); [self.density_combo.addItem('', key) for key in ('compact', 'normal', 'large')]; self.density_combo.setCurrentIndex(max(0, self.density_combo.findData(app.settings.get('ui_density', 'normal')))); self.density_combo.currentIndexChanged.connect(self.changed_density); l.addWidget(self.density_label); l.addWidget(self.density_combo)
        self.auto_updates = QCheckBox(); self.auto_updates.setChecked(bool(app.settings.get('auto_update_check', True))); self.auto_updates.toggled.connect(self.changed_auto_updates); l.addWidget(self.auto_updates)
        actions = QHBoxLayout(); self.export_button = QPushButton(); self.export_button.setObjectName('outlineButton'); self.export_button.clicked.connect(self.export_settings); self.import_button = QPushButton(); self.import_button.setObjectName('outlineButton'); self.import_button.clicked.connect(self.import_settings); actions.addWidget(self.export_button); actions.addWidget(self.import_button); actions.addStretch(); l.addLayout(actions)
        self.layout.addWidget(card); self.layout.addStretch(); self.update_text(app.lang)
    def changed(self):
        code = self.combo.currentData()
        if code and code != self.app.lang["_lang_code"]: self.app.change_language(code)
    def changed_theme(self):
        theme = self.theme_combo.currentData()
        if theme and theme != self.app.settings.get("theme"): self.app.change_theme(theme)
    def changed_density(self):
        density = self.density_combo.currentData()
        if density: self.app.settings['ui_density'] = density; save_settings(self.app.settings); self.app.apply_theme(self.app.settings.get('theme'), persist=False)
    def changed_auto_updates(self, enabled):
        self.app.settings['auto_update_check'] = bool(enabled); save_settings(self.app.settings)
    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Экспорт настроек' if self.app.lang['_lang_code'] == 'ru' else 'Export settings', 'scs_tools_settings.json', 'JSON (*.json)')
        if path:
            try: Path(path).write_text(json.dumps(self.app.settings, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError as error: QMessageBox.warning(self, self.app.lang['error'], str(error))
    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Импорт настроек' if self.app.lang['_lang_code'] == 'ru' else 'Import settings', '', 'JSON (*.json)')
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(data, dict): raise ValueError('Settings must be a JSON object')
            self.app.settings.update({key: value for key, value in data.items() if key in DEFAULT_SETTINGS}); save_settings(self.app.settings)
            self.app.change_language(str(self.app.settings.get('language', 'ru'))); self.app.apply_theme(self.app.settings.get('theme'), persist=False); self.update_text(self.app.lang)
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error: QMessageBox.warning(self, self.app.lang['error'], str(error))
    def update_text(self, lang):
        self.title.setText(lang["settings_header"]); self.subtitle.setText(lang["settings_subtitle"]); self.lang_label.setText(lang["settings_language"]); self.theme_label.setText(lang["settings_theme"])
        self.combo.blockSignals(True); self.combo.clear()
        for code, name in languages.available_languages(): self.combo.addItem(name, code)
        self.combo.setCurrentIndex(self.combo.findData(lang["_lang_code"])); self.combo.blockSignals(False)
        self.theme_combo.blockSignals(True); self.theme_combo.clear()
        for key, name in self.app.theme_choices(): self.theme_combo.addItem(name, key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.app.settings.get("theme", "dark")))); self.theme_combo.blockSignals(False)
        ru = lang['_lang_code'] == 'ru'; self.density_label.setText('Плотность интерфейса' if ru else 'Interface density'); names = {'compact': ('Компактная', 'Compact'), 'normal': ('Обычная', 'Normal'), 'large': ('Крупная', 'Large')}
        self.density_combo.blockSignals(True)
        for index in range(self.density_combo.count()): self.density_combo.setItemText(index, names[self.density_combo.itemData(index)][0 if ru else 1])
        self.density_combo.setCurrentIndex(max(0, self.density_combo.findData(self.app.settings.get('ui_density', 'normal')))); self.density_combo.blockSignals(False)
        self.auto_updates.setText('Автоматически проверять обновления менеджера' if ru else 'Automatically check for manager updates')
        self.export_button.setText('Экспорт настроек' if ru else 'Export settings'); self.import_button.setText('Импорт настроек' if ru else 'Import settings')


class MainWindow(QMainWindow):
    launcher_resolution_done = pyqtSignal(object, str)
    def __init__(self):
        super().__init__(); self.detached_windows = []; self._launcher_startup = False; self.settings = load_settings(); self.settings["theme"] = {"dark_scs": "dark"}.get(self.settings.get("theme"), self.settings.get("theme", "dark")); self.lang = languages.get_lang(self.settings["language"]); set_log_language(self.lang['_lang_code']); self.registry = ToolRegistry(); self.cleanup_tool_trash(); self.setWindowTitle("SCS Tools Manager"); self.setWindowIcon(QIcon(resource_path("assets", "icon.ico"))); self.resize(1200, 780); self.setMinimumSize(1000, 680)
        self.launcher_resolution_done.connect(self.finish_launcher_resolution)
        root = AnimatedBackground(); root.setObjectName("root"); root.setAttribute(Qt.WA_OpaquePaintEvent, True); self.setCentralWidget(root); self.root_layout = layout = QHBoxLayout(root); layout.setContentsMargins(20, 12, 20, 14); layout.setSpacing(18)
        side = QFrame(); self.sidebar = side; side.setObjectName("sidebar"); side.setFixedWidth(225); sl = QVBoxLayout(side); sl.setContentsMargins(13, 20, 13, 15); sl.setSpacing(7)
        brand = QLabel("SCS  TOOLS"); brand.setObjectName("brand"); sl.addWidget(brand); self.nav_title = QLabel(); self.nav_title.setObjectName("navTitle"); sl.addWidget(self.nav_title); self.buttons = {}
        for key, icon in (("dashboard", "⌂"), ("store", "▣"), ("library", "↓"), ("running", "▤"), ("create", "+"), ("debug", "⌁"), ("settings", "⚙")):
            b = QPushButton(); b.setObjectName("navButton"); b.setCheckable(True); b.clicked.connect(lambda _, k=key: self.show_page(k)); sl.addWidget(b); self.buttons[key] = (b, icon)
            if key == "running": b.hide()
        sl.addStretch(); version = QLabel("v" + APP_VERSION); version.setObjectName("muted"); sl.addWidget(version); layout.addWidget(side)
        self.content = content = QVBoxLayout(); self.header_box = QWidget(); header = QHBoxLayout(self.header_box); header.setContentsMargins(0, 0, 0, 0); self.exit_button = QPushButton(); self.exit_button.setObjectName("outlineButton"); self.exit_button.setFixedHeight(34); self.exit_button.clicked.connect(self.close); self.lang_button = QPushButton("RU / EN"); self.lang_button.setObjectName("outlineButton"); self.lang_button.setFixedHeight(34); self.lang_button.clicked.connect(lambda: self.change_language("en" if self.lang["_lang_code"] == "ru" else "ru")); self.notification_button = QPushButton(); self.notification_button.setObjectName('outlineButton'); self.notification_button.setFixedHeight(34); self.notification_button.clicked.connect(self.show_notifications); header.addStretch(); header.addWidget(self.notification_button); header.addWidget(self.exit_button); header.addWidget(self.lang_button); content.addWidget(self.header_box)
        self.stack = QStackedWidget(); self.pages = {"dashboard": HomePage(self), "store": StorePage(self), "library": LibraryPage(self), "running": RunningPage(self), "create": CreatePage(self), "debug": DebugPage(self), "settings": SettingsPage(self)}
        for p in self.pages.values(): self.stack.addWidget(p)
        self.full_reveal = QFrame(); self.full_reveal.setFixedHeight(1); self.full_reveal.hide(); self.full_reveal.installEventFilter(self)
        self.full_toolbar = QFrame(); self.full_toolbar.setObjectName("fullToolbar"); self.full_toolbar.setMaximumHeight(0); self.full_toolbar.hide(); self.full_toolbar.installEventFilter(self)
        full_layout = QHBoxLayout(self.full_toolbar); full_layout.setContentsMargins(8, 0, 8, 0); self.full_tabs = QTabBar(); self.full_tabs.setObjectName("runningTabs"); self.full_tabs.setTabsClosable(True); self.full_tabs.setUsesScrollButtons(False); self.full_tabs.setExpanding(False); self.full_tabs.setDrawBase(False); self.full_tabs.setFixedHeight(34); self.full_tabs.tabCloseRequested.connect(lambda i: self.pages["running"].close_index(i)); self.full_tabs.currentChanged.connect(lambda i: i >= 0 and self.pages["running"].tab_bar.setCurrentIndex(i)); self.full_tabs.currentChanged.connect(lambda _i: QTimer.singleShot(0, self.ensure_full_current_tab_visible)); self.full_tab_scroll = QScrollArea(); self.full_tab_scroll.setObjectName("runningTabScroll"); self.full_tab_scroll.setFrameShape(QFrame.NoFrame); self.full_tab_scroll.setWidgetResizable(False); self.full_tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.full_tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.full_tab_scroll.setWidget(self.full_tabs); full_layout.addWidget(self.full_tab_scroll, 1)
        self.full_exit_mode = QPushButton(); self.full_exit_mode.setObjectName("outlineButton"); self.full_exit_mode.clicked.connect(self.exit_full_mode); self.full_lang = QPushButton("RU / EN"); self.full_lang.setObjectName("outlineButton"); self.full_lang.clicked.connect(lambda: self.change_language("en" if self.lang["_lang_code"] == "ru" else "ru")); self.full_exit = QPushButton(); self.full_exit.setObjectName("outlineButton"); self.full_exit.clicked.connect(self.close); full_layout.addWidget(self.full_exit_mode); full_layout.addWidget(self.full_lang); full_layout.addWidget(self.full_exit)
        content.addWidget(self.full_reveal); content.addWidget(self.full_toolbar); content.addWidget(self.stack, 1); layout.addLayout(content, 1); self.full_view = None; self.full_mode_kind = None; self.full_hover_timer = QTimer(self); self.full_hover_timer.setInterval(50); self.full_hover_timer.timeout.connect(self.check_full_hover); self._scroll_animations = {}; self._scroll_targets = {}; self._density_layout_bases = {}; self._base_application_font = QApplication.instance().font(); QApplication.instance().installEventFilter(self)
        self.command_shortcut = QShortcut(QKeySequence(), self); self.command_shortcut.setContext(Qt.ApplicationShortcut); self.command_shortcut.activated.connect(self.show_command_palette)
        self.navigation_shortcuts = {}
        for key in ('dashboard', 'store', 'library', 'running', 'create', 'debug', 'settings'):
            shortcut = QShortcut(QKeySequence(), self); shortcut.setContext(Qt.ApplicationShortcut); shortcut.activated.connect(lambda target=key: self.show_page(target)); self.navigation_shortcuts[key] = shortcut
        self.apply_shortcuts()
        application_logger().setLevel(logging.DEBUG if self.settings.get('detailed_logging') else logging.INFO)
        configure_detailed_tracing(bool(self.settings.get('detailed_logging')))
        self.update_text(); self.apply_theme(self.settings.get("theme"), persist=False); last_page = self.settings.get('last_page', 'dashboard'); self.show_page('library' if last_page == 'running' else last_page if last_page in self.pages else 'dashboard')
        QTimer.singleShot(120, lambda: None if self._launcher_startup else self.restore_open_tools())
        if self.settings.get('first_run_tip', True): QTimer.singleShot(550, self.show_first_run_tip)
    def show_first_run_tip(self):
        if self._launcher_startup: return
        ru = self.lang['_lang_code'] == 'ru'; dialog = QDialog(self); dialog.setObjectName('firstRunTip'); dialog.setWindowTitle('Быстрый старт' if ru else 'Quick start'); dialog.resize(560, 330)
        layout = QVBoxLayout(dialog); title = QLabel('Добро пожаловать в SCS Tools Manager' if ru else 'Welcome to SCS Tools Manager'); title.setObjectName('toolName'); body = QLabel(('Ctrl+K открывает поиск и команды из любой вкладки.\n\nСкачивайте проверенные инструменты в Магазине, запускайте их из Скачанных и создавайте собственные во вкладке Создание.\n\nВкладка Дебаг содержит журнал, проверку приложения и создание отчёта об ошибке.') if ru else ('Ctrl+K opens search and commands from any page.\n\nDownload verified tools from Store, launch them from Downloaded and build your own tools on Create.\n\nDebug contains the event log, integrity check and problem report creation.')); body.setWordWrap(True); body.setObjectName('pageSubtitle'); hide = QCheckBox('Больше не показывать' if ru else 'Do not show again'); hide.setChecked(True); close = QPushButton(self.lang['close']); close.setObjectName('primaryButton'); close.clicked.connect(dialog.accept); layout.addWidget(title); layout.addWidget(body, 1); layout.addWidget(hide); layout.addWidget(close, alignment=Qt.AlignRight); dialog.exec_()
        if hide.isChecked(): self.settings['first_run_tip'] = False; save_settings(self.settings)
    def cleanup_tool_trash(self):
        trash = self.registry.tools_dir / '.trash'
        if not trash.is_dir(): return
        cutoff = time.time() - 5 * 24 * 60 * 60
        for path in trash.glob('*.zip'):
            try:
                if path.stat().st_mtime < cutoff: path.unlink()
            except OSError: pass
    def notification_items(self):
        value = self.settings.get('notifications', [])
        return value if isinstance(value, list) else []
    def apply_shortcuts(self):
        defaults = DEFAULT_SETTINGS['shortcuts']; configured = self.settings.get('shortcuts', {})
        if not isinstance(configured, dict): configured = {}
        values = {**defaults, **configured}; self.settings['shortcuts'] = values
        self.command_shortcut.setKey(QKeySequence(values['command_palette']))
        for key, shortcut in self.navigation_shortcuts.items(): shortcut.setKey(QKeySequence(values.get(key, '')))
    def add_notification(self, message, kind='info', source_page=None, key=None, modal=False):
        items = self.notification_items()
        if key and any(isinstance(item, dict) and item.get('key') == key for item in items[-50:]): return
        items.append({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'message': str(message), 'kind': kind, 'source': source_page or '', 'key': key or ''})
        self.settings['notifications'] = items[-100:]
        save_settings(self.settings); self.update_notification_button()
        dashboard = getattr(self, 'pages', {}).get('dashboard')
        if dashboard and hasattr(dashboard, 'refresh_overview'): dashboard.refresh_overview()
        if modal and source_page == getattr(self, 'current_key', None):
            function = QMessageBox.warning if kind in ('warning', 'error') else QMessageBox.information
            function(self, self.lang['error'] if kind == 'error' else ('Уведомление' if self.lang['_lang_code'] == 'ru' else 'Notification'), str(message))
    def update_notification_button(self):
        count = len(self.notification_items())
        self.notification_button.setText(('Уведомления' if self.lang['_lang_code'] == 'ru' else 'Notifications') + (f'  {count}' if count else ''))
    def show_notifications(self):
        ru = self.lang['_lang_code'] == 'ru'; dialog = QDialog(self); dialog.setWindowTitle('Центр уведомлений' if ru else 'Notification center'); dialog.resize(700, 500)
        layout = QVBoxLayout(dialog); tree = QTreeWidget(); tree.setObjectName('projectTree'); tree.setHeaderLabels(['Время' if ru else 'Time', 'Тип' if ru else 'Type', 'Сообщение' if ru else 'Message']); tree.setColumnWidth(0, 145); tree.setColumnWidth(1, 85)
        for item in reversed(self.notification_items()):
            if isinstance(item, dict): tree.addTopLevelItem(QTreeWidgetItem([str(item.get('time', '')), str(item.get('kind', '')), str(item.get('message', ''))]))
        layout.addWidget(tree, 1); buttons = QHBoxLayout(); clear = QPushButton('Очистить' if ru else 'Clear'); close = QPushButton(self.lang['close']); buttons.addWidget(clear); buttons.addStretch(); buttons.addWidget(close); layout.addLayout(buttons)
        clear.clicked.connect(lambda: (self.settings.__setitem__('notifications', []), save_settings(self.settings), tree.clear(), self.update_notification_button())); close.clicked.connect(dialog.accept); dialog.exec_()
    def remember_open_tools(self):
        running = self.pages.get('running'); uids = []
        if running:
            for index in range(running.tool_pages.count()):
                view = running.tool_pages.widget(index)
                if isinstance(view, ToolView): uids.append(view.tool.uid)
        self.settings['open_tool_uids'] = uids; save_settings(self.settings)
    def restore_open_tools(self):
        wanted = self.settings.get('open_tool_uids', [])
        if not isinstance(wanted, list) or not wanted: return
        tools = {tool.uid: tool for tool in self.registry.scan()}
        for uid in wanted:
            tool = tools.get(str(uid))
            if tool: self.pages['running'].open_tool(tool)
        if self.pages['running'].tool_pages.count(): self.buttons['running'][0].show()
    def continue_work(self):
        recent = self.settings.get('tool_last_opened', {})
        if not isinstance(recent, dict) or not recent: return
        available = {tool.uid: tool for tool in self.registry.scan()}
        ordered = sorted(recent.items(), key=lambda item: item[1] if isinstance(item[1], (int, float)) else 0)
        for uid, _opened_at in ordered:
            tool = available.get(str(uid))
            if tool: self.open_tool(tool)
        # "Open all" is an inbox-like action: once consumed, the recent list
        # starts afresh. open_tool() records entries, so clear it last.
        self.settings['tool_last_opened'] = {}
        save_settings(self.settings)
        self.pages['dashboard'].refresh_overview()
        if self.pages['running'].tool_pages.count(): self.show_page('running')
    def command_palette_entries(self):
        """Build live commands from the current navigation and tool library."""
        ru = self.lang['_lang_code'] == 'ru'
        entries = []
        for key in ('dashboard', 'store', 'library', 'running', 'create', 'debug', 'settings'):
            name = self.lang['tab_' + key]
            entries.append((name, 'page tab вкладка открыть open ' + key, lambda target=key: self.show_page(target)))
        entries.append(('Обновить каталог' if ru else 'Refresh catalog', 'store магазин каталог refresh',
                        lambda: (self.show_page('store'), self.pages['store'].refresh_catalog(clear_page_cache=True))))
        entries.append(('Создать новый инструмент' if ru else 'Create a new tool', 'create editor редактор новый инструмент',
                        lambda: (self.show_page('create'), self.pages['create'].start_new_tool())))
        for key, name in self.theme_choices():
            entries.append(((('Тема: ' if ru else 'Theme: ') + name), 'theme тема settings настройки ' + key,
                            lambda theme=key: self.change_theme(theme)))
        for tool in self.registry.scan():
            entries.append(((('Инструмент: ' if ru else 'Tool: ') + tool.name),
                            'tool инструмент downloaded скачанные ' + tool.uid + ' ' + tool.archive.name,
                            lambda item=tool: self.open_tool(item)))
        running = self.pages['running']
        for index in range(running.tab_bar.count()):
            name = running.tab_bar.tabText(index)
            entries.append(((('Открытая вкладка: ' if ru else 'Open tab: ') + name),
                            'running запущенные tab вкладка ' + name,
                            lambda target=index: (self.show_page('running'), running.tab_bar.setCurrentIndex(target))))
        return entries
    def command_palette_file_entries(self):
        ru = self.lang['_lang_code'] == 'ru'
        entries = []
        for tool in self.registry.scan():
            try:
                with zipfile.ZipFile(tool.archive) as archive:
                    names = [item.filename for item in archive.infolist() if not item.is_dir()]
            except (OSError, zipfile.BadZipFile):
                continue
            for name in names:
                entries.append(((('Файл: ' if ru else 'File: ') + tool.name + ' / ' + name),
                                'file файл archive архив ' + tool.archive.name,
                                lambda path=tool.archive.parent: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))))
        return entries
    def show_command_palette(self):
        ru = self.lang['_lang_code'] == 'ru'
        dialog = QDialog(self); dialog.setObjectName('commandPalette'); dialog.setWindowTitle('Поиск и команды' if ru else 'Search and commands')
        dialog.resize(600, 480)
        layout = QVBoxLayout(dialog); layout.setContentsMargins(18, 18, 18, 18); layout.setSpacing(10)
        query = QLineEdit(); query.setObjectName('input'); query.setPlaceholderText('Найти инструмент, вкладку, действие или тему…' if ru else 'Find a tool, tab, action or theme…')
        results = QTreeWidget(); results.setObjectName('projectTree'); results.setHeaderHidden(True); results.setRootIsDecorated(False)
        layout.addWidget(query); layout.addWidget(results, 1)
        entries = self.command_palette_entries()
        visible_entries = []
        file_entries = None
        activated = False
        def refresh():
            nonlocal file_entries
            words = query.text().casefold().split()
            results.clear()
            visible_entries.clear()
            if len(query.text().strip()) >= 3:
                if file_entries is None: file_entries = self.command_palette_file_entries()
            for label, terms, action in entries + ((file_entries or []) if words else []):
                searchable = (label + ' ' + terms).casefold()
                if all(word in searchable for word in words):
                    item = QTreeWidgetItem([label]); item.setData(0, Qt.UserRole, len(visible_entries)); results.addTopLevelItem(item)
                    visible_entries.append(action)
                    if len(visible_entries) >= 100: break
            if results.topLevelItemCount(): results.setCurrentItem(results.topLevelItem(0))
        def activate(item, _column=0):
            nonlocal activated
            if activated: return
            if item is None: return
            action = visible_entries[item.data(0, Qt.UserRole)]
            activated = True
            dialog.accept(); QTimer.singleShot(0, action)
        query.textChanged.connect(refresh)
        query.returnPressed.connect(lambda: activate(results.currentItem()))
        results.itemActivated.connect(activate)
        results.itemDoubleClicked.connect(activate)
        refresh(); query.setFocus(); dialog.exec_()
    def sync_full_tabs(self):
        running = self.pages["running"]; self.full_tabs.blockSignals(True)
        while self.full_tabs.count(): self.full_tabs.removeTab(0)
        for index in range(running.tab_bar.count()): self.full_tabs.addTab(running.tab_bar.tabText(index))
        self.full_tabs.setCurrentIndex(running.tab_bar.currentIndex()); self.full_tabs.blockSignals(False); QTimer.singleShot(0, self.update_full_tab_scrollbar)
    def update_full_tab_scrollbar(self):
        self.full_tabs.setFixedWidth(max(1, self.full_tabs.sizeHint().width() - 3))
        overflow = self.full_tabs.width() > self.full_tab_scroll.viewport().width()
        scroll_bar = self.full_tab_scroll.horizontalScrollBar(); scroll_bar.setVisible(overflow)
        bar_height = scroll_bar.sizeHint().height() if overflow else 0
        self.full_tab_scroll.setFixedHeight(34 + bar_height)
        self.full_toolbar_target = 42 + bar_height
        self.ensure_full_current_tab_visible()
    def ensure_full_current_tab_visible(self):
        index = self.full_tabs.currentIndex()
        if index < 0: return
        rect = self.full_tabs.tabRect(index); viewport = self.full_tab_scroll.viewport().width(); scroll = self.full_tab_scroll.horizontalScrollBar()
        if rect.left() < scroll.value(): scroll.setValue(rect.left())
        elif rect.right() >= scroll.value() + viewport: scroll.setValue(rect.right() - viewport + 1)
    def set_application_fullscreen(self, view, enabled):
        if not view: return
        self.full_view = view if enabled else None; self.full_mode_kind = "tool" if enabled else None; view.set_application_fullscreen(enabled); self.full_tab_scroll.setVisible(True)
        self.sidebar.setVisible(not enabled); self.header_box.setVisible(not enabled); self.full_reveal.setVisible(enabled)
        self.pages["running"].title.setVisible(not enabled); self.pages["running"].subtitle.setVisible(not enabled); self.pages["running"].tab_scroll.setVisible(not enabled)
        self.root_layout.setContentsMargins(0, 0, 0, 0) if enabled else self.root_layout.setContentsMargins(20, 12, 20, 14)
        self.content.setSpacing(0 if enabled else 6)
        if enabled: self.sync_full_tabs(); self.full_hover_timer.start()
        else:
            self.full_hover_timer.stop(); self.full_toolbar.hide(); self.full_toolbar.setMaximumHeight(0); self.full_toolbar.setGraphicsEffect(None); self.full_toolbar_effect = None
    def set_creation_fullscreen(self, panel, enabled):
        if enabled:
            self.full_view = panel; self.full_mode_kind = "creation"; self.sidebar.hide(); self.header_box.hide(); self.pages["create"].title.hide(); self.pages["create"].subtitle.hide(); self.full_tab_scroll.hide(); self.full_reveal.show(); self.root_layout.setContentsMargins(0, 0, 0, 0); self.content.setSpacing(0); self.full_hover_timer.start()
        else:
            self.full_view = None; self.full_mode_kind = None; self.sidebar.show(); self.header_box.show(); self.pages["create"].title.show(); self.pages["create"].subtitle.show(); self.full_tab_scroll.show(); self.full_reveal.hide(); self.full_hover_timer.stop(); self.full_toolbar.hide(); self.full_toolbar.setMaximumHeight(0); self.root_layout.setContentsMargins(20, 12, 20, 14); self.content.setSpacing(6)
    def exit_full_mode(self):
        if self.full_mode_kind == "creation": self.set_creation_fullscreen(self.full_view, False)
        elif self.full_mode_kind == "tool": self.set_application_fullscreen(self.full_view, False)
    def cursor_in_reveal_zone(self):
        point = self.centralWidget().mapFromGlobal(QCursor.pos())
        return 0 <= point.y() <= 100
    def check_full_hover(self):
        if not self.full_view: return
        if self.cursor_in_reveal_zone() or self.full_toolbar.underMouse(): self.show_full_toolbar()
        else: self.hide_full_toolbar()
    def show_full_toolbar(self):
        if not self.full_view: return
        target = getattr(self, "full_toolbar_target", 42)
        if getattr(self, "full_toolbar_showing", False): return
        if self.full_toolbar.isVisible() and getattr(self, "full_toolbar_effect", None) and self.full_toolbar_effect.opacity() >= .99: return
        if hasattr(self, "full_toolbar_anim"): self.full_toolbar_anim.stop()
        self.full_toolbar_hiding = False; self.full_toolbar_showing = True; self.sync_full_tabs(); self.full_toolbar.setMaximumHeight(target); self.full_toolbar.show()
        self.full_toolbar_effect = QGraphicsOpacityEffect(self.full_toolbar); self.full_toolbar.setGraphicsEffect(self.full_toolbar_effect); self.full_toolbar_effect.setOpacity(0.0)
        QTimer.singleShot(0, self.start_full_toolbar_fade_in)
    def start_full_toolbar_fade_in(self):
        if not self.full_view or not getattr(self, "full_toolbar_effect", None): return
        self.full_toolbar_anim = QPropertyAnimation(self.full_toolbar_effect, b"opacity", self); self.full_toolbar_anim.setDuration(280); self.full_toolbar_anim.setStartValue(0.0); self.full_toolbar_anim.setEndValue(1.0); self.full_toolbar_anim.setEasingCurve(QEasingCurve.InOutCubic); self.full_toolbar_anim.finished.connect(lambda: setattr(self, "full_toolbar_showing", False)); self.full_toolbar_anim.start()
    def hide_full_toolbar(self):
        if not self.full_view or self.cursor_in_reveal_zone() or self.full_reveal.underMouse() or self.full_toolbar.underMouse(): return
        if not self.full_toolbar.isVisible() or self.full_toolbar.maximumHeight() == 0: return
        if getattr(self, "full_toolbar_hiding", False): return
        if hasattr(self, "full_toolbar_anim"): self.full_toolbar_anim.stop()
        self.full_toolbar_showing = False; self.full_toolbar_hiding = True
        effect = getattr(self, "full_toolbar_effect", None)
        if not effect: effect = QGraphicsOpacityEffect(self.full_toolbar); self.full_toolbar.setGraphicsEffect(effect); self.full_toolbar_effect = effect
        self.full_toolbar_anim = QPropertyAnimation(effect, b"opacity", self); self.full_toolbar_anim.setDuration(280); self.full_toolbar_anim.setStartValue(effect.opacity()); self.full_toolbar_anim.setEndValue(0.0); self.full_toolbar_anim.setEasingCurve(QEasingCurve.InOutCubic); self.full_toolbar_anim.finished.connect(self.finish_hide_full_toolbar); self.full_toolbar_anim.start()
    def finish_hide_full_toolbar(self):
        effect = getattr(self, "full_toolbar_effect", None)
        if effect and effect.opacity() <= .01:
            self.full_toolbar.hide(); self.full_toolbar.setMaximumHeight(0); self.full_toolbar.setGraphicsEffect(None); self.full_toolbar_effect = None; self.full_toolbar_hiding = False
    def scroll_area_under_cursor(self, event, watched):
        """Return the innermost scroll area physically below the mouse cursor."""
        point = QCursor.pos()
        cursor_widget = QApplication.widgetAt(point)
        seen = set()
        for origin in (cursor_widget, watched):
            widget = origin
            while widget is not None:
                # Editors, trees and text browsers already implement their own
                # wheel handling.  Do not consume their event for the page
                # scroll area behind them.
                if isinstance(widget, QAbstractScrollArea) and not isinstance(widget, QScrollArea):
                    viewport = widget.viewport()
                    if viewport.rect().contains(viewport.mapFromGlobal(point)):
                        return None
                if isinstance(widget, QScrollArea) and id(widget) not in seen:
                    seen.add(id(widget))
                    viewport = widget.viewport()
                    inside = viewport.rect().contains(viewport.mapFromGlobal(point))
                    bar = widget.verticalScrollBar()
                    if inside and bar.maximum() > bar.minimum():
                        return widget
                widget = widget.parentWidget() if hasattr(widget, "parentWidget") else None
        return None

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            area = self.scroll_area_under_cursor(event, watched)
            if area is not None:
                bar = area.verticalScrollBar()
                if bar.maximum() > bar.minimum():
                    pixels = event.pixelDelta().y()
                    delta = pixels if pixels else (event.angleDelta().y() / 120.0) * bar.singleStep() * 3
                    if delta:
                        animation = self._scroll_animations.get(area)
                        previous = self._scroll_targets.get(area, bar.value()) if animation and animation.state() == QPropertyAnimation.Running else bar.value()
                        target = max(bar.minimum(), min(bar.maximum(), round(previous - delta)))
                        if target != bar.value():
                            if animation is None:
                                animation = QPropertyAnimation(bar, b"value", area); animation.setEasingCurve(QEasingCurve.OutCubic); self._scroll_animations[area] = animation
                            animation.stop(); animation.setStartValue(bar.value()); animation.setEndValue(target); animation.setDuration(170); self._scroll_targets[area] = target; animation.start()
                            return True
        if getattr(self, "full_view", None) and event.type() == QEvent.MouseMove:
            if self.cursor_in_reveal_zone(): self.show_full_toolbar()
            elif not self.full_toolbar.underMouse(): QTimer.singleShot(0, self.hide_full_toolbar)
        if watched in (getattr(self, "full_reveal", None), getattr(self, "full_toolbar", None)):
            if event.type() == QEvent.Enter: self.show_full_toolbar()
            elif event.type() == QEvent.Leave: QTimer.singleShot(0, self.hide_full_toolbar)
        return super().eventFilter(watched, event)
    def update_text(self):
        self.nav_title.setText(self.lang["navigation"])
        self.exit_button.setText(self.lang["exit_application"])
        self.full_exit.setText(self.lang["exit_application"]); self.full_exit_mode.setText(self.lang["exit_full_screen"])
        self.update_notification_button()
        for key, (button, icon) in self.buttons.items(): button.setText(f"{icon}   {self.lang['tab_' + ('store' if key == 'store' else 'library' if key == 'library' else key)]}")
        for page in self.pages.values(): page.update_text(self.lang)
    def change_language(self, code):
        self.lang = languages.get_lang(code); set_log_language(code); self.settings["language"] = code; save_settings(self.settings); self.update_text()
    def theme_choices(self):
        if self.lang["_lang_code"] == "ru": return (("dark", "Тёмная"), ("light", "Светлая"), ("ember", "Тёплая тёмная"), ("ocean", "Океанская"), ("lavender", "Лавандовая"), ("fire", "Огненная"), ("spectrum_dark", "Радужная тёмная"), ("spectrum_light", "Радужная светлая"))
        return (("dark", "Dark"), ("light", "Light"), ("ember", "Warm dark"), ("ocean", "Ocean"), ("lavender", "Lavender"), ("fire", "Fire"), ("spectrum_dark", "Spectrum dark"), ("spectrum_light", "Spectrum light"))
    def apply_theme(self, theme, persist=True):
        theme = theme if theme in THEME_STYLES else "dark"; self.settings["theme"] = theme
        density = self.settings.get('ui_density', 'normal')
        QApplication.instance().setStyleSheet(STYLE + THEME_STYLES[theme] + DENSITY_STYLES.get(density, ''))
        self.apply_density_layout({'compact': .88, 'normal': 1.0, 'large': 1.16}.get(density, 1.0))
        background = self.centralWidget()
        if isinstance(background, AnimatedBackground): background.set_theme(theme)
        editor = getattr(self.pages.get('create'), 'html_editor', None)
        if editor and hasattr(editor, 'update_theme'): editor.update_theme(theme)
        running = self.pages.get('running')
        if running and hasattr(running, 'update_theme'): running.update_theme(theme)
        if persist: save_settings(self.settings)
    def apply_density_layout(self, factor):
        """Scale typography, layout spacing and persistent chrome together."""
        app = QApplication.instance(); font = QFont(self._base_application_font)
        if font.pointSizeF() > 0: font.setPointSizeF(max(7.0, self._base_application_font.pointSizeF() * factor))
        app.setFont(font)
        for layout in self.findChildren(QLayout):
            if layout not in self._density_layout_bases:
                margins = layout.contentsMargins()
                self._density_layout_bases[layout] = (margins.left(), margins.top(), margins.right(), margins.bottom(), layout.spacing())
            left, top, right, bottom, spacing = self._density_layout_bases[layout]
            layout.setContentsMargins(*(max(0, round(value * factor)) for value in (left, top, right, bottom)))
            if spacing >= 0: layout.setSpacing(max(0, round(spacing * factor)))
        self.sidebar.setFixedWidth(round(225 * factor))
        for button in (self.exit_button, self.lang_button, self.notification_button): button.setFixedHeight(round(34 * factor))
        for tabs in (self.full_tabs, self.pages['running'].tab_bar): tabs.setFixedHeight(round(34 * factor))
        QTimer.singleShot(0, self.pages['running'].update_tab_scrollbar)
    def change_theme(self, theme):
        self.apply_theme(theme, persist=True)
    def is_verified_original(self, tool):
        store = self.pages.get("store")
        if not getattr(store, "verified_loaded", False): return False
        verified = next((item for item in store.tools if str(item.get("uid", "")) == tool.uid), None)
        if not isinstance(verified, dict): return False
        expected_hash = str(verified.get("sha256", "")).lower()
        expected_name = download_archive_name(verified.get("download_url", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or tool.archive.name != expected_name: return False
        try: return file_sha256(tool.archive).lower() == expected_hash
        except OSError: return False
    def confirm_unknown_tool(self, message):
        box = QMessageBox(self); box.setIcon(QMessageBox.Warning); box.setWindowTitle(self.lang['error']); box.setText(message)
        open_button = box.addButton("Открыть" if self.lang['_lang_code'] == 'ru' else "Open", QMessageBox.AcceptRole)
        box.addButton("Отмена" if self.lang['_lang_code'] == 'ru' else "Cancel", QMessageBox.RejectRole); box.exec_()
        return box.clickedButton() is open_button
    def show_manager_window(self):
        was_launcher_only = getattr(self, '_launcher_startup', False); self._launcher_startup = False
        self.showNormal(); self.raise_(); self.activateWindow()
        if was_launcher_only: QTimer.singleShot(0, self.restore_open_tools)
    def open_detached_tool(self, tool, source_view=None, launcher_mode=False):
        # Chromium must be constructed under its final top-level ancestor.
        # Dispose an embedded view before creating the detached one so only a
        # single renderer remains alive and the separate window stays smooth.
        if source_view is not None: self.pages['running'].remove_for_detach(source_view)
        dialog = QDialog(self, Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint); dialog.setWindowTitle(tool.name); dialog.resize(1050, 760); dialog.setMinimumSize(720, 480); dialog.setAttribute(Qt.WA_DeleteOnClose); dialog.setWindowIcon(self.windowIcon()); layout = QVBoxLayout(dialog); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        if launcher_mode:
            launcher_bar = QFrame(); launcher_bar.setObjectName('detachedLauncherBar'); launcher_layout = QHBoxLayout(launcher_bar); launcher_layout.setContentsMargins(9, 7, 9, 7); title = QLabel(tool.name); title.setObjectName('fieldLabel'); open_manager = QPushButton('Открыть SCS Tools Manager' if self.lang['_lang_code'] == 'ru' else 'Open SCS Tools Manager'); open_manager.setObjectName('outlineButton'); open_manager.clicked.connect(self.show_manager_window); launcher_layout.addWidget(title); launcher_layout.addStretch(); launcher_layout.addWidget(open_manager); layout.addWidget(launcher_bar)
        view = ToolView(self, tool, detached=True); layout.addWidget(view, 1); self.detached_windows.append(dialog)
        def cleanup(*_):
            if hasattr(view, 'exe_host'): view.exe_host.stop()
            if hasattr(view, 'browser_server'): view.browser_server.close()
            if hasattr(view, 'web'): view.web.stop(); view.web.setUrl(QUrl('about:blank'))
            if dialog in self.detached_windows: self.detached_windows.remove(dialog)
            background = self.centralWidget()
            if isinstance(background, AnimatedBackground) and not self.detached_windows: background.set_animation_enabled(getattr(self, 'current_key', '') != 'running')
            if launcher_mode and not self.isVisible() and not getattr(self, '_shutdown_started', False): QTimer.singleShot(0, self.close)
        dialog.destroyed.connect(cleanup); dialog.show(); view.set_runtime_active(True)
        background = self.centralWidget()
        if isinstance(background, AnimatedBackground): background.set_animation_enabled(False)
        if launcher_mode: self.hide()
        return dialog
    def open_tool(self, tool, warn_unknown=True, detached=False):
        if warn_unknown and not self.is_created_tool(tool.uid) and not self.is_verified_original(tool):
            if not self.confirm_unknown_tool("Открывается неизвестный инструмент: он не найден среди проверенных архивов Verified Tools." if self.lang['_lang_code'] == 'ru' else "An unknown tool is being opened: it was not found among Verified Tools archives."): return
        duplicates = [item for item in self.registry.scan() if item.uid == tool.uid]
        if len(duplicates) > 1 and not self.is_verified_original(tool):
            if detached: self.show_manager_window()
            QMessageBox.warning(self, self.lang['error'], "Невозможно запустить инструмент: этот ZIP является дубликатом UID. Запуск разрешён только для оригинала из Verified Tools." if self.lang['_lang_code'] == 'ru' else "Cannot launch the tool: this ZIP duplicates a UID. Only the original from Verified Tools may run.")
            return
        if detached: self.open_detached_tool(tool, launcher_mode=True)
        else: self.buttons["running"][0].show(); self.pages["running"].open_tool(tool)
        recent = self.settings.get('tool_last_opened', {})
        if not isinstance(recent, dict): recent = {}
        recent[tool.uid] = time.time()
        self.settings['tool_last_opened'] = recent
        save_settings(self.settings)
        if not detached: self.show_page("running")
    def show_install_dialog(self, tool):
        duplicates = [item for item in self.registry.scan() if item.uid == tool.uid]
        if len(duplicates) > 1 and not self.is_verified_original(tool):
            QMessageBox.warning(self, self.lang['error'], "Нельзя установить дубликат UID. Установите оригинальный архив из Verified Tools." if self.lang['_lang_code'] == 'ru' else "A duplicate UID cannot be installed. Install the original archive from Verified Tools.")
            return
        return InstallationDialog(self, tool).exec_()
    def tool_installed(self, uid):
        store = self.pages.get('store')
        if store:
            for entry in store.tools:
                if str(entry.get('uid', '')).lower() == uid.lower() and entry.get('_store_state') == 'open': entry['_store_state'] = 'installed'
            store.populate()
            if store.tool_page: store.tool_page.update_language()
        self.pages['library'].refresh(); self.pages['dashboard'].refresh_overview()
    def uninstall_downloaded_tool(self, uid):
        """Remove only installation launchers; keep the downloaded ZIP intact."""
        if not is_installed(uid): return
        try: uninstall_tool(uid)
        except OSError as error:
            QMessageBox.warning(self, self.lang['error'], str(error)); return
        store = self.pages.get('store')
        if store:
            for entry in store.tools:
                if str(entry.get('uid', '')).lower() == uid.lower() and entry.get('_store_state') == 'installed': entry['_store_state'] = 'open'
            store.refresh_cards()
            if store.tool_page: store.tool_page.update_language()
        self.pages['library'].refresh(); self.pages['dashboard'].refresh_overview()
    def register_created_tool(self, uid):
        if not isinstance(uid, str) or not uid: return
        created = self.settings.get('created_tools')
        if not isinstance(created, list): created = []
        if uid.lower() not in {str(item).lower() for item in created}:
            created.append(uid); self.settings['created_tools'] = created; save_settings(self.settings)
    def delete_tool_by_uid(self, uid):
        matches = [tool for tool in self.registry.scan() if tool.uid == uid]
        if not matches:
            QMessageBox.information(self, self.lang['error'], "Инструмент с указанным UID не найден." if self.lang['_lang_code'] == 'ru' else "No tool with this UID was found.")
            return
        self.delete_tool(matches[0])
    def move_tool_to_trash(self, tool):
        """Move a downloaded ZIP to the recoverable library trash."""
        trash = self.registry.tools_dir / '.trash'; trash.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        destination = trash / f'{stamp}__{tool.archive.name}'
        number = 2
        while destination.exists():
            destination = trash / f'{stamp}_{number}__{tool.archive.name}'; number += 1
        shutil.move(str(tool.archive), str(destination))
        return destination
    def delete_tool(self, tool):
        box = QMessageBox(QMessageBox.Warning, self.lang['error'], "Переместить ZIP-архив инструмента в корзину?" if self.lang['_lang_code'] == 'ru' else "Move this tool ZIP archive to Trash?", QMessageBox.Yes | QMessageBox.Cancel, self)
        box.button(QMessageBox.Yes).setText("Удалить" if self.lang['_lang_code'] == 'ru' else "Delete"); box.button(QMessageBox.Cancel).setText("Отмена" if self.lang['_lang_code'] == 'ru' else "Cancel")
        remove_installation = QCheckBox("Деинсталлировать: удалить .scstool и ярлыки" if self.lang['_lang_code'] == 'ru' else "Uninstall: remove the .scstool file and shortcuts")
        box.setCheckBox(remove_installation)
        if box.exec_() != QMessageBox.Yes: return
        try:
            self.move_tool_to_trash(tool)
        except OSError as error:
            QMessageBox.warning(self, self.lang['error'], str(error)); return
        if remove_installation.isChecked(): uninstall_tool(tool.uid)
        self.registry.cleanup_tool(tool)
        store = self.pages.get('store')
        if store:
            store.installed_hashes = None
            for entry in store.tools:
                if entry.get('uid') == tool.uid: entry['_store_state'] = 'download'
            # Keep the already-loaded banner pixmaps; only state/buttons change.
            store.refresh_cards()
            if store.tool_page: store.tool_page.update_language()
        create = self.pages.get('create')
        if create and create.uid.text().strip() == tool.uid:
            create.update_action_text()
        self.pages['library'].refresh(); self.pages['dashboard'].refresh_overview()
        self.add_notification(f'{tool.name} перемещён в корзину на 5 дней.' if self.lang['_lang_code'] == 'ru' else f'{tool.name} moved to trash for 5 days.', 'info', 'library')
    def is_created_tool(self, uid):
        return isinstance(uid, str) and uid.lower() in {str(item).lower() for item in self.settings.get('created_tools', []) if isinstance(item, str)}
    def find_tool_by_launcher_uid(self, uid):
        return next((item for item in self.registry.scan() if launcher_uid(item.uid) == uid.lower()), None)
    def verified_launcher_entry(self, uid, entries=None):
        source = entries if isinstance(entries, list) else self.pages['store'].tools
        for item in source:
            if not isinstance(item, dict): continue
            try:
                if launcher_uid(str(item.get('uid', ''))) == uid.lower(): return item
            except ValueError: continue
        return None
    def open_launcher_tool(self, tool, verified=False, unverified=False):
        if unverified and not self.is_created_tool(tool.uid):
            if not self.confirm_unknown_tool("Инструмент не найден в Verified Tools и был восстановлен из .scstool со встроенным ZIP. Он считается непроверенным." if self.lang['_lang_code'] == 'ru' else "The tool was not found in Verified Tools and was restored from a one-file .scstool. It is considered unverified."): self.show_manager_window(); return
        elif not verified and not self.is_created_tool(tool.uid):
            if not self.confirm_unknown_tool("Открывается неизвестный инструмент: он не найден в Verified Tools." if self.lang['_lang_code'] == 'ru' else "An unknown tool is being opened: it was not found in Verified Tools."): self.show_manager_window(); return
        self.open_tool(tool, warn_unknown=False, detached=True)
    def resolve_missing_launcher(self, data):
        uid = str(data['uid']).lower()
        def fetch():
            temporary = None
            try:
                request = Request(STORE_CATALOG_URL, headers={'User-Agent': 'SCS-Mega-Manager/1.0'})
                with open_remote(request, timeout=15) as response: payload = json.loads(response.read().decode('utf-8-sig'))
                entries = payload.get('verified_tools') if isinstance(payload, dict) else None
                entry = self.verified_launcher_entry(uid, entries)
                if not entry: raise LookupError
                expected_hash = str(entry.get('sha256', '')).lower(); archive_name = download_archive_name(entry.get('download_url', ''))
                if not re.fullmatch(r'[0-9a-f]{64}', expected_hash) or not archive_name: raise ValueError
                url = catalog_tool_url(entry, entry.get('download_url', ''))
                if not url: raise ValueError
                handle, temporary = tempfile.mkstemp(prefix='scs_launcher_', suffix='.zip', dir=str(self.registry.tools_dir)); os.close(handle)
                with open_remote(Request(url, headers={'User-Agent': 'SCS-Mega-Manager/1.0'}), timeout=30) as response, open(temporary, 'wb') as output:
                    shutil.copyfileobj(response, output)
                if file_sha256(temporary).lower() != expected_hash: raise ValueError
                with zipfile.ZipFile(temporary) as archive:
                    if archive.testzip() is not None: raise zipfile.BadZipFile
                    info = json.loads(archive.read('info.json').decode('utf-8-sig'))
                if launcher_uid(str(info.get('uid', ''))) != uid: raise ValueError
                target = self.registry.tools_dir / archive_name
                occupied = next((item for item in self.registry.scan() if item.archive == target), None)
                if occupied and launcher_uid(occupied.uid) != uid: raise ValueError
                os.replace(temporary, target); temporary = None; self.launcher_resolution_done.emit(data, 'verified')
            except LookupError:
                try: restore_embedded_archive(data, self.registry); self.launcher_resolution_done.emit(data, 'unverified')
                except (OSError, ValueError): self.launcher_resolution_done.emit(data, 'missing')
            except (OSError, URLError, ValueError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile): self.launcher_resolution_done.emit(data, 'error')
            finally:
                if temporary:
                    try: os.unlink(temporary)
                    except OSError: pass
        threading.Thread(target=fetch, daemon=True).start()
    def finish_launcher_resolution(self, data, status):
        if status in ('missing', 'error'):
            self.show_manager_window()
            QMessageBox.warning(self, self.lang['error'], "Не удалось найти и установить инструмент из .scstool." if self.lang['_lang_code'] == 'ru' else "Unable to find and install the tool from .scstool."); return
        tool = self.find_tool_by_launcher_uid(str(data['uid']))
        if not tool:
            self.show_manager_window()
            QMessageBox.warning(self, self.lang['error'], "Инструмент из .scstool не удалось установить." if self.lang['_lang_code'] == 'ru' else "The tool from .scstool could not be installed."); return
        self.pages['library'].refresh()
        if status == 'unverified':
            QMessageBox.warning(self, self.lang['error'], "Инструмент не найден в Verified Tools и был восстановлен из .scstool со встроенным ZIP. Он считается непроверенным." if self.lang['_lang_code'] == 'ru' else "The tool was not found in Verified Tools and was restored from a one-file .scstool. It is considered unverified.")
        if self.show_install_dialog(tool) == QDialog.Accepted: self.open_launcher_tool(tool, verified=status == 'verified', unverified=False)
        else: self.show_manager_window()
    def open_launcher_file(self, path):
        try:
            data = read_launcher(Path(path)); uid = str(data['uid']).lower()
        except (OSError, ValueError):
            self.show_manager_window()
            QMessageBox.warning(self, self.lang['error'], "Не удалось открыть файл .scstool." if self.lang['_lang_code'] == 'ru' else "Unable to open the .scstool file."); return
        tool = self.find_tool_by_launcher_uid(uid)
        if tool:
            store = self.pages['store']; trusted_offline = False
            for value in self.settings.get('downloaded', []) if isinstance(self.settings.get('downloaded'), list) else []:
                try:
                    if launcher_uid(str(value)) == uid: trusted_offline = True; break
                except ValueError: continue
            verified = bool(self.verified_launcher_entry(uid)) or (not store.verified_loaded and trusted_offline)
            self.open_launcher_tool(tool, verified=verified); return
        self.resolve_missing_launcher(data)
    def close_tool(self, view): self.pages["running"].close_tool(view)
    def closeEvent(self, event):
        # Do not tear down QtWebEngine and its Chromium helper processes in the
        # same event that closes the native window.  macOS can report that as
        # an unexpected termination even when the user pressed Exit normally.
        if not getattr(self, "_shutdown_ready", False):
            if not getattr(self, "_shutdown_started", False):
                self._shutdown_started = True
                self.remember_open_tools()
                backup_text_resources(self.registry.resources_dir)
                self.pages["running"].stop_all()
                for dialog in list(self.detached_windows): dialog.close()
                QTimer.singleShot(180, self.finish_shutdown)
            event.ignore()
            return
        self.registry.cleanup_all()
        event.accept()
    def finish_shutdown(self):
        self._shutdown_ready = True
        self.close()
    def show_page(self, key):
        if key not in self.pages: return
        previous_key = getattr(self, "current_key", None)
        background = self.centralWidget()
        if isinstance(background, AnimatedBackground):
            background.set_animation_enabled(key != "running")
        if previous_key == "running" and key != "running":
            self.pages["running"].set_runtime_active(False)
        previous_page = self.stack.currentWidget()
        if previous_page is not self.pages[key]: previous_page.setGraphicsEffect(None)
        self.current_key = key; self.settings['last_page'] = key; save_settings(self.settings); self.stack.setCurrentWidget(self.pages[key])
        for name, (b, _) in self.buttons.items(): b.setChecked(name == key)
        if key == 'dashboard': self.pages['dashboard'].refresh_overview()
        if key == 'debug': self.pages['debug'].refresh()
        if key == 'create': QTimer.singleShot(0, self.pages['create'].restore_authoring_session)
        if key in ("running", "create", "store"):
            # QWebEngineView нельзя безопасно смешивать с QGraphicsOpacityEffect:
            # при повторной перерисовке Chromium может оставить старый слой поверх нового.
            self.stack.currentWidget().setGraphicsEffect(None)
            if key == "running": self.pages["running"].activate_current()
            return
        effect = QGraphicsOpacityEffect(self.stack.currentWidget()); self.stack.currentWidget().setGraphicsEffect(effect); animation = QPropertyAnimation(effect, b"opacity", self); animation.setDuration(220); animation.setStartValue(0.15); animation.setEndValue(1.0); animation.setEasingCurve(QEasingCurve.OutCubic); animation.start(); self.animation = animation


def run(launcher_file=None):
    # Qt WebEngine attributes are configured by main.py before this module is
    # imported; changing them here would be too late on macOS.
    initial_settings = load_settings()
    set_log_language(initial_settings.get('language', 'ru'))
    configure_logging()
    # `run()` is also used directly during development; do not rely on
    # main.py to request Windows' 1 ms scheduler resolution.
    timer_resolution_requested = False
    try:
        ctypes.windll.winmm.timeBeginPeriod(1); timer_resolution_requested = True
    except Exception: pass
    try: ensure_manager_shortcut()
    except OSError: pass
    app = QApplication(sys.argv); app.setApplicationName("SCS Tools Manager")
    if sys.platform.startswith("linux") and hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("scs-tools-manager")
    app.setWindowIcon(QIcon(resource_path("assets", "icon.ico"))); app.installEventFilter(ActionLogger(app)); application_logger().info("Application started")
    app.setStyleSheet(STYLE); window = MainWindow(); window._launcher_startup = bool(launcher_file)
    if launcher_file: QTimer.singleShot(0, lambda: window.open_launcher_file(launcher_file))
    else: window.show()
    try: return app.exec_()
    finally:
        configure_detailed_tracing(False)
        if timer_resolution_requested:
            try: ctypes.windll.winmm.timeEndPeriod(1)
            except Exception: pass


STYLE = '''
QWidget#root { background:transparent; color:#ff6b5f; font: 10pt "Segoe UI"; }
QLabel { color:#ff6b5f; }
QCheckBox { color:#d8e0ea; }
QDialog, QMessageBox { background:#101216; color:#f4f6f8; }
QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox, QMessageBox QLabel, QMessageBox QCheckBox { color:#d8e0ea; }
QDialog QPushButton, QMessageBox QPushButton { background:#1d2129; color:#f4f6f8; border:1px solid #394150; border-radius:8px; padding:7px 12px; } QDialog QPushButton:hover, QMessageBox QPushButton:hover { background:#262d38; }
QMenu { background:#191d25; color:#f4f6f8; border:1px solid #303744; padding:5px; }
QMenu::item { padding:7px 24px 7px 12px; border-radius:6px; } QMenu::item:selected { background:#ee6b2f; color:white; }
QToolTip { background:#191d25; color:#f4f6f8; border:1px solid #465365; padding:5px; }
QScrollBar:vertical { background:#171c23; width:12px; margin:0; border:1px solid #303744; border-radius:6px; }
QScrollBar::handle:vertical { min-height:34px; margin:2px; background:#c95829; border:2px solid #171c23; border-radius:5px; }
QScrollBar::handle:vertical:hover { background:#ff814a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:0; background:transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { background:#171c23; height:12px; margin:0; border:1px solid #303744; border-radius:6px; }
QProgressBar { min-height:16px; color:#f4f6f8; background:#171c23; border:1px solid #303744; border-radius:8px; text-align:center; }
QProgressBar::chunk { background:#ee6b2f; border-radius:7px; }
QScrollBar::handle:horizontal { min-width:34px; margin:2px; background:#c95829; border:2px solid #171c23; border-radius:5px; }
QScrollBar::handle:horizontal:hover { background:#ff814a; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; border:0; background:transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
QFrame#sidebar, QFrame#card, QFrame#statCard, QFrame#toolCard, QFrame#emptyCard { background:#191d25; border:1px solid #303744; border-radius:18px; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #252c39, stop:1 #1a1e26); border:1px solid #3d4655; border-radius:22px; }
QFrame#toolInfoCard, QFrame#applicationCard { background:#191d25; border:1px solid #303744; border-radius:18px; }
QFrame#toolInfoCard { border-top:0; border-top-left-radius:0; border-top-right-radius:0; border-bottom-left-radius:18px; border-bottom-right-radius:18px; } QFrame#toolInfoCard[roundedTopRight="true"] { border-top-right-radius:18px; }
QFrame#applicationCard[fullscreen="true"] { border:0; border-radius:0; } QFrame#fullToolbar, QFrame#detachedLauncherBar { background:#101216; border-bottom:1px solid #303744; }
QLabel#brand { color:white; background:#ee6b2f; border-radius:12px; padding:12px; font:700 14pt "Segoe UI"; }
QLabel#navTitle, QLabel#eyebrow { color:#ee6b2f; font:700 8pt "Segoe UI"; letter-spacing:1px; padding:12px 7px 4px; }
QPushButton#navButton { color:#aab4c2; border:0; border-radius:12px; text-align:left; padding:12px; font:600 10pt "Segoe UI"; }
QPushButton#navButton:hover { background:#252b36; color:white; } QPushButton#navButton:checked { background:#ee6b2f; color:white; }
QLabel#topTitle { font:700 18pt "Segoe UI"; } QLabel#pageTitle { font:700 22pt "Segoe UI"; } QLabel#runningTitle { font:700 20pt "Segoe UI"; } QLabel#pageSubtitle, QLabel#muted, QLabel#heroText, QLabel#toolMeta, QLabel#emptyHint { color:#9aa4b2; }
QLabel#heroTitle { color:white; font:700 26pt "Segoe UI"; } QLabel#statValue { color:#ee6b2f; font:700 24pt "Segoe UI"; } QLabel#statLabel { color:#aab4c2; } QLabel#toolIcon { color:#ee6b2f; font:20pt "Segoe UI"; } QLabel#toolName, QLabel#emptyTitle { font:700 12pt "Segoe UI"; } QLabel#emptyIcon { color:#9aa4b2; font:42pt "Segoe UI"; } QLabel#fieldLabel { font:600 10pt "Segoe UI"; padding-top:6px; }
QLabel#dashboardSectionTitle { color:#ffffff; font:700 12pt "Segoe UI"; } QLabel#dashboardBody { color:#aeb9c6; font:11pt "Segoe UI"; line-height:1.35; } QLabel#heroText { font:11pt "Segoe UI"; }
QLabel#howToText { color:#c2cad6; font:11pt "Segoe UI"; line-height:1.45; } QLabel#howToText b { color:#ff8b5a; } QScrollArea#homeScroll, QScrollArea#homeScroll::viewport, QWidget#homeContent { background:transparent; border:0; }
QLabel#downloadedToolName { color:#ff6b5f; font:700 14pt "Segoe UI"; } QLabel#downloadedToolDescription { color:#aeb9c6; font:11pt "Segoe UI"; }
QDialog#installationDialog { background:#101216; color:#f4f6f8; } QDialog#installationDialog QCheckBox { color:#d7dde6; spacing:8px; padding:4px 0; } QDialog#installationDialog QCheckBox::indicator { width:17px; height:17px; } QDialog#installationDialog QCheckBox::indicator:unchecked { background:#151b24; border:1px solid #465365; border-radius:4px; } QDialog#installationDialog QCheckBox::indicator:checked { background:#ee6b2f; border:1px solid #ff935f; border-radius:4px; }
QDialog#toolInfoDialog { background:#101216; color:#f4f6f8; }
QDialog#toolInfoDialog QTreeWidget { background:#151b24; color:#e5eaf0; border:1px solid #303744; border-radius:12px; alternate-background-color:#191f29; }
QDialog#toolInfoDialog QTreeWidget::item { color:#e5eaf0; padding:5px 7px; }
QDialog#toolInfoDialog QTreeWidget::item:selected { background:#ee6b2f; color:#ffffff; }
QDialog#toolInfoDialog QHeaderView::section { background:#1d232d; color:#ffb082; border:0; border-bottom:1px solid #303744; padding:7px; }
QListWidget#trashList { background:#11141a; color:#f4f6f8; border:1px solid #384150; border-radius:12px; padding:6px; outline:0; }
QListWidget#trashList::item { padding:8px 10px; border-radius:7px; } QListWidget#trashList::item:hover { background:#262d38; } QListWidget#trashList::item:selected { background:#ee6b2f; color:#ffffff; }
QTextBrowser#publishText { background:#151b24; border:1px solid #303744; border-radius:12px; color:#c8d0db; padding:14px; } QTextBrowser#publishText h3 { color:#ff8b5a; } QTextBrowser#publishText a { color:#69c7ff; } QTextBrowser#publishText pre { color:#d8e1ee; background:#101216; border:1px solid #2b3441; padding:10px; }
QLabel#largeToolIcon { color:#ee6b2f; font:28pt "Segoe UI"; background:#11141a; border-radius:14px; } QScrollArea#createScroll, QScrollArea#createScroll::viewport, QScrollArea#runningTabScroll, QScrollArea#runningTabScroll::viewport, QTabBar#runningTabs, QStackedWidget#runningToolPages { background:transparent; border:0; margin:0; padding:0; } QTabBar::tab { background:#343b47; color:#c5cbd4; border:0; border-top-left-radius:10px; border-top-right-radius:10px; border-bottom-left-radius:0; border-bottom-right-radius:0; padding:9px 18px 9px 16px; margin-right:3px; } QTabBar::close-button { subcontrol-position:right; margin-right:6px; } QTabBar::tab:selected { background:#ee6b2f; color:white; } QScrollArea#runningTabScroll QScrollBar:horizontal { height:12px; margin:0; padding:0; background:#171c23; border:1px solid #303744; border-radius:6px; } QScrollArea#runningTabScroll QScrollBar::handle:horizontal { min-width:34px; margin:2px; background:#c95829; border:2px solid #171c23; border-radius:5px; } QScrollArea#runningTabScroll QScrollBar::handle:horizontal:hover { background:#ff814a; } QScrollArea#runningTabScroll QScrollBar::add-line:horizontal, QScrollArea#runningTabScroll QScrollBar::sub-line:horizontal, QScrollArea#runningTabScroll QScrollBar::left-arrow:horizontal, QScrollArea#runningTabScroll QScrollBar::right-arrow:horizontal { width:0px; height:0px; image:none; border:0; background:transparent; } QScrollArea#runningTabScroll QScrollBar::add-page:horizontal, QScrollArea#runningTabScroll QScrollBar::sub-page:horizontal { background:transparent; }
QPushButton#primaryButton, QPushButton#outlineButton, QPushButton#disabledButton, QPushButton#dangerButton { border-radius:12px; padding:10px 16px; font:600 10pt "Segoe UI"; } QPushButton#primaryButton { background:#ee6b2f; color:white; border:0; } QPushButton#primaryButton:hover { background:#ff834b; } QPushButton#outlineButton { background:#1d2129; border:1px solid #394150; color:white; } QPushButton#outlineButton:hover { background:#262d38; } QPushButton#dangerButton { background:#351b21; border:1px solid #76343d; color:#ffb9bd; } QPushButton#dangerButton:hover { background:#54232c; color:white; } QPushButton#historyButton { min-width:30px; max-width:30px; min-height:30px; max-height:30px; padding:0; background:#1d2129; border:1px solid #394150; border-radius:9px; color:white; font:700 12pt "Segoe UI"; } QPushButton#historyButton:hover { background:#262d38; } QPushButton#disabledButton { background:#323845; color:#aab4c2; border:0; }
QLineEdit#input, QComboBox#input { background:#11141a; border:1px solid #384150; border-radius:12px; padding:11px; color:#f4f6f8; } QLineEdit#input:focus, QComboBox#input:focus { border:1px solid #ee6b2f; } QComboBox QAbstractItemView { background:#1d2129; color:white; selection-background-color:#ee6b2f; } QTabWidget::pane { background:#191d25; border:1px solid #303744; border-radius:12px; } QTabWidget::tab-bar { left:0; } QTabBar::tab { background:#151920; color:#aab4c2; padding:7px 11px; margin-right:2px; } QTabBar::tab:selected { background:#262d38; color:white; } QScrollArea, QScrollArea::viewport { background:#191d25; } QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#11141a; border:1px solid #303744; border-radius:14px; color:#f4f6f8; padding:8px; } QLabel#stageNote { color:#f2cc60; background:#2b2617; border:1px solid #5c4d1d; border-radius:10px; padding:9px 12px; font:9pt "Segoe UI"; } QTreeWidget#projectTree { font:9pt "Segoe UI"; } QTreeWidget#projectTree::item { padding:5px 4px; border-radius:7px; } QTreeWidget#projectTree::item:selected { background:#ee6b2f; color:white; } QTreeWidget#projectTree::item:hover { background:#262d38; } QHeaderView::section { background:#191d25; color:#aab4c2; border:0; padding:7px; font:600 9pt "Segoe UI"; } QPlainTextEdit#projectEditor { font:9pt "Cascadia Code"; padding:10px; } QLabel#imagePreview { color:#aab4c2; }
QStackedWidget#createStage, QWidget#createStageOne, QWidget#authoringEditor, QSplitter, QSplitter::handle { background:transparent; }
QFrame#storeToolCard { background:transparent; border:0; }
QFrame#downloadedToolCard { background:transparent; border:0; }
QLabel#storeBannerIcon { background:rgba(8,12,18,185); border:1px solid #5b7188; border-radius:16px; color:#ee6b2f; font:24pt "Segoe UI"; }
QLabel#storeBannerTitle { color:#ffffff; font:700 18pt "Segoe UI"; }
QLabel#storeBannerDescription { color:#e1e7ee; font:10pt "Segoe UI"; }
QLabel#storeBannerMeta { color:#ffb082; font:600 9pt "Segoe UI"; }
QLabel#storeBannerUid { color:#aeb9c6; font:8pt "Cascadia Code"; }
QFrame#storeDetailCard { background:#191d25; border:1px solid #303744; border-radius:18px; }
QScrollArea#storeDetailScroll, QScrollArea#storeDetailScroll::viewport, QScrollArea#libraryScroll, QScrollArea#libraryScroll::viewport, QScrollArea#storeScroll, QScrollArea#storeScroll::viewport, QWidget#libraryContent, QWidget#storeContent { background:transparent; border:0; }
QWidget#storeDetailContent { background:#191d25; border:0; }
QLabel#storeDetailTitle { color:#ffffff; font:700 22pt "Segoe UI"; padding:2px 4px; }
QLabel#storeDetailIcon { background:#151c26; border:1px solid #44566b; border-radius:20px; color:#ee6b2f; font:30pt "Segoe UI"; }
QTextBrowser#storeReadme { background:#191d25; border:1px solid #303744; border-radius:14px; color:#e5eaf0; padding:14px; font:10pt "Segoe UI"; }
QLabel#storeDetailLinks { color:#aeb9c6; font:9pt "Segoe UI"; }
QLabel#duplicateUidWarning { color:#ff8f98; background:#351b21; border:1px solid #8a3c49; border-radius:8px; padding:5px 8px; font:600 8pt "Segoe UI"; }
QLabel#systemUnavailable { color:#f2cc60; font:600 8pt "Segoe UI"; padding:3px 0; }
QWidget#hashMismatchWarning { background:#302817; border:1px solid #806827; border-radius:8px; } QWidget#hashMismatchWarning QLabel { color:#ffd27a; font:600 8pt "Segoe UI"; } QPushButton#hashWarningUpdate { background:#6e4b13; border:1px solid #b7832d; color:#fff0c5; border-radius:7px; padding:5px 9px; font:600 8pt "Segoe UI"; } QPushButton#hashWarningUpdate:hover { background:#8c621a; color:white; }
QTabBar#runningTabs::tab { background:#343b47; color:#c5cbd4; border:0; border-top-left-radius:10px; border-top-right-radius:10px; border-bottom-left-radius:0; border-bottom-right-radius:0; padding:9px 18px 9px 16px; margin-right:3px; }
QTabBar#runningTabs::tab:selected { background:#ee6b2f; color:white; }
QWidget#runningToolView, QStackedWidget#runningToolPages { margin:0; padding:0; border:0; }
'''

# The base sheet remains the original dark appearance.  Alternative themes are
# appended to it, so widget-specific spacing and layout styling stays identical.
LIGHT_STYLE = '''
QCheckBox { color:#202733; }
QWidget#root { color:#202733; } QLabel { color:#202733; }
QFrame#sidebar, QFrame#card, QFrame#statCard, QFrame#toolCard, QFrame#emptyCard, QFrame#toolInfoCard, QFrame#applicationCard, QFrame#storeDetailCard { background:#ffffff; border-color:#d4dce6; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffffff, stop:1 #e7edf4); border-color:#ced8e4; }
QFrame#fullToolbar, QFrame#detachedLauncherBar { background:#f6f8fb; border-bottom-color:#d4dce6; }
QPushButton#navButton { color:#5c6878; } QPushButton#navButton:hover { background:#e8edf3; color:#202733; } QPushButton#navButton:checked { background:#ee6b2f; color:white; }
QLabel#pageSubtitle, QLabel#muted, QLabel#heroText, QLabel#toolMeta, QLabel#emptyHint, QLabel#statLabel, QLabel#dashboardBody, QLabel#howToText, QLabel#downloadedToolDescription, QLabel#storeDetailLinks, QLabel#storeBannerUid { color:#657386; }
QLabel#heroTitle, QLabel#dashboardSectionTitle, QLabel#storeDetailTitle { color:#202733; } QLabel#howToText b, QLabel#storeBannerMeta { color:#c95424; }
QPushButton#outlineButton, QPushButton#historyButton { background:#ffffff; border-color:#cbd5e1; color:#202733; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#e9eef4; }
QPushButton#disabledButton { background:#dde4ec; color:#7b8796; }
QPushButton#dangerButton { background:#fff0f1; border-color:#e6a6ad; color:#a1323e; } QPushButton#dangerButton:hover { background:#fbdadd; color:#7d202c; }
QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#ffffff; border-color:#cbd5e1; color:#202733; }
QComboBox QAbstractItemView { background:#ffffff; color:#202733; selection-background-color:#ee6b2f; }
QScrollArea, QScrollArea::viewport { background:#ffffff; } QScrollArea#homeScroll, QScrollArea#homeScroll::viewport, QScrollArea#storeDetailScroll, QScrollArea#storeDetailScroll::viewport, QScrollArea#libraryScroll, QScrollArea#libraryScroll::viewport, QScrollArea#storeScroll, QScrollArea#storeScroll::viewport { background:transparent; }
QScrollBar:vertical, QScrollBar:horizontal { background:#e4eaf1; border-color:#cbd5e1; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#d56434; border-color:#e4eaf1; } QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background:#ee6b2f; }
QTabWidget::pane { background:#ffffff; border-color:#d4dce6; } QTabBar::tab, QTabBar#runningTabs::tab { background:#e4eaf1; color:#536172; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#ee6b2f; color:white; }
QHeaderView::section { background:#f1f4f8; color:#536172; } QTextBrowser#publishText, QTextBrowser#storeReadme { background:#ffffff; border-color:#d4dce6; color:#27313d; } QTextBrowser#publishText pre { background:#f1f4f8; border-color:#d4dce6; color:#27313d; }
QDialog#installationDialog, QDialog#toolInfoDialog { background:#f6f8fb; color:#202733; } QDialog#toolInfoDialog QTreeWidget { background:#ffffff; color:#202733; border-color:#cbd5e1; alternate-background-color:#f2f5f8; } QDialog#toolInfoDialog QTreeWidget::item { color:#202733; } QDialog#toolInfoDialog QHeaderView::section { background:#eef2f6; color:#b64b23; border-bottom-color:#cbd5e1; }
QLabel#storeBannerIcon { background:rgba(255,255,255,205); border-color:#d2a08b; } QLabel#storeBannerTitle, QLabel#storeBannerDescription { color:white; } QLabel#largeToolIcon, QLabel#storeDetailIcon { background:#f3f6fa; border-color:#cbd5e1; }
QWidget#storeDetailContent { background:#ffffff; } QLabel#stageNote { color:#765400; background:#fff6d9; border-color:#e5c66b; }
QDialog#installationDialog QCheckBox { color:#354152; } QDialog#installationDialog QCheckBox::indicator:unchecked { background:#ffffff; border-color:#aebbc9; } QDialog#installationDialog QCheckBox::indicator:checked { background:#ee6b2f; border-color:#d56434; }
QLabel#duplicateUidWarning { background:#fff0f1; border-color:#e6a6ad; color:#a1323e; } QWidget#hashMismatchWarning { background:#fff7e5; border-color:#dfbd67; } QWidget#hashMismatchWarning QLabel { color:#79580a; } QPushButton#hashWarningUpdate { background:#f0c969; border-color:#c99a2f; color:#473300; }
QDialog, QMessageBox { background:#f6f8fb; color:#202733; } QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox, QMessageBox QLabel, QMessageBox QCheckBox { color:#202733; } QMenu { background:#ffffff; color:#202733; border-color:#cbd5e1; } QMenu::item:selected { background:#ee6b2f; color:white; } QToolTip { background:#ffffff; color:#202733; border-color:#cbd5e1; }
QDialog QPushButton, QMessageBox QPushButton { background:#ffffff; color:#202733; border-color:#cbd5e1; } QDialog QPushButton:hover, QMessageBox QPushButton:hover { background:#e9eef4; }
QListWidget#trashList { background:#ffffff; color:#202733; border-color:#cbd5e1; } QListWidget#trashList::item:hover { background:#e9eef4; } QListWidget#trashList::item:selected { background:#ee6b2f; color:white; }
'''

EMBER_STYLE = '''
QCheckBox { color:#ffe4d9; }
QWidget#root { color:#ffd2bd; } QLabel { color:#ffd2bd; }
QFrame#sidebar, QFrame#card, QFrame#statCard, QFrame#toolCard, QFrame#emptyCard, QFrame#toolInfoCard, QFrame#applicationCard, QFrame#storeDetailCard { background:#211816; border-color:#4d342d; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #33211d, stop:1 #211614); border-color:#674036; }
QFrame#fullToolbar, QFrame#detachedLauncherBar { background:#17110f; border-bottom-color:#4d342d; }
QPushButton#navButton { color:#d2a99a; } QPushButton#navButton:hover { background:#36231e; color:#fff1ea; }
QLabel#pageSubtitle, QLabel#muted, QLabel#heroText, QLabel#toolMeta, QLabel#emptyHint, QLabel#statLabel, QLabel#dashboardBody, QLabel#howToText, QLabel#downloadedToolDescription, QLabel#storeDetailLinks, QLabel#storeBannerUid { color:#c8a99f; }
QPushButton#outlineButton, QPushButton#historyButton { background:#281c19; border-color:#614139; color:#ffe4d9; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#38241f; }
QPushButton#dangerButton { background:#3b1c20; border-color:#91404b; color:#ffb6bd; } QPushButton#dangerButton:hover { background:#54232c; color:#ffffff; }
QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#17110f; border-color:#5a3a32; color:#ffe4d9; } QComboBox QAbstractItemView { background:#281c19; color:#ffe4d9; }
QScrollArea, QScrollArea::viewport { background:#211816; } QScrollBar:vertical, QScrollBar:horizontal { background:#211816; border-color:#4d342d; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#b84d30; border-color:#211816; }
QTabWidget::pane { background:#211816; border-color:#4d342d; } QTabBar::tab, QTabBar#runningTabs::tab { background:#38241f; color:#d4b5aa; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#d95e32; color:white; }
QHeaderView::section { background:#281c19; color:#d7aa96; } QTextBrowser#publishText, QTextBrowser#storeReadme { background:#211816; border-color:#4d342d; color:#f2d5ca; } QTextBrowser#publishText pre { background:#17110f; border-color:#4d342d; color:#f2d5ca; }
QDialog#installationDialog, QDialog#toolInfoDialog { background:#17110f; color:#ffe4d9; } QDialog#toolInfoDialog QTreeWidget { background:#211816; color:#ffe4d9; border-color:#4d342d; alternate-background-color:#281c19; } QDialog#toolInfoDialog QTreeWidget::item { color:#ffe4d9; } QDialog#toolInfoDialog QHeaderView::section { background:#281c19; color:#ffae83; border-bottom-color:#4d342d; }
QLabel#largeToolIcon, QLabel#storeDetailIcon { background:#17110f; border-color:#5a3a32; }
QWidget#storeDetailContent { background:#211816; } QLabel#stageNote { color:#ffd37a; background:#332416; border-color:#77542a; }
QDialog#installationDialog QCheckBox { color:#f2d5ca; } QDialog#installationDialog QCheckBox::indicator:unchecked { background:#211816; border-color:#7a5144; } QDialog#installationDialog QCheckBox::indicator:checked { background:#d95e32; border-color:#ff9a70; }
QLabel#duplicateUidWarning { background:#3b1c20; border-color:#91404b; color:#ffb6bd; } QWidget#hashMismatchWarning { background:#3b2a17; border-color:#8e682d; } QWidget#hashMismatchWarning QLabel { color:#ffd27a; } QPushButton#hashWarningUpdate { background:#754717; border-color:#b97a2f; color:#fff0c5; }
QDialog, QMessageBox { background:#17110f; color:#ffe4d9; } QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox, QMessageBox QLabel, QMessageBox QCheckBox { color:#f2d5ca; } QMenu { background:#211816; color:#ffe4d9; border-color:#4d342d; } QMenu::item:selected { background:#d95e32; color:white; } QToolTip { background:#211816; color:#ffe4d9; border-color:#5a3a32; }
QDialog QPushButton, QMessageBox QPushButton { background:#281c19; color:#ffe4d9; border-color:#614139; } QDialog QPushButton:hover, QMessageBox QPushButton:hover { background:#38241f; }
QListWidget#trashList { background:#17110f; color:#ffe4d9; border-color:#5a3a32; } QListWidget#trashList::item:hover { background:#38241f; } QListWidget#trashList::item:selected { background:#d95e32; color:white; }
'''

OCEAN_STYLE = '''
QCheckBox { color:#e4f8ff; }
QWidget#root { color:#d9f5ff; } QLabel { color:#d9f5ff; }
QFrame#sidebar, QFrame#card, QFrame#statCard, QFrame#toolCard, QFrame#emptyCard, QFrame#toolInfoCard, QFrame#applicationCard, QFrame#storeDetailCard { background:#102233; border-color:#24465e; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #17334a, stop:1 #102132); border-color:#32647f; } QFrame#fullToolbar, QFrame#detachedLauncherBar { background:#0c1724; border-bottom-color:#24465e; }
QPushButton#navButton { color:#a9cbdc; } QPushButton#navButton:hover { background:#19354a; color:#effbff; } QPushButton#navButton:checked, QPushButton#primaryButton { background:#1f9dc6; color:white; } QPushButton#primaryButton:hover { background:#35b9e3; }
QLabel#pageSubtitle, QLabel#muted, QLabel#heroText, QLabel#toolMeta, QLabel#emptyHint, QLabel#statLabel, QLabel#dashboardBody, QLabel#howToText, QLabel#downloadedToolDescription, QLabel#storeDetailLinks, QLabel#storeBannerUid { color:#a7c2d1; } QLabel#statValue, QLabel#toolIcon, QLabel#navTitle, QLabel#eyebrow { color:#44c6ea; }
QPushButton#outlineButton, QPushButton#historyButton { background:#142b3d; border-color:#315770; color:#e4f8ff; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#1c3b51; } QPushButton#disabledButton { background:#284253; color:#91aebb; }
QPushButton#dangerButton { background:#3a1d2a; border-color:#83475a; color:#ffbec9; } QPushButton#dangerButton:hover { background:#572534; color:white; }
QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#0c1724; border-color:#315770; color:#e4f8ff; } QComboBox QAbstractItemView { background:#142b3d; color:#e4f8ff; selection-background-color:#1f9dc6; }
QScrollArea, QScrollArea::viewport { background:#102233; } QScrollBar:vertical, QScrollBar:horizontal { background:#102233; border-color:#24465e; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#238eb3; border-color:#102233; }
QTabWidget::pane { background:#102233; border-color:#24465e; } QTabBar::tab, QTabBar#runningTabs::tab { background:#183247; color:#afcddd; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#1f9dc6; color:white; } QHeaderView::section { background:#142b3d; color:#a9cbdc; }
QTextBrowser#publishText, QTextBrowser#storeReadme { background:#102233; border-color:#24465e; color:#d9f5ff; } QTextBrowser#publishText pre { background:#0c1724; border-color:#24465e; color:#d9f5ff; }
QDialog#installationDialog, QDialog#toolInfoDialog, QDialog, QMessageBox { background:#0c1724; color:#e4f8ff; } QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox, QMessageBox QLabel, QMessageBox QCheckBox { color:#d9f5ff; } QDialog#toolInfoDialog QTreeWidget { background:#102233; color:#e4f8ff; border-color:#24465e; alternate-background-color:#142b3d; } QDialog#toolInfoDialog QTreeWidget::item { color:#e4f8ff; } QDialog#toolInfoDialog QHeaderView::section { background:#142b3d; color:#6bd8f6; border-bottom-color:#24465e; }
QDialog QPushButton, QMessageBox QPushButton { background:#142b3d; color:#e4f8ff; border-color:#315770; } QDialog QPushButton:hover, QMessageBox QPushButton:hover { background:#1c3b51; }
QMenu { background:#102233; color:#e4f8ff; border-color:#24465e; } QMenu::item:selected { background:#1f9dc6; color:white; } QToolTip { background:#102233; color:#e4f8ff; border-color:#315770; }
QWidget#storeDetailContent { background:#102233; } QLabel#largeToolIcon, QLabel#storeDetailIcon { background:#0c1724; border-color:#315770; } QLabel#stageNote { color:#b5ecff; background:#123247; border-color:#31708d; }
QDialog#installationDialog QCheckBox { color:#d9f5ff; } QDialog#installationDialog QCheckBox::indicator:unchecked { background:#102233; border-color:#4e7890; } QDialog#installationDialog QCheckBox::indicator:checked { background:#1f9dc6; border-color:#67d6f5; }
QListWidget#trashList { background:#0c1724; color:#e4f8ff; border-color:#315770; } QListWidget#trashList::item:hover { background:#1c3b51; } QListWidget#trashList::item:selected { background:#1f9dc6; color:white; }
'''

LAVENDER_STYLE = '''
QCheckBox { color:#342c4a; }
QWidget#root { color:#2d2740; } QLabel { color:#2d2740; }
QFrame#sidebar, QFrame#card, QFrame#statCard, QFrame#toolCard, QFrame#emptyCard, QFrame#toolInfoCard, QFrame#applicationCard, QFrame#storeDetailCard { background:#ffffff; border-color:#ddd3f0; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffffff, stop:1 #eee9fa); border-color:#d8c9ef; } QFrame#fullToolbar, QFrame#detachedLauncherBar { background:#f8f5fd; border-bottom-color:#ddd3f0; }
QPushButton#navButton { color:#665e7d; } QPushButton#navButton:hover { background:#eee9f8; color:#2d2740; } QPushButton#navButton:checked, QPushButton#primaryButton { background:#7d5bd6; color:white; } QPushButton#primaryButton:hover { background:#9576e7; }
QLabel#pageSubtitle, QLabel#muted, QLabel#heroText, QLabel#toolMeta, QLabel#emptyHint, QLabel#statLabel, QLabel#dashboardBody, QLabel#howToText, QLabel#downloadedToolDescription, QLabel#storeDetailLinks, QLabel#storeBannerUid { color:#746c86; } QLabel#statValue, QLabel#toolIcon, QLabel#navTitle, QLabel#eyebrow { color:#7654c9; }
QPushButton#outlineButton, QPushButton#historyButton { background:#ffffff; border-color:#d9d0e9; color:#342c4a; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#f0ecf8; } QPushButton#disabledButton { background:#e8e2f1; color:#8d849b; }
QPushButton#dangerButton { background:#fff0f5; border-color:#e8abc0; color:#a23658; } QPushButton#dangerButton:hover { background:#fbdbe6; color:#7d1f3c; }
QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#ffffff; border-color:#d9d0e9; color:#342c4a; } QComboBox QAbstractItemView { background:#ffffff; color:#342c4a; selection-background-color:#7d5bd6; }
QScrollArea, QScrollArea::viewport { background:#ffffff; } QScrollBar:vertical, QScrollBar:horizontal { background:#eee9f6; border-color:#d9d0e9; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#8b6bdd; border-color:#eee9f6; }
QTabWidget::pane { background:#ffffff; border-color:#ddd3f0; } QTabBar::tab, QTabBar#runningTabs::tab { background:#ece6f6; color:#665e7d; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#7d5bd6; color:white; } QHeaderView::section { background:#f2eef9; color:#665e7d; }
QTextBrowser#publishText, QTextBrowser#storeReadme { background:#ffffff; border-color:#ddd3f0; color:#342c4a; } QTextBrowser#publishText pre { background:#f4f0fb; border-color:#ddd3f0; color:#342c4a; }
QDialog#installationDialog, QDialog#toolInfoDialog, QDialog, QMessageBox { background:#f8f5fd; color:#342c4a; } QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox, QMessageBox QLabel, QMessageBox QCheckBox { color:#342c4a; } QDialog#toolInfoDialog QTreeWidget { background:#ffffff; color:#342c4a; border-color:#d9d0e9; alternate-background-color:#f4f0fa; } QDialog#toolInfoDialog QTreeWidget::item { color:#342c4a; } QDialog#toolInfoDialog QHeaderView::section { background:#f0ebf8; color:#6c49bc; border-bottom-color:#d9d0e9; }
QDialog QPushButton, QMessageBox QPushButton { background:#ffffff; color:#342c4a; border-color:#d9d0e9; } QDialog QPushButton:hover, QMessageBox QPushButton:hover { background:#f0ecf8; }
QMenu { background:#ffffff; color:#342c4a; border-color:#d9d0e9; } QMenu::item:selected { background:#7d5bd6; color:white; } QToolTip { background:#ffffff; color:#342c4a; border-color:#d9d0e9; }
QWidget#storeDetailContent { background:#ffffff; } QLabel#largeToolIcon, QLabel#storeDetailIcon { background:#f4f0fb; border-color:#d9d0e9; } QLabel#stageNote { color:#5a408d; background:#f2ecff; border-color:#cbb8ee; }
QDialog#installationDialog QCheckBox { color:#4c4264; } QDialog#installationDialog QCheckBox::indicator:unchecked { background:#ffffff; border-color:#bdb1d3; } QDialog#installationDialog QCheckBox::indicator:checked { background:#7d5bd6; border-color:#a88ded; }
QListWidget#trashList { background:#ffffff; color:#342c4a; border-color:#d9d0e9; } QListWidget#trashList::item:hover { background:#f0ecf8; } QListWidget#trashList::item:selected { background:#7d5bd6; color:white; }
'''

FIRE_STYLE = EMBER_STYLE + '''
QWidget#root { color:#ffe1d2; } QLabel { color:#ffe1d2; }
QFrame#sidebar { background:#2a1412; border-color:#703129; } QFrame#card, QFrame#toolInfoCard, QFrame#applicationCard, QFrame#storeDetailCard { background:#281513; border-color:#623029; } QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #4a1d14, stop:.55 #301514, stop:1 #24100f); border-color:#88382a; }
QPushButton#navButton:checked, QPushButton#primaryButton { background:#d84b2d; } QPushButton#primaryButton:hover { background:#f16939; } QLabel#statValue, QLabel#toolIcon, QLabel#navTitle, QLabel#eyebrow { color:#ff9c45; }
QPushButton#outlineButton, QPushButton#historyButton { background:#351a16; border-color:#77372d; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#4a211a; }
QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#1b0f0d; border-color:#71342b; } QComboBox QAbstractItemView { background:#351a16; selection-background-color:#d84b2d; }
QScrollArea, QScrollArea::viewport { background:#281513; } QScrollBar:vertical, QScrollBar:horizontal { background:#281513; border-color:#623029; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#b9402c; border-color:#281513; } QTabBar::tab, QTabBar#runningTabs::tab { background:#432019; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#d84b2d; }
QDialog, QMessageBox, QDialog#installationDialog, QDialog#toolInfoDialog { background:#1b0f0d; } QMenu, QTextBrowser#publishText, QTextBrowser#storeReadme { background:#281513; border-color:#623029; } QWidget#storeDetailContent { background:#281513; } QLabel#stageNote { background:#402012; border-color:#8b4d22; color:#ffd37a; }
QListWidget#trashList { background:#1b0f0d; border-color:#71342b; } QListWidget#trashList::item:hover { background:#4a211a; } QListWidget#trashList::item:selected { background:#d84b2d; }
'''

SPECTRUM_DARK_STYLE = OCEAN_STYLE + '''
QWidget#root { color:#f1edff; } QLabel { color:#f1edff; }
QFrame#sidebar { background:#211b48; border-color:#524a9a; } QFrame#card { background:#181b38; border-color:#3d4b85; } QFrame#statCard { background:#162e3e; border-color:#2f7082; } QFrame#toolCard, QFrame#toolInfoCard, QFrame#applicationCard { background:#291b42; border-color:#704493; } QFrame#emptyCard, QFrame#storeDetailCard { background:#35251b; border-color:#9a7040; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #263c7f, stop:.45 #572b7c, stop:1 #a34266); border-color:#7b65ce; }
QPushButton#navButton:checked, QPushButton#primaryButton { background:#d548b2; } QPushButton#primaryButton:hover { background:#ed63cf; } QLabel#statValue { color:#43e0d1; } QLabel#toolIcon, QLabel#navTitle, QLabel#eyebrow { color:#ffd05e; }
QPushButton#outlineButton, QPushButton#historyButton { background:#222450; border-color:#595cc0; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#30336b; } QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#101126; border-color:#4c4ca1; } QComboBox QAbstractItemView { background:#222450; selection-background-color:#d548b2; }
QScrollArea, QScrollArea::viewport { background:#181b38; } QScrollBar:vertical, QScrollBar:horizontal { background:#211b48; border-color:#524a9a; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#38cfc4; border-color:#211b48; } QTabBar::tab, QTabBar#runningTabs::tab { background:#273063; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#d548b2; }
QDialog, QMessageBox, QDialog#installationDialog, QDialog#toolInfoDialog { background:#101126; } QMenu { background:#211b48; border-color:#524a9a; } QMenu::item:selected { background:#d548b2; } QTextBrowser#publishText, QTextBrowser#storeReadme { background:#181b38; border-color:#3d4b85; } QWidget#storeDetailContent { background:#35251b; } QLabel#stageNote { background:#182f4b; border-color:#2f7796; color:#9aeaff; }
QListWidget#trashList { background:#101126; border-color:#4c4ca1; } QListWidget#trashList::item:hover { background:#30336b; } QListWidget#trashList::item:selected { background:#d548b2; }
'''

SPECTRUM_LIGHT_STYLE = LAVENDER_STYLE + '''
QWidget#root { color:#302b47; } QLabel { color:#302b47; }
QFrame#sidebar { background:#f1edff; border-color:#c9bdf0; } QFrame#card { background:#f3fbff; border-color:#b7dced; } QFrame#statCard { background:#f1fff9; border-color:#b2e7d2; } QFrame#toolCard, QFrame#toolInfoCard, QFrame#applicationCard { background:#fff2fb; border-color:#ebc0df; } QFrame#emptyCard, QFrame#storeDetailCard { background:#fff8e8; border-color:#efd39c; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #e3ecff, stop:.42 #f7e5ff, stop:1 #ffe7ed); border-color:#c9b3ef; }
QPushButton#navButton:checked, QPushButton#primaryButton { background:#c441a7; } QPushButton#primaryButton:hover { background:#dc5bc0; } QLabel#statValue { color:#159c91; } QLabel#toolIcon, QLabel#navTitle, QLabel#eyebrow { color:#d07d13; }
QPushButton#outlineButton, QPushButton#historyButton { background:#ffffff; border-color:#c6c4ee; } QPushButton#outlineButton:hover, QPushButton#historyButton:hover { background:#eef0ff; } QLineEdit#input, QComboBox#input, QTreeWidget#projectTree, QPlainTextEdit#projectEditor, QLabel#imagePreview { background:#ffffff; border-color:#c3c4e9; } QComboBox QAbstractItemView { background:#ffffff; selection-background-color:#c441a7; }
QScrollArea, QScrollArea::viewport { background:#f3fbff; } QScrollBar:vertical, QScrollBar:horizontal { background:#ece9fa; border-color:#c9c6e6; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#4a9ed3; border-color:#ece9fa; } QTabBar::tab, QTabBar#runningTabs::tab { background:#e7e3f8; } QTabBar::tab:selected, QTabBar#runningTabs::tab:selected { background:#c441a7; }
QDialog, QMessageBox, QDialog#installationDialog, QDialog#toolInfoDialog { background:#f8f7ff; } QMenu { background:#ffffff; border-color:#c9c6e6; } QMenu::item:selected { background:#c441a7; } QTextBrowser#publishText, QTextBrowser#storeReadme { background:#ffffff; border-color:#c6d8ef; } QWidget#storeDetailContent { background:#fff8e8; } QLabel#stageNote { background:#e8f8ff; border-color:#9ccde4; color:#276988; }
QListWidget#trashList { background:#ffffff; color:#302b47; border-color:#c3c4e9; } QListWidget#trashList::item:hover { background:#eef0ff; } QListWidget#trashList::item:selected { background:#c441a7; color:white; }
'''

THEME_STYLES = {"dark": "", "light": LIGHT_STYLE, "ember": EMBER_STYLE, "ocean": OCEAN_STYLE, "lavender": LAVENDER_STYLE, "fire": FIRE_STYLE, "spectrum_dark": SPECTRUM_DARK_STYLE, "spectrum_light": SPECTRUM_LIGHT_STYLE}
DENSITY_STYLES = {
    'compact': '''
        QWidget { font-size:9pt; } QLabel#brand { font-size:12pt; } QLabel#navTitle, QLabel#eyebrow { font-size:7pt; }
        QLabel#topTitle { font-size:16pt; } QLabel#pageTitle { font-size:19pt; } QLabel#runningTitle { font-size:18pt; } QLabel#heroTitle { font-size:23pt; }
        QLabel#statValue { font-size:21pt; } QLabel#toolIcon { font-size:18pt; } QLabel#toolName, QLabel#emptyTitle { font-size:11pt; }
        QLabel#emptyIcon { font-size:37pt; } QLabel#fieldLabel { font-size:9pt; } QLabel#dashboardSectionTitle { font-size:11pt; }
        QLabel#dashboardBody, QLabel#heroText, QLabel#howToText, QLabel#downloadedToolDescription { font-size:10pt; }
        QLabel#downloadedToolName { font-size:12pt; } QLabel#largeToolIcon { font-size:25pt; }
        QLabel#storeBannerIcon { font-size:21pt; } QLabel#storeBannerTitle { font-size:16pt; } QLabel#storeBannerDescription { font-size:9pt; }
        QLabel#storeBannerMeta, QLabel#storeDetailLinks { font-size:8pt; } QLabel#storeBannerUid { font-size:7pt; }
        QLabel#storeDetailTitle { font-size:19pt; } QLabel#storeDetailIcon { font-size:26pt; } QTextBrowser#storeReadme { font-size:9pt; }
        QLabel#duplicateUidWarning, QLabel#systemUnavailable, QWidget#hashMismatchWarning QLabel, QPushButton#hashWarningUpdate { font-size:7pt; }
        QPushButton, QPushButton#primaryButton, QPushButton#outlineButton, QPushButton#disabledButton, QPushButton#dangerButton { font-size:9pt; padding:6px 10px; border-radius:9px; }
        QPushButton#navButton { font-size:9pt; padding:8px 11px; } QPushButton#historyButton { font-size:11pt; }
        QLabel#stageNote, QTreeWidget#projectTree, QHeaderView::section, QPlainTextEdit#projectEditor { font-size:8pt; }
        QLineEdit, QComboBox, QPlainTextEdit { padding:6px; } QFrame#toolCard, QFrame#downloadedToolCard { max-height:108px; }
    ''',
    'normal': '',
    'large': '''
        QWidget { font-size:11pt; } QLabel#brand { font-size:16pt; } QLabel#navTitle, QLabel#eyebrow { font-size:9pt; }
        QLabel#topTitle { font-size:21pt; } QLabel#pageTitle { font-size:26pt; } QLabel#runningTitle { font-size:23pt; } QLabel#heroTitle { font-size:30pt; }
        QLabel#statValue { font-size:28pt; } QLabel#toolIcon { font-size:23pt; } QLabel#toolName, QLabel#emptyTitle { font-size:14pt; }
        QLabel#emptyIcon { font-size:49pt; } QLabel#fieldLabel { font-size:12pt; } QLabel#dashboardSectionTitle { font-size:14pt; }
        QLabel#dashboardBody, QLabel#heroText, QLabel#howToText, QLabel#downloadedToolDescription { font-size:13pt; }
        QLabel#downloadedToolName { font-size:16pt; } QLabel#largeToolIcon { font-size:32pt; }
        QLabel#storeBannerIcon { font-size:28pt; } QLabel#storeBannerTitle { font-size:21pt; } QLabel#storeBannerDescription { font-size:12pt; }
        QLabel#storeBannerMeta, QLabel#storeDetailLinks { font-size:10pt; } QLabel#storeBannerUid { font-size:9pt; }
        QLabel#storeDetailTitle { font-size:26pt; } QLabel#storeDetailIcon { font-size:35pt; } QTextBrowser#storeReadme { font-size:12pt; }
        QLabel#duplicateUidWarning, QLabel#systemUnavailable, QWidget#hashMismatchWarning QLabel, QPushButton#hashWarningUpdate { font-size:9pt; }
        QLabel#pageSubtitle, QLabel#toolMeta { font-size:11pt; }
        QPushButton, QPushButton#primaryButton, QPushButton#outlineButton, QPushButton#disabledButton, QPushButton#dangerButton { font-size:12pt; padding:11px 17px; border-radius:13px; }
        QPushButton#navButton { font-size:12pt; padding:14px 16px; } QPushButton#historyButton { font-size:14pt; }
        QLabel#stageNote, QTreeWidget#projectTree, QHeaderView::section, QPlainTextEdit#projectEditor { font-size:10pt; }
        QLineEdit, QComboBox, QPlainTextEdit { padding:12px; }
    '''
}

