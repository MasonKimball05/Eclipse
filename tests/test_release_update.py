import io
import plistlib
import subprocess

import pytest

from browser import macos, release_update


def _app(folder, build, bundle_id=macos.APP_BUNDLE_ID, name="Eclipse.app"):
    app = folder / name
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id,
                                                                  "CFBundleVersion": str(build)}))
    return app


def _release(tag="release-202611011200", assets=("Eclipse.zip",), body="- Better tabs\n- Faster\n"):
    return lambda _url: {"tag_name": tag, "name": "Eclipse 2026.11.1", "body": body,
                         "assets": [{"name": a, "browser_download_url": f"https://example.invalid/{a}",
                                     "size": 215_000_000} for a in assets]}


def test_newer_release_is_offered(tmp_path):
    found = release_update.inspect(_app(tmp_path, 202610271452), fetch=_release())
    assert found["state"] == "available" and found["build"] == 202611011200
    assert found["subjects"] == ["Better tabs", "Faster"] and found["url"].endswith("Eclipse.zip")


@pytest.mark.parametrize("fetch", [
    _release(tag="release-202610271452"),           # the same build
    _release(tag="release-202601010000"),           # older
    _release(tag="v2"),                             # not a release tag
    _release(assets=("Notes.txt",)),                # no app attached
])
def test_nothing_newer(tmp_path, fetch):
    assert release_update.inspect(_app(tmp_path, 202610271452), fetch=fetch)["state"] == "current"


def test_offline_and_translocated(tmp_path):
    def offline(_url):
        raise OSError("no network")
    assert release_update.inspect(_app(tmp_path, 1), fetch=offline)["state"] == "unavailable"
    moved = _app(tmp_path / "AppTranslocation" / "x", 1)
    assert "Applications" in release_update.inspect(moved, fetch=_release())["reason"]


def test_download_reports_progress(tmp_path):
    class Response(io.BytesIO):
        headers = {"Content-Length": str(3 << 20)}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False
    seen = []
    to = release_update.download("https://example.invalid/Eclipse.zip", tmp_path / "d" / "Eclipse.zip",
                                 seen.append, opener=lambda _request, timeout: Response(b"x" * (3 << 20)))
    assert to.stat().st_size == 3 << 20 and seen[-1] == 1.0 and len(seen) == 3


def test_only_a_newer_signed_eclipse_is_accepted(tmp_path):
    ok = lambda *_command: (0, "")  # noqa: E731
    assert release_update.problem(_app(tmp_path / "a", 5), newer_than=4, run=ok) is None
    assert "isn't newer" in release_update.problem(_app(tmp_path / "b", 4), newer_than=4, run=ok)
    assert "isn't Eclipse" in release_update.problem(_app(tmp_path / "c", 5, "com.example.other"), 4, run=ok)
    unsigned = lambda *command: (1, "code object is not signed at all") if "codesign" in command[0] else (0, "")  # noqa: E731
    assert "signature" in release_update.problem(_app(tmp_path / "d", 5), 4, run=unsigned)
    not_notarized = lambda *command: (3, "rejected") if "spctl" in command[0] else (0, "")  # noqa: E731
    assert "notarized" in release_update.problem(_app(tmp_path / "e", 5), 4, run=not_notarized)


def test_requirement_names_the_team(tmp_path):
    commands = []
    release_update.problem(_app(tmp_path, 5), 4, run=lambda *c: commands.append(c) or (0, ""))
    assert any("F4P3T9JYP2" in part for part in commands[0]) and "--strict" in commands[0]


@pytest.mark.mac
def test_an_ad_hoc_signed_app_is_refused(tmp_path):
    app = _app(tmp_path, 5)
    (app / "Contents" / "MacOS").mkdir()
    exe = app / "Contents" / "MacOS" / "Eclipse"
    exe.write_bytes((b"#!/bin/sh\n"))
    exe.chmod(0o755)
    subprocess.run(["codesign", "--sign", "-", str(app)], check=True, capture_output=True)
    assert "signature" in release_update.problem(app, 4)  # signed, but not by Eclipse's maker


@pytest.mark.mac
def _fake_open(tmp_path, starts: bool):
    """A stand-in for /usr/bin/open: notes what it opened, and (as a copy that starts does) writes the marker."""
    log, script = tmp_path / "opened.txt", tmp_path / "open.sh"
    marker = tmp_path / "work" / release_update.STARTED
    script.write_text("#!/bin/sh\necho \"$2\" >> " + str(log) + ("\ntouch " + str(marker) if starts else "") + "\n")
    script.chmod(0o755)
    return {"ECLIPSE_OPEN": str(script), "ECLIPSE_SWAP_WAIT": "5", "PATH": "/usr/bin:/bin"}, log


def test_swap_puts_the_new_copy_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(release_update, "work_dir", lambda: tmp_path / "work")
    (tmp_path / "work").mkdir()
    current, new = _app(tmp_path / "Applications", 1), _app(tmp_path / "work" / "new", 2)
    command = release_update.swap_command(current, new, pid=999_999)  # (no such process: it's already gone)
    env, opened = _fake_open(tmp_path, starts=True)
    subprocess.run(command, check=True, capture_output=True, env=env)
    assert release_update.build_of(current) == 2 and not new.exists()
    assert release_update.build_of(tmp_path / "work" / "old.app") == 1
    assert opened.read_text().split() == [str(current)]
    release_update.tidy()
    assert not (tmp_path / "work").exists() and current.exists()


def test_a_new_copy_that_never_starts_is_swapped_back(tmp_path, monkeypatch):
    """One that quit as it opened (as 26.1.3.0 did) left nothing to go back to: its first act was deleting the old
    copy. Not started (and not running) in time, the old copy's put back and opened."""
    monkeypatch.setattr(release_update, "work_dir", lambda: tmp_path / "work")
    (tmp_path / "work").mkdir()
    current, new = _app(tmp_path / "Applications", 1), _app(tmp_path / "work" / "new", 2)
    env, opened = _fake_open(tmp_path, starts=False)
    subprocess.run(release_update.swap_command(current, new, pid=999_999), check=True, capture_output=True, env=env)
    assert release_update.build_of(current) == 1  # (the old one, back)
    assert opened.read_text().split() == [str(current), str(current)]  # (the new tried, then the old)

def test_a_release_copy_asks_github_releases(tmp_path, monkeypatch):
    from browser import self_update
    app = _app(tmp_path, 1)
    code = app / "Contents" / "Resources" / "app"
    code.mkdir(parents=True)
    monkeypatch.setattr(self_update, "REPO", code)
    monkeypatch.setattr(release_update, "inspect", lambda bundle: {"state": "current", "asked": bundle})
    assert self_update.release_bundle() == app and self_update.inspect()["asked"] == app


def test_the_update_window_says_what_you_have_briefly():
    from browser.update_window import notes_markdown, you_have
    assert you_have("Eclipse 2026.10.27 (build 202610271716)") == "You have 2026.10.27."
    assert you_have("From source, commit abc1234 (Oct 27)") == "You're running it from source."
    assert notes_markdown({"notes": "- A\n- B"}) == "- A\n- B"
    assert notes_markdown({"subjects": ["Fix", "Feature"], "behind": 5}) == "- Fix\n- Feature\n- …and 3 more"


def test_the_update_window_wording_is_shared_by_its_qt_and_native_pages():
    from browser.update_window import about_text, notes_markdown, status_view
    found = {"build": 1, "version": "Eclipse 26.1.3.0", "size": 168_000_000}
    assert about_text(found, "From source, commit abc (Oct 27)") == (
        "Eclipse 26.1.3.0 · 168 MB. You're running it from source. Your tabs come back after the restart.")
    assert about_text({"behind": 1}, "From source, commit abc (Oct 27)").startswith("1 new change on GitHub.")
    # (a release's headings as bold lines in Qt's page: full-size ones would outsize "What's new")
    assert notes_markdown({"notes": "### This week\n- A"}) == "**This week**\n- A"
    assert status_view({"state": "downloading", "percent": 40}) == {
        "progress": 40, "busy": True, "status": "Downloading… 40%. Eclipse restarts when it's checked and ready."}
    failed = status_view({"state": "failed", "reason": "No room."})
    assert not failed["busy"] and failed["go_label"] == "Try Again" and failed["progress"] is None
    assert status_view({"state": "checking"}) == {}
