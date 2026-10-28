# Backup & restore: everything you've set up, in one .zip.
#
# In a backup: Spaces, tabs, folders, favorites, history, the archive and every setting (which
# includes Boosts, Air Traffic rules and the extension lists), plus notes, easels, Browse for Me answers,
# userscripts, extensions and their saved data, and your passwords and passkeys (encrypted, with their keys
# only in the copy locked with your recovery PIN: without the PIN, a backup can't sign in anywhere).
# Not in it: the browser profiles (cookies, logins, cache), which are private and large, and
# favicons and ad-block lists, which rebuild themselves.
#
# Restoring never happens under the running browser (which would save over it on the way out):
# the backup is unpacked next to the data as "restore-pending", and swapped in at the next launch.
# What it replaced is kept in a "before-restore-…" folder.

import json
import shutil
import time
from pathlib import Path

from . import brand, storage

FORMAT = "eclipse-backup"
OLD_FORMATS = ("pythonbrowser-backup",)  # (made before the rename: restored the same)
INCLUDED = ["session.json", "notes", "easels", "browse", "userscripts", "extension_storage", "extensions",
            "NativeMessagingHosts", "passkeys", "passwords", "recovery.json"]
PENDING = "restore-pending"
# What runs code: in your pages (extensions, userscripts) or on your Mac (the apps extensions may talk to). A backup
# from someone else could carry any of it, so restoring says what's there, and can leave it out.
CODE = ["extensions", "extension_storage", "userscripts", "NativeMessagingHosts"]
KEEP_CODE = "keep-current-extensions"  # (in a staged restore: leave the extensions you have now)


def create(dest: Path) -> int:
    """Write a backup to dest; returns how many files it holds."""
    import zipfile  # (here, not at launch: only backing up and restoring need it)
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
    import zipfile
    try:
        with zipfile.ZipFile(path) as archive:
            info = json.loads(archive.read("backup.json"))
            session = json.loads(archive.read("session.json")) if "session.json" in archive.namelist() else {}
            names = archive.namelist()
    except (KeyError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError(f"That isn't a {brand.NAME} backup.") from error
    if info.get("format") not in (FORMAT, *OLD_FORMATS):
        raise ValueError(f"That isn't a {brand.NAME} backup.")
    spaces = session.get("spaces", [])
    return {"created": info.get("created", 0), "files": info.get("files", 0),
            "spaces": [s.get("name", "") for s in spaces],
            "tabs": sum(len(s.get("pinned", [])) + len(s.get("today", [])) for s in spaces),
            **code_in(names)}


def code_in(names: list[str]) -> dict:
    """What in a backup's files runs code: {"extensions": n, "userscripts": n, "native": n}."""
    def under(top, depth):
        return {"/".join(n.split("/")[:depth]) for n in names if n.startswith(top + "/") and n.count("/") >= depth - 1
                and not n.endswith("/")}
    return {"extensions": len(under("extensions", 2)),
            "userscripts": len([n for n in under("userscripts", 2) if n.endswith(".js")]),
            "native": len([n for n in under("NativeMessagingHosts", 2) if n.endswith(".json")])}


def stage(path: Path, with_code=True):
    """Unpack a backup so the next launch restores it. Without code: not its extensions, userscripts or apps
    for extensions, and the ones you have now stay."""
    import zipfile
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
            if top in CODE and not with_code:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    if not with_code:
        (pending / KEEP_CODE).touch()


def apply_pending() -> Path | None:
    """At startup, before anything is loaded: swap in a staged backup. Returns where the replaced data went."""
    root = storage.data_dir()
    pending = root / PENDING
    if not pending.is_dir():
        return None
    kept = root / time.strftime("before-restore-%Y-%m-%d-%H%M%S")
    kept.mkdir()
    keep_code = (pending / KEEP_CODE).exists()
    for name in INCLUDED:
        if keep_code and name in CODE:
            continue
        if (root / name).exists():
            shutil.move(str(root / name), str(kept / name))
        if (pending / name).exists():
            shutil.move(str(pending / name), str(root / name))
    shutil.rmtree(pending, ignore_errors=True)
    return kept
