# Space rules: Spaces that know when they're needed. Right-click a Space → Space Rules…:
#   - On a schedule: Work on weekdays from 9 to 5, say. As the time comes, Eclipse switches to it (asking first,
#     with a toast, if you're in the middle of using Eclipse; quietly if you're not).
#   - When a display's connected: plugging in your office monitor brings Work forward.
#   - Sites blocked while you're in it: in Work, news.example and the like show a quiet "not in Work" page instead
#     (Open Anyway lets one through for 10 minutes).
# Rules are kept in the settings ({space id: rule}), not with the Space, so sync and shared Spaces carry none:
# a schedule's about your day, not the Space. Only a change is acted on, so switching Space by hand sticks until
# the next one.

import datetime
import html
import time

from PyQt6.QtCore import QTime, QTimer, QUrl, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                             QPlainTextEdit, QTimeEdit, QToolButton, QVBoxLayout)

from .features import Feature
from .theme import stylesheet
from .webview import WebPage

SETTING = "space_rules"
CHECK_MS = 30 * 1000
ALLOW_FOR = 10 * 60  # seconds "Open Anyway" lets a blocked site through
DAY_NAMES = ["M", "T", "W", "T", "F", "S", "S"]
WEEKDAYS = [0, 1, 2, 3, 4]


def _minutes(text: str) -> int:
    hours, _, minutes = text.partition(":")
    return int(hours) * 60 + int(minutes or 0)


def on_schedule(rule: dict, now: datetime.datetime) -> bool:
    """Whether now's in the rule's schedule (a schedule past midnight, 22:00 to 02:00, counts from its start day)."""
    days, start, end = rule.get("days"), rule.get("start"), rule.get("end")
    if not days or not start or not end:
        return False
    minute, start, end = now.hour * 60 + now.minute, _minutes(start), _minutes(end)
    if start <= end:
        return now.weekday() in days and start <= minute < end
    yesterday = (now.weekday() - 1) % 7
    return (now.weekday() in days and minute >= start) or (yesterday in days and minute < end)


def blocks(rule: dict, host: str) -> bool:
    host = host.lower().removeprefix("www.")
    return bool(host) and any(host == site or host.endswith("." + site) for site in rule.get("blocked", []))


def clean_sites(text: str) -> list[str]:
    """Sites typed one per line (or pasted as links) as bare hosts: "https://www.x.com/home" → "x.com"."""
    sites = []
    for line in text.replace(",", "\n").splitlines():
        line = line.strip().lower()
        if not line:
            continue
        host = QUrl(line if "://" in line else "https://" + line).host().removeprefix("www.")
        if "." in host and host not in sites:
            sites.append(host)
    return sites


class SpaceRulesFeature(Feature):
    name = "space_rules"

    def __init__(self, window):
        super().__init__(window)
        self.wanted = None        # the Space the rules want now (its id), as last worked out
        self.allowed = {}         # (space id, site) -> until when "Open Anyway" lets it through
        self.timer = QTimer(window, interval=CHECK_MS, timeout=self.check)
        WebPage.blockers.append(self._allow_navigation)
        app = QGuiApplication.instance()
        app.screenAdded.connect(lambda _screen: QTimer.singleShot(500, self.check))
        app.screenRemoved.connect(lambda _screen: QTimer.singleShot(500, self.check))

    def rules(self) -> dict:
        return self.window.store.settings.setdefault(SETTING, {})

    def rule(self, space) -> dict:
        return self.rules().get(space.id, {})

    def set_rule(self, space, rule: dict):
        rule = {k: v for k, v in rule.items() if v}
        if rule:
            self.rules()[space.id] = rule
        else:
            self.rules().pop(space.id, None)
        self.window.store.schedule_save()
        self.wanted = None  # (worked out afresh: a rule that applies now takes effect now)
        self.check()

    def ready(self):
        self.timer.start()
        self.wanted = self.wanted_space()  # (on launch, the Space you left open stays: only later changes act)

    def shutdown(self):
        self.timer.stop()
        if self._allow_navigation in WebPage.blockers:
            WebPage.blockers.remove(self._allow_navigation)

    # --- When ------------------------------------------------------------------------------------

    @staticmethod
    def displays() -> list[str]:
        return [screen.name() for screen in QGuiApplication.screens()]

    def wanted_space(self, now=None) -> str | None:
        """The Space the rules want now: one whose display is connected, else one whose schedule it is."""
        now = now or datetime.datetime.now()
        spaces, displays = self.window.store.spaces, self.displays()
        for space in spaces:
            if self.rule(space).get("display") in displays:
                return space.id
        for space in spaces:
            if on_schedule(self.rule(space), now):
                return space.id
        return None

    def check(self, now=None):
        wanted = self.wanted_space(now)
        if wanted == self.wanted:
            return
        self.wanted = wanted
        space = self.window.store.space(wanted) if wanted else None
        if space is None or space is self.window.store.active_space:
            return
        if QApplication.applicationState() == Qt.ApplicationState.ApplicationActive and self.window.isActiveWindow():
            self.window.toast(f"Time for {space.name}", lambda: self.window.switch_space(space), "Switch")
        else:
            self.window.switch_space(space)  # (you're not using Eclipse: it's ready when you come back)

    # --- Blocked sites ----------------------------------------------------------------------------

    def _space_of(self, page):
        for tab in self.window.store.all_tabs():
            view = tab.view
            if view is not None and hasattr(view, "page") and view.page() is page:
                return (self.window.store.locate(tab) or (None, None))[1]
        return None

    def _allow_navigation(self, page, url: QUrl) -> bool:
        """Whether a tab's page may go to url (a site blocked in the Space it's in shows the blocked page)."""
        if url.scheme() not in ("http", "https"):
            return True
        space = self._space_of(page)
        if space is None or not blocks(self.rule(space), url.host()):
            return True
        site = url.host().lower().removeprefix("www.")
        if self.allowed.get((space.id, site), 0) > time.time():
            return True
        QTimer.singleShot(0, lambda: self._show_blocked(page, space, url))
        return False

    def _show_blocked(self, page, space, url):
        site = url.host().lower().removeprefix("www.")
        e = html.escape
        page.setHtml(
            "<html><body style='font: 15px -apple-system, sans-serif; display: flex; align-items: center; "
            "justify-content: center; height: 90vh; color: #888; text-align: center'><div>"
            f"<div style='font-size: 22px; color: #555; margin-bottom: 8px'>{e(site)} isn't in {e(space.name)}</div>"
            f"You blocked it here, in Space Rules. It opens in your other Spaces.</div></body></html>")
        self.window.toast(f"{site} is blocked in {space.name}", lambda: self.open_anyway(page, space, url),
                          "Open Anyway")

    def open_anyway(self, page, space, url):
        self.allowed[(space.id, url.host().lower().removeprefix("www."))] = time.time() + ALLOW_FOR
        page.load(url)

    # --- Where it's set ---------------------------------------------------------------------------

    def space_actions(self, space) -> list:
        return [("ph.clock", "Space Rules…", lambda: self.edit(space))]

    def edit(self, space):
        dialog = SpaceRulesDialog(self.window, space, self.rule(space), self.displays())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_rule(space, dialog.result_rule())
        return dialog


class SpaceRulesDialog(QDialog):
    def __init__(self, browser, space, rule, displays):
        super().__init__(browser)
        self.setWindowTitle(f"Space Rules · {space.name}")
        self.setStyleSheet(stylesheet(browser.theme))
        self.setProperty("panel", True)
        self.resize(440, 0)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.scheduled = QCheckBox(f"Switch to {space.name} on a schedule")
        self.scheduled.setChecked(bool(rule.get("days")))
        layout.addWidget(self.scheduled)
        days_row = QHBoxLayout()
        self.day_buttons = []
        for i, name in enumerate(DAY_NAMES):
            button = QToolButton()
            button.setText(name)
            button.setCheckable(True)
            button.setChecked(i in rule.get("days", WEEKDAYS))
            button.setFixedSize(30, 28)
            button.setToolTip(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][i])
            self.day_buttons.append(button)
            days_row.addWidget(button)
        days_row.addSpacing(10)
        self.start = QTimeEdit(QTime.fromString(rule.get("start", "09:00"), "HH:mm"))
        self.end = QTimeEdit(QTime.fromString(rule.get("end", "17:00"), "HH:mm"))
        for edit in (self.start, self.end):
            edit.setDisplayFormat("h:mm AP")
        days_row.addWidget(self.start)
        days_row.addWidget(QLabel("to"))
        days_row.addWidget(self.end)
        days_row.addStretch(1)
        layout.addLayout(days_row)

        self.on_display = QCheckBox("Switch to it when this display is connected")
        self.display = QComboBox()
        for name in dict.fromkeys(displays + ([rule["display"]] if rule.get("display") else [])):
            self.display.addItem(name)
        self.on_display.setChecked(bool(rule.get("display")))
        if rule.get("display"):
            self.display.setCurrentText(rule["display"])
        layout.addWidget(self.on_display)
        layout.addWidget(self.display)

        layout.addWidget(QLabel(f"Sites blocked while you're in {space.name} (one per line)"))
        self.blocked = QPlainTextEdit("\n".join(rule.get("blocked", [])))
        self.blocked.setPlaceholderText("news.example.com\nreddit.com")
        self.blocked.setFixedHeight(90)
        layout.addWidget(self.blocked)
        hint = QLabel("Switching by hand always works: a rule only acts as its time comes or its display "
                      "connects. Rules stay on this Mac.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_rule(self) -> dict:
        rule = {"blocked": clean_sites(self.blocked.toPlainText())}
        days = [i for i, b in enumerate(self.day_buttons) if b.isChecked()]
        if self.scheduled.isChecked() and days:
            rule.update(days=days, start=self.start.time().toString("HH:mm"), end=self.end.time().toString("HH:mm"))
        if self.on_display.isChecked() and self.display.currentText():
            rule["display"] = self.display.currentText()
        return rule


FEATURE = SpaceRulesFeature
