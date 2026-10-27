# The welcome, on a first launch: bring your things over from the browser you used before (bookmarks, history,
# passwords; Arc's Spaces and tabs), then make Eclipse your own (default browser, your Space's colour).
# Any time later: ⌘T or File → Import from Another Browser…
#
# Nothing is brought over unless you tick it, and passwords only after macOS asks you about that browser's key.

import os
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget,
                             QToolButton, QVBoxLayout, QWidget)

from . import brand, browser_import, macos, password_store, storage
from .browser_import import SAFARI
from .features import Feature
from .motion import FadesIn
from .theme import PALETTE, stylesheet


def _count(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


class _Secret(QObject):
    got = pyqtSignal(object)


def sources(window) -> list[dict]:
    """What could come over from this Mac: {name, what, passwords, kind, file}."""
    with_passwords = set(password_store.browsers_installed())
    found = []
    arc = window.feature("arc_import")
    if arc is not None:
        from .arc_import import arc_installed
        if arc_installed():
            found.append({"name": "Arc", "what": "Spaces, tabs, Favorites and history", "kind": "arc",
                          "passwords": "Arc" in with_passwords})
    for name in browser_import.chromium_installed():
        found.append({"name": name, "what": "Bookmarks and history", "kind": "chromium",
                      "passwords": name in with_passwords})
    if browser_import.safari_installed():
        readable = os.access(SAFARI / "Bookmarks.plist", os.R_OK)
        try:
            readable = readable and bool((SAFARI / "Bookmarks.plist").read_bytes()[:1])
        except OSError:
            readable = False  # (macOS's Full Disk Access: Safari's own files aren't Eclipse's to read)
        found.append({"name": "Safari", "kind": "safari" if readable else "safari-file", "passwords": False,
                      "what": "Bookmarks and history" if readable else
                      "Bookmarks, from the file Safari makes: File → Export → Bookmarks…"})
    return found


class Welcome(FadesIn, QWidget):
    def __init__(self, window):
        super().__init__(None, Qt.WindowType.Window)
        self.window_ = window
        self.setWindowTitle(f"Welcome to {brand.NAME}")
        self.setProperty("panel", True)
        self.setStyleSheet(stylesheet(window.theme))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedWidth(540)
        self.pages = QStackedWidget()
        self.pages.currentChanged.connect(lambda _: QTimer.singleShot(0, self._fit))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.addWidget(self.pages)
        self.found = sources(window)
        self.picks = []  # (source, its checkbox, its passwords' checkbox or None)
        self.summary = []
        self._queue = []
        self.pages.addWidget(self._import_page())
        self.pages.addWidget(self._yours_page())
        if not self.found:
            self.pages.setCurrentIndex(1)

    def _fit(self):
        """As tall as the page showing needs (the summary can add lines)."""
        current = self.pages.currentWidget()
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            page.setSizePolicy(page.sizePolicy().horizontalPolicy(),
                               page.sizePolicy().Policy.Preferred if page is current else page.sizePolicy().Policy.Ignored)
        self.adjustSize()

    # --- Bring your things over -------------------------------------------------------------------------

    def _heading(self, text, detail):
        title = QLabel(text)
        title.setObjectName("WelcomeTitle")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        about = QLabel(detail)
        about.setObjectName("Hint")
        about.setWordWrap(True)
        return title, about

    def _import_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        for widget in self._heading(f"Welcome to {brand.NAME}",
                                    "Bring your things over from the browser you used before. Nothing comes over "
                                    "unless you tick it, and you can do this again any time from the File menu."):
            v.addWidget(widget)
        v.addSpacing(6)
        for source in self.found:
            box = QCheckBox(f"{source['name']}: {source['what']}")
            box.setChecked(source["kind"] != "safari-file")
            v.addWidget(box)
            passwords = None
            if source["passwords"]:
                passwords = QCheckBox(f"{source['name']}'s saved passwords (macOS will ask about its key: Allow)")
                passwords.setChecked(True)
                passwords.setStyleSheet("margin-left: 26px;")
                v.addWidget(passwords)
            if source["kind"] == "safari-file":
                choose = QPushButton("Choose the Exported File…")
                choose.clicked.connect(lambda _=False, s=source, b=box: self._choose_file(s, b))
                row = QHBoxLayout()
                row.setContentsMargins(26, 0, 0, 0)
                row.addWidget(choose)
                row.addStretch(1)
                v.addLayout(row)
                note = QLabel("Safari's passwords: in the Passwords app, File → Export All Passwords, then "
                              "Settings → Passwords & Passkeys → Import.")
                note.setObjectName("Hint")
                note.setWordWrap(True)
                note.setStyleSheet("margin-left: 26px;")
                v.addWidget(note)
            self.picks.append((source, box, passwords))
        v.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        skip = QPushButton("Skip")
        skip.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        skip.setMinimumHeight(32)
        self.go = QPushButton("Import")
        self.go.setMinimumHeight(32)
        self.go.setObjectName("PermissionAllow")
        self.go.setDefault(True)
        self.go.clicked.connect(self.bring_over)
        buttons.addWidget(skip)
        buttons.addWidget(self.go)
        v.addLayout(buttons)
        return page

    def _choose_file(self, source, box):
        path, _ = QFileDialog.getOpenFileName(self, "Safari's Bookmarks", os.path.expanduser("~/Downloads"),
                                              "Exported bookmarks (*.html *.htm)")
        if path:
            source["file"] = path
            box.setChecked(True)

    def bring_over(self):
        """Everything ticked: bookmarks, history and Arc's setup straight away, then each browser's passwords in
        turn (each asks macOS for that browser's key first)."""
        window = self.window_
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for source, box, passwords in self.picks:
                if box.isChecked():
                    try:
                        self._bring(source)
                    except Exception as error:  # (Arc's import, say: the rest still come)
                        self.summary.append(f"{source['name']}: couldn't be brought over ({error})")
                if passwords is not None and passwords.isChecked():
                    self._queue.append(source["name"])
        finally:
            QApplication.restoreOverrideCursor()
        window.apply_theme()
        window._restore_space_tab()
        self.go.setEnabled(False)
        self._next_passwords()

    def _bring(self, source):
        window = self.window_
        try:
            if source["kind"] == "arc":
                from .arc_import import read_arc
                arc = read_arc()
                window.feature("arc_import").bring_in(arc)
                self.summary.append(f"Arc: {_count(len(arc.spaces), 'Space')} and {_count(arc.tab_count, 'tab')}")
                return
            if source["kind"] == "chromium":
                found = browser_import.read_chromium(source["name"])
            elif source["kind"] == "safari":
                found = browser_import.read_safari()
            elif source.get("file"):
                found = browser_import.read_bookmarks_file(Path(source["file"]))
            else:
                return  # (Safari, without its file chosen)
        except Exception as error:  # (a damaged file must not stop the rest coming over)
            self.summary.append(f"{source['name']}: couldn't be read ({error})")
            return
        before = list(window.store.all_tabs())
        browser_import.apply(window.store, found)
        remaining = set(map(id, window.store.all_tabs()))
        for tab in before:  # (bookmarks imported again: the old ones' pages go)
            if id(tab) not in remaining:
                window._remove_view(tab)
        if window.current_tab is not None and id(window.current_tab) not in remaining:
            window.current_tab = None
        parts = [_count(found.bookmark_count, "bookmark") if found.bookmark_count else "",
                 _count(len(found.history), "page") + " of history" if found.history else ""]
        self.summary.append(f"{source['name']}: " + (" and ".join(p for p in parts if p) or "nothing to bring"))

    def _next_passwords(self):
        if not self._queue:
            self._finished()
            return
        name = self._queue.pop(0)
        secret = self._secret = _Secret(self)
        secret.got.connect(lambda key, n=name: self._passwords(n, key))
        threading.Thread(target=lambda: secret.got.emit(password_store.browser_secret(name)), daemon=True).start()

    def _passwords(self, name, key):
        try:
            if key is None:
                self.summary.append(f"{name}'s passwords: not brought over (macOS wasn't allowed to share its key)")
            else:
                added, changed, _same = password_store.import_rows(password_store.read_browser(name, key), name)
                self.summary.append(f"{name}'s passwords: {added + changed} saved")
        except Exception as error:  # (locked, a damaged database: say so, and go on to the next)
            self.summary.append(f"{name}'s passwords: couldn't be brought over ({error or 'locked'})")
        finally:
            self._next_passwords()

    def _finished(self):
        self.result.setText("Brought over: " + "; ".join(self.summary) + "." if self.summary else "")
        self.result.setVisible(bool(self.summary))
        self.pages.setCurrentIndex(1)
        QTimer.singleShot(0, self._fit)

    # --- Make it yours ------------------------------------------------------------------------------------

    def _yours_page(self):
        window = self.window_
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        for widget in self._heading("Make it yours", "Two quick things. Both are in Settings later, too."):
            v.addWidget(widget)
        self.result = QLabel()
        self.result.setObjectName("Hint")
        self.result.setWordWrap(True)
        self.result.hide()
        v.addWidget(self.result)
        v.addSpacing(8)
        default = QHBoxLayout()
        already = macos.default_browser() == macos.APP_BUNDLE_ID
        label = QLabel(f"{brand.NAME} is your default browser." if already else
                       f"Open links from other apps in {brand.NAME}.")
        label.setWordWrap(True)
        default.addWidget(label, 1)
        if not already:
            make = QPushButton("Make Default")
            make.clicked.connect(lambda: (window.set_as_default_browser(), make.setEnabled(False)))
            default.addWidget(make)
        v.addLayout(default)
        v.addSpacing(8)
        v.addWidget(QLabel(f"Your Space's colour ({window.store.active_space.name}):"))
        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        self.swatches = []
        for name, color in PALETTE:
            swatch = QToolButton()
            swatch.setToolTip(name)
            swatch.setAccessibleName(f"{name} colour")
            swatch.setFixedSize(30, 30)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setCheckable(True)
            swatch.setChecked(color == window.store.active_space.color)
            swatch.setStyleSheet(f"QToolButton {{ background: {color}; border-radius: 15px; border: none; }}"
                                 f"QToolButton:hover, QToolButton:checked {{ border: 3px solid palette(text); }}")
            swatch.clicked.connect(lambda _=False, c=color: self._colour(c))
            swatches.addWidget(swatch)
            self.swatches.append(swatch)
        swatches.addStretch(1)
        v.addLayout(swatches)
        v.addStretch(1)
        done = QHBoxLayout()
        done.addStretch(1)
        finish = QPushButton("Start Browsing")
        finish.setMinimumHeight(32)
        finish.setObjectName("PermissionAllow")
        finish.clicked.connect(self.close)
        done.addWidget(finish)
        v.addLayout(done)
        return page

    def _colour(self, color):
        window = self.window_
        window.set_space_color(window.store.active_space, color)
        self.setStyleSheet(stylesheet(window.theme))
        for swatch, (_name, each) in zip(self.swatches, PALETTE):
            swatch.setChecked(each == color)


class WelcomeFeature(Feature):
    """The first launch's welcome, and importing from another browser any time."""
    name = "welcome"

    def __init__(self, window):
        super().__init__(window)
        self.welcome = None

    def ready(self):
        if self.window.store.is_new and not storage.throwaway():  # (not tests, not --fresh)
            QTimer.singleShot(800, self.show)

    def show(self):
        if self.welcome is not None:
            try:
                self.welcome.raise_()
                self.welcome.activateWindow()
                return
            except RuntimeError:  # (closed: deleted)
                pass
        self.welcome = Welcome(self.window)
        self.welcome.destroyed.connect(lambda: setattr(self, "welcome", None))
        self.welcome.show()

    def commands(self):
        return [("Import from Another Browser…", "ph.arrow-square-in", self.show)]

    def menus(self, add, menus, bar):
        add(menus["File"], "Import from Another Browser…", "", self.show)


FEATURE = WelcomeFeature
