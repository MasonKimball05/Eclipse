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
