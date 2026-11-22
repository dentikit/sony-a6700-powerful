"""Display-only interface language for the Windows UI; no device logic.

Chinese stays the source and default language. When English is active, only
what Qt draws changes: widgets keep the original Chinese as their logical text
(``text()``, ``windowTitle()`` and ``toolTip()`` return it), so app logic, the
service bridge, feedback reports and the offline self-test see exactly the
same strings as before.

Language order: ``POWERFUL6700_LANG`` (``zh``/``en``), the saved
``Interface/language`` setting, then the Windows display language. The offline
self-test keeps Chinese unless ``POWERFUL6700_LANG`` is set.
"""
import atexit
import os
import re
from functools import lru_cache

LANGUAGES = ('zh', 'en')
SETTINGS = ('Powerful6700', 'Interface')
SOURCE = 'i18nSource'
_HAN = re.compile(r'[一-鿿]')
_EDGE = re.compile(r'^([\s\W_]*)(.*?)(\s*)$', re.S)
_SENTENCE = re.compile(r'(?<=[。；！？])')
_SEPARATORS = (('\n', '\n'), (' → ', ' → '), (' · ', ' · '), ('、', ', '))
_language = 'zh'
_missing = set()
_installed = False


def language():
    return _language


def resolve(settings=None, force_default=False):
    """Pick the interface language without touching any device state."""
    env = os.environ.get('POWERFUL6700_LANG', '').strip().lower()
    if env in LANGUAGES:
        return env
    if force_default:
        return 'zh'
    if settings is not None:
        saved = str(settings.value('language', '') or '').strip().lower()
        if saved in LANGUAGES:
            return saved
    from PySide6.QtCore import QLocale
    return 'zh' if QLocale.system().language() == QLocale.Language.Chinese else 'en'


def tr(text):
    """Return the display text for ``text`` in the active language."""
    if _language == 'zh' or not isinstance(text, str) or not _HAN.search(text):
        return text
    return _translate(text)


@lru_cache(maxsize=1)
def _catalog():
    from locale_en import EXACT, TEMPLATES
    templates = []
    for source, english in TEMPLATES.items():
        pieces = re.split(r'(\{\d+\})', source)
        literal = ''.join(p for p in pieces if not re.fullmatch(r'\{\d+\}', p))
        if not _HAN.search(literal):
            continue
        order = [int(p[1:-1]) for p in pieces if re.fullmatch(r'\{\d+\}', p)]
        pattern = ''.join('(.+?)' if re.fullmatch(r'\{\d+\}', p) else re.escape(p.replace('{{', '{').replace('}}', '}'))
                          for p in pieces)
        templates.append((len(literal), re.compile(pattern), order, english))
    templates.sort(key=lambda item: -item[0])
    return EXACT, templates


def tr_for(widget, text):
    """Translate ``text`` for ``widget``; ``text@objectName`` entries resolve same-word ambiguity."""
    if _language == 'zh' or not isinstance(text, str) or not _HAN.search(text):
        return text
    from locale_en import CONTEXT
    try:
        name = widget.objectName()
    except (AttributeError, RuntimeError):
        name = ''
    return CONTEXT.get(text + '@' + name) or _translate(text)


def _part(text, lenient=False):
    return text if not _HAN.search(text) else _resolve(text, lenient)


@lru_cache(maxsize=8192)
def _resolve(text, lenient=False):
    """Translate ``text``. Strict mode returns None when any Chinese part is unknown;
    lenient mode keeps unknown parts (such as user-chosen sound names) as they are."""
    exact, templates = _catalog()
    hit = exact.get(text)
    if hit is not None:
        return hit
    head, core, tail = _EDGE.match(text).groups()
    if (head or tail) and core in exact:
        return head + exact[core] + tail
    for _, pattern, order, english in templates:
        match = pattern.fullmatch(text)
        if match:
            parts = [_part(value) for value in match.groups()]
            if None not in parts:
                return _fill(english, order, parts)
    for separator, joiner in _SEPARATORS:
        if separator in text:
            parts = [_part(piece) for piece in text.split(separator)]
            if None not in parts:
                return joiner.join(parts)
            break
    sentences = [piece for piece in _SENTENCE.split(text) if piece]
    if len(sentences) > 1:
        parts = [_sentence(piece) for piece in sentences]
        if None not in parts:
            return ' '.join(parts)
    for separator in ('：', ': '):
        if separator in text:
            left, right = text.split(separator, 1)
            if left in exact or not _HAN.search(left):
                parts = (_part(left), _part(right))
                if None not in parts:
                    return parts[0] + ': ' + parts[1]
    if not lenient:
        return None
    for separator, joiner in _SEPARATORS:
        if separator in text:
            return joiner.join(_part(piece, True) for piece in text.split(separator))
    for _, pattern, order, english in templates:
        match = pattern.fullmatch(text)
        if match:
            return _fill(english, order, [_part(value, True) for value in match.groups()])
    if len(sentences) > 1:
        return ' '.join(_sentence(piece, True) for piece in sentences)
    return text


def _fill(english, order, parts):
    values = [None] * (max(order) + 1 if order else 0)
    for index, value in zip(order, parts):
        values[index] = value
    return english.format(*values)


def _sentence(piece, lenient=False):
    exact, _ = _catalog()
    if piece in exact:
        return exact[piece]
    bare = piece.rstrip('。；！？')
    translated = _part(bare, lenient)
    if translated is None:
        return None
    if translated == bare:
        return piece
    return translated if translated.endswith(('.', '!', '?')) else translated + '.'


def _translate(text):
    """Translate fully when possible; otherwise keep unknown parts and translate the rest."""
    full = _resolve(text)
    if full is not None:
        return full
    _missing.add(text)
    return _resolve(text, True)


def _translated_args(args, kwargs, positions=None):
    args = [tr(a) if (positions is None or i in positions) and isinstance(a, str) else a for i, a in enumerate(args)]
    kwargs = {k: tr(v) if isinstance(v, str) else v for k, v in kwargs.items()}
    return args, kwargs


def _wrap(cls, name, positions=None, static=False):
    original = getattr(cls, name)
    if static:
        def wrapper(*args, **kwargs):
            args, kwargs = _translated_args(args, kwargs, positions)
            return original(*args, **kwargs)
        setattr(cls, name, staticmethod(wrapper))
    else:
        def wrapper(self, *args, **kwargs):
            args, kwargs = _translated_args(args, kwargs, positions)
            return original(self, *args, **kwargs)
        setattr(cls, name, wrapper)


def _wrap_logical(cls, setter, getter):
    """Show translated text while keeping the original as the logical value."""
    original_set, original_get = getattr(cls, setter), getattr(cls, getter)
    key = SOURCE + setter

    def set_value(self, value):
        if isinstance(value, str) and _HAN.search(value):
            self.setProperty(key, value)
            return original_set(self, tr_for(self, value))
        self.setProperty(key, None)
        return original_set(self, value)

    def get_value(self):
        source = self.property(key)
        return source if source is not None else original_get(self)

    setattr(cls, setter, set_value)
    setattr(cls, getter, get_value)
    return original_get


def install(app, chosen):
    """Activate ``chosen``; Chinese leaves Qt untouched."""
    global _language, _installed
    _language = chosen if chosen in LANGUAGES else 'zh'
    if _language == 'zh' or _installed:
        return _language
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtGui import QAction, QPainter
    from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QLabel, QLineEdit, QMenu, QMessageBox,
                                   QPlainTextEdit, QTextEdit, QWidget)
    label_text = _wrap_logical(QLabel, 'setText', 'text')
    button_text = _wrap_logical(QAbstractButton, 'setText', 'text')
    _wrap_logical(QWidget, 'setWindowTitle', 'windowTitle')
    _wrap_logical(QWidget, 'setToolTip', 'toolTip')
    original_clear = QLabel.clear

    def clear(self):
        self.setProperty(SOURCE + 'setText', None)
        return original_clear(self)
    QLabel.clear = clear
    for cls, name in ((QWidget, 'setAccessibleName'), (QWidget, 'setAccessibleDescription'),
                      (QLineEdit, 'setPlaceholderText'), (QPlainTextEdit, 'setPlaceholderText'),
                      (QTextEdit, 'setPlaceholderText'), (QMessageBox, 'setText'),
                      (QMessageBox, 'setInformativeText'), (QMessageBox, 'setDetailedText'),
                      (QMessageBox, 'addButton'), (QMenu, 'addAction'), (QMenu, 'addSection'),
                      (QMenu, 'addMenu'), (QMenu, 'setTitle'), (QAction, 'setText'), (QPainter, 'drawText')):
        _wrap(cls, name)
    for name in ('information', 'warning', 'critical', 'question', 'about'):
        _wrap(QMessageBox, name, positions={1, 2}, static=True)
    for name in ('getOpenFileName', 'getOpenFileNames', 'getSaveFileName'):
        _wrap(QFileDialog, name, positions={1, 3}, static=True)

    class _ConstructorText(QObject):
        """Translate text passed to widget constructors when they are first polished."""
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Polish:
                if isinstance(obj, QLabel) and obj.property(SOURCE + 'setText') is None:
                    raw = label_text(obj)
                    if _HAN.search(raw):
                        obj.setText(raw)
                elif isinstance(obj, QAbstractButton) and obj.property(SOURCE + 'setText') is None:
                    raw = button_text(obj)
                    if _HAN.search(raw):
                        obj.setText(raw)
            return False

    app._i18n_filter = _ConstructorText(app)
    app.installEventFilter(app._i18n_filter)
    report = os.environ.get('POWERFUL6700_I18N_MISSING')
    if report:
        atexit.register(lambda: open(report, 'w', encoding='utf-8').write('\n'.join(sorted(_missing)) + '\n'))
    _installed = True
    return _language
