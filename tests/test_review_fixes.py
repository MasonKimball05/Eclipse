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


def test_a_translation_is_dropped_when_its_page_closes(qapp):
    from browser.translate import Translator, _Job

    class Request:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    class Browser:
        pass
    translator = Translator.__new__(Translator)
    translator.browser = Browser()
    translator._jobs, translator._translated, translator._watched = {}, set(), set()
    job = _Job(object(), [], "English")
    job.requests = [Request(), Request()]
    translator._jobs[7] = job
    translator._translated.add(7)
    translator._watched.add(7)
    translator._drop(7)  # (its view's destroyed signal)
    assert job.cancelled and all(r.cancelled for r in job.requests)
    assert 7 not in translator._jobs and 7 not in translator._translated and 7 not in translator._watched


def test_away_queue_copes_with_the_tab_closing_while_homebase_answers(qapp):
    from browser.integrations_ui import AppsUI

    class Homebase:
        def projects(self, done):
            done(["Eclipse"], None)  # (answers: by now the tab has closed)

    class Browser:
        homebase = Homebase()
        toasts = []

        def toast(self, text, *args):
            self.toasts.append(text)

    class Tab:
        url = "https://github.com/MasonKimball05/Eclipse/issues/12"
        view = None  # closed
    apps = AppsUI.__new__(AppsUI)
    apps.browser = Browser()
    apps.issue_to_away(Tab())
    assert apps.browser.toasts == ["The issue's tab was closed"]


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
