"""Bookmarks and history from Chrome-like browsers and Safari: fake profiles, never your real ones."""

import json
import plistlib
import sqlite3

from browser import browser_import as bi
from browser.models import Folder, Store, Tab, walk_tabs

CHROME_TS = lambda unix: int((unix + 11_644_473_600) * 1_000_000)  # noqa: E731


def _chrome_profile(folder, bar, other=(), history=()):
    folder.mkdir(parents=True)
    (folder / "Bookmarks").write_text(json.dumps({"roots": {
        "bookmark_bar": {"children": bar}, "other": {"children": list(other)}, "synced": {"children": []}}}))
    with sqlite3.connect(folder / "History") as db:
        db.execute("CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER, visit_count INTEGER, "
                   "hidden INTEGER)")
        db.executemany("INSERT INTO urls VALUES (?, ?, ?, ?, 0)", history)


def _url(name, url):
    return {"type": "url", "name": name, "url": url}


def test_a_chrome_profile_becomes_a_space_with_its_folders(tmp_path):
    chrome = tmp_path / "Chrome"
    _chrome_profile(chrome / "Default", [
        _url("GitHub", "https://github.com/"),
        {"type": "folder", "name": "Reading", "children": [_url("HN", "https://news.ycombinator.com/"),
                                                           _url("Settings", "chrome://settings")]}],
        other=[_url("Bank", "https://bank.example/")],
        history=[("https://github.com/", "GitHub", CHROME_TS(1_700_000_000), 42)])
    _chrome_profile(chrome / "Profile 1", [_url("Work", "https://work.example/")])
    (chrome / "Local State").write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"name": "Personal"}, "Profile 1": {"name": "Work"}}}}))
    found = bi.read_chromium("Chrome", chrome)
    assert [s.name for s in found.spaces] == ["Chrome · Personal", "Chrome · Work"]
    personal = found.spaces[0].pinned
    assert isinstance(personal[0], Tab) and personal[0].home_url == "https://github.com/"
    assert isinstance(personal[1], Folder) and [t.title for t in personal[1].children] == ["HN"]  # (no chrome://)
    assert personal[2].name == "Other Bookmarks" and personal[2].children[0].url == "https://bank.example/"
    assert found.history == [{"url": "https://github.com/", "title": "GitHub", "ts": 1_700_000_000, "visits": 42}]
    assert found.bookmark_count == 4


def test_safaris_own_files(tmp_path):
    leaf = lambda title, url: {"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": url,  # noqa: E731
                               "URIDictionary": {"title": title}}
    plist = {"Children": [
        {"WebBookmarkType": "WebBookmarkTypeList", "Title": "BookmarksBar", "Children": [leaf("Apple", "https://apple.com/")]},
        {"WebBookmarkType": "WebBookmarkTypeList", "Title": "BookmarksMenu", "Children": [leaf("Docs", "https://docs.example/")]},
        {"WebBookmarkType": "WebBookmarkTypeList", "Title": "com.apple.ReadingList", "Children": [leaf("Later", "https://later.example/")]}]}
    (tmp_path / "Bookmarks.plist").write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_BINARY))
    nodes = bi.safari_bookmarks(tmp_path / "Bookmarks.plist")
    assert nodes[0].url == "https://apple.com/" and nodes[1].name == "Bookmarks Menu"
    assert [t.url for t in walk_tabs(nodes)] == ["https://apple.com/", "https://docs.example/"]  # (no Reading List)
    with sqlite3.connect(tmp_path / "History.db") as db:
        db.execute("CREATE TABLE history_items (id INTEGER, url TEXT, visit_count INTEGER)")
        db.execute("CREATE TABLE history_visits (history_item INTEGER, visit_time REAL, title TEXT)")
        db.execute("INSERT INTO history_items VALUES (1, 'https://apple.com/', 7)")
        db.executemany("INSERT INTO history_visits VALUES (1, ?, 'Apple')", [(700_000_000.0,), (721_692_800.0,)])
    assert bi.safari_history(tmp_path / "History.db") == [
        {"url": "https://apple.com/", "title": "Apple", "ts": 721_692_800.0 + 978_307_200, "visits": 7}]


SAFARI_EXPORT = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<HTML><META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8"><Title>Bookmarks</Title><H1>Bookmarks</H1>
<DT><H3 FOLDED>Favourites</H3>
<DL><p>
<DT><A HREF="https://apple.com/">Apple</A>
<DT><H3 FOLDED>News</H3>
<DL><p><DT><A HREF="https://bbc.co.uk/news">BBC &amp; more</A></DL><p>
</DL><p>
<DT><H3 FOLDED>Bookmarks Menu</H3>
<DL><p><DT><A HREF="https://docs.example/">Docs</A></DL><p>
<DT><H3 id="com.apple.ReadingList">Reading List</H3>
<DL><p><DT><A HREF="javascript:void(0)">Not a page</A></DL><p>
</HTML>"""

CHROME_EXPORT = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE><H1>Bookmarks</H1>
<DL><p>
    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
    <DL><p>
        <DT><A HREF="https://github.com/">GitHub</A>
    </DL><p>
    <DT><A HREF="https://loose.example/">Loose</A>
</DL><p>"""


def test_an_exported_bookmarks_file_from_safari_or_chrome():
    safari = bi.parse_bookmarks_html(SAFARI_EXPORT)
    assert [n.title if isinstance(n, Tab) else n.name for n in safari] == ["Apple", "News", "Bookmarks Menu"]
    assert safari[1].children[0].title == "BBC & more"
    assert [n.name if isinstance(n, Folder) else n.url for n in safari[2].children] == [
        "https://docs.example/"]  # (the Reading List, holding only a javascript: link, is gone)
    assert len(safari) == 3
    chrome = bi.parse_bookmarks_html(CHROME_EXPORT)
    assert chrome[0].url == "https://github.com/" and chrome[1].name == "Other Bookmarks"


def test_bringing_it_in_replaces_the_starter_spaces_once(tmp_path):
    store = Store(tmp_path / "session.json")
    assert store.is_new and [s.name for s in store.spaces] == ["Personal", "Work"]
    imported = bi.BrowserImport("Chrome", spaces=[bi._space("Chrome", 0, "chrome-default",
                                                            [Tab("https://a.example/", "A", home_url="https://a.example/")])],
                                history=[{"url": "https://a.example/", "title": "A", "ts": 1.0, "visits": 3}])
    bi.apply(store, imported)
    assert [s.name for s in store.spaces] == ["Chrome"] and store.active_space_id == "import-chrome-default"
    assert store.history[0]["url"] == "https://a.example/"
    space = store.spaces[0]
    space.name, space.today = "My Chrome", [Tab("https://open.example/", "Open")]
    store.spaces.append(type(space)("Mine", "#000000"))  # (an empty Space of your own, made later)
    again = bi.BrowserImport("Chrome", spaces=[bi._space("Chrome", 0, "chrome-default",
                                                         [Tab("https://b.example/", "B", home_url="https://b.example/")])])
    bi.apply(store, again)  # (again: the bookmarks refreshed, the rest kept, your empty Space left alone)
    assert [s.name for s in store.spaces] == ["My Chrome", "Mine"]
    assert [t.url for t in store.spaces[0].pinned] == ["https://b.example/"]
    assert [t.url for t in store.spaces[0].today] == ["https://open.example/"]
