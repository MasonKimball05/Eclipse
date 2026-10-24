"""Arc import (against a fake Arc folder), Boosts, Notes and storage."""

import json
import sqlite3

import pytest

from browser import arc_import, storage
from browser.boosts import Boost, BoostManager, site_of
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


def test_boosts_match_sites_and_collect_zaps(store):
    manager = BoostManager(store, None)
    manager.save(Boost("example.com", css="h1 { color: red }"))
    assert manager.for_site("docs.example.com") and not manager.for_site("notexample.com")
    boost = manager.add_zapped("news.com", "div.ad")
    manager.add_zapped("news.com", "div.ad")
    assert boost.zapped == ["div.ad"]
    assert len(store.settings["boosts"]) == 2
    source = BoostManager._script(boost).sourceCode()
    assert '"news.com"' in source and "div.ad" in source and "new Function" in source
    assert site_of("https://www.Example.com/page") == "example.com"


def test_note_saves_markdown_and_titles_itself(data_dir):
    from browser.notes import NoteEditor, new_note_url
    url = new_note_url()
    editor = NoteEditor(url)
    assert editor.title == "Untitled Note"
    editor.insertPlainText("# Trip plan\n\nPack bags")  # like typing
    assert editor.title == "Trip plan"
    editor.save()
    reopened = NoteEditor(url)
    assert "Pack bags" in reopened.toPlainText()


def test_stale_note_editor_does_not_overwrite_newer_text(data_dir):
    from browser.notes import NoteEditor, new_note_url
    url = new_note_url()
    stale, fresh = NoteEditor(url), NoteEditor(url)  # e.g. the same note opened twice
    fresh.insertPlainText("Newer text")
    fresh.save()
    stale.save()  # never edited, so it must not write its empty copy over the file
    assert "Newer text" in NoteEditor(url).toPlainText()


def test_write_json_is_atomic(data_dir):
    path = storage.data_dir() / "x.json"
    storage.write_json(path, {"a": 1})
    assert storage.read_json(path) == {"a": 1}
    assert not path.with_suffix(".tmp").exists()
