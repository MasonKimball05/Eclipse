# Send Feedback: telling Eclipse's maker what went wrong (or what you'd like), with what's needed to fix it.
#
# A small window takes what happened, and adds the technical details if you leave them in: Eclipse's version,
# macOS's, the Mac's chip, and the recent error and crash logs (crash.py), with your home folder's path shortened
# to ~ so your account's name isn't in it. You see all of it before it goes anywhere, and nothing is sent by
# itself: Open GitHub Issue starts an issue on Eclipse's GitHub with it filled in (you send it), and Copy Report
# puts it on the clipboard, for a message or an email instead.

import datetime
import platform
import re
import plistlib
import time
from pathlib import Path
from urllib.parse import urlencode

from PyQt6.QtCore import QT_VERSION_STR, Qt
from PyQt6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
                             QWidget)

from . import brand, verified
from .crash import logs_dir
from .motion import FadesIn
from .theme import stylesheet

ISSUES = f"https://github.com/{brand.ORGANIZATION}/{brand.NAME}/issues/new"
RECENT_DAYS = 14
LOG_LINES = 60
MAX_URL = 7500  # (GitHub, and browsers, stop taking longer addresses well before 8,000 characters)


def version() -> str:
    bundle = verified.app_bundle()
    if bundle is not None:
        try:
            info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
            return f"{info.get('CFBundleShortVersionString', '?')} (build {info.get('CFBundleVersion', '?')})"
        except (OSError, plistlib.InvalidFileException):
            pass
    return "from source"


def _tail(path: Path, lines=LOG_LINES) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def _recent(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < RECENT_DAYS * 86400 and path.stat().st_size > 0
    except OSError:
        return False


def details(folder: Path | None = None) -> str:
    """The technical part: versions, then the recent error log and the newest crash's stack."""
    folder = folder or logs_dir()
    lines = [f"{brand.NAME} {version()}", f"macOS {platform.mac_ver()[0] or '?'} ({platform.machine()})",
             f"Qt {QT_VERSION_STR}", f"Sent {datetime.datetime.now():%Y-%m-%d %H:%M}"]
    errors = folder / "errors.log"
    if _recent(errors):
        lines += ["", "Recent errors (logs/errors.log):", _tail(errors)]
    crashes = sorted((p for p in folder.glob("native-crash-*.log") if _recent(p)), key=lambda p: p.stat().st_mtime)
    if crashes:
        lines += ["", f"Last crash ({crashes[-1].name}):", _tail(crashes[-1])]
    return _private("\n".join(lines))


def _private(text: str) -> str:
    """Without what's yours: your home folder as ~ (it holds your account's name), web addresses without their
    query or fragment (where sign-in tokens and search terms go), and email addresses hidden."""
    text = text.replace(str(Path.home()), "~")
    text = re.sub(r"(https?://[^\s?#\"'<>]+)[?#][^\s\"'<>]*", r"\1?…", text)
    return re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "[email]", text)


def report(said: str, with_details: bool, folder: Path | None = None) -> str:
    body = said.strip() or "(nothing written)"
    if with_details:
        body += "\n\n---\n" + details(folder)
    return body


def issue_url(title: str, body: str) -> str:
    """A new GitHub issue with it filled in (trimmed from the logs' end when it's too long for an address)."""
    query = lambda text: urlencode({"title": title, "body": text})  # noqa: E731
    while len(ISSUES) + 1 + len(query(body)) > MAX_URL and len(body) > 200:
        body = body[:int(len(body) * 0.85)].rstrip() + "\n…(trimmed: Copy Report has all of it)"
    return f"{ISSUES}?{query(body)}"


class FeedbackWindow(FadesIn, QWidget):
    def __init__(self, browser, crashed=False):
        super().__init__(None, Qt.WindowType.Window)
        self.browser = browser
        self.crashed = crashed
        self.setWindowTitle("Send Feedback")
        self.setProperty("panel", True)
        self.setStyleSheet(stylesheet(browser.theme))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(520, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(10)
        title = QLabel(f"{brand.NAME} quit unexpectedly" if crashed else "Send Feedback")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        v.addWidget(title)
        about = QLabel("What were you doing when it happened? Anything helps." if crashed else
                       "What went wrong, or what would you like? Nothing is sent until you send it.")
        about.setObjectName("Hint")
        about.setWordWrap(True)
        v.addWidget(about)
        self.said = QPlainTextEdit()
        self.said.setPlaceholderText("I was… / It would be nice if…")
        v.addWidget(self.said, 1)
        self.include = QCheckBox("Include technical details: version, macOS, recent error and crash logs")
        self.include.setChecked(True)
        v.addWidget(self.include)
        self.preview = QPlainTextEdit(details())
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(160)
        self.preview.hide()
        show = QPushButton("See What's Included")
        show.setFlat(True)
        show.clicked.connect(lambda: (self.preview.setVisible(not self.preview.isVisible()),
                                      show.setText("Hide Details" if self.preview.isVisible()
                                                   else "See What's Included")))
        self.include.toggled.connect(show.setEnabled)
        row = QHBoxLayout()
        row.addWidget(show)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(self.preview)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        copy = QPushButton("Copy Report")
        copy.clicked.connect(self.copy)
        issue = QPushButton("Open GitHub Issue")
        issue.setObjectName("PermissionAllow")
        issue.clicked.connect(self.open_issue)
        for button in (copy, issue):
            button.setMinimumHeight(32)
            buttons.addWidget(button)
        v.addLayout(buttons)

    def text(self) -> str:
        return report(self.said.toPlainText(), self.include.isChecked())

    def title(self) -> str:
        first = (self.said.toPlainText().strip().splitlines() or [""])[0][:80]
        return ("Crash: " if self.crashed else "Feedback: ") + (first or f"{brand.NAME} {version()}")

    def copy(self):
        QApplication.clipboard().setText(f"{self.title()}\n\n{self.text()}")
        self.browser.toast("Report copied: paste it into a message or an email")

    def open_issue(self):
        self.browser.open_url(issue_url(self.title(), self.text()))
        self.browser.toast("Look it over on GitHub, then Submit (you'll need to be signed in)")
        self.close()
