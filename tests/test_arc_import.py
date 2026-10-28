"""Arc import, against a fake Arc folder (never your real one)."""

import json
import plistlib
import sqlite3

import pytest

from browser import arc_import, storage
from browser.models import Folder, walk_tabs


def _arc_folder(tmp_path):
    """A tiny Arc data folder: 2 Spaces, a nested folder, favorites, history and a favicon."""
    arc = tmp_path / "Arc"
    profile = arc / "User Data" / "Default"
    profile.mkdir(parents=True)

    def tab(i, url, title, parent, renamed=None):
        return [i, {"id": i, "parentID": parent, "title": renamed, "childrenIds": [],
                    "data": {"tab": {"savedURL": url, "savedTitle": title}}}]

    def group(i, title, children, data):
        return [i, {"id": i, "title": title, "childrenIds": children, "data": data}]

    items = (
        group("fav", None, ["f1"], {"itemContainer": {"containerType": {"topApps": {}}}})
        + tab("f1", "https://mail.example.com/", "Mail", "fav")
        + group("p1", None, ["t1", "folder"], {"itemContainer": {"containerType": {"spaceItems": {}}}})
        + tab("t1", "https://github.com/", "GitHub", "p1", renamed="Code")
        + group("folder", "Projects", ["t2", "sub"], {"list": {}})
        + tab("t2", "https://a.com/", "A", "folder")
        + group("sub", "Nested", ["t3"], {"list": {}})
        + tab("t3", "https://b.com/", "B", "sub")
        + group("u1", None, ["t4"], {"itemContainer": {"containerType": {"spaceItems": {}}}})
        + tab("t4", "https://today.com/", "Today", "u1")
        + group("p2", None, [], {"itemContainer": {"containerType": {"spaceItems": {}}}})
        + group("u2", None, [], {"itemContainer": {"containerType": {"spaceItems": {}}}})
    )
    spaces = [
        "s1", {"id": "s1", "title": "Home", "containerIDs": ["pinned", "p1", "unpinned", "u1"],
               "customInfo": {"iconType": {"emoji_v2": "🏠"}, "windowTheme": {"primaryColorPalette": {
                   "midTone": {"red": 1.0, "green": 0.0, "blue": 0.5}}}}},
        "s2", {"id": "s2", "title": "", "containerIDs": ["pinned", "p2", "unpinned", "u2"],
               "customInfo": {"iconType": {"icon": "planet"}}},
    ]
    sidebar = {"sidebar": {"containers": [{"global": {}}, {
        "spaces": spaces, "items": items, "topAppsContainerIDs": [{"default": True}, "fav"]}]}}
    (arc / "StorableSidebar.json").write_text(json.dumps(sidebar))

    with sqlite3.connect(profile / "History") as db:
        db.execute("CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER, hidden INTEGER, "
                   "visit_count INTEGER DEFAULT 1)")
        chrome_now = (1_700_000_000 + arc_import.CHROME_EPOCH_OFFSET) * 1_000_000
        db.executemany("INSERT INTO urls (url, title, last_visit_time, hidden) VALUES (?, ?, ?, 0)", [
            ("https://visited.com/", "Visited", chrome_now), ("chrome://settings", "x", chrome_now)])
    png = storage.Path(__file__).parent / "fixtures" / "dot.png"
    with sqlite3.connect(profile / "Favicons") as db:
        db.execute("CREATE TABLE icon_mapping (page_url TEXT, icon_id INTEGER)")
        db.execute("CREATE TABLE favicon_bitmaps (icon_id INTEGER, image_data BLOB, width INTEGER)")
        db.execute("INSERT INTO icon_mapping VALUES ('https://github.com/', 1)")
        db.execute("INSERT INTO favicon_bitmaps VALUES (1, ?, 16)", (png.read_bytes(),))
    ext = profile / "Extensions" / "abc" / "1.0"
    ext.mkdir(parents=True)
    (ext / "manifest.json").write_text(json.dumps({"name": "Some Blocker", "manifest_version": 3}))
    return arc


@pytest.fixture
def fake_arc(tmp_path, monkeypatch):
    monkeypatch.setattr(arc_import, "ARC_DIR", _arc_folder(tmp_path))
    monkeypatch.setattr(arc_import, "ARC_PREFERENCES", tmp_path / "company.thebrowser.Browser.plist")  # (nor yours)


def _arc_extras(tmp_path):
    """Arc's settings, archive, routing rules and a second profile, in the fake folder."""
    arc = tmp_path / "Arc"
    (tmp_path / "company.thebrowser.Browser.plist").write_bytes(plistlib.dumps({
        "autoArchiveTimeThreshold": "sevenDays", "openExternalLinksInLittleBrowserEnabled": False,
        "renameDownloadsEnabled": True, "renamePinnedTabsEnabled": False, "somethingElse": 3}))
    (arc / "StorableArchiveItems.json").write_text(json.dumps({"version": 1, "items": [
        "a1", {"archivedAt": 800_000_000.0, "source": {"space": {"_0": "s1"}},
               "sidebarItem": {"data": {"tab": {"savedURL": "https://old.example/", "savedTitle": "Old"}}}},
        "a2", {"archivedAt": 810_000_000.0, "source": {"space": {"_0": "s2"}},
               "sidebarItem": {"data": {"tab": {"savedURL": "https://newer.example/", "savedTitle": "Newer"}}}},
        "a3", {"archivedAt": 805_000_000.0, "sidebarItem": {"data": {"tab": {"savedURL": "javascript:x"}}}}]}))
    (arc / "StorableLinkRouting.json").write_text(json.dumps({"version": 1, "rules": [
        {"destination": {"space": {"_0": "s2"}}, "sourceComponents": [
            {"componentType": {"urlMatch": {"_0": {"contains": {}}, "_1": "zoom.us"}}}]},
        {"destination": {"space": {"_0": {"mostRecent": {}}}}, "sourceComponents": [
            {"componentType": {"urlMatch": {"_0": {"contains": {}}, "_1": "meet.google.com"}}}]}]}))
    (arc / "User Data" / "Local State").write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": "Your Chromium"}, "Profile 1": {"name": "School Stuff"}}}}))
    sidebar = json.loads((arc / "StorableSidebar.json").read_text())
    spaces = sidebar["sidebar"]["containers"][1]["spaces"]
    spaces[3]["profile"] = {"custom": {"_0": {"directoryBasename": "Profile 1"}}}
    spaces[1]["profile"] = {"default": True}
    (arc / "StorableSidebar.json").write_text(json.dumps(sidebar))


def test_arc_import_reads_everything(fake_arc):
    arc = arc_import.read_arc()
    home, other = arc.spaces
    assert (home.name, home.icon, home.color) == ("Home", "🏠", "#ff0080")
    assert other.name == "Space 2" and other.icon == "ph.planet"  # SF Symbol mapped to a line icon
    code, projects = home.pinned
    assert code.custom_title == "Code" and code.home_url == "https://github.com/"
    assert isinstance(projects, Folder) and projects.name == "Projects" and not projects.expanded
    assert isinstance(projects.children[1], Folder) and projects.children[1].children[0].url == "https://b.com/"
    assert [t.url for t in home.today] == ["https://today.com/"]
    assert [t.url for t in arc.favorites] == ["https://mail.example.com/"]
    assert arc.tab_count == 5 and arc.folder_count == 2  # 1 favorite + 3 pinned + 1 today
    assert [h["url"] for h in arc.history] == ["https://visited.com/"]
    assert "https://github.com/" in arc.favicons
    assert arc.extensions == ["Some Blocker"]


def test_arc_import_apply_is_repeatable(fake_arc, store):
    arc_import.apply(store, arc_import.read_arc())
    assert [s.name for s in store.spaces] == ["Home", "Space 2"]  # empty starter Spaces dropped
    assert [t.url for t in store.favorites] == ["https://mail.example.com/"]  # replaced the default favorites
    assert store.active_space.name == "Home"
    assert storage.favicon("https://github.com/x") is not None

    arc_import.apply(store, arc_import.read_arc())  # importing again replaces, not duplicates
    assert len(store.spaces) == 2
    assert len(list(walk_tabs(store.spaces[0].pinned))) == 3


def test_arcs_settings_archive_rules_and_profiles_come_too(fake_arc, store, tmp_path):
    _arc_extras(tmp_path)
    arc = arc_import.read_arc()
    assert arc.settings == {"archive_after_hours": 168, "external_links": "tab", "tidy_downloads": True,
                            "tidy_titles": False}
    assert [(e["url"], e["space_id"]) for e in arc.archive] == [("https://newer.example/", "arc-s2"),
                                                               ("https://old.example/", "arc-s1")]
    assert arc.archive[0]["archived_at"] == 810_000_000 + arc_import.APPLE_EPOCH_OFFSET  # (Arc counts from 2001)
    assert arc.routes == [{"match": "zoom.us", "space": "arc-s2"}]  # (not "whichever Space is open")
    assert [s.profile for s in arc.spaces] == ["default", "school-stuff"]
    store.settings["air_traffic"] = [{"match": "mine.example", "space": "x"}]
    arc_import.apply(store, arc)
    assert store.settings["archive_after_hours"] == 168 and store.settings["external_links"] == "tab"
    assert [e["url"] for e in store.archive[:2]] == ["https://newer.example/", "https://old.example/"]
    assert store.settings["air_traffic"] == [{"match": "mine.example", "space": "x"}, {"match": "zoom.us", "space": "arc-s2"}]
    assert store.settings[arc_import.OFFERED] is True
    arc_import.apply(store, arc_import.read_arc())  # (again: nothing doubles)
    assert len(store.archive) == 2 and len(store.settings["air_traffic"]) == 2


def test_no_arc_settings_or_archive_is_fine(fake_arc):
    arc = arc_import.read_arc()  # (the fake folder has neither, and no preferences file)
    assert arc.settings == {} and arc.archive == [] and arc.routes == []
    assert {s.profile for s in arc.spaces} == {"default"}


def test_a_launch_that_missed_the_welcome_still_offers_arc_once(fake_arc, store, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(storage, "throwaway", lambda: False)
    asked = []
    monkeypatch.setattr(arc_import.QTimer, "singleShot", lambda ms, slot: asked.append(slot))
    store.is_new = False  # (Eclipse had been opened before: no welcome)
    feature = arc_import.ArcImportFeature.__new__(arc_import.ArcImportFeature)
    feature.window = SimpleNamespace(store=store)
    feature.ready()
    assert len(asked) == 1 and store.settings[arc_import.OFFERED]
    feature.ready()  # (once: not every launch)
    assert len(asked) == 1
    # Not when you've Spaces of your own already
    del store.settings[arc_import.OFFERED]
    from browser.models import Tab
    store.spaces[0].pinned.append(Tab("https://mine.example/", "Mine"))
    feature.ready()
    assert len(asked) == 1
