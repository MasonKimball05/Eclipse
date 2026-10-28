# Watch This Page: what counts as a change, when to look again, what's kept, and what you're told. (Loading pages
# in the background is the window's: the live tests drive it.)

from browser import page_watch
from browser.page_watch import (INTERVALS, MAX_TEXT, Watcher, changed, clean_watch, diff_summary, next_delay,
                                normalise)


# --- What counts as a change -----------------------------------------------------------------------------

def test_noise_isnt_a_change():
    before = "Posted 3 minutes ago · 1,204 views · 12 comments\nUpdated Oct 27, 2026 at 10:42 AM\nPrice: $299"
    after = "Posted 4 minutes ago · 1,388 views · 15 comments\nUpdated Oct 28, 2026 at 9:05 AM\nPrice:   $299"
    assert normalise(before) == normalise(after)
    assert not changed(before, after)
    assert not changed("Last updated 2026-10-27T10:42:00Z", "Last updated 2026-10-28T09:01:13Z")
    assert not changed("just now", "an hour ago") and not changed("Seen 28/10/2026", "Seen 29/10/2026")


def test_a_real_change_is():
    assert changed("Price: $299", "Price: $249")
    assert changed("In stock: no", "In stock: yes")
    assert changed("Version 1.4.2", "Version 1.5.0")
    assert changed("3 seats left", "1 seat left")


def test_the_plain_summary_says_what_changed():
    assert diff_summary("Acme Phone\nPrice: $299\nShips soon", "Acme Phone\nPrice: $249\nShips soon") == "“$299” → “$249”"
    assert diff_summary("A\nB", "A\nB\nNew item: kites") == "Added: “New item: kites”"
    assert diff_summary("A\nGone line\nB", "A\nB") == "Removed: “Gone line”"
    many = diff_summary("a\nb\nc", "x\ny\nz\nw")
    assert many.startswith("4 lines added, 3 removed") and "“x”" in many
    assert len(diff_summary("x", "y" * 500)) < 140


# --- When to look again ------------------------------------------------------------------------------------

def test_backoff_doubles_on_errors_and_stops_at_a_day():
    assert next_delay(30, 0) == 30 * 60
    assert [next_delay(30, n) // 60 for n in range(1, 7)] == [60, 120, 240, 480, 960, 1440]
    assert next_delay(1440, 3) == 24 * 3600 and next_delay(15, 0) == 15 * 60


def test_intervals_are_kept_between_a_quarter_hour_and_a_day():
    assert INTERVALS[0] == 15 and INTERVALS[-1] == 1440
    assert clean_watch({"url": "https://a.example/", "interval": 1})["interval"] == 15
    assert clean_watch({"url": "https://a.example/", "interval": 99999})["interval"] == 1440


# --- What's kept -------------------------------------------------------------------------------------------

def test_watches_round_trip_and_odd_ones_are_dropped():
    watch = clean_watch({"id": "w1", "url": "https://shop.example/phone", "title": "Phone", "selector": "#price",
                         "text": "$299", "interval": 60, "paused": True, "failures": 2, "due": 5.0,
                         "before": "a", "after": "b", "summary": "Price dropped", "changed": 4.0})
    assert clean_watch(dict(watch)) == watch
    assert clean_watch({"url": "javascript:alert(1)"}) is None and clean_watch({"url": "file:///etc/hosts"}) is None
    assert clean_watch("nonsense") is None
    assert len(clean_watch({"url": "https://a.example/", "text": "x" * (MAX_TEXT * 2)})["text"]) == MAX_TEXT
    fresh = clean_watch({"url": "https://a.example/"})
    assert fresh["id"] and fresh["interval"] == 30 and fresh["text"] is None and not fresh["paused"]


class _Store:
    def __init__(self):
        self.settings, self.saved = {}, 0

    def schedule_save(self):
        self.saved += 1


class _Notifications:
    def __init__(self):
        self.shown = []

    def present(self, profile, notice):
        self.shown.append(notice)


def _looked(watcher, watch_id, data):
    """What check() ends with: the page's answer, for the watch being looked at."""
    watcher.busy = watch_id
    watcher._result(watch_id, data)


class _Window:
    def __init__(self):
        self.store, self.notifications, self.profile = _Store(), _Notifications(), None

    def feature(self, name):
        return None  # (no Claude: the plain summary)


def test_first_look_is_the_baseline_then_changes_are_kept_and_told(qapp):
    window = _Window()
    watcher = Watcher(window, start=False)
    watch = watcher.add("https://shop.example/phone", "Acme Phone")
    assert window.store.settings["page_watches"][0]["url"] == "https://shop.example/phone"
    assert watcher.add("https://shop.example/phone", "Again") is None  # (already watched)
    _looked(watcher, watch["id"], {"text": "Acme Phone\nPrice: $299\n3 minutes ago"})
    assert watcher.get(watch["id"])["text"].endswith("3 minutes ago") and window.notifications.shown == []
    _looked(watcher, watch["id"], {"text": "Acme Phone\nPrice: $299\n9 minutes ago"})
    assert window.notifications.shown == []  # (only noise moved)
    _looked(watcher, watch["id"], {"text": "Acme Phone\nPrice: $249\n1 minute ago"})
    kept = watcher.get(watch["id"])
    assert "$299" in kept["before"] and "$249" in kept["after"] and kept["summary"] == "“$299” → “$249”"
    notice = window.notifications.shown[0]
    assert "Acme Phone" in notice.title() and notice.message() == "“$299” → “$249”"
    assert notice.origin().host() == "shop.example"


def test_errors_back_off_and_a_success_resets(qapp):
    import time
    window = _Window()
    watcher = Watcher(window, start=False)
    watch = watcher.add("https://shop.example/", "Shop", interval=30)
    for n in range(1, 4):
        before = time.time()
        _looked(watcher, watch["id"], {"error": "Couldn't load it"})
        kept = watcher.get(watch["id"])
        assert kept["failures"] == n and kept["error"] == "Couldn't load it"
        assert abs(kept["due"] - (before + next_delay(30, n))) < 5
    _looked(watcher, watch["id"], {"text": "fine"})
    assert watcher.get(watch["id"])["failures"] == 0 and watcher.get(watch["id"])["error"] == ""


def test_due_watches_go_one_at_a_time_and_paused_ones_wait(qapp):
    window = _Window()
    watcher = Watcher(window, start=False)
    checked = []
    watcher.check = lambda watch: (checked.append(watch["id"]), setattr(watcher, "busy", watch["id"]))
    a = watcher.add("https://a.example/", "A")
    b = watcher.add("https://b.example/", "B")
    watcher.set_paused(a["id"], True)
    watcher.tick()
    watcher.tick()  # (still busy with the first)
    assert checked == [b["id"]]
    watcher.remove(b["id"])
    assert [w["id"] for w in window.store.settings["page_watches"]] == [a["id"]]


def test_the_check_script_follows_a_selection_or_reads_the_page():
    script = page_watch.read_script("#price > span:nth-of-type(2)", "$299")
    assert '"#price > span:nth-of-type(2)"' in script and '"$299"' in script
    assert "null" in page_watch.read_script(None, None)


def test_a_late_answer_from_a_look_thats_over_is_ignored(qapp):
    window = _Window()
    watcher = Watcher(window, start=False)
    watch = watcher.add("https://shop.example/", "Shop")
    _looked(watcher, watch["id"], {"error": "It took too long to load"})  # (timed out)
    watcher._result(watch["id"], {"text": "the page answering after all"})
    assert watcher.get(watch["id"])["text"] is None and watcher.get(watch["id"])["failures"] == 1
