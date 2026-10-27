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


def test_an_ad_hoc_signed_app_is_refused(tmp_path):
    app = _app(tmp_path, 5)
    (app / "Contents" / "MacOS").mkdir()
    exe = app / "Contents" / "MacOS" / "Eclipse"
    exe.write_bytes((b"#!/bin/sh\n"))
    exe.chmod(0o755)
    subprocess.run(["codesign", "--sign", "-", str(app)], check=True, capture_output=True)
    assert "signature" in release_update.problem(app, 4)  # signed, but not by Eclipse's maker


def test_swap_puts_the_new_copy_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(release_update, "work_dir", lambda: tmp_path / "work")
    (tmp_path / "work").mkdir()
    current, new = _app(tmp_path / "Applications", 1), _app(tmp_path / "work" / "new", 2)
    command = release_update.swap_command(current, new, pid=999_999)  # (no such process: it's already gone)
    command[2] = command[2].replace("/usr/bin/open -n", "echo")
    subprocess.run(command, check=True, capture_output=True)
    assert release_update.build_of(current) == 2 and not new.exists()
    assert release_update.build_of(tmp_path / "work" / "old.app") == 1
    release_update.tidy()
    assert not (tmp_path / "work").exists() and current.exists()


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
