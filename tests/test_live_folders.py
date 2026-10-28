import pytest

from browser.models import Folder, Tab, node_from_dict, walk_tabs
from browser.live_folders import apply_items, find_feed_link, mark_seen, parse_feed
from browser.timefmt import portable


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Example Blog</title>
<item><title>Second post</title><link>https://blog.example/2</link></item>
<item><title>First   post</title><link>https://blog.example/1</link></item></channel></rss>"""


ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Feed</title>
<entry><title>Hello</title><link rel="alternate" href="https://atom.example/hello"/>
<link rel="edit" href="https://atom.example/edit"/></entry></feed>"""


def _row(title, repo, number, pr=False):
    return {"title": title, "url": f"https://github.com/me/{repo}/{'pull' if pr else 'issues'}/{number}",
            "repository": {"name": repo}, "number": number, "isPullRequest": pr}


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


def test_github_addresses_follow_their_hidden_feeds():
    from browser.live_folders import feed_name, github_feed, github_repo
    assert github_feed("https://github.com/qt/qtwebengine") == "https://github.com/qt/qtwebengine/releases.atom"
    assert github_feed("github.com/qt/qtwebengine.git") == "https://github.com/qt/qtwebengine/releases.atom"
    assert github_feed("https://github.com/qt/qtwebengine/releases/tag/v6.11") == \
        "https://github.com/qt/qtwebengine/releases.atom"
    assert github_feed("https://github.com/qt/qtwebengine/tags") == "https://github.com/qt/qtwebengine/tags.atom"
    assert github_feed("https://github.com/qt/qtwebengine/commits/dev") == \
        "https://github.com/qt/qtwebengine/commits/dev.atom"
    assert github_feed("https://github.com/torvalds") == "https://github.com/torvalds.atom"
    # One file's history: the branch's feed (a file has none)
    assert github_feed("https://github.com/a/b/commits/main/src/x.py") == "https://github.com/a/b/commits/main.atom"
    # Not repos or people, other sites, and feeds already
    for url in ("https://github.com/settings/profile", "https://github.com/explore", "https://github.com/apps/dependabot",
                "https://github.com/users/foo/projects/1", "https://github.com/codespaces", "https://gitlab.com/a/b",
                "https://github.com/qt/qtwebengine/releases.atom", "https://github.com/"):
        assert github_feed(url) is None, url
    assert github_repo("https://www.github.com/qt/qtwebengine/pull/3") == ("qt", "qtwebengine")
    assert github_repo("https://github.com/apps/dependabot") is None
    assert github_repo("https://github.com/notifications/beta") is None and github_repo("https://github.com/qt") is None
    assert feed_name("https://github.com/qt/qtwebengine") == "qtwebengine"


def test_github_sections_sort_everything_into_sub_folders():
    from browser.live_folders import github_sections
    review = _row("Fix login", "parliament", 7, pr=True)
    assigned = _row("Crash on start", "eclipse", 3)
    results = {
        "review": [review], "mine": [], "assigned": [assigned],
        "yours": [_row("Dark mode", "eclipse", 9), assigned, _row("Bump deps", "eclipse", 10, pr=True),
                  _row("Menu", "shelf", 1)],
    }
    sections = github_sections(results)
    assert [name for name, _ in sections] == ["Review Requested", "Assigned to Me", "eclipse", "shelf"]  # (no empty one)
    assert sections[0][1] == [("Fix login · parliament", review["url"])]
    eclipse = dict(sections)["eclipse"]
    # Its pull requests first, numbered; what's already in a section above isn't repeated
    assert [t for t, _ in eclipse] == ["PR #10 Bump deps", "#9 Dark mode"]


def test_nested_items_keep_tabs_and_sub_folders_across_refreshes():
    folder = Folder("GitHub", live={"kind": "github", "query": "overview"})
    a, b = "https://github.com/me/x/issues/1", "https://github.com/me/x/issues/2"
    apply_items(folder, [("Assigned to Me", [("One", a)]), ("x", [("Two", b)])])
    assigned, repo = folder.children
    assert isinstance(assigned, Folder) and [t.url for t in assigned.children] == [a]
    assert not any(t.fresh for t in walk_tabs(folder.children))  # (the first look: nothing's new)
    tab_one = assigned.children[0]
    assigned.expanded = True
    c = "https://github.com/me/x/issues/3"
    dropped = apply_items(folder, [("Assigned to Me", [("Two", b), ("One", a)]), ("x", [("Three", c)])])
    assert folder.children[0] is assigned and assigned.expanded  # the same sub-folder, still open
    assert assigned.children[1] is tab_one  # the same tab, moved
    new = folder.children[1].children[0]
    assert new.url == c and new.fresh and not dropped
    # A section that empties goes; its tabs are dropped
    dropped = apply_items(folder, [("x", [("Three", c)])])
    assert [f.name for f in folder.children] == ["x"] and {t.url for t in dropped} == {a, b}
    # Seen state, and loading, reach into sub-folders
    mark_seen(folder, new)
    again = node_from_dict(folder.to_dict())
    assert not any(t.fresh for t in walk_tabs(again.children))


def test_nothing_moves_into_or_out_of_a_live_folders_sub_folders(store):
    from browser.models import PINNED
    space = store.active_space
    live = store.add_folder(space, "GitHub")
    live.live = {"kind": "github", "query": "overview", "refreshed": 1}
    apply_items(live, [("x", [("One", "https://github.com/me/x/issues/1")])])
    sub = live.children[0]
    assert store.live_folder_of(sub) is live and store.live_folder_of(sub.children[0]) is live
    loose = Tab("https://example.com")
    store.add_tab(loose, PINNED)
    store.move_tab(loose, PINNED, folder=sub)
    assert loose not in sub.children
    store.move_tab(sub, PINNED, index=0)
    assert sub in live.children and sub not in space.pinned


def test_notifications_become_a_section_of_web_pages():
    from browser.live_folders import github_sections, notification_rows
    api = [{"subject": {"title": "Fix login", "url": "https://api.github.com/repos/me/x/pulls/7", "type": "PullRequest"},
            "repository": {"name": "x", "html_url": "https://github.com/me/x"}},
           {"subject": {"title": "v2.0", "url": "https://api.github.com/repos/me/x/releases/99", "type": "Release"},
            "repository": {"name": "x", "html_url": "https://github.com/me/x"}},
           {"subject": {"title": "Bug", "url": "https://api.github.com/repos/me/y/issues/3", "type": "Issue"},
            "repository": {"name": "y", "html_url": "https://github.com/me/y"}}]
    rows = notification_rows(api)
    assert [r["url"] for r in rows] == ["https://github.com/me/x/pull/7", "https://github.com/me/x#notification-",
                                        "https://github.com/me/y/issues/3"]  # (a release: its repo's page)
    review = {"title": "Fix login", "url": "https://github.com/me/x/pull/7", "repository": {"name": "x"}}
    sections = dict(github_sections({"review": [review], "notifications": api}))
    # Already under Review Requested: not again under Notifications
    assert [t for t, _ in sections["Notifications"]] == ["v2.0 · x", "Bug · y"]


def test_an_empty_github_folder_drops_its_old_sub_folders_and_keeps_seen():
    folder = Folder("GitHub", live={"kind": "github", "query": "overview"})
    a = "https://github.com/me/x/issues/1"
    apply_items(folder, [("x", [("One", a)])])
    apply_items(folder, [])  # inbox zero
    assert folder.children == []
    apply_items(folder, [("x", [("One", a)])])
    assert not folder.children[0].children[0].fresh  # (seen before: not new again)


def test_a_repo_named_like_a_section_goes_by_its_full_name():
    from browser.live_folders import github_sections
    note = {"title": "n", "url": "https://github.com/o/x/pull/1", "repository": {"name": "x"}}
    mine = {"title": "i", "url": "https://github.com/me/Notifications/issues/2",
            "repository": {"name": "Notifications", "nameWithOwner": "me/Notifications"}, "number": 2}
    api = [{"id": "7", "subject": {"title": "n", "url": "https://api.github.com/repos/o/x/pulls/1"},
            "repository": {"name": "x", "html_url": "https://github.com/o/x"}}]
    names = [name for name, _ in github_sections({"notifications": api, "yours": [mine]})]
    assert names == ["Notifications", "me/Notifications"]
    assert note  # (the notification's own section keeps its name)


def test_notifications_without_their_own_page_each_stay_separate():
    from browser.live_folders import notification_rows
    api = [{"id": str(i), "subject": {"title": f"v{i}", "url": None, "type": "Release"},
            "repository": {"name": "x", "html_url": "https://github.com/me/x"}} for i in (1, 2)]
    urls = [r["url"] for r in notification_rows(api)]
    assert urls == ["https://github.com/me/x#notification-1", "https://github.com/me/x#notification-2"]


def test_calendar_time_zones_and_weekly_repeats():
    import datetime
    from zoneinfo import ZoneInfo
    from browser.live_folders import upcoming_events
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:a
SUMMARY:Standup
DTSTART;TZID=America/New_York:20260105T090000
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
END:VEVENT
BEGIN:VEVENT
UID:b
SUMMARY:Ended
DTSTART:20260105T090000Z
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
END:VCALENDAR"""
    now = datetime.datetime(2026, 10, 27, 12, 0)  # a Tuesday
    items = upcoming_events(ics, "https://cal.example/feed.ics", now=now)
    assert len(items) == 1 and "Standup" in items[0][0]  # (the daily one ran out long ago)
    # Next: Wednesday Oct 28, 9:00 New York time, shown in this Mac's time
    expected = datetime.datetime(2026, 10, 28, 9, 0, tzinfo=ZoneInfo("America/New_York")).astimezone()
    assert items[0][0].startswith(expected.strftime(portable("%b %-d")))
    assert expected.strftime(portable("%-I:%M %p")) in items[0][0]


def test_notifications_that_cant_be_read_are_kept():
    from browser.live_folders import github_sections, notification_rows
    kept = [{"title": "Old one · x", "url": "https://github.com/me/x/issues/1"}]
    assert notification_rows(kept) == kept
    assert dict(github_sections({"notifications": kept}))["Notifications"][0][1] == "https://github.com/me/x/issues/1"



@pytest.mark.parametrize("page, feed", [
    ("https://bsky.app/profile/jay.bsky.team", "https://bsky.app/profile/jay.bsky.team/rss"),
    ("https://mastodon.social/@Gargron", "https://mastodon.social/@Gargron.rss"),
    ("https://medium.com/@someone", "https://medium.com/feed/@someone"),
    ("https://www.reddit.com/r/python/top/", "https://www.reddit.com/r/python/.rss"),
    ("old.reddit.com/u/spez", "https://www.reddit.com/user/spez/.rss"),
    ("https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
     "https://www.youtube.com/feeds/videos.xml?channel_id=UC_x5XG1OV2P6uZZ5FSM9Ttw"),
    ("https://www.youtube.com/playlist?list=PL123", "https://www.youtube.com/feeds/videos.xml?playlist_id=PL123"),
    ("https://gitlab.com/gitlab-org/gitlab-runner/-/tree/main", "https://gitlab.com/gitlab-org/gitlab-runner/-/releases.atom"),
    ("https://gitlab.com/group/sub/project", "https://gitlab.com/group/sub/project/-/releases.atom"),
    ("https://pypi.org/project/PyQt6/", "https://pypi.org/rss/project/pyqt6/releases.xml"),
    ("https://stackoverflow.com/questions/tagged/pyqt6", "https://stackoverflow.com/feeds/tag/pyqt6"),
    ("https://unix.stackexchange.com/questions/tagged/zsh", "https://unix.stackexchange.com/feeds/tag/zsh"),
    ("https://news.ycombinator.com/", "https://news.ycombinator.com/rss"),
    ("https://github.com/qt/qtwebengine", "https://github.com/qt/qtwebengine/releases.atom"),
])
def test_well_known_pages_have_their_sites_feed(page, feed):
    from browser.live_folders import known_feed
    assert known_feed(page) == feed


@pytest.mark.parametrize("page", ["https://www.youtube.com/@mkbhd", "https://x.com/@someone", "https://example.com/blog",
                                  "https://gitlab.com/explore/projects", "https://news.ycombinator.com/item?id=1"])
def test_other_pages_are_looked_at_for_their_feed(page):
    from browser.live_folders import known_feed
    assert known_feed(page) is None


def test_a_guessed_feed_that_isnt_one_falls_back_to_the_page():
    from browser.live_folders import LiveFolders
    pages = {"https://blog.example/@writer.rss": b"<html>not found</html>",
             "https://blog.example/@writer": b'<html><head><link rel="alternate" type="application/rss+xml" '
                                              b'href="/feeds/writer.xml"></head></html>',
             "https://blog.example/feeds/writer.xml": b"<rss><channel><title>Writer</title><item><title>Hi</title>"
                                                      b"<link>https://blog.example/hi</link></item></channel></rss>"}
    asked, results = [], []

    class Fake:
        _feed = LiveFolders._feed

        def _get(self, url, got, fail):
            asked.append(url)
            got(pages[url], url) if url in pages else fail("404")
    Fake()._feed("https://blog.example/@writer", lambda items, title="", error="": results.append((items, title)))
    assert asked == ["https://blog.example/@writer.rss", "https://blog.example/@writer",
                     "https://blog.example/feeds/writer.xml"]
    assert results == [([("Hi", "https://blog.example/hi")], "Writer")]
