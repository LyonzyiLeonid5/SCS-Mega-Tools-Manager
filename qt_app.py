import gc
import ctypes
import html
import hashlib
import math
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from collections import OrderedDict
import json
import os
import re
import sys
import threading
import uuid
import zipfile
import shutil
import ssl
import tempfile
from urllib.error import URLError
from urllib.parse import urljoin, urlparse, unquote
from urllib.request import Request, urlopen
from pathlib import Path
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, QObject, pyqtSlot, pyqtSignal, pyqtProperty, QTimer, QUrl, QEvent, QRectF, QRect, QElapsedTimer
from PyQt5.QtGui import QIcon, QPixmap, QCursor, QSyntaxHighlighter, QTextCharFormat, QColor, QFont, QKeySequence, QPainter, QPainterPath, QLinearGradient, QRadialGradient, QPen, QDesktopServices
from PyQt5.QtWidgets import (QApplication, QComboBox, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel as QtLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget, QSizePolicy, QTabWidget, QTabBar, QDialog, QToolButton,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QScrollArea, QFileDialog, QPlainTextEdit, QSplitter, QInputDialog, QMenu, QShortcut, QTextBrowser, QCheckBox)
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWebChannel import QWebChannel
import languages
from app_logging import ActionLogger, configure_logging, logger as application_logger
from settings_store import load_settings, save_settings
from tool_manager import ToolRegistry, Tool, flatten
from installation_manager import ensure_manager_shortcut, install_tool, is_installed, launcher_path, launcher_uid, read_launcher, register_file_association, restore_embedded_archive, uninstall_tool
if sys.platform == "win32":
    from win_embed import ExeHost
else:
    ExeHost = None
from html_workspace import AuthoringWorkspace, basic_document, format_html

try:
    import certifi
    REMOTE_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except (ImportError, OSError):
    # Development installations can use the operating system certificate store.
    REMOTE_SSL_CONTEXT = ssl.create_default_context()


def open_remote(request, timeout):
    """Open HTTPS resources with a bundled CA set on every supported OS."""
    return urlopen(request, timeout=timeout, context=REMOTE_SSL_CONTEXT)


class QLabel(QtLabel):
    """A label whose text can be selected and copied anywhere in the app."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs); self.setTextInteractionFlags(Qt.TextSelectableByMouse)


class AnimatedBackground(QWidget):
    """An intentionally subtle moving glow behind the application content."""
    def __init__(self):
        super().__init__(); self._phase = 0.0; self.clock = QElapsedTimer(); self.clock.start()
        self.last_glow_rect = QRect()
        self.frame_timer = QTimer(self); self.frame_timer.setTimerType(Qt.PreciseTimer); self.frame_timer.setInterval(16); self.frame_timer.timeout.connect(self.advance_frame); self.frame_timer.start()
    def advance_frame(self):
        old_rect = self.glow_rect()
        self._phase = (self.clock.elapsed() % 24000) / 24000.0
        new_rect = self.glow_rect(); self.last_glow_rect = new_rect
        # Repaint only the moving effect, never the whole maximized desktop.
        self.update(old_rect.united(new_rect).adjusted(-5, -5, 5, 5))
    def get_phase(self): return self._phase
    def set_phase(self, value): self._phase = float(value); self.update()
    phase = pyqtProperty(float, fget=get_phase, fset=set_phase)
    def glow_rect(self):
        radius = min(260, max(150, min(self.width(), self.height()) // 4))
        angle = self._phase * math.tau
        x = int(self.width() * (.48 + .28 * math.sin(angle)))
        y = int(self.height() * (.45 + .20 * math.cos(angle * .8)))
        return QRect(x - radius, y - radius, radius * 2, radius * 2)
    def resizeEvent(self, event):
        self.last_glow_rect = self.glow_rect(); self.update()
        super().resizeEvent(event)
    def paintEvent(self, event):
        painter = QPainter(self); dirty = event.rect(); painter.fillRect(dirty, QColor('#101216'))
        glow = self.glow_rect(); center = glow.center(); radius = glow.width() / 2
        painter.setClipRect(dirty); gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0, QColor(238, 107, 47, 28)); gradient.setColorAt(.48, QColor(76, 122, 183, 13)); gradient.setColorAt(1, QColor(16, 18, 22, 0))
        painter.fillRect(glow, gradient); painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 141, 85, 32), 1)); painter.drawEllipse(center, 3, 3)


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


BRIDGE_MARKER = "<!-- SCS_TOOL_BRIDGE -->"
def inject_tool_bridge(html):
    """Adds the editor bridge inside body, never as document-level markup."""
    bridge = '''<!-- SCS_TOOL_BRIDGE -->
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script>
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
        r'<!--\s*SCS_TOOL_BRIDGE\s*-->\s*'
        r'<script\b[^>]*qrc:///qtwebchannel/qwebchannel\.js[^>]*>\s*</script>\s*'
        r'<script\b[^>]*>.*?</script\s*>', re.I | re.S)
    html = generated_bridge.sub('', html)
    html = re.sub(r'<!--\s*SCS_TOOL_BRIDGE\s*-->\s*', '', html, flags=re.I)
    closing = re.search(r'</body\s*>', html, re.I)
    if closing:
        return html[:closing.start()].rstrip() + '\n' + bridge + '\n' + html[closing.start():]
    # A fragment or a malformed document still gets a valid body for bridge code.
    html_close = re.search(r'</html\s*>', html, re.I)
    body = '\n<body>\n' + bridge + '\n</body>\n'
    return html[:html_close.start()] + body + html[html_close.start():] if html_close else html.rstrip() + body


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


class HomePage(Page):
    def __init__(self, app):
        super().__init__(app, "dashboard")
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
        self.update_text(app.lang)
    def refresh_overview(self):
        store = getattr(self.app, "pages", {}).get("store")
        verified = len(store.tools) if getattr(store, "verified_loaded", False) else "—"
        downloaded = len(self.app.registry.scan())
        catalog_state = self.app.lang["dashboard_catalog_ready"] if getattr(store, "verified_loaded", False) else self.app.lang["dashboard_catalog_unavailable"]
        values = (str(verified), str(downloaded), "3", "v1.0.0")
        labels = (self.app.lang["dashboard_verified"], self.app.lang["dashboard_downloaded_count"], self.app.lang["dashboard_runtimes"], self.app.lang["dashboard_version"])
        for (number, label), value, text in zip(self.stats, values, labels): number.setText(value); label.setText(text)
        self.subtitle.setText(f"{self.app.lang['dashboard_subtitle']} · {catalog_state}")
    def update_text(self, lang):
        self.title.setText(lang["tab_dashboard"]); self.hero_title.setText(lang["dashboard_title"]); self.hero_text.setText(lang["dashboard_info"])
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
        mandatory_layout.addWidget(self.launcher_label); mandatory_layout.addWidget(self.launcher_path_label); layout.addWidget(mandatory)
        self.one_file = QCheckBox(); self.start_menu = QCheckBox(); self.desktop = QCheckBox(); layout.addWidget(self.one_file); layout.addWidget(self.start_menu); layout.addWidget(self.desktop)
        buttons = QHBoxLayout(); buttons.addStretch(); self.cancel = QPushButton(); self.cancel.setObjectName('outlineButton'); self.cancel.clicked.connect(self.reject); self.install = QPushButton(); self.install.setObjectName('primaryButton'); self.install.clicked.connect(self.perform_install); buttons.addWidget(self.cancel); buttons.addWidget(self.install); layout.addLayout(buttons)
        self.update_text()
    def text(self, ru, en): return ru if self.app.lang['_lang_code'] == 'ru' else en
    def update_text(self):
        self.setWindowTitle(self.text('Установка инструмента', 'Install tool')); self.title.setText(self.text(f'Установить: {self.tool.name}', f'Install: {self.tool.name}'))
        self.description.setText(self.text('Файл запуска создаётся всегда. Его можно открывать двойным кликом: SCS Tools Manager найдёт инструмент по UID.', 'A launch file is always created. Double-clicking it opens SCS Tools Manager, which finds the tool by UID.'))
        self.launcher_label.setText(self.text('Обязательный файл запуска (.scstool)', 'Required launch file (.scstool)'))
        self.one_file.setText(self.text('Сохранить инструмент внутри одного файла .scstool', 'Store the tool inside one .scstool file'))
        self.start_menu.setText(self.text('Создать ярлык в Пуск → SCS Tools', 'Create a shortcut in Start → SCS Tools'))
        self.desktop.setText(self.text('Создать ярлык на рабочем столе', 'Create a desktop shortcut'))
        self.cancel.setText(self.text('Отмена', 'Cancel')); self.install.setText(self.text('Установить', 'Install'))
    def perform_install(self):
        try: result = install_tool(self.tool, one_file=self.one_file.isChecked(), start_menu=self.start_menu.isChecked(), desktop=self.desktop.isChecked())
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
        self.delete = QPushButton(); self.delete.setObjectName("dangerButton"); self.delete.clicked.connect(lambda: self.page.delete_tool(self.tool))
        actions = QVBoxLayout(); actions.addStretch(); actions.addWidget(self.open_page); actions.addWidget(self.download); actions.addWidget(self.delete); actions.addStretch(); layout.addWidget(self.icon); layout.addLayout(text_block, 1); layout.addLayout(actions)
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
        self.meta.setText(self.page.text(f"Обновлено: {updated}  •  Runtime: {runtime}  •  Размер: {size}", f"Updated: {updated}  •  Runtime: {runtime}  •  Size: {size}") + category_meta)
        self.uid.setText("UID: " + str(self.tool.get("uid", "—"))); self.open_page.setText(self.page.text("Открыть страницу", "Open Page"))
        state = self.tool.get('_store_state', 'download')
        labels = {'download': "↓  " + self.page.app.lang["store_download"], 'downloading': self.page.text("Загрузка…", "Downloading…"), 'update': self.page.text("Обновить", "Update"), 'open': self.page.app.lang['open'] if sys.platform != 'win32' else self.page.text("Установить", "Install"), 'installed': self.page.text("Открыть", "Open")}
        available = self.page.runtime_available(self.tool)
        self.system_notice.setText(self.page.text("Недоступно на Вашей системе", "Unavailable on your system")); self.system_notice.setVisible(not available)
        self.download.setText(labels.get(state, labels['download']))
        self.download.setDisabled(state == 'downloading' or not available)
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
    def __init__(self, app):
        super().__init__(app, "store"); self.title, self.subtitle = self.heading("", ""); self.tools = []; self.tool_page = None; self.page_cache = OrderedDict(); self.catalog_unavailable_notified = False; self.catalog_error = False; self.verified_loaded = False; self.installed_hashes = None; self.status_state = ("catalog_loading", {}); self.active_downloads = set()
        self.catalog_panel = QWidget(); self.catalog_panel.setObjectName('storeContent'); catalog_layout = QVBoxLayout(self.catalog_panel); catalog_layout.setContentsMargins(0, 0, 0, 4); catalog_layout.setSpacing(10); catalog_layout.setAlignment(Qt.AlignTop)
        head = QHBoxLayout(); self.status = QLabel(); self.status.setObjectName("pageSubtitle"); head.addWidget(self.status); head.addStretch(); self.publish = QPushButton(); self.publish.setObjectName("outlineButton"); self.publish.clicked.connect(lambda: PublishToolDialog(self.app).exec_()); self.refresh = QPushButton(); self.refresh.setObjectName("primaryButton"); self.refresh.clicked.connect(lambda: self.refresh_catalog(clear_page_cache=True)); head.addWidget(self.publish); head.addWidget(self.refresh); catalog_layout.addLayout(head)
        self.list = QVBoxLayout(); self.list.setSpacing(0); catalog_layout.addLayout(self.list); catalog_layout.addStretch()
        self.catalog_scroll = QScrollArea(); self.catalog_scroll.setObjectName('storeScroll'); self.catalog_scroll.setWidgetResizable(True); self.catalog_scroll.setFrameShape(QFrame.NoFrame); self.catalog_scroll.setWidget(self.catalog_panel); self.layout.addWidget(self.catalog_scroll, 1)
        self.catalog_ready.connect(self.receive_catalog); self.download_finished.connect(self.finish_download); self.update_text(app.lang); self.refresh_catalog()
    def text(self, ru, en): return ru if self.app.lang["_lang_code"] == "ru" else en
    def runtime_available(self, tool):
        runtime = tool.get("runtime") if isinstance(tool.get("runtime"), dict) else tool.get("runtime_type", "")
        kind = runtime.get("type", "") if isinstance(runtime, dict) else str(runtime)
        return kind != "exe" or sys.platform == "win32"
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
        }
        self.status.setText(messages.get(key, ''))
    def remote_url(self, value): return urljoin(STORE_CATALOG_URL, str(value)) if isinstance(value, str) and value.strip() else ""
    def tool_url(self, tool, filename):
        uid = str(tool.get('uid', ''))
        if not uid or not isinstance(filename, str) or not filename.strip() or '/' in filename.replace('://', ''): return ""
        return urljoin(STORE_CATALOG_URL, f"verified_tools/{uid}/{filename}")
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
                    entry = dict(item); expected = str(entry.get('sha256', '')).lower(); expected_name = Path(str(entry.get('download_url', ''))).name; actual = installed_hashes.get(entry['uid'])
                    # A byte mismatch always wins: do not offer installation
                    # or opening until the verified archive has been updated.
                    entry['_store_state'] = 'update' if actual and actual[0] != expected else 'installed' if actual and actual[1] == expected_name and is_installed(entry['uid']) else 'open' if actual and actual[1] == expected_name else 'download'
                    valid.append(entry)
                try: self.catalog_ready.emit(valid, "")
                except RuntimeError: pass
            except (OSError, URLError, ValueError, UnicodeError, json.JSONDecodeError):
                try: self.catalog_ready.emit([], "catalog_unavailable")
                except RuntimeError: pass
        threading.Thread(target=fetch, daemon=True).start()
    def receive_catalog(self, tools, error):
        self.refresh.setEnabled(True); self.tools = tools; self.catalog_error = bool(error); self.verified_loaded = not self.catalog_error; self.set_status('catalog_error' if self.catalog_error else 'catalog_count', count=len(tools)); self.populate()
        pages = getattr(self.app, 'pages', {})
        if isinstance(pages, dict) and pages.get('dashboard'): pages['dashboard'].refresh_overview()
        if isinstance(pages, dict) and pages.get('library'): pages['library'].refresh()
        if error and not self.catalog_unavailable_notified:
            self.catalog_unavailable_notified = True; QMessageBox.warning(self, self.app.lang['error'], self.text('Магазин недоступен. Проверьте подключение к интернету.', 'The store is unavailable. Check your internet connection.'))
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
    def action(self, tool):
        state = tool.get('_store_state')
        if state in ('open', 'installed'):
            expected_name = Path(str(tool.get('download_url', ''))).name
            local = next((item for item in self.app.registry.scan() if item.uid == tool.get('uid') and item.archive.name == expected_name), None)
            if local:
                if state == 'installed' or sys.platform != 'win32': self.app.open_tool(local)
                else: self.app.show_install_dialog(local)
                return
            tool['_store_state'] = 'update'; self.refresh_cards()
        self.download(tool)
    def local_tool(self, tool):
        expected_name = Path(str(tool.get('download_url', ''))).name
        return next((item for item in self.app.registry.scan() if item.uid == tool.get('uid') and item.archive.name == expected_name), None)
    def delete_tool(self, tool):
        local = self.local_tool(tool)
        if local: self.app.delete_tool(local)
    def download(self, tool):
        url = self.tool_url(tool, tool.get("download_url")); uid = str(tool.get("uid", ""))
        if not url or not uid or uid in self.active_downloads: return
        self.active_downloads.add(uid); tool['_store_state'] = 'downloading'; self.refresh_cards()
        application_logger().info("Store download started: name=%r uid=%s url=%s", tool.get('name', ''), uid, url)
        self.set_status('download_loading', name=str(tool.get('name', '')))
        def fetch():
            temporary = None
            try:
                request = Request(url, headers={"User-Agent": "SCS-Mega-Manager/1.0"})
                with open_remote(request, timeout=30) as response:
                    suffix = Path(urlparse(url).path).suffix or ".zip"; handle, temporary = tempfile.mkstemp(prefix="scs_download_", suffix=suffix, dir=str(self.app.registry.tools_dir)); os.close(handle)
                    with open(temporary, "wb") as output:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk: break
                            output.write(chunk)
                with zipfile.ZipFile(temporary) as archive:
                    if archive.testzip() is not None: raise zipfile.BadZipFile
                    info = json.loads(archive.read("info.json").decode("utf-8-sig"))
                if info.get("uid") != uid: raise ValueError
                expected_hash = str(tool.get("sha256", "")).lower()
                if not re.fullmatch(r"[0-9a-f]{64}", expected_hash): raise ValueError
                digest = file_sha256(temporary)
                if digest != expected_hash: raise ValueError
                expected_name = Path(str(tool.get('download_url', ''))).name
                if not expected_name or expected_name != str(tool.get('download_url', '')) or Path(expected_name).suffix.lower() != '.zip': raise ValueError
                target = self.app.registry.tools_dir / expected_name
                occupied = next((item for item in self.app.registry.scan() if item.archive == target), None)
                if occupied and occupied.uid != uid: raise ValueError
                os.replace(temporary, target); temporary = None
                application_logger().info("Store download verified: name=%r uid=%s", tool.get('name', ''), uid)
                try: self.download_finished.emit(tool, "")
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
        self.active_downloads.discard(str(tool.get('uid', '')))
        if error:
            tool['_store_state'] = 'download'; self.set_status(error); self.refresh_cards(); return
        name = str(tool.get('name', '')); tool['_store_state'] = 'installed' if is_installed(str(tool.get('uid', ''))) else 'open'; self.installed_hashes = self.installed_hashes or {}; self.installed_hashes[str(tool.get('uid', ''))] = (str(tool.get('sha256', '')).lower(), Path(str(tool.get('download_url', ''))).name); self.app.pages["library"].refresh(); self.set_status('download_complete', name=name)
        self.refresh_cards()
        if self.tool_page and str(self.tool_page.tool.get('uid', '')) == str(tool.get('uid', '')): self.tool_page.update_language()
        QMessageBox.information(self, self.app.lang["download_ready"], f"{name} {self.app.lang['download_success']}")
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
        self.render_status()
        if not self.tools:
            self.populate()
        else: self.refresh_cards()
        if self.tool_page: self.tool_page.update_language()


class ToolInfoDialog(QDialog):
    def __init__(self, tool, parent):
        super().__init__(parent); lang = parent.app.lang; self.setWindowTitle(lang["tool_info_title"]); self.resize(720, 500)
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
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(10)
        header = QFrame(); header.setObjectName("card"); header_layout = QVBoxLayout(header); self.badge = QLabel(self.text("ЭТАП 2  •  СТРУКТУРА ПРОЕКТА", "STEP 2  •  PROJECT STRUCTURE")); self.badge.setObjectName("eyebrow"); self.hint = QLabel(self.text("Щёлкните правой кнопкой мыши по дереву, чтобы создать папку, файл или загрузить файл. Изменения сохраняются в ZIP.", "Right-click the tree to create a folder, file or upload a file. Changes are saved to the ZIP.")); self.hint.setObjectName("pageSubtitle"); self.hint.setWordWrap(True); header_layout.addWidget(self.badge); header_layout.addWidget(self.hint); layout.addWidget(header)
        self.stage_note = QLabel(self.text("* Удобно отредактировать HTML Вы сможете на 3 этапе. Здесь рекомендуется создать только скрипты.", "* You will be able to edit HTML conveniently in step 3. It is recommended to create scripts only here.")); self.stage_note.setObjectName("stageNote"); self.stage_note.setWordWrap(True); layout.addWidget(self.stage_note)
        splitter = QSplitter(Qt.Horizontal); self.tree = QTreeWidget(); self.tree.setObjectName("projectTree"); self.tree.setHeaderLabel(self.text("Структура проекта", "Project structure")); self.tree.itemClicked.connect(self.open_item); self.tree.setContextMenuPolicy(Qt.CustomContextMenu); self.tree.customContextMenuRequested.connect(self.show_context_menu); splitter.addWidget(self.tree)
        editor_panel = QFrame(); editor_panel.setObjectName("card"); editor_layout = QVBoxLayout(editor_panel); self.file_name = QLabel(self.text("Выберите текстовый файл", "Select a text file")); self.file_name.setObjectName("fieldLabel"); self.content_stack = QStackedWidget(); self.editor = QPlainTextEdit(); self.editor.setObjectName("projectEditor"); self.editor.setPlaceholderText(self.text("Содержимое файла", "File contents")); self.editor.setReadOnly(True); self.highlighter = CodeHighlighter(self.editor.document()); self.image_preview = QLabel(self.text("Выберите изображение", "Select an image")); self.image_preview.setObjectName("imagePreview"); self.image_preview.setAlignment(Qt.AlignCenter); self.image_preview.setWordWrap(True); self.content_stack.addWidget(self.editor); self.content_stack.addWidget(self.image_preview); editor_layout.addWidget(self.file_name); editor_layout.addWidget(self.content_stack, 1); splitter.addWidget(editor_panel); splitter.setSizes([310, 650]); layout.addWidget(splitter, 1)
        buttons = QHBoxLayout(); self.save_state = QLabel(); self.save_state.setObjectName("pageSubtitle"); self.back = QPushButton(self.text("← К этапу 1", "← Back to step 1")); self.back.setObjectName("outlineButton"); self.back.clicked.connect(self.owner.close_project_editor); self.next = QPushButton(self.text("К этапу 3 →", "To step 3 →")); self.next.setObjectName("primaryButton"); self.next.clicked.connect(self.open_html_editor); self.save = QPushButton(self.text("Сохранить в ZIP", "Save to ZIP")); self.save.setObjectName("outlineButton"); self.save.clicked.connect(self.save_project); buttons.addWidget(self.save_state); buttons.addWidget(self.back); buttons.addStretch(); buttons.addWidget(self.save); buttons.addWidget(self.next); layout.addLayout(buttons)
        self.autosave_timer = QTimer(self); self.autosave_timer.setSingleShot(True); self.autosave_timer.timeout.connect(self.save_project); self.editor.textChanged.connect(self.mark_unsaved); self.is_saved = True
        self.refresh_tree()
        self.set_saved()
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
        self.save_state.setText(self.text("● Сохранено", "● Saved") if getattr(self, 'is_saved', True) else self.text("● Не сохранено", "● Unsaved"))
        if self.current_file is None: self.file_name.setText(self.text("Выберите текстовый файл", "Select a text file"))
    def refresh_tree(self):
        selected = str(self.current_file) if self.current_file else ""; self.tree.clear(); root = QTreeWidgetItem([self.archive_path.stem]); root.setData(0, Qt.UserRole, str(self.work_dir)); self.tree.addTopLevelItem(root)
        def add(parent, folder):
            for path in sorted(folder.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
                item = QTreeWidgetItem([path.name]); item.setData(0, Qt.UserRole, str(path)); parent.addChild(item)
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
        file_action = menu.addAction(self.text("Создать файл", "Create file")); folder_action = menu.addAction(self.text("Создать папку", "Create folder")); upload_action = menu.addAction(self.text("Загрузить файл", "Upload file")); rename_action = None; duplicate_action = None; copy_action = None; cut_action = None; paste_action = None; delete_action = None
        current = self.tree.currentItem()
        if current and Path(current.data(0, Qt.UserRole)) != self.work_dir:
            menu.addSeparator(); rename_action = menu.addAction(self.text("Переименовать", "Rename")); duplicate_action = menu.addAction(self.text("Дублировать", "Duplicate")); copy_action = menu.addAction(self.text("Копировать", "Copy")); cut_action = menu.addAction(self.text("Вырезать", "Cut")); delete_action = menu.addAction(self.text("Удалить", "Delete"))
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
                    if path.is_file() and path.name != 'html_history.json': archive.write(path, path.relative_to(self.work_dir).as_posix())
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
        self.heading = QLabel(); self.heading.setObjectName("toolName"); self.hint = QLabel(); self.hint.setObjectName("pageSubtitle"); self.hint.setWordWrap(True); card_layout.addWidget(self.heading); card_layout.addWidget(self.hint)
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
        self.preview = QWebEngineView(); self.preview_channel = QWebChannel(self.preview.page()); self.preview_bridge = EditorPreviewBridge(); self.preview_channel.registerObject("toolBridge", self.preview_bridge); self.preview.page().setWebChannel(self.preview_channel); self.logic_running = False; self.logic_finished.connect(self.render_logic_result)
        splitter = QSplitter(Qt.Horizontal); self.authoring = AuthoringWorkspace(self, CodeHighlighter); splitter.addWidget(self.authoring); splitter.addWidget(self.preview); splitter.setSizes([520, 520]); card_layout.addWidget(splitter, 1); self.save_state = QLabel(); self.save_state.setObjectName("pageSubtitle"); card_layout.addWidget(self.save_state)
        controls = QHBoxLayout(); self.back = QPushButton(); self.back.setObjectName("outlineButton"); self.back.clicked.connect(self.owner.close_html_editor); self.undo = QPushButton("↶"); self.undo.setObjectName("historyButton"); self.undo.setToolTip(self.text("Отменить", "Undo")); self.undo.clicked.connect(self.undo_source); self.redo = QPushButton("↷"); self.redo.setObjectName("historyButton"); self.redo.setToolTip(self.text("Повторить", "Redo")); self.redo.clicked.connect(self.redo_source); self.find = QPushButton(); self.find.setObjectName("outlineButton"); self.find.clicked.connect(self.find_text); self.create_element = QPushButton(); self.create_element.setObjectName("outlineButton"); self.create_menu = QMenu(self.create_element); self.create_element.setMenu(self.create_menu); self.delete_element = QPushButton(); self.delete_element.setObjectName("dangerButton"); self.delete_element.clicked.connect(self.delete_selected_element); self.bridge = QPushButton(); self.bridge.setObjectName("outlineButton"); self.bridge.clicked.connect(self.enable_bridge); self.visual = QPushButton(); self.visual.setObjectName("outlineButton"); self.visual.clicked.connect(self.toggle_visual_edit); self.apply_visual = QPushButton(); self.apply_visual.setObjectName("outlineButton"); self.apply_visual.clicked.connect(self.apply_visual_changes); self.full = QPushButton(); self.full.setObjectName("outlineButton"); self.full.clicked.connect(self.toggle_fullscreen); self.save = QPushButton(); self.save.setObjectName("primaryButton"); self.save.clicked.connect(self.save_html); controls.addWidget(self.back); controls.addWidget(self.undo); controls.addWidget(self.redo); controls.addWidget(self.find); controls.addWidget(self.create_element); controls.addWidget(self.delete_element); controls.addWidget(self.bridge); controls.addWidget(self.visual); controls.addWidget(self.apply_visual); controls.addStretch(); controls.addWidget(self.full); controls.addWidget(self.save); card_layout.addLayout(controls); self.update_language(self.lang_code); self.refresh_preview()
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
        output_ids.update(re.findall(r'\bid\s*=\s*["\']([\w.-]+)["\']', html, re.I))
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
    def build_element_menu(self):
        self.create_menu.clear()
        elements = [
            ("button", self.text("Кнопка действия", "Action button"), '<button data-scs-action="action_name">Button</button>'),
            ("input", self.text("Поле ввода", "Text input"), '<input name="value" type="text" placeholder="Enter value">'),
            ("select", self.text("Список выбора", "Select list"), '<select name="choice">\n  <option value="one">Option one</option>\n  <option value="two">Option two</option>\n</select>'),
            ("textarea", self.text("Многострочное поле", "Text area"), '<textarea name="message" placeholder="Enter text"></textarea>'),
            ("heading", self.text("Заголовок", "Heading"), '<h2>Heading</h2>'),
            ("paragraph", self.text("Текст", "Paragraph"), '<p>Text</p>'),
            ("container", self.text("Контейнер", "Container"), '<div class="container">\n  Content\n</div>'),
            ("link", self.text("Ссылка", "Link"), '<a href="https://example.com">Link</a>'),
            ("image", self.text("Изображение", "Image"), '<img src="image.png" alt="Description">'),
            ("divider", self.text("Разделитель", "Divider"), '<hr>'),
        ]
        for _, title, snippet in elements:
            action = self.create_menu.addAction(title); action.triggered.connect(lambda _=False, value=snippet: self.insert_snippet(value))
    def insert_snippet(self, snippet):
        script = '''(() => {const selected=window.__scsSelectedSelector&&document.querySelector(window.__scsSelectedSelector);
          const parent=selected||document.body; if(!parent)return null; const template=document.createElement('template'); template.innerHTML=%s;
          const element=Array.from(template.content.children)[0]; if(!element)return null;
          const bridgeMarker=parent===document.body&&Array.from(parent.childNodes).find(node=>node.nodeType===Node.COMMENT_NODE&&node.nodeValue.trim()==='SCS_TOOL_BRIDGE');
          const firstScript=parent===document.body&&Array.from(parent.children).find(node=>node.tagName==='SCRIPT');
          parent.insertBefore(element, bridgeMarker||firstScript||null);
          let node=element, parts=[]; while(node&&node.nodeType===1){let index=1,previous=node.previousElementSibling;while(previous){if(previous.tagName===node.tagName)index++;previous=previous.previousElementSibling;}parts.unshift(node.tagName.toLowerCase()+':nth-of-type('+index+')');node=node.parentElement;}
          const selector=parts.join(' > ');window.__scsSelectedSelector=selector;return {selector,html:'<!doctype html>\\n'+document.documentElement.outerHTML};})()''' % json.dumps(snippet)
        def done(result):
            if not result: return
            self.authoring.last_selection=result['selector']; self.authoring.pending_highlight=True; self.replace_source(format_html(result['html']))
            self.authoring.tabs.setCurrentIndex(0); self.source.setFocus()
        self.preview.page().runJavaScript(script, done)
    def delete_selected_element(self):
        script = '''(() => {const selector=window.__scsSelectedSelector, e=selector&&document.querySelector(selector);
        if(!e || e===document.body || e===document.documentElement) return null;
        e.remove(); window.__scsSelectedSelector=null; return '<!doctype html>\\n'+document.documentElement.outerHTML;})()'''
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
                if urlparse(self.path).path != "/__scs_bridge__": self.send_error(404); return
                try:
                    length = min(int(self.headers.get("Content-Length", "0")), 1024 * 1024)
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict): raise ValueError
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
  confirm:(title,message,accept,cancel)=>Promise.resolve(window.confirm(title+'\\n\\n'+message))
}};
window.qt=window.qt||{{}};window.qt.webChannelTransport=window.qt.webChannelTransport||{{}};
window.QWebChannel=(_transport,ready)=>ready({{objects:{{toolBridge}}}});
queueMicrotask(()=>{{if(window.setLanguage)window.setLanguage({language});}});
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


class ToolView(QWidget):
    logic_finished = pyqtSignal(str)
    def __init__(self, app, tool):
        super().__init__(); self.setObjectName('runningToolView'); self.setContentsMargins(0, 0, 0, 0); self.app, self.tool, self.logic_running = app, tool, False; self.logic_finished.connect(self.render_logic_result); self.view_layout = QVBoxLayout(self); self.view_layout.setContentsMargins(0, 0, 0, 0); self.view_layout.setSpacing(0)
        info = QFrame(); info.setObjectName("toolInfoCard"); self.info_card = info; self.info_card.setProperty("roundedTopRight", True); row = QHBoxLayout(info); row.setContentsMargins(22, 18, 22, 18)
        icon = QLabel("◆"); icon.setObjectName("largeToolIcon"); path = app.registry.icon(tool)
        if path:
            icon.setPixmap(QPixmap(str(path)).scaled(94, 94, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else: icon.setFixedSize(94, 94); icon.setAlignment(Qt.AlignCenter)
        text = QVBoxLayout(); name = QLabel(tool.name); name.setObjectName("toolName"); self.description = QLabel(tool.description_for(app.lang["_lang_code"], app.lang["tool_description_missing"])); self.description.setObjectName("toolMeta"); self.description.setWordWrap(True); self.version = QLabel(f"{app.lang['store_version']}: {tool.version or app.lang['tool_version_missing']}"); self.version.setObjectName("toolMeta")
        text.addWidget(name); text.addWidget(self.description); text.addWidget(self.version); self.more = QPushButton(app.lang["more_info"]); self.more.setObjectName("outlineButton"); self.more.clicked.connect(lambda: ToolInfoDialog(tool, self).exec_())
        self.reload_button = QPushButton("↻  " + app.lang["download_refresh_btn"]); self.reload_button.setObjectName("outlineButton"); self.reload_button.clicked.connect(lambda: self.app.pages["running"].reload_tool(self))
        actions = QVBoxLayout(); actions.addStretch(); actions.addWidget(self.reload_button); actions.addWidget(self.more); actions.addStretch(); row.addWidget(icon); row.addLayout(text, 1); row.addLayout(actions); self.view_layout.addWidget(info)
        app_card = QFrame(); app_card.setObjectName("applicationCard"); self.app_card = app_card; app_layout = QVBoxLayout(app_card); self.app_layout = app_layout; caption_row = QHBoxLayout(); self.caption = QLabel(app.lang["application"]); self.caption.setObjectName("eyebrow"); self.full_button = QPushButton(app.lang["full_screen"]); self.full_button.setObjectName("outlineButton"); self.full_button.clicked.connect(lambda: self.app.set_application_fullscreen(self, True)); caption_row.addWidget(self.caption); caption_row.addStretch(); caption_row.addWidget(self.full_button); app_layout.addLayout(caption_row)
        url = app.registry.html_url(tool)
        if url:
            if sys.platform == "darwin":
                # Recent macOS virtual machines can abort Qt 5 WebEngine when
                # the first embedded page becomes visible.  Serve the extracted
                # tool only on loopback instead: its HTML, assets and Python/JS
                # action bridge stay local to this manager process.
                try:
                    self.browser_server = BrowserToolServer(app.registry, tool, app.lang['_lang_code'])
                    self.external_tool_url = self.browser_server.url
                except (OSError, ValueError):
                    self.external_tool_url = url
                self.mac_web_notice = QLabel(); self.mac_web_notice.setObjectName("emptyHint"); self.mac_web_notice.setWordWrap(True); self.mac_web_notice.setAlignment(Qt.AlignCenter)
                self.mac_web_open = QPushButton(); self.mac_web_open.setObjectName("primaryButton"); self.mac_web_open.clicked.connect(self.open_external_tool)
                mac_russian = app.lang['_lang_code'] == 'ru'
                self.mac_web_notice.setText("На этой версии macOS HTML-инструмент открыт в системном браузере. Встроенный браузер Qt аварийно завершается в виртуальной машине." if mac_russian else "On this macOS version, the HTML tool is opened in the system browser. The embedded Qt browser crashes in this virtual machine.")
                self.mac_web_open.setText("Открыть в браузере" if mac_russian else "Open in browser")
                holder = QWidget(); holder_layout = QVBoxLayout(holder); holder_layout.addStretch(); holder_layout.addWidget(self.mac_web_notice); holder_layout.addWidget(self.mac_web_open, 0, Qt.AlignHCenter); holder_layout.addStretch(); app_layout.addWidget(holder, 1)
                QTimer.singleShot(0, self.open_external_tool)
            else:
                self.web_url = url
                self.web_placeholder = QLabel("Loading tool…"); self.web_placeholder.setObjectName("emptyHint"); self.web_placeholder.setAlignment(Qt.AlignCenter)
                app_layout.addWidget(self.web_placeholder, 1)
                # QWebEngine must be created after its parent page is put in
                # the stacked interface. Creating it from the click handler
                # while the page is still hidden can abort QtWebEngine on macOS.
                QTimer.singleShot(0, self.create_web_view)
        elif app.registry.exe_path(tool) and ExeHost is not None:
            self.exe_scroll = QScrollArea(); self.exe_scroll.setObjectName("exeScroll"); self.exe_scroll.setWidgetResizable(True); self.exe_scroll.setFrameShape(QFrame.NoFrame)
            # Родитель задаётся сразу, чтобы геометрия и clipping прокрутки были
            # корректны ещё до появления окна инструмента.
            self.exe_host = ExeHost(app.registry.exe_path(tool), app.lang, self.exe_scroll.viewport()); self.exe_scroll.setWidget(self.exe_host)
            def sync_after_scroll():
                self.exe_host.sync_overlay()
            self.exe_scroll.horizontalScrollBar().valueChanged.connect(sync_after_scroll)
            self.exe_scroll.verticalScrollBar().valueChanged.connect(sync_after_scroll)
            app_layout.addWidget(self.exe_scroll, 1)
        else:
            note = QLabel(app.lang["runtime_missing"]); note.setObjectName("emptyHint"); app_layout.addWidget(note, 1)
        self.view_layout.addWidget(app_card, 1)
    def create_web_view(self):
        if not hasattr(self, "web_url") or hasattr(self, "web"): return
        # Give WebEngine its final native parent at construction time.  On
        # macOS, constructing it parentless and reparenting through a layout
        # can make Chromium abort while changing the view visibility.
        self.web = QWebEngineView(self.app_card)
        self.channel = QWebChannel(self.web.page()); self.bridge = ToolBridge(self); self.channel.registerObject("toolBridge", self.bridge); self.web.page().setWebChannel(self.channel)
        self.web.loadFinished.connect(lambda _ok: self.web.page().runJavaScript(f"if (window.setLanguage) window.setLanguage('{self.app.lang['_lang_code']}');"))
        index = self.app_layout.indexOf(self.web_placeholder); self.app_layout.insertWidget(max(0, index), self.web, 1)
        self.web_placeholder.deleteLater(); del self.web_placeholder
        self.web.load(self.web_url)
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
    def run_logic(self, payload=""):
        if self.logic_running: return
        self.logic_running = True
        def execute():
            import json
            data = json.dumps(self.app.registry.run_logic(self.tool, payload), ensure_ascii=False)
            try: self.logic_finished.emit(data)
            except RuntimeError: pass  # Виджет мог быть закрыт до завершения фоновой логики.
        threading.Thread(target=execute, daemon=True).start()
    def render_logic_result(self, data):
        self.logic_running = False
        try: result = json.loads(data)
        except (TypeError, ValueError): result = {}
        if result.get("status") == "security_violation":
            self.show_security_warning(); return
        if hasattr(self, "web"): self.web.page().runJavaScript(f"if (window.renderTool) window.renderTool({data});")
    def show_security_warning(self):
        if hasattr(self, "security_warning"): return
        if hasattr(self, "web"): self.web.hide()
        self.security_warning = QLabel()
        self.security_warning.setWordWrap(True); self.security_warning.setAlignment(Qt.AlignCenter)
        self.security_warning.setStyleSheet("background:#32171b;border:2px solid #ff4f5e;border-radius:14px;color:#ffdde0;font-size:18px;font-weight:800;padding:32px;")
        self.security_warning.setText(self.security_warning_text())
        self.app_layout.addWidget(self.security_warning, 1)
    def security_warning_text(self):
        if self.app.lang["_lang_code"] == "ru":
            return "ВНИМАНИЕ! ИНСТРУМЕНТ ПЫТАЕТСЯ ВЫЙТИ ЗА ПРЕДЕЛЫ ЗАЩИТЫ.\nЗапуск остановлен.\n\nЕсли вы создатель, пожалуйста, записывайте файлы в папку ./tools_resources/ или в несистемную папку."
        return "WARNING! THE TOOL ATTEMPTED TO BREAK OUTSIDE THE PROTECTION.\nLaunch stopped.\n\nIf you are the author, please write files to ./tools_resources/ or to a non-system folder."
    def update_language(self, lang):
        self.description.setText(self.tool.description_for(lang["_lang_code"], lang["tool_description_missing"]))
        self.version.setText(f"{lang['store_version']}: {self.tool.version or lang['tool_version_missing']}")
        self.more.setText(lang["more_info"]); self.reload_button.setText("↻  " + lang["download_refresh_btn"]); self.caption.setText(lang["application"]); self.full_button.setText(lang["full_screen"])
        if hasattr(self, "security_warning"): self.security_warning.setText(self.security_warning_text())
        if hasattr(self, "exe_host"): self.exe_host.update_language(lang)
        if hasattr(self, "web"): self.web.page().runJavaScript(f"if (window.setLanguage) window.setLanguage('{lang['_lang_code']}');")
        if hasattr(self, "mac_web_notice"):
            russian = lang['_lang_code'] == 'ru'
            self.mac_web_notice.setText("На этой версии macOS HTML-инструмент открыт в системном браузере. Встроенный браузер Qt аварийно завершается в виртуальной машине." if russian else "On this macOS version, the HTML tool is opened in the system browser. The embedded Qt browser crashes in this virtual machine.")
            self.mac_web_open.setText("Открыть в браузере" if russian else "Open in browser")


class RunningPage(Page):
    def __init__(self, app):
        super().__init__(app, "running"); self.title, self.subtitle = self.heading("", "")
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
        view = ToolView(self.app, tool); self.tool_pages.addWidget(view); self.tab_bar.addTab(tool.name); self.tab_bar.setCurrentIndex(self.tab_bar.count() - 1); self.app.sync_full_tabs(); QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.ensure_current_tab_visible); QTimer.singleShot(0, self.update_info_corner)
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
        self.app.registry.cleanup_tool(old_tool)
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
    def close_tool(self, view):
        index = self.tool_pages.indexOf(view)
        if index >= 0:
            tool = view.tool; is_exe = hasattr(view, "exe_host")
            view.hide()
            if is_exe: view.exe_host.stop()
            if hasattr(view, "browser_server"): view.browser_server.close()
            if hasattr(view, "web"): view.web.setUrl(QUrl("about:blank"))
            self.tab_bar.removeTab(index); self.tool_pages.removeWidget(view); self.app.sync_full_tabs(); view.deleteLater(); QTimer.singleShot(0, self.update_tab_scrollbar); QTimer.singleShot(0, self.update_info_corner)
            if not self.tool_pages.count():
                if self.app.full_view is view: self.app.set_application_fullscreen(view, False)
                self.app.buttons["running"][0].hide()
                QTimer.singleShot(0, lambda: self.app.show_page("library"))
            QTimer.singleShot(250, lambda: self.finish_cleanup(tool, is_exe))
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
            if hasattr(view, "web"): view.web.setUrl(QUrl("about:blank"))
    def activate_current(self, _index=None):
        self.tool_pages.setCurrentIndex(self.tab_bar.currentIndex()); QTimer.singleShot(0, self.ensure_current_tab_visible); QTimer.singleShot(0, self.update_info_corner)
        view = self.tool_pages.currentWidget()
        if view and hasattr(view, "exe_host"): QTimer.singleShot(0, lambda: view.exe_host.sync_overlay(force=True))
    def update_text(self, lang):
        self.title.setText(lang["tab_running"]); self.subtitle.setText(lang["running_subtitle"])
        for index in range(self.tool_pages.count()): self.tool_pages.widget(index).update_language(lang)


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
        super().__init__(app, "library"); self.title, self.subtitle = self.heading("", ""); bar = QHBoxLayout(); bar.addStretch(); self.refresh_button = QPushButton(); self.refresh_button.setObjectName("outlineButton"); self.refresh_button.clicked.connect(self.refresh); bar.addWidget(self.refresh_button); self.layout.addLayout(bar); self.scroll = QScrollArea(); self.scroll.setObjectName("libraryScroll"); self.scroll.setWidgetResizable(True); self.scroll.setFrameShape(QFrame.NoFrame); self.content = QWidget(); self.content.setObjectName("libraryContent"); self.list = QVBoxLayout(self.content); self.list.setContentsMargins(0, 0, 0, 4); self.list.setSpacing(9); self.list.setAlignment(Qt.AlignTop); self.scroll.setWidget(self.content); self.layout.addWidget(self.scroll, 1); self.update_text(app.lang)
    def verified_original_archives(self, tools):
        groups = {}
        for tool in tools: groups.setdefault(tool.uid, []).append(tool)
        pages = getattr(self.app, 'pages', {})
        store = pages.get('store') if isinstance(pages, dict) else None
        if not getattr(store, 'verified_loaded', False): return set()
        verified_tools = {str(item.get('uid')): (str(item.get('sha256')).lower(), Path(str(item.get('download_url', ''))).name) for item in getattr(store, 'tools', []) if isinstance(item, dict) and re.fullmatch(r'[0-9a-fA-F]{64}', str(item.get('sha256', '')))}
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
        store = self.app.pages.get('store')
        if not getattr(store, 'verified_loaded', False): return
        entry = next((item for item in store.tools if str(item.get('uid', '')) == tool.uid), None)
        if not isinstance(entry, dict): return
        entry['_store_state'] = 'update'
        store.refresh_cards(); store.action(entry)
    def refresh(self):
        while self.list.count():
            item = self.list.takeAt(0); item.widget() and item.widget().deleteLater()
        tools = self.app.registry.scan()
        if not tools:
            empty = self.card(); empty.setObjectName("emptyCard"); label = QLabel(self.app.lang["library_empty"]); label.setObjectName("emptyTitle"); l = QVBoxLayout(empty); l.addWidget(label, alignment=Qt.AlignCenter); self.list.addWidget(empty); self.list.addStretch(); return
        uid_warnings = self.duplicate_uid_warnings(tools); blocked_archives = self.duplicate_uid_blocked_archives(tools); hash_warnings = self.sha_mismatch_archives(tools)
        for tool in tools:
            row = DownloadedToolCard(self.app.registry.banner(tool)); r = QHBoxLayout(row); r.setContentsMargins(20, 14, 20, 14); icon = QLabel("◆"); icon.setObjectName("toolIcon")
            path = self.app.registry.icon(tool)
            if path: icon.setPixmap(QPixmap(str(path)).scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            text = QVBoxLayout(); text.setSpacing(0); text.setAlignment(Qt.AlignVCenter); name = QLabel(tool.name); name.setObjectName("downloadedToolName"); name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed); desc = QLabel(tool.description_for(self.app.lang["_lang_code"], self.app.lang["tool_description_missing"])); desc.setObjectName("downloadedToolDescription"); desc.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed); desc.setWordWrap(True); text.addWidget(name); text.addWidget(desc)
            if tool.archive in uid_warnings:
                warning = QLabel("⚠ " + ("Повторяющийся UID: этот ZIP конфликтует с другим инструментом." if self.app.lang['_lang_code'] == 'ru' else "Duplicate UID: this ZIP conflicts with another tool.")); warning.setObjectName('duplicateUidWarning'); warning.setWordWrap(True); text.addWidget(warning)
            if tool.archive in hash_warnings:
                warning = QWidget(); warning.setObjectName('hashMismatchWarning'); warning_layout = QHBoxLayout(warning); warning_layout.setContentsMargins(8, 4, 5, 4); warning_layout.setSpacing(8)
                warning_text = QLabel("⚠ " + ("SHA-256 этого локального ZIP не совпадает с версией инструмента в Магазине." if self.app.lang['_lang_code'] == 'ru' else "This local ZIP's SHA-256 does not match the tool version in Store.")); warning_text.setWordWrap(True)
                update = QPushButton("Обновить" if self.app.lang['_lang_code'] == 'ru' else "Update"); update.setObjectName('hashWarningUpdate'); update.clicked.connect(lambda _=False, t=tool: self.update_from_sha_warning(t)); warning_layout.addWidget(warning_text, 1); warning_layout.addWidget(update); text.addWidget(warning)
            blocked = tool.archive in blocked_archives
            runtime_type = str(tool.runtime.get("type", "")); unsupported_exe = runtime_type == "exe" and sys.platform != "win32"
            if unsupported_exe:
                system_notice = QLabel("Недоступно на Вашей системе" if self.app.lang['_lang_code'] == 'ru' else "Unavailable on your system"); system_notice.setObjectName('systemUnavailable'); system_notice.setWordWrap(True); text.addWidget(system_notice)
            blocked = blocked or unsupported_exe
            installed = is_installed(tool.uid)
            install_button = QPushButton("Деинсталлировать" if self.app.lang['_lang_code'] == 'ru' else "Uninstall") if installed else QPushButton("Установить" if self.app.lang['_lang_code'] == 'ru' else "Install")
            install_button.setObjectName(('dangerButton' if installed else 'outlineButton') if sys.platform == 'win32' else 'disabledButton'); install_button.setMinimumWidth(150); install_button.setEnabled(sys.platform == 'win32' and (installed or not blocked))
            install_button.clicked.connect((lambda _, t=tool: self.app.uninstall_downloaded_tool(t.uid)) if installed else (lambda _, t=tool: self.app.show_install_dialog(t)))
            btn = QPushButton("▶  " + self.app.lang["open"]); btn.setObjectName("disabledButton" if blocked else "primaryButton"); btn.setMinimumWidth(150); btn.setEnabled(not blocked); btn.setToolTip("Инструмент нельзя запустить: этот ZIP — дубликат UID, а оригинал уже установлен." if self.app.lang['_lang_code'] == 'ru' and blocked else "This tool cannot run: this ZIP duplicates a UID and the original is already installed." if blocked else ""); btn.clicked.connect(lambda _, t=tool: self.app.open_tool(t)); delete = QPushButton("Удалить" if self.app.lang['_lang_code'] == 'ru' else "Delete"); delete.setObjectName('dangerButton'); delete.setMinimumWidth(150); delete.clicked.connect(lambda _, t=tool: self.app.delete_tool(t)); actions = QVBoxLayout(); actions.addWidget(install_button); actions.addWidget(btn); actions.addWidget(delete); r.addWidget(icon); r.addLayout(text, 1); r.addLayout(actions); self.list.addWidget(StoreCardSlot(row))
    def update_text(self, lang): self.title.setText(lang["library_header"]); self.subtitle.setText(lang["downloaded_subtitle"]); self.refresh_button.setText("↻  " + lang["download_refresh_btn"]); self.refresh()


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
        for key in ("version", "author", "category"):
            self.add_field(l, key)
        self.section_tech = QLabel(); self.section_tech.setObjectName("toolName"); l.addWidget(self.section_tech)
        self.uid_label = QLabel(); self.uid_label.setObjectName("fieldLabel"); self.uid = QLineEdit(self.generate_unique_uid()); self.uid.setObjectName("input"); self.uid.textChanged.connect(self.update_action_text)
        self.uid_regenerate = QPushButton(); self.uid_regenerate.setObjectName("outlineButton"); self.uid_regenerate.clicked.connect(lambda: self.uid.setText(self.generate_unique_uid()))
        uid_row = QHBoxLayout(); uid_row.addWidget(self.uid, 1); uid_row.addWidget(self.uid_regenerate); l.addWidget(self.uid_label); l.addLayout(uid_row)
        self.runtime_label = QLabel(); self.runtime_label.setObjectName("fieldLabel"); self.runtime_combo = NoWheelComboBox(); self.runtime_combo.setObjectName("input")
        self.runtime_combo.addItem("HTML + Python", "html_python"); self.runtime_combo.addItem("HTML + JavaScript", "html_script"); self.runtime_combo.addItem("EXE", "exe"); self.runtime_combo.addItem("Not selected", ""); self.runtime_combo.currentIndexChanged.connect(self.update_runtime_inputs); self.runtime_combo.currentIndexChanged.connect(lambda _=None: self.update_text(self.app.lang))
        l.addWidget(self.runtime_label); l.addWidget(self.runtime_combo)
        self.entry_label = QLabel(); self.entry_label.setObjectName("fieldLabel"); self.entry_path = QLineEdit(); self.entry_path.setObjectName("input"); self.entry_browse = QPushButton(); self.entry_browse.setObjectName("outlineButton"); self.entry_browse.clicked.connect(lambda: self.pick_runtime_file("entry")); entry_row = QHBoxLayout(); entry_row.addWidget(self.entry_path, 1); entry_row.addWidget(self.entry_browse); l.addWidget(self.entry_label); l.addLayout(entry_row)
        self.logic_label = QLabel(); self.logic_label.setObjectName("fieldLabel"); self.logic_path = QLineEdit(); self.logic_path.setObjectName("input"); l.addWidget(self.logic_label); l.addWidget(self.logic_path)
        self.icon_label = QLabel(); self.icon_label.setObjectName("fieldLabel"); self.icon_path = QLineEdit(); self.icon_path.setObjectName("input"); self.icon_path.setReadOnly(True); self.icon_browse = QPushButton(); self.icon_browse.setObjectName("outlineButton"); self.icon_browse.clicked.connect(self.pick_icon)
        icon_row = QHBoxLayout(); icon_row.addWidget(self.icon_path, 1); icon_row.addWidget(self.icon_browse); l.addWidget(self.icon_label); l.addLayout(icon_row)
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
    def text(self, ru, en): return ru if self.app.lang["_lang_code"] == "ru" else en
    def pick_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, self.text("Выберите иконку", "Choose an icon"), "", "Images (*.png *.ico *.jpg *.jpeg *.webp);;All files (*)")
        if path: self.icon_path.setText(path); self.save_draft()
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
        for _, edit in self.fields.values(): edit.textChanged.connect(self.save_draft)
    def draft_data(self):
        return {"fields": {key: edit.text() for key, (_, edit) in self.fields.items()}, "descriptions": {code: edit.text() for code, (_, edit, _) in self.description_fields.items()}, "uid": self.uid.text(), "runtime": self.runtime_combo.currentData(), "entry": self.entry_path.text(), "logic": self.logic_path.text(), "icon": self.icon_path.text(), "banner": self.banner_path.text()}
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
            "runtime": "html_python", "entry": "", "logic": "", "icon": "", "banner": ""
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
        data = {"fields": {key: info.get(key, "") for key in self.fields}, "descriptions": descriptions, "uid": info.get("uid", ""), "runtime": runtime.get("type", "html_python"), "entry": runtime.get("entry", ""), "logic": runtime.get("logic", runtime.get("script", "")), "icon": "", "banner": ""}
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
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "new_tool"
        target = existing.archive if existing else self.app.registry.tools_dir / f"{safe}.zip"; number = 2
        while not existing and target.exists(): target = self.app.registry.tools_dir / f"{safe}_{number}.zip"; number += 1
        info = {"uid": self.uid.text(), "name": name}
        for code, (_, edit, _) in self.description_fields.items():
            value = edit.text().strip()
            if value: info[f"desc_{code}"] = value
        for key in ("version", "author", "category"):
            value = self.fields[key][1].text().strip()
            if value: info[key] = value
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
        labels = {"name":("Название инструмента *", "Tool name *"), "version":("Версия", "Version"), "author":("Автор", "Author"), "category":("Категория", "Category")}
        for key, (label, _) in self.fields.items(): label.setText(self.text(*labels[key]))
        self.descriptions_title.setText(self.text("Описания", "Descriptions"))
        for code, (label, _, required) in self.description_fields.items():
            names = dict(languages.available_languages()); language_name = names.get(code, code.upper())
            label.setText(self.text(f"Описание — {language_name}" + (" *" if required else ""), f"Description — {language_name}" + (" *" if required else "")))
        current = self.description_language.currentData(); self.description_language.blockSignals(True); self.description_language.clear()
        for code, name in languages.available_languages():
            if code != "en" and code not in self.description_fields: self.description_language.addItem(name, code)
        index = self.description_language.findData(current)
        if index >= 0: self.description_language.setCurrentIndex(index)
        self.description_language.blockSignals(False); self.description_language.setEnabled(self.description_language.count() > 0); self.add_description_button.setEnabled(self.description_language.count() > 0); self.add_description_button.setText("+  " + self.text("Добавить язык", "Add language"))
        self.uid_label.setText(self.text("UID (64 символа)", "UID (64 characters)")); self.uid_regenerate.setText(self.text("Новый UID", "New UID")); self.runtime_label.setText(self.text("Тип запуска *", "Runtime type *")); self.entry_label.setText(self.text("Название HTML-файла *" if self.runtime_combo.currentData() != "exe" else "EXE-файл *", "HTML file name *" if self.runtime_combo.currentData() != "exe" else "EXE file *")); self.logic_label.setText(self.text("Название Python-файла *" if self.runtime_combo.currentData() == "html_python" else "Название JavaScript-файла *", "Python file name *" if self.runtime_combo.currentData() == "html_python" else "JavaScript file name *")); self.entry_path.setPlaceholderText("app.html" if self.runtime_combo.currentData() != "exe" else "Choose an EXE…"); self.logic_path.setPlaceholderText("logic.py" if self.runtime_combo.currentData() == "html_python" else "logic.js"); self.entry_browse.setText(self.text("Обзор…", "Browse…")); self.icon_label.setText(self.text("Иконка (необязательно)", "Icon (optional)")); self.icon_browse.setText(self.text("Обзор…", "Browse…")); self.delete_existing.setText(self.text("Удалить инструмент по UID", "Delete tool by UID")); self.update_action_text()
        self.banner_label.setText(self.text("Шапка (необязательно)", "Banner (optional)")); self.banner_browse.setText(self.text("Обзор…", "Browse…"))
        if self.stage_stack.currentWidget() is not self.stage_one:
            is_html_stage = self.html_editor is not None and self.stage_stack.currentWidget() is self.html_editor
            self.title.setText(self.text("HTML редактор", "HTML editor") if is_html_stage else self.text("Структура инструмента", "Tool structure")); self.subtitle.setText(self.text("Этап 3 из 3 — настройте внешний вид и связь элементов со скриптом.", "Step 3 of 3 — refine the appearance and connect controls to script logic.") if is_html_stage else self.text("Этап 2 из 3 — добавьте и отредактируйте файлы проекта.", "Step 2 of 3 — add and edit project files."))
            if self.project_editor: self.project_editor.update_language(lang["_lang_code"])
            if self.html_editor: self.html_editor.update_language(lang["_lang_code"])


class SettingsPage(Page):
    def __init__(self, app):
        super().__init__(app, "settings"); self.title, self.subtitle = self.heading("", "")
        card = self.card(); l = QVBoxLayout(card); l.setContentsMargins(28, 24, 28, 24)
        self.lang_label = QLabel(); self.lang_label.setObjectName("fieldLabel"); self.combo = NoWheelComboBox(); self.combo.setObjectName("input"); self.combo.currentIndexChanged.connect(self.changed)
        l.addWidget(self.lang_label); l.addWidget(self.combo); self.theme_label = QLabel(); self.theme_label.setObjectName("fieldLabel"); self.theme_value = QLabel(); self.theme_value.setObjectName("muted")
        l.addWidget(self.theme_label); l.addWidget(self.theme_value); self.layout.addWidget(card); self.layout.addStretch(); self.update_text(app.lang)
    def changed(self):
        code = self.combo.currentData()
        if code and code != self.app.lang["_lang_code"]: self.app.change_language(code)
    def update_text(self, lang):
        self.title.setText(lang["settings_header"]); self.subtitle.setText(lang["settings_subtitle"]); self.lang_label.setText(lang["settings_language"]); self.theme_label.setText(lang["settings_theme"]); self.theme_value.setText(lang["settings_theme_value"])
        self.combo.blockSignals(True); self.combo.clear()
        for code, name in languages.available_languages(): self.combo.addItem(name, code)
        self.combo.setCurrentIndex(self.combo.findData(lang["_lang_code"])); self.combo.blockSignals(False)


class MainWindow(QMainWindow):
    launcher_resolution_done = pyqtSignal(object, str)
    def __init__(self):
        super().__init__(); self.settings = load_settings(); self.lang = languages.get_lang(self.settings["language"]); self.registry = ToolRegistry(); self.setWindowTitle("SCS Tools Manager"); self.setWindowIcon(QIcon(resource_path("assets", "icon.ico"))); self.resize(1200, 780); self.setMinimumSize(1000, 680)
        self.launcher_resolution_done.connect(self.finish_launcher_resolution)
        root = AnimatedBackground(); root.setObjectName("root"); root.setAttribute(Qt.WA_OpaquePaintEvent, True); self.setCentralWidget(root); self.root_layout = layout = QHBoxLayout(root); layout.setContentsMargins(20, 12, 20, 14); layout.setSpacing(18)
        side = QFrame(); self.sidebar = side; side.setObjectName("sidebar"); side.setFixedWidth(225); sl = QVBoxLayout(side); sl.setContentsMargins(13, 20, 13, 15); sl.setSpacing(7)
        brand = QLabel("SCS  TOOLS"); brand.setObjectName("brand"); sl.addWidget(brand); self.nav_title = QLabel(); self.nav_title.setObjectName("navTitle"); sl.addWidget(self.nav_title); self.buttons = {}
        for key, icon in (("dashboard", "⌂"), ("store", "▣"), ("library", "↓"), ("running", "▤"), ("create", "+"), ("settings", "⚙")):
            b = QPushButton(); b.setObjectName("navButton"); b.setCheckable(True); b.clicked.connect(lambda _, k=key: self.show_page(k)); sl.addWidget(b); self.buttons[key] = (b, icon)
            if key == "running": b.hide()
        sl.addStretch(); version = QLabel("v1.0.0"); version.setObjectName("muted"); sl.addWidget(version); layout.addWidget(side)
        self.content = content = QVBoxLayout(); self.header_box = QWidget(); header = QHBoxLayout(self.header_box); header.setContentsMargins(0, 0, 0, 0); self.exit_button = QPushButton(); self.exit_button.setObjectName("outlineButton"); self.exit_button.setFixedHeight(34); self.exit_button.clicked.connect(self.close); self.lang_button = QPushButton("RU / EN"); self.lang_button.setObjectName("outlineButton"); self.lang_button.setFixedHeight(34); self.lang_button.clicked.connect(lambda: self.change_language("en" if self.lang["_lang_code"] == "ru" else "ru")); header.addStretch(); header.addWidget(self.exit_button); header.addWidget(self.lang_button); content.addWidget(self.header_box)
        self.stack = QStackedWidget(); self.pages = {"dashboard": HomePage(self), "store": StorePage(self), "library": LibraryPage(self), "running": RunningPage(self), "create": CreatePage(self), "settings": SettingsPage(self)}
        for p in self.pages.values(): self.stack.addWidget(p)
        self.full_reveal = QFrame(); self.full_reveal.setFixedHeight(1); self.full_reveal.hide(); self.full_reveal.installEventFilter(self)
        self.full_toolbar = QFrame(); self.full_toolbar.setObjectName("fullToolbar"); self.full_toolbar.setMaximumHeight(0); self.full_toolbar.hide(); self.full_toolbar.installEventFilter(self)
        full_layout = QHBoxLayout(self.full_toolbar); full_layout.setContentsMargins(8, 0, 8, 0); self.full_tabs = QTabBar(); self.full_tabs.setObjectName("runningTabs"); self.full_tabs.setTabsClosable(True); self.full_tabs.setUsesScrollButtons(False); self.full_tabs.setExpanding(False); self.full_tabs.setDrawBase(False); self.full_tabs.setFixedHeight(34); self.full_tabs.tabCloseRequested.connect(lambda i: self.pages["running"].close_index(i)); self.full_tabs.currentChanged.connect(lambda i: i >= 0 and self.pages["running"].tab_bar.setCurrentIndex(i)); self.full_tabs.currentChanged.connect(lambda _i: QTimer.singleShot(0, self.ensure_full_current_tab_visible)); self.full_tab_scroll = QScrollArea(); self.full_tab_scroll.setObjectName("runningTabScroll"); self.full_tab_scroll.setFrameShape(QFrame.NoFrame); self.full_tab_scroll.setWidgetResizable(False); self.full_tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); self.full_tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.full_tab_scroll.setWidget(self.full_tabs); full_layout.addWidget(self.full_tab_scroll, 1)
        self.full_exit_mode = QPushButton(); self.full_exit_mode.setObjectName("outlineButton"); self.full_exit_mode.clicked.connect(self.exit_full_mode); self.full_lang = QPushButton("RU / EN"); self.full_lang.setObjectName("outlineButton"); self.full_lang.clicked.connect(lambda: self.change_language("en" if self.lang["_lang_code"] == "ru" else "ru")); self.full_exit = QPushButton(); self.full_exit.setObjectName("outlineButton"); self.full_exit.clicked.connect(self.close); full_layout.addWidget(self.full_exit_mode); full_layout.addWidget(self.full_lang); full_layout.addWidget(self.full_exit)
        content.addWidget(self.full_reveal); content.addWidget(self.full_toolbar); content.addWidget(self.stack, 1); layout.addLayout(content, 1); self.full_view = None; self.full_mode_kind = None; self.full_hover_timer = QTimer(self); self.full_hover_timer.setInterval(50); self.full_hover_timer.timeout.connect(self.check_full_hover); self._scroll_animations = {}; self._scroll_targets = {}; QApplication.instance().installEventFilter(self)
        self.update_text(); last_page = self.settings.get('last_page', 'dashboard'); self.show_page('library' if last_page == 'running' else last_page if last_page in self.pages else 'dashboard')
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
        if enabled: self.sync_full_tabs(); self.full_hover_timer.start()
        else:
            self.full_hover_timer.stop(); self.full_toolbar.hide(); self.full_toolbar.setMaximumHeight(0); self.full_toolbar.setGraphicsEffect(None); self.full_toolbar_effect = None
    def set_creation_fullscreen(self, panel, enabled):
        if enabled:
            self.full_view = panel; self.full_mode_kind = "creation"; self.sidebar.hide(); self.header_box.hide(); self.pages["create"].title.hide(); self.pages["create"].subtitle.hide(); self.full_tab_scroll.hide(); self.full_reveal.show(); self.root_layout.setContentsMargins(0, 0, 0, 0); self.full_hover_timer.start()
        else:
            self.full_view = None; self.full_mode_kind = None; self.sidebar.show(); self.header_box.show(); self.pages["create"].title.show(); self.pages["create"].subtitle.show(); self.full_tab_scroll.show(); self.full_reveal.hide(); self.full_hover_timer.stop(); self.full_toolbar.hide(); self.full_toolbar.setMaximumHeight(0); self.root_layout.setContentsMargins(20, 12, 20, 14)
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
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            area, parent = None, watched
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    area = parent; break
                parent = parent.parentWidget() if hasattr(parent, "parentWidget") else None
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
        for key, (button, icon) in self.buttons.items(): button.setText(f"{icon}   {self.lang['tab_' + ('store' if key == 'store' else 'library' if key == 'library' else key)]}")
        for page in self.pages.values(): page.update_text(self.lang)
    def change_language(self, code):
        self.lang = languages.get_lang(code); self.settings["language"] = code; save_settings(self.settings); self.update_text()
    def is_verified_original(self, tool):
        store = self.pages.get("store")
        if not getattr(store, "verified_loaded", False): return False
        verified = next((item for item in store.tools if str(item.get("uid", "")) == tool.uid), None)
        if not isinstance(verified, dict): return False
        expected_hash = str(verified.get("sha256", "")).lower()
        expected_name = Path(str(verified.get("download_url", ""))).name
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or tool.archive.name != expected_name: return False
        try: return file_sha256(tool.archive).lower() == expected_hash
        except OSError: return False
    def confirm_unknown_tool(self, message):
        box = QMessageBox(self); box.setIcon(QMessageBox.Warning); box.setWindowTitle(self.lang['error']); box.setText(message)
        open_button = box.addButton("Открыть" if self.lang['_lang_code'] == 'ru' else "Open", QMessageBox.AcceptRole)
        box.addButton("Отмена" if self.lang['_lang_code'] == 'ru' else "Cancel", QMessageBox.RejectRole); box.exec_()
        return box.clickedButton() is open_button
    def open_tool(self, tool, warn_unknown=True):
        if warn_unknown and not self.is_created_tool(tool.uid) and not self.is_verified_original(tool):
            if not self.confirm_unknown_tool("Открывается неизвестный инструмент: он не найден среди проверенных архивов Verified Tools." if self.lang['_lang_code'] == 'ru' else "An unknown tool is being opened: it was not found among Verified Tools archives."): return
        duplicates = [item for item in self.registry.scan() if item.uid == tool.uid]
        if len(duplicates) > 1 and not self.is_verified_original(tool):
            QMessageBox.warning(self, self.lang['error'], "Невозможно запустить инструмент: этот ZIP является дубликатом UID. Запуск разрешён только для оригинала из Verified Tools." if self.lang['_lang_code'] == 'ru' else "Cannot launch the tool: this ZIP duplicates a UID. Only the original from Verified Tools may run.")
            return
        self.buttons["running"][0].show(); self.pages["running"].open_tool(tool); self.show_page("running")
    def show_install_dialog(self, tool):
        duplicates = [item for item in self.registry.scan() if item.uid == tool.uid]
        if len(duplicates) > 1 and not self.is_verified_original(tool):
            QMessageBox.warning(self, self.lang['error'], "Нельзя установить дубликат UID. Установите оригинальный архив из Verified Tools." if self.lang['_lang_code'] == 'ru' else "A duplicate UID cannot be installed. Install the original archive from Verified Tools.")
            return
        InstallationDialog(self, tool).exec_()
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
    def delete_tool(self, tool):
        box = QMessageBox(QMessageBox.Warning, self.lang['error'], "Удалить ZIP-архив инструмента?" if self.lang['_lang_code'] == 'ru' else "Delete this tool ZIP archive?", QMessageBox.Yes | QMessageBox.Cancel, self)
        box.button(QMessageBox.Yes).setText("Удалить" if self.lang['_lang_code'] == 'ru' else "Delete"); box.button(QMessageBox.Cancel).setText("Отмена" if self.lang['_lang_code'] == 'ru' else "Cancel")
        remove_installation = QCheckBox("Деинсталлировать: удалить .scstool и ярлыки" if self.lang['_lang_code'] == 'ru' else "Uninstall: remove the .scstool file and shortcuts")
        box.setCheckBox(remove_installation)
        if box.exec_() != QMessageBox.Yes: return
        try: tool.archive.unlink()
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
            if not self.confirm_unknown_tool("Инструмент не найден в Verified Tools и был восстановлен из .scstool со встроенным ZIP. Он считается непроверенным." if self.lang['_lang_code'] == 'ru' else "The tool was not found in Verified Tools and was restored from a one-file .scstool. It is considered unverified."): return
        elif not verified and not self.is_created_tool(tool.uid):
            if not self.confirm_unknown_tool("Открывается неизвестный инструмент: он не найден в Verified Tools." if self.lang['_lang_code'] == 'ru' else "An unknown tool is being opened: it was not found in Verified Tools."): return
        self.open_tool(tool, warn_unknown=False)
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
                expected_hash = str(entry.get('sha256', '')).lower(); archive_name = Path(str(entry.get('download_url', ''))).name
                if not re.fullmatch(r'[0-9a-f]{64}', expected_hash) or not archive_name or archive_name != str(entry.get('download_url', '')) or archive_name.lower().endswith('.zip') is False: raise ValueError
                url = urljoin(STORE_CATALOG_URL, f"verified_tools/{entry['uid']}/{archive_name}")
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
            QMessageBox.warning(self, self.lang['error'], "Не удалось найти и установить инструмент из .scstool." if self.lang['_lang_code'] == 'ru' else "Unable to find and install the tool from .scstool."); return
        tool = self.find_tool_by_launcher_uid(str(data['uid']))
        if not tool:
            QMessageBox.warning(self, self.lang['error'], "Инструмент из .scstool не удалось установить." if self.lang['_lang_code'] == 'ru' else "The tool from .scstool could not be installed."); return
        self.pages['library'].refresh()
        if status == 'unverified':
            QMessageBox.warning(self, self.lang['error'], "Инструмент не найден в Verified Tools и был восстановлен из .scstool со встроенным ZIP. Он считается непроверенным." if self.lang['_lang_code'] == 'ru' else "The tool was not found in Verified Tools and was restored from a one-file .scstool. It is considered unverified.")
        self.show_install_dialog(tool)
    def open_launcher_file(self, path):
        try:
            data = read_launcher(Path(path)); uid = str(data['uid']).lower()
        except (OSError, ValueError):
            QMessageBox.warning(self, self.lang['error'], "Не удалось открыть файл .scstool." if self.lang['_lang_code'] == 'ru' else "Unable to open the .scstool file."); return
        tool = self.find_tool_by_launcher_uid(uid)
        if tool:
            store = self.pages['store']; verified = bool(self.verified_launcher_entry(uid)) or not store.verified_loaded
            self.open_launcher_tool(tool, verified=verified); return
        self.resolve_missing_launcher(data)
    def close_tool(self, view): self.pages["running"].close_tool(view)
    def closeEvent(self, event):
        self.pages["running"].stop_all()
        self.registry.cleanup_all()
        super().closeEvent(event)
    def show_page(self, key):
        previous_page = self.stack.currentWidget()
        if previous_page is not self.pages[key]: previous_page.setGraphicsEffect(None)
        self.current_key = key; self.settings['last_page'] = key; save_settings(self.settings); self.stack.setCurrentWidget(self.pages[key])
        for name, (b, _) in self.buttons.items(): b.setChecked(name == key)
        if key == 'dashboard': self.pages['dashboard'].refresh_overview()
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
    configure_logging()
    # `run()` is also used directly during development; do not rely on
    # main.py to request Windows' 1 ms scheduler resolution.
    timer_resolution_requested = False
    try:
        ctypes.windll.winmm.timeBeginPeriod(1); timer_resolution_requested = True
    except Exception: pass
    try: register_file_association(); ensure_manager_shortcut()
    except OSError: pass
    app = QApplication(sys.argv); app.setApplicationName("SCS Tools Manager"); app.setWindowIcon(QIcon(resource_path("assets", "icon.ico"))); app.installEventFilter(ActionLogger(app)); application_logger().info("Application started")
    app.setStyleSheet(STYLE); window = MainWindow(); window.show()
    if launcher_file: QTimer.singleShot(0, lambda: window.open_launcher_file(launcher_file))
    try: return app.exec_()
    finally:
        if timer_resolution_requested:
            try: ctypes.windll.winmm.timeEndPeriod(1)
            except Exception: pass


STYLE = '''
QWidget#root { background:transparent; color:#ff6b5f; font: 10pt "Segoe UI"; }
QLabel { color:#ff6b5f; }
QScrollBar:vertical { background:#171c23; width:12px; margin:0; border:1px solid #303744; border-radius:6px; }
QScrollBar::handle:vertical { min-height:34px; margin:2px; background:#c95829; border:2px solid #171c23; border-radius:5px; }
QScrollBar::handle:vertical:hover { background:#ff814a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:0; background:transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { background:#171c23; height:12px; margin:0; border:1px solid #303744; border-radius:6px; }
QScrollBar::handle:horizontal { min-width:34px; margin:2px; background:#c95829; border:2px solid #171c23; border-radius:5px; }
QScrollBar::handle:horizontal:hover { background:#ff814a; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; border:0; background:transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
QFrame#sidebar, QFrame#card, QFrame#statCard, QFrame#toolCard, QFrame#emptyCard { background:#191d25; border:1px solid #303744; border-radius:18px; }
QFrame#hero { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #252c39, stop:1 #1a1e26); border:1px solid #3d4655; border-radius:22px; }
QFrame#toolInfoCard, QFrame#applicationCard { background:#191d25; border:1px solid #303744; border-radius:18px; }
QFrame#toolInfoCard { border-top:0; border-top-left-radius:0; border-top-right-radius:0; border-bottom-left-radius:18px; border-bottom-right-radius:18px; } QFrame#toolInfoCard[roundedTopRight="true"] { border-top-right-radius:18px; }
QFrame#applicationCard[fullscreen="true"] { border:0; border-radius:0; } QFrame#fullToolbar { background:#101216; border-bottom:1px solid #303744; }
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
