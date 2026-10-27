# Updates for the release app (tools/release.py): the app with everything inside it, signed and notarized.
#
# A release can't fast-forward a checkout the way the development app does (self_update.py), so it updates
# the way other Mac apps do: once a day it asks GitHub for the newest release (a tag like release-202610271452,
# with Eclipse.zip attached), and when that's newer than this copy it offers to download it.
#
# Nothing downloaded runs, or replaces this copy, until macOS has checked it:
#   - codesign: every file is as signed, by a Developer ID certificate from Eclipse's maker's team
#   - spctl: Apple notarized it
#   - its bundle ID is Eclipse's, and its build number is newer than this one's (no going backwards)
# So a tampered download, or someone else's build, is refused, whatever GitHub served.
#
# The swap happens after Eclipse quits: the old copy is moved aside, the new one put in its place, and if
# that fails the old one goes back. The copy set aside is deleted the next time Eclipse starts.

import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import QStandardPaths

from . import brand, macos, verified

REPOSITORY = f"{brand.ORGANIZATION}/{brand.NAME}"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TAG_PREFIX = "release-"
ASSET = f"{brand.NAME}.zip"
TIMEOUT = 30
TRANSLOCATED = "/AppTranslocation/"  # (macOS runs a downloaded app from here until it's moved out of Downloads)


def work_dir() -> Path:
    """Where downloads are unpacked and the old copy is set aside (on the same disk as the app, usually)."""
    base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation))
    return base / "update"


def build_of(bundle: Path) -> int:
    """A copy's build number (CFBundleVersion: the time it was built, like 202610271452); 0 if unknown."""
    try:
        version = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes()).get("CFBundleVersion", "")
        return int(version)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return 0


def _fetch_json(url):
    import urllib.request  # (here, not at launch: it brings in the whole web stack, and the first look is later)
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"{brand.NAME} updater"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def notes(body: str, limit=8) -> list[str]:
    """A release's notes as a few lines for the offer ("- Better tabs" → "Better tabs")."""
    lines = [line.strip().lstrip("-*•").strip() for line in (body or "").splitlines()]
    return [line for line in lines if line and not line.startswith("#")][:limit]


def inspect(bundle: Path, fetch=_fetch_json) -> dict:
    """Like self_update.inspect, for a release: {"state": …, "build": n, "url": …, "subjects": […], …}."""
    if TRANSLOCATED in str(bundle):
        return {"state": "unavailable", "reason": f"Move {brand.NAME} to the Applications folder to get updates."}
    try:
        release = fetch(LATEST)
    except Exception as error:  # (offline, GitHub down, rate limited: all just "try later")
        return {"state": "unavailable", "reason": f"Couldn't reach GitHub ({error})"}
    tag = str(release.get("tag_name", ""))
    build = int(tag[len(TAG_PREFIX):]) if tag.startswith(TAG_PREFIX) and tag[len(TAG_PREFIX):].isdigit() else 0
    asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET), None)
    if not build or asset is None or build <= build_of(bundle):
        return {"state": "current", "behind": 0}
    found = {"behind": 1, "build": build, "url": asset["browser_download_url"], "size": asset.get("size", 0),
             "version": release.get("name") or tag, "subjects": notes(release.get("body", "")),
             "notes": (release.get("body") or "")[:6000]}  # (the whole of them, for the update window)
    if not (os.access(bundle, os.W_OK) and os.access(bundle.parent, os.W_OK)):
        return {**found, "state": "blocked", "reason": f"This account can't change {bundle}."}
    return {**found, "state": "available"}


def download(url: str, to: Path, progress=None, opener=None) -> Path:
    """Fetch the zip to `to`; progress(fraction) as it comes in."""
    import urllib.request
    opener = opener or urllib.request.urlopen
    to.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": f"{brand.NAME} updater"})
    with opener(request, timeout=TIMEOUT) as response, open(to, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    return to


def _run(*command) -> tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return done.returncode, (done.stderr or done.stdout).strip()


def unpack(archive: Path, into: Path) -> Path | None:
    """The app inside the zip (ditto keeps the signature's extended attributes and links intact)."""
    shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True)
    code, _ = _run("/usr/bin/ditto", "-x", "-k", str(archive), str(into))
    apps = list(into.glob("*.app"))
    return apps[0] if code == 0 and len(apps) == 1 else None


def problem(app: Path, newer_than: int, team=verified.TEAM_ID, run=_run) -> str | None:
    """Why a downloaded app mustn't be installed, or None when macOS vouches for it."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return "It isn't an app."
    if info.get("CFBundleIdentifier") != macos.APP_BUNDLE_ID:
        return f"It isn't {brand.NAME}."
    if build_of(app) <= newer_than:
        return "It isn't newer than this copy."
    code, output = run("/usr/bin/codesign", "--verify", "--deep", "--strict",
                       f"-R={verified.REQUIREMENT.format(team=team)}", str(app))
    if code != 0:
        return f"Its signature isn't {brand.NAME}'s maker's ({output.splitlines()[-1] if output else 'no detail'})."
    code, _ = run("/usr/sbin/spctl", "--assess", "--type", "execute", str(app))
    if code != 0:
        return "Apple hasn't notarized it."
    return None


def prepare(found: dict, bundle: Path, progress=None) -> tuple[Path | None, str]:
    """Download and check the release `found` describes: (the checked app ready to swap in, "") or (None, why)."""
    folder = work_dir()
    try:
        archive = download(found["url"], folder / ASSET, progress)
    except Exception as error:
        return None, f"The download didn't finish ({error})."
    app = unpack(archive, folder / "new")
    archive.unlink(missing_ok=True)
    if app is None:
        return None, "The download wasn't a working app."
    why = problem(app, build_of(bundle))
    if why:
        shutil.rmtree(folder / "new", ignore_errors=True)
        return None, f"The update was refused: {why}"
    return app, ""


# Run by /bin/sh once this process has quit: $0 the pid, $1 this copy, $2 the new one, $3 where the old goes
SWAP = ('while kill -0 "$0" 2>/dev/null; do sleep 0.2; done; '
        'rm -rf "$3"; '
        'if mv "$1" "$3"; then mv "$2" "$1" || mv "$3" "$1"; fi; '
        'exec /usr/bin/open -n "$1"')


def swap_command(bundle: Path, new: Path, pid: int) -> list[str]:
    return ["/bin/sh", "-c", SWAP, str(pid), str(bundle), str(new), str(work_dir() / "old.app")]


def tidy():
    """Delete what the last update left (the old copy, a half-finished download). Quick when there's none."""
    shutil.rmtree(work_dir(), ignore_errors=True)
