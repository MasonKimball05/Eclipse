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
        remotes = sync.read_remotes(self.folder, self.state["device"])
        elsewhere = {k for r in remotes for k in [*(r.get("items") or {}), *(r.get("deleted") or {})]}
        sync.note_local(self.state, self.items, now, elsewhere)
        (self.folder / f"{self.state['device']}.json").write_text(
            json.dumps(sync.device_file(self.state, self.items, now)))
        plan = sync.merge(self.state, remotes, set(self.items))
        for key in plan["delete"]:
            self.items.pop(key, None)
        self.items.update(plan["apply"])
        sync.note_local(self.state, self.items, now, elsewhere)  # (what came isn't a change of this Mac's)
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


def test_an_edit_here_isnt_undone_by_a_mac_whose_clock_runs_fast(tmp_path):
    """Mac B's clock an hour fast: its change was stamped ahead, and an edit on A after it, stamped earlier, was put
    back to B's at A's next look."""
    a, b = _two(tmp_path)
    b.items["space:w"] = _space("Work (B)", "b.example")
    b.look(3700)  # (an hour ahead)
    a.look(200)
    assert a.items["space:w"]["name"] == "Work (B)"
    a.items["space:w"] = _space("Work (A, later)", "a.example")
    a.look(300)  # (really after B's: stamped just after it, not at 300)
    a.look(310)
    assert a.items["space:w"]["name"] == "Work (A, later)"
    b.look(3710)
    assert b.items["space:w"]["name"] == "Work (A, later)"


def test_a_restored_or_new_macs_old_copy_doesnt_beat_the_newer_one(tmp_path):
    """A week-old backup restored on a new Mac (the same Spaces), then sync turned on: each was stamped as just
    changed, and replaced the other Mac's newer copies everywhere."""
    a, _b = _two(tmp_path)
    a.items["space:w"] = _space("Work (newer)", "new.example")
    a.look(500)
    old = Mac(tmp_path, {"space:w": _space("Work (old)", "old.example"), FAVORITES: [], "order": ["w"]})
    old.look(600)  # (a fresh sync state: its copy is older, whatever the clock says)
    assert old.items["space:w"]["name"] == "Work (newer)"
    a.look(610)
    assert a.items["space:w"]["name"] == "Work (newer)"


def test_a_live_folders_refresh_and_what_its_seen_arent_synced():
    """Each refresh stamped the folder's last look into its Space: every refresh was an edit, sent back and forth
    over newer ones. And a JSON Live Folder's title setting was taken for a page title, never synced."""
    from browser.models import Folder
    folder = Folder("Feed", [], "f1", True, {"kind": "json", "url": "https://api.example/x", "title": "name",
                                             "seen": ["a"], "refreshed": 123.0})
    node = sync._node(folder)
    assert "refreshed" not in node["live"] and "seen" not in node["live"]
    renamed = dict(node, live={**node["live"], "title": "label"})
    assert sync.digest(node) != sync.digest(renamed)


def test_a_restored_backup_doesnt_delete_whats_been_made_since_everywhere(tmp_path, monkeypatch):
    """Restoring a backup kept sync's memory of the Spaces since: missing here, they were sent as deleted, from every
    Mac. A restore now forgets what sync knew, so the next look takes the others' copies instead."""
    from browser import backup, storage
    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)
    storage.write_json(tmp_path / "sync-state.json", {"device": "d1", "known": {"space:new": {"hash": "h",
                                                     "modified": 5.0}}, "deleted": {"space:x": 1.0}})
    backup._forget_what_sync_knew(tmp_path)
    state = storage.read_json(tmp_path / "sync-state.json")
    assert state["device"] == "d1" and state["known"] == {} and state["deleted"] == {}


def _apply_window(qapp, spaces):
    """A Sync with a stand-in window holding `spaces`: what apply() changes, and the pages it closes."""
    from types import SimpleNamespace
    from PyQt6.QtCore import QObject
    from browser.models import Space
    closed = []
    store = SimpleNamespace(spaces=spaces, favorites=[], active_space_id=spaces[0].id, _changed=lambda: None,
                            remove_from_split=lambda tab: None)
    store.space = lambda sid: next((s for s in store.spaces if s.id == sid), None)
    store.active_space = spaces[0]
    window = SimpleNamespace(current_tab=None, _remove_view=closed.append, apply_theme=lambda: None,
                             _restore_space_tab=lambda: None, _activate_fallback=lambda: None)
    syncer = sync.Sync.__new__(sync.Sync)
    QObject.__init__(syncer)
    syncer.store, syncer.window = store, window
    return syncer, closed


def test_another_macs_edit_keeps_a_live_folders_pages_and_folders_looks(qapp):
    """A Space with a Live Folder, changed on another Mac: the folder's items were taken for gone, their pages closed
    though still listed in it. And every folder came back with no color or icon, open."""
    from browser.models import Folder, Space, Tab
    item = Tab("https://github.com/o/r/pull/1", "PR", tab_id="t-pr")
    live = Folder("Reviews", [item], "f-live", True, {"kind": "github", "query": "review"})
    plain = Folder("Work", [Tab("https://docs.example/", "Docs", tab_id="t-docs")], "f-work", expanded=False,
                   color="#3b82f6", icon="ph.star")
    space = Space("Home", "#3366ff", "", "s1")
    space.pinned = [live, plain]
    syncer, closed = _apply_window(qapp, [space])
    data = sync.snapshot(syncer.store)["space:s1"]
    data = {**data, "name": "Home (renamed)"}
    syncer.apply({"apply": {"space:s1": data}, "delete": []})
    assert space.name == "Home (renamed)" and closed == []
    live2, plain2 = space.pinned
    assert live2.children == [item]
    assert (plain2.color, plain2.icon, plain2.expanded) == ("#3b82f6", "ph.star", False)


def test_a_deleted_space_isnt_brought_back_by_a_mac_whose_clock_runs_fast(tmp_path):
    """Mac B's clock five minutes fast: its edit was stamped ahead, and A's deleting the Space after it, stamped now
    (earlier), lost to it, so the Space came straight back."""
    a, b = _two(tmp_path)
    b.items["space:w"] = _space("Work (B)", "b.example")
    b.look(400)  # (300 ahead)
    a.look(150)
    assert a.items["space:w"]["name"] == "Work (B)"
    del a.items["space:w"]
    a.items["order"] = []
    a.look(160)
    a.look(170)
    assert "space:w" not in a.items
    b.look(410)
    assert "space:w" not in b.items


def test_a_failed_backup_before_syncing_leaves_it_to_try_again(tmp_path, monkeypatch, qapp):
    """merge() counted what came as done before the backup and apply: when the backup failed, the next look skipped
    it, took this Mac's older copy for a change of its own, and sent it out over the newer one."""
    from browser import storage
    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)
    folder = tmp_path / "icloud"
    folder.mkdir()
    a = Mac(folder, {"space:w": _space("Work", "mail.example"), FAVORITES: [], "order": ["w"]})
    a.look(100)
    syncer = sync.Sync.__new__(sync.Sync)
    syncer.state = sync.new_state()
    syncer.state["known"] = {"space:w": {"hash": "old", "modified": 50.0}}
    monkeypatch.setattr(sync, "sync_folder", lambda: folder)
    monkeypatch.setattr(syncer, "on", lambda: True, raising=False)
    monkeypatch.setattr(syncer, "_write", lambda *_: None, raising=False)
    monkeypatch.setattr(syncer, "_state_path", lambda: tmp_path / "state.json", raising=False)
    monkeypatch.setattr(sync, "snapshot", lambda store: {"space:w": _space("Work (old)", "old.example")})
    syncer.store, syncer.window = None, None

    def fails():
        raise OSError("disk full")
    monkeypatch.setattr(syncer, "_back_up", fails, raising=False)
    monkeypatch.setattr(syncer, "_still_downloading", lambda f: False, raising=False)
    syncer.run()
    # (still known as this Mac's copy from before: the other Mac's newer one is taken at the next look)
    assert sync.merge(syncer.state, sync.read_remotes(folder, syncer.state["device"]))["apply"]


def test_a_restored_backup_wins_over_the_other_macs_copies(tmp_path, monkeypatch):
    """Restoring a "before-sync" backup to undo a bad sync: with sync's memory forgotten, the other Mac's copy (the
    bad one) was taken over what was restored, whatever its time."""
    from browser import backup, storage
    storage.write_json(tmp_path / "session.json", {"spaces": [{"id": "w", "name": "Work"}]})
    storage.write_json(tmp_path / "sync-state.json", {"device": "d1", "known": {"space:w": {"hash": "h",
                                                     "modified": 5.0}}, "deleted": {}})
    backup._forget_what_sync_knew(tmp_path)
    state = storage.read_json(tmp_path / "sync-state.json")
    assert set(state["claim"]) >= {"space:w", FAVORITES, "order"}
    # And so: stamped as this Mac's change, the other Mac's older copy doesn't replace it
    a, _b = _two(tmp_path / "x") if (tmp_path / "x").mkdir() is None else None
    restored = Mac(tmp_path / "x", {"space:w": _space("Work (restored)", "good.example"), FAVORITES: [],
                                    "order": ["w"]})
    restored.state["device"], restored.state["claim"] = "d1", state["claim"]
    remotes = sync.read_remotes(restored.folder, "d1")
    elsewhere = {k for r in remotes for k in r.get("items") or {}} - set(state["claim"])
    sync.note_local(restored.state, restored.items, 900, elsewhere)
    assert not sync.merge(restored.state, remotes, set(restored.items))["apply"]
