import pytest
import subprocess
from pathlib import Path

from PyQt6.QtCore import QUrl

from browser import keychain
from browser.devmode import DevMode, project_files


@pytest.mark.mac
def test_keychain_tokens_go_only_where_eclipse_can_read_them(monkeypatch, no_real_keychain):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 44, "", "")  # (no old item)
    monkeypatch.setattr(keychain.subprocess, "run", fake_run)
    monkeypatch.setattr(keychain, "available", lambda: True)
    assert keychain.store("acct", 'tok "q" \\ x') and keychain.load("acct") == 'tok "q" \\ x'
    assert no_real_keychain["acct"] == 'tok "q" \\ x'.encode()  # (Eclipse's own item)
    assert all('tok "q"' not in " ".join(a) for a in calls)  # (never on a command line)
    assert not any(a[:2] == ["security", "add-generic-password"] or a[:2] == ["security", "-i"] for a in calls)
    assert not keychain.store("acct", "two\nlines")
    keychain.delete("acct")
    assert "acct" not in no_real_keychain


@pytest.mark.mac
def test_an_old_token_moves_over_when_its_read(monkeypatch, no_real_keychain):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["security", "find-generic-password"]:
            return subprocess.CompletedProcess(args, 0, "old-token\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(keychain.subprocess, "run", fake_run)
    monkeypatch.setattr(keychain, "available", lambda: True)
    assert keychain.load("gh") == "old-token"
    assert no_real_keychain["gh"] == b"old-token"
    assert ["security", "delete-generic-password", "-s", "PythonBrowser", "-a", "gh"] in calls
    calls.clear()
    assert keychain.load("gh") == "old-token" and calls == []  # (from Eclipse's own item now)


@pytest.mark.mac
def test_keychain_timeout_doesnt_raise(monkeypatch, no_real_keychain):
    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)
    monkeypatch.setattr(keychain.subprocess, "run", slow)
    monkeypatch.setattr(keychain, "available", lambda: True)
    assert keychain.load("missing") is None  # (and nothing for the error log to print)
    assert keychain.store("acct", "secret")


def test_dev_mode_only_watches_pages_you_write(tmp_path):
    watching = DevMode.watching
    mode = type("M", (), {"settings": {}})()
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x")
    assert watching(mode, QUrl.fromLocalFile(str(site / "index.html")).toString()) == str(site)
    assert watching(mode, QUrl.fromLocalFile(str(site / "report.pdf")).toString()) is None  # not a web page
    downloads = Path.home() / "Downloads" / "page.html"
    assert watching(mode, QUrl.fromLocalFile(str(downloads)).toString()) is None  # never all of Downloads


def test_project_files_depth(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    for p in ("top.html", "a/one.css", "a/b/two.js", "a/b/c/three.js"):
        (tmp_path / p).write_text("x")
    names = {Path(p).name for p in project_files(str(tmp_path), depth=2)}
    assert names == {"top.html", "one.css"}
    assert len(project_files(str(tmp_path))) == 4


def test_dark_websites_rules():
    from browser import dark_pages
    on = {dark_pages.SETTING: True}
    assert not dark_pages.wanted({}, "https://example.com/")  # off unless turned on
    assert dark_pages.wanted(on, "https://example.com/")
    assert not dark_pages.wanted(on, "pbext://abc/popup.html")  # the browser's own pages keep their colors
    dark_pages.set_skipped(on, "www.example.com", True)
    assert not dark_pages.wanted(on, "https://example.com/a") and not dark_pages.wanted(on, "https://docs.example.com/")
    assert dark_pages.wanted(on, "https://notexample.com/")
    dark_pages.set_skipped(on, "example.com", False)
    assert dark_pages.wanted(on, "https://example.com/")


def test_handoff_offers_only_pages_your_other_devices_can_open():
    """localhost:5173/admin?token=…, a router's page or a .test name went to iCloud for your iPhone, which can't
    reach any of them; and an address with a name and password in it too."""
    from browser.handoff import offerable
    assert offerable("https://github.com/x") and offerable("http://example.com/")
    for url in ("http://localhost:5173/admin?token=abc", "http://127.0.0.1:8000/", "http://192.168.1.1/",
                "http://myapp.test/", "http://printer.local/", "https://user:pass@example.com/", "file:///Users/me/a",
                "note:abc", "http://intranet/"):
        assert not offerable(url), url


def test_hop_quicklinks_skip_odd_rows_and_homebase_errors_from_a_list(tmp_path):
    """A quicklinks file with a string or null among its rows lost every shortcut (an AttributeError nothing
    caught); a homebase answer that's a list crashed reading its error."""
    import json
    from browser import integrations
    path = tmp_path / "quicklinks.json"
    path.write_text(json.dumps([None, "oops", {"keyword": "gh", "url": "https://gh.example/search?q={query}",
                                               "name": "GH"}]))
    assert [q["keyword"] for q in integrations.hop_quicklinks(str(path), builtin={})] == ["gh"]
    assert integrations._said_error([1, 2]) is None and integrations._said_error({"error": "x"}) == "x"


def test_a_crash_is_noticed_after_the_mac_restarts(data_dir, monkeypatch):
    """After a crash and a restart, the last run's process number belonged to a system process by then (numbers
    start again from low): it counted as still running, so nothing said Eclipse hadn't quit properly, and the crash's
    stack was written over."""
    import os
    import time
    from browser import crash
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / crash.MARKER
    monkeypatch.setattr(crash, "_booted_at", lambda: time.time() - 60)
    marker.write_text(f"{os.getpid()} 2026-11-10 09:00 {time.time() - 3600:.0f}")  # (before the restart: "alive")
    found = crash.start()
    assert found is not None and found["when"] == "2026-11-10 09:00"
    crash.clean_exit()
    marker.write_text("1 2026-11-10 09:00")  # (an older marker; process 1 isn't ours)
    assert crash.start() is not None
    crash.clean_exit()
    assert not crash._alive(1)


def test_ad_blocking_tells_sites_on_shared_hosts_apart_and_ignores_a_trailing_dot():
    """Allowing ads on alice.github.io allowed them on every github.io site (the "site" was github.io), and its
    trackers counted as its own; a tracker's address with a trailing dot (google-analytics.com.) got past the
    lists."""
    from browser.adblock import _site, should_block
    assert _site("alice.github.io") == "alice.github.io" != _site("bob.github.io")
    assert _site("shop.myshopify.com") == "shop.myshopify.com" and _site("news.bbc.co.uk") == "bbc.co.uk"
    assert _site("cdn.example.com") == "example.com"
    lists = {"google-analytics.com", "tracker.github.io"}
    assert should_block("www.google-analytics.com.", "news.example", lists)
    assert should_block("tracker.github.io", "alice.github.io", lists)  # (not alice's own, though both github.io)
    assert not should_block("tracker.github.io", "bob.github.io", lists, allowed_sites={"bob.github.io"})
    assert should_block("tracker.github.io", "carol.github.io", lists, allowed_sites={"bob.github.io"})


def test_a_window_or_popup_is_kept_on_its_screen():
    """Cascaded Little and Private windows went under the Dock (or off the screen), and popups past its edge."""
    from PyQt6.QtCore import QRect
    from browser import screens
    area = QRect(0, 25, 1440, 850)  # (a MacBook's, below the menu bar, above the Dock)
    assert screens.fit(QRect(400, 200, 680, 820), area) == QRect(400, 55, 680, 820)  # (moved up, all of it in)
    assert screens.fit(QRect(-50, 0, 300, 2000), area) == QRect(0, 25, 300, 850)  # (in from the left, no taller)
    assert screens.fit(QRect(1300, 800, 300, 200), area) == QRect(1140, 675, 300, 200)
    assert screens.fit(QRect(10, 30, 50, 50), None) == QRect(10, 30, 50, 50)


def test_download_rows_are_numbered_once_and_never_again():
    """Rows were numbered by id(item): a new download given a freed one's address took over its row."""
    from types import SimpleNamespace
    from browser.downloads_native import _row_id
    a, b = SimpleNamespace(), SimpleNamespace()
    first = _row_id(a)
    assert _row_id(a) == first and _row_id(b) != first
    del a
    assert _row_id(SimpleNamespace()) not in (first, _row_id(b))


def test_the_librarys_row_numbers_forget_what_isnt_listed():
    """Every row ever listed (each search's, every 2 s) was kept for the whole session; numbers are never reused."""
    from types import SimpleNamespace
    from browser import library_native
    entries = [{"url": f"https://e.com/{i}", "title": str(i), "ts": 0} for i in range(3)]
    shown = {"rows": entries}
    qt = SimpleNamespace(section="history", matching=lambda q: shown["rows"], _icon=None, empty_text=lambda q: "")
    lib = SimpleNamespace(qt=qt, query="", _icons={}, _icon_key=lambda url: "k")
    lib._icons["k"] = "png"
    describe = library_native.NativeLibrary.describe
    first = [e["id"] for e in describe(lib)["entries"]]
    assert [e["id"] for e in describe(lib)["entries"]] == first  # (the same rows keep their numbers)
    shown["rows"] = entries[1:] + [{"url": "https://e.com/new", "title": "new", "ts": 0}]
    second = [e["id"] for e in describe(lib)["entries"]]
    assert second[:2] == first[1:] and second[2] not in first
    assert len(lib._ids) == 3  # (the one no longer listed, forgotten)


def test_a_new_live_folders_name_is_as_long_as_edit_keeps_it():
    from browser.live_folder_dialog import LiveFoldersFeature
    made = []
    me = type("F", (), {"create_live_folder": lambda self, live, name, token: made.append(name)})()
    LiveFoldersFeature._made(me, ({"url": "https://e.com/feed"}, "x" * 60, None))
    assert made == ["x" * 40]
