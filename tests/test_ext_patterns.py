import re

import pytest

from browser.ext.patterns import glob_pattern, match_pattern, matches
from browser.ext.userscripts import from_source, parse_metadata

SCRIPT = """// ==UserScript==
// @name         Hide Shorts
// @namespace    https://example.dev
// @version      1.2
// @description  Removes YouTube Shorts
// @match        https://www.youtube.com/*
// @include      http*://m.youtube.com/*
// @exclude      https://www.youtube.com/embed/*
// @run-at       document-start
// @grant        GM_addStyle
// @grant        GM_getValue
// @grant        GM_cookie
// @require      https://cdn.example.com/lib.js
// @noframes
// ==/UserScript==
GM_addStyle('ytd-reel-shelf-renderer { display: none }');
"""


@pytest.mark.parametrize("pattern, url, hit", [
    ("<all_urls>", "https://a.com/x", True),
    ("*://*.example.com/*", "https://docs.example.com/a", True),
    ("*://*.example.com/*", "http://example.com/", True),
    ("*://*.example.com/*", "https://notexample.com/", False),
    ("https://example.com/path*", "https://example.com/path/to", True),
    ("https://example.com/path*", "http://example.com/path", False),
    ("https://example.com/*", "https://example.com:8443/x", True),
])
def test_match_patterns(pattern, url, hit):
    assert bool(re.search(match_pattern(pattern), url)) is hit


def test_invalid_match_pattern():
    assert match_pattern("example.com") is None


def test_globs():
    assert re.search(glob_pattern("http*://m.youtube.com/*"), "https://m.youtube.com/watch")
    assert re.search(glob_pattern("https://google.tld/*"), "https://google.co.uk/search")
    assert glob_pattern("/^https?:\\/\\/x\\.com/") == "^https?:\\/\\/x\\.com"


def test_parse_userscript():
    meta = parse_metadata(SCRIPT)
    assert meta["grant"] == ["GM_addStyle", "GM_getValue", "GM_cookie"]
    s = from_source(SCRIPT, "https://greasyfork.org/x.user.js")
    assert (s.name, s.version, s.run_at, s.noframes) == ("Hide Shorts", "1.2", "document-start", True)
    assert s.requires == ["https://cdn.example.com/lib.js"]
    assert s.uses_gm and s.unsupported_grants == ["GM_cookie"]
    assert matches("https://www.youtube.com/watch?v=1", s.include_regexes, s.exclude_regexes)
    assert matches("https://m.youtube.com/feed", s.include_regexes, s.exclude_regexes)
    assert not matches("https://www.youtube.com/embed/abc", s.include_regexes, s.exclude_regexes)
    assert not matches("https://vimeo.com/", s.include_regexes, s.exclude_regexes)


def test_script_without_match_runs_everywhere_and_grant_none_is_page_world():
    s = from_source("// ==UserScript==\n// @name A\n// ==/UserScript==\nalert(1)")
    assert not s.uses_gm and matches("https://anything.org/", s.include_regexes)
