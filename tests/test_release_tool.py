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
