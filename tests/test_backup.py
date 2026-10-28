import json
import zipfile

import pytest

from browser import backup


def _fill(root, marker):
    root.mkdir(parents=True, exist_ok=True)
    (root / "session.json").write_text(json.dumps({"spaces": [{"name": marker, "pinned": [{}], "today": [{}, {}]}]}))
    (root / "notes").mkdir(exist_ok=True)
    (root / "notes" / "a.md").write_text(f"note {marker}")
    (root / "profile").mkdir(exist_ok=True)
    (root / "profile" / "Cookies").write_text("secret")


def test_round_trip(data_dir, tmp_path):
    _fill(data_dir, "Original")
    zip_path = tmp_path / "b.zip"
    assert backup.create(zip_path) == 2  # session + note; never the profile's cookies
    assert "profile/Cookies" not in zipfile.ZipFile(zip_path).namelist()
    info = backup.describe(zip_path)
    assert info["spaces"] == ["Original"] and info["tabs"] == 3

    _fill(data_dir, "Changed since")
    backup.stage(zip_path)
    assert (data_dir / "session.json").read_text().count("Changed since")  # nothing swapped until relaunch
    kept = backup.apply_pending()
    assert "Original" in (data_dir / "session.json").read_text()
    assert (data_dir / "notes" / "a.md").read_text() == "note Original"
    assert "Changed since" in (kept / "session.json").read_text()  # the replaced data is kept
    assert (data_dir / "profile" / "Cookies").read_text() == "secret"  # sign-ins untouched
    assert backup.apply_pending() is None


def test_rejects_other_zips_and_tricks(data_dir, tmp_path):
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("session.json", "{}")
    with pytest.raises(ValueError):
        backup.describe(plain)
    sneaky = tmp_path / "sneaky.zip"
    with zipfile.ZipFile(sneaky, "w") as z:
        z.writestr("backup.json", json.dumps({"format": backup.FORMAT}))
        z.writestr("notes/../../escape.txt", "x")
        z.writestr("profile/Cookies", "planted")
    backup.stage(sneaky)
    backup.apply_pending()
    assert not (data_dir.parent / "escape.txt").exists()
    assert not (data_dir / "profile" / "Cookies").exists()


def test_a_backups_extensions_are_counted_and_can_be_left_out(data_dir, tmp_path):
    _fill(data_dir, "Theirs")
    for path, text in (("extensions/abc/manifest.json", "{}"), ("extensions/abc/content.js", "steal()"),
                       ("userscripts/one.user.js", "x"), ("NativeMessagingHosts/com.evil.json", "{}")):
        (data_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / path).write_text(text)
    zip_path = tmp_path / "theirs.zip"
    backup.create(zip_path)
    info = backup.describe(zip_path)
    assert (info["extensions"], info["userscripts"], info["native"]) == (1, 1, 1)
    # Restored without them: the backup's Spaces arrive, and the extensions you have now stay
    import shutil
    for name in backup.CODE + ["session.json"]:
        target = data_dir / name
        shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    (data_dir / "extensions" / "mine").mkdir(parents=True)
    (data_dir / "extensions" / "mine" / "manifest.json").write_text("{}")
    backup.stage(zip_path, with_code=False)
    backup.apply_pending()
    assert "Theirs" in (data_dir / "session.json").read_text()
    assert [p.name for p in (data_dir / "extensions").iterdir()] == ["mine"]
    assert not (data_dir / "userscripts").exists() and not (data_dir / "NativeMessagingHosts").exists()
