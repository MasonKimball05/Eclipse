"""Small pure functions: URL detection, ad-block matching, yt-dlp arguments, questions, themes."""

import pytest

from browser.adblock import _site, parse_list, should_block
from browser.ai import looks_like_question
from browser.command_bar import search_url, to_url
from browser.downloads import DEFAULT_SETTINGS, InfoFetch, _error_message, build_arguments
from browser.theme import make_theme


@pytest.mark.parametrize("text, url", [
    ("github.com", "https://github.com"),
    ("github.com/MasonKimball05", "https://github.com/MasonKimball05"),
    ("localhost:3000", "http://localhost:3000"),
    ("http://example.com", "http://example.com"),
    ("about:blank", "about:blank"),
    ("how do tabs work", None),
    ("python", None),
    ("3.14", None),
    ("", None),
])
def test_to_url(text, url):
    assert to_url(text) == url


def test_search_url_encodes():
    assert search_url("c++ & rust") == "https://www.google.com/search?q=c%2B%2B%20%26%20rust"


@pytest.mark.parametrize("host, site", [
    ("news.bbc.co.uk", "bbc.co.uk"), ("www.example.com", "example.com"), ("a.b.cdn.example.com", "example.com"),
])
def test_site(host, site):
    assert _site(host) == site


def test_parse_list_handles_hosts_files_and_plain_lists():
    text = """# comment
0.0.0.0 ads.example.com
127.0.0.1 tracker.net  # trailing comment
localhost
plain-domain.org
0.0.0.0 0.0.0.0
"""
    assert parse_list(text) == {"ads.example.com", "tracker.net", "plain-domain.org"}


def test_should_block():
    domains = {"doubleclick.net", "ads.example.com"}
    assert should_block("securepubads.g.doubleclick.net", "news.com", domains)  # subdomain of a listed domain
    assert not should_block("cdn.news.com", "www.news.com", domains)             # first party is never blocked
    assert not should_block("doubleclick.net", "doubleclick.net", domains)       # ...even if it's on the list
    assert not should_block("example.com", "news.com", domains)                  # parent of a listed host
    assert not should_block("doubleclick.net", "news.com", domains, ["news.com"])  # site allowed by the user


def test_questions():
    assert looks_like_question("who owns this?")
    assert looks_like_question("what is the plot")
    assert looks_like_question("summarize")
    assert not looks_like_question("python docs")
    assert not looks_like_question("?")


def _args(**overrides):
    return build_arguments({**DEFAULT_SETTINGS, "folder": "/tmp/dl", **overrides}, "https://youtu.be/x", playlist=False)


def test_ytdlp_video_mp4_caps_height_and_prefers_h264():
    args = _args(max_height=720)
    fmt = args[args.index("-f") + 1]
    assert fmt.startswith("bv*[height<=?720][vcodec^=avc]+ba[ext=m4a]")
    assert args[args.index("--merge-output-format") + 1] == "mp4/mkv"
    assert "--no-playlist" in args and args[-2:] == ["--", "https://youtu.be/x"]


def test_ytdlp_audio_formats():
    mp3 = _args(kind="audio", audio_format="mp3")
    assert mp3[mp3.index("--audio-format") + 1] == "mp3" and "--embed-thumbnail" in mp3
    wav = _args(kind="audio", audio_format="wav")
    assert "--embed-thumbnail" not in wav  # WAV can't hold cover art


def test_ytdlp_subtitles_cookies_rate_and_playlists():
    args = _args(subtitle_langs="en, es", embed_subs=True, cookies_from="safari", rate_limit="5M")
    assert args[args.index("--sub-langs") + 1] == "en,es" and "--embed-subs" in args
    assert args[args.index("--cookies-from-browser") + 1] == "safari"
    assert args[args.index("-r") + 1] == "5M"
    playlist = build_arguments({**DEFAULT_SETTINGS, "folder": "/tmp"}, "https://x", playlist=True)
    assert "--yes-playlist" in playlist


def test_ytdlp_error_message():
    stderr = "WARNING: x\nERROR: [youtube] abc123: Video unavailable\n"
    assert _error_message(stderr) == "Video unavailable"
    assert _error_message("all fine") is None


def test_ytdlp_info_summary():
    video = InfoFetch._summarize({"title": "T", "uploader": "U", "duration": 61, "formats": [
        {"height": 1080, "vcodec": "avc1"}, {"height": 720, "vcodec": "vp9"}, {"vcodec": "none", "acodec": "opus"}],
        "subtitles": {"en": [], "live_chat": []}, "automatic_captions": {"de-orig": [], "fr": []}})
    assert video["heights"] == [1080, 720] and video["subtitles"] == ["en", "de-orig"] and not video["playlist"]
    playlist = InfoFetch._summarize({"_type": "playlist", "title": "P", "entries": [
        {"url": "a"}, {"url": "b"}, {"_type": "playlist", "url": "nested"}]})
    assert playlist["playlist"] and playlist["count"] == 2


def test_themes():
    single = make_theme("#3b82f6", dark=False, intensity=0.5)
    assert len(single.stops) == 2 and not single.dark
    triple = make_theme("#ff0000", colors=["#ff0000", "#00ff00", "#0000ff"], mode="dark")
    assert len(triple.stops) == 3 and triple.dark
    # More intensity means more colour (less white) in light mode
    pale = make_theme("#ff0000", dark=False, intensity=0.1).bottom
    vivid = make_theme("#ff0000", dark=False, intensity=0.9).bottom
    assert vivid.green() < pale.green()


def test_site_search_shortcuts():
    from browser.command_bar import parse_site_search, site_for_tab, site_url
    assert parse_site_search("yt lofi beats") == ("youtube", "lofi beats")
    assert parse_site_search("GH pyqt6") == ("github", "pyqt6")
    assert parse_site_search("x factor") is None       # one-letter keywords need Tab
    assert parse_site_search("so what happened") is None  # everyday words need Tab too
    assert parse_site_search("yt") is None
    assert site_for_tab("you") == "youtube" and site_for_tab("w") == "wikipedia"
    assert site_for_tab("zzz") is None
    assert site_url("youtube", "a&b c") == "https://www.youtube.com/results?search_query=a%26b%20c"


def test_inline_completion():
    from browser.command_bar import inline_completion, matches_query, url_key
    c = [("https://github.com/pyqt/pyqt6", "PyQt6", 50), ("https://github.com/", "GitHub", 30),
         ("https://gitlab.com/", "GitLab", 10), ("https://www.google.com/", "Google", 500)]
    assert inline_completion("git", c) == ("github.com", "https://github.com/", "GitHub")  # the most-used site
    assert inline_completion("Git", c)[0] == "Github.com"  # your typing keeps its case
    assert inline_completion("goo", c)[:2] == ("google.com", "https://www.google.com/")  # "www." ignored, kept
    assert inline_completion("github.com/py", c)[:2] == ("github.com/pyqt/pyqt6", "https://github.com/pyqt/pyqt6")
    assert inline_completion("git hub", c) is None and inline_completion("zzz", c) is None
    assert inline_completion("github.com", c) is None  # already complete
    assert url_key("https://www.x.com/a") == "x.com/a"
    assert matches_query("python docs", "Qt for Python docs", "https://doc.qt.io/qtforpython/")
    assert not matches_query("pyqt rust", "Qt for Python docs", "https://doc.qt.io/")


def test_frecency_and_visits(store):
    import time
    from browser.models import frecency
    now = time.time()
    assert frecency({"ts": now, "visits": 3}, now) > frecency({"ts": now - 100 * 86400, "visits": 20}, now)
    assert frecency({"ts": now}, now) == 100  # older entries without a count count once
    store.record_history("https://a.example/", "A")
    store.record_history("https://a.example/", "A")
    assert store.history[0]["visits"] == 2
    store.merge_history([{"url": "https://a.example/", "title": "A", "ts": 1, "visits": 9}])
    assert store.history[0]["visits"] == 9


def test_page_action_helpers():
    from browser.page_actions import markdown_link, safe_filename
    assert safe_filename("Q3: Budget / Forecast?") == "Q3 Budget Forecast"
    assert safe_filename("   ") == "Page" and len(safe_filename("x" * 300)) == 120
    assert markdown_link("Arrays [MDN]", "https://x.dev/a_(b)") == "[Arrays \\[MDN\\]](https://x.dev/a_%28b%29)"


def test_recent_tabs_order():
    from browser.models import Tab
    from browser.tab_switcher import recent_tabs
    a, b, c, d = (Tab(f"https://{n}.example/") for n in "abcd")
    a.last_active, b.last_active, c.last_active, d.last_active = 1, 4, 3, 2
    assert recent_tabs([a, b, c, d], [c.id, a.id]) == [c, a, b, d]  # used recently first, then by last use
    assert recent_tabs([a, b], [c.id]) == [b, a]  # tabs from other Spaces are left out
    assert len(recent_tabs([a, b, c, d], [], limit=2)) == 2
