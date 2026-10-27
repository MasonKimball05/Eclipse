# Browsing time, for Daybook: how long you spent in each Space and on each site, a day at a time, the way
# Daybook already counts your coding from commits. Daybook on the Mac reads it into its Time tab and the
# time.md its briefs use.
#
# Only time you're actually here counts: the main window has to be the one you're in (not a private window,
# not a Little window, not another app), and you have to have touched the Mac in the last few minutes,
# unless the page you're on is playing sound (a video, a call). Each day keeps its total, its Spaces and its
# busiest sites (by address, without the www); nothing else about the pages is kept.
#
# The file is ~/Library/Application Support/Daybook/browsing.json, written only when Daybook is on this Mac
# (its folder is there), at most once a minute and as Eclipse quits. Off in Settings → Daybook.
#
#   {"app": "Eclipse", "version": 1, "updated": "2026-10-27T14:05:00-06:00",
#    "days": [{"date": "2026-10-27", "seconds": 5400,
#              "spaces": [{"name": "Work", "seconds": 3600}, …],
#              "sites": [{"host": "github.com", "seconds": 1200}, …]}, …]}   (newest day first)

import datetime
import json
import os
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtWidgets import QCheckBox

from . import macos
from .features import Feature

FOLDER = Path.home() / "Library" / "Application Support" / "Daybook"
FILE_NAME = "browsing.json"
SETTING = "browsing_time"
TICK_MS = 15_000
IDLE_SECONDS = 3 * 60  # no key, click or pointer move for this long, and you're away
SAVE_SECONDS = 60
KEEP_DAYS = 35
SITES_PER_DAY = 40


def site_of(url: str) -> str | None:
    """The site a page is on, as it's counted: its host without the www. None for anything that isn't a web
    page (a note, an easel, a file, the empty tab)."""
    q = QUrl(url or "")
    if q.scheme() not in ("http", "https") or not q.host():
        return None
    return q.host().lower().removeprefix("www.")


class Ledger:
    """Seconds by day, by Space and by site."""

    def __init__(self):
        self.days: dict[str, dict] = {}  # "2026-10-27": {"seconds", "spaces": {name: s}, "sites": {host: s}}

    def add(self, day: datetime.date, space: str, site: str | None, seconds: float):
        if seconds <= 0:
            return
        entry = self.days.setdefault(day.isoformat(), {"seconds": 0.0, "spaces": {}, "sites": {}})
        entry["seconds"] += seconds
        entry["spaces"][space] = entry["spaces"].get(space, 0.0) + seconds
        if site:
            entry["sites"][site] = entry["sites"].get(site, 0.0) + seconds

    def to_json(self, now: datetime.datetime) -> dict:
        since = (now.date() - datetime.timedelta(days=KEEP_DAYS - 1)).isoformat()

        def ranked(counts: dict, key: str, limit=None) -> list:
            rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
            return [{key: name, "seconds": round(seconds)} for name, seconds in rows if round(seconds) > 0]
        days = [{"date": date, "seconds": round(entry["seconds"]),
                 "spaces": ranked(entry["spaces"], "name"),
                 "sites": ranked(entry["sites"], "host", SITES_PER_DAY)}
                for date, entry in sorted(self.days.items(), reverse=True) if date >= since]
        return {"app": "Eclipse", "version": 1, "updated": now.isoformat(timespec="seconds"), "days": days}

    @classmethod
    def from_json(cls, data) -> "Ledger":
        """What a browsing.json said (to carry on today's count after a restart); empty for anything unreadable."""
        ledger = cls()
        days = data.get("days") if isinstance(data, dict) else None
        for day in days if isinstance(days, list) else []:
            try:
                datetime.date.fromisoformat(day["date"])
                ledger.days[day["date"]] = {
                    "seconds": float(day.get("seconds", 0)),
                    "spaces": {s["name"]: float(s["seconds"]) for s in day.get("spaces") or []},
                    "sites": {s["host"]: float(s["seconds"]) for s in day.get("sites") or []},
                }
            except (KeyError, TypeError, ValueError):
                continue
        return ledger


class BrowsingTime(QObject):
    def __init__(self, browser, folder=FOLDER):
        super().__init__(browser)
        self.browser = browser
        self.folder = Path(folder)
        self.ledger = Ledger.from_json(self._read())
        self._last = time.monotonic()
        self._saved = self._last
        self._dirty = False
        self._timer = QTimer(self, interval=TICK_MS, timeout=self.tick)
        self._timer.start()

    @property
    def path(self) -> Path:
        return self.folder / FILE_NAME

    @property
    def enabled(self) -> bool:
        return self.browser.store.settings.get(SETTING, True)

    def _read(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None

    def here(self) -> bool:
        """Whether you're using the browser right now: its main window is the one you're in, and you've touched
        the Mac lately, or the page on screen is playing sound."""
        window = self.browser
        if not window.isActiveWindow() or window.isMinimized():
            return False
        if macos.idle_seconds() < IDLE_SECONDS:
            return True
        tab = window.current_tab
        playing, muted = window.tab_audio(tab) if tab is not None else (False, False)
        return playing and not muted

    def tick(self, now=None):
        moment = time.monotonic()
        # (capped: after the Mac slept, the gap since the last tick wasn't time spent here)
        seconds = min(moment - self._last, 2 * TICK_MS / 1000)
        self._last = moment
        if not self.enabled:
            return
        if self.here():
            space = self.browser.store.active_space
            tab = self.browser.current_tab
            now = now or datetime.datetime.now().astimezone()
            self.ledger.add(now.date(), space.name if space else "", site_of(tab.url) if tab else None, seconds)
            self._dirty = True
        if self._dirty and moment - self._saved >= SAVE_SECONDS:
            self.save()

    def save(self):
        """Writes browsing.json for Daybook (only if Daybook is on this Mac: its folder's there)."""
        self._saved = time.monotonic()
        if not self._dirty or not self.enabled or not self.folder.is_dir():
            return
        data = self.ledger.to_json(datetime.datetime.now().astimezone())
        temp = self.path.with_suffix(".json.tmp")
        try:
            temp.write_text(json.dumps(data, indent=1))
            os.replace(temp, self.path)  # (whole, or not at all: Daybook never reads half a file)
            self._dirty = False
        except OSError:
            pass


class BrowsingTimeFeature(Feature):
    """Time in each Space and on each site, a day at a time, for Daybook's Time tab."""
    name = "browsing_time"

    def __init__(self, window):
        super().__init__(window)
        self.tracker = BrowsingTime(window)

    def shutdown(self):
        self.tracker.tick()  # (the time since the last tick counts too)
        self.tracker.save()

    def daybook_settings(self, settings):
        """Its switch, on Settings → Daybook (up_next's page)."""
        box = QCheckBox()
        box.setChecked(self.tracker.enabled)
        box.toggled.connect(lambda on: settings._setting(SETTING, on))
        return [settings._row("Browsing time", "Daybook's Time tab shows how long you spent in each Space and on "
                                               "each site, counting only while you're using Eclipse. It stays on "
                                               "this Mac, in Daybook's folder.", box)]


FEATURE = BrowsingTimeFeature
