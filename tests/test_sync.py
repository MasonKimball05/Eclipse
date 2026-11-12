# Sync's merging, as two Macs passing files through iCloud Drive (a folder here). The window side, applying what
# comes, is in tests/live/test_live_features.py.

import json

from browser import sync
from browser.models import FAVORITES


class Mac:
    """One device: what it has (as sync sees it) and its sync state."""

    def __init__(self, folder, items):
        self.folder, self.items, self.state = folder, items, sync.new_state()

    def look(self, now):
        """What run() does, minus the window: note changes here, write them, take the others'."""
        sync.note_local(self.state, self.items, now)
        (self.folder / f"{self.state['device']}.json").write_text(
            json.dumps(sync.device_file(self.state, self.items, now)))
        plan = sync.merge(self.state, sync.read_remotes(self.folder, self.state["device"]))
        for key in plan["delete"]:
            self.items.pop(key, None)
        self.items.update(plan["apply"])
        sync.note_local(self.state, self.items, now)  # (what came isn't a change of this Mac's)
        return plan


def _space(name, *urls):
    return {"name": name, "color": "#3366ff", "icon": "", "theme": {},
            "pinned": [{"id": f"t-{u}", "url": f"https://{u}/", "title": u, "custom_title": None} for u in urls]}


def _two(tmp_path):
    work = _space("Work", "mail.example", "docs.example")
    a = Mac(tmp_path, {"space:w": work, FAVORITES: [], "order": ["w"]})
    a.look(100)
    b = Mac(tmp_path, {})
    b.look(110)  # (a new Mac with nothing of its own: it takes everything)
    assert b.items == a.items
    return a, b


def test_a_change_on_one_mac_reaches_the_other(tmp_path):
    a, b = _two(tmp_path)
    a.items["space:w"] = _space("Work", "mail.example", "docs.example", "calendar.example")
    a.look(200)
    plan = b.look(210)
    assert list(plan["apply"]) == ["space:w"] and b.items["space:w"] == a.items["space:w"]
    assert b.look(220) == {"apply": {}, "delete": []} and a.look(230) == {"apply": {}, "delete": []}  # (settles)


def test_changes_to_different_spaces_on_each_mac_both_stay(tmp_path):
    a, b = _two(tmp_path)
    a.items["space:w"] = _space("Work", "mail.example")
    b.items["space:h"] = _space("Home", "recipes.example")
    b.items["order"] = ["w", "h"]
    a.look(200)
    b.look(210)
    a.look(220)
    assert a.items["space:w"] == b.items["space:w"] == _space("Work", "mail.example")
    assert a.items["space:h"] == b.items["space:h"] and a.items["order"] == ["w", "h"]


def test_the_same_space_changed_on_both_the_later_one_wins(tmp_path):
    a, b = _two(tmp_path)
    a.items["space:w"] = _space("Work (A)", "a.example")
    b.items["space:w"] = _space("Work (B)", "b.example")
    a.look(200)
    b.look(300)  # (B's change is later: it stays, and goes to A)
    a.look(310)
    assert a.items["space:w"]["name"] == b.items["space:w"]["name"] == "Work (B)"


def test_a_deleted_space_goes_everywhere_unless_its_changed_after(tmp_path):
    a, b = _two(tmp_path)
    b.items["space:h"] = _space("Home", "recipes.example")
    b.look(150)
    a.look(160)
    del a.items["space:h"]
    a.look(200)
    plan = b.look(210)
    assert plan["delete"] == ["space:h"] and "space:h" not in b.items
    # Deleted on A, but changed on B later: the change wins, and comes back to A
    b.items["space:w"] = _space("Work", "kept.example")
    b.look(300)
    del a.items["space:w"]
    a.look(250)  # (A's clock: before B's change)
    a.look(320)
    assert a.items["space:w"] == _space("Work", "kept.example")


def test_another_devices_file_is_checked(tmp_path):
    a, b = _two(tmp_path)
    bad = {"format": "eclipse-sync", "device": "evil", "name": "Evil", "written": 999, "deleted": {"space:w": "x"},
           "items": {"space:w": {"modified": 999, "data": {
               "name": "‮Work", "color": "red", "icon": "<b>", "theme": {"colors": ["javascript:"]},
               "pinned": [{"id": "1", "url": "javascript:alert(1)"}, {"id": "2", "url": "file:///etc/passwd"},
                          {"id": "3", "url": "https://fine.example/", "title": "Fine"},
                          {"type": "folder", "id": "f", "live": {"kind": "feed", "url": "data:x"}}]}},
               "nonsense": {"modified": 999, "data": [1, 2]}, FAVORITES: {"modified": "soon", "data": []}}}
    (tmp_path / "evil.json").write_text(json.dumps(bad))
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "other.json").write_text(json.dumps({"format": "something-else"}))
    b.look(1000)
    work = b.items["space:w"]
    assert work["name"] == "Work" and work["color"] == "#6b7280" and work["icon"] == "" and work["theme"] == {}
    assert [n["url"] for n in work["pinned"]] == ["https://fine.example/"]
    assert "nonsense" not in b.items and b.items[FAVORITES] == []


def test_whats_synced_from_a_store(data_dir):
    from browser import storage
    from browser.models import Folder, Store, Tab
    store = Store(storage.session_path())
    space = store.spaces[0]
    space.pinned = [Folder("Feed", [Tab("https://n/1", "1")], live={"kind": "feed", "url": "https://n/rss", "seen": ["x"]}),
                    Tab("https://x.example/page", "X", home_url="https://x.example/", custom_title="Ex")]
    space.today = [Tab("https://today.example/", "Today")]
    items = sync.snapshot(store)
    assert set(items) == {f"space:{space.id}", FAVORITES, "order"} | {f"space:{s.id}" for s in store.spaces}
    pinned = items[f"space:{space.id}"]["pinned"]
    assert pinned[0] == {"type": "folder", "id": pinned[0]["id"], "name": "Feed", "live": {"kind": "feed", "url": "https://n/rss"}}
    assert pinned[1]["url"] == "https://x.example/" and pinned[1]["custom_title"] == "Ex"
    assert "today.example" not in json.dumps(items)


def test_tabs_sync_cant_carry_stay_when_another_macs_copy_comes(qapp):
    """Only web addresses sync: a pinned note, easel or file: tab never reaches the other Mac, and when its copy of
    the Space came back newer, it was rebuilt without them, and they were taken away as gone."""
    from browser.models import Folder, Tab
    note, easel = Tab("note:abc", "My note"), Tab("easel:xyz", "Sketch")
    web = Tab("https://mail.example/", "Mail", tab_id="t-mail")
    folder = Folder("Work", [Tab("https://docs.example/", "Docs", tab_id="t-docs"), easel], "f-work")
    pinned = [web, note, folder]
    pool = {t.id: t for t in (web, note, easel, *folder.children)}
    kept = sync.local_only(pinned)
    came = [{"type": "folder", "id": "f-work", "name": "Work",
             "children": [{"id": "t-docs", "url": "https://docs.example/", "title": "Docs"}]},
            {"id": "t-mail", "url": "https://mail.example/", "title": "Mail"}]  # (reordered on the other Mac)
    rebuilt = sync.put_back(sync.build_nodes(came, pool, {}), kept, pool)
    assert note in rebuilt and easel in next(n for n in rebuilt if isinstance(n, Folder)).children
    assert note.id not in pool and easel.id not in pool  # (so not taken for gone)


def test_a_pinned_pages_changing_title_isnt_a_change_to_sync():
    """A pinned inbox's unread count changed its title, each Mac its own: counted as a change, two Macs sent the
    Space back and forth every look, for good."""
    state = sync.new_state()
    space = _space("Work", "mail.example")
    assert sync.note_local(state, {"space:w": space}, 1)
    space["pinned"][0]["title"] = "Inbox (3)"
    assert not sync.note_local(state, {"space:w": space}, 2)
    space["pinned"][0]["custom_title"] = "Mail"  # (renamed by you: that is one)
    assert sync.note_local(state, {"space:w": space}, 3)


def test_sync_state_brought_from_another_mac_gets_its_own_device():
    """Copied over with the data folder (Migration Assistant), both Macs had one device id: they wrote the same file,
    each skipped it as its own, and never merged. What it knows of the items stays, so nothing counts as changed."""
    state = sync.new_state()
    sync.note_local(state, {"space:w": _space("Work", "mail.example")}, 1)
    known, device = dict(state["known"]), state["device"]
    sync.own_state(state, "MAC-A")
    assert state["device"] == device  # (first seen here: the same Mac)
    sync.own_state(state, "MAC-A")
    assert state["device"] == device
    sync.own_state(state, "MAC-B")  # (the folder, on another Mac)
    assert state["device"] != device and state["known"] == known and state["machine"] == "MAC-B"
    assert sync.own_state(state, None) is state  # (where there's no telling: left as it is)
