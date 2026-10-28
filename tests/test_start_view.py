"""The start view's most-visited sites: one tile per site, named sensibly."""

import time

from browser.start_view import _site_name, most_visited


def test_one_tile_per_site_ranked_by_visits_and_recency():
    now = time.time()
    history = [{"url": "https://github.com/a/b", "title": "b · GitHub", "ts": now, "visits": 5},
               {"url": "https://github.com/", "title": "GitHub", "ts": now, "visits": 30},
               {"url": "https://old.example/", "title": "Old", "ts": now - 200 * 86400, "visits": 100},
               {"url": "https://news.example/", "title": "News", "ts": now, "visits": 12},
               {"url": "file:///tmp/x.html", "title": "Local", "ts": now, "visits": 99}]
    sites = most_visited(history)
    assert [s["host"] for s in sites] == ["github.com", "news.example", "old.example"]  # (recent visits count more)
    assert sites[0]["url"] == "https://github.com/"  # (its home page, not the page visited)


def test_tile_names_come_from_the_site():
    assert _site_name("Inbox (3) - Gmail", "mail.google.com") == "Gmail"
    assert _site_name("Eclipse - Wikipedia", "en.wikipedia.org") == "Wikipedia"
    assert _site_name("3.14 Documentation", "docs.python.org") == "Python"
    assert _site_name("Hacker News", "news.ycombinator.com") == "Hacker News"
    assert _site_name("", "example.com") == "Example"
