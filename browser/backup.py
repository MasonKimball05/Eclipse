# Backup & restore: everything you've set up, in one .zip.
#
# In a backup: Spaces, tabs, folders, favorites, history, the archive and every setting (which
# includes Boosts, Air Traffic rules and the extension lists), plus notes, easels, Browse for Me answers,
# userscripts, extensions and their saved data.
# Not in it: the browser profiles (cookies, logins, cache), which are private and large, and
# favicons and ad-block lists, which rebuild themselves.
#
# Restoring never happens under the running browser (which would save over it on the way out):
# the backup is unpacked next to the data as "restore-pending", and swapped in at the next launch.
# What it replaced is kept in a "before-restore-…" folder.

import json
import shutil
import time
import zipfile
from pathlib import Path

from . import brand, storage

FORMAT = "pythonbrowser-backup"
INCLUDED = ["session.json", "notes", "easels", "browse", "userscripts", "extension_storage", "extensions",
            "NativeMessagingHosts"]
PENDING = "restore-pending"


def create(dest: Path) -> int:
    """Write a backup to dest; returns how many files it holds."""
    root = storage.data_dir()
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in INCLUDED:
            path = root / name
            files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.exists() else []
            for file in files:
                archive.write(file, file.relative_to(root).as_posix())
                count += 1
        archive.writestr("backup.json", json.dumps({"format": FORMAT, "version": 1, "created": time.time(),
                                                    "files": count}))
    return count


def describe(path: Path) -> dict:
    """What's in a backup (for the confirmation), or raises ValueError if it isn't one."""
    try:
        with zipfile.ZipFile(path) as archive:
            info = json.loads(archive.read("backup.json"))
            session = json.loads(archive.read("session.json")) if "session.json" in archive.namelist() else {}
    except (KeyError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError(f"That isn't a {brand.NAME} backup.") from error
    if info.get("format") != FORMAT:
        raise ValueError(f"That isn't a {brand.NAME} backup.")
    spaces = session.get("spaces", [])
    return {"created": info.get("created", 0), "files": info.get("files", 0),
            "spaces": [s.get("name", "") for s in spaces],
            "tabs": sum(len(s.get("pinned", [])) + len(s.get("today", [])) for s in spaces)}


def stage(path: Path):
    """Unpack a backup so the next launch restores it."""
    describe(path)  # refuses anything that isn't a backup
    pending = storage.data_dir() / PENDING
    if pending.exists():
        shutil.rmtree(pending)
    pending.mkdir()
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            top = name.split("/", 1)[0]
            target = (pending / name).resolve()
            # Only what a backup can contain, and nothing that climbs out of the folder
            if top not in INCLUDED or not target.is_relative_to(pending.resolve()) or name.endswith("/"):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))


def apply_pending() -> Path | None:
    """At startup, before anything is loaded: swap in a staged backup. Returns where the replaced data went."""
    root = storage.data_dir()
    pending = root / PENDING
    if not pending.is_dir():
        return None
    kept = root / time.strftime("before-restore-%Y-%m-%d-%H%M%S")
    kept.mkdir()
    for name in INCLUDED:
        if (root / name).exists():
            shutil.move(str(root / name), str(kept / name))
        if (pending / name).exists():
            shutil.move(str(pending / name), str(root / name))
    shutil.rmtree(pending, ignore_errors=True)
    return kept
