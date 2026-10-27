import datetime
import json
from types import SimpleNamespace

from PyQt6.QtCore import QObject

from browser import browsing_time, upnext
from browser.browsing_time import BrowsingTime, Ledger, site_of
from browser.integrations import daybook_task_url, daybook_url, task_text
from browser.upnext import UpNext, offer_text, should_offer, space_for

NOW = datetime.datetime(2026, 10, 27, 14, 0, tzinfo=datetime.timezone.utc)


def _event(minutes_away, link="https://zoom.us/j/1", calendar="Work"):
    start = NOW + datetime.timedelta(minutes=minutes_away)
    return {"title": "Standup", "start": start, "end": start + datetime.timedelta(minutes=30), "place": link,
            "calendar": calendar, "link": link, "live": minutes_away <= 0}


class FakeWindow(QObject):
    """What Up Next and browsing time use of the window."""

    def __init__(self, spaces=("Personal", "Work"), active=True):
        super().__init__()
        self.store = SimpleNamespace(settings={}, spaces=[SimpleNamespace(name=n) for n in spaces])
        self.store.active_space = self.store.spaces[0]
        self.active, self.toasts, self.opened, self.current_tab = active, [], [], None

    def isActiveWindow(self):
        return self.active

    def isMinimized(self):
        return False

    def toast(self, text, action=None, label="Undo", seconds=None):
        self.toasts.append((text, action, label))

    def switch_space(self, space):
        self.store.active_space = space

    def open_url(self, url):
        self.opened.append((url, self.store.active_space.name))

    def tab_audio(self, tab):
        return getattr(tab, "playing", False), False


# --- Joining from Up Next ---------------------------------------------------------------------------

def test_offer_comes_a_minute_or_two_before_a_meeting():
    assert not should_offer(_event(10), NOW)
    assert should_offer(_event(2), NOW) and should_offer(_event(0), NOW)
    assert should_offer(_event(-4), NOW)  # (you weren't here when it started)
    assert not should_offer(_event(-10), NOW)
    assert not should_offer(_event(1, link=""), NOW)  # nothing to join
    assert not should_offer(None, NOW)
    assert offer_text(_event(2), NOW) == "Standup starts in 2 min"
    assert offer_text(_event(0), NOW) == "Standup is starting"
    assert offer_text(_event(-3), NOW) == "Standup started 3 min ago"


def test_space_for_a_calendar():
    spaces = [SimpleNamespace(name=n) for n in ("Personal", "Work", "School Stuff")]
    assert space_for("work", spaces).name == "Work"
    assert space_for("Work Calendar", spaces).name == "Work"
    assert space_for("School", spaces).name == "School Stuff"
    assert space_for("Home", spaces) is None and space_for("", spaces) is None
    # Two that could be it: neither
    assert space_for("Work", [SimpleNamespace(name="Work Projects"), SimpleNamespace(name="Work Admin")]) is None
    assert space_for("Homework", spaces) is None  # (a word, not a part of one)


def test_offer_to_join_once_and_only_when_you_are_here(monkeypatch):
    window = FakeWindow(active=False)
    up = UpNext(window, path="/nonexistent")
    up.event = _event(1)
    up.offer_to_join(NOW)
    assert window.toasts == []  # elsewhere: it waits
    window.active = True
    up.offer_to_join(NOW)
    up.offer_to_join(NOW)
    assert len(window.toasts) == 1 and window.toasts[0][2] == "Join"
    window.toasts[0][1]()  # Join
    assert window.opened == [("https://zoom.us/j/1", "Personal")]  # the Space switch is off by default

    window.store.settings[upnext.JOIN_PROMPT] = False
    up.event = {**_event(1), "title": "Another"}
    up.offer_to_join(NOW)
    assert len(window.toasts) == 1


def test_join_switches_to_the_matching_space_when_asked():
    window = FakeWindow()
    window.store.settings[upnext.JOIN_SPACE] = True
    up = UpNext(window, path="/nonexistent")
    up.event = _event(1)
    up.join(False)  # (as the Join button's click calls it)
    assert window.opened == [("https://zoom.us/j/1", "Work")]
    up.join({**_event(1), "calendar": "Nothing like it"})
    assert window.opened[-1][1] == "Work"  # no match: stays where it is


# --- Tasks from the command bar ---------------------------------------------------------------------

def test_task_command():
    assert task_text("task call mom friday 5pm") == "call mom friday 5pm"
    assert task_text("  Task  pay rent tomorrow ") == "pay rent tomorrow"
    assert task_text("task") is None and task_text("taskrabbit.com") is None and task_text("tasks app") is None
    assert daybook_task_url("call mom & dad friday 5pm").toEncoded().data().decode() \
        == "daybook://add-task?text=call%20mom%20%26%20dad%20friday%205pm"
    assert daybook_url("Read", "https://a.example/").toString().startswith("daybook://add-task?text=Read")


# --- Browsing time ----------------------------------------------------------------------------------

def test_site_of():
    assert site_of("https://www.GitHub.com/x") == "github.com"
    assert site_of("http://localhost:8000/") == "localhost"
    assert site_of("file:///tmp/a.html") is None and site_of("") is None and site_of("about:blank") is None


def test_ledger_round_trip():
    ledger = Ledger()
    today, yesterday = NOW.date(), NOW.date() - datetime.timedelta(days=1)
    ledger.add(today, "Work", "github.com", 600)
    ledger.add(today, "Work", "docs.python.org", 120.4)
    ledger.add(today, "Personal", None, 60)  # (a note: Space time, no site)
    ledger.add(yesterday, "Personal", "youtube.com", 30)
    ledger.add(NOW.date() - datetime.timedelta(days=60), "Old", "old.example", 30)  # past keeping
    data = ledger.to_json(NOW)
    assert [d["date"] for d in data["days"]] == ["2026-10-27", "2026-10-26"]
    day = data["days"][0]
    assert day["seconds"] == 780
    assert day["spaces"] == [{"name": "Work", "seconds": 720}, {"name": "Personal", "seconds": 60}]
    assert day["sites"] == [{"host": "github.com", "seconds": 600}, {"host": "docs.python.org", "seconds": 120}]
    again = Ledger.from_json(json.loads(json.dumps(data)))
    assert again.to_json(NOW)["days"] == data["days"]
    assert Ledger.from_json(None).days == {} and Ledger.from_json([1]).days == {}
    assert Ledger.from_json({"days": [{"date": "nope"}, {"seconds": 3}]}).days == {}


def test_browsing_time_counts_only_while_you_are_here(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(browsing_time.time, "monotonic", lambda: clock[0])
    idle = [0.0]
    monkeypatch.setattr(browsing_time.macos, "idle_seconds", lambda: idle[0])
    window = FakeWindow()
    window.current_tab = SimpleNamespace(url="https://www.github.com/pulls")
    tracker = BrowsingTime(window, folder=tmp_path)

    def tick(seconds):
        clock[0] += seconds
        tracker.tick(NOW)
    tick(15)
    window.active = False
    tick(15)  # another app
    window.active = True
    idle[0] = 600
    tick(15)  # away from the Mac
    window.current_tab.playing = True
    tick(15)  # away, but watching something: counts
    idle[0] = 0
    tick(3600)  # the Mac slept: only a tick's worth
    day = tracker.ledger.to_json(NOW)["days"][0]
    assert day["seconds"] == 15 + 15 + 30
    assert day["sites"] == [{"host": "github.com", "seconds": 60}]

    tick(61)  # a minute on: saved
    saved = json.loads((tmp_path / "browsing.json").read_text())
    assert saved["app"] == "Eclipse" and saved["days"][0]["spaces"][0]["name"] == "Personal"
    # A new run carries on today's count
    assert BrowsingTime(window, folder=tmp_path).ledger.days == tracker.ledger.days


def test_browsing_time_needs_daybook_and_the_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(browsing_time.macos, "idle_seconds", lambda: 0.0)
    window = FakeWindow()
    missing = tmp_path / "no-daybook"
    tracker = BrowsingTime(window, folder=missing)
    tracker.tick(NOW)
    tracker._dirty = True
    tracker.save()
    assert not missing.exists()  # Daybook isn't on this Mac: nothing written
    window.store.settings[browsing_time.SETTING] = False
    off = BrowsingTime(window, folder=tmp_path)
    off.tick(NOW)
    off.save()
    assert off.ledger.days == {} and not (tmp_path / "browsing.json").exists()
