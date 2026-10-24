# Developer Mode, Arc style: pages you're building (localhost, 127.0.0.1, *.localhost, *.test, files
# on disk) get the full address in the URL pill, a DEV chip with the page's error count, and a
# console drawer under the page: its log, a line to run JavaScript, and Reload on Save.
#
# Reload on Save watches a project folder and reloads the page whenever a file in it changes.
# A page opened from a file watches its own folder without being asked.

import html
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit,
                             QToolButton, QVBoxLayout)

import qtawesome as qta

MAX_LINES = 400
DRAWER_HEIGHT = 230
POLL_SECONDS = 0.7
RESCAN_SECONDS = 8
MAX_FILES = 6000
SKIP_DIRS = {"node_modules", ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".next", ".nuxt", ".cache",
             ".parcel-cache", ".turbo", "dist", "build", "out", "target", "coverage", ".idea", ".vscode"}
FAILED = "!pb-eval-failed:"  # marks an exception in a console result
WEB_FILES = {".html", ".htm", ".xhtml", ".svg"}
_BROAD_FOLDERS = {Path.home(), Path("/"), Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents"}
LEVELS = {0: "log", 1: "warn", 2: "error"}  # QWebEnginePage.JavaScriptConsoleMessageLevel values


def is_dev_url(url: str) -> bool:
    """A page you're working on: a local server, a .test/.localhost name, or a file."""
    q = QUrl(url)
    if q.scheme() == "file":
        return True
    if q.scheme() not in ("http", "https"):
        return False
    host = q.host().lower()
    return (host in ("localhost", "0.0.0.0", "::1") or host.startswith("127.")
            or host.endswith((".localhost", ".test")))


def dev_label(url: str) -> str:
    """"localhost:5173/settings" — everything but the scheme, the way Arc shows a dev URL."""
    q = QUrl(url)
    if q.scheme() == "file":
        return Path(q.toLocalFile()).name or q.toLocalFile()
    text = q.adjusted(QUrl.UrlFormattingOption.RemoveScheme | QUrl.UrlFormattingOption.RemoveUserInfo).toString()
    return text.removeprefix("//").rstrip("/") or url


def origin_of(url: str) -> str:
    q = QUrl(url)
    if q.scheme() == "file":
        return "file:" + str(Path(q.toLocalFile()).parent)
    return f"{q.scheme()}://{q.host()}:{q.port(443 if q.scheme() == 'https' else 80)}"


def project_files(folder: str, limit=MAX_FILES, depth=None) -> list[str]:
    """The files worth watching under a folder (no dependencies, builds or version control), at most
    `depth` folders down (None: all the way)."""
    found = []
    top = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if depth is not None and root.rstrip(os.sep).count(os.sep) - top >= depth - 1:
            dirs[:] = []  # depth 1: just this folder; 2: it and the folders right inside it
        for name in files:
            if not name.startswith(".") and not name.endswith((".swp", "~", ".tmp", ".log")):
                found.append(os.path.join(root, name))
                if len(found) >= limit:
                    return found
    return found


class FolderWatcher(QObject):
    """Polls a folder's files in a background thread; `changed` fires once per burst of saves."""

    changed = pyqtSignal(str)  # the file that changed

    def __init__(self, folder, parent=None, depth=None):
        super().__init__(parent)
        self.folder = folder
        self.depth = depth
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _snapshot(self, files):
        out = {}
        for path in files:
            try:
                out[path] = os.stat(path).st_mtime_ns
            except OSError:
                pass
        return out

    def _run(self):
        files = project_files(self.folder, depth=self.depth)
        seen = self._snapshot(files)
        scanned = time.monotonic()
        while not self._stop.wait(POLL_SECONDS):
            if time.monotonic() - scanned > RESCAN_SECONDS:  # new files appear now and then
                files, scanned = project_files(self.folder, depth=self.depth), time.monotonic()
            now = self._snapshot(files)
            changed = [p for p, m in now.items() if seen.get(p) != m] + [p for p in seen if p not in now]
            seen = now
            if changed and not self._stop.is_set():
                self.changed.emit(changed[0])


class ConsoleDrawer(QFrame):
    """The panel under a dev page: its console, and a line to run JavaScript in it."""

    def __init__(self, devmode, parent):
        super().__init__(parent)
        self.devmode = devmode
        self.setObjectName("DevConsole")
        self.setFixedHeight(DRAWER_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 8)
        layout.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        title = QLabel("Console")
        title.setObjectName("DevTitle")
        self.watch_btn = QPushButton()
        self.watch_btn.setObjectName("DevButton")
        self.watch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.watch_btn.clicked.connect(devmode.toggle_watch)
        clear = QPushButton("Clear")
        clear.setObjectName("DevButton")
        clear.clicked.connect(devmode.clear)
        tools = QPushButton("DevTools")
        tools.setObjectName("DevButton")
        tools.clicked.connect(devmode.browser.open_devtools)
        close = QToolButton()
        close.setObjectName("BannerClose")
        close.setIcon(qta.icon("ph.x", color=devmode.browser.theme.panel_text))
        close.clicked.connect(devmode.toggle_drawer)
        top.addWidget(title)
        top.addStretch(1)
        for widget in (self.watch_btn, clear, tools, close):
            top.addWidget(widget)
        layout.addLayout(top)
        self.log = QTextEdit()
        self.log.setObjectName("DevLog")
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(MAX_LINES)
        layout.addWidget(self.log, 1)
        self.input = QLineEdit()
        self.input.setObjectName("DevInput")
        self.input.setPlaceholderText("› Run JavaScript in this page (↑ for history)")
        self.input.returnPressed.connect(self._run)
        self.input.installEventFilter(self)
        self._history, self._at = [], 0
        layout.addWidget(self.input)

    def _run(self):
        source = self.input.text().strip()
        if source:
            self._history.append(source)
            self._at = len(self._history)
            self.input.clear()
            self.devmode.evaluate(source)

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress and self._history:
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._at = max(0, min(len(self._history), self._at + (-1 if event.key() == Qt.Key.Key_Up else 1)))
                self.input.setText(self._history[self._at] if self._at < len(self._history) else "")
                return True
        return False

    def show_entries(self, entries):
        self.log.clear()
        for entry in entries:
            self.add(entry)

    def add(self, entry):
        level, message, line, source = entry
        theme = self.devmode.browser.theme
        color = {"error": "#e5484d", "warn": "#d9a400", "result": theme.accent.name(),
                 "input": theme.panel_text.name()}.get(level)
        where = ""
        if source and level in ("error", "warn"):
            where = f' <span style="opacity:.55">{html.escape(Path(QUrl(source).path()).name)}:{line}</span>'
        prefix = {"input": "› ", "result": "← "}.get(level, "")
        style = f' style="color:{color}"' if color else ""
        self.log.append(f'<span{style}>{html.escape(prefix + message)}</span>{where}')

    def set_watching(self, folder):
        self.watch_btn.setText(f"Reloading on save · {Path(folder).name}" if folder else "Reload on Save…")
        self.watch_btn.setToolTip(f"Watching {folder} — click to stop" if folder else
                                  "Pick your project folder: the page reloads whenever a file in it changes")


class DevMode(QObject):
    changed = pyqtSignal()  # error counts or watching changed: the URL pill updates

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._logs: dict[int, deque] = {}     # id(view) → recent console entries
        self._errors: dict[int, int] = {}     # id(view) → errors since it last loaded
        self._watchers: dict[str, FolderWatcher] = {}  # origin → watcher
        self._users: dict[str, set[int]] = {}           # origin → id(view) of the pages it's for
        self.drawer = None

    @property
    def settings(self):
        return self.browser.store.settings

    # --- Pages -----------------------------------------------------------------------------------

    def attach(self, view):
        page = view.page()
        if hasattr(page, "console_entry"):
            page.console_entry.connect(lambda level, message, line, source, v=view:
                                       self._console(v, level, message, line, source))
        view.loadStarted.connect(lambda v=view: self._load_started(v))
        view.urlChanged.connect(lambda url, v=view: self._url_changed(v, url.toString()))
        view.destroyed.connect(lambda _=None, key=id(view): (self._logs.pop(key, None), self._errors.pop(key, None),
                                                             self._release(key)))

    def _url_changed(self, view, url):
        self._release(id(view))  # whatever it was watching before
        if not view.page().profile().isOffTheRecord():
            self._ensure_watch(url, id(view))
        if view is self.browser._current_view():
            self.update_for_current()  # leaving localhost for a website closes the drawer

    def _console(self, view, level, message, line, source):
        if not is_dev_url(view.url().toString()):
            return
        entry = (LEVELS.get(int(level), "log"), message, line, source)
        self._logs.setdefault(id(view), deque(maxlen=MAX_LINES)).append(entry)
        if entry[0] == "error":
            self._errors[id(view)] = self._errors.get(id(view), 0) + 1
            if view is self.browser._current_view():
                self.changed.emit()
        if self.drawer is not None and self.drawer.isVisible() and view is self.browser._current_view():
            self.drawer.add(entry)

    def _load_started(self, view):
        self._errors.pop(id(view), None)
        log = self._logs.get(id(view))
        if log is not None:
            log.append(("info", "— reloaded —", 0, ""))
        if view is self.browser._current_view():
            self.changed.emit()
            self._refresh_drawer()

    def errors(self, view) -> int:
        return self._errors.get(id(view), 0) if view is not None else 0

    # --- The drawer --------------------------------------------------------------------------------

    def drawer_open(self) -> bool:
        return self.settings.get("dev_console_open", False)

    def toggle_drawer(self):
        self.settings["dev_console_open"] = not self.drawer_open()
        self.browser.store.schedule_save()
        self.update_for_current()

    def update_for_current(self):
        """Show the drawer under the current page if it's a dev page and the drawer is open."""
        view = self.browser._current_view()
        show = view is not None and is_dev_url(view.url().toString()) and self.drawer_open()
        content = self.browser.content
        if show and self.drawer is None:
            self.drawer = ConsoleDrawer(self, content)
        if self.drawer is not None:
            content.set_drawer(self.drawer if show else None)
            if show:
                self._refresh_drawer()
        self.changed.emit()

    def _refresh_drawer(self):
        view = self.browser._current_view()
        if self.drawer is None or view is None:
            return
        self.drawer.show_entries(self._logs.get(id(view), ()))
        self.drawer.set_watching(self.watching(view.url().toString()))

    def clear(self):
        view = self.browser._current_view()
        if view is not None:
            self._logs.pop(id(view), None)
            self._errors.pop(id(view), None)
        self._refresh_drawer()
        self.changed.emit()

    def evaluate(self, source):
        view = self.browser._current_view()
        if view is None or self.drawer is None:
            return
        self.drawer.add(("input", source, 0, ""))
        # Run like the DevTools console: in the page itself, showing the result (or the error).
        # eval() gives us the error message; a page whose CSP forbids eval runs the code directly
        wrapped = ("(() => { try { const r = (0, eval)(%s); "
                   "return (typeof r === 'object' && r !== null) ? (() => { try { return JSON.stringify(r, null, 1) } "
                   "catch (e) { return String(r) } })() : String(r); } "
                   "catch (e) { return '%s' + e }})()") % (json.dumps(source), FAILED)

        def show(result, v=view):
            if self.drawer is None or v is not self.browser._current_view():
                return
            text = "undefined" if result is None else str(result)
            if text.startswith(FAILED + "EvalError"):
                v.page().runJavaScript(source, lambda r: self.drawer is not None and self.drawer.add(
                    ("result", json.dumps(r, default=str) if isinstance(r, (dict, list)) else
                     "undefined" if r is None else str(r), 0, "")))
            elif text.startswith(FAILED):
                self.drawer.add(("error", text[len(FAILED):], 0, ""))
            else:
                self.drawer.add(("result", text, 0, ""))
        view.page().runJavaScript(wrapped, show)

    # --- Reload on Save ----------------------------------------------------------------------------

    def watching(self, url) -> str | None:
        if not is_dev_url(url):
            return None
        if QUrl(url).scheme() == "file":
            path = Path(QUrl(url).toLocalFile())
            # A web page you're writing, not any file you opened (a PDF in Downloads, say), and never a
            # folder that holds everything
            if path.suffix.lower() not in WEB_FILES or path.parent in _BROAD_FOLDERS:
                return None
            return str(path.parent)
        return self.settings.get("dev_watch", {}).get(origin_of(url))

    def _ensure_watch(self, url, user=None):
        folder = self.watching(url)
        if not folder or not os.path.isdir(folder):
            return
        origin = origin_of(url)
        if origin not in self._watchers:
            # A page from a file watches its own folder and the one below (its css/, js/), not a whole tree
            depth = 2 if QUrl(url).scheme() == "file" else None
            watcher = FolderWatcher(folder, self, depth=depth)
            watcher.changed.connect(lambda path, o=origin: self._file_changed(o, path))
            self._watchers[origin] = watcher
        if user is not None:
            self._users.setdefault(origin, set()).add(user)

    def _release(self, user):
        """A page closed or went elsewhere: stop the watchers nothing else is using."""
        for origin, users in list(self._users.items()):
            users.discard(user)
            if not users:
                del self._users[origin]
                watcher = self._watchers.pop(origin, None)
                if watcher is not None:
                    watcher.stop()

    def toggle_watch(self):
        view = self.browser._current_view()
        if view is None:
            return
        url = view.url().toString()
        origin = origin_of(url)
        if QUrl(url).scheme() == "file":
            self.browser.toast("Pages opened from a file always reload when it changes")
            return
        rules = self.settings.setdefault("dev_watch", {})
        if origin in rules:
            rules.pop(origin)
            watcher = self._watchers.pop(origin, None)
            if watcher is not None:
                watcher.stop()
            self.browser.toast("Stopped reloading on save")
        else:
            folder = QFileDialog.getExistingDirectory(self.browser, "Reload this page when files in… change",
                                                      str(Path.home()))
            if not folder:
                return
            rules[origin] = folder
            self._ensure_watch(url, id(view))
            self.browser.toast(f"Reloading on save: {Path(folder).name}")
        self.browser.store.schedule_save()
        self._refresh_drawer()

    def _file_changed(self, origin, path):
        reloaded = 0
        for tab in self.browser.store.all_tabs():
            view = tab.view
            if hasattr(view, "page") and is_dev_url(view.url().toString()) and origin_of(view.url().toString()) == origin:
                view.reload()
                reloaded += 1


    def shutdown(self):
        for watcher in self._watchers.values():
            watcher.stop()
        self._watchers.clear()

