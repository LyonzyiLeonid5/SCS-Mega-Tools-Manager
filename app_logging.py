"""Central application logging for user actions and failures."""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
import warnings

from PyQt5.QtCore import QEvent, QObject, qInstallMessageHandler, QtDebugMsg, QtInfoMsg, QtWarningMsg, QtCriticalMsg, QtFatalMsg
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QAbstractButton, QMessageBox

from tool_manager import app_data_path


LOGGER_NAME = "scs_tools_manager"
LOG_LIMIT_BYTES = 32 * 1024 * 1024
LOG_RETAIN_BYTES = 28 * 1024 * 1024


class LineTruncatingFileHandler(logging.FileHandler):
    """Keep one readable log file, removing complete oldest lines at 32 MiB."""
    def emit(self, record):
        super().emit(record)
        try:
            self.flush()
            if os.path.getsize(self.baseFilename) <= LOG_LIMIT_BYTES:
                return
            # FileHandler.handle() already owns the handler lock here. Close it
            # before atomically replacing the file on Windows.
            if self.stream:
                self.stream.close(); self.stream = None
            size = os.path.getsize(self.baseFilename)
            with open(self.baseFilename, "rb") as source:
                source.seek(max(0, size - LOG_RETAIN_BYTES))
                if source.tell(): source.readline()  # never retain half a line
                retained = source.read()
            temporary = self.baseFilename + ".trim"
            with open(temporary, "wb") as target: target.write(retained)
            os.replace(temporary, self.baseFilename)
            self.stream = self._open()
            self.stream.write("--- oldest log lines removed after reaching 32 MiB ---\n")
            self.flush()
        except OSError:
            # Logging must not interrupt a user action if storage is unavailable.
            if self.stream is None:
                try: self.stream = self._open()
                except OSError: pass


def logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure_logging() -> logging.Logger:
    log = logger()
    if log.handlers: return log
    log.setLevel(logging.INFO)
    directory = app_data_path() / "logs"; directory.mkdir(parents=True, exist_ok=True)
    handler = LineTruncatingFileHandler(directory / "application.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    log.addHandler(handler)

    def exception_hook(kind, value, trace):
        log.critical("Unhandled exception\n%s", "".join(traceback.format_exception(kind, value, trace)))
        sys.__excepthook__(kind, value, trace)

    def thread_exception_hook(args):
        log.critical("Unhandled thread exception\n%s", "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    def warning_hook(message, category, filename, lineno, file=None, line=None):
        log.warning("Python warning %s:%s: %s: %s", filename, lineno, category.__name__, message)

    def qt_message(mode, context, message):
        levels = {QtDebugMsg: logging.DEBUG, QtInfoMsg: logging.INFO, QtWarningMsg: logging.WARNING, QtCriticalMsg: logging.ERROR, QtFatalMsg: logging.CRITICAL}
        log.log(levels.get(mode, logging.INFO), "Qt: %s", message)

    try:
        qInstallMessageHandler(qt_message)
    except Exception:
        log.exception("Unable to install Qt message handler")
    sys.excepthook = exception_hook; threading.excepthook = thread_exception_hook; warnings.showwarning = warning_hook
    _install_message_box_logging(log)
    log.info("Logging started")
    return log


def _install_message_box_logging(log: logging.Logger) -> None:
    if getattr(QMessageBox, "_scs_logging_installed", False): return
    for name, level in (("warning", logging.WARNING), ("critical", logging.ERROR), ("information", logging.INFO), ("question", logging.INFO)):
        original = getattr(QMessageBox, name)
        def wrapped(*args, _original=original, _level=level, _name=name, **kwargs):
            text = str(args[2]) if len(args) > 2 else str(kwargs.get("text", ""))
            log.log(_level, "Message box %s: %s", _name, text.replace("\n", " "))
            return _original(*args, **kwargs)
        setattr(QMessageBox, name, staticmethod(wrapped))
    QMessageBox._scs_logging_installed = True


class ActionLogger(QObject):
    """Logs actual UI commands without intercepting or changing them."""
    def eventFilter(self, watched, event):
        try:
            log = logger()
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                text = watched.text() if isinstance(watched, QAbstractButton) else ""
                log.info("UI click: %s object=%s text=%r", type(watched).__name__, watched.objectName(), str(text)[:180])
            elif event.type() == QEvent.KeyPress:
                modifiers = int(event.modifiers()); key = event.key()
                if modifiers or key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape, Qt.Key_Delete):
                    log.info("UI key: object=%s key=%s modifiers=%s", watched.objectName(), key, modifiers)
        except Exception:
            # Event filters must never interrupt a user action because logging failed.
            pass
        return False
