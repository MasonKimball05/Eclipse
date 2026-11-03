# The update window: a new version of Eclipse is ready. What it is, what's new in it (the release's notes),
# and Update & Restart, which downloads it right here with a progress bar and restarts into it. Later closes
# it; Skip This Version stops it being offered again by itself (Check Now in Settings still shows it).
#
# It follows the updater's status (self_update.py), so the download, and anything that goes wrong, show here.

import re

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from . import brand, native
from .motion import FadesIn
from .theme import quiet_scrollbars, stylesheet

SKIP_SETTING = "skipped_update_build"


def notes_markdown(found: dict) -> str:
    """What's new, as Markdown: a release's own notes, or a checkout's new commits."""
    if found.get("notes"):
        # A release's headings ("This week", "Changes") as bold lines: full-size ones would outsize "What's new" above
        return re.sub(r"(?m)^#{1,6}\s+(.+?)\s*$", r"**\1**", found["notes"])
    lines = [f"- {subject}" for subject in found.get("subjects", [])]
    more = found.get("behind", 0) - len(found.get("subjects", []))
    if more > 0:
        lines.append(f"- …and {more} more")
    return "\n".join(lines) or "Small fixes and improvements."


def about_text(found: dict, current: str) -> str:
    """The line under the title: which version, how big, and what's running now."""
    size = f" · {found['size'] / 1_000_000:.0f} MB" if found.get("size") else ""
    behind = found.get("behind", 0)
    return ((f"{found.get('version', 'A new version')}{size}." if "build" in found else
             f"{behind} new change{'s' if behind != 1 else ''} on GitHub.") +
            f" {you_have(current)} Your tabs come back after the restart.").replace("  ", " ")


def status_view(status: dict) -> dict:
    """What the window shows for the updater's status: the progress bar (a percent, or None), the line under it,
    and whether its buttons can be used. {} for a status it shows nothing new for."""
    state = status.get("state")
    if state == "downloading":
        percent = status.get("percent", 0)
        return {"progress": percent, "busy": True,
                "status": f"Downloading… {percent}%. {brand.NAME} restarts when it's checked and ready."}
    if state == "failed":
        return {"progress": None, "busy": False, "go_label": "Try Again",
                "status": f"The update didn't work: {status.get('reason', '')}"}
    return {}


def skip(updater, found: dict):
    """Skip This Version: not offered again by itself (Check Now still shows it)."""
    settings = updater.browser.store.settings
    settings[SKIP_SETTING] = found.get("build")
    updater.browser.store.schedule_save()
    updater.browser.toast(f"{found.get('version', 'That version')} won't be offered again "
                          "(Settings → General → Check Now still finds it)")


def you_have(current: str) -> str:
    """What's running, in a few words: "You have 2026.10.27", or that it's running from source."""
    if current.startswith(f"{brand.NAME} "):
        return f"You have {current.removeprefix(brand.NAME + ' ').split(' (build')[0]}."
    return "You're running it from source." if current.startswith("From source") else ""


class UpdateWindow(FadesIn, QWidget):
    def __init__(self, updater, found, current):
        super().__init__(None, Qt.WindowType.Window)
        self.updater, self.found = updater, found
        browser = updater.browser
        self.setWindowTitle(f"{brand.NAME} Update")
        self.setProperty("panel", True)
        self.setStyleSheet(stylesheet(browser.theme))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(500, 380)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 18)
        v.setSpacing(10)
        title = QLabel(f"A new version of {brand.NAME} is ready")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        v.addWidget(title)
        release = "build" in found
        about = QLabel(about_text(found, current))
        about.setObjectName("Hint")
        about.setWordWrap(True)
        v.addWidget(about)
        heading = QLabel("What's new")
        heading.setStyleSheet("font-weight: 600; font-size: 14px; margin-top: 6px;")
        v.addWidget(heading)
        self.notes = QTextBrowser()
        self.notes.setObjectName("Answer")
        self.notes.setOpenExternalLinks(False)
        self.notes.setMarkdown(notes_markdown(found))
        self.notes.document().setDocumentMargin(0)  # (its text lines up with the title's)
        quiet_scrollbars(self.notes)
        v.addWidget(self.notes, 1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.hide()
        v.addWidget(self.bar)
        self.state = QLabel()
        self.state.setObjectName("Hint")
        self.state.setWordWrap(True)
        self.state.hide()
        v.addWidget(self.state)
        buttons = QHBoxLayout()
        self.skip = QPushButton("Skip This Version")
        self.skip.setFlat(True)
        self.skip.setVisible(release)
        self.skip.clicked.connect(self._skip)
        self.later = QPushButton("Later")
        self.later.clicked.connect(self.close)
        self.go = QPushButton("Update && Restart")
        self.go.setObjectName("PermissionAllow")
        self.go.setDefault(True)
        self.go.clicked.connect(self._update)
        buttons.addWidget(self.skip)
        buttons.addStretch(1)
        for button in (self.later, self.go):
            button.setMinimumHeight(32)
            buttons.addWidget(button)
        v.addLayout(buttons)
        updater.status_changed.connect(self._status)

    def _skip(self):
        skip(self.updater, self.found)
        self.close()

    def _update(self):
        self.go.setEnabled(False)
        self.skip.setEnabled(False)
        self.state.setText("Getting it ready…")
        self.state.show()
        self.updater.update_and_restart()

    def _status(self):
        if sip.isdeleted(self):
            return
        view = status_view(self.updater.status)
        if not view:
            return
        self.bar.setVisible(view["progress"] is not None)
        self.bar.setValue(view["progress"] or 0)
        self.state.setText(view["status"])
        self.state.show()
        if not view["busy"]:
            self.go.setEnabled(True)
            self.go.setText(view["go_label"])
            self.skip.setEnabled(True)


class NativeUpdateWindow(QObject):
    """The same window drawn natively (SwiftUI, native/Sources/Pages.swift), where it's available."""

    def __init__(self, updater, found, current):
        super().__init__(updater)
        self.updater, self.found = updater, found
        notes = found.get("notes") or notes_markdown(found)  # (its headings as headings: the page sizes them)
        self.page = native.NativePage({
            "page": "update", "title": f"{brand.NAME} Update", "heading": f"A new version of {brand.NAME} is ready",
            "about": about_text(found, current), "sections": native.note_sections(notes),
            "can_skip": "build" in found, "width": 520, "height": 440, "min_height": 380,
            **native.theme_state(updater.browser.theme)}, self)
        self.page.action.connect(self._act)
        self.page.closed.connect(self.deleteLater)  # (sip.isdeleted() then tells the updater it's gone)
        updater.status_changed.connect(self._status)

    def _act(self, name, _event):
        if name == "skip":
            skip(self.updater, self.found)
            self.close()
        elif name == "later":
            self.close()
        elif name == "update":
            self.page.update({"busy": True, "status": "Getting it ready…"})
            self.updater.update_and_restart()

    def _status(self):
        if not sip.isdeleted(self) and (view := status_view(self.updater.status)):
            self.page.update(view)

    def show(self):
        pass  # (open from the start)

    def raise_(self):
        self.page.front()

    def activateWindow(self):
        self.page.front()

    def close(self):
        self.page.close()

    def isVisible(self) -> bool:
        return self.page.is_open()


def window_for(updater, found, current):
    """The update window, natively where that's built, else Qt's."""
    return (NativeUpdateWindow if native.available() else UpdateWindow)(updater, found, current)
