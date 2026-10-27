"""Verified builds: asking macOS whether a copy is as it was signed, against real apps on this Mac."""

import plistlib
import re
import subprocess
import time
from pathlib import Path

import pytest

from browser import verified

pytestmark = pytest.mark.mac  # (codesign and spctl)


def _check(bundle, team=""):
    from PyQt6.QtCore import QCoreApplication
    check = verified.Verification(bundle=Path(bundle), team=team)
    end = time.monotonic() + 60
    while check.state == verified.CHECKING and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    return check.state


def test_running_from_source_is_built_from_source(qapp):
    assert verified.app_bundle() is None  # (this checkout)
    assert verified.Verification().state == verified.SOURCE
    assert verified.app_bundle("/Applications/Some.app/Contents/Resources/browser/x.py") is None  # (no such app)


def test_what_the_checks_mean():
    ok = (0, "")
    assert verified.outcome((1, "x: code object is not signed at all"), None, None) == verified.UNSIGNED
    assert verified.outcome((1, "x: a sealed resource is missing or invalid"), None, None) == verified.CHANGED
    assert verified.outcome(ok, None, None) == verified.OTHER  # (no maker to check against)
    assert verified.outcome(ok, (3, "test-requirement: code failed to satisfy"), None) == verified.OTHER
    assert verified.outcome(ok, ok, (3, "rejected")) == verified.OTHER  # (not notarized)
    assert verified.outcome(ok, ok, ok) == verified.VERIFIED


def test_an_unsigned_app_and_a_changed_one(qapp, tmp_path):
    fake = tmp_path / "Fake.app" / "Contents"
    (fake / "MacOS").mkdir(parents=True)
    (fake / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Fake", "CFBundleIdentifier": "t.fake"}))
    (fake / "MacOS" / "Fake").write_text("#!/bin/sh\n")
    (fake / "MacOS" / "Fake").chmod(0o755)
    assert verified.app_bundle(fake / "MacOS" / "Fake") == fake.parent
    assert _check(fake.parent) == verified.UNSIGNED
    # Signed (ad hoc, as ./run.sh --build-app does), then changed
    subprocess.run(["codesign", "--force", "--sign", "-", str(fake.parent)], check=True, capture_output=True)
    assert _check(fake.parent) == verified.OTHER  # (signed, but not by the maker)
    (fake / "Resources").mkdir()
    (fake / "Resources" / "added.py").write_text("print('changed')\n")
    assert _check(fake.parent) == verified.CHANGED


def test_an_apple_app_is_signed_but_isnt_an_eclipse_release(qapp):
    app = Path("/System/Applications/Calculator.app")
    if not app.exists():
        pytest.skip("no Calculator here")
    assert _check(app, team="ABCDE12345") == verified.OTHER


def _a_developer_id_app():
    """A notarized Developer ID app on this Mac and its team, or None."""
    for app in sorted(Path("/Applications").glob("*.app"))[:40]:
        info = subprocess.run(["codesign", "-dvv", str(app)], capture_output=True, text=True).stderr
        team = re.search(r"TeamIdentifier=(\w+)", info)
        if "Authority=Developer ID Application" in info and team:
            return app, team.group(1)
    return None


def test_a_notarized_developer_id_app_is_verified_only_for_its_own_team(qapp):
    found = _a_developer_id_app()
    if found is None:
        pytest.skip("no Developer ID app in /Applications")
    app, team = found
    assert _check(app, team=team) == verified.VERIFIED
    assert _check(app, team="ABCDE12345") == verified.OTHER  # (someone else's)
