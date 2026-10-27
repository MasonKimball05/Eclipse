# Up Next: your next event today, from Daybook, in a small card at the bottom of the sidebar, the
# way Arc showed your next meeting. A meeting link in the event (Zoom, Meet, Teams, Webex, or any
# address) gets a Join button that opens it here; click the card for Daybook itself.
#
# A minute or two before an event with a meeting link starts, a message at the bottom of the page offers
# to join it (never a tab of its own accord). If Eclipse isn't the app you're in then, it waits for you to
# come back, as long as the meeting's still young. Joining can also switch to the Space named like the
# event's calendar ("Work" for a Work calendar). Both are in Settings → Daybook.
#
# Daybook writes ~/Library/Application Support/Daybook/hop.json on every refresh (for Hop, the
# launcher); this only reads it.

import datetime
import json
import os
import re
from pathlib import Path

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl
from PyQt6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .features import Feature

import qtawesome as qta

FEED = Path.home() / "Library" / "Application Support" / "Daybook" / "hop.json"
SETTING = "up_next"
JOIN_PROMPT = "up_next_join_prompt"  # offer to join a meeting as it starts (on unless turned off)
JOIN_SPACE = "up_next_join_space"  # joining switches to the Space named like its calendar (off unless on)
PROMPT_BEFORE = 120  # seconds before the start the offer comes
PROMPT_AFTER = 5 * 60  # and how late it still comes, if you weren't here at the start
PROMPT_SECONDS = 15  # how long it stays
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
        return f"Now · {left} min left" if left < 60 else f"Now · until {event['end'].strftime('%-I:%M %p')}"
    minutes = int((event["start"] - now).total_seconds() // 60)
    at = event["start"].strftime("%-I:%M %p")
    if minutes < 1:
        return f"{at} · starting now"
    if minutes < 60:
        return f"{at} · in {minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{at} · in {hours} h" + (f" {rest} min" if rest and hours < 3 else "")


def should_offer(event: dict | None, now: datetime.datetime) -> bool:
    """Whether it's time to offer to join `event`: it has a meeting link and starts within a couple of minutes
    (or did, a few minutes ago)."""
    if not event or not event["link"]:
        return False
    lead = (event["start"] - now).total_seconds()
    return -PROMPT_AFTER <= lead <= PROMPT_BEFORE


def offer_text(event: dict, now: datetime.datetime) -> str:
    lead = (event["start"] - now).total_seconds()
    if lead >= 60:
        return f"{event['title']} starts in {round(lead / 60)} min"
    if lead > -60:
        return f"{event['title']} is starting"
    return f"{event['title']} started {round(-lead / 60)} min ago"


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def space_for(calendar: str, spaces):
    """The Space named like a calendar: the same name ("Work" and "work"), else one whose name is a word of the
    calendar's, or the other way round ("Work" for "Work Calendar"). None for none, or more than one."""
    wanted = _words(calendar)
    if not wanted:
        return None
    named = [(s, _words(s.name)) for s in spaces]
    same = [s for s, words in named if words == wanted]
    if same:
        return same[0]

    def inside(small, big):
        return small and any(big[i:i + len(small)] == small for i in range(len(big) - len(small) + 1))
    close = [s for s, words in named if inside(words, wanted) or inside(wanted, words)]
    return close[0] if len(close) == 1 else None


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
        self._offered = set()  # the events already offered (title, start), so each is offered once
        self._timer = QTimer(self, interval=POLL_MS, timeout=self.refresh)
        self._timer.start()
        QTimer.singleShot(1500, self.refresh)
        # (an offer that came while you were in another app waits for you to come back)
        QApplication.instance().applicationStateChanged.connect(
            lambda state: state == Qt.ApplicationState.ApplicationActive
            and QTimer.singleShot(300, self.offer_to_join))  # (once the window's active too)

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
            self.card.hide()
            return
        soon = (self.event["start"] - now).total_seconds() < SOON_MINUTES * 60
        self.card.set_event(self.event, when_text(self.event, now), soon, self.browser.theme)
        self.offer_to_join(now)

    def offer_to_join(self, now=None):
        """A meeting's about to start: offer to join it, in a message at the bottom of the page (once)."""
        now = now or datetime.datetime.now().astimezone()
        event = self.event
        if not self.browser.store.settings.get(JOIN_PROMPT, True) or not should_offer(event, now):
            return
        key = (event["title"], event["start"].isoformat())
        if key in self._offered or not self.browser.isActiveWindow():
            return  # (offered already, or you're elsewhere: it waits for you)
        self._offered.add(key)
        self.browser.toast(offer_text(event, now), lambda e=event: self.join(e), "Join", seconds=PROMPT_SECONDS)

    def join(self, event=None):
        event = event if isinstance(event, dict) else self.event  # (a button's click passes a bool)
        if not event or not event["link"]:
            return
        if self.browser.store.settings.get(JOIN_SPACE, False):
            space = space_for(event["calendar"], self.browser.store.spaces)
            if space is not None and space is not self.browser.store.active_space:
                self.browser.switch_space(space)  # (so the meeting's tab opens there)
        self.browser.open_url(event["link"])

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

    def settings_pages(self):
        return [("daybook", "ph.calendar-blank", "Daybook", self._settings_page, "extensions")]

    def _settings_page(self, settings):
        settings_ = self.window.store.settings

        def switch(key, default, then=None):
            box = QCheckBox()
            box.setChecked(settings_.get(key, default))
            box.toggled.connect(lambda on: (settings._setting(key, on), then and then()))
            return box
        rows = [
            settings._row("Up Next", "Your next event today, from Daybook, at the bottom of the sidebar.",
                          switch(SETTING, True, self.up_next.refresh)),
            settings._row("Offer to join meetings", "A minute or two before an event with a meeting link starts, "
                                                    "a message at the bottom of the page offers to join it.",
                          switch(JOIN_PROMPT, True)),
            settings._row("Join in the matching Space", "Joining a meeting switches to the Space named like its "
                                                        "calendar (Work for a Work calendar) first.",
                          switch(JOIN_SPACE, False)),
        ]
        # The other features that work with Daybook add their switches here (browsing time)
        for feature in self.window.features.values():
            rows += getattr(feature, "daybook_settings", lambda _s: [])(settings)
        return settings._stack(settings._card(rows))


FEATURE = UpNextFeature
