import importlib.util
import plistlib
from pathlib import Path

from browser import brand, keychain


def test_the_icon_draws_at_every_size(qapp):
    for size in (16, 32, 64, 128, 1024):
        image = brand.icon_image(size)
        assert image.width() == image.height() == size
        middle, corner = image.pixelColor(size // 2, size // 2), image.pixelColor(0, 0)
        assert middle.alpha() == 255 and corner.alpha() == 0  # a tile with macOS's margin round it


def test_renaming_keeps_your_data_and_tokens_where_they_were():
    assert brand.NAME == "Eclipse"
    assert brand.INTERNAL_NAME == keychain.SERVICE == "PythonBrowser"


def _build_app_module():
    spec = importlib.util.spec_from_file_location("build_app", Path(__file__).parent.parent / "tools" / "build_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_app(path: Path, bundle_id):
    (path / "Contents").mkdir(parents=True)
    (path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))


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
