import subprocess
from pathlib import Path

from PyQt6.QtCore import QUrl

from browser import keychain
from browser.devmode import DevMode, project_files


def test_keychain_secret_never_on_the_command_line(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs.get("input")))
        if args[:2] == ["security", "find-generic-password"]:
            return subprocess.CompletedProcess(args, 0, 'tok "q" \\ x\n', "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(keychain.subprocess, "run", fake_run)
    monkeypatch.setattr(keychain, "available", lambda: True)
    assert keychain.store("acct", 'tok "q" \\ x')
    args, stdin = calls[0]
    assert args == ["security", "-i"] and 'tok \\"q\\" \\\\ x' in stdin
    assert all('tok "q"' not in " ".join(a) for a, _ in calls)  # nowhere in any argv
    assert not keychain.store("acct", "two\nlines")


def test_keychain_timeout_doesnt_raise(monkeypatch):
    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 30)
    monkeypatch.setattr(keychain.subprocess, "run", slow)
    monkeypatch.setattr(keychain, "available", lambda: True)
    assert keychain.store("acct", "secret") is False  # and nothing for the error log to print


class _Store:
    settings = {}


class _Browser:
    store = _Store()


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
