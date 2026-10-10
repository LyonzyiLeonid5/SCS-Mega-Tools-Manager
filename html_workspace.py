"""Additional HTML authoring panels; no third-party editor assets required."""
import json
import re
import uuid
from html.parser import HTMLParser
from html import unescape, escape
from pathlib import Path
from settings_store import save_settings
from PyQt5.QtCore import QTimer, Qt, QUrl
from PyQt5.QtGui import QCursor, QColor, QBrush, QTextCursor
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPlainTextEdit, QPushButton, QLabel, QLineEdit, QFormLayout, QScrollArea,
    QMessageBox, QComboBox, QDialog, QTableWidget, QTableWidgetItem, QListWidget, QMenu, QColorDialog, QInputDialog, QTextBrowser, QFrame, QSizePolicy, QTreeWidget, QTreeWidgetItem, QSpinBox, QCheckBox)


class PropertyChoice(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

    def text(self):
        return self.currentData()


class _PreviewNode:
    def __init__(self, tag='root', attrs=None):
        self.tag, self.attrs, self.children, self.text = tag, dict(attrs or ()), [], ''


class _PreviewParser(HTMLParser):
    """Small, deliberately safe parser for the Configure preview tree."""
    VOID = {'area', 'base', 'br', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr'}
    def __init__(self):
        super().__init__(convert_charrefs=True); self.root = _PreviewNode(); self.stack = [self.root]
    def handle_starttag(self, tag, attrs):
        node = _PreviewNode(tag.lower(), attrs); self.stack[-1].children.append(node)
        if node.tag not in self.VOID: self.stack.append(node)
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs)
    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower(): del self.stack[index:]; break
    def handle_data(self, data): self.stack[-1].text += data


class ConfigurePreview(QScrollArea):
    """Visual preview that renders common HTML controls as real Qt controls."""
    BLOCKS = {'main', 'div', 'section', 'article', 'aside', 'header', 'footer', 'form', 'fieldset', 'details'}
    def __init__(self, height):
        super().__init__(); self.setObjectName('elementPreview'); self.setWidgetResizable(True); self.setFrameShape(QFrame.NoFrame); self.setFixedHeight(height)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    @staticmethod
    def _css(styles):
        keys = (
            'background', 'background-color', 'color',
            'border', 'border-width', 'border-style', 'border-color',
            'border-top', 'border-right', 'border-bottom', 'border-left',
            'border-top-width', 'border-right-width', 'border-bottom-width', 'border-left-width',
            'border-top-style', 'border-right-style', 'border-bottom-style', 'border-left-style',
            'border-top-color', 'border-right-color', 'border-bottom-color', 'border-left-color',
            'border-radius', 'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
            'margin', 'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
            'font-size', 'font-family', 'font-weight', 'font-style', 'text-align', 'opacity'
        )
        return '; '.join('%s:%s' % (key, value) for key, value in styles.items() if key in keys and value)

    def render(self, fragment, styles=None):
        parser = _PreviewParser(); parser.feed(fragment or ''); parser.close()
        root = QFrame(); root.setObjectName('configurePreviewRoot')
        root.setStyleSheet('QFrame#configurePreviewRoot { background:#101216; color:#f4f6f8; padding:10px; }')
        layout = QVBoxLayout(root); layout.setContentsMargins(10, 10, 10, 10); layout.setSpacing(7)
        for node in parser.root.children: layout.addWidget(self._widget(node, styles or {}))
        if not parser.root.children: layout.addWidget(QLabel('—'))
        layout.addStretch(1)
        # Preserve the complete height of wrapped labels and controls.  The
        # scroll area can then scroll instead of clipping text vertically.
        layout.activate(); root.setMinimumHeight(layout.sizeHint().height())
        self.setWidget(root)

    def _apply(self, widget, styles, root=False):
        if root and styles:
            css = self._css(styles)
            # Qt clips a stylesheet background to border-radius reliably only
            # when it has a border primitive.  A transparent zero-width border
            # has no visual effect, but makes rounded containers (section,
            # div, form, etc.) paint exactly as CSS containers do.
            if 'border-radius' in styles and 'border' not in styles:
                css += '; border: 0px solid transparent'
            widget.setStyleSheet(css)
        width, height = styles.get('width', ''), styles.get('height', '')
        width_match = re.fullmatch(r'\s*(\d+)px\s*', width or '')
        height_match = re.fullmatch(r'\s*(\d+)px\s*', height or '')
        if width_match: widget.setFixedWidth(int(width_match.group(1)))
        # Computed CSS height is the content box. A fixed Qt outer height
        # clips glyphs after margins and padding have been applied.
        if height_match: widget.setMinimumHeight(int(height_match.group(1)))

    def _widget(self, node, root_styles):
        styles = dict(root_styles)
        for key, value in re.findall(r'([\w-]+)\s*:\s*([^;]+)', node.attrs.get('style', '')):
            styles[key.strip()] = value.strip()
        tag = node.tag; text = unescape(node.text).strip()
        if tag in self.BLOCKS:
            # A QWidget normally does not paint a stylesheet background of its
            # own.  Explicitly enabling it is important here: otherwise a
            # background is painted as a rectangle and border-radius is lost.
            frame = QFrame(); frame.setFrameShape(QFrame.NoFrame)
            frame.setAttribute(Qt.WA_StyledBackground, True)
            self._apply(frame, styles, True)
            direction = QHBoxLayout if styles.get('display') == 'flex' and styles.get('flex-direction') == 'row' else QVBoxLayout
            box = direction(frame); box.setContentsMargins(10, 8, 10, 8); box.setSpacing(6)
            if text: box.addWidget(self._text(text, tag, styles))
            for child in node.children: box.addWidget(self._widget(child, {}))
            return frame
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'label', 'span', 'a', 'strong', 'small', 'li'}:
            label = self._text(text or ' ', tag, styles); self._apply(label, styles, True)
            return label
        if tag == 'button':
            button = QPushButton(text or node.attrs.get('value', 'Button')); self._apply(button, styles, True); return button
        if tag == 'input':
            field = QLineEdit(); field.setText(node.attrs.get('value', '')); field.setPlaceholderText(node.attrs.get('placeholder', ''))
            field.setReadOnly(True); self._apply(field, styles, True); return field
        if tag == 'textarea':
            field = QPlainTextEdit(); field.setPlainText(text); field.setPlaceholderText(node.attrs.get('placeholder', '')); field.setReadOnly(True); field.setFixedHeight(54); self._apply(field, styles, True); return field
        if tag == 'select':
            combo = QComboBox()
            for option in node.children:
                if option.tag == 'option': combo.addItem(unescape(option.text).strip() or option.attrs.get('value', 'Option'))
            if combo.count() == 0: combo.addItem('Option')
            self._apply(combo, styles, True); return combo
        if tag == 'img':
            image = QLabel(node.attrs.get('alt', 'Image')); image.setAlignment(Qt.AlignCenter); image.setMinimumHeight(40); self._apply(image, styles, True); return image
        if tag in {'hr', 'br'}:
            line = QFrame(); line.setFrameShape(QFrame.HLine); return line
        frame = QFrame(); box = QVBoxLayout(frame); box.setContentsMargins(0, 0, 0, 0)
        if text: box.addWidget(self._text(text, tag, styles))
        for child in node.children: box.addWidget(self._widget(child, {}))
        return frame

    @staticmethod
    def _text(value, tag, styles):
        label = QLabel(value); label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        size = {'h1': 22, 'h2': 19, 'h3': 17, 'h4': 15, 'small': 11}.get(tag, 13)
        weight = '700' if tag.startswith('h') or tag == 'strong' else '400'
        label.setStyleSheet('font-size:%dpx; font-weight:%s; color:%s;' % (size, weight, styles.get('color', '#f4f6f8')))
        return label


def preview_shell(fragment, style_sheets='', selector='', declarations='', theme=''):
    """Render Configure previews in Chromium, not Qt's limited rich-text view."""
    selector_css = f'\n{selector} {{{declarations}}}' if selector and declarations else ''
    theme_attr = f' data-scs-theme="{theme}"' if theme else ''
    return '''<!doctype html><html%s><head><meta charset="utf-8"><style>
    html, body { margin:0; min-height:100%%; background:#101216; color:#f4f6f8; font:16px system-ui,sans-serif; }
    body { padding:12px; overflow:auto; }
    %s%s
    </style></head><body><main data-scs-config-preview>%s</main></body></html>''' % (theme_attr, style_sheets, selector_css, fragment)


def make_dialog_preview(workspace, height):
    """Create a reliable HTML surface for a Configure dialog.

    An embedded second Chromium surface stays blank on some Windows drivers
    even though its DOM is loaded. QTextBrowser renders the same isolated HTML
    shell in-process and remains visible in every dialog configuration.
    """
    return ConfigurePreview(height)


def load_dialog_preview(view, workspace, markup, styles=None):
    match = re.search(r'<main\s+data-scs-config-preview[^>]*>(.*)</main>\s*</body>', markup, re.I | re.S)
    view.render(match.group(1) if match else markup, styles or {})


def themed_selector(target, theme):
    """Scope a selector to a theme without making a second ``html`` child."""
    if re.match(r'^html(?=\b|\[|:)', target):
        return re.sub(r'^html', f'html[data-scs-theme="{theme}"]', target, count=1)
    if target == ':root':
        return f'html[data-scs-theme="{theme}"]'
    return f'html[data-scs-theme="{theme}"] {target}'


COMMON_CSS = [
    ('width','Ширина','Width'), ('height','Высота','Height'),
    ('color','Цвет текста','Text color'), ('background-color','Цвет фона','Background color'),
    ('font-size','Размер текста','Font size'), ('font-weight','Жирность','Font weight'),
    ('font-family','Семейство шрифта','Font family'), ('text-align','Выравнивание текста','Text alignment'), ('padding','Внутренние отступы','Padding'),
    ('margin','Внешние отступы','Margin'), ('border-radius','Скругление углов','Corner radius'),
    ('border','Граница','Border'), ('display','Расположение элементов','Layout'), ('flex-direction','Направление flex','Flex direction'),
    ('justify-content','Выравнивание по основной оси','Justify content'), ('align-items','Выравнивание по поперечной оси','Align items'),
    ('gap','Расстояние между элементами','Gap'), ('max-width','Максимальная ширина','Maximum width'),
    ('box-shadow','Тень','Box shadow'), ('opacity','Прозрачность','Opacity'), ('transition','Переход','Transition')
]

# The class editor exposes every quick CSS property available in the authoring workspace.
CLASS_CSS_PROPERTIES = tuple(prop for prop, _, _ in COMMON_CSS)
EXTRA_GENERAL_CSS_PROPERTIES = set()

# Full editable catalogue for the "Advanced: CSS" tables. Unknown properties from
# an existing rule are appended as well, so no authored CSS is hidden or discarded.
CSS_PROPERTY_CATALOG = tuple('''
accent-color align-content align-items align-self alignment-baseline all animation animation-delay animation-direction animation-duration animation-fill-mode animation-iteration-count animation-name animation-play-state animation-timing-function appearance aspect-ratio backface-visibility background background-attachment background-blend-mode background-clip background-color background-image background-origin background-position background-repeat background-size baseline-shift block-size border border-block border-block-color border-block-end border-block-start border-block-style border-block-width border-bottom border-bottom-color border-bottom-left-radius border-bottom-right-radius border-bottom-style border-bottom-width border-collapse border-color border-end-end-radius border-end-start-radius border-image border-image-outset border-image-repeat border-image-slice border-image-source border-image-width border-inline border-inline-color border-inline-end border-inline-start border-inline-style border-inline-width border-left border-left-color border-left-style border-left-width border-radius border-right border-right-color border-right-style border-right-width border-spacing border-start-end-radius border-start-start-radius border-style border-top border-top-color border-top-left-radius border-top-right-radius border-top-style border-top-width border-width bottom box-shadow box-sizing break-after break-before break-inside caption-side caret-color clear clip clip-path clip-rule color color-interpolation color-rendering color-scheme column-count column-fill column-gap column-rule column-rule-color column-rule-style column-rule-width column-span column-width columns contain content counter-increment counter-reset cursor direction display empty-cells fill fill-opacity fill-rule filter flex flex-basis flex-direction flex-flow flex-grow flex-shrink flex-wrap float flood-color flood-opacity font font-family font-feature-settings font-kerning font-language-override font-optical-sizing font-palette font-size font-size-adjust font-stretch font-style font-synthesis font-variant font-variant-caps font-variant-east-asian font-variant-ligatures font-variant-numeric font-variation-settings font-weight gap grid grid-area grid-auto-columns grid-auto-flow grid-auto-rows grid-column grid-column-end grid-column-start grid-row grid-row-end grid-row-start grid-template grid-template-areas grid-template-columns grid-template-rows height hyphens image-rendering inline-size inset inset-block inset-inline isolation justify-content justify-items justify-self left letter-spacing line-break line-height list-style list-style-image list-style-position list-style-type margin margin-block margin-bottom margin-inline margin-left margin-right margin-top marker mask mask-image mask-position mask-repeat mask-size max-block-size max-height max-inline-size max-width min-block-size min-height min-inline-size min-width mix-blend-mode object-fit object-position offset offset-anchor offset-distance offset-path offset-position offset-rotate opacity order orphans outline outline-color outline-offset outline-style outline-width overflow overflow-wrap overflow-x overflow-y overscroll-behavior padding padding-block padding-bottom padding-inline padding-left padding-right padding-top page-break-after page-break-before page-break-inside paint-order perspective perspective-origin place-content place-items place-self pointer-events position quotes resize right rotate row-gap ruby-align ruby-position scale scroll-behavior scroll-margin scroll-padding shape-image-threshold shape-margin shape-outside stop-color stop-opacity stroke stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width tab-size table-layout text-align text-align-last text-anchor text-combine-upright text-decoration text-decoration-color text-decoration-line text-decoration-style text-decoration-thickness text-emphasis text-indent text-justify text-orientation text-overflow text-rendering text-shadow text-transform text-underline-offset text-underline-position top touch-action transform transform-box transform-origin transform-style transition transition-delay transition-duration transition-property transition-timing-function translate unicode-bidi user-select vertical-align visibility white-space widows width will-change word-break word-spacing word-wrap writing-mode z-index zoom
'''.split())


AUTHORING_DIALOG_STYLE = '''
    QDialog { background:#191d25; color:#f4f6f8; }
    QDialog QWidget, QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox { color:#d8e0ea; }
    QLabel { color:#d8e0ea; }
    QLabel#settingsField { color:#d8e0ea; font-weight:600; }
    QLabel#configureHeader { background:#11141a; border:1px solid #384150; border-radius:10px; color:#f4f6f8; font-size:10pt; padding:7px 10px; }
    QLabel#configureHeader span { color:#aab4c2; font-size:8pt; }
    QTextBrowser#elementPreview, QWebEngineView#elementPreview { background:#11141a; color:#f4f6f8; border:1px solid #384150; border-radius:12px; }
    QLineEdit, QComboBox, QSpinBox, QTableWidget { background:#11141a; color:#f4f6f8; border:1px solid #384150; border-radius:8px; padding:6px; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTableWidget:focus { border-color:#ee6b2f; }
    QComboBox QAbstractItemView { background:#1d2129; color:#f4f6f8; selection-background-color:#ee6b2f; }
    QTabWidget::pane { background:#191d25; border:1px solid #303744; border-radius:10px; }
    QTabBar::tab { background:#151920; color:#aab4c2; padding:7px 11px; margin-right:2px; }
    QTabBar::tab:selected { background:#262d38; color:#ffffff; }
    QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#191d25; border:0; }
    QScrollBar:vertical { background:#151920; border:0; width:11px; margin:2px; }
    QScrollBar::handle:vertical { background:#46505f; border-radius:4px; min-height:28px; }
    QScrollBar::handle:vertical:hover { background:#ee6b2f; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; background:transparent; border:0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; border:0; }
    QTableCornerButton::section { background:#1d2129; border:0; border-right:1px solid #303744; border-bottom:1px solid #303744; }
    QTableWidget#elementAttributesTable QHeaderView::section:vertical { background:#1d2129; color:#aab4c2; border:0; border-right:1px solid #303744; border-bottom:1px solid #303744; padding:0; min-width:28px; }
    QTableWidget::item:selected { background:#ee6b2f; color:#ffffff; }
    QPushButton { background:#ee6b2f; color:#ffffff; border:0; border-radius:10px; padding:9px 14px; font:600 10pt "Segoe UI"; }
    QPushButton:hover { background:#ff834b; }
'''

AUTHORING_WORKSPACE_STYLE = '''
    QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport { background:#191d25; border:1px solid #303744; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget { background:#11141a; color:#f4f6f8; border:1px solid #384150; border-radius:8px; padding:6px; }
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected { background:#ee6b2f; color:white; }
    QHeaderView::section { background:#1d2129; color:#aab4c2; border:0; padding:7px; }
    QTableCornerButton::section { background:#1d2129; border:1px solid #303744; }
    QComboBox QAbstractItemView { background:#1d2129; color:#f4f6f8; selection-background-color:#ee6b2f; }
'''

AUTHORING_LIGHT_STYLE = '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#f8fafc; color:#202733; border-color:#cbd5e1; }
    QLabel { color:#202733; }
    QLabel#settingsField { color:#202733; }
    QDialog QWidget, QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox { color:#202733; }
    QLabel#configureHeader, QTextBrowser#elementPreview, QWebEngineView#elementPreview { background:#ffffff; color:#202733; border-color:#cbd5e1; } QLabel#configureHeader span { color:#536172; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#ffffff; color:#202733; border-color:#cbd5e1; }
    QComboBox QAbstractItemView { background:#ffffff; color:#202733; selection-background-color:#ee6b2f; }
    QTabBar::tab { background:#e5ebf2; color:#536172; } QTabBar::tab:selected { background:#ee6b2f; color:white; }
    QHeaderView::section, QTableCornerButton::section, QTableWidget#elementAttributesTable QHeaderView::section:vertical { background:#eef2f6; color:#536172; border-color:#cbd5e1; }
    QScrollBar:vertical, QScrollBar:horizontal { background:#e4eaf1; border-color:#cbd5e1; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#d56434; border-color:#e4eaf1; }
'''

AUTHORING_EMBER_STYLE = '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#211816; color:#ffe4d9; border-color:#4d342d; }
    QLabel { color:#f2d5ca; }
    QLabel#settingsField { color:#f2d5ca; }
    QDialog QWidget, QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox { color:#f2d5ca; }
    QLabel#configureHeader, QTextBrowser#elementPreview, QWebEngineView#elementPreview { background:#17110f; color:#ffe4d9; border-color:#5a3a32; } QLabel#configureHeader span { color:#d4b5aa; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#17110f; color:#ffe4d9; border-color:#5a3a32; }
    QComboBox QAbstractItemView { background:#281c19; color:#ffe4d9; selection-background-color:#d95e32; }
    QTabBar::tab { background:#38241f; color:#d4b5aa; } QTabBar::tab:selected { background:#d95e32; color:white; }
    QHeaderView::section, QTableCornerButton::section, QTableWidget#elementAttributesTable QHeaderView::section:vertical { background:#281c19; color:#d7aa96; border-color:#4d342d; }
'''

AUTHORING_OCEAN_STYLE = '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#102233; color:#e4f8ff; border-color:#24465e; }
    QLabel { color:#d9f5ff; }
    QLabel#settingsField { color:#d9f5ff; }
    QDialog QWidget, QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox { color:#d9f5ff; }
    QLabel#configureHeader, QTextBrowser#elementPreview, QWebEngineView#elementPreview { background:#0c1724; color:#e4f8ff; border-color:#315770; } QLabel#configureHeader span { color:#afcddd; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#0c1724; color:#e4f8ff; border-color:#315770; }
    QComboBox QAbstractItemView { background:#142b3d; color:#e4f8ff; selection-background-color:#1f9dc6; }
    QTabBar::tab { background:#183247; color:#afcddd; } QTabBar::tab:selected { background:#1f9dc6; color:white; }
    QHeaderView::section, QTableCornerButton::section, QTableWidget#elementAttributesTable QHeaderView::section:vertical { background:#142b3d; color:#a9cbdc; border-color:#24465e; }
    QScrollBar:vertical, QScrollBar:horizontal { background:#102233; border-color:#24465e; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#238eb3; border-color:#102233; }
'''

AUTHORING_LAVENDER_STYLE = '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#ffffff; color:#342c4a; border-color:#ddd3f0; }
    QLabel { color:#342c4a; }
    QLabel#settingsField { color:#342c4a; }
    QDialog QWidget, QDialog QLabel, QDialog QCheckBox, QDialog QRadioButton, QDialog QGroupBox { color:#342c4a; }
    QLabel#configureHeader, QTextBrowser#elementPreview, QWebEngineView#elementPreview { background:#ffffff; color:#342c4a; border-color:#d9d0e9; } QLabel#configureHeader span { color:#665e7d; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#ffffff; color:#342c4a; border-color:#d9d0e9; }
    QComboBox QAbstractItemView { background:#ffffff; color:#342c4a; selection-background-color:#7d5bd6; }
    QTabBar::tab { background:#ece6f6; color:#665e7d; } QTabBar::tab:selected { background:#7d5bd6; color:white; }
    QHeaderView::section, QTableCornerButton::section, QTableWidget#elementAttributesTable QHeaderView::section:vertical { background:#f2eef9; color:#665e7d; border-color:#ddd3f0; }
    QScrollBar:vertical, QScrollBar:horizontal { background:#eee9f6; border-color:#d9d0e9; } QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background:#8b6bdd; border-color:#eee9f6; }
'''

AUTHORING_FIRE_STYLE = AUTHORING_EMBER_STYLE + '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#281513; border-color:#623029; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#1b0f0d; border-color:#71342b; } QComboBox QAbstractItemView { background:#351a16; selection-background-color:#d84b2d; }
    QTabBar::tab { background:#432019; } QTabBar::tab:selected { background:#d84b2d; } QHeaderView::section, QTableCornerButton::section { background:#351a16; border-color:#623029; }
'''

AUTHORING_SPECTRUM_DARK_STYLE = AUTHORING_OCEAN_STYLE + '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#181b38; border-color:#3d4b85; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#101126; border-color:#4c4ca1; } QComboBox QAbstractItemView { background:#222450; selection-background-color:#d548b2; }
    QTabBar::tab { background:#273063; } QTabBar::tab:selected { background:#d548b2; } QHeaderView::section, QTableCornerButton::section { background:#222450; border-color:#4c4ca1; }
'''

AUTHORING_SPECTRUM_LIGHT_STYLE = AUTHORING_LAVENDER_STYLE + '''
    QDialog, QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#f3fbff; border-color:#b7dced; }
    QLineEdit, QComboBox, QSpinBox, QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit { background:#ffffff; border-color:#c3c4e9; } QComboBox QAbstractItemView { background:#ffffff; selection-background-color:#c441a7; }
    QTabBar::tab { background:#e7e3f8; } QTabBar::tab:selected { background:#c441a7; } QHeaderView::section, QTableCornerButton::section { background:#eef0ff; border-color:#c3c4e9; }
'''


def authoring_theme_style(theme, dialog=False):
    """Return a local editor stylesheet without importing the main window module."""
    base = AUTHORING_DIALOG_STYLE if dialog else AUTHORING_WORKSPACE_STYLE
    overrides = {'light': AUTHORING_LIGHT_STYLE, 'ember': AUTHORING_EMBER_STYLE, 'ocean': AUTHORING_OCEAN_STYLE, 'lavender': AUTHORING_LAVENDER_STYLE, 'fire': AUTHORING_FIRE_STYLE, 'spectrum_dark': AUTHORING_SPECTRUM_DARK_STYLE, 'spectrum_light': AUTHORING_SPECTRUM_LIGHT_STYLE}
    return base + overrides.get(theme, '')

VOID_HTML_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


def _format_css(content, level):
    lines = []
    for selector, declarations in re.findall(r'(?s)([^{}]+)\{([^{}]*)\}', content):
        selector = selector.strip()
        if not selector: continue
        values = [declaration.strip() for declaration in declarations.split(';') if declaration.strip()]
        lines.append('  ' * level + selector + ' {' + '; '.join(values) + (';' if values else '') + '}')
    return lines or ['  ' * level + line.strip() for line in content.splitlines() if line.strip()]


def _format_script(content, level):
    source_lines = [line.rstrip() for line in content.splitlines()]
    nonempty = [line for line in source_lines if line.strip()]
    if not nonempty: return []
    margin = min(len(line) - len(line.lstrip()) for line in nonempty)
    return ['  ' * level + line[margin:] for line in source_lines if line.strip()]


def format_html(source):
    """Format DOM-serialized HTML without altering the contents of style/script blocks."""
    # The orange outline is preview-only UI state. Chromium serializes it together
    # with the document when an element is edited, so remove only our exact marker
    # before the source is shown or saved.
    source = re.sub(r'\sdata-scs-editor-selected(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?', '', source, flags=re.I)

    def clean_preview_outline(match):
        value = match.group(1)
        value = re.sub(r'(?i)(?:^|;)\s*outline\s*:\s*(?:2px\s+dashed\s+(?:#ee6b2f|rgb\(238\s*,\s*107\s*,\s*47\))|rgb\(238\s*,\s*107\s*,\s*47\)\s+dashed\s+2px)\s*;?', ';', value)
        value = re.sub(r'(?i)(?:^|;)\s*outline-offset\s*:\s*3px\s*;?', ';', value)
        value = re.sub(r';\s*;', ';', value).strip(' ;')
        return ' style="' + value + '"' if value else ''

    source = re.sub(r'\sstyle\s*=\s*"([^"]*)"', clean_preview_outline, source, flags=re.I)
    tokens = re.findall(r'(?is)<!--.*?-->|<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>|<![^>]*>|<[^>]+>|[^<]+', source)
    output, level, index = [], 0, 0
    while index < len(tokens):
        token = tokens[index]; stripped = token.strip()
        if not stripped: index += 1; continue
        style = re.match(r'(?is)^(<style\b[^>]*>)(.*)(</style\s*>)$', stripped)
        if style:
            output.append('  ' * level + style.group(1)); output.extend(_format_css(style.group(2), level + 1)); output.append('  ' * level + style.group(3)); index += 1; continue
        script = re.match(r'(?is)^(<script\b[^>]*>)(.*)(</script\s*>)$', stripped)
        if script:
            if script.group(2).strip():
                output.append('  ' * level + script.group(1)); output.extend(_format_script(script.group(2), level + 1)); output.append('  ' * level + script.group(3))
            else: output.append('  ' * level + stripped)
            index += 1; continue
        if stripped.startswith('<!--') or stripped.startswith('<!'):
            output.append('  ' * level + stripped); index += 1; continue
        closing = re.match(r'^</\s*([\w:-]+)', stripped)
        opening = re.match(r'^<\s*([\w:-]+)\b', stripped)
        if closing:
            level = max(0, level - 1); output.append('  ' * level + stripped); index += 1; continue
        if opening:
            name = opening.group(1).lower()
            # Keep ordinary text elements compact: <h1>Text</h1>.
            if name not in VOID_HTML_TAGS and index + 2 < len(tokens) and tokens[index + 1].strip() and not tokens[index + 1].lstrip().startswith('<') and re.match(r'^</\s*' + re.escape(name) + r'\s*>', tokens[index + 2].strip(), re.I):
                output.append('  ' * level + stripped + tokens[index + 1].strip() + tokens[index + 2].strip()); index += 3; continue
            output.append('  ' * level + stripped)
            if not stripped.endswith('/>') and name not in VOID_HTML_TAGS: level += 1
            index += 1; continue
        output.append('  ' * level + stripped); index += 1
    return '\n'.join(output) + '\n'


def synchronize_css_fields(fields, table):
    """Keep quick controls and the matching Advanced: CSS rows mutually in sync."""
    rows = {table.item(row, 0).text(): row for row in range(table.rowCount()) if table.item(row, 0)}
    def set_field(field, value):
        field.blockSignals(True)
        if isinstance(field, QComboBox):
            index = field.findData(value)
            if index < 0 and value: field.addItem(value, value); index = field.count() - 1
            field.setCurrentIndex(index if index >= 0 else 0)
        else: field.setText(value)
        field.blockSignals(False)
    def from_field(prop):
        row = rows.get(prop)
        if row is None: return
        value = fields[prop].text()
        table.blockSignals(True); table.item(row, 1).setText(value); table.blockSignals(False)
    def from_table(item):
        if item.column() != 1: return
        name = table.item(item.row(), 0)
        if name and name.text() in fields: set_field(fields[name.text()], item.text())
    for prop, field in fields.items():
        signal = field.currentIndexChanged if isinstance(field, QComboBox) else field.textChanged
        signal.connect(lambda _=None, name=prop: from_field(name))
    table.itemChanged.connect(from_table)


class SelectorPositionParser(HTMLParser):
    """Find the opening-tag position that corresponds to the preview selector."""
    def __init__(self, target):
        super().__init__(convert_charrefs=False)
        self.target = target.lower()
        self.stack = [{'path': '', 'counts': {}}]
        self.position = None
        self.length = 0

    def handle_starttag(self, tag, attrs):
        parent = self.stack[-1]
        index = parent['counts'].get(tag, 0) + 1
        parent['counts'][tag] = index
        path = (parent['path'] + ' > ' if parent['path'] else '') + f'{tag}:nth-of-type({index})'
        if path == self.target and self.position is None:
            self.position = self.getpos(); self.length = len(self.get_starttag_text() or '')
        if tag not in VOID_HTML_TAGS:
            self.stack.append({'tag': tag, 'path': path, 'counts': {}})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1].get('tag') == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].get('tag') == tag:
                del self.stack[index:]
                break


class ElementSettings(QDialog):
    def __init__(self, workspace, data):
        super().__init__(workspace.panel); self.workspace = workspace; self.data = data
        self.setStyleSheet(workspace.dialog_style())
        tr = workspace.panel.text
        self.group_mode = len(data.get('selectors') or [data.get('selector')]) > 1
        self.setWindowTitle((tr('Настроить выбранные элементы', 'Configure selected elements') + ' (%d)' % len(data.get('selectors', []))) if self.group_mode else tr('Настроить элемент', 'Configure element') + ' — ' + data['tag']); self.resize(760, 720)
        layout = QVBoxLayout(self); layout.setContentsMargins(18, 18, 18, 18); layout.setSpacing(12)
        header = QLabel(); header.setObjectName('configureHeader'); header.setFixedHeight(54); header.setText('<b>%s</b><br><span>%s</span>' % (data['tag'], data.get('selector', ''))); header.setTextFormat(Qt.RichText); layout.addWidget(header)
        self.preview = make_dialog_preview(workspace, 132)
        outer_html = data.get('outer_html', '') or ('<%s>%s</%s>' % (data['tag'], data.get('text', ''), data['tag']))
        self._preview_inner_html = self.inner_html(outer_html)
        self._preview_styles = data.get('style_sheets', '')
        # ``styles`` contains only declarations authored for the element;
        # ``preview_base_styles`` contains the browser-computed appearance.
        # Keeping them separate lets General remain an editor for authored
        # properties while the preview faithfully shows inherited CSS too.
        self._preview_base_styles = dict(data.get('preview_base_styles', {}))
        layout.addWidget(self.preview)
        self.classes = QLineEdit(data['attributes'].get('class', ''))
        layout.addWidget(QLabel(tr('Классы (через пробел)', 'Classes (space-separated)'))); layout.addWidget(self.classes)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        common = QWidget(); common.setObjectName('settingsContent'); form = QFormLayout(common); form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow); form.setRowWrapPolicy(QFormLayout.DontWrapRows); form.setHorizontalSpacing(14); form.setVerticalSpacing(8); form.setContentsMargins(12, 12, 18, 12); self.general_form = form; self.common = {}; self.common_labels = {}; self.common_displays = {}; self.general_extra = {}
        self.caption = QLineEdit(data.get('text', '')); form.addRow(tr('Текст элемента', 'Element text'), self.caption)
        choices = {
            'font-weight': [('Обычный','Normal','400'),('Полужирный','Semibold','600'),('Жирный','Bold','700')],
            'text-align': [('Слева','Left','left'),('По центру','Center','center'),('Справа','Right','right'),('По ширине','Justify','justify')],
            'display': [('Блок','Block','block'),('В строку','Inline','inline'),('Гибкий ряд / колонка','Flex layout','flex'),('Сетка','Grid','grid'),('Скрыть','Hidden','none')],
        }
        for prop, ru, en in COMMON_CSS:
            value = data['styles'].get(prop, '')
            if prop in choices:
                edit = PropertyChoice()
                for option_ru, option_en, css in choices[prop]: edit.addItem(tr(option_ru, option_en), css)
                index = edit.findData(value)
                if index < 0: edit.addItem(value, value); index = edit.count() - 1
                edit.setCurrentIndex(index)
            else:
                edit = QLineEdit(value)
            edit.setToolTip(prop); self.common[prop] = edit
            if prop in ('color', 'background-color'):
                wrapper = QWidget(); wrapper.setAttribute(Qt.WA_StyledBackground, True); row = QHBoxLayout(wrapper); row.setContentsMargins(0, 0, 0, 0); row.addWidget(edit)
                button = QPushButton(tr('Выбрать…', 'Choose…')); row.addWidget(button); button.clicked.connect(lambda _, field=edit: self.choose_color(field))
                form.addRow(tr(ru, en), wrapper); self.common_labels[prop] = form.labelForField(wrapper); self.common_displays[prop] = wrapper
            else:
                form.addRow(tr(ru, en), edit); self.common_labels[prop] = form.labelForField(edit); self.common_displays[prop] = edit
        self.field_name=QLineEdit(data['attributes'].get('name','')); form.addRow(tr('Имя поля для скрипта','Field name for script'),self.field_name)
        self.action_name=QLineEdit(data['attributes'].get('data-scs-action','')); form.addRow(tr('Действие кнопки','Button action'),self.action_name)
        common_scroll=QScrollArea(); common_scroll.setObjectName('configureGeneralScroll'); common_scroll.setFrameShape(QFrame.NoFrame); common_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); common_scroll.setWidgetResizable(True); common_scroll.setWidget(common); self.tabs.addTab(common_scroll,tr('Основное','General'))
        general_properties = getattr(workspace, 'general_css_properties', lambda: CLASS_CSS_PROPERTIES)()
        for prop in general_properties:
            if prop not in self.common:
                self.add_general_field(prop, data['styles'].get(prop, ''))
        # Keep properties that already exist in Advanced: CSS available in
        # General as editable fields as well.
        for prop, value in data['styles'].items():
            if prop not in self.common:
                self.add_general_field(prop, value)
        self.caption.setEnabled(not data.get('has_children',False) and not self.group_mode)
        self.caption.setToolTip(tr('Текст контейнера редактируйте через его дочерние элементы.','Edit container text through its child elements.'))
        self.tables = {}
        for key, title in [('attributes', tr('Дополнительно: атрибуты', 'Advanced: attributes')), ('styles', tr('Дополнительно: CSS','Advanced: CSS'))]:
            values = data[key]
            names = list(values) if key == 'attributes' else list(dict.fromkeys((*CSS_PROPERTY_CATALOG, *values)))
            table = QTableWidget(len(names), 2); table.setHorizontalHeaderLabels([tr('Параметр', 'Property'), tr('Значение', 'Value')]); table.horizontalHeader().setStretchLastSection(True)
            for row, name in enumerate(names):
                value = values.get(name, '')
                table.setItem(row, 0, QTableWidgetItem(name)); table.setItem(row, 1, QTableWidgetItem(value))
            if key == 'attributes':
                table.setObjectName('elementAttributesTable')
                vertical_header = table.verticalHeader()
                vertical_header.setMinimumWidth(28)
                vertical_header.setStyleSheet(workspace.vertical_header_style())
                attribute_page = QWidget(); attribute_layout = QVBoxLayout(attribute_page); attribute_layout.setContentsMargins(0, 0, 0, 0); attribute_layout.setSpacing(4); attribute_layout.addWidget(table)
                add_attribute = QPushButton('+'); add_attribute.setObjectName('outlineButton'); add_attribute.setFixedWidth(34)
                def add_custom_attribute(_=False, target=table):
                    row = target.rowCount(); target.insertRow(row); target.setItem(row, 0, QTableWidgetItem('')); target.setItem(row, 1, QTableWidgetItem(''))
                    target.scrollToBottom(); target.setCurrentCell(row, 0); target.editItem(target.item(row, 0))
                add_attribute.clicked.connect(add_custom_attribute); attribute_layout.addWidget(add_attribute, 0, Qt.AlignLeft)
                self.attribute_add_button = add_attribute; tab = attribute_page
            else: tab = table
            self.tables[key] = table; self.tabs.addTab(tab, title)
        synchronize_css_fields(self.common, self.tables['styles'])
        for prop, field in self.general_extra.items():
            synchronize_css_fields({prop: field}, self.tables['styles'])
        # General contains properties that are already authored.  + Property
        # adds one of the missing quick properties, rather than duplicating a
        # field which is already there.
        self._general_properties = {prop for prop, _, _ in COMMON_CSS}
        for prop, _, _ in COMMON_CSS: self.set_common_visible(prop, True)
        property_buttons = QHBoxLayout(); self.add_property_button = QPushButton(tr('+ Параметр', '+ Property')); self.add_property_button.clicked.connect(self.add_row); property_buttons.addWidget(self.add_property_button)
        self.all_properties_button = QPushButton(tr('Все параметры', 'All parameters')); self.all_properties_button.setObjectName('outlineButton'); self.all_properties_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.tables['styles'])); property_buttons.addWidget(self.all_properties_button); property_buttons.addStretch(); layout.addLayout(property_buttons)
        self.scope = QComboBox(); self.scope.addItems([tr('Этот объект', 'This element'), tr('Все объекты этого типа', 'All elements of this type'), tr('Все объекты класса', 'All elements of class')]); layout.addWidget(self.scope)
        self.class_target = QLineEdit(); self.class_target.setPlaceholderText(tr('Имя класса для общего CSS', 'Class name for shared CSS')); layout.addWidget(self.class_target)
        self.contrast_warning = QLabel(); self.contrast_warning.setObjectName('contrastWarning'); self.contrast_warning.setWordWrap(True); layout.addWidget(self.contrast_warning)
        self.error = QLabel(); layout.addWidget(self.error)
        self.apply_button = QPushButton(tr('Применить', 'Apply')); self.apply_button.clicked.connect(self.apply); layout.addWidget(self.apply_button)
        self._original_outer_html = data.get('outer_html', '')
        self._live_timer = QTimer(self); self._live_timer.setSingleShot(True); self._live_timer.timeout.connect(self.update_live_preview)
        watched = [self.classes, self.caption, self.field_name, self.action_name, self.class_target, *self.common.values(), *self.general_extra.values()]
        for field in watched:
            if isinstance(field, QLineEdit): field.textChanged.connect(self.schedule_live_preview)
            elif isinstance(field, QComboBox): field.currentIndexChanged.connect(self.schedule_live_preview)
        for table in self.tables.values(): table.itemChanged.connect(lambda _item: self.schedule_live_preview())
        self.refresh_dialog_preview()

    def schedule_live_preview(self, *_):
        self.update_contrast_warning(); self._live_timer.start(80)

    @staticmethod
    def _rgb(value):
        value = (value or '').strip()
        match = re.fullmatch(r'#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})', value)
        if match:
            digits = match.group(1); digits = ''.join(char * 2 for char in digits) if len(digits) == 3 else digits
            return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))
        match = re.fullmatch(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', value)
        return tuple(min(255, int(match.group(index))) for index in (1, 2, 3)) if match else None

    def update_contrast_warning(self):
        color = self._rgb(self.common['color'].text()); background = self._rgb(self.common['background-color'].text())
        if not color or not background: self.contrast_warning.clear(); return
        def luminance(rgb):
            values = [value / 255.0 for value in rgb]; values = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
            return .2126 * values[0] + .7152 * values[1] + .0722 * values[2]
        first, second = luminance(color), luminance(background); ratio = (max(first, second) + .05) / (min(first, second) + .05)
        self.contrast_warning.setText(self.workspace.panel.text('⚠ Низкая читаемость текста: контраст %.2f:1 (рекомендуется не менее 4.5:1).' % ratio, '⚠ Low text readability: %.2f:1 contrast (at least 4.5:1 is recommended).' % ratio) if ratio < 4.5 else '')

    def inner_html(self, outer_html):
        match = re.match(r'^\s*<([A-Za-z][\w:-]*)\b[^>]*>(.*)</\1\s*>\s*$', outer_html, re.I | re.S)
        return match.group(2) if match else self.data.get('text', '')

    def live_payload(self):
        attrs = dict(self.data.get('attributes', {})); attrs['class'] = self.classes.text().strip()
        attrs['name'] = self.field_name.text().strip(); attrs['data-scs-action'] = self.action_name.text().strip()
        for row in range(self.tables['attributes'].rowCount()):
            key_item, value_item = self.tables['attributes'].item(row, 0), self.tables['attributes'].item(row, 1)
            if key_item and key_item.text().strip() not in ('class', 'name', 'data-scs-action'): attrs[key_item.text().strip()] = value_item.text() if value_item else ''
        styles = dict(self.data.get('styles', {}))
        for row in range(self.tables['styles'].rowCount()):
            key_item, value_item = self.tables['styles'].item(row, 0), self.tables['styles'].item(row, 1)
            if key_item and key_item.text().strip():
                key, value = key_item.text().strip(), value_item.text() if value_item else ''
                if value != self.data.get('styles', {}).get(key, ''): styles[key] = value
        for key, field in self.common.items(): styles[key] = field.currentData() if isinstance(field, QComboBox) else field.text()
        for key, field in self.general_extra.items(): styles[key] = field.text()
        return attrs, styles

    def refresh_dialog_preview(self):
        _, styles = self.live_payload()
        preview_styles = dict(self._preview_base_styles)
        # Empty editor fields mean "do not override" for the private preview;
        # do not erase the computed base value with an empty string.
        preview_styles.update({key: value for key, value in styles.items() if value})
        load_dialog_preview(self.preview, self.workspace, self.preview_markup(), preview_styles)

    def preview_markup(self):
        attrs, styles = self.live_payload(); tag = self.data['tag']; text = self.caption.text() if not self.data.get('has_children') else self._preview_inner_html
        allowed = ('class', 'name', 'id', 'data-scs-action', 'placeholder', 'title')
        attr_text = ' '.join('%s="%s"' % (key, str(value).replace('&', '&amp;').replace('"', '&quot;')) for key, value in attrs.items() if key in allowed and value)
        style_text = '; '.join('%s:%s' % (key, value) for key, value in styles.items() if value)
        fragment = '<%s %s style="%s">%s</%s>' % (tag, attr_text, style_text, text, tag)
        return preview_shell(fragment, self._preview_styles)

    def update_live_preview(self):
        attrs, styles = self.live_payload(); self.refresh_dialog_preview()
        if self.group_mode: return
        payload = {'selector': self.data['selector'], 'selectors': self.data.get('selectors') or [self.data['selector']], 'attrs': attrs, 'styles': styles, 'group': self.group_mode}
        if not self.data.get('has_children'): payload['text'] = self.caption.text()
        script = '''(() => {const p=%s,locks=window.__scsEditorLocks||{};const nodes=p.selectors.map(s=>[s,document.querySelector(s)]).filter(x=>x[1]);if(!nodes.length)return;for(const [selector,e] of nodes){if(['all','style'].includes(locks[selector]))continue;if(!p.group)Object.entries(p.attrs).forEach(([k,v])=>v?e.setAttribute(k,v):e.removeAttribute(k));else if(p.attrs.class!==undefined)e.setAttribute('class',p.attrs.class);Object.entries(p.styles).forEach(([k,v])=>e.style.setProperty(k,v||''));}const e=nodes[0][1];if(!p.group&&'text'in p){e.textContent=p.text;if(e.hasAttribute('data-scs-i18n'))e.dataset.scsI18nSource=p.text;}})()''' % json.dumps(payload)
        self.workspace.panel.preview.page().runJavaScript(script)

    def reject(self):
        if self._original_outer_html:
            script = '''(() => {const e=document.querySelector(%s);if(e)e.outerHTML=%s;})()''' % (json.dumps(self.data['selector']), json.dumps(self._original_outer_html))
            self.workspace.panel.preview.page().runJavaScript(script)
        super().reject()

    def choose_color(self, field):
        color=QColorDialog.getColor(QColor(field.text()),self,self.workspace.panel.text('Выбор цвета','Choose color'))
        if color.isValid(): field.setText(color.name())

    def add_row(self):
        labels = {prop: self.workspace.panel.text(ru, en) for prop, ru, en in COMMON_CSS}
        # The picker deliberately mirrors the whole Advanced: CSS catalogue.
        # Existing fields remain selectable and simply receive focus.
        options = [(labels.get(prop, prop), prop) for prop in CSS_PROPERTY_CATALOG]
        title = self.workspace.panel.text('Выберите параметр', 'Choose property')
        choice, ok = QInputDialog.getItem(self, title, self.workspace.panel.text('Параметр', 'Property'), [label for label, _ in options], 0, False)
        if not ok: return
        prop = next(value for label, value in options if label == choice); self._general_properties.add(prop)
        register = getattr(self.workspace, 'register_general_css_property', None)
        if register: register(prop)
        if prop in self.common:
            self.set_common_visible(prop, True); field = self.common[prop]
        else:
            field = self.add_general_field(prop)
        self.tabs.setCurrentIndex(0); field.setFocus()

    def set_common_visible(self, prop, visible):
        label = self.common_labels.get(prop); widget = self.common_displays.get(prop)
        if label: label.setVisible(visible)
        if widget: widget.setVisible(visible)

    def add_general_field(self, prop, value=None):
        """Expose any Advanced: CSS declaration on the General tab."""
        if prop in self.general_extra:
            return self.general_extra[prop]
        field = QLineEdit(self.data['styles'].get(prop, '') if value is None else value)
        field.setToolTip(prop); self.general_form.addRow(prop, field)
        self.general_extra[prop] = field
        if hasattr(self, '_live_timer'):
            field.textChanged.connect(self.schedule_live_preview)
        if hasattr(self, 'tables') and 'styles' in self.tables:
            synchronize_css_fields({prop: field}, self.tables['styles'])
        return field

    def apply(self):
        values = {}
        for key, table in self.tables.items():
            values[key] = {table.item(row, 0).text().strip(): table.item(row, 1).text() if table.item(row, 1) else '' for row in range(table.rowCount()) if table.item(row, 0) and table.item(row, 0).text().strip()}
        attrs = {k: v for k, v in values['attributes'].items() if self.data['attributes'].get(k) != v and k not in ('class', 'style')}
        attrs['class'] = self.classes.text()
        styles = {k: v for k, v in values['styles'].items() if self.data['styles'].get(k, '') != v}
        for key,edit in self.common.items():
            if edit.text()!=self.data['styles'].get(key,''): styles[key]=edit.text()
        for key, edit in self.general_extra.items():
            if edit.text() != self.data['styles'].get(key, ''): styles[key] = edit.text()
        for key,edit in [('name',self.field_name),('data-scs-action',self.action_name)]:
            if edit.text()!=self.data['attributes'].get(key,''): attrs[key]=edit.text()
        if self.scope.currentIndex() == 2 and not self.class_target.text().strip():
            self.error.setText(self.workspace.panel.text('Укажите имя класса.', 'Enter a class name.')); return
        payload = dict(selector=self.data['selector'], selectors=self.data.get('selectors') or [self.data['selector']], group=self.group_mode, tag=self.data['tag'], scope=self.scope.currentIndex(), cls=self.class_target.text().strip(), attrs=attrs, styles=styles)
        if self.caption.text()!=self.data.get('text',''): payload['text']=self.caption.text()
        script = '''(() => {try {const p=%s; const nodes=p.selectors.map(s=>[s,document.querySelector(s)]).filter(pair=>pair[1]); const e=nodes[0]&&nodes[0][1]; if(!e)throw Error('Element not found');
        const locks=window.__scsEditorLocks||{};for(const [selector,node] of nodes){if(['all','style'].includes(locks[selector]))continue;if(!p.group)Object.entries(p.attrs).forEach(([k,v])=>v?node.setAttribute(k,v):node.removeAttribute(k));else if(p.attrs.class!==undefined)node.setAttribute('class',p.attrs.class);if(p.scope===0)Object.entries(p.styles).forEach(([k,v])=>node.style.setProperty(k,v));}
        if(!p.group&&'text' in p){if(e.children.length)throw Error('Text contains nested elements');e.textContent=p.text;if(e.hasAttribute('data-scs-i18n'))e.dataset.scsI18nSource=p.text;}
        return {html:'<!doctype html>\\n'+document.documentElement.outerHTML};
        }catch(e){return {error:e.message};}})()''' % json.dumps(payload)
        def done(result):
            if not result or result.get('error'): self.error.setText((result or {}).get('error', 'Error')); return
            self.workspace.panel.replace_source(format_html(result['html']))
            if self.scope.currentIndex() == 1: self.workspace.update_css_rule(self.data['tag'], styles)
            elif self.scope.currentIndex() == 2:
                self.workspace.update_css_rule('.' + payload['cls'].lstrip('.'), styles)
        self.workspace.panel.preview.page().runJavaScript(script, done)
        # The visual preview already contains the pending change.  Close the
        # modal immediately; the source update above finishes asynchronously.
        self.done(QDialog.Accepted)


class RuleSettings(QDialog):
    """A friendly editor for an existing CSS selector or a new class rule."""
    def __init__(self, workspace, selector, styles, preview_html='', preview_selector='', preview_styles='', preview_base_styles=None):
        super().__init__(workspace.panel); self.workspace = workspace; self.selector = selector
        self.preview_html, self.preview_selector, self.preview_styles = preview_html, preview_selector, preview_styles
        self.preview_base_styles = dict(preview_base_styles or {})
        self._rule_styles = dict(styles)
        self.setStyleSheet(workspace.dialog_style())
        tr = workspace.panel.text; self.setWindowTitle(tr('Настроить стили', 'Configure styles') + ' — ' + selector); self.resize(760, 680)
        layout = QVBoxLayout(self); layout.setContentsMargins(18, 18, 18, 18); layout.setSpacing(12)
        header = QLabel(); header.setObjectName('configureHeader'); header.setFixedHeight(54); header.setText('<b>%s</b><br><span>%s</span>' % (tr('Правило CSS', 'CSS rule'), selector)); header.setTextFormat(Qt.RichText); layout.addWidget(header)
        self.preview = make_dialog_preview(workspace, 108)
        self.set_preview_html(styles); layout.addWidget(self.preview)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        choices = {
            'font-weight': [('Обычный', 'Normal', '400'), ('Полужирный', 'Semibold', '600'), ('Жирный', 'Bold', '700')],
            'text-align': [('Слева', 'Left', 'left'), ('По центру', 'Center', 'center'), ('Справа', 'Right', 'right'), ('По ширине', 'Justify', 'justify')],
            'display': [('Блок', 'Block', 'block'), ('В строку', 'Inline', 'inline'), ('Гибкий ряд / колонка', 'Flex layout', 'flex'), ('Сетка', 'Grid', 'grid'), ('Скрыть', 'Hidden', 'none')],
        }
        page = QWidget(); page.setObjectName('settingsContent'); page.setMinimumWidth(680); page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum); form = QFormLayout(page); self.general_form = form; self.common = {}; self.common_rows = {}; self.general_extra = {}
        for prop, ru, en in COMMON_CSS:
            row_widget = QWidget(); row = QHBoxLayout(row_widget); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(10)
            label = QLabel(tr(ru,en)); label.setObjectName('settingsField'); label.setFixedWidth(155); row.addWidget(label)
            value = styles.get(prop, '')
            if prop in choices:
                field = PropertyChoice(); field.addItem('—', '')
                for option_ru, option_en, css in choices[prop]: field.addItem(tr(option_ru, option_en), css)
                index = field.findData(value)
                if index < 0 and value: field.addItem(value, value); index = field.count() - 1
                field.setCurrentIndex(index if index >= 0 else 0)
                field.setToolTip(prop); row.addWidget(field, 1)
            elif prop in ('color', 'background-color'):
                field = QLineEdit(value); field.setToolTip(prop)
                wrapper = QWidget(); color_row = QHBoxLayout(wrapper); color_row.setContentsMargins(0, 0, 0, 0); color_row.setSpacing(6)
                color_row.addWidget(field, 1)
                button = QPushButton(tr('Выбрать…', 'Choose…')); color_row.addWidget(button)
                button.clicked.connect(lambda _, field=field: self.choose_color(field))
                row.addWidget(wrapper, 1)
            else:
                field = QLineEdit(value); field.setToolTip(prop); row.addWidget(field, 1)
            self.common[prop] = field; self.common_rows[prop] = row_widget; form.addRow(row_widget)
        general_properties = getattr(workspace, 'general_css_properties', lambda: CLASS_CSS_PROPERTIES)()
        for prop in general_properties:
            if prop not in self.common:
                self.add_general_field(prop, styles.get(prop, ''))
        for prop, value in styles.items():
            if prop not in self.common:
                self.add_general_field(prop, value)
        self._general_properties = {prop for prop, _, _ in COMMON_CSS}
        for prop, _, _ in COMMON_CSS: self.common_rows[prop].setVisible(True)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); scroll.setWidget(page); self.tabs.addTab(scroll, tr('Основное', 'General'))
        names = list(dict.fromkeys((*CSS_PROPERTY_CATALOG, *styles)))
        self.table = QTableWidget(len(names), 2); self.table.setHorizontalHeaderLabels([tr('Параметр','Property'), tr('Значение','Value')]); self.table.horizontalHeader().setStretchLastSection(True)
        for row, key in enumerate(names): self.table.setItem(row, 0, QTableWidgetItem(key)); self.table.setItem(row, 1, QTableWidgetItem(styles.get(key, '')))
        self.tabs.addTab(self.table, tr('Дополнительно: CSS', 'Advanced: CSS'))
        synchronize_css_fields(self.common, self.table)
        for prop, field in self.general_extra.items():
            synchronize_css_fields({prop: field}, self.table)
        property_buttons = QHBoxLayout(); self.add_property_button = QPushButton(tr('+ Параметр', '+ Property'))
        def add_property():
            labels = {prop: tr(ru, en) for prop, ru, en in COMMON_CSS}
            options = [(labels.get(prop, prop), prop) for prop in CSS_PROPERTY_CATALOG]
            choice, ok = QInputDialog.getItem(self, tr('Выберите параметр', 'Choose property'), tr('Параметр', 'Property'), [label for label, _ in options], 0, False)
            if not ok: return
            prop = next(value for label, value in options if label == choice); self._general_properties.add(prop)
            register = getattr(self.workspace, 'register_general_css_property', None)
            if register: register(prop)
            if prop in self.common:
                self.common_rows[prop].setVisible(True); field = self.common[prop]
            else:
                field = self.add_general_field(prop)
            self.tabs.setCurrentIndex(0); field.setFocus()
        self.add_property_button.clicked.connect(add_property); property_buttons.addWidget(self.add_property_button)
        self.all_properties_button = QPushButton(tr('Все параметры', 'All parameters')); self.all_properties_button.setObjectName('outlineButton'); self.all_properties_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.table)); property_buttons.addWidget(self.all_properties_button); property_buttons.addStretch(); layout.addLayout(property_buttons)
        self.error = QLabel(); layout.addWidget(self.error)
        self.apply_button = QPushButton(tr('Применить', 'Apply')); layout.addWidget(self.apply_button)
        self._preview_style_id = 'scs-rule-preview-' + uuid.uuid4().hex
        self._live_timer = QTimer(self); self._live_timer.setSingleShot(True); self._live_timer.timeout.connect(self.update_live_preview)
        for field in (*self.common.values(), *self.general_extra.values()):
            if isinstance(field, QLineEdit): field.textChanged.connect(self.schedule_live_preview)
            elif isinstance(field, QComboBox): field.currentIndexChanged.connect(self.schedule_live_preview)
        self.table.itemChanged.connect(lambda _item: self.schedule_live_preview())
        def save():
            values = {self.table.item(row,0).text().strip(): self.table.item(row,1).text() if self.table.item(row,1) else '' for row in range(self.table.rowCount()) if self.table.item(row,0) and self.table.item(row,0).text().strip()}
            for key, field in self.common.items():
                if field.text() != styles.get(key, ''): values[key] = field.text()
            for key, field in self.general_extra.items():
                if field.text() != styles.get(key, ''): values[key] = field.text()
            if self.workspace.update_css_rule(self.selector, values): self.done(QDialog.Accepted)
            else: self.error.setText(self.workspace.panel.text('В проекте нет блока CSS для нового правила.', 'The project has no CSS block for the new rule.'))
        self.apply_button.clicked.connect(save)

    def values(self):
        values = {self.table.item(row,0).text().strip(): self.table.item(row,1).text() if self.table.item(row,1) else '' for row in range(self.table.rowCount()) if self.table.item(row,0) and self.table.item(row,0).text().strip()}
        for key, field in self.common.items(): values[key] = field.currentData() if isinstance(field, QComboBox) else field.text()
        for key, field in self.general_extra.items(): values[key] = field.text()
        return {key: value for key, value in values.items() if value}

    def preview_markup(self, values):
        declaration = '; '.join('%s:%s' % (key, value) for key, value in values.items() if value)
        if self.preview_html:
            selector = self.preview_selector or self.selector
            theme = (re.search(r'data-scs-theme="([^"]+)"', self.selector) or [None, ''])[1]
            return preview_shell(self.preview_html, self.preview_styles, selector, declaration, theme)
        return preview_shell('<b>%s</b><br><small>%s</small>' % (self.workspace.panel.text('Предпросмотр стиля', 'Style preview'), self.selector), '', 'main', declaration)

    def set_preview_html(self, values):
        preview_values = dict(self.preview_base_styles); preview_values.update(values)
        load_dialog_preview(self.preview, self.workspace, self.preview_markup(values), preview_values)

    def schedule_live_preview(self, *_): self._live_timer.start(80)

    def update_live_preview(self):
        values = self.values(); declaration = '; '.join('%s:%s' % (key, value) for key, value in values.items())
        self.set_preview_html(values)
        script = '''(() => {let style=document.getElementById(%s);if(!style){style=document.createElement('style');style.id=%s;document.head.appendChild(style);}style.textContent=%s;})()''' % (json.dumps(self._preview_style_id), json.dumps(self._preview_style_id), json.dumps(self.selector + ' {' + declaration + '}'))
        self.workspace.panel.preview.page().runJavaScript(script)

    def reject(self):
        self.workspace.panel.preview.page().runJavaScript("document.getElementById(%s)?.remove();" % json.dumps(self._preview_style_id))
        super().reject()

    def choose_color(self, field):
        color = QColorDialog.getColor(QColor(field.text()), self, self.workspace.panel.text('Выбор цвета', 'Choose color'))
        if color.isValid(): field.setText(color.name())

    def add_general_field(self, prop, value=None):
        if prop in self.general_extra:
            return self.general_extra[prop]
        field = QLineEdit(self._rule_styles.get(prop, '') if value is None else value)
        field.setToolTip(prop); self.general_form.addRow(prop, field)
        self.general_extra[prop] = field
        if hasattr(self, '_live_timer'):
            field.textChanged.connect(self.schedule_live_preview)
        if hasattr(self, 'table'):
            synchronize_css_fields({prop: field}, self.table)
        return field


def basic_document(language='en'):
    return f'''<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SCS Tool</title>
  <style name="BASE_STYLES">
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 24px; background: #101216; color: #f4f6f8;
           font: 16px system-ui, sans-serif; }}
    main {{ max-width: 960px; margin: auto; }}
    button, input, select {{ font: inherit; padding: 10px 16px; border-radius: 10px; }}
    button {{ background: #ee6b2f; color: white; border: 0; cursor: pointer; }}
    .card, .tool-card {{ background: #191d25; padding: 24px; border-radius: 18px; }}
    .tool-title {{ margin-top: 0; }}
    .tool-description {{ margin-bottom: 0; }}
  </style>
</head>
<body class="tool-page">
  <main id="tool-root" class="tool-main">
    <section class="card tool-card">
      <h1 id="tool-title" class="tool-title">SCS Tool</h1>
      <p id="tool-description" class="tool-description">Welcome</p>
    </section>
  </main>
  <!-- LANGUAGE_BRIDGE -->
  <script name="LANGUAGE_BRIDGE" data-bridge="language">
  (function languageBridge() {{
    const applyLanguage = language => {{
      const code = language === 'ru' ? 'ru' : 'en';
      const baseLanguage = document.documentElement.dataset.scsBaseLanguage || document.documentElement.lang || 'en';
      document.documentElement.lang = code;
      const translations = window.scsTranslations || {{}};
      document.querySelectorAll('[data-scs-i18n]').forEach(node => {{
        const source = node.dataset.scsI18nSource ?? node.textContent;
        node.dataset.scsI18nSource = source;
        const value = code === baseLanguage ? source : translations[code] && translations[code][node.dataset.scsI18n];
        node.textContent = value != null ? value : source;
      }});
    }};
    const previous = window.setLanguage;
    window.setLanguage = language => {{ if (typeof previous === 'function') previous(language); applyLanguage(language); }};
    window.setLanguage(document.documentElement.lang || 'ru');
  }})();
  </script>
  <!-- THEME_BRIDGE -->
  <script name="THEME_BRIDGE" data-bridge="theme">
  (function themeBridge() {{
    const applyTheme = theme => {{
      const value = String(theme || 'dark');
      document.documentElement.dataset.scsTheme = value;
      document.documentElement.style.colorScheme = ['light', 'lavender', 'spectrum_light'].includes(value) ? 'light' : 'dark';
    }};
    const previous = window.setManagerTheme;
    window.setManagerTheme = theme => {{ if (typeof previous === 'function') previous(theme); applyTheme(theme); }};
    applyTheme(document.documentElement.dataset.scsTheme || 'dark');
  }})();
  </script>
  <!-- DENSITY_BRIDGE -->
  <script name="DENSITY_BRIDGE" data-bridge="density">
  (function densityBridge() {{
    const previous = window.setInterfaceDensity;
    window.setInterfaceDensity = percent => {{
      if (typeof previous === 'function') previous(percent);
      const value = Math.max(25, Math.min(200, Number(percent) || 100));
      document.documentElement.style.setProperty('--scs-interface-density', value / 100);
      document.documentElement.style.fontSize = value + '%';
    }};
    window.setInterfaceDensity(100);
  }})();
  </script>
</body>
</html>
'''


class AuthoringWorkspace(QWidget):
    def __init__(self, panel, highlighter):
        super().__init__(); self.panel = panel; self.highlighter_class = highlighter
        self.update_theme()
        self.labels = []; self.editors = {}; self.active_path = None; self.selected_selectors = []; self.copied_styles = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        self.tabs.addTab(panel.source, 'HTML')
        assets = QWidget(); assets.setObjectName('authoringPage'); box = QVBoxLayout(assets)
        asset_bar = QHBoxLayout(); self.files = QComboBox(); asset_bar.addWidget(self.files, 1)
        self.rename_asset_button = QPushButton(); self.rename_asset_button.setObjectName('outlineButton')
        self.rename_asset_button.clicked.connect(self.rename_asset); asset_bar.addWidget(self.rename_asset_button)
        box.addLayout(asset_bar)
        self.asset_editor = QPlainTextEdit(); self.asset_editor.setObjectName('projectEditor')
        self.asset_highlighter = highlighter(self.asset_editor.document()); box.addWidget(self.asset_editor)
        self.tabs.addTab(assets, 'CSS / JS')
        self.asset_editor.textChanged.connect(self.save_asset)
        self.files.currentIndexChanged.connect(self.open_asset)
        row = QHBoxLayout(); box.addLayout(row)
        self.button(row, 'Добавить стили в HTML', 'Add styles to HTML', lambda: self.new_asset('css'))
        self.button(row, 'Добавить JS в HTML', 'Add JS to HTML', lambda: self.new_asset('js'))
        classes = QWidget(); classes.setObjectName('authoringPage'); classes_form = QFormLayout(classes); self.classes_form = classes_form
        self.class_selector = QLineEdit(); self.class_selector.setReadOnly(True); classes_form.addRow('CSS selector', self.class_selector)
        self.element_classes_label = QLabel(); self.element_classes = QLineEdit(); classes_form.addRow(self.element_classes_label, self.element_classes)
        self.button(classes_form, 'Сохранить классы объекта', 'Save element classes', self.save_element_classes)
        self.class_list_label = QLabel(); classes_form.addRow(self.class_list_label)
        self.class_list = QListWidget(); classes_form.addRow(self.class_list)
        class_buttons = QHBoxLayout(); classes_form.addRow(class_buttons)
        self.button(class_buttons, '+ Класс', '+ Class', self.new_class)
        self.button(class_buttons, 'Удалить правило', 'Remove rule', self.remove_class_rule)
        self.class_rule_title = QLabel(); classes_form.addRow(self.class_rule_title)
        self.class_properties = {}; self.class_property_labels = {}
        class_labels = {prop: (ru, en) for prop, ru, en in COMMON_CSS}
        for prop in CLASS_CSS_PROPERTIES:
            field = QLineEdit(); field.setToolTip(prop); self.class_properties[prop] = field
            label = QLabel(); self.class_property_labels[prop] = label
            classes_form.addRow(label, field)
        self.class_choice_options = {
            'font-weight': [('Обычный', 'Normal', '400'), ('Полужирный', 'Semibold', '600'), ('Жирный', 'Bold', '700')],
            'text-align': [('Слева', 'Left', 'left'), ('По центру', 'Center', 'center'), ('Справа', 'Right', 'right'), ('По ширине', 'Justify', 'justify')],
            'display': [('Блок', 'Block', 'block'), ('В строку', 'Inline', 'inline'), ('Гибкий ряд / колонка', 'Flex layout', 'flex'), ('Сетка', 'Grid', 'grid'), ('Скрыть', 'Hidden', 'none')],
        }
        for prop, options in self.class_choice_options.items():
            old = self.class_properties[prop]; combo = PropertyChoice()
            combo.addItem('—', '')
            for ru, en, css in options: combo.addItem(self.panel.text(ru, en), css)
            classes_form.replaceWidget(old, combo); old.deleteLater(); self.class_properties[prop] = combo
        self.class_color_buttons = {}
        for prop in ('color', 'background-color'):
            field = self.class_properties[prop]; wrapper = QWidget(); row = QHBoxLayout(wrapper); row.setContentsMargins(0, 0, 0, 0)
            classes_form.replaceWidget(field, wrapper); row.addWidget(field)
            choose = QPushButton(self.panel.text('Выбрать…', 'Choose…')); choose.setObjectName('outlineButton'); row.addWidget(choose)
            self.class_color_buttons[prop] = choose
            choose.clicked.connect(lambda _, target=field: self.choose_class_color(target))
        for prop in self.general_css_properties():
            if prop not in self.class_properties:
                self.ensure_class_property(prop)
        self.class_advanced = QTableWidget(len(CSS_PROPERTY_CATALOG), 2); self.class_advanced.setHorizontalHeaderLabels([self.panel.text('Параметр', 'Property'), self.panel.text('Значение', 'Value')]); self.class_advanced.horizontalHeader().setStretchLastSection(True)
        for row, prop in enumerate(CSS_PROPERTY_CATALOG): self.class_advanced.setItem(row, 0, QTableWidgetItem(prop)); self.class_advanced.setItem(row, 1, QTableWidgetItem(''))
        self.class_advanced.setMinimumHeight(330); self.class_advanced_label = QLabel(); classes_form.addRow(self.class_advanced_label, self.class_advanced)
        synchronize_css_fields(self.class_properties, self.class_advanced)
        self.button(classes_form, 'Применить стили класса', 'Apply class styles', self.save_class_rule)
        self.class_list.currentTextChanged.connect(self.load_class_rule)
        classes_scroll = QScrollArea(); classes_scroll.setWidgetResizable(True); classes_scroll.setWidget(classes)
        self.tabs.addTab(classes_scroll, '')
        inspector = QWidget(); inspector.setObjectName('authoringPage'); form = QFormLayout(inspector)
        self.actions_title = QLabel(); self.actions_title.setObjectName('toolName'); form.addRow(self.actions_title)
        self.action_bridge = QPushButton(); self.action_bridge.setObjectName('outlineButton'); self.action_bridge.clicked.connect(self.panel.enable_bridge); form.addRow(self.action_bridge)
        self.logging_bridge = QPushButton(); self.logging_bridge.setObjectName('outlineButton'); self.logging_bridge.clicked.connect(self.panel.enable_logging_bridge); form.addRow(self.logging_bridge)
        self.responsive_button = QPushButton(); self.responsive_button.setObjectName('outlineButton'); self.responsive_button.clicked.connect(self.apply_responsive_layout); form.addRow(self.responsive_button)
        density_row = QWidget(); density_layout = QHBoxLayout(density_row); density_layout.setContentsMargins(0, 0, 0, 0); self.density_scale = QLineEdit('100'); self.density_scale.setPlaceholderText('100'); self.density_scale.setMaximumWidth(90); self.density_bridge = QPushButton(); self.density_bridge.setObjectName('outlineButton'); self.density_bridge.clicked.connect(self.apply_density_bridge); density_layout.addWidget(self.density_scale); density_layout.addWidget(QLabel('%')); density_layout.addWidget(self.density_bridge, 1); self.density_label = QLabel(); form.addRow(self.density_label, density_row)
        self.selector = QLineEdit('body'); form.addRow(self.panel.text('Селектор элемента', 'Element selector'), self.selector)
        self.selector.setReadOnly(True)
        self.action_direction = QComboBox(); self.action_direction.addItem('', 'input'); self.action_direction.addItem('', 'output')
        self.direction_label = QLabel(); form.addRow(self.direction_label, self.action_direction)
        self.name = QLineEdit(); self.action = QLineEdit(); self.name_label = QLabel(); self.action_label = QLabel(); form.addRow(self.name_label, self.name); form.addRow(self.action_label, self.action)
        self.action_choices = QComboBox(); self.action_choices.setObjectName('input'); self.action_choices.setEditable(True); self.action_choices.currentTextChanged.connect(lambda value: self.action.setText(value))
        self.id_choices = QComboBox(); self.id_choices.setObjectName('input'); self.id_choices.setEditable(True); self.id_choices.currentTextChanged.connect(lambda value: self.name.setText(value))
        self.action_choices_label = QLabel(); self.id_choices_label = QLabel(); form.addRow(self.action_choices_label, self.action_choices); form.addRow(self.id_choices_label, self.id_choices)
        self.refresh_action_choices = QPushButton(); self.refresh_action_choices.setObjectName('outlineButton'); self.refresh_action_choices.clicked.connect(self.refresh_logic_options); form.addRow(self.refresh_action_choices)
        self.action_direction.currentIndexChanged.connect(self.update_action_direction)
        self.button(form, 'Связать элементы со скриптом', 'Bind controls to script', self.bind)
        self.language_tools_title = QLabel(); self.language_tools_title.setObjectName('toolName'); form.addRow(self.language_tools_title)
        self.language_bridge = QPushButton(); self.language_bridge.setObjectName('outlineButton'); self.language_bridge.clicked.connect(self.panel.enable_language_bridge); form.addRow(self.language_bridge)
        self.translation_language = QComboBox(); self.translation_language.addItem('Русский', 'ru'); self.translation_language.addItem('English', 'en')
        self.translation_source = QLineEdit(); self.translation_target = QLineEdit(); self.translation_language_label = QLabel(); self.translation_source_label = QLabel(); self.translation_target_label = QLabel()
        form.addRow(self.translation_language_label, self.translation_language); form.addRow(self.translation_source_label, self.translation_source); form.addRow(self.translation_target_label, self.translation_target)
        self.apply_translation_button = QPushButton(); self.apply_translation_button.setObjectName('primaryButton'); self.apply_translation_button.clicked.connect(self.apply_language_replacement); form.addRow(self.apply_translation_button)
        self.translation_table_button = QPushButton(); self.translation_table_button.setObjectName('outlineButton'); self.translation_table_button.clicked.connect(self.open_translation_table); form.addRow(self.translation_table_button)
        self.theme_tools_title = QLabel(); self.theme_tools_title.setObjectName('toolName'); form.addRow(self.theme_tools_title)
        self.theme_bridge = QPushButton(); self.theme_bridge.setObjectName('outlineButton'); self.theme_bridge.clicked.connect(self.panel.enable_theme_bridge); form.addRow(self.theme_bridge)
        self.theme_name = QComboBox(); self.theme_target_kind = QComboBox(); self.theme_target = QComboBox(); self.theme_name_label = QLabel(); self.theme_target_kind_label = QLabel(); self.theme_target_label = QLabel()
        for code in ('dark', 'light', 'ember', 'ocean', 'lavender', 'fire', 'spectrum_dark', 'spectrum_light'): self.theme_name.addItem(code, code)
        self.theme_target_kind.addItem('', 'element'); self.theme_target_kind.addItem('', 'class'); self.theme_target_kind.addItem('', 'tag')
        self.theme_target_kind.currentIndexChanged.connect(self.refresh_theme_targets)
        form.addRow(self.theme_name_label, self.theme_name); form.addRow(self.theme_target_kind_label, self.theme_target_kind); form.addRow(self.theme_target_label, self.theme_target)
        self.configure_theme_button = QPushButton(); self.configure_theme_button.setObjectName('primaryButton'); self.configure_theme_button.clicked.connect(self.configure_theme_rule); form.addRow(self.configure_theme_button)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(inspector)
        self.tabs.addTab(scroll, '')
        code = QWidget(); code.setObjectName('authoringPage'); code_layout = QVBoxLayout(code); code_bar = QHBoxLayout(); self.code_files = QComboBox(); self.code_files.currentIndexChanged.connect(self.open_code_file); self.code_actions = QComboBox(); self.code_actions.currentIndexChanged.connect(self.jump_to_action); code_bar.addWidget(self.code_files, 1); code_bar.addWidget(self.code_actions, 1); code_layout.addLayout(code_bar); self.code_editor = QPlainTextEdit(); self.code_editor.setObjectName('projectEditor'); self.code_highlighter = highlighter(self.code_editor.document()); self.code_editor.textChanged.connect(self.schedule_code_save); code_layout.addWidget(self.code_editor, 1); self.code_save_timer = QTimer(self); self.code_save_timer.setSingleShot(True); self.code_save_timer.setInterval(500); self.code_save_timer.timeout.connect(self.save_code_file); self.code_path = None; self.tabs.addTab(code, '')
        dom = QWidget(); dom.setObjectName('authoringPage'); dom_layout = QVBoxLayout(dom); self.dom_search = QLineEdit(); self.dom_search.textChanged.connect(self.filter_dom); self.dom_breadcrumb = QLabel(); self.dom_breadcrumb.setWordWrap(True); self.dom_breadcrumb.setObjectName('pageSubtitle'); self.dom_tree = QTreeWidget(); self.dom_tree.setHeaderHidden(True); self.dom_tree.itemClicked.connect(self.select_dom_item); dom_layout.addWidget(self.dom_search); dom_layout.addWidget(self.dom_breadcrumb); dom_layout.addWidget(self.dom_tree, 1); self.tabs.addTab(dom, '')
        row = QHBoxLayout(); layout.addLayout(row)
        self.button(row, 'Базовая структура HTML', 'Basic HTML document', self.new_document)
        self.width = QComboBox(); self.width.addItems(['Auto', '375 px', '768 px', '1024 px']); row.addWidget(self.width)
        self.width.currentIndexChanged.connect(self.resize_preview)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.reload_assets(); self.reload_code_files(); self.reload_dom(); self.translate()
        self.tabs.currentChanged.connect(self.tab_changed)
        self.panel.source.textChanged.connect(self.source_changed)
        self.panel.preview.loadFinished.connect(self.install_selection)
        self.selection_timer = QTimer(self); self.selection_timer.setInterval(400)
        self.selection_timer.timeout.connect(self.read_selection); self.selection_timer.start()
        self.last_selection = None; self.pending_highlight = False
        panel.preview.setContextMenuPolicy(Qt.NoContextMenu)

    def general_css_properties(self):
        """Return the persistent set of fields shown in every General editor."""
        app = getattr(getattr(self.panel, 'owner', None), 'app', None)
        stored = getattr(app, 'settings', {}).get('html_general_css_properties', [])
        if not isinstance(stored, list): stored = []
        extras = [prop for prop in (*stored, *sorted(EXTRA_GENERAL_CSS_PROPERTIES)) if prop in CSS_PROPERTY_CATALOG]
        return tuple(dict.fromkeys((*CLASS_CSS_PROPERTIES, *extras)))

    def register_general_css_property(self, prop):
        """Make a chosen CSS property a permanent General field everywhere."""
        if prop not in CSS_PROPERTY_CATALOG: return
        EXTRA_GENERAL_CSS_PROPERTIES.add(prop)
        app = getattr(getattr(self.panel, 'owner', None), 'app', None)
        settings = getattr(app, 'settings', None)
        if isinstance(settings, dict):
            stored = settings.get('html_general_css_properties', [])
            stored = list(stored) if isinstance(stored, list) else []
            if prop not in stored:
                stored.append(prop); settings['html_general_css_properties'] = stored
                save_settings(settings)
        self.ensure_class_property(prop)

    def ensure_class_property(self, prop):
        """Add a persistent General property to the Classes editor immediately."""
        if prop in self.class_properties: return self.class_properties[prop]
        field = QLineEdit(); field.setToolTip(prop); label = QLabel(prop)
        self.class_properties[prop] = field; self.class_property_labels[prop] = label
        if hasattr(self, 'class_advanced_label'):
            row, _role = self.classes_form.getWidgetPosition(self.class_advanced_label)
            self.classes_form.insertRow(max(0, row), label, field)
        else:
            self.classes_form.addRow(label, field)
        if hasattr(self, 'class_advanced'):
            synchronize_css_fields({prop: field}, self.class_advanced)
            item = self.class_list.currentItem() if hasattr(self, 'class_list') else None
            if item: field.setText(self.class_rule_values(item.text()).get(prop, ''))
        return field

    def theme_key(self):
        app = getattr(getattr(self.panel, 'owner', None), 'app', None)
        return getattr(app, 'settings', {}).get('theme', 'dark')

    def dialog_style(self):
        return authoring_theme_style(self.theme_key(), dialog=True)

    def vertical_header_style(self):
        theme = self.theme_key()
        colors = ('#eef2f6', '#536172', '#cbd5e1') if theme == 'light' else ('#281c19', '#d7aa96', '#4d342d') if theme == 'ember' else ('#142b3d', '#a9cbdc', '#24465e') if theme == 'ocean' else ('#f2eef9', '#665e7d', '#ddd3f0') if theme == 'lavender' else ('#351a16', '#e2b6a9', '#623029') if theme == 'fire' else ('#222450', '#c7c8ff', '#4c4ca1') if theme == 'spectrum_dark' else ('#eef0ff', '#665e7d', '#c3c4e9') if theme == 'spectrum_light' else ('#1d2129', '#aab4c2', '#303744')
        return f'QHeaderView {{ background:{colors[0]}; border:0; }} QHeaderView::section {{ background:{colors[0]}; color:{colors[1]}; border:0; border-right:1px solid {colors[2]}; border-bottom:1px solid {colors[2]}; padding:0; }}'

    def update_theme(self):
        self.setStyleSheet(authoring_theme_style(self.theme_key()))

    def context_menu(self, position):
        QTimer.singleShot(50, lambda: self.show_element_menu(position))

    def show_element_menu(self, position):
        menu = QMenu(self.panel)
        configure = menu.addAction(self.panel.text('Настроить', 'Configure'))
        global_styles = menu.addAction(self.panel.text('Глобальные стили…', 'Global styles…'))
        style_sources = menu.addAction(self.panel.text('Источники и конфликты стилей…', 'Style sources and conflicts…'))
        visual_effects = menu.addAction(self.panel.text('Границы, тень и transform…', 'Borders, shadow and transform…'))
        move = menu.addAction(self.panel.text('Переместить', 'Move'))
        html_move = menu.addAction(self.panel.text('Переместить в HTML', 'Move in HTML'))
        menu.addSeparator()
        duplicate = menu.addAction(self.panel.text('Дублировать элемент', 'Duplicate element'))
        copy_menu = menu.addMenu(self.panel.text('Копировать элемент', 'Copy element'))
        copy_html = copy_menu.addAction('HTML'); copy_css = copy_menu.addAction('CSS'); copy_component = copy_menu.addAction(self.panel.text('Готовый компонент', 'Ready component'))
        lock_menu = menu.addMenu(self.panel.text('Блокировка', 'Lock'))
        lock_all = lock_menu.addAction(self.panel.text('От всех изменений', 'All changes')); lock_move = lock_menu.addAction(self.panel.text('От любого перемещения', 'All movement')); lock_x = lock_menu.addAction(self.panel.text('От перемещения по X', 'Horizontal movement')); lock_y = lock_menu.addAction(self.panel.text('От перемещения по Y', 'Vertical movement')); lock_style = lock_menu.addAction(self.panel.text('Только стили', 'Styles only')); unlock = lock_menu.addAction(self.panel.text('Снять блокировку', 'Unlock'))
        grid = menu.addAction(self.panel.text('Сетка и направляющие', 'Grid and guides'))
        reset = menu.addAction(self.panel.text('Сбросить масштаб', 'Reset zoom'))
        chosen = menu.exec_(self.panel.preview.mapToGlobal(position))
        if chosen == style_sources: self.show_style_sources(); return
        if chosen == visual_effects: self.open_visual_effects(); return
        if chosen == duplicate: self.duplicate_selected(); return
        if chosen in (copy_html, copy_css, copy_component): self.copy_selected_element('html' if chosen == copy_html else 'css' if chosen == copy_css else 'component'); return
        if chosen in (lock_all, lock_move, lock_x, lock_y, lock_style, unlock): self.set_selected_lock('all' if chosen == lock_all else 'move' if chosen == lock_move else 'move-x' if chosen == lock_x else 'move-y' if chosen == lock_y else 'style' if chosen == lock_style else ''); return
        if chosen == grid: self.toggle_grid(); return
        if chosen == html_move: self.start_html_move(); return
        if chosen == move: self.start_move(); return
        if chosen == reset: self.panel.preview.setZoomFactor(1.0)
        if chosen == global_styles: self.choose_rule('global'); return
        if chosen != configure: return
        script = '''(() => {const selector=window.__scsSelectedSelector;const e=selector&&document.querySelector(selector);if(!e)return null;
        const lock=(window.__scsEditorLocks||{})[selector]||'';if(lock==='all'||lock==='style')return {locked:lock};
        const styles={};
        for(const sheet of document.styleSheets){try{for(const r of sheet.cssRules||[]){if(r.type===CSSRule.STYLE_RULE&&e.matches(r.selectorText))for(const key of r.style)styles[key]=r.style.getPropertyValue(key);}}catch(_){}}
        for(const key of e.style)styles[key]=e.style.getPropertyValue(key);
        const attributes={};Array.from(e.attributes).forEach(a=>attributes[a.name]=a.value);
        const computed=getComputedStyle(e), preview_base_styles={};
        %s.forEach(key=>preview_base_styles[key]=computed.getPropertyValue(key));
        const styleSheets=Array.from(document.querySelectorAll('style')).map(style=>style.textContent).join("\\n");
        return {selector, selectors:(window.__scsSelectedSelectors||[selector]).slice(), tag:e.tagName.toLowerCase(), styles, attributes, has_children:!!e.children.length, text:e.children.length?'':e.textContent, outer_html:e.outerHTML, style_sheets:styleSheets, preview_base_styles};})()''' % json.dumps([prop for prop, _, _ in COMMON_CSS])
        def open_settings(data):
            if data and data.get('locked'):
                QMessageBox.information(self.panel, self.panel.text('Элемент заблокирован', 'Element is locked'), self.panel.text('Сначала снимите блокировку элемента.', 'Unlock the element first.'))
            elif data: ElementSettings(self, data).exec_()
        self.panel.preview.page().runJavaScript(script, open_settings)

    def show_style_sources(self):
        script = '''(() => {const e=document.querySelector(window.__scsSelectedSelector);if(!e)return null;const sources=[],seen={};
          for(const sheet of document.styleSheets){try{for(const rule of sheet.cssRules||[]){if(rule.type!==CSSRule.STYLE_RULE||!e.matches(rule.selectorText))continue;const props={};for(const key of rule.style){const value=rule.style.getPropertyValue(key);props[key]=value;(seen[key]||(seen[key]=[])).push({source:rule.selectorText,value});}sources.push({source:rule.selectorText,props});}}catch(_) {}}
          const inline={};for(const key of e.style){if(key==='outline'||key==='outline-offset')continue;const value=e.style.getPropertyValue(key);inline[key]=value;(seen[key]||(seen[key]=[])).push({source:'inline',value});}if(Object.keys(inline).length)sources.push({source:'inline',props:inline});
          const conflicts=Object.entries(seen).filter(([_,items])=>new Set(items.map(x=>x.value)).size>1).map(([property,items])=>({property,items}));return {sources,conflicts};})()'''
        def show(data):
            if not data: return
            lines=[]
            for source in data.get('sources', []):
                lines.append('[' + source.get('source', '') + ']'); lines.extend('  %s: %s' % item for item in source.get('props', {}).items())
            conflicts=data.get('conflicts', [])
            if conflicts:
                lines.append(''); lines.append(self.panel.text('КОНФЛИКТЫ:', 'CONFLICTS:'))
                for conflict in conflicts: lines.append('  %s → %s' % (conflict['property'], ' | '.join('%s: %s' % (item['source'], item['value']) for item in conflict['items'])))
            QMessageBox.information(self.panel, self.panel.text('Источники стилей', 'Style sources'), '\n'.join(lines) if lines else self.panel.text('Явно заданные стили не найдены.', 'No explicitly authored styles were found.'))
        self.panel.preview.page().runJavaScript(script, show)

    def open_visual_effects(self):
        dialog = QDialog(self.panel); dialog.setStyleSheet(self.dialog_style()); dialog.setWindowTitle(self.panel.text('Визуальные эффекты', 'Visual effects')); dialog.resize(560, 540)
        layout = QVBoxLayout(dialog); form = QFormLayout(); layout.addLayout(form)
        def spin(minimum, maximum, value=0, suffix=' px'):
            field = QSpinBox(); field.setRange(minimum, maximum); field.setValue(value); field.setSuffix(suffix); return field
        radius, width = spin(0, 200, 12), spin(0, 40, 0)
        border_color = QLineEdit('#ee6b2f'); shadow_color = QLineEdit('rgba(0, 0, 0, 0.35)')
        shadow_x, shadow_y, shadow_blur = spin(-100, 100, 0), spin(-100, 100, 10), spin(0, 200, 24)
        rotation, scale = spin(-180, 180, 0, '°'), spin(25, 300, 100, ' %')
        gradient = QCheckBox(self.panel.text('Использовать линейный градиент', 'Use linear gradient')); gradient_angle = spin(0, 360, 135, '°'); gradient_from = QLineEdit('#ee6b2f'); gradient_to = QLineEdit('#b53c45')
        fields = [
            (self.panel.text('Скругление', 'Border radius'), radius), (self.panel.text('Толщина границы', 'Border width'), width),
            (self.panel.text('Цвет границы', 'Border color'), border_color), (self.panel.text('Тень X', 'Shadow X'), shadow_x),
            (self.panel.text('Тень Y', 'Shadow Y'), shadow_y), (self.panel.text('Размытие тени', 'Shadow blur'), shadow_blur),
            (self.panel.text('Цвет тени', 'Shadow color'), shadow_color), (self.panel.text('Поворот', 'Rotation'), rotation),
            (self.panel.text('Масштаб', 'Scale'), scale), ('', gradient), (self.panel.text('Угол градиента', 'Gradient angle'), gradient_angle),
            (self.panel.text('Начальный цвет', 'Start color'), gradient_from), (self.panel.text('Конечный цвет', 'End color'), gradient_to)]
        for label, field in fields: form.addRow(label, field)
        buttons = QHBoxLayout(); cancel = QPushButton(self.panel.text('Отмена', 'Cancel')); apply = QPushButton(self.panel.text('Применить', 'Apply')); apply.setObjectName('primaryButton'); cancel.clicked.connect(dialog.reject); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(apply); layout.addLayout(buttons)
        def save():
            values = {
                'border-radius': f'{radius.value()}px', 'border-width': f'{width.value()}px',
                'border-style': 'solid' if width.value() else 'none', 'border-color': border_color.text().strip(),
                'box-shadow': f'{shadow_x.value()}px {shadow_y.value()}px {shadow_blur.value()}px {shadow_color.text().strip()}',
                'transform': f'rotate({rotation.value()}deg) scale({scale.value() / 100:g})'}
            if gradient.isChecked(): values['background-image'] = f'linear-gradient({gradient_angle.value()}deg, {gradient_from.text().strip()}, {gradient_to.text().strip()})'
            script = '''(() => {const selectors=window.__scsSelectedSelectors||[window.__scsSelectedSelector],locks=window.__scsEditorLocks||{};let changed=0;for(const selector of selectors){const e=selector&&document.querySelector(selector);if(!e||['all','style'].includes(locks[selector]))continue;for(const [key,value] of Object.entries(%s))e.style.setProperty(key,value);changed++;}return changed?'<!doctype html>\\n'+document.documentElement.outerHTML:null;})()''' % json.dumps(values)
            self.panel.preview.page().runJavaScript(script, lambda html: self.panel.replace_source(format_html(html)) if html else None); dialog.accept()
        apply.clicked.connect(save); dialog.exec_()

    def set_selected_lock(self, value):
        script = '''(() => {const selectors=window.__scsSelectedSelectors||[window.__scsSelectedSelector];window.__scsEditorLocks=window.__scsEditorLocks||{};let changed=0;for(const selector of selectors){if(!selector||!document.querySelector(selector))continue;if(%s)window.__scsEditorLocks[selector]=%s;else delete window.__scsEditorLocks[selector];changed++;}return changed;})()''' % (json.dumps(bool(value)), json.dumps(value))
        self.panel.preview.page().runJavaScript(script, lambda changed: self.status.setText(self.panel.text('Блокировка изменена.', 'Lock updated.')) if changed else None)

    def duplicate_selected(self):
        script = '''(() => {const selector=window.__scsSelectedSelector,e=document.querySelector(selector);if(!e||e===document.body||['all'].includes((window.__scsEditorLocks||{})[selector]))return null;const clone=e.cloneNode(true),used=new Set(Array.from(document.querySelectorAll('[id]')).map(n=>n.id)),map={};
          for(const node of [clone,...clone.querySelectorAll('[id]')]){if(!node.id)continue;const old=node.id;let base=old.replace(/-copy(?:-\\d+)?$/,'')+'-copy',id=base,n=2;while(used.has(id))id=base+'-'+n++;used.add(id);map[old]=id;node.id=id;}
          for(const node of [clone,...clone.querySelectorAll('*')])for(const name of ['for','aria-labelledby','aria-describedby']){const value=node.getAttribute&&node.getAttribute(name);if(value)node.setAttribute(name,value.split(/\\s+/).map(v=>map[v]||v).join(' '));}
          e.parentNode.insertBefore(clone,e.nextSibling);return '<!doctype html>\\n'+document.documentElement.outerHTML;})()'''
        self.panel.preview.page().runJavaScript(script, lambda html: self.panel.replace_source(format_html(html)) if html else None)

    def copy_selected_element(self, kind):
        script = '''(() => {const e=document.querySelector(window.__scsSelectedSelector);if(!e)return null;const styles={};for(const key of e.style){if(key!=='outline'&&key!=='outline-offset')styles[key]=e.style.getPropertyValue(key);}return {html:e.outerHTML,css:Object.entries(styles).map(([k,v])=>k+': '+v+';').join(' ')};})()'''
        def copied(data):
            if not data: return
            if kind == 'html': value=data['html']
            elif kind == 'css': value=data['css']
            else: value='<!-- Component -->\n'+data['html']+('\n<style>\n.component {'+data['css']+'}\n</style>' if data['css'] else '')
            QApplication.clipboard().setText(value); self.status.setText(self.panel.text('Элемент скопирован в буфер обмена.', 'Element copied to the clipboard.'))
        self.panel.preview.page().runJavaScript(script, copied)

    def copy_selected_styles(self):
        script = '''(() => {const e=document.querySelector(window.__scsSelectedSelector);if(!e)return null;const out={};for(const key of e.style){if(key!=='outline'&&key!=='outline-offset')out[key]=e.style.getPropertyValue(key);}return out;})()'''
        def copied(values):
            if values is not None: self.copied_styles=dict(values); self.status.setText(self.panel.text('Стили элемента скопированы. Выберите другой элемент и нажмите Alt+V.', 'Element styles copied. Select another element and press Alt+V.'))
        self.panel.preview.page().runJavaScript(script, copied)

    def copy_editor_selection(self):
        """Copy both the selected element and its inline styles."""
        script = '''(() => {const e=document.querySelector(window.__scsSelectedSelector);if(!e)return null;const styles={};for(const key of e.style){if(key!=='outline'&&key!=='outline-offset')styles[key]=e.style.getPropertyValue(key);}const clone=e.cloneNode(true);clone.removeAttribute('data-scs-editor-selected');clone.style.outline='';clone.style.outlineOffset='';return {html:clone.outerHTML,styles};})()'''
        def copied(data):
            if not data: return
            self.copied_element_html = str(data.get('html') or '')
            self.copied_styles = dict(data.get('styles') or {})
            QApplication.clipboard().setText(self.copied_element_html)
            self.status.setText(self.panel.text('Элемент скопирован. Ctrl+V — вставить, Alt+V — применить стили.', 'Element copied. Ctrl+V pastes it; Alt+V applies its styles.'))
        self.panel.preview.page().runJavaScript(script, copied)

    def paste_copied_element(self):
        fragment = getattr(self, 'copied_element_html', '')
        if not fragment:
            clipboard = QApplication.clipboard().text().strip()
            fragment = clipboard if clipboard.startswith('<') else ''
        if not fragment: return
        script = '''(() => {const current=document.querySelector(window.__scsSelectedSelector);if(!current||current===document.documentElement)return null;const template=document.createElement('template');template.innerHTML=%s;const clone=template.content.firstElementChild;if(!clone)return null;const used=new Set(Array.from(document.querySelectorAll('[id]')).map(node=>node.id)),map={};for(const node of [clone,...clone.querySelectorAll('[id]')]){if(!node.id)continue;const old=node.id;let base=old.replace(/-copy(?:-\\d+)?$/,'')+'-copy',id=base,n=2;while(used.has(id))id=base+'-'+n++;used.add(id);map[old]=id;node.id=id;}for(const node of [clone,...clone.querySelectorAll('*')])for(const name of ['for','aria-labelledby','aria-describedby']){const value=node.getAttribute&&node.getAttribute(name);if(value)node.setAttribute(name,value.split(/\\s+/).map(v=>map[v]||v).join(' '));}current.parentNode.insertBefore(clone,current.nextSibling);return '<!doctype html>\\n'+document.documentElement.outerHTML;})()''' % json.dumps(fragment)
        self.panel.preview.page().runJavaScript(script, lambda html: self.panel.replace_source(format_html(html)) if html else None)

    def paste_selected_styles(self):
        if not self.copied_styles: return
        script = '''(() => {const selectors=window.__scsSelectedSelectors||[window.__scsSelectedSelector],locks=window.__scsEditorLocks||{};let changed=0;for(const selector of selectors){const e=selector&&document.querySelector(selector);if(!e||['all','style'].includes(locks[selector]))continue;for(const [key,value] of Object.entries(%s))e.style.setProperty(key,value);changed++;}return changed?'<!doctype html>\\n'+document.documentElement.outerHTML:null;})()''' % json.dumps(self.copied_styles)
        self.panel.preview.page().runJavaScript(script, lambda html: self.panel.replace_source(format_html(html)) if html else None)

    def toggle_grid(self):
        self.panel.preview.page().runJavaScript('''(() => {let e=document.getElementById('scs-editor-grid');if(e){e.remove();return false;}e=document.createElement('div');e.id='scs-editor-grid';e.style.cssText='position:fixed;inset:0;pointer-events:none;z-index:2147483000;background-image:linear-gradient(rgba(238,107,47,.13) 1px,transparent 1px),linear-gradient(90deg,rgba(238,107,47,.13) 1px,transparent 1px);background-size:16px 16px';document.documentElement.appendChild(e);return true;})()''')

    def choose_rule(self, kind):
        script = '''(() => {const kind=%s, selected=document.querySelector(window.__scsSelectedSelector), out=[];
          for(const sheet of document.styleSheets){try{for(const rule of sheet.cssRules||[]){if(rule.type!==CSSRule.STYLE_RULE)continue;const selector=rule.selectorText;
            if((kind==='class'&&selected&&Array.from(selected.classList).some(c=>selector.split(',').map(x=>x.trim()).includes('.'+c))) ||
               (kind==='global'&&selector.split(',').some(x=>['html','body',':root','*'].includes(x.trim()))) ) out.push(selector);}}catch(_){}}
          if(kind==='class'&&selected)Array.from(selected.classList).forEach(c=>{if(!out.includes('.'+c))out.push('.'+c)});
          return [...new Set(out)].sort();})()''' % json.dumps(kind)
        def choose(rules):
            if not rules:
                QMessageBox.information(self.panel, self.panel.text('Стили', 'Styles'), self.panel.text('У выбранного элемента нет классов.' if kind == 'class' else 'В проекте нет глобальных CSS-правил.', 'The selected element has no classes.' if kind == 'class' else 'There are no global CSS rules in the project.'))
                return
            selector, ok = QInputDialog.getItem(self.panel, self.panel.text('Выберите правило CSS', 'Choose CSS rule'), self.panel.text('Правило', 'Rule'), rules, 0, False)
            if not ok: return
            self.open_rule(selector)
        self.panel.preview.page().runJavaScript(script, choose)

    def open_rule(self, selector):
        script = '''(() => {const selector=%s, styles={};
          for(const sheet of document.styleSheets){try{for(const rule of sheet.cssRules||[]){if(rule.type===CSSRule.STYLE_RULE&&rule.selectorText===selector)for(const key of rule.style)styles[key]=rule.style.getPropertyValue(key);}}catch(_){}}
          return styles;})()''' % json.dumps(selector)
        self.panel.preview.page().runJavaScript(script, lambda styles: RuleSettings(self, selector, styles or {}).exec_())

    def tab_changed(self, index):
        if index == 1: self.reload_assets()
        elif index == 2: self.reload_classes()
        elif index == 3:
            self.refresh_logic_options(); self.refresh_theme_targets()
        elif index == 4: self.reload_code_files(); self.refresh_logic_options()
        elif index == 5: self.reload_dom()

    def reload_code_files(self):
        previous = self.code_files.currentData() if hasattr(self, 'code_files') else None; self.code_files.blockSignals(True); self.code_files.clear()
        for path in sorted(self.panel.work_dir.rglob('*')):
            if path.is_file() and path.suffix.lower() in ('.py', '.js', '.mjs'): self.code_files.addItem(path.relative_to(self.panel.work_dir).as_posix(), str(path))
        index = self.code_files.findData(previous); self.code_files.setCurrentIndex(index if index >= 0 else (0 if self.code_files.count() else -1)); self.code_files.blockSignals(False); self.open_code_file()
    def open_code_file(self, *_):
        self.save_code_file(); value = self.code_files.currentData(); self.code_path = Path(value) if value else None; self.code_editor.blockSignals(True)
        try: self.code_editor.setPlainText(self.code_path.read_text(encoding='utf-8') if self.code_path else ''); self.code_editor.setReadOnly(self.code_path is None); self.code_highlighter.set_mode(self.code_path.name if self.code_path else '')
        except (OSError, UnicodeError): self.code_editor.clear(); self.code_editor.setReadOnly(True)
        self.code_editor.blockSignals(False)
    def schedule_code_save(self):
        if self.code_path and not self.code_editor.isReadOnly(): self.code_save_timer.start()
    def save_code_file(self):
        if not getattr(self, 'code_path', None) or self.code_editor.isReadOnly(): return
        try: self.code_path.write_text(self.code_editor.toPlainText(), encoding='utf-8'); self.panel.save_html()
        except OSError as error: self.status.setText(str(error))
    def jump_to_action(self, index):
        action = self.code_actions.itemData(index)
        if not action: return
        pattern = re.compile(r'["\']' + re.escape(str(action)) + r'["\']')
        for file_index in range(self.code_files.count()):
            path = Path(self.code_files.itemData(file_index))
            try: source = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError): continue
            match = pattern.search(source)
            if not match: continue
            self.code_files.setCurrentIndex(file_index); cursor = self.code_editor.textCursor(); cursor.setPosition(match.start()); cursor.setPosition(match.end(), QTextCursor.KeepAnchor); self.code_editor.setTextCursor(cursor); self.code_editor.centerCursor(); return

    def reload_dom(self):
        if not hasattr(self, 'dom_tree'): return
        parser = _PreviewParser()
        try: parser.feed(self.panel.source.toPlainText()); parser.close()
        except Exception: return
        self.dom_tree.clear()
        def add(parent, node, ancestors):
            siblings = parent.childCount() if parent else self.dom_tree.topLevelItemCount(); label = node.tag
            if node.attrs.get('id'): label += '#' + node.attrs['id']
            classes = node.attrs.get('class', '').split()
            if classes: label += ''.join('.' + value for value in classes[:3])
            if node.attrs.get('id'): selector = '#' + node.attrs['id']
            else:
                same = [child for child in (ancestors[-1][0].children if ancestors else parser.root.children) if child.tag == node.tag]; position = same.index(node) + 1; selector = ' > '.join([value[1] for value in ancestors] + [f'{node.tag}:nth-of-type({position})'])
            item = QTreeWidgetItem([label]); item.setData(0, Qt.UserRole, selector)
            if parent: parent.addChild(item)
            else: self.dom_tree.addTopLevelItem(item)
            for child in node.children: add(item, child, ancestors + [(node, selector.split(' > ')[-1])])
        for node in parser.root.children: add(None, node, [])
        self.dom_tree.expandToDepth(2); self.filter_dom(self.dom_search.text())
    def filter_dom(self, value):
        words = value.casefold().split()
        def visit(item):
            child_match = any(visit(item.child(index)) for index in range(item.childCount())); own = all(word in item.text(0).casefold() for word in words); visible = not words or own or child_match; item.setHidden(not visible); return visible
        for index in range(self.dom_tree.topLevelItemCount()): visit(self.dom_tree.topLevelItem(index))
    def select_dom_item(self, item, _column=0):
        selector = item.data(0, Qt.UserRole); self.last_selection = selector; self.selected_selectors = [selector]; self.selector.setText(selector); self.dom_breadcrumb.setText(selector.replace(':nth-of-type(1)', '')); self.highlight_source_selector(selector); self.panel.preview.page().runJavaScript("window.__scsSelectedSelector=%s;window.__scsSelectedSelectors=[%s];document.querySelectorAll('[data-scs-editor-selected]').forEach(e=>{e.removeAttribute('data-scs-editor-selected');e.style.outline='';});const e=document.querySelector(%s);if(e){e.setAttribute('data-scs-editor-selected','');e.style.outline='2px dashed #ee6b2f';e.scrollIntoView({behavior:'smooth',block:'center'});}" % (json.dumps(selector), json.dumps(selector), json.dumps(selector)))

    def ensure_bridge(self, kind):
        marker = '<!-- LANGUAGE_BRIDGE -->' if kind == 'language' else '<!-- THEME_BRIDGE -->'
        if marker not in self.panel.source.toPlainText():
            (self.panel.enable_language_bridge if kind == 'language' else self.panel.enable_theme_bridge)()

    def translation_data(self, html):
        match = re.search(r'<script\b[^>]*id=["\']scs-language-translations["\'][^>]*>(.*?)</script\s*>', html, re.I | re.S)
        if not match: return {}, None
        try: return json.loads(match.group(1).strip() or '{}'), match
        except (TypeError, ValueError): return {}, match

    def write_translation_data(self, html, values):
        payload = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
        block = '''<!-- LANGUAGE_TRANSLATIONS -->
<script type="application/json" id="scs-language-translations">%s</script>
<script>
window.scsTranslations = Object.assign(window.scsTranslations || {}, JSON.parse(document.getElementById('scs-language-translations').textContent));
if (window.setLanguage) window.setLanguage(document.documentElement.lang || 'ru');
</script>''' % payload
        pattern = re.compile(r'<!--\s*LANGUAGE_TRANSLATIONS\s*-->\s*<script\b[^>]*id=["\']scs-language-translations["\'][^>]*>.*?</script\s*>\s*<script\b[^>]*>.*?</script\s*>', re.I | re.S)
        if pattern.search(html): return pattern.sub(block, html, count=1)
        closing = re.search(r'</body\s*>', html, re.I)
        return html[:closing.start()].rstrip() + '\n' + block + '\n' + html[closing.start():] if closing else html.rstrip() + '\n' + block

    def apply_language_replacement(self):
        source, target = self.translation_source.text(), self.translation_target.text()
        if not source:
            QMessageBox.warning(self.panel, self.panel.text('Перевод', 'Translation'), self.panel.text('Введите текст, который нужно заменить.', 'Enter the text to replace.'))
            return
        self.ensure_bridge('language')
        html = self.panel.source.toPlainText(); language = self.translation_language.currentData()
        key = None
        escaped = re.escape(source)
        pattern = re.compile(r'(<(?P<tag>[A-Za-z][\w:-]*)(?P<attrs>[^>]*)>)(?P<text>\s*' + escaped + r'\s*)(</(?P=tag)\s*>)', re.I)
        def bind(match):
            nonlocal key
            attrs = match.group('attrs')
            existing = re.search(r'\bdata-scs-i18n\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            item_key = existing.group(1) if existing else key or ('text_' + uuid.uuid4().hex[:10])
            key = key or item_key
            if not existing: attrs += ' data-scs-i18n="%s"' % item_key
            return '<%s%s>%s</%s>' % (match.group('tag'), attrs, match.group('text'), match.group('tag'))
        html, count = pattern.subn(bind, html)
        if not count:
            QMessageBox.warning(self.panel, self.panel.text('Перевод', 'Translation'), self.panel.text('Не найден простой текстовый элемент с таким содержимым. Выберите текст элемента без вложенных тегов.', 'No simple text element with this content was found. Choose text from an element without nested tags.'))
            return
        translations, _ = self.translation_data(html); translations.setdefault(language, {})[key] = target
        html = self.write_translation_data(html, translations)
        self.panel.replace_source(format_html(html)); self.translation_target.clear(); self.panel.preview_timer.start(10)

    def open_translation_table(self):
        html = self.panel.source.toPlainText(); translations, _match = self.translation_data(html); sources = {}
        pattern = re.compile(r'<([A-Za-z][\w:-]*)\b([^>]*\bdata-scs-i18n\s*=\s*["\']([^"\']+)["\'][^>]*)>(.*?)</\1\s*>', re.I | re.S)
        for match in pattern.finditer(html): sources[match.group(3)] = re.sub(r'<[^>]+>', '', match.group(4)).strip()
        keys = sorted(set(sources) | {str(key) for values in translations.values() if isinstance(values, dict) for key in values})
        dialog = QDialog(self.panel); dialog.setWindowTitle(self.panel.text('Таблица переводов', 'Translation table')); dialog.resize(860, 560); layout = QVBoxLayout(dialog); table = QTableWidget(len(keys), 4); table.setHorizontalHeaderLabels([self.panel.text('Ключ', 'Key'), self.panel.text('Исходный текст', 'Source text'), 'Русский', 'English']); table.horizontalHeader().setStretchLastSection(True)
        for row, key in enumerate(keys):
            table.setItem(row, 0, QTableWidgetItem(key)); table.item(row, 0).setFlags(table.item(row, 0).flags() & ~Qt.ItemIsEditable); table.setItem(row, 1, QTableWidgetItem(sources.get(key, ''))); table.item(row, 1).setFlags(table.item(row, 1).flags() & ~Qt.ItemIsEditable)
            for column, language in ((2, 'ru'), (3, 'en')):
                value = translations.get(language, {}).get(key, '') if isinstance(translations.get(language), dict) else ''; item = QTableWidgetItem(str(value)); table.setItem(row, column, item)
                if not value: item.setBackground(QBrush(QColor('#55252b'))); item.setToolTip(self.panel.text('Перевод не заполнен', 'Translation is missing'))
        layout.addWidget(table, 1); buttons = QHBoxLayout(); cancel = QPushButton(self.panel.text('Отмена', 'Cancel')); save = QPushButton(self.panel.text('Применить', 'Apply')); save.setObjectName('primaryButton'); cancel.clicked.connect(dialog.reject)
        def apply():
            values = {language: dict(translations.get(language, {})) if isinstance(translations.get(language), dict) else {} for language in ('ru', 'en')}
            for row, key in enumerate(keys):
                for column, language in ((2, 'ru'), (3, 'en')):
                    value = table.item(row, column).text().strip()
                    if value: values[language][key] = value
                    else: values[language].pop(key, None)
            merged = dict(translations); merged.update(values); self.ensure_bridge('language'); self.panel.replace_source(format_html(self.write_translation_data(self.panel.source.toPlainText(), merged))); dialog.accept()
        save.clicked.connect(apply); buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(save); layout.addLayout(buttons); dialog.exec_()

    def refresh_theme_targets(self, *_):
        if not hasattr(self, 'theme_target'): return
        kind = self.theme_target_kind.currentData() or 'element'; current = self.theme_target.currentData()
        html = self.panel.source.toPlainText(); choices = []
        if kind == 'element':
            selector = self.last_selection or self.selector.text().strip() or 'body'; choices = [(selector, selector)]
        elif kind == 'class':
            classes = sorted(set(re.findall(r'\bclass\s*=\s*["\']([^"\']*)["\']', html, re.I)))
            choices = [(f'.{name}', f'.{name}') for value in classes for name in value.split()]
        else:
            choices = [(name.lower(), name.lower()) for name in sorted(set(re.findall(r'<\s*([A-Za-z][\w-]*)\b', html))) if name.lower() not in ('html', 'head', 'style', 'script', 'meta', 'title')]
        choices = list(dict.fromkeys(choices))
        self.theme_target.blockSignals(True); self.theme_target.clear()
        for label, value in choices: self.theme_target.addItem(label, value)
        index = self.theme_target.findData(current)
        self.theme_target.setCurrentIndex(index if index >= 0 else 0); self.theme_target.blockSignals(False)

    def configure_theme_rule(self):
        self.ensure_bridge('theme'); self.refresh_theme_targets()
        target = self.theme_target.currentData()
        if not target:
            QMessageBox.warning(self.panel, self.panel.text('Тема', 'Theme'), self.panel.text('Нет подходящего элемента для настройки.', 'There is no matching element to configure.'))
            return
        if self.theme_target_kind.currentData() == 'element' and self.last_selection:
            target = self.last_selection
        theme = self.theme_name.currentData()
        # Selected-element paths begin with ``html:nth-of-type(...)``.  A
        # simple prefix would produce ``html[data-theme] html:nth...`` — an
        # impossible descendant selector.  Merge the theme attribute into the
        # first html selector instead.
        selector = themed_selector(target, theme)
        # The main editor preview always reflects the application's currently
        # selected theme.  Only this dialog's private preview is switched to
        # the theme being edited.
        # The Configure preview is rendered with native Qt controls.  Supply
        # the computed base values of the selected DOM element, then let the
        # theme-rule values override them.  This keeps its appearance equal to
        # the ordinary Configure dialog (including border radius, padding and
        # inherited colours), rather than showing a stripped-down theme-only
        # version.
        preview_properties = json.dumps([prop for prop, _, _ in COMMON_CSS])
        script = '''(() => {
            const node = document.querySelector(%s);
            if (!node) return null;
            const computed = getComputedStyle(node), base_styles = {};
            %s.forEach(property => base_styles[property] = computed.getPropertyValue(property));
            return {outer_html: node.outerHTML,
                    style_sheets: Array.from(document.querySelectorAll('style')).map(style => style.textContent).join('\\n'),
                    base_styles};
        })()''' % (json.dumps(target), preview_properties)
        self.panel.preview.page().runJavaScript(script, lambda data: RuleSettings(
            self, selector, self.class_rule_values(selector),
            (data or {}).get('outer_html', ''), target,
            (data or {}).get('style_sheets', ''),
            (data or {}).get('base_styles', {})
        ).exec_())

    def source_changed(self):
        if self.tabs.currentIndex() == 1: self.reload_assets()
        elif self.tabs.currentIndex() == 2: self.reload_classes()
        elif self.tabs.currentIndex() == 5: self.reload_dom()

    def reload_classes(self):
        selected = self.class_list.currentItem().text() if self.class_list.currentItem() else ''
        html = self.panel.source.toPlainText(); selectors = set()
        for value in re.findall(r'\bclass\s*=\s*["\']([^"\']*)["\']', html, re.I): selectors.update('.' + name for name in value.split())
        selectors.update(tag.lower() for tag in re.findall(r'<\s*([A-Za-z][\w-]*)\b', html) if tag.lower() not in ('html', 'head', 'style', 'script', 'meta', 'title'))
        for css in re.findall(r'<style\b[^>]*>(.*?)</style\s*>', html, re.I | re.S):
            selectors.update(selector.strip() for selector in re.findall(r'(?m)^\s*([^@{}][^{}]*)\s*\{[^{}]*\}', css) if selector.strip())
        self.class_list.clear(); self.class_list.addItems(sorted(selectors))
        matches = self.class_list.findItems(selected, Qt.MatchExactly)
        if matches: self.class_list.setCurrentItem(matches[0])

    def choose_class_color(self, field):
        color = QColorDialog.getColor(QColor(field.text()), self, self.panel.text('Выбор цвета', 'Choose color'))
        if color.isValid(): field.setText(color.name())

    def class_rule_values(self, selector):
        pattern = re.compile(r'(?m)^\s*' + re.escape(selector) + r'\s*\{([^{}]*)\}')
        match = pattern.search(self.panel.source.toPlainText())
        if not match: return {}
        return {key.strip(): value.strip() for key, value in re.findall(r'([\w-]+)\s*:\s*([^;{}]+)', match.group(1))}

    def load_class_rule(self, selector):
        values = self.class_rule_values(selector) if selector else {}
        self.class_rule_title.setText((self.panel.text('Редактор селектора: ', 'Selector editor: ') + selector) if selector else self.panel.text('Выберите селектор из списка.', 'Select a selector from the list.'))
        for prop, field in self.class_properties.items():
            value = values.get(prop, '')
            if isinstance(field, QComboBox):
                index = field.findData(value)
                if index < 0 and value: field.addItem(value, value); index = field.count() - 1
                field.setCurrentIndex(index if index >= 0 else 0)
            else: field.setText(value)
        names = list(dict.fromkeys((*CSS_PROPERTY_CATALOG, *(key for key in values if key not in CSS_PROPERTY_CATALOG))))
        self.class_advanced.blockSignals(True); self.class_advanced.setRowCount(len(names))
        for row, key in enumerate(names):
            self.class_advanced.setItem(row, 0, QTableWidgetItem(key)); self.class_advanced.setItem(row, 1, QTableWidgetItem(values.get(key, '')))
        self.class_advanced.blockSignals(False)

    def read_element_classes(self):
        selector = self.last_selection
        if not selector: return
        script = '''(() => {const e=document.querySelector(%s);return e ? e.getAttribute('class') || '' : null;})()''' % json.dumps(selector)
        def done(classes):
            if classes is not None:
                self.class_selector.setText(selector); self.element_classes.setText(classes)
        self.panel.preview.page().runJavaScript(script, done)

    def save_element_classes(self):
        selector = self.class_selector.text().strip()
        if not selector:
            self.status.setText(self.panel.text('Выберите объект в предпросмотре.', 'Select an element in the preview first.')); return
        classes = ' '.join(self.element_classes.text().split())
        script = '''(() => {const e=document.querySelector(%s);if(!e)return null;if(%s)e.setAttribute('class',%s);else e.removeAttribute('class');return '<!doctype html>\\n'+document.documentElement.outerHTML;})()''' % (json.dumps(selector), json.dumps(classes), json.dumps(classes))
        def done(html):
            if html: self.panel.replace_source(format_html(html)); self.reload_classes()
        self.panel.preview.page().runJavaScript(script, done)

    def new_class(self):
        name, ok = QInputDialog.getText(self.panel, self.panel.text('Новый класс', 'New class'), self.panel.text('Имя класса:', 'Class name:'))
        name = name.strip().lstrip('.')
        if ok and re.fullmatch(r'[A-Za-z_-][\w-]*', name):
            if not self.update_css_rule('.' + name, {}, replace=True): return
            self.reload_classes(); matches = self.class_list.findItems('.' + name, Qt.MatchExactly)
            if matches: self.class_list.setCurrentItem(matches[0])
            self.status.setText(self.panel.text('Класс создан. Назначьте его нужному объекту отдельно.', 'Class created. Assign it to an object when needed.'))

    def save_class_rule(self):
        item = self.class_list.currentItem()
        if not item:
            self.status.setText(self.panel.text('Сначала выберите или создайте класс.', 'Select or create a class first.')); return
        values = {}
        for prop, field in self.class_properties.items():
            value = field.text().strip()
            if value: values[prop] = value
        for row in range(self.class_advanced.rowCount()):
            key = self.class_advanced.item(row, 0); value = self.class_advanced.item(row, 1)
            if key and key.text().strip() and value and value.text().strip(): values[key.text().strip()] = value.text().strip()
        if self.update_css_rule(item.text(), values, replace=True): self.status.setText(self.panel.text('Правило класса обновлено.', 'Class rule updated.'))

    def remove_class_rule(self):
        item = self.class_list.currentItem()
        if not item: return
        selector = item.text()
        pattern = re.compile(r'<style\b[^>]*>.*?</style\s*>', re.I | re.S)
        html = self.panel.source.toPlainText()
        updated = pattern.sub(lambda match: re.sub(r'(?s)(?:^|\n)\s*' + re.escape(selector) + r'\s*\{[^{}]*\}\s*', '\n', match.group(0)), html)
        if updated != html: self.panel.replace_source(updated)
        self.reload_classes()

    def install_selection(self, ok):
        if not ok: return
        self.panel.preview.page().runJavaScript('''
        function clearEditorSelection() {
            document.querySelectorAll('[data-scs-editor-selected]').forEach(node=>{node.style.outline='';node.style.outlineOffset='';node.removeAttribute('data-scs-editor-selected');});
            window.__scsSelectedSelector=null;window.__scsSelectedSelectors=[];
        }
        function selectElement(e, toggle=true) {
            if(window.__scsMoving)return;
            if(!document.body || !document.body.contains(e.target)) { clearEditorSelection(); return false; }
            const current=window.__scsSelectedSelector&&document.querySelector(window.__scsSelectedSelector);
            if(toggle && !e.shiftKey && (e.target.hasAttribute('data-scs-editor-selected') || current===e.target)) {
                clearEditorSelection(); return false;
            }
            // Controls must stay usable in the live preview.  Non-interactive
            // elements are still intercepted so selecting layout nodes cannot
            // accidentally trigger page behaviour.
            const interactive=e.target.closest('button,input,select,textarea,option,label,[data-scs-action]');
            if(!interactive){e.preventDefault();e.stopPropagation();}
            let element=e.target, parts=[];
            while(element && element.nodeType===1) {
                let index=1, previous=element.previousElementSibling;
                while(previous){if(previous.tagName===element.tagName)index++; previous=previous.previousElementSibling;}
                parts.unshift(element.tagName.toLowerCase()+':nth-of-type('+index+')');
                element=element.parentElement;
            }
            const selector=parts.join(' > ');window.__scsSelectedSelectors=window.__scsSelectedSelectors||[];
            if(e.shiftKey){const old=window.__scsSelectedSelectors.indexOf(selector);if(old>=0){window.__scsSelectedSelectors.splice(old,1);e.target.style.outline='';e.target.style.outlineOffset='';e.target.removeAttribute('data-scs-editor-selected');window.__scsSelectedSelector=window.__scsSelectedSelectors.at(-1)||null;return false;}window.__scsSelectedSelectors.push(selector);}
            else{document.querySelectorAll('[data-scs-editor-selected]').forEach(node=>{node.style.outline='';node.style.outlineOffset='';node.removeAttribute('data-scs-editor-selected');});window.__scsSelectedSelectors=[selector];}
            window.__scsSelectedSelector=selector;
            e.target.setAttribute('data-scs-editor-selected','');e.target.style.outline=e.shiftKey?'2px dashed #ffb36b':'2px dashed #ee6b2f';e.target.style.outlineOffset='3px';
        }
        document.addEventListener('click', selectElement, true);
        document.addEventListener('contextmenu', e=>{
          const selected=window.__scsSelectedSelectors||[],target=e.target;
          let element=target,parts=[];
          while(element&&element.nodeType===1){let index=1,previous=element.previousElementSibling;while(previous){if(previous.tagName===element.tagName)index++;previous=previous.previousElementSibling;}parts.unshift(element.tagName.toLowerCase()+':nth-of-type('+index+')');element=element.parentElement;}
          const selector=parts.join(' > ');
          // Opening a menu for one member of a group must not destroy the
          // selection.  Only an unselected target starts a new selection.
          if(selected.includes(selector)){window.__scsSelectedSelector=selector;window.__scsContextPending=true;return;}
          if(selectElement(e, false)!==false) window.__scsContextPending=true;
        }, true);
        let pan=null;
        document.addEventListener('mousedown',e=>{if(e.button===1){e.preventDefault();pan=[e.clientX,e.clientY];}},true);
        document.addEventListener('mousemove',e=>{if(pan){window.scrollBy(pan[0]-e.clientX,pan[1]-e.clientY);pan=[e.clientX,e.clientY];}},true);
        document.addEventListener('mouseup',()=>pan=null,true);
        document.addEventListener('wheel',e=>{if(e.ctrlKey){e.preventDefault();
        window.__scsZoomDelta=(window.__scsZoomDelta||0)+(e.deltaY<0?.1:-.1);}}, {passive:false});
        ''', lambda _=None: self.highlight_selected())

    def highlight_selected(self):
        if not self.pending_highlight or not self.last_selection: return
        self.pending_highlight = False
        script = '''(() => {const selector=%s,e=document.querySelector(selector);if(!e)return;window.__scsSelectedSelector=selector;window.__scsSelectedSelectors=[selector];
          e.setAttribute('data-scs-editor-selected','');e.style.outline='2px dashed #ee6b2f';e.style.outlineOffset='3px';})()''' % json.dumps(self.last_selection)
        self.panel.preview.page().runJavaScript(script)

    def highlight_source_selector(self, selector):
        """Select the same opening tag in the source editor as in the preview."""
        source = self.panel.source.toPlainText()
        parser = SelectorPositionParser(selector)
        try: parser.feed(source); parser.close()
        except Exception: return
        if parser.position is None or not parser.length: return
        line, column = parser.position
        lines = source.splitlines(keepends=True)
        if line < 1 or line > len(lines): return
        start = sum(len(value) for value in lines[:line - 1]) + column
        cursor = self.panel.source.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(start + parser.length, QTextCursor.KeepAnchor)
        self.panel.source.setTextCursor(cursor)
        self.panel.source.ensureCursorVisible()

    def read_selection(self):
        if not self.panel.isVisible(): return
        def selected(value):
            if not value: return
            self.selected_selectors = list(value.get('selectors') or [])
            if value.get('zoom'):
                self.panel.preview.setZoomFactor(min(3.0,max(.25,self.panel.preview.zoomFactor()+value['zoom'])))
            if value.get('html'): self.panel.replace_source(format_html(value['html'])); return
            selector=value.get('selector')
            if not selector:
                self.last_selection = None; self.selected_selectors = []; self.selector.clear(); self.class_selector.clear(); self.element_classes.clear()
                return
            if selector and selector != self.last_selection:
                self.last_selection = selector; self.selector.setText(selector); self.highlight_source_selector(selector); self.read_element_classes(); self.read_action_binding(); self.refresh_theme_targets()
            if value.get('context'): self.show_element_menu(self.panel.preview.mapFromGlobal(QCursor.pos()))
        self.panel.preview.page().runJavaScript('(() => {const result={selector:window.__scsSelectedSelector,selectors:window.__scsSelectedSelectors||[],context:!!window.__scsContextPending,html:window.__scsMovedHTML,zoom:window.__scsZoomDelta};window.__scsMovedHTML=null;window.__scsContextPending=false;window.__scsZoomDelta=0;return result;})()', selected)

    def start_move(self):
        self.panel.preview_timer.stop()
        labels=json.dumps([self.panel.text('Перетащите объект мышью','Drag the element'),self.panel.text('Применить','Apply'),self.panel.text('Отмена','Cancel')])
        script='''(() => {
        if(window.__scsMoving)return;
        const selector=window.__scsSelectedSelector,e=document.querySelector(selector),lock=(window.__scsEditorLocks||{})[selector]||'';if(!e||e===document.body||e===document.documentElement||['all','move'].includes(lock))return;
        const lockX=lock==='move-x',lockY=lock==='move-y',original=e.getAttribute('style'), labels=%s, editorLanguage=%s; window.__scsMoving=true;
        let x=0,y=0,drag=null,angle=0,scale=1,resize=null;
        const transform=getComputedStyle(e).transform;
        e.style.outline='2px dashed #ee6b2f'; e.style.cursor='move';if(getComputedStyle(e).position==='static')e.style.position='relative';
        const bar=document.createElement('div');bar.style.cssText='position:fixed;bottom:16px;left:16px;z-index:2147483647;padding:14px;background:#191d25;color:white;border-radius:12px;font:14px sans-serif';
        const hint=document.createElement('span');hint.textContent=labels[0];bar.appendChild(hint);
        function paint(){e.style.transform='translate('+x+'px,'+y+'px) rotate('+angle+'deg) scale('+scale+') '+(transform==='none'?'':transform);if(typeof placeHandles==='function')requestAnimationFrame(placeHandles);}
        let sliderNumber=0;function slider(title,min,max,value,step,apply){const translated=[editorLanguage==='ru'?'Размер':'Size',editorLanguage==='ru'?'Поворот':'Rotation'];title=translated[sliderNumber++]||title;const label=document.createElement('label');label.textContent=' '+title+' ';label.style.cssText='margin-left:16px;padding:3px 7px;border:1px solid #ee6b2f;border-radius:7px;color:#fff;font-weight:600';const input=document.createElement('input');input.type='range';input.min=min;input.max=max;input.value=value;input.step=step;input.style.cssText='width:130px;vertical-align:middle';input.oninput=()=>apply(+input.value);label.appendChild(input);bar.appendChild(label);}
        slider('Размер',20,300,100,1,v=>{scale=v/100;paint()});
        slider('Поворот',-180,180,0,1,v=>{angle=v;paint()});
        function control(text, action){const b=document.createElement('button');b.textContent=text;b.style.marginLeft='6px';b.onclick=action;bar.appendChild(b);}
        control('←',()=>{if(!lockX){x=-e.getBoundingClientRect().left;paint()}}); control('↔',()=>{if(!lockX){x+=window.innerWidth/2-(e.getBoundingClientRect().left+e.getBoundingClientRect().width/2);paint()}}); control('→',()=>{if(!lockX){x+=window.innerWidth-e.getBoundingClientRect().right;paint()}});
        const handles=[];function handle(kind,px,py,cursor){const h=document.createElement('span');h.dataset.scsResize=kind;h._px=px;h._py=py;h.style.cssText='position:fixed;width:12px;height:12px;border-radius:50%%;background:#ee6b2f;border:2px solid #fff;z-index:2147483646;cursor:'+cursor;document.body.appendChild(h);handles.push(h)}
        function placeHandles(){const r=e.getBoundingClientRect();handles.forEach(h=>{h.style.left=(r.left+r.width*h._px-8)+'px';h.style.top=(r.top+r.height*h._py-8)+'px'})}
        handle('x:-1:0',0,.5,'ew-resize');handle('x:1:0',1,.5,'ew-resize');handle('y:0:-1',.5,0,'ns-resize');handle('y:0:1',.5,1,'ns-resize');
        handle('xy:-1:-1',0,0,'nwse-resize');handle('xy:1:-1',1,0,'nesw-resize');handle('xy:-1:1',0,1,'nesw-resize');handle('xy:1:1',1,1,'nwse-resize');placeHandles();
        function finish(commit){
            document.removeEventListener('mousedown',down,true);document.removeEventListener('mousemove',motion,true);document.removeEventListener('mouseup',up,true);
            bar.remove();handles.forEach(h=>h.remove());if(!commit){if(original===null)e.removeAttribute('style');else e.setAttribute('style',original);}else{e.style.outline='';e.style.outlineOffset='';e.removeAttribute('data-scs-editor-selected');paint();}
            window.__scsMoving=false;
            if(commit)window.__scsMovedHTML='<!doctype html>\\n'+document.documentElement.outerHTML;
        }
        [true,false].forEach((commit,i)=>{const b=document.createElement('button');b.textContent=labels[i+1];b.style.marginLeft='10px';b.onclick=()=>finish(commit);bar.appendChild(b);});document.body.appendChild(bar);
        function down(ev){if(ev.button!==0||bar.contains(ev.target))return;ev.preventDefault();const data=ev.target.dataset.scsResize;if(data){const [axis,sx,sy]=data.split(':');resize=[axis,+sx,+sy,ev.clientX,ev.clientY,e.offsetWidth,e.offsetHeight];return}if(e.contains(ev.target))drag=[ev.clientX-x,ev.clientY-y];}
        function motion(ev){if(resize){const [axis,sx,sy,startX,startY,startW,startH]=resize,dx=(ev.clientX-startX)*sx,dy=(ev.clientY-startY)*sy;if(!lockX&&(axis==='x'||axis==='xy'))e.style.width=Math.max(12,startW+dx)+'px';if(!lockY&&(axis==='y'||axis==='xy'))e.style.height=Math.max(12,startH+dy)+'px';placeHandles();hint.textContent='W: '+e.offsetWidth+'  H: '+e.offsetHeight;return}if(!drag)return;if(!lockX)x=ev.clientX-drag[0];if(!lockY)y=ev.clientY-drag[1];paint();const r=e.getBoundingClientRect(),d=8;if(!lockX&&Math.abs(r.left)<d)x-=r.left;if(!lockX&&Math.abs(r.right-window.innerWidth)<d)x+=window.innerWidth-r.right;if(!lockX&&Math.abs(r.left+r.width/2-window.innerWidth/2)<d)x+=window.innerWidth/2-(r.left+r.width/2);if(!lockY&&Math.abs(r.top)<d)y-=r.top;if(!lockY&&Math.abs(r.bottom-window.innerHeight)<d)y+=window.innerHeight-r.bottom;if(!lockY&&Math.abs(r.top+r.height/2-window.innerHeight/2)<d)y+=window.innerHeight/2-(r.top+r.height/2);paint();hint.textContent=labels[0]+'  X: '+Math.round(x)+'  Y: '+Math.round(y);}
        function up(){drag=null;resize=null;}
        document.addEventListener('mousedown',down,true);document.addEventListener('mousemove',motion,true);document.addEventListener('mouseup',up,true);
        })()''' % (labels, json.dumps(self.panel.lang_code))
        self.panel.preview.page().runJavaScript(script)

    def start_html_move(self):
        """Drag a node to change its actual parent/order in the HTML document."""
        self.panel.preview_timer.stop()
        labels = json.dumps([
            self.panel.text('Перетащите объект: верх цели — перед ней, низ — после, центр — внутрь контейнера.', 'Drag: target top inserts before, bottom after, center puts inside the container.'),
            self.panel.text('Применить', 'Apply'), self.panel.text('Отменить', 'Cancel')])
        script = '''(() => {if(window.__scsMoving)return;const selector=window.__scsSelectedSelector,e=document.querySelector(selector);if(!e||e===document.body||e===document.documentElement||['all','move'].includes((window.__scsEditorLocks||{})[selector]))return;
          const originalParent=e.parentNode, originalNext=e.nextSibling, labels=%s;window.__scsMoving=true;e.style.outline='2px dashed #ee6b2f';e.style.cursor='move';
          const bar=document.createElement('div');bar.dataset.scsMoveBar='';bar.style.cssText='position:fixed;bottom:16px;left:16px;z-index:2147483647;padding:14px;background:#191d25;color:white;border-radius:12px;font:14px sans-serif';bar.append(labels[0]);
          function selectorFor(n){let p=[];while(n&&n.nodeType===1){let i=1,q=n.previousElementSibling;while(q){if(q.tagName===n.tagName)i++;q=q.previousElementSibling}p.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');n=n.parentElement}return p.join(' > ')}
          function finish(ok){document.removeEventListener('mousedown',down,true);document.removeEventListener('mousemove',move,true);document.removeEventListener('mouseup',up,true);bar.remove();e.style.pointerEvents='';e.style.outline='';e.style.cursor='';window.__scsMoving=false;if(!ok)originalParent.insertBefore(e,originalNext);else{window.__scsSelectedSelector=selectorFor(e);window.__scsMovedHTML='<!doctype html>\\n'+document.documentElement.outerHTML;}}
          [true,false].forEach((ok,i)=>{const b=document.createElement('button');b.textContent=labels[i+1];b.style.marginLeft='10px';b.onclick=()=>finish(ok);bar.appendChild(b)});document.body.appendChild(bar);let dragging=false;
          function down(ev){if(ev.button===0&&e.contains(ev.target)&&!bar.contains(ev.target)){dragging=true;e.style.pointerEvents='none';ev.preventDefault()}}
          function place(ev){const raw=document.elementFromPoint(ev.clientX,ev.clientY);if(!raw||raw.closest('[data-scs-move-bar]'))return;const target=raw.closest('body *')||document.body;if(target===e||e.contains(target)||['SCRIPT','STYLE','META','LINK'].includes(target.tagName))return;const r=target.getBoundingClientRect();document.querySelectorAll('[data-scs-drop-target]').forEach(n=>n.removeAttribute('data-scs-drop-target'));target.setAttribute('data-scs-drop-target','');if(target===document.body){const marker=Array.from(target.childNodes).find(n=>n.nodeType===8&&['ACTIONS_BRIDGE','SCS_TOOL_BRIDGE'].includes(n.nodeValue.trim()));target.insertBefore(e,marker||null)}else if(ev.clientY<=r.top+r.height*.35)target.parentNode.insertBefore(e,target);else if(ev.clientY>=r.bottom-r.height*.35)target.parentNode.insertBefore(e,target.nextSibling);else target.appendChild(e)}
          function move(ev){if(dragging)place(ev)} function up(ev){if(!dragging)return;place(ev);dragging=false;e.style.pointerEvents=''}
          document.addEventListener('mousedown',down,true);document.addEventListener('mousemove',move,true);document.addEventListener('mouseup',up,true);})()''' % labels
        self.panel.preview.page().runJavaScript(script)

    def button(self, layout, ru, en, callback):
        button = QPushButton(); button.setObjectName('outlineButton'); button.clicked.connect(callback)
        self.labels.append((button, ru, en))
        if isinstance(layout, QFormLayout): layout.addRow(button)
        else: layout.addWidget(button)
        return button

    def translate(self):
        if hasattr(self, 'action_direction'):
            direction = self.action_direction.currentData() or 'input'; self.action_direction.blockSignals(True)
            self.action_direction.setItemText(0, self.panel.text('Ввод', 'Input')); self.action_direction.setItemText(1, self.panel.text('Вывод', 'Output'))
            self.action_direction.setCurrentIndex(0 if direction == 'input' else 1); self.action_direction.blockSignals(False)
            self.direction_label.setText(self.panel.text('Тип объекта', 'Object role')); self.action_label.setText(self.panel.text('Действие', 'Action')); self.name_label.setText(self.panel.text('ID для вывода', 'Output ID')); self.action_choices_label.setText(self.panel.text('Действие (введите или выберите)', 'Action (type or choose)')); self.id_choices_label.setText(self.panel.text('ID (введите или выберите)', 'ID (type or choose)')); self.refresh_action_choices.setText(self.panel.text('Обновить из Python / JS', 'Refresh from Python / JS')); self.update_action_direction()
            self.actions_title.setText(self.panel.text('Мосты', 'Bridges')); self.action_bridge.setText(self.panel.text('Создать Action Bridge', 'Create Action Bridge')); self.logging_bridge.setText(self.panel.text('Создать Logging Bridge', 'Create Logging Bridge')); self.responsive_button.setText(self.panel.text('Адаптация к размеру окна', 'Adapt to window size')); self.language_bridge.setText(self.panel.text('Создать Lang Bridge', 'Create Lang Bridge')); self.theme_bridge.setText(self.panel.text('Создать Theme Bridge', 'Create Theme Bridge'))
            self.density_label.setText(self.panel.text('Масштаб интерфейса', 'Interface scale')); self.density_bridge.setText(self.panel.text('Создать Density Bridge', 'Create Density Bridge'))
            self.language_tools_title.setText(self.panel.text('Переводы', 'Translations')); self.translation_language_label.setText(self.panel.text('Язык для замены', 'Language to replace')); self.translation_source_label.setText(self.panel.text('Заменить текст', 'Text to replace')); self.translation_target_label.setText(self.panel.text('Новое значение', 'Replacement text')); self.apply_translation_button.setText(self.panel.text('Добавить перевод', 'Add translation'))
            self.translation_table_button.setText(self.panel.text('Таблица переводов…', 'Translation table…'))
            self.theme_tools_title.setText(self.panel.text('Тема', 'Theme')); self.theme_name_label.setText(self.panel.text('Тема', 'Theme')); self.theme_target_kind_label.setText(self.panel.text('Настраивать', 'Configure')); self.theme_target_label.setText(self.panel.text('Цель', 'Target')); self.theme_target_kind.setItemText(0, self.panel.text('Выбранный элемент', 'Selected element')); self.theme_target_kind.setItemText(1, self.panel.text('Класс', 'Class')); self.theme_target_kind.setItemText(2, self.panel.text('Тип элемента', 'Element type')); self.configure_theme_button.setText(self.panel.text('Настроить тему…', 'Configure theme…'))
        for widget, ru, en in self.labels: widget.setText(self.panel.text(ru, en))
        labels = {prop: (ru, en) for prop, ru, en in COMMON_CSS}
        for prop, label in self.class_property_labels.items():
            ru, en = labels.get(prop, (prop, prop)); label.setText(self.panel.text(ru, en))
        for prop, options in self.class_choice_options.items():
            combo = self.class_properties[prop]; value = combo.currentData(); combo.blockSignals(True); combo.clear(); combo.addItem('—', '')
            for ru, en, css in options: combo.addItem(self.panel.text(ru, en), css)
            index = combo.findData(value)
            if index < 0 and value: combo.addItem(value, value); index = combo.count() - 1
            combo.setCurrentIndex(index if index >= 0 else 0); combo.blockSignals(False)
        for button in self.class_color_buttons.values(): button.setText(self.panel.text('Выбрать…', 'Choose…'))
        self.element_classes_label.setText(self.panel.text('Классы (через пробел)', 'Classes (space-separated)'))
        self.class_list_label.setText(self.panel.text('Классы и CSS-селекторы проекта', 'Project classes and CSS selectors'))
        self.class_advanced_label.setText(self.panel.text('Дополнительно: CSS', 'Advanced: CSS'))
        self.tabs.setTabText(2, self.panel.text('Классы и стили', 'Classes and styles'))
        self.tabs.setTabText(3, self.panel.text('Действия', 'Actions'))
        self.tabs.setTabText(4, self.panel.text('Код', 'Code')); self.tabs.setTabText(5, 'DOM')
        self.dom_search.setPlaceholderText(self.panel.text('Поиск по DOM…', 'Search DOM…'))
        self.rename_asset_button.setText(self.panel.text('Переименовать блок', 'Rename block'))
        item = self.class_list.currentItem()
        self.load_class_rule(item.text() if item else '')
        self.status.setText(self.panel.text('Классы редактируются во вкладке «Классы». Правый клик по объекту — настройка или перемещение.', 'Edit classes in the Classes tab. Right-click an element to configure or move it.'))

    def reload_assets(self):
        previous = self.files.currentData()
        self.files.blockSignals(True); self.files.clear()
        self.inline_blocks=list(re.finditer(r'<(style|script)\b([^>]*)>(.*?)</\1\s*>',self.panel.source.toPlainText(),re.I|re.S))
        for index,match in enumerate(self.inline_blocks):
            if re.search(r'\bsrc\s*=',match.group(2),re.I):continue
            named = (re.search(r'\bdata-editor-name\s*=\s*(["\'])(.*?)\1', match.group(2), re.I | re.S)
                     or re.search(r'\bname\s*=\s*(["\'])(.*?)\1', match.group(2), re.I | re.S))
            title = unescape(named.group(2)) if named else f'#{index + 1}'
            self.files.addItem(f'{match.group(1).upper()}  {title}',index)
        selected = self.files.findData(previous)
        if selected >= 0: self.files.setCurrentIndex(selected)
        self.files.blockSignals(False); self.open_asset()

    def rename_asset(self):
        index = self.files.currentData()
        if index is None or index >= len(self.inline_blocks): return
        match = self.inline_blocks[index]
        previous = re.search(r'\bdata-editor-name\s*=\s*(["\'])(.*?)\1', match.group(2), re.I | re.S)
        current = unescape(previous.group(2)) if previous else ''
        name, accepted = QInputDialog.getText(self, self.panel.text('Название блока', 'Block name'),
            self.panel.text('Название скрипта или стиля:', 'Script or style name:'), text=current)
        if not accepted: return
        opening = match.group(0)[:match.group(0).find('>') + 1]
        opening = re.sub(r'\s+(?:data-editor-name|name)\s*=\s*(["\']).*?\1', '', opening, flags=re.I | re.S)
        if name.strip(): opening = opening[:-1] + ' data-editor-name="' + escape(name.strip(), quote=True) + '">'
        cursor = self.panel.source.textCursor(); cursor.setPosition(match.start()); cursor.setPosition(match.start() + len(match.group(0)[:match.group(0).find('>') + 1]), cursor.KeepAnchor)
        cursor.insertText(opening)
        self.reload_assets(); self.files.setCurrentIndex(self.files.findData(index))

    def open_asset(self, *_):
        self.active_path = None; self.asset_editor.blockSignals(True)
        value = self.files.currentData()
        try:
            if value is not None:
                self.active_path = value; match=self.inline_blocks[value]; self.asset_editor.setPlainText(match.group(3))
                self.asset_highlighter.set_mode('inline.css' if match.group(1).lower()=='style' else 'inline.js')
            else: self.asset_editor.clear()
            self.asset_editor.setReadOnly(value is None)
        except (OSError, UnicodeError) as exc:
            self.asset_editor.setPlainText(str(exc)); self.asset_editor.setReadOnly(True); self.active_path = None
        finally: self.asset_editor.blockSignals(False)

    def save_asset(self):
        if self.active_path is not None:
            html=self.panel.source.toPlainText(); blocks=list(re.finditer(r'<(style|script)\b([^>]*)>(.*?)</\1\s*>',html,re.I|re.S))
            if self.active_path>=len(blocks):return
            match=blocks[self.active_path]; cursor=self.panel.source.textCursor(); cursor.setPosition(match.start(3)); cursor.setPosition(match.end(3),cursor.KeepAnchor); cursor.insertText(self.asset_editor.toPlainText())

    def insert_head(self, snippet):
        html = self.panel.source.toPlainText(); position = html.lower().rfind('</head>')
        if position < 0: html = snippet + '\n' + html
        else: html = html[:position] + snippet + '\n' + html[position:]
        cursor = self.panel.source.textCursor(); cursor.select(cursor.Document); cursor.insertText(html)

    def update_css_rule(self, selector, values, replace=False):
        """Update a source CSS rule in place; create it only when it is absent."""
        pattern = re.compile(r'(?m)^\s*' + re.escape(selector) + r'\s*\{[^{}]*\}')
        html = self.panel.source.toPlainText()
        existing = {}; matches = list(pattern.finditer(html))
        if matches and not replace:
            for match in matches:
                existing.update({key.strip(): value.strip() for key, value in re.findall(r'([\w-]+)\s*:\s*([^;{}]+)', match.group(0))})
        for key, value in values.items():
            if value: existing[key] = value
            else: existing.pop(key, None)
        declarations = '; '.join(f'{key}: {value}' for key, value in existing.items())
        rule = selector + ' {' + declarations + (';' if declarations else '') + '}'
        if matches:
            replacement_count = 0
            def replace_rule(_match):
                nonlocal replacement_count
                replacement_count += 1
                return rule if replacement_count == 1 else ''
            html = pattern.sub(replace_rule, html)
            html = re.sub(r'<style\b[^>]*>\s*</style\s*>', '', html, flags=re.I)
        else:
            style_blocks = list(re.finditer(r'<style\b[^>]*>.*?</style\s*>', html, re.I | re.S))
            if not style_blocks:
                self.status.setText(self.panel.text('В проекте нет блока CSS для нового правила.', 'The project has no CSS block for the new rule.'))
                return False
            block = style_blocks[-1]; close = html.lower().rfind('</style', block.start(), block.end())
            html = html[:close] + '\n' + rule + '\n' + html[close:]
        self.panel.replace_source(html)
        return True

    def new_asset(self, suffix):
        tag='style' if suffix=='css' else 'script'
        block_id = 'editor-' + uuid.uuid4().hex
        self.insert_head(f'<{tag} id="{block_id}">\n\n</{tag}>')
        self.tabs.setCurrentIndex(1); self.reload_assets()
        index = next(i for i, match in enumerate(self.inline_blocks) if block_id in match.group(2))
        self.files.setCurrentIndex(self.files.findData(index))

    def apply_css(self):
        selector = self.selector.text().strip()
        declarations = {name: edit.text().strip() for name, edit in self.properties.items() if edit.text().strip()}
        if not selector:
            self.status.setText(self.panel.text('Введите CSS-селектор.', 'Enter a CSS selector.')); return
        if self.update_css_rule(selector, declarations): self.status.setText(self.panel.text('Правило CSS обновлено.', 'CSS rule updated.'))

    def update_action_direction(self):
        is_input = self.action_direction.currentData() != 'output'
        self.action_label.setVisible(False); self.action.setVisible(False)
        self.name_label.setVisible(False); self.name.setVisible(False)
        self.action_choices_label.setVisible(is_input); self.action_choices.setVisible(is_input)
        self.id_choices_label.setVisible(not is_input); self.id_choices.setVisible(not is_input)
    def refresh_logic_options(self):
        actions, output_ids = self.panel.logic_options()
        for combo, values in ((self.action_choices, actions), (self.id_choices, output_ids)):
            current = combo.currentText(); combo.blockSignals(True); combo.clear(); combo.addItems(values)
            index = combo.findText(current)
            if index >= 0: combo.setCurrentIndex(index)
            combo.blockSignals(False)
        if hasattr(self, 'code_actions'):
            current = self.code_actions.currentData(); self.code_actions.blockSignals(True); self.code_actions.clear(); self.code_actions.addItem(self.panel.text('Перейти к action…', 'Go to action…'), '')
            for action in actions: self.code_actions.addItem(action, action)
            index = self.code_actions.findData(current); self.code_actions.setCurrentIndex(max(0, index)); self.code_actions.blockSignals(False)
        html_actions = set(re.findall(r'\bdata-scs-action\s*=\s*["\']([\w.-]+)["\']', self.panel.source.toPlainText(), re.I)); missing = sorted(html_actions - set(actions))
        self.status.setText((self.panel.text('Предупреждение: в Python/JS не найдены action: ', 'Warning: actions missing from Python/JS: ') + ', '.join(missing)) if missing else self.panel.text('Список действий и ID обновлён из логики.', 'Actions and IDs were refreshed from logic.'))
    def read_action_binding(self):
        selector = self.last_selection
        if not selector: return
        script = '''(() => {const e=document.querySelector(%s);return e?{action:e.getAttribute('data-scs-action')||'',id:e.id||''}:null})()''' % json.dumps(selector)
        def done(data):
            if not data: return
            self.selector.setText(selector)
            is_input = bool(data.get('action')) or not bool(data.get('id'))
            self.action_direction.blockSignals(True); self.action_direction.setCurrentIndex(0 if is_input else 1); self.action_direction.blockSignals(False)
            self.action.setText(data.get('action', '')); self.name.setText(data.get('id', '')); self.action_choices.setCurrentText(data.get('action', '')); self.id_choices.setCurrentText(data.get('id', '')); self.update_action_direction()
        self.panel.preview.page().runJavaScript(script, done)
    def bind(self):
        selector = self.selector.text().strip(); is_input = self.action_direction.currentData() != 'output'
        value = self.action.text().strip() if is_input else self.name.text().strip()
        if not selector or not value:
            self.status.setText(self.panel.text('Выберите объект и заполните действие или ID.', 'Select an object and fill in an action or ID.')); return
        attribute = 'data-scs-action' if is_input else 'id'
        script = '''(() => {try {const nodes=document.querySelectorAll(%s); nodes.forEach(e=>e.setAttribute(%s,%s));
        return nodes.length ? '<!doctype html>\\n'+document.documentElement.outerHTML : null;
        }catch(e){return null;}})()''' % (json.dumps(selector), json.dumps(attribute), json.dumps(value))
        def done(html):
            if html:
                self.panel.replace_source(format_html(html)); self.panel.enable_bridge()
            else: self.status.setText(self.panel.text('Элементы по селектору не найдены.', 'No elements match the selector.'))
        self.panel.preview.page().runJavaScript(script, done)

    def apply_responsive_layout(self):
        """Install an idempotent responsive layer for current and future DOM."""
        begin, end = '/* RESPONSIVE_LAYOUT_BEGIN */', '/* RESPONSIVE_LAYOUT_END */'
        responsive_css = '''%s
html, body { width: 100%%; min-height: 100%%; }
body { min-height: 100vh; max-width: 100%%; overflow-x: hidden; }
*, *::before, *::after { box-sizing: border-box; }
body * { max-width: 100%%; min-width: 0; overflow-wrap: anywhere; }
img, picture, video, canvas, svg, iframe { max-width: 100%%; height: auto; }
input, select, textarea, button { max-width: 100%%; }
[style*="display: flex"], [style*="display:flex"] { flex-wrap: wrap; }
@media (max-width: 768px) {
  body { padding-left: clamp(12px, 4vw, 24px); padding-right: clamp(12px, 4vw, 24px); }
  main, section, article, aside, header, footer, nav, form, fieldset { width: 100%%; }
  table { display: block; max-width: 100%%; overflow-x: auto; }
}
%s''' % (begin, end)
        html = self.panel.source.toPlainText()
        block_pattern = re.compile(re.escape(begin) + r'.*?' + re.escape(end), re.S)
        if block_pattern.search(html):
            html = block_pattern.sub(responsive_css, html, count=1)
        else:
            style_blocks = list(re.finditer(r'<style\b[^>]*>.*?</style\s*>', html, re.I | re.S))
            if style_blocks:
                block = style_blocks[-1]
                close = html.lower().rfind('</style', block.start(), block.end())
                html = html[:close].rstrip() + '\n' + responsive_css + '\n' + html[close:]
            else:
                head_close = html.lower().find('</head>')
                snippet = '<style>\n' + responsive_css + '\n</style>\n'
                html = html[:head_close] + snippet + html[head_close:] if head_close >= 0 else snippet + html
        if not re.search(r'<meta\b[^>]*name\s*=\s*["\']viewport["\']', html, re.I):
            head_open = re.search(r'<head\b[^>]*>', html, re.I)
            viewport = '\n  <meta name="viewport" content="width=device-width, initial-scale=1">'
            html = html[:head_open.end()] + viewport + html[head_open.end():] if head_open else html
        self.panel.replace_source(format_html(html))
        self.status.setText(self.panel.text(
            'Адаптивные правила добавлены. Они действуют и для новых элементов.',
            'Responsive rules added. They also apply to future elements.'))
        self.tabs.setCurrentIndex(0)

    def apply_density_bridge(self):
        try: percent = max(25, min(200, int(self.density_scale.text().strip() or '100')))
        except ValueError:
            self.status.setText(self.panel.text('Введите масштаб от 25 до 200 процентов.', 'Enter a scale from 25 to 200 percent.')); return
        self.density_scale.setText(str(percent)); self.panel.enable_density_bridge(percent)
        self.status.setText(self.panel.text('Density Bridge добавлен. Масштаб интерфейса: %d%%.' % percent, 'Density Bridge added. Interface scale: %d%%.' % percent))

    def new_document(self):
        if self.panel.source.toPlainText().strip() and QMessageBox.question(self, self.panel.text('Заменить HTML?', 'Replace HTML?'), self.panel.text('Заменить текущий HTML базовым документом? Отмена доступна в редакторе.', 'Replace current HTML with a basic document? You can undo this in the editor.'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        cursor = self.panel.source.textCursor(); cursor.select(cursor.Document); cursor.insertText(basic_document(self.panel.lang_code)); self.panel.enable_bridge()
        self.tabs.setCurrentIndex(0)

    def resize_preview(self, index):
        self.panel.preview.setMaximumWidth([16777215,375,768,1024][index])
