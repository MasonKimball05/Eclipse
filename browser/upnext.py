# Up Next: your next event today, from Daybook, in a small card at the bottom of the sidebar, the
# way Arc showed your next meeting. A meeting link in the event (Zoom, Meet, Teams, Webex, or any
# address) gets a Join button that opens it here; click the card for Daybook itself.
#
# Daybook writes ~/Library/Application Support/Daybook/hop.json on every refresh (for Hop, the
# launcher); this only reads it.

import datetime
import json
import os
import re
from pathlib import Path

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from . import motion
from .features import Feature
from .timefmt import portable

import qtawesome as qta

FEED = Path.home() / "Library" / "Application Support" / "Daybook" / "hop.json"
SETTING = "up_next"
POLL_MS = 30_000
SOON_MINUTES = 15
MEETING = re.compile(r"https?://[^\s<>\"]*(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|"
                     r"webex\.com|whereby\.com|around\.co|facetime\.apple\.com)[^\s<>\"]*", re.IGNORECASE)
ANY_LINK = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


def _time(value: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return None


def next_event(feed: dict, now: datetime.datetime) -> dict | None:
    """The timed event on now, or the next one today: {title, start, end, place, link, live}."""
    for event in feed.get("today") or []:
        if event.get("allDay"):
            continue
        start, end = _time(event.get("start", "")), _time(event.get("end", ""))
        if start is None or end is None or end <= now:
            continue
        place = event.get("place") or ""
        found = MEETING.search(place) or MEETING.search(event.get("title", "")) or ANY_LINK.search(place)
        return {"title": event.get("title") or "Event", "start": start, "end": end, "place": place,
                "calendar": event.get("calendar", ""), "link": found.group(0).rstrip(".,)") if found else "",
                "live": start <= now}
    return None


def when_text(event: dict, now: datetime.datetime) -> str:
    if event["live"]:
        left = int((event["end"] - now).total_seconds() // 60)
        return f"Now · {left} min left" if left < 60 else f"Now · until {event['end'].strftime(portable('%-I:%M %p'))}"
    minutes = int((event["start"] - now).total_seconds() // 60)
    at = event["start"].strftime(portable("%-I:%M %p"))
    if minutes < 1:
        return f"{at} · starting now"
    if minutes < 60:
        return f"{at} · in {minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{at} · in {hours} h" + (f" {rest} min" if rest and hours < 3 else "")


def read_feed(path=FEED) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


class UpNextCard(QFrame):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("NowPlaying")  # the same quiet card as Now Playing
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 6, 7)
        row.setSpacing(9)
        self.icon = QLabel()
        self.icon.setFixedSize(QSize(18, 18))
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("NowPlayingTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.when = QLabel()
        self.when.setObjectName("NowPlayingSub")
        self.when.setTextFormat(Qt.TextFormat.PlainText)
        text.addWidget(self.title)
        text.addWidget(self.when)
        self.join = QPushButton("Join")
        self.join.setObjectName("UpNextJoin")
        self.join.setCursor(Qt.CursorShape.PointingHandCursor)
        self.join.clicked.connect(controller.join)
        row.addWidget(self.icon)
        row.addLayout(text, 1)
        row.addWidget(self.join)
        self.hide()

    def set_event(self, event, when, soon, theme):
        # On now, or starting soon: in the Space's colour, so it catches your eye
        color = theme.accent if event["live"] or soon else theme.text
        self.icon.setPixmap(qta.icon("ph.broadcast" if event["live"] else "ph.calendar-blank",
                                     color=color).pixmap(18, 18))
        self._full = (event["title"], when)
        self.join.setVisible(bool(event["link"]))
        self.setToolTip("\n".join(x for x in (event["title"], when, event["calendar"], event["place"]) if x)
                        + "\nClick for Daybook")
        self._elide()
        self.show()

    def _elide(self):
        title, when = getattr(self, "_full", ("", ""))
        room = max(self.title.width(), 60)
        self.title.setText(self.title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, room))
        self.when.setText(self.when.fontMetrics().elidedText(when, Qt.TextElideMode.ElideRight, room))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.controller.open_daybook()


class UpNext(QObject):
    def __init__(self, browser, path=FEED):
        super().__init__(browser)
        self.browser = browser
        self.path = path
        self.card: UpNextCard | None = None
        self.event = None
        self._timer = QTimer(self, interval=POLL_MS, timeout=self.refresh)
        self._timer.start()
        QTimer.singleShot(1500, self.refresh)

    @property
    def enabled(self) -> bool:
        return self.browser.store.settings.get(SETTING, True) and os.path.exists(self.path)

    def refresh(self):
        if self.card is None:
            return
        feed = read_feed(self.path) if self.enabled else None
        now = datetime.datetime.now().astimezone()
        self.event = next_event(feed, now) if feed else None
        if self.event is None:
            self._in_or_out(False, self.card.hide)
            return
        soon = (self.event["start"] - now).total_seconds() < SOON_MINUTES * 60
        event, when = self.event, when_text(self.event, now)
        self._in_or_out(True, lambda: self.card.set_event(event, when, soon, self.browser.theme))

    def _in_or_out(self, showing, change):
        """Coming or going, the sidebar crossfades round it (rather than what's below it jumping); just changing, as
        the minutes count down, it simply changes."""
        if showing == (not self.card.isHidden()):
            change()
        else:
            motion.crossfade(self.browser.centralWidget(), self.browser.sidebar, change)

    def join(self):
        if self.event and self.event["link"]:
            self.browser.open_url(self.event["link"])

    def open_daybook(self):
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("daybook://show"))


class UpNextFeature(Feature):
    """Your next event today, from Daybook, at the bottom of the sidebar."""
    name = "up_next"

    def __init__(self, window):
        super().__init__(window)
        self.up_next = UpNext(window)

    def sidebar_cards(self):
        self.up_next.card = UpNextCard(self.up_next)
        return [self.up_next.card]


FEATURE = UpNextFeature
