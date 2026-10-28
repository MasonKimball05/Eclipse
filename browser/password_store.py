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

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
from contextlib import closing
from pathlib import Path

from PyQt6.QtCore import QUrl

from . import net, recovery, secure_store, storage
from .passkey_store import PUBLIC_SUFFIXES

KEY_ACCOUNT = "passwords-key"
SEAL, KEY_SEAL = b"eclipse-passwords-v1", b"eclipse-passwords-key"
_SUPPORT = Path.home() / "Library" / "Application Support"
# Browsers built on Chromium keep passwords the same way on a Mac: a "Login Data" database in each profile,
# encrypted with a key in the Keychain under "<name> Safe Storage". (name → its profiles folder, that key)
CHROMIUM_BROWSERS = {
    "Arc": (_SUPPORT / "Arc" / "User Data", "Arc Safe Storage"),
    "Chrome": (_SUPPORT / "Google" / "Chrome", "Chrome Safe Storage"),
    "Brave": (_SUPPORT / "BraveSoftware" / "Brave-Browser", "Brave Safe Storage"),
    "Edge": (_SUPPORT / "Microsoft Edge", "Microsoft Edge Safe Storage"),
    "Vivaldi": (_SUPPORT / "Vivaldi", "Vivaldi Safe Storage"),
    "Chromium": (_SUPPORT / "Chromium", "Chromium Safe Storage"),
}


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
            entry.update({k: v for k, v in changes.items() if k in ("username", "password", "note", "last_used",
                                                                       "otp")})
            if changes.get("url"):
                entry["origin"] = _origin(changes["url"])
            entry["changed"] = time.time()
    _save(entries)


# --- Two-factor codes ----------------------------------------------------------------------------------------
# A login can keep its two-factor setup key (the secret behind a site's QR code), as an otpauth:// link, so
# Eclipse makes the 6-digit codes an authenticator app would (RFC 6238), kept encrypted with the password.

def otp_link(text: str) -> str | None:
    """A setup key as an otpauth:// link: given one, or the bare key a site shows under its QR code. None when it
    isn't one."""
    text = (text or "").strip()
    if not text:
        return None
    if not text.lower().startswith("otpauth://"):
        text = "otpauth://totp/?secret=" + text.replace(" ", "").replace("-", "").upper()
    q = urllib.parse.urlparse(text)
    params = {k.lower(): v for k, v in urllib.parse.parse_qsl(q.query)}
    if q.netloc.lower() != "totp" or _otp_key(params.get("secret", "")) is None:
        return None  # (hotp, counter-based, is rare enough to leave out)
    period, digits = params.get("period", "30"), params.get("digits", "6")
    if not (period.isdigit() and 0 < int(period) <= 300 and digits.isdigit() and 6 <= int(digits) <= 10):
        return None
    if params.get("algorithm", "SHA1").upper() not in ("SHA1", "SHA256", "SHA512"):
        return None  # (anything else would make the wrong codes)
    return text


def _otp_key(secret: str) -> bytes | None:
    secret = secret.replace(" ", "").upper()
    try:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    except ValueError:  # (binascii.Error is one)
        return None
    return key or None


def otp_code(link: str, at: float | None = None) -> tuple[str, int]:
    """The code for now (or `at`), and the seconds it has left."""
    params = {k.lower(): v for k, v in urllib.parse.parse_qsl(urllib.parse.urlparse(link).query)}
    key = _otp_key(params.get("secret", ""))
    period = int(params.get("period") or 30)
    digits = int(params.get("digits") or 6)
    algorithm = {"SHA256": hashlib.sha256, "SHA512": hashlib.sha512}.get(params.get("algorithm", "").upper(),
                                                                        hashlib.sha1)
    now = time.time() if at is None else at
    digest = hmac.new(key, int(now // period).to_bytes(8, "big"), algorithm).digest()
    offset = digest[-1] & 0x0F
    number = int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF
    return str(number % 10 ** digits).zfill(digits), period - int(now % period)


def codes_for_page(url: str) -> list[dict]:
    """The logins for a page that have a two-factor setup key."""
    return [e for e in for_page(url) if e.get("otp")]


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


PWNED = "https://api.pwnedpasswords.com/range/"


def _pwned_range(prefix: str) -> str:
    import urllib.request  # (here, not at launch: it brings in the whole web stack)
    request = urllib.request.Request(PWNED + prefix, headers={"User-Agent": "Eclipse browser",
                                                              "Add-Padding": "true"})
    with net.urlopen(request, timeout=20) as response:  # (macOS's trusted certificates: see net.py)
        return response.read().decode()


def breached(passwords, fetch=_pwned_range) -> dict[str, int]:
    """Which passwords have turned up in a known data breach, and how often: Have I Been Pwned's range check.
    Only the first 5 characters of each password's SHA-1 hash are sent; the answer is every breached hash that
    starts with them (padded with made-up ones, so even its size says nothing), and the match is found here."""
    by_prefix = {}
    for password in set(passwords):
        digest = hashlib.sha1(password.encode()).hexdigest().upper()
        by_prefix.setdefault(digest[:5], []).append((digest[5:], password))
    found = {}
    for prefix, waiting in by_prefix.items():
        counts = {}
        for line in fetch(prefix).splitlines():
            suffix, _, count = line.strip().partition(":")
            if count.isdigit():
                counts[suffix.upper()] = int(count)
        for suffix, password in waiting:
            if counts.get(suffix, 0) > 0:  # (the padding's made-up hashes all count 0)
                found[password] = counts[suffix]
    return found


def breach_key(password: str) -> str:
    """How a breach check's findings remember a password (its SHA-1, as the check uses: never the password)."""
    return hashlib.sha1(password.encode()).hexdigest()


def health(entries: list[dict], breaches: dict[str, int] | None = None) -> dict[str, list[str]]:
    """What's worth changing, by login: "reused" (the same password on another site), "weak", and (from a check
    you ran: `breaches`, breach_key → times seen) "breached". Reused and weak are worked out here, on this Mac."""
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
        if breaches and breaches.get(breach_key(entry["password"])):
            issues.insert(0, "breached")
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
    "otp": ("otpauth", "login_totp", "totp", "otp", "otp url"),
}


def export_csv(entries: list[dict]) -> str:
    """Logins as a CSV in the layout Apple's Passwords exports (which it, Chrome, and Eclipse all import)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Title", "URL", "Username", "Password", "Notes", "OTPAuth"])
    for entry in sorted(entries, key=lambda e: (site_of(e["origin"]), e["username"])):
        writer.writerow([site_of(entry["origin"]), entry["origin"] + "/", entry["username"], entry["password"],
                         entry.get("note", ""), entry.get("otp", "")])
    return out.getvalue()


def write_export(path: str, entries: list[dict]):
    """The export, readable by this account only (it's every password, in plain text)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        os.fchmod(f.fileno(), 0o600)  # (a file that was already there keeps its mode otherwise)
        f.write(export_csv(entries))


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
                     "password": password, "note": (row.get(pick["note"]) or "").strip() if pick["note"] else "",
                     "otp": otp_link(row.get(pick["otp"]) or "") if pick["otp"] else None})
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
            if row.get("otp"):
                found["otp"] = row["otp"]
            entries.append(found)
            added += 1
        elif found["password"] != row["password"] or (row.get("otp") and found.get("otp") != row["otp"]):
            found.update(password=row["password"], changed=now, source=source)
            if row.get("otp"):
                found["otp"] = row["otp"]  # (a file exported again brings the codes along: one never goes away)
            changed += 1
        else:
            same += 1
    _save(entries)
    return added, changed, same


def browsers_installed() -> list[str]:
    """The browsers here with saved passwords to import (in CHROMIUM_BROWSERS' order)."""
    found = []
    for name, (folder, _service) in CHROMIUM_BROWSERS.items():
        try:
            if any((p / "Login Data").exists() for p in folder.iterdir()):
                found.append(name)
        except OSError:
            pass
    return found


def browser_secret(name: str) -> bytes | None:
    """A browser's key for its saved passwords, from the Keychain: macOS asks you first (it's that browser's)."""
    try:
        done = subprocess.run(["security", "find-generic-password", "-s", CHROMIUM_BROWSERS[name][1], "-w"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.rstrip(b"\n") if done.returncode == 0 and done.stdout.strip() else None


def chromium_decrypt(value: bytes, secret: bytes) -> str | None:
    """A Chromium browser's saved password, as macOS versions keep it ("v10", AES-128-CBC)."""
    if not value.startswith(b"v10"):
        return None
    from cryptography.hazmat.primitives import padding  # (only for importing: not loaded at launch)
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1003, 16)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    try:
        padded = decryptor.update(value[3:]) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()
    except ValueError:
        return None


def read_browser(name: str, secret: bytes, folder: Path | None = None) -> list[dict]:
    """Every login a browser saved, in every profile (read from a copy: it may have them open)."""
    folder = folder or CHROMIUM_BROWSERS[name][0]
    rows, seen = [], set()
    with tempfile.TemporaryDirectory() as scratch:
        for profile in sorted(p for p in folder.iterdir() if p.is_dir()):
            for name in ("Login Data", "Login Data For Account"):
                if not (profile / name).exists():
                    continue
                copy = Path(scratch) / f"{profile.name}-{name}"
                shutil.copy2(profile / name, copy)
                with closing(sqlite3.connect(copy)) as db:  # (closed: Windows can't delete an open copy)
                    found = db.execute("SELECT origin_url, username_value, password_value FROM logins "
                                       "WHERE blacklisted_by_user = 0").fetchall()
                for url, username, value in found:
                    password = chromium_decrypt(bytes(value or b""), secret)
                    if password and url.startswith(("https://", "http://")) and (url, username) not in seen:
                        seen.add((url, username))
                        rows.append({"url": url, "username": username or "", "password": password, "note": ""})
    return rows
