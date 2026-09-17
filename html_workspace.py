"""Additional HTML authoring panels; no third-party editor assets required."""
import json
import re
import uuid
from html.parser import HTMLParser
from pathlib import Path
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QCursor, QColor, QTextCursor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QPlainTextEdit, QPushButton, QLabel, QLineEdit, QFormLayout, QScrollArea,
    QMessageBox, QComboBox, QDialog, QTableWidget, QTableWidgetItem, QListWidget, QMenu, QColorDialog, QInputDialog)


class PropertyChoice(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

    def text(self):
        return self.currentData()


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

# Full editable catalogue for the "Advanced: CSS" tables. Unknown properties from
# an existing rule are appended as well, so no authored CSS is hidden or discarded.
CSS_PROPERTY_CATALOG = tuple('''
accent-color align-content align-items align-self alignment-baseline all animation animation-delay animation-direction animation-duration animation-fill-mode animation-iteration-count animation-name animation-play-state animation-timing-function appearance aspect-ratio backface-visibility background background-attachment background-blend-mode background-clip background-color background-image background-origin background-position background-repeat background-size baseline-shift block-size border border-block border-block-color border-block-end border-block-start border-block-style border-block-width border-bottom border-bottom-color border-bottom-left-radius border-bottom-right-radius border-bottom-style border-bottom-width border-collapse border-color border-end-end-radius border-end-start-radius border-image border-image-outset border-image-repeat border-image-slice border-image-source border-image-width border-inline border-inline-color border-inline-end border-inline-start border-inline-style border-inline-width border-left border-left-color border-left-style border-left-width border-radius border-right border-right-color border-right-style border-right-width border-spacing border-start-end-radius border-start-start-radius border-style border-top border-top-color border-top-left-radius border-top-right-radius border-top-style border-top-width border-width bottom box-shadow box-sizing break-after break-before break-inside caption-side caret-color clear clip clip-path clip-rule color color-interpolation color-rendering color-scheme column-count column-fill column-gap column-rule column-rule-color column-rule-style column-rule-width column-span column-width columns contain content counter-increment counter-reset cursor direction display empty-cells fill fill-opacity fill-rule filter flex flex-basis flex-direction flex-flow flex-grow flex-shrink flex-wrap float flood-color flood-opacity font font-family font-feature-settings font-kerning font-language-override font-optical-sizing font-palette font-size font-size-adjust font-stretch font-style font-synthesis font-variant font-variant-caps font-variant-east-asian font-variant-ligatures font-variant-numeric font-variation-settings font-weight gap grid grid-area grid-auto-columns grid-auto-flow grid-auto-rows grid-column grid-column-end grid-column-start grid-row grid-row-end grid-row-start grid-template grid-template-areas grid-template-columns grid-template-rows height hyphens image-rendering inline-size inset inset-block inset-inline isolation justify-content justify-items justify-self left letter-spacing line-break line-height list-style list-style-image list-style-position list-style-type margin margin-block margin-bottom margin-inline margin-left margin-right margin-top marker mask mask-image mask-position mask-repeat mask-size max-block-size max-height max-inline-size max-width min-block-size min-height min-inline-size min-width mix-blend-mode object-fit object-position offset offset-anchor offset-distance offset-path offset-position offset-rotate opacity order orphans outline outline-color outline-offset outline-style outline-width overflow overflow-wrap overflow-x overflow-y overscroll-behavior padding padding-block padding-bottom padding-inline padding-left padding-right padding-top page-break-after page-break-before page-break-inside paint-order perspective perspective-origin place-content place-items place-self pointer-events position quotes resize right rotate row-gap ruby-align ruby-position scale scroll-behavior scroll-margin scroll-padding shape-image-threshold shape-margin shape-outside stop-color stop-opacity stroke stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit stroke-opacity stroke-width tab-size table-layout text-align text-align-last text-anchor text-combine-upright text-decoration text-decoration-color text-decoration-line text-decoration-style text-decoration-thickness text-emphasis text-indent text-justify text-orientation text-overflow text-rendering text-shadow text-transform text-underline-offset text-underline-position top touch-action transform transform-box transform-origin transform-style transition transition-delay transition-duration transition-property transition-timing-function translate unicode-bidi user-select vertical-align visibility white-space widows width will-change word-break word-spacing word-wrap writing-mode z-index zoom
'''.split())


AUTHORING_DIALOG_STYLE = '''
    QDialog { background:#191d25; color:#f4f6f8; }
    QLabel { color:#d8e0ea; }
    QLineEdit, QComboBox, QTableWidget { background:#11141a; color:#f4f6f8; border:1px solid #384150; border-radius:8px; padding:6px; }
    QLineEdit:focus, QComboBox:focus, QTableWidget:focus { border-color:#ee6b2f; }
    QComboBox QAbstractItemView { background:#1d2129; color:#f4f6f8; selection-background-color:#ee6b2f; }
    QTabWidget::pane { background:#191d25; border:1px solid #303744; border-radius:10px; }
    QTabBar::tab { background:#151920; color:#aab4c2; padding:7px 11px; margin-right:2px; }
    QTabBar::tab:selected { background:#262d38; color:#ffffff; }
    QScrollArea, QScrollArea::viewport, QWidget#settingsContent { background:#191d25; border:0; }
    QTableCornerButton::section { background:#1d2129; border:0; border-right:1px solid #303744; border-bottom:1px solid #303744; }
    QTableWidget#elementAttributesTable QHeaderView::section:vertical { background:#1d2129; color:#aab4c2; border:0; border-right:1px solid #303744; border-bottom:1px solid #303744; padding:0; min-width:28px; }
    QTableWidget::item:selected { background:#ee6b2f; color:#ffffff; }
    QPushButton { background:#ee6b2f; color:#ffffff; border:0; border-radius:10px; padding:9px 14px; font:600 10pt "Segoe UI"; }
    QPushButton:hover { background:#ff834b; }
'''

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
        self.setStyleSheet(AUTHORING_DIALOG_STYLE)
        tr = workspace.panel.text
        self.setWindowTitle(tr('Настроить элемент', 'Configure element') + ' — ' + data['tag']); self.resize(680, 620)
        layout = QVBoxLayout(self); self.classes = QLineEdit(data['attributes'].get('class', ''))
        layout.addWidget(QLabel(tr('Классы (через пробел)', 'Classes (space-separated)'))); layout.addWidget(self.classes)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        common = QWidget(); common.setObjectName('settingsContent'); form = QFormLayout(common); self.common = {}
        self.caption = QLineEdit(data.get('text', '')); form.addRow(tr('Текст элемента', 'Element text'), self.caption)
        for prop, ru, en in COMMON_CSS:
            edit=QLineEdit(data['styles'].get(prop,'')); edit.setToolTip(prop); self.common[prop]=edit; form.addRow(tr(ru,en),edit)
        self.field_name=QLineEdit(data['attributes'].get('name','')); form.addRow(tr('Имя поля для скрипта','Field name for script'),self.field_name)
        self.action_name=QLineEdit(data['attributes'].get('data-scs-action','')); form.addRow(tr('Действие кнопки','Button action'),self.action_name)
        common_scroll=QScrollArea(); common_scroll.setWidgetResizable(True); common_scroll.setWidget(common); self.tabs.addTab(common_scroll,tr('Основное','General'))
        choices = {
            'font-weight': [('Обычный','Normal','400'),('Полужирный','Semibold','600'),('Жирный','Bold','700')],
            'text-align': [('Слева','Left','left'),('По центру','Center','center'),('Справа','Right','right'),('По ширине','Justify','justify')],
            'display': [('Блок','Block','block'),('В строку','Inline','inline'),('Гибкий ряд / колонка','Flex layout','flex'),('Сетка','Grid','grid'),('Скрыть','Hidden','none')],
        }
        for prop, options in choices.items():
            old = self.common[prop]; value = old.text(); combo = PropertyChoice()
            for ru, en, css in options: combo.addItem(tr(ru,en),css)
            index = combo.findData(value)
            if index < 0: combo.addItem(value,value); index=combo.count()-1
            combo.setCurrentIndex(index); combo.setToolTip(prop)
            form.replaceWidget(old,combo); old.deleteLater(); self.common[prop]=combo
        for prop in ('color','background-color'):
            edit=self.common[prop]; wrapper=QWidget(); row=QHBoxLayout(wrapper); row.setContentsMargins(0,0,0,0)
            form.replaceWidget(edit,wrapper); row.addWidget(edit)
            button=QPushButton(tr('Выбрать…','Choose…')); row.addWidget(button)
            button.clicked.connect(lambda _, field=edit: self.choose_color(field))
        self.caption.setEnabled(not data.get('has_children',False))
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
                vertical_header.setStyleSheet('QHeaderView { background:#1d2129; border:0; } QHeaderView::section { background:#1d2129; color:#aab4c2; border:0; border-right:1px solid #303744; border-bottom:1px solid #303744; padding:0; }')
            self.tables[key] = table; self.tabs.addTab(table, title)
        synchronize_css_fields(self.common, self.tables['styles'])
        add = QPushButton(tr('+ Параметр', '+ Property')); add.clicked.connect(self.add_row); layout.addWidget(add)
        self.scope = QComboBox(); self.scope.addItems([tr('Этот объект', 'This element'), tr('Все объекты этого типа', 'All elements of this type'), tr('Все объекты класса', 'All elements of class')]); layout.addWidget(self.scope)
        self.class_target = QLineEdit(); self.class_target.setPlaceholderText(tr('Имя класса для общего CSS', 'Class name for shared CSS')); layout.addWidget(self.class_target)
        self.error = QLabel(); layout.addWidget(self.error)
        apply = QPushButton(tr('Применить', 'Apply')); apply.clicked.connect(self.apply); layout.addWidget(apply)

    def choose_color(self, field):
        color=QColorDialog.getColor(QColor(field.text()),self,self.workspace.panel.text('Выбор цвета','Choose color'))
        if color.isValid(): field.setText(color.name())

    def add_row(self):
        table = self.tables['attributes' if self.tabs.currentIndex() == 1 else 'styles']; self.tabs.setCurrentWidget(table); table.insertRow(table.rowCount())

    def apply(self):
        values = {}
        for key, table in self.tables.items():
            values[key] = {table.item(row, 0).text().strip(): table.item(row, 1).text() if table.item(row, 1) else '' for row in range(table.rowCount()) if table.item(row, 0) and table.item(row, 0).text().strip()}
        attrs = {k: v for k, v in values['attributes'].items() if self.data['attributes'].get(k) != v and k not in ('class', 'style')}
        attrs['class'] = self.classes.text()
        styles = {k: v for k, v in values['styles'].items() if self.data['styles'].get(k, '') != v}
        for key,edit in self.common.items():
            if edit.text()!=self.data['styles'].get(key,''): styles[key]=edit.text()
        for key,edit in [('name',self.field_name),('data-scs-action',self.action_name)]:
            if edit.text()!=self.data['attributes'].get(key,''): attrs[key]=edit.text()
        payload = dict(selector=self.data['selector'], tag=self.data['tag'], scope=self.scope.currentIndex(), cls=self.class_target.text().strip(), attrs=attrs, styles=styles)
        if self.caption.text()!=self.data.get('text',''): payload['text']=self.caption.text()
        script = '''(() => {try {const p=%s; const e=document.querySelector(p.selector); if(!e)throw Error('Element not found');
        Object.entries(p.attrs).forEach(([k,v])=>e.setAttribute(k,v));
        if('text' in p){if(e.children.length)throw Error('Text contains nested elements');e.textContent=p.text;}
        if(p.scope===0)Object.entries(p.styles).forEach(([k,v])=>e.style.setProperty(k,v));
        return {html:'<!doctype html>\\n'+document.documentElement.outerHTML};
        }catch(e){return {error:e.message};}})()''' % json.dumps(payload)
        def done(result):
            if not result or result.get('error'): self.error.setText((result or {}).get('error', 'Error')); return
            self.workspace.panel.replace_source(format_html(result['html']))
            if self.scope.currentIndex() == 1: self.workspace.update_css_rule(self.data['tag'], styles)
            elif self.scope.currentIndex() == 2:
                if not payload['cls']: self.error.setText(self.workspace.panel.text('Укажите имя класса.', 'Enter a class name.')); return
                self.workspace.update_css_rule('.' + payload['cls'].lstrip('.'), styles)
            self.accept()
        self.workspace.panel.preview.page().runJavaScript(script, done)


class RuleSettings(QDialog):
    """A friendly editor for an existing CSS selector or a new class rule."""
    def __init__(self, workspace, selector, styles):
        super().__init__(workspace.panel); self.workspace = workspace; self.selector = selector
        self.setStyleSheet(AUTHORING_DIALOG_STYLE)
        tr = workspace.panel.text; self.setWindowTitle(tr('Настроить стили', 'Configure styles') + ' — ' + selector); self.resize(620, 570)
        layout = QVBoxLayout(self); layout.addWidget(QLabel(tr('Правило CSS', 'CSS rule') + ': ' + selector))
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        page = QWidget(); page.setObjectName('settingsContent'); form = QFormLayout(page); self.common = {}
        for prop, ru, en in COMMON_CSS:
            edit = QLineEdit(styles.get(prop, '')); edit.setToolTip(prop); self.common[prop] = edit; form.addRow(tr(ru,en), edit)
        choices = {
            'font-weight': [('Обычный', 'Normal', '400'), ('Полужирный', 'Semibold', '600'), ('Жирный', 'Bold', '700')],
            'text-align': [('Слева', 'Left', 'left'), ('По центру', 'Center', 'center'), ('Справа', 'Right', 'right'), ('По ширине', 'Justify', 'justify')],
            'display': [('Блок', 'Block', 'block'), ('В строку', 'Inline', 'inline'), ('Гибкий ряд / колонка', 'Flex layout', 'flex'), ('Сетка', 'Grid', 'grid'), ('Скрыть', 'Hidden', 'none')],
        }
        for prop, options in choices.items():
            old = self.common[prop]; value = old.text(); combo = PropertyChoice(); combo.addItem('—', '')
            for ru, en, css in options: combo.addItem(tr(ru, en), css)
            index = combo.findData(value)
            if index < 0 and value: combo.addItem(value, value); index = combo.count() - 1
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.setToolTip(prop); form.replaceWidget(old, combo); old.deleteLater(); self.common[prop] = combo
        for prop in ('color', 'background-color'):
            edit = self.common[prop]; wrapper = QWidget(); row = QHBoxLayout(wrapper); row.setContentsMargins(0, 0, 0, 0)
            form.replaceWidget(edit, wrapper); row.addWidget(edit)
            button = QPushButton(tr('Выбрать…', 'Choose…')); row.addWidget(button)
            button.clicked.connect(lambda _, field=edit: self.choose_color(field))
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page); self.tabs.addTab(scroll, tr('Основное', 'General'))
        names = list(dict.fromkeys((*CSS_PROPERTY_CATALOG, *styles)))
        self.table = QTableWidget(len(names), 2); self.table.setHorizontalHeaderLabels([tr('Параметр','Property'), tr('Значение','Value')]); self.table.horizontalHeader().setStretchLastSection(True)
        for row, key in enumerate(names): self.table.setItem(row, 0, QTableWidgetItem(key)); self.table.setItem(row, 1, QTableWidgetItem(styles.get(key, '')))
        self.tabs.addTab(self.table, tr('Дополнительно: CSS', 'Advanced: CSS'))
        synchronize_css_fields(self.common, self.table)
        add = QPushButton(tr('+ Параметр', '+ Property')); add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount())); layout.addWidget(add)
        error = QLabel(); layout.addWidget(error)
        apply = QPushButton(tr('Применить', 'Apply')); layout.addWidget(apply)
        def save():
            values = {self.table.item(row,0).text().strip(): self.table.item(row,1).text() if self.table.item(row,1) else '' for row in range(self.table.rowCount()) if self.table.item(row,0) and self.table.item(row,0).text().strip()}
            for key, field in self.common.items():
                if field.text() != styles.get(key, ''): values[key] = field.text()
            if self.workspace.update_css_rule(self.selector, values): self.accept()
            else: error.setText(self.workspace.panel.text('В проекте нет блока CSS для нового правила.', 'The project has no CSS block for the new rule.'))
        apply.clicked.connect(save)

    def choose_color(self, field):
        color = QColorDialog.getColor(QColor(field.text()), self, self.workspace.panel.text('Выбор цвета', 'Choose color'))
        if color.isValid(): field.setText(color.name())


def basic_document(language='en'):
    return f'''<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SCS Tool</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 24px; background: #101216; color: #f4f6f8;
           font: 16px system-ui, sans-serif; }}
    main {{ max-width: 960px; margin: auto; }}
    button, input, select {{ font: inherit; padding: 10px 16px; border-radius: 10px; }}
    button {{ background: #ee6b2f; color: white; border: 0; cursor: pointer; }}
    .card {{ background: #191d25; padding: 24px; border-radius: 18px; }}
  </style>
</head>
<body>
  <main><section class="card"><h1>SCS Tool</h1><p>Welcome</p></section></main>
</body>
</html>
'''


class AuthoringWorkspace(QWidget):
    def __init__(self, panel, highlighter):
        super().__init__(); self.panel = panel; self.highlighter_class = highlighter
        self.setStyleSheet('''
            QWidget#authoringPage, QTabWidget::pane, QScrollArea, QScrollArea::viewport { background:#191d25; border:1px solid #303744; }
            QLineEdit, QComboBox, QListWidget, QTableWidget { background:#11141a; color:#f4f6f8; border:1px solid #384150; border-radius:8px; padding:6px; }
            QListWidget::item:selected, QTableWidget::item:selected { background:#ee6b2f; color:white; }
            QHeaderView::section { background:#1d2129; color:#aab4c2; border:0; padding:7px; }
            QTableCornerButton::section { background:#1d2129; border:1px solid #303744; }
            QComboBox QAbstractItemView { background:#1d2129; color:#f4f6f8; selection-background-color:#ee6b2f; }
        ''')
        self.labels = []; self.editors = {}; self.active_path = None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        self.tabs.addTab(panel.source, 'HTML')
        assets = QWidget(); assets.setObjectName('authoringPage'); box = QVBoxLayout(assets)
        self.files = QComboBox(); box.addWidget(self.files)
        self.asset_editor = QPlainTextEdit(); self.asset_editor.setObjectName('projectEditor')
        self.asset_highlighter = highlighter(self.asset_editor.document()); box.addWidget(self.asset_editor)
        self.tabs.addTab(assets, 'CSS / JS')
        self.asset_editor.textChanged.connect(self.save_asset)
        self.files.currentIndexChanged.connect(self.open_asset)
        row = QHBoxLayout(); box.addLayout(row)
        self.button(row, 'Добавить стили в HTML', 'Add styles to HTML', lambda: self.new_asset('css'))
        self.button(row, 'Добавить JS в HTML', 'Add JS to HTML', lambda: self.new_asset('js'))
        classes = QWidget(); classes.setObjectName('authoringPage'); classes_form = QFormLayout(classes)
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
        self.class_advanced = QTableWidget(len(CSS_PROPERTY_CATALOG), 2); self.class_advanced.setHorizontalHeaderLabels([self.panel.text('Параметр', 'Property'), self.panel.text('Значение', 'Value')]); self.class_advanced.horizontalHeader().setStretchLastSection(True)
        for row, prop in enumerate(CSS_PROPERTY_CATALOG): self.class_advanced.setItem(row, 0, QTableWidgetItem(prop)); self.class_advanced.setItem(row, 1, QTableWidgetItem(''))
        self.class_advanced.setMinimumHeight(330); self.class_advanced_label = QLabel(); classes_form.addRow(self.class_advanced_label, self.class_advanced)
        synchronize_css_fields(self.class_properties, self.class_advanced)
        self.button(classes_form, 'Применить стили класса', 'Apply class styles', self.save_class_rule)
        self.class_list.currentTextChanged.connect(self.load_class_rule)
        classes_scroll = QScrollArea(); classes_scroll.setWidgetResizable(True); classes_scroll.setWidget(classes)
        self.tabs.addTab(classes_scroll, '')
        inspector = QWidget(); inspector.setObjectName('authoringPage'); form = QFormLayout(inspector)
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
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(inspector)
        self.tabs.addTab(scroll, '')
        row = QHBoxLayout(); layout.addLayout(row)
        self.button(row, 'Базовая структура HTML', 'Basic HTML document', self.new_document)
        self.width = QComboBox(); self.width.addItems(['Auto', '375 px', '768 px', '1024 px']); row.addWidget(self.width)
        self.width.currentIndexChanged.connect(self.resize_preview)
        self.status = QLabel(); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.reload_assets(); self.translate()
        self.tabs.currentChanged.connect(self.tab_changed)
        self.panel.source.textChanged.connect(self.source_changed)
        self.panel.preview.loadFinished.connect(self.install_selection)
        self.selection_timer = QTimer(self); self.selection_timer.setInterval(400)
        self.selection_timer.timeout.connect(self.read_selection); self.selection_timer.start()
        self.last_selection = None; self.pending_highlight = False
        panel.preview.setContextMenuPolicy(Qt.NoContextMenu)

    def context_menu(self, position):
        QTimer.singleShot(50, lambda: self.show_element_menu(position))

    def show_element_menu(self, position):
        menu = QMenu(self.panel)
        configure = menu.addAction(self.panel.text('Настроить', 'Configure'))
        global_styles = menu.addAction(self.panel.text('Глобальные стили…', 'Global styles…'))
        move = menu.addAction(self.panel.text('Переместить', 'Move'))
        html_move = menu.addAction(self.panel.text('Переместить в HTML', 'Move in HTML'))
        reset = menu.addAction(self.panel.text('Сбросить масштаб', 'Reset zoom'))
        chosen = menu.exec_(self.panel.preview.mapToGlobal(position))
        if chosen == html_move: self.start_html_move(); return
        if chosen == move: self.start_move(); return
        if chosen == reset: self.panel.preview.setZoomFactor(1.0)
        if chosen == global_styles: self.choose_rule('global'); return
        if chosen != configure: return
        script = '''(() => {const selector=window.__scsSelectedSelector;const e=selector&&document.querySelector(selector);if(!e)return null;
        const styles={};
        for(const sheet of document.styleSheets){try{for(const r of sheet.cssRules||[]){if(r.type===CSSRule.STYLE_RULE&&e.matches(r.selectorText))for(const key of r.style)styles[key]=r.style.getPropertyValue(key);}}catch(_){}}
        for(const key of e.style)styles[key]=e.style.getPropertyValue(key);
        const attributes={};Array.from(e.attributes).forEach(a=>attributes[a.name]=a.value);
        return {selector, tag:e.tagName.toLowerCase(), styles, attributes, has_children:!!e.children.length, text:e.children.length?'':e.textContent};})()'''
        self.panel.preview.page().runJavaScript(script, lambda data: ElementSettings(self, data).exec_() if data else None)

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
        elif index == 3: self.refresh_logic_options()

    def source_changed(self):
        if self.tabs.currentIndex() == 1: self.reload_assets()
        elif self.tabs.currentIndex() == 2: self.reload_classes()

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
            window.__scsSelectedSelector=null;
        }
        function selectElement(e, toggle=true) {
            if(window.__scsMoving)return;
            if(!document.body || !document.body.contains(e.target)) { clearEditorSelection(); return false; }
            const current=window.__scsSelectedSelector&&document.querySelector(window.__scsSelectedSelector);
            if(toggle && (e.target.hasAttribute('data-scs-editor-selected') || current===e.target)) {
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
            window.__scsSelectedSelector=parts.join(' > ');
            document.querySelectorAll('[data-scs-editor-selected]').forEach(node=>{node.style.outline='';node.style.outlineOffset='';node.removeAttribute('data-scs-editor-selected');});
            e.target.setAttribute('data-scs-editor-selected','');e.target.style.outline='2px dashed #ee6b2f';e.target.style.outlineOffset='3px';
        }
        document.addEventListener('click', selectElement, true);
        document.addEventListener('contextmenu', e=>{if(selectElement(e, false)!==false) window.__scsContextPending=true;}, true);
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
        script = '''(() => {const e=document.querySelector(%s);if(!e)return;
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
            if value.get('zoom'):
                self.panel.preview.setZoomFactor(min(3.0,max(.25,self.panel.preview.zoomFactor()+value['zoom'])))
            if value.get('html'): self.panel.replace_source(format_html(value['html'])); return
            selector=value.get('selector')
            if not selector:
                self.last_selection = None; self.selector.clear(); self.class_selector.clear(); self.element_classes.clear()
                return
            if selector and selector != self.last_selection:
                self.last_selection = selector; self.selector.setText(selector); self.highlight_source_selector(selector); self.read_element_classes(); self.read_action_binding()
            if value.get('context'): self.show_element_menu(self.panel.preview.mapFromGlobal(QCursor.pos()))
        self.panel.preview.page().runJavaScript('(() => {const result={selector:window.__scsSelectedSelector,context:!!window.__scsContextPending,html:window.__scsMovedHTML,zoom:window.__scsZoomDelta};window.__scsMovedHTML=null;window.__scsContextPending=false;window.__scsZoomDelta=0;return result;})()', selected)

    def start_move(self):
        self.panel.preview_timer.stop()
        labels=json.dumps([self.panel.text('Перетащите объект мышью','Drag the element'),self.panel.text('Применить','Apply'),self.panel.text('Отмена','Cancel')])
        script='''(() => {
        if(window.__scsMoving)return;
        const e=document.querySelector(window.__scsSelectedSelector);if(!e||e===document.body||e===document.documentElement)return;
        const original=e.getAttribute('style'), labels=%s, editorLanguage=%s; window.__scsMoving=true;
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
        control('←',()=>{x=-e.getBoundingClientRect().left;paint()}); control('↔',()=>{x+=window.innerWidth/2-(e.getBoundingClientRect().left+e.getBoundingClientRect().width/2);paint()}); control('→',()=>{x+=window.innerWidth-e.getBoundingClientRect().right;paint()});
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
        function motion(ev){if(resize){const [axis,sx,sy,startX,startY,startW,startH]=resize,dx=(ev.clientX-startX)*sx,dy=(ev.clientY-startY)*sy;if(axis==='x'||axis==='xy')e.style.width=Math.max(12,startW+dx)+'px';if(axis==='y'||axis==='xy')e.style.height=Math.max(12,startH+dy)+'px';placeHandles();hint.textContent='W: '+e.offsetWidth+'  H: '+e.offsetHeight;return}if(!drag)return;x=ev.clientX-drag[0];y=ev.clientY-drag[1];paint();const r=e.getBoundingClientRect(),d=8;if(Math.abs(r.left)<d)x-=r.left;if(Math.abs(r.right-window.innerWidth)<d)x+=window.innerWidth-r.right;if(Math.abs(r.left+r.width/2-window.innerWidth/2)<d)x+=window.innerWidth/2-(r.left+r.width/2);if(Math.abs(r.top)<d)y-=r.top;if(Math.abs(r.bottom-window.innerHeight)<d)y+=window.innerHeight-r.bottom;if(Math.abs(r.top+r.height/2-window.innerHeight/2)<d)y+=window.innerHeight/2-(r.top+r.height/2);paint();hint.textContent=labels[0]+'  X: '+Math.round(x)+'  Y: '+Math.round(y);}
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
        script = '''(() => {if(window.__scsMoving)return;const e=document.querySelector(window.__scsSelectedSelector);if(!e||e===document.body||e===document.documentElement)return;
          const originalParent=e.parentNode, originalNext=e.nextSibling, labels=%s;window.__scsMoving=true;e.style.outline='2px dashed #ee6b2f';e.style.cursor='move';
          const bar=document.createElement('div');bar.dataset.scsMoveBar='';bar.style.cssText='position:fixed;bottom:16px;left:16px;z-index:2147483647;padding:14px;background:#191d25;color:white;border-radius:12px;font:14px sans-serif';bar.append(labels[0]);
          function selectorFor(n){let p=[];while(n&&n.nodeType===1){let i=1,q=n.previousElementSibling;while(q){if(q.tagName===n.tagName)i++;q=q.previousElementSibling}p.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');n=n.parentElement}return p.join(' > ')}
          function finish(ok){document.removeEventListener('mousedown',down,true);document.removeEventListener('mousemove',move,true);document.removeEventListener('mouseup',up,true);bar.remove();e.style.pointerEvents='';e.style.outline='';e.style.cursor='';window.__scsMoving=false;if(!ok)originalParent.insertBefore(e,originalNext);else{window.__scsSelectedSelector=selectorFor(e);window.__scsMovedHTML='<!doctype html>\\n'+document.documentElement.outerHTML;}}
          [true,false].forEach((ok,i)=>{const b=document.createElement('button');b.textContent=labels[i+1];b.style.marginLeft='10px';b.onclick=()=>finish(ok);bar.appendChild(b)});document.body.appendChild(bar);let dragging=false;
          function down(ev){if(ev.button===0&&e.contains(ev.target)&&!bar.contains(ev.target)){dragging=true;e.style.pointerEvents='none';ev.preventDefault()}}
          function place(ev){const raw=document.elementFromPoint(ev.clientX,ev.clientY);if(!raw||raw.closest('[data-scs-move-bar]'))return;const target=raw.closest('body *')||document.body;if(target===e||e.contains(target)||['SCRIPT','STYLE','META','LINK'].includes(target.tagName))return;const r=target.getBoundingClientRect();document.querySelectorAll('[data-scs-drop-target]').forEach(n=>n.removeAttribute('data-scs-drop-target'));target.setAttribute('data-scs-drop-target','');if(target===document.body){const marker=Array.from(target.childNodes).find(n=>n.nodeType===8&&n.nodeValue.trim()==='SCS_TOOL_BRIDGE');target.insertBefore(e,marker||null)}else if(ev.clientY<=r.top+r.height*.35)target.parentNode.insertBefore(e,target);else if(ev.clientY>=r.bottom-r.height*.35)target.parentNode.insertBefore(e,target.nextSibling);else target.appendChild(e)}
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
        item = self.class_list.currentItem()
        self.load_class_rule(item.text() if item else '')
        self.status.setText(self.panel.text('Классы редактируются во вкладке «Классы». Правый клик по объекту — настройка или перемещение.', 'Edit classes in the Classes tab. Right-click an element to configure or move it.'))

    def reload_assets(self):
        previous = self.files.currentData()
        self.files.blockSignals(True); self.files.clear()
        self.inline_blocks=list(re.finditer(r'<(style|script)\b([^>]*)>(.*?)</\1\s*>',self.panel.source.toPlainText(),re.I|re.S))
        for index,match in enumerate(self.inline_blocks):
            if re.search(r'\bsrc\s*=',match.group(2),re.I):continue
            self.files.addItem(f'{match.group(1).upper()} #{index+1}',index)
        selected = self.files.findData(previous)
        if selected >= 0: self.files.setCurrentIndex(selected)
        self.files.blockSignals(False); self.open_asset()

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
        self.status.setText(self.panel.text('Список действий и ID обновлён из логики.', 'Actions and IDs were refreshed from logic.'))
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

    def new_document(self):
        if self.panel.source.toPlainText().strip() and QMessageBox.question(self, self.panel.text('Заменить HTML?', 'Replace HTML?'), self.panel.text('Заменить текущий HTML базовым документом? Отмена доступна в редакторе.', 'Replace current HTML with a basic document? You can undo this in the editor.'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        cursor = self.panel.source.textCursor(); cursor.select(cursor.Document); cursor.insertText(basic_document(self.panel.lang_code)); self.panel.enable_bridge()
        self.tabs.setCurrentIndex(0)

    def resize_preview(self, index):
        self.panel.preview.setMaximumWidth([16777215,375,768,1024][index])
