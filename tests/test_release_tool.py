"""tools/release.py's checks (nothing is built, signed or published here)."""

import importlib.util
import sys
from pathlib import Path

TOOLS = Path(__file__).parent.parent / "tools"


def _release():
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("release", TOOLS / "release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOOLS))


def _code(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


def test_a_built_app_matches_only_the_code_it_was_built_from(tmp_path):
    release = _release()
    built, source = tmp_path / "app", tmp_path / "checkout"
    files = {"main.py": "run()", "browser/a.py": "a", "browser/ext/b.py": "b"}
    _code(built, files)
    _code(source, files)
    (built / "browser" / "a.cpython-314.pyc").write_bytes(b"compiled")  # (only code counts)
    assert release.differences(built, source) == []
    (source / "browser" / "a.py").write_text("changed")
    (source / "browser" / "new.py").write_text("added")
    (built / "browser" / "ext" / "b.py").unlink()
    assert release.differences(built, source) == ["browser/a.py", "browser/ext/b.py", "browser/new.py"]


def test_the_release_installs_as_eclipse_never_over_eclipse_dev():
    release = _release()
    import build_app
    assert release.APP == Path("/Applications/Eclipse.app")
    assert build_app.APP == Path("/Applications/Eclipse Dev.app") and release.APP != build_app.APP
    plist = release.build_app.info_plist()
    assert plist["CFBundleIdentifier"] == "com.masonkimball.eclipse" and plist["CFBundleName"] == "Eclipse"


def test_a_failing_command_says_why():
    import pytest
    release = _release()
    with pytest.raises(SystemExit) as stopped:
        release.run(sys.executable, "-c", "import sys; sys.stderr.write('HTTP 502: upload cut off'); sys.exit(1)")
    assert "upload cut off" in str(stopped.value)


def test_publishing_finishes_a_draft_an_earlier_try_left(monkeypatch, tmp_path):
    release = _release()
    calls = []
    monkeypatch.setattr(release, "run", lambda *args, **_: calls.append(args[:3]) or "")
    monkeypatch.setattr(release, "git", lambda *args: "abc123" if args[0] == "rev-parse" else "")
    monkeypatch.setattr(release, "draft_of", lambda tag: True)
    release.publish(tmp_path / "Eclipse.zip", "202610281556", "2026.10.28", tmp_path / "missing.dmg")
    assert calls == [("gh", "release", "upload"), ("gh", "release", "edit")]
    calls.clear()
    monkeypatch.setattr(release, "draft_of", lambda tag: False)
    release.publish(tmp_path / "Eclipse.zip", "202610281556", "2026.10.28")
    assert calls == [("gh", "release", "create")]


def test_versions_go_year_major_week_and_release_within_the_week():
    next_version = _release().next_version
    assert next_version("week", [], 2026) == "26.1.0.0"  # (the first numbered release)
    published = [(26, 1, 0, 0), (26, 1, 3, 0), (26, 1, 2, 4)]  # (the newest by number, not by order)
    assert next_version("week", published, 2026) == "26.1.4.0"
    assert next_version("minor", published, 2026) == "26.1.3.1"
    assert next_version("major", published, 2026) == "26.2.0.0"
    assert next_version("week", published, 2027) == "27.1.0.0"  # (a new year starts again)


def test_published_versions_come_from_the_release_titles(monkeypatch):
    release = _release()
    titles = "Eclipse 26.1.3.0\nEclipse 2026.10.29\nEclipse 26.1.2.0\n"
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *a, **k: type("Done", (), {"returncode": 0, "stdout": titles})())
    assert release.published_versions() == [(26, 1, 3, 0), (26, 1, 2, 0)]  # (the old dated ones aren't numbered)


def test_numbering_stops_when_github_cant_be_read(monkeypatch):
    import pytest
    release = _release()
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *a, **k: type("Done", (), {"returncode": 1, "stdout": "", "stderr": "not logged in"})())
    with pytest.raises(SystemExit, match="not logged in"):  # (not a guess that numbers it as the year's first)
        release.published_versions()


def test_a_latest_release_tagged_by_hand_is_pointed_out(monkeypatch):
    release = _release()
    tag = {"now": "26.1.1.0"}
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *a, **k: type("Done", (), {"returncode": 0, "stdout": tag["now"] + "\n"})())
    assert "26.1.1.0" in release.latest_tag_warning() and "release-<build>" in release.latest_tag_warning()
    tag["now"] = "release-202611051200"
    assert release.latest_tag_warning() == ""


SOURCE = '''EDITION = {edition}

NOTES = """
## This week

- **Smoother swipes**: a swipe goes on to the next Space
  without needing to come halfway.
- [mine] **Homebase**: only on the maker's copy.

## Lately

- **Older**: last week's.
"""
'''


def test_release_notes_are_this_weeks_whats_new_then_the_commits(monkeypatch):
    release = _release()
    assert release.this_week(SOURCE.format(edition=24)) == (
        24, ["- **Smoother swipes**: a swipe goes on to the next Space without needing to come halfway."])
    answers = {"describe": "release-1", "log": "- A commit\n- Another", "show": SOURCE.format(edition=23)}
    monkeypatch.setattr(release, "git", lambda *args: answers.get(args[0], ""))
    monkeypatch.setattr(release, "WHATS_NEW", type("File", (), {"read_text": lambda self: SOURCE.format(edition=24)})())
    notes = release.release_notes()
    assert notes.splitlines()[0].startswith("- **Smoother swipes**") and "Homebase" not in notes and "Older" not in notes
    assert notes.endswith("### Changes\n- A commit\n- Another")
    # The edition hasn't gone up since the last release: "This week" is last week's, so just the commits
    answers["show"] = SOURCE.format(edition=24)
    assert release.release_notes() == "- A commit\n- Another"
    # Commits about the maker's own pieces stay out
    answers["log"] = "- A commit\n- Hop integration: Send To through hop:// links\n- Homebase card\n- Hopping fix"
    assert release.release_notes() == "- A commit\n- Hopping fix"


def test_every_pyqt_module_eclipse_imports_is_kept_in_the_app(tmp_path):
    """26.1.3.0 and 26.1.3.1 wouldn't open: the window imported PyQt6.QtQuickWidgets (inside a function, so the
    build's check, which only imported every module, never ran it), and the app had been trimmed of it."""
    release = _release()
    used = release.qt_modules_used()
    assert "QtQuickWidgets" in used and "QtCore" in used
    assert used <= set(release.QT_MODULES), sorted(used - set(release.QT_MODULES))
    # and every one those bring in when loaded (QtQuickWidgets' binding imports QtQml and QtQuick: kept without
    # them, it still wouldn't load)
    import subprocess
    loaded = subprocess.run([sys.executable, "-c", "import sys\n" + "".join(f"import PyQt6.{m}\n" for m in sorted(used))
                             + "print(','.join(n.split('.')[1] for n in sys.modules if n.startswith('PyQt6.Qt')))"],
                            capture_output=True, text=True, check=True).stdout.strip().split(",")
    assert set(loaded) <= set(release.QT_MODULES), sorted(set(loaded) - set(release.QT_MODULES))
    # Found wherever they're imported: at the top, in a function, either way of writing it
    _code(tmp_path, {"a.py": "from PyQt6.QtCore import Qt\n", "b.py": "def f():\n    from PyQt6.QtQuick import X\n",
                     "c/d.py": "import PyQt6.QtMultimedia\n", "e.py": "# from PyQt6.QtNope import nothing\n"})
    assert release.qt_modules_used(tmp_path) == {"QtCore", "QtQuick", "QtMultimedia"}
    # and the build's own check imports each one and opens a window
    assert "importlib.import_module(\"PyQt6.\" + name)" in release.CHECK and "MainWindow(" in release.CHECK


def test_a_rehearsal_puts_the_release_together_and_opens_it_without_signing_or_publishing():
    """./run.sh --release --rehearse: what a release does up to signing (put together, every module imported and a
    window opened on the app's own Python and Qt, then the app opened with its own launcher and left running), in
    dist/rehearsal. The real release opens it the same way before signing it."""
    import inspect
    release = _release()
    rehearse, assemble = inspect.getsource(release.rehearse), inspect.getsource(release.assemble)
    assert "assemble(" in rehearse and "sign_all(app, identity" not in rehearse and "publish(" not in rehearse
    assert "notarize(" not in rehearse and '"rehearsal"' in rehearse
    assert assemble.index("check_runs(") < assemble.index('sign_all(app, "-")') < assemble.index("check_launches(")
    assert "assemble(" in inspect.getsource(release.release)  # (the real one checks the same, then signs)
    # Signed for this Mac only: no Apple timestamp, no team to check
    assert release.LAUNCH_SECONDS >= 10
