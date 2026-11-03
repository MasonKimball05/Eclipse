# EclipseKit (native/EclipseKit, Swift) reads and merges Sync's files as sync.py does. This makes the fixture its
# tests check against, from sync.py itself: two devices' files (with parts that must be left out), what's kept
# of them, each item's hash, a merge, json.dumps's text for awkward values, and sites' letters. If sync.py's format changes, this
# fails until the fixture is made again:  ECLIPSE_WRITE_FIXTURES=1 pytest tests/test_sync_swift_fixture.py
import json
import os
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent.parent / "native" / "EclipseKit" / "Tests" / "EclipseKitTests" / "Fixtures" / "sync.json"


def build() -> dict:
    from browser import sync
    work = {"name": "Wörk  ‮ tricks\n", "color": "#3B82F6", "icon": "ph.briefcase",
            "theme": {"colors": ["#3b82f6", "nope", "#f59e0b"], "intensity": 1.5, "mode": "dark", "grain": True,
                      "extra": 1},
            "pinned": [{"id": "t1", "url": "https://mail.example.com/inbox", "title": "Inbox", "custom_title": None},
                       {"id": "t2", "url": "javascript:alert(1)", "title": "Bad"},
                       {"type": "folder", "id": "f1", "name": "Docs \"quoted\" \\ back", "children": [
                           {"id": "t3", "url": "https://docs.example.com/a b", "title": "Has a space"},
                           {"id": "t4", "url": "https://docs.example.com/q3", "title": "Q3 plan – 💼",
                            "custom_title": "Q3"}]},
                       {"type": "folder", "id": "f2", "name": "Feed", "live": {"kind": "feed",
                                                                               "url": "https://example.com/rss",
                                                                               "every": 30, "on": True}},
                       {"type": "folder", "id": "f3", "name": "Sneaky", "live": {"kind": "feed",
                                                                                 "url": "file:///etc/passwd"}}]}
    home = {"name": "Home", "color": "red", "icon": "🏠", "theme": {"intensity": 0.25, "mode": "auto"}, "pinned": []}
    mac = {"format": "eclipse-sync", "version": 1, "device": "mac-1", "name": "Studio", "written": 1730000000.5,
           "items": {"space:work": {"modified": 100.0, "data": work},
                     "favorites": {"modified": 90, "data": [{"id": "fav1", "url": "https://example.org", "title": "Ex"},
                                                            {"type": "folder", "id": "nf", "name": "Not a tab"}]},
                     "order": {"modified": 95.25, "data": ["work", "home", 7]},
                     "space:broken": {"modified": "soon", "data": home},
                     "unknown": {"modified": 1, "data": {}}},
           "deleted": {"space:old": 80, "space:bad": "x"}}
    laptop = {"format": "eclipse-sync", "version": 1, "device": "mac-2", "name": "Laptop", "written": 1730000100,
              "items": {"space:home": {"modified": 120, "data": home},
                        "space:work": {"modified": 50, "data": {**work, "name": "Older"}},
                        "space:old": {"modified": 70, "data": home}},
              "deleted": {"favorites": 85}}
    remotes = [sync.clean_remote(mac), sync.clean_remote(laptop)]
    state = sync.new_state()
    state["device"] = "phone"
    state["known"]["space:home"] = {"hash": "old", "modified": 130}  # (changed here since: stays)
    plan = sync.merge(state, remotes)
    values = [0.5, 1.0, 0.1, 1e-05, 0.0001, 1e15, 1e16, 123456789.125, -2.5, 1e100, 2.5e-07, 3, -0.0,
              "tab\there", "quote\" back\\ slash/", "ctl\x01\x1f\x7f", "é ü 中文 😀  ", True, None,
              {"b": 1, "a": [1, 2.0, {"é": "x", "e": "y", "Z": 0}]}, []]
    from browser.theme import site_letter
    hosts = ["mail.google.com", "en.wikipedia.org", "www.bbc.co.uk", "abc.net.au", "example.com", "localhost",
             "192.168.0.1", "github.io", "x.com", "über.de", ""]
    return {"files": [mac, laptop], "clean": remotes, "letters": {h: site_letter(h) for h in hosts},
            "digests": {k: sync.digest(item["data"]) for r in remotes for k, item in r["items"].items()},
            "state": state, "plan": plan,
            "canonical": [{"value": v, "text": json.dumps(v, sort_keys=True, ensure_ascii=False)} for v in values]}


def test_the_swift_fixture_is_what_sync_py_makes():
    made = build()
    if os.environ.get("ECLIPSE_WRITE_FIXTURES"):
        FIXTURE.write_text(json.dumps(made, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(made)), \
        "sync.py changed: ECLIPSE_WRITE_FIXTURES=1 pytest tests/test_sync_swift_fixture.py (then swift test)"
