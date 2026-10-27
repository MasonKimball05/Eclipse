# The update window: a new version of Eclipse is ready. What it is, what's new in it (the release's notes),
# and Update & Restart, which downloads it right here with a progress bar and restarts into it. Later closes
# it; Skip This Version stops it being offered again by itself (Check Now in Settings still shows it).
#
# It follows the updater's status (self_update.py), so the download, and anything that goes wrong, show here.

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from . import brand
from .motion import FadesIn
from .theme import quiet_scrollbars, stylesheet

SKIP_SETTING = "skipped_update_build"


def notes_markdown(found: dict) -> str:
    """What's new, as Markdown: a release's own notes, or a checkout's new commits."""
    if found.get("notes"):
        return found["notes"]
    lines = [f"- {subject}" for subject in found.get("subjects", [])]
    more = found.get("behind", 0) - len(found.get("subjects", []))
    if more > 0:
        lines.append(f"- …and {more} more")
    return "\n".join(lines) or "Small fixes and improvements."


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
        size = f" · {found['size'] / 1_000_000:.0f} MB" if found.get("size") else ""
        about = QLabel((f"{found.get('version', 'A new version')}{size}." if release else
                        f"{found.get('behind', 0)} new change{'s' if found.get('behind') != 1 else ''} on GitHub.") +
                       f" {you_have(current)} Your tabs come back after the restart.".replace("  ", " "))
        about.setObjectName("Hint")
        about.setWordWrap(True)
        v.addWidget(about)
        heading = QLabel("What's new")
        heading.setStyleSheet("font-weight: 600; margin-top: 6px;")
        v.addWidget(heading)
        self.notes = QTextBrowser()
        self.notes.setObjectName("Answer")
        self.notes.setOpenExternalLinks(False)
        self.notes.setMarkdown(notes_markdown(found))
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
        settings = self.updater.browser.store.settings
        settings[SKIP_SETTING] = self.found.get("build")
        self.updater.browser.store.schedule_save()
        self.updater.browser.toast(f"{self.found.get('version', 'That version')} won't be offered again "
                                   "(Settings → General → Check Now still finds it)")
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
        status = self.updater.status
        state = status.get("state")
        if state == "downloading":
            self.bar.show()
            self.bar.setValue(status.get("percent", 0))
            self.state.setText(f"Downloading… {status.get('percent', 0)}%. {brand.NAME} restarts when it's "
                               "checked and ready.")
            self.state.show()
        elif state == "failed":
            self.bar.hide()
            self.state.setText(f"The update didn't work: {status.get('reason', '')}")
            self.state.show()
            self.go.setEnabled(True)
            self.go.setText("Try Again")
            self.skip.setEnabled(True)
