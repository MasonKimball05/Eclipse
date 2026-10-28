import json

import pytest

from browser import space_share
from browser.models import Folder, Space, Tab, walk_tabs


def _space():
    work = Folder("Work", [Tab("https://mail.example.com/inbox", "Inbox (3)", home_url="https://mail.example.com/"),
                           Tab("https://docs.example.com/", "Docs", custom_title="Our Docs")])
    feed = Folder("News", [Tab("https://news.example/1", "One")], live={"kind": "feed", "url": "https://news.example/rss",
                                                                       "seen": ["https://news.example/1"]})
    github = Folder("GitHub", [], live={"kind": "github", "query": "review"})
    space = Space("Work / Life", "#3366ff", "ph.briefcase", pinned=[work, feed, github, Tab("note:abc", "A note")],
                  today=[Tab("https://private.example/", "Not shared")], profile="Work",
                  theme={"colors": ["#3366ff", "#ff6633"], "intensity": 0.7, "mode": "dark", "grain": True})
    return space


def test_a_space_is_shared_as_its_pinned_tabs_and_look(tmp_path):
    path = space_share.write(_space(), tmp_path)
    assert path.name == "Work   Life.eclipse-space"
    data = json.loads(path.read_text())
    assert data["format"] == "eclipse-space" and data["name"] == "Work / Life" and data["icon"] == "ph.briefcase"
    work, news = data["pinned"]  # (not the GitHub folder: it's yours; not the note: it isn't a web page)
    assert work["children"] == [{"url": "https://mail.example.com/", "title": "Inbox (3)"},
                                {"url": "https://docs.example.com/", "title": "Our Docs"}]
    assert news == {"type": "folder", "name": "News", "live": {"kind": "feed", "url": "https://news.example/rss"}}
    text = path.read_text()
    assert "private.example" not in text and "seen" not in text
    assert "profile" not in data


def test_it_comes_back_as_a_new_space(tmp_path):
    original = _space()
    shared = space_share.read(space_share.write(original, tmp_path))
    space = space_share.build(shared, "#000000")
    assert space.id != original.id and space.name == "Work / Life" and space.color == "#3366ff"
    assert space.profile == "default" and space.today == []
    assert space.theme == {"colors": ["#3366ff", "#ff6633"], "intensity": 0.7, "mode": "dark", "grain": True}
    work, news = space.pinned
    assert [t.home_url for t in work.children] == ["https://mail.example.com/", "https://docs.example.com/"]
    assert work.children[0].id != original.pinned[0].children[0].id
    assert news.live == {"kind": "feed", "url": "https://news.example/rss", "seen": []} and news.children == []
    assert space_share.summary(shared) == "2 pinned tabs in 2 folders"


def test_nothing_in_a_shared_file_is_trusted():
    shared = space_share.parse({
        "format": "eclipse-space", "name": "‮Evil\x00 <b>Space</b>" + "x" * 100, "icon": "<img src=x>",
        "color": "red; background: url(x)", "theme": {"colors": ["#ffffff", "javascript:x"], "intensity": 9,
                                                      "mode": "neon", "grain": "yes", "extra": {"a": 1}},
        "pinned": [{"url": "javascript:alert(1)", "title": "bad"}, {"url": "file:///etc/passwd"},
                   {"url": "pbext://abc/page.html"}, {"url": "https://ok.example/\npath"},
                   {"url": "https://ok.example/", "title": "Fine​"},
                   {"type": "folder", "name": "Deep", "children": [{"type": "folder", "children": [
                       {"type": "folder", "children": [{"type": "folder", "children": [{"type": "folder",
                        "children": [{"type": "folder", "children": [{"url": "https://too.deep/"}]}]}]}]}]}]},
                   {"type": "folder", "name": "Feed", "live": {"kind": "github", "url": "data:text/html,x"}}, "junk"]})
    assert shared["name"].startswith("Evil <b>Space</b>") and len(shared["name"]) == 60
    assert shared["icon"] == "" and shared["color"] is None
    assert shared["theme"] == {"colors": ["#ffffff"], "intensity": 1.0}
    space = space_share.build(shared, "#123456")
    assert space.color == "#123456"
    assert [t.url for t in walk_tabs(space.pinned)] == ["https://ok.example/"]
    assert next(walk_tabs(space.pinned)).title == "Fine"
    with pytest.raises(ValueError):
        space_share.parse({"format": "something-else"})
    with pytest.raises(ValueError):
        space_share.parse(["not", "a", "space"])


def test_a_file_thats_not_a_space_is_refused(tmp_path):
    for content in (b"not json", b"\xff\xfe", json.dumps({"format": "eclipse-backup"}).encode()):
        bad = tmp_path / "bad.eclipse-space"
        bad.write_bytes(content)
        with pytest.raises(ValueError):
            space_share.read(bad)
    huge = tmp_path / "huge.eclipse-space"
    huge.write_text(json.dumps({"format": "eclipse-space", "pinned": [], "pad": "x" * space_share.MAX_BYTES}))
    with pytest.raises(ValueError, match="too big"):
        space_share.read(huge)
    tabs = [{"url": f"https://example.com/{n}"} for n in range(space_share.MAX_TABS + 50)]
    assert len(space_share.parse({"format": "eclipse-space", "pinned": tabs})["pinned"]) == space_share.MAX_TABS


def test_the_app_says_it_opens_shared_spaces():
    from tools import build_app
    plist = build_app.info_plist()
    kinds = [t for d in plist["CFBundleDocumentTypes"] for t in d["LSItemContentTypes"]]
    assert space_share.TYPE_ID in kinds
    declared = plist["UTExportedTypeDeclarations"][0]
    assert declared["UTTypeIdentifier"] == space_share.TYPE_ID
    assert declared["UTTypeTagSpecification"]["public.filename-extension"] == [space_share.SUFFIX.lstrip(".")]
