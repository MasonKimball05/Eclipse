from browser.browse import markdown_to_html, pick_sources, render_page

SOURCES = [("https://a.com/x", "Alpha"), ("https://b.org/y", "Beta")]


def test_pick_sources_unwraps_duckduckgo_and_keeps_one_per_site():
    results = [
        ["https://duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fx&rut=1", "Alpha"],
        ["https://duckduckgo.com/y.js?ad_provider=bing", "An ad"],
        ["https://duckduckgo.com/l/?uddg=https%3A%2F%2Fa.com%2Fother", "Alpha again"],
        ["https://b.org/y", "Beta"],
    ]
    assert pick_sources(results) == SOURCES


def test_markdown_basics_and_citations():
    out = markdown_to_html("## Short answer\nUse **Zen** [1][2].\n\n- one\n- two *fast*\n\n1. first", SOURCES)
    assert "<h3>Short answer</h3>" in out
    assert "<strong>Zen</strong>" in out
    assert out.count('class="cite"') == 2 and 'href="https://b.org/y"' in out
    assert "<ul>\n<li>one</li>\n<li>two <em>fast</em></li>\n</ul>" in out
    assert "<ol>\n<li>first</li>\n</ol>" in out


def test_citation_numbers_out_of_range_stay_text():
    assert "[9]" in markdown_to_html("claim [9]", SOURCES)


def test_untrusted_text_cannot_inject_html():
    out = markdown_to_html('<script>alert(1)</script> [x](https://e.com/"onmouseover="alert(1))', SOURCES)
    assert "<script>" not in out
    assert '"onmouseover' not in out


def test_rendered_page_escapes_query_and_titles():
    page = render_page("<b>q</b>", [("https://a.com", "<img src=x>")], "", "Searching…", "#123456")
    assert "<b>q</b>" not in page and "<img src=x>" not in page



def test_a_cancelled_or_failed_search_starts_nothing_after(qapp):
    """A search reply landing after Cancel started reading pages for a run no one was watching; and one that never
    loaded (offline) was said as "no results"."""
    from PyQt6.QtCore import QObject
    from browser import browse
    run = browse.BrowseForMe.__new__(browse.BrowseForMe)
    QObject.__init__(run)
    run.sources, run._texts, run._candidates, run._next, run._pages, run._readers = [], {}, [], 0, [], []
    run._request, run._stopped, run._searched = None, False, False
    told = []
    run.failed.connect(told.append)
    run.cancel()
    run._got_results([{"url": "https://example.com/a", "title": "A"}])
    assert run._readers == [] and not run._searched
    fresh = browse.BrowseForMe.__new__(browse.BrowseForMe)
    QObject.__init__(fresh)
    fresh.sources, fresh._texts, fresh._candidates, fresh._next, fresh._pages, fresh._readers = [], {}, [], 0, [], []
    fresh._request, fresh._stopped, fresh._searched = None, False, False
    fresh.failed.connect(told.append)
    fresh._search_loaded(None, False)
    assert told == ["Couldn't reach the search: check your connection."] and fresh._stopped
