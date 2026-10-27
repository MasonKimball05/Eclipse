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
    assert not dark_pages.wanted(on, "pbext://abc/popup.html")  # the browser's own pages keep their colours
    dark_pages.set_skipped(on, "www.example.com", True)
    assert not dark_pages.wanted(on, "https://example.com/a") and not dark_pages.wanted(on, "https://docs.example.com/")
    assert dark_pages.wanted(on, "https://notexample.com/")
    dark_pages.set_skipped(on, "example.com", False)
    assert dark_pages.wanted(on, "https://example.com/")
