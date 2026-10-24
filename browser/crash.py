# Crash recovery.
#
#   - The session is saved as you go (and the last good copy is kept as session.prev.json), so a crash
#     loses nothing: next launch brings every tab back and says so.
#   - Python errors from callbacks are written to logs/errors.log; a hard crash inside Qt or Chromium
#     leaves its stack in logs/native-crash.log (faulthandler). "Show Crash Logs" in ⌘T opens the folder.
#   - A page whose renderer crashes (or was killed for memory) shows a card with Reload instead of a
#     blank page; one in the background reloads by itself when you go back to it.

import datetime
import faulthandler
import os
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

import qtawesome as qta

from . import storage

MARKER = "running"
MAX_LOG_BYTES = 1_000_000
_native_log = None


def logs_dir() -> Path:
    folder = storage.data_dir() / "logs"
    folder.mkdir(exist_ok=True)
    return folder


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start() -> dict | None:
    """Call once at launch. Returns {"when": …, "native": bool} if the last run didn't quit properly."""
    global _native_log
    marker = storage.data_dir() / MARKER
    native = logs_dir() / "native-crash.log"
    crashed = None
    try:
        previous = marker.read_text().split()
        if previous and not _alive(int(previous[0])):
            crashed = {"when": " ".join(previous[1:]), "native": native.exists() and native.stat().st_size > 0}
    except (OSError, ValueError):
        pass
    marker.write_text(f"{os.getpid()} {datetime.datetime.now():%Y-%m-%d %H:%M}")
    if crashed and crashed["native"]:
        # Keep the crash's stack for reading; this run gets a fresh file
        native.replace(logs_dir() / f"native-crash-{datetime.datetime.now():%Y%m%d-%H%M%S}.log")
    _native_log = open(native, "w")
    faulthandler.enable(_native_log, all_threads=True)
    return crashed


def clean_exit():
    """Call as the app quits normally."""
    global _native_log
    try:
        (storage.data_dir() / MARKER).unlink(missing_ok=True)
    except OSError:
        pass
    if _native_log is not None:
        faulthandler.disable()
        _native_log.close()
        _native_log = None


def log_exception(kind, value, tb):
    """sys.excepthook: print it and keep it in logs/errors.log, but never stop the app."""
    text = "".join(traceback.format_exception(kind, value, tb))
    print(text, end="")
    try:
        log = logs_dir() / "errors.log"
        if log.exists() and log.stat().st_size > MAX_LOG_BYTES:
            log.replace(log.with_suffix(".old.log"))
        with open(log, "a") as f:
            f.write(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
    except OSError:
        pass


# --- A page that crashed ------------------------------------------------------------------------

class CrashedCard(QFrame):
    """Shown over a page whose renderer is gone."""

    def __init__(self, view, reason, theme):
        super().__init__(view)
        self.setObjectName("CrashedCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(qta.icon("ph.smiley-sad", color=theme.accent).pixmap(40, 40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("This page crashed" if reason == "crash" else "This page stopped unexpectedly")
        title.setObjectName("CrashedTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("Your other tabs are fine." if reason == "crash" else
                        "macOS may have closed it to free memory. Your other tabs are fine.")
        detail.setObjectName("CrashedDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        self.setFixedWidth(380)
        reload = QPushButton("Reload")
        reload.setObjectName("CrashReload")
        reload.setCursor(Qt.CursorShape.PointingHandCursor)
        reload.clicked.connect(view.reload)
        for widget in (icon, title, detail):
            layout.addWidget(widget)
        layout.addWidget(reload, 0, Qt.AlignmentFlag.AlignCenter)
        self.adjustSize()
        view.installEventFilter(self)
        self._place()
        self.show()

    def _place(self):
        host = self.parentWidget()
        self.move((host.width() - self.width()) // 2, max((host.height() - self.height()) // 3, 20))

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == event.Type.Resize:
            self._place()
        return False


class CrashWatcher:
    """Watches every page for its renderer going away."""

    def __init__(self, browser):
        self.browser = browser
        self._cards = {}  # id(view) → CrashedCard

    def attach(self, view):
        page = view.page()
        page.renderProcessTerminated.connect(lambda status, code, v=view: self._terminated(v, status, code))
        view.loadStarted.connect(lambda v=view: self._clear(v))
        view.destroyed.connect(lambda _=None, key=id(view): self._cards.pop(key, None))

    def _terminated(self, view, status, code):
        S = QWebEnginePage.RenderProcessTerminationStatus
        if status == S.NormalTerminationStatus:
            return
        reason = "killed" if status == S.KilledTerminationStatus else "crash"
        tab = next((t for t in self.browser.store.all_tabs() if t.view is view), None)
        if tab is not None and view is not self.browser._current_view() and not view.isVisible():
            tab.crashed = True  # reloaded when you come back to it
            return
        self._clear(view)
        self._cards[id(view)] = CrashedCard(view, reason, self.browser.theme)

    def tab_shown(self, tab):
        if getattr(tab, "crashed", False) and hasattr(tab.view, "reload"):
            tab.crashed = False
            tab.view.reload()

    def _clear(self, view):
        card = self._cards.pop(id(view), None)
        if card is not None:
            card.hide()
            card.deleteLater()
