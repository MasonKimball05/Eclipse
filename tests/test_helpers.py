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
