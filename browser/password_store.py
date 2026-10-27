# Saved passwords: kept encrypted, with the key somewhere a password stealer can't just copy.
#
# Your logins are in passwords/logins.json, encrypted (AES-256-GCM) with a random key. The key isn't in the data
# folder: it's in the Keychain, where only Eclipse can read it (secure_store.py), so the file on its own (in a
# backup, say, or copied off the Mac) is useless. That's what went wrong for Chrome: its key could be read by
# any program, so stealing passwords meant stealing two files. Showing or filling a password also asks for
# Touch ID or the Mac's password first (passwords.py).
#
# With a recovery PIN (recovery.py), passwords/recovery.json is the key, sealed with the key made from the
# PIN; it goes into backups with the logins, so on another Mac the PIN brings your passwords back.
#
# Importing: a CSV exported from Apple's Passwords app (File → Export All Passwords), Chrome, Arc, Firefox or
# Bitwarden, or straight from Arc's own saved passwords (macOS asks you first: Arc's key is Arc's).

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PyQt6.QtCore import QUrl

from . import recovery, secure_store, storage
from .passkey_store import PUBLIC_SUFFIXES

KEY_ACCOUNT = "passwords-key"
SEAL, KEY_SEAL = b"eclipse-passwords-v1", b"eclipse-passwords-key"
ARC_PROFILES = Path.home() / "Library" / "Application Support" / "Arc" / "User Data"


class Locked(Exception):
    """There are saved passwords, but not their key: they came from a backup, and need the recovery PIN."""


# --- The logins ----------------------------------------------------------------------------------------

def _folder() -> Path:
    return storage.private_folder(storage.data_dir() / "passwords")


def _logins_path() -> Path:
    return storage.data_dir() / "passwords" / "logins.json"


def _key(create=False) -> bytes | None:
    key = secure_store.load(KEY_ACCOUNT)
    if key is None and create and not _logins_path().exists():
        key = os.urandom(32)
        if not secure_store.store(KEY_ACCOUNT, key):
            raise OSError("Couldn't keep the passwords' key in the Keychain")
        if (recovery_key := recovery.key()) is not None:
            _reseal(recovery_key, None, key)
    return key


def needs_recovery() -> bool:
    return _logins_path().exists() and secure_store.load(KEY_ACCOUNT) is None


def logins() -> list[dict]:
    saved = storage.read_json(_logins_path())
    if not saved:
        return []
    key = _key()
    if key is None:
        raise Locked()
    return json.loads(recovery.unseal(key, saved, SEAL))["logins"]


def _save(entries: list[dict]):
    key = _key(create=True)
    if key is None:
        raise Locked()
    storage.write_json(_folder() / "logins.json", {"version": 1, **recovery.seal(
        key, json.dumps({"logins": entries}).encode(), SEAL)}, private=True)


def site_of(url: str) -> str:
    return QUrl(url).host().lower().removeprefix("www.")


def _origin(url: str) -> str:
    q = QUrl(url.strip())
    port = q.port()
    default = {"https": 443, "http": 80}.get(q.scheme())
    return f"{q.scheme()}://{q.host().lower()}" + (f":{port}" if port not in (-1, default) else "")


def add(url: str, username: str, password: str, note: str = "", source: str = "") -> dict:
    """Save a login (one per site and username: saving again changes it)."""
    entries = logins()
    origin = _origin(url)
    found = next((e for e in entries if e["origin"] == origin and e["username"] == username), None)
    now = time.time()
    if found is None:
        found = {"id": os.urandom(8).hex(), "origin": origin, "username": username, "created": now,
                 "last_used": None}
        entries.append(found)
    found.update(password=password, note=note or found.get("note", ""), changed=now, source=source)
    _save(entries)
    return found


def update(login_id: str, **changes):
    """Change a login (its site too: url=…)."""
    entries = logins()
    for entry in entries:
        if entry["id"] == login_id:
            entry.update({k: v for k, v in changes.items() if k in ("username", "password", "note", "last_used")})
            if changes.get("url"):
                entry["origin"] = _origin(changes["url"])
            entry["changed"] = time.time()
    _save(entries)


COMMON = {"password", "password1", "123456", "12345678", "123456789", "1234567890", "qwerty", "qwerty123",
          "letmein", "welcome", "iloveyou", "admin", "abc123", "111111", "000000", "monkey", "dragon", "sunshine",
          "football", "baseball", "princess", "trustno1", "passw0rd", "p@ssw0rd", "changeme"}


def weak(password: str) -> bool:
    """Easy to guess: short, all one kind of character and not long, or one of the passwords everyone tries."""
    p = password.lower()
    if len(password) < 8 or p in COMMON or p.rstrip("0123456789!") in COMMON:
        return True
    kinds = sum(bool(any(test(c) for c in password)) for test in (str.islower, str.isupper, str.isdigit,
                                                                   lambda c: not c.isalnum()))
    return kinds == 1 and len(password) < 14


def health(entries: list[dict]) -> dict[str, list[str]]:
    """What's worth changing, by login: "reused" (the same password on another site), "weak". Worked out here,
    on this Mac: nothing's sent anywhere to check."""
    sites_by_password = {}
    for entry in entries:
        sites_by_password.setdefault(entry["password"], set()).add(site_of(entry["origin"]))
    found = {}
    for entry in entries:
        issues = []
        if len(sites_by_password[entry["password"]]) > 1:
            issues.append("reused")
        if weak(entry["password"]):
            issues.append("weak")
        if issues:
            found[entry["id"]] = issues
    return found


def delete(login_id: str):
    _save([e for e in logins() if e["id"] != login_id])


def related(saved_host: str, page_host: str) -> bool:
    """The same site, or one of its own subdomains (accounts.google.com for google.com), never another site
    under a shared suffix (one github.io site for another)."""
    a, b = saved_host.lower().removeprefix("www."), page_host.lower().removeprefix("www.")
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return long.endswith("." + short) and "." in short and short not in PUBLIC_SUFFIXES


def same_site(a: str, b: str) -> bool:
    """Two hosts of one site: one inside the other, or both under the same domain (shop. and accounts.example.com),
    never two sites under a shared suffix (two github.io sites, two co.uk companies)."""
    a, b = a.lower().removeprefix("www."), b.lower().removeprefix("www.")
    if related(a, b):
        return True
    parts = a.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent not in PUBLIC_SUFFIXES and b.endswith("." + parent):
            return True
    return False


def for_page(url: str) -> list[dict]:
    """The logins for a page: its own site's first, then its subdomains'. Only for a page over https (or this
    Mac's own), and an http login only for an http page."""
    q = QUrl(url)
    if q.scheme() not in ("https", "http"):
        return []
    try:
        entries = logins()
    except Locked:
        return []
    host = q.host().lower()
    if q.scheme() == "http" and not (host == "localhost" or host.endswith(".localhost")):
        return []  # (a password typed into a page anyone on the network can read: never filled)
    found = [e for e in entries if related(QUrl(e["origin"]).host(), host)]
    return sorted(found, key=lambda e: (QUrl(e["origin"]).host().removeprefix("www.") != host.removeprefix("www."),
                                        -(e.get("last_used") or e.get("created") or 0)))


# --- The recovery copy -----------------------------------------------------------------------------------

def _reseal(new: bytes, old: bytes | None, key: bytes | None = None):
    if (key := key or secure_store.load(KEY_ACCOUNT)) is not None:
        storage.write_json(_folder() / "recovery.json", {"version": 1, **recovery.seal(new, key, KEY_SEAL)},
                           private=True)


def _restore(recovery_key: bytes) -> int:
    if secure_store.load(KEY_ACCOUNT) is not None:
        return 0
    saved = storage.read_json(storage.data_dir() / "passwords" / "recovery.json")
    if not saved:
        return 0
    try:
        key = recovery.unseal(recovery_key, saved, KEY_SEAL)
    except Exception:
        return 0
    secure_store.store(KEY_ACCOUNT, key)
    try:
        return len(logins())
    except Exception:
        return 0


recovery.register(_reseal, _restore)


# --- Importing -------------------------------------------------------------------------------------------

COLUMNS = {  # (each app's names for the same things, lowercased)
    "url": ("url", "login_uri", "website", "web site", "origin_url", "login url"),
    "username": ("username", "login_username", "user name", "login", "email"),
    "password": ("password", "login_password"),
    "note": ("notes", "note", "comments"),
}


def parse_csv(text: str) -> list[dict]:
    """Logins from an exported CSV (Apple Passwords, Chrome, Arc, Firefox, Bitwarden): url, username, password,
    note. Rows that aren't a website's login (apps, cards, notes) are left out."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        return []
    names = {name.strip().lower(): name for name in reader.fieldnames}
    pick = {field: next((names[c] for c in choices if c in names), None) for field, choices in COLUMNS.items()}
    if not pick["url"] or not pick["password"]:
        raise ValueError("That file doesn't look like a passwords export (it needs URL and Password columns)")
    rows = []
    for row in reader:
        url = (row.get(pick["url"]) or "").strip()
        password = row.get(pick["password"]) or ""
        if not password or ("://" in url and not url.startswith(("https://", "http://"))) or "." not in url:
            continue  # (no password, an app's link like android://, or not an address at all)
        if not url.startswith(("https://", "http://")):
            url = "https://" + url  # (a bare "example.com")
        rows.append({"url": url, "username": (row.get(pick["username"]) or "").strip() if pick["username"] else "",
                     "password": password, "note": (row.get(pick["note"]) or "").strip() if pick["note"] else ""})
    return rows


def import_rows(rows: list[dict], source: str) -> tuple[int, int, int]:
    """Save imported logins: (added, changed, already the same)."""
    entries = logins()
    by_key = {(e["origin"], e["username"]): e for e in entries}
    added = changed = same = 0
    now = time.time()
    for row in rows:
        origin = _origin(row["url"])
        if not QUrl(origin).host():
            continue
        found = by_key.get((origin, row["username"]))
        if found is None:
            found = by_key[(origin, row["username"])] = {
                "id": os.urandom(8).hex(), "origin": origin, "username": row["username"], "created": now,
                "last_used": None, "password": row["password"], "note": row.get("note", ""), "changed": now,
                "source": source}
            entries.append(found)
            added += 1
        elif found["password"] != row["password"]:
            found.update(password=row["password"], changed=now, source=source)
            changed += 1
        else:
            same += 1
    _save(entries)
    return added, changed, same


def arc_installed() -> bool:
    try:
        return any((p / "Login Data").exists() for p in ARC_PROFILES.iterdir())
    except OSError:
        return False


def arc_secret() -> bytes | None:
    """Arc's key for its saved passwords, from the Keychain: macOS asks you first (it's Arc's)."""
    try:
        done = subprocess.run(["security", "find-generic-password", "-s", "Arc Safe Storage", "-w"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.rstrip(b"\n") if done.returncode == 0 and done.stdout.strip() else None


def chromium_decrypt(value: bytes, secret: bytes) -> str | None:
    """A Chromium browser's saved password, as macOS versions keep it ("v10", AES-128-CBC)."""
    if not value.startswith(b"v10"):
        return None
    key = hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1003, 16)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    try:
        padded = decryptor.update(value[3:]) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()
    except ValueError:
        return None


def read_arc(secret: bytes, folder: Path | None = None) -> list[dict]:
    """Every login Arc saved, in every profile (read from a copy: Arc may have them open)."""
    folder = folder or ARC_PROFILES
    rows, seen = [], set()
    with tempfile.TemporaryDirectory() as scratch:
        for profile in sorted(p for p in folder.iterdir() if p.is_dir()):
            for name in ("Login Data", "Login Data For Account"):
                if not (profile / name).exists():
                    continue
                copy = Path(scratch) / f"{profile.name}-{name}"
                shutil.copy2(profile / name, copy)
                with sqlite3.connect(copy) as db:
                    found = db.execute("SELECT origin_url, username_value, password_value FROM logins "
                                       "WHERE blacklisted_by_user = 0").fetchall()
                for url, username, value in found:
                    password = chromium_decrypt(bytes(value or b""), secret)
                    if password and url.startswith(("https://", "http://")) and (url, username) not in seen:
                        seen.add((url, username))
                        rows.append({"url": url, "username": username or "", "password": password, "note": ""})
    return rows
