import importlib.util
import plistlib
from pathlib import Path

import pytest

from browser import brand, keychain


def test_the_icon_draws_at_every_size(qapp):
    for size in (16, 32, 64, 128, 1024):
        image = brand.icon_image(size)
        assert image.width() == image.height() == size
        middle, corner = image.pixelColor(size // 2, size // 2), image.pixelColor(0, 0)
        assert middle.alpha() == 255 and corner.alpha() == 0  # a tile with macOS's margin round it


def test_the_old_name_is_only_where_things_are_moved_from():
    assert brand.NAME == brand.INTERNAL_NAME == "Eclipse"
    assert brand.OLD_INTERNAL_NAME == keychain.SERVICE == "PythonBrowser"  # (the oldest tokens, moved when read)


def test_the_data_folder_moves_to_its_new_name(tmp_path):
    from browser import storage
    old, new = tmp_path / "MasonKimball05" / "PythonBrowser", tmp_path / "MasonKimball05" / "Eclipse"
    (old / "profile").mkdir(parents=True)
    (old / "session.json").write_text("{}")
    caches = tmp_path / "Caches" / "MasonKimball05" / "Eclipse"
    (caches.with_name("PythonBrowser") / "cache").mkdir(parents=True)
    storage._move_old_folder(new, caches)
    assert (new / "session.json").read_text() == "{}" and (new / "profile").is_dir() and not old.exists()
    assert not caches.with_name("PythonBrowser").exists()
    (old / "late").mkdir(parents=True)
    storage._move_old_folder(new, caches)  # (a new folder in use is never replaced by an old one)
    assert old.exists() and not (new / "late").exists()


def test_an_empty_new_folder_made_too_early_doesnt_strand_your_data(tmp_path):
    from browser import storage
    old, new = tmp_path / "PythonBrowser", tmp_path / "Eclipse"
    old.mkdir()
    (old / "session.json").write_text("{\"yours\": 1}")
    (new / "dictionaries").mkdir(parents=True)  # (something called data_dir() before the move)
    storage._move_old_folder(new, tmp_path / "Caches" / "Eclipse")
    assert (new / "session.json").read_text() == '{"yours": 1}' and not old.exists()
    assert (tmp_path / "Eclipse (made before the move)" / "dictionaries").is_dir()


def _build_app_module():
    spec = importlib.util.spec_from_file_location("build_app", Path(__file__).parent.parent / "tools" / "build_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_app(path: Path, bundle_id):
    (path / "Contents").mkdir(parents=True)
    (path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))


@pytest.mark.mac
def test_the_old_app_goes_to_the_trash_only_if_its_ours(tmp_path, monkeypatch):
    build_app = _build_app_module()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(build_app, "LSREGISTER", "/usr/bin/true")
    (tmp_path / ".Trash").mkdir()
    ours, theirs = tmp_path / "PythonBrowser.app", tmp_path / "Other" / "PythonBrowser.app"
    _fake_app(ours, build_app.BUNDLE_ID)
    _fake_app(theirs, "com.someone.else")
    build_app.retire_old_app(ours)
    build_app.retire_old_app(theirs)
    assert not ours.exists() and (tmp_path / ".Trash" / "PythonBrowser.app").exists()
    assert theirs.exists()  # someone else's app of the same name is never touched
    _fake_app(ours, build_app.BUNDLE_ID)
    build_app.retire_old_app(ours)
    assert (tmp_path / ".Trash" / "PythonBrowser 2.app").exists()  # an earlier one in the Trash is kept


@pytest.mark.mac
def test_copies_in_the_trash_are_forgotten_by_launch_services(tmp_path, monkeypatch):
    build_app = _build_app_module()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    forgotten = []
    monkeypatch.setattr(build_app.subprocess, "run", lambda args, check=False: forgotten.append(args[-1]))
    _fake_app(tmp_path / ".Trash" / "Halo.app", build_app.BUNDLE_ID)
    _fake_app(tmp_path / ".Trash" / "Other.app", "com.someone.else")
    build_app.forget_trashed()
    assert forgotten == [str(tmp_path / ".Trash" / "Halo.app")]


def test_the_chosen_icon_is_remembered_and_an_unknown_one_falls_back():
    assert brand.chosen_look({}) == brand.LOOK
    assert brand.chosen_look({brand.SETTING: "violet"}) == "violet"
    assert brand.chosen_look({brand.SETTING: "neon-pink"}) == brand.LOOK  # (a look that's since been removed)
    assert set(brand.LABELS) == set(brand.LOOKS)


def test_building_the_app_uses_the_icon_picked_in_settings(qapp, data_dir):
    import json
    from browser import storage
    storage.session_path().write_text(json.dumps({"settings": {brand.SETTING: "sunset"}}))
    assert brand.saved_look() == "sunset"
    a, b = brand.icon_image(64, "sunset"), brand.icon_image(64, "original")
    assert a.pixelColor(20, 20) != b.pixelColor(20, 20)  # they really are different pictures


@pytest.mark.mac  # (clang and the macOS launcher)
def test_the_launcher_compiles(tmp_path):
    import shutil
    if shutil.which("clang") is None:
        pytest.skip("needs clang (Xcode's command line tools)")
    build_app = _build_app_module()
    build_app.compile_launcher(tmp_path / "Eclipse")
    assert (tmp_path / "Eclipse").stat().st_size > 0


@pytest.mark.mac  # (clang and the macOS launcher)
def test_the_launcher_compiles_from_a_folder_with_quotes_and_backslashes(tmp_path, monkeypatch):
    import shutil
    if shutil.which("clang") is None:
        pytest.skip("needs clang (Xcode's command line tools)")
    build_app = _build_app_module()
    odd = tmp_path / 'my "odd" \\ folder'
    odd.mkdir()
    monkeypatch.setattr(build_app, "ROOT", odd)
    assert build_app.c_string('/a/b"c\\d') == '"/a/b\\"c\\\\d"'
    build_app.compile_launcher(tmp_path / "Eclipse")
    assert str(odd / "main.py").encode() in (tmp_path / "Eclipse").read_bytes()  # the path, exactly as it is


def test_downloads_get_the_quarantine_flag(tmp_path):
    import subprocess
    import sys
    from browser import macos
    if sys.platform != "darwin":
        pytest.skip("macOS's own attribute")
    target = tmp_path / "tool.command"
    target.write_text("echo hi")
    assert macos.quarantine(str(target))
    listed = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(target)], capture_output=True, text=True)
    assert listed.stdout.startswith("0081;") and "Eclipse" in listed.stdout


def test_installs_where_finder_shows_it_and_retires_the_home_folder_copy():
    from pathlib import Path
    build_app = _build_app_module()
    assert build_app.APP == Path("/Applications/Eclipse.app")
    assert Path.home() / "Applications" / "Eclipse.app" in build_app.OLD_APPS
    assert build_app.APP not in build_app.OLD_APPS


def test_falls_back_to_the_home_folder_when_applications_has_someone_elses_eclipse(tmp_path, monkeypatch):
    build_app = _build_app_module()
    taken = tmp_path / "Applications" / "Eclipse.app"
    _fake_app(taken, "org.eclipse.sdk.ide")  # (the Eclipse IDE)
    monkeypatch.setattr(build_app, "APP", taken)
    monkeypatch.setattr(build_app.Path, "home", lambda: tmp_path / "home")
    chosen = build_app.install_path()
    assert chosen == tmp_path / "home" / "Applications" / "Eclipse.app" and build_app.APP == chosen
    assert chosen not in build_app.OLD_APPS
