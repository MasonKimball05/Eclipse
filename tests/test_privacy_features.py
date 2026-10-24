import time

from browser import cookies
from browser.command_bar import _path_hint
from browser.page_index import PageIndex, fts_query, indexable


# --- Cookie banners -------------------------------------------------------------------------------

def test_cookie_script_never_accepts():
    # "Accept" only ever appears limited to what's necessary, or as "without accepting" (French: "sans accepter")
    assert all(any(w in label for w in ("only", "necessary", "essential", "without", "sans")) for label in cookies.REJECT_LABELS
               if "accept" in label)
    assert not any("accept" in s.lower() for s in cookies.KNOWN_BUTTONS)


def test_cookie_script_quotes_allowed_sites():
    source = cookies.script(["evil.com'); alert(1); ('"]).sourceCode()
    assert "alert(1); ('\"]" in source  # inside a JSON string, not code
    assert cookies.script([]).runsOnSubFrames()


# --- Copied links ----------------------------------------------------------------------------------

def test_path_hint():
    assert _path_hint("https://www.reddit.com/") == ""
    assert _path_hint("https://github.com/anthropics/claude-code") == "/anthropics/claude-code"
    assert _path_hint("https://example.com/" + "a" * 80).endswith("…")


# --- Pages you've read ------------------------------------------------------------------------------

def test_fts_query_is_safe():
    assert fts_query("sourdough hydration") == '"sourdough" "hydration"*'
    assert fts_query('NEAR(") OR *') == '"near" "or"*'  # operators become plain quoted words
    assert fts_query('"*()') is None
    assert fts_query("it's") == '"it\'s"*'
    assert fts_query("a") == '"a"'


def test_what_gets_indexed():
    assert indexable("https://news.example.com/story", [])
    assert not indexable("https://bank.example.com/account", ["example.com"])
    assert not indexable("file:///Users/me/notes.html", [])
    assert not indexable("pbext://abc/popup.html", [])


class _Store:
    def __init__(self):
        self.settings = {}

    def schedule_save(self):
        pass


class _Browser:
    def __init__(self):
        self.store = _Store()


def _until(condition, timeout=5.0):
    """The index writes on its own thread: wait for it to catch up."""
    end = time.time() + timeout
    while time.time() < end and not condition():
        time.sleep(0.05)
    return condition()


def _wait(index, n):
    assert _until(lambda: index.count() == n)


def test_index_add_search_and_forget(tmp_path):
    from PyQt6.QtCore import QObject
    browser, parent = _Browser(), QObject()
    index = PageIndex(browser, tmp_path / "pages.db", parent=parent)
    try:
        index._writes.put(("add", "https://bake.example/sourdough", "Bread notes",
                           "Hydration around seventy-five percent gives an open crumb. " * 5))
        index._writes.put(("add", "https://news.example/other", "Other", "Nothing about bread at all here. " * 5))
        _wait(index, 2)
        hits = index.search("open crumb")
        assert [h["url"] for h in hits] == ["https://bake.example/sourdough"]
        assert "crumb" in hits[0]["snippet"] and hits[0]["host"] == "bake.example"
        assert index.search("hydrat")[0]["title"] == "Bread notes"  # prefix of the last word
        index._writes.put(("add", "https://bake.example/sourdough", "Bread notes v2", "Rewritten: rye. " * 20))
        assert _until(lambda: index.search("rye") != [])
        assert index.count() == 2 and index.search("crumb") == []  # a revisit replaces the old text
        index.skip_site("bake.example")
        _wait(index, 1)  # its pages are dropped too
        assert index.search("rye") == [] and "bake.example" in browser.store.settings["page_index_skip"]
        browser.store.settings["page_index"] = False
        assert index.search("bread") == []  # off means off
        browser.store.settings["page_index"] = True
        index.clear()
        _wait(index, 0)
        assert index.count() == 0
    finally:
        index.shutdown()
