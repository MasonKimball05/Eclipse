from browser.live_folders import apply_items, find_feed_link, mark_seen, parse_feed
from browser.models import Folder, Tab, node_from_dict

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Example Blog</title>
<item><title>Second post</title><link>https://blog.example/2</link></item>
<item><title>First   post</title><link>https://blog.example/1</link></item></channel></rss>"""
ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Feed</title>
<entry><title>Hello</title><link rel="alternate" href="https://atom.example/hello"/>
<link rel="edit" href="https://atom.example/edit"/></entry></feed>"""


def test_parse_rss_and_atom():
    assert parse_feed(RSS) == ("Example Blog", [("Second post", "https://blog.example/2"),
                                                ("First post", "https://blog.example/1")])
    assert parse_feed(ATOM) == ("Atom Feed", [("Hello", "https://atom.example/hello")])


def test_not_a_feed():
    import pytest
    for data in (b"<html><body>hi</body></html>", b"not xml"):
        with pytest.raises(ValueError):
            parse_feed(data)


def test_feed_discovery():
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    assert find_feed_link(html, "https://site.example/blog/") == "https://site.example/feed.xml"
    assert find_feed_link("<html></html>", "https://x.example") is None


def test_refreshing_keeps_tabs_and_marks_new_ones():
    folder = Folder("Blog", live={"kind": "feed", "url": "https://blog.example/feed"})
    apply_items(folder, [("One", "https://b/1")])
    first = folder.children[0]
    assert not first.fresh  # the first fill isn't "new"
    dropped = apply_items(folder, [("Two", "https://b/2"), ("One (edited)", "https://b/1")])
    assert folder.children[1] is first and first.title == "One (edited)"
    assert folder.children[0].fresh and not dropped
    mark_seen(folder, folder.children[0])
    assert not folder.children[0].fresh
    dropped = apply_items(folder, [("Two", "https://b/2")])
    assert dropped == [first]
    # Saved and loaded again, the fresh/seen state survives
    folder.children[0].fresh = False
    apply_items(folder, [("Two", "https://b/2"), ("Three", "https://b/3")])
    again = node_from_dict(folder.to_dict())
    assert [t.fresh for t in again.children] == [False, True]
    assert isinstance(again, Folder) and again.live["kind"] == "feed"


def test_relative_links_resolve_against_the_feed():
    feed = b"""<rss version="2.0"><channel><title>T</title><item><title>A</title><link>/posts/a</link></item></channel></rss>"""
    assert parse_feed(feed, "https://blog.example/feed.xml")[1] == [("A", "https://blog.example/posts/a")]


def test_a_tab_you_clicked_through_stays():
    folder = Folder("Blog", live={"kind": "feed", "url": "x"})
    apply_items(folder, [("One", "https://b/1")])
    tab = folder.children[0]
    tab.url = "https://b/1/comments"  # you followed a link in it
    dropped = apply_items(folder, [("One", "https://b/1")])
    assert folder.children == [tab] and not dropped


def test_nothing_can_be_moved_into_a_live_folder(store):
    folder = store.add_folder(store.active_space, "Feed")
    folder.live = {"kind": "feed", "url": "x"}
    tab = Tab("https://mine.example/")
    store.add_tab(tab)
    from browser.models import PINNED
    store.move_tab(tab, PINNED, folder=folder)
    assert tab not in folder.children


def test_only_web_links_become_tabs():
    feed = b"""<rss version="2.0"><channel><title>T</title>
      <item><title>A</title><link>javascript:alert(1)</link></item>
      <item><title>B</title><link>note:abc</link></item>
      <item><title>C</title><link>https://ok.example/c</link></item></channel></rss>"""
    assert parse_feed(feed, "https://blog.example/feed.xml")[1] == [("C", "https://ok.example/c")]


def test_an_item_listed_twice_is_one_tab():
    folder = Folder("Blog", live={"kind": "feed", "url": "x"})
    apply_items(folder, [("One", "https://b/1"), ("One again", "https://b/1")])
    assert len(folder.children) == 1
    # A folder that somehow holds the same item twice gives the extra one back, to be closed
    extra = Tab("https://b/1", "One", home_url="https://b/1")
    folder.children.append(extra)
    assert apply_items(folder, [("One", "https://b/1")]) == [extra]
