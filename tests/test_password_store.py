"""Saved passwords: encrypted on disk with a key only in the (stand-in) Keychain, the recovery PIN, matching
pages, and importing from exported CSVs and a fake Arc profile (never your real one)."""

import hashlib
import sqlite3

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from browser import password_store as pw
from browser import recovery


@pytest.fixture(autouse=True)
def quick_pin(monkeypatch):
    monkeypatch.setattr(recovery, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})


def test_saved_logins_are_unreadable_without_the_keychain_key(data_dir, no_real_keychain):
    pw.add("https://github.com/login", "arkan", "hunter2-very-secret", note="work")
    pw.add("https://github.com/session", "arkan", "changed-secret")  # (the same site and account: changed)
    entries = pw.logins()
    assert [(e["origin"], e["username"], e["password"]) for e in entries] == [
        ("https://github.com", "arkan", "changed-secret")]
    on_disk = (data_dir / "passwords" / "logins.json").read_bytes()
    assert b"secret" not in on_disk and b"arkan" not in on_disk and b"github" not in on_disk
    assert pw.KEY_ACCOUNT in no_real_keychain and len(no_real_keychain[pw.KEY_ACCOUNT]) == 32
    no_real_keychain.clear()  # (another Mac: the file came over, the key didn't)
    assert pw.needs_recovery() and pw.for_page("https://github.com") == []
    with pytest.raises(pw.Locked):
        pw.logins()
    with pytest.raises(pw.Locked):
        pw.add("https://a.com", "x", "y")  # (never a new key over the old file)


def test_the_recovery_pin_brings_passwords_back(no_real_keychain):
    pw.add("https://example.com", "me", "s3cret")
    assert recovery.set_pin("correct horse battery")  # (set after: the key's copy is made then)
    pw.add("https://other.com", "me", "two")
    no_real_keychain.clear()
    assert recovery.unlock("wrong horse battery") is None and pw.needs_recovery()
    assert recovery.unlock("correct horse battery") == 2
    assert {e["password"] for e in pw.logins()} == {"s3cret", "two"}


def test_a_pin_set_before_any_password_still_covers_them(no_real_keychain):
    assert recovery.set_pin("set first please")
    pw.add("https://example.com", "me", "later")
    no_real_keychain.clear()
    assert recovery.unlock("set first please") == 1 and pw.logins()[0]["password"] == "later"


def test_which_logins_a_page_gets():
    pw.add("https://github.com", "me", "a")
    pw.add("https://accounts.google.com", "me@gmail.com", "b")
    pw.add("https://alice.github.io", "alice", "c")
    pw.add("http://localhost:8000", "dev", "d")
    pw.add("http://plain.example", "old", "e")
    assert [e["username"] for e in pw.for_page("https://www.github.com/login")] == ["me"]
    assert [e["username"] for e in pw.for_page("https://google.com")] == ["me@gmail.com"]  # (its own subdomain)
    assert pw.for_page("https://bob.github.io") == []  # (someone else's site under a shared suffix)
    assert [e["username"] for e in pw.for_page("http://localhost:8000/")] == ["dev"]
    assert pw.for_page("http://plain.example/") == []  # (never filled into a page sent in the clear)
    assert pw.for_page("file:///tmp/x.html") == []


APPLE = ('﻿Title,URL,Username,Password,Notes,OTPAuth\n'
         'GitHub,https://github.com/,arkan,gh-pass,,\n'
         'Bank,bank.example,me,bank-pass,"has, a comma",\n'
         'Some App,android://abc@com.app/,x,y,,\n'
         'No password,https://nopass.example,x,,,\n')
CHROME = "name,url,username,password,note\nGitHub,https://github.com/,arkan,newer-pass,\n"
BITWARDEN = "folder,favorite,type,name,notes,fields,login_uri,login_username,login_password\n" \
            ",,login,Site,,,https://site.example,me,bw-pass\n"


def test_reading_each_apps_export():
    apple = pw.parse_csv(APPLE)
    assert [(r["url"], r["username"], r["password"], r["note"]) for r in apple] == [
        ("https://github.com/", "arkan", "gh-pass", ""), ("https://bank.example", "me", "bank-pass", "has, a comma")]
    assert pw.parse_csv(BITWARDEN)[0]["password"] == "bw-pass"
    with pytest.raises(ValueError):
        pw.parse_csv("a,b\n1,2\n")


def test_importing_adds_changes_and_skips_the_same():
    assert pw.import_rows(pw.parse_csv(APPLE), "Passwords") == (2, 0, 0)
    assert pw.import_rows(pw.parse_csv(APPLE), "Passwords") == (0, 0, 2)
    assert pw.import_rows(pw.parse_csv(CHROME), "Chrome") == (0, 1, 0)
    assert next(e for e in pw.logins() if e["username"] == "arkan")["password"] == "newer-pass"


RFC_KEY = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ("12345678901234567890", RFC 6238's test key, in base32)


@pytest.mark.parametrize("at, code", [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"),
                                      (2000000000, "69279037")])
def test_codes_match_the_rfcs_examples(at, code):
    assert pw.otp_code(f"otpauth://totp/x?secret={RFC_KEY}&digits=8", at) == (code, 30 - at % 30)


def test_codes_from_a_bare_key_or_a_link():
    assert pw.otp_link("gezd gnbv gy3t qojq") == "otpauth://totp/?secret=GEZDGNBVGY3TQOJQ"
    assert pw.otp_code(pw.otp_link(RFC_KEY), 59)[0] == "287082"  # (6 digits unless the link says otherwise)
    link = "otpauth://totp/GitHub:arkan?secret=" + RFC_KEY + "&issuer=GitHub&algorithm=SHA256&period=60"
    assert pw.otp_link(link) == link and pw.otp_code(link, 59) == ("875740", 1)  # (counter 0, worked out with hmac directly)
    for wrong in ("", "not a key!", "otpauth://hotp/x?secret=" + RFC_KEY, "otpauth://totp/x?issuer=nokey",
                  f"otpauth://totp/x?secret={RFC_KEY}&period=0", f"otpauth://totp/x?secret={RFC_KEY}&digits=abc",
                  f"otpauth://totp/x?secret={RFC_KEY}&algorithm=MD5"):
        assert pw.otp_link(wrong) is None


def test_an_export_brings_its_codes_and_importing_again_adds_them():
    without = APPLE
    with_codes = APPLE.replace("GitHub,https://github.com/,arkan,gh-pass,,",
                               f"GitHub,https://github.com/,arkan,gh-pass,,otpauth://totp/GitHub:arkan?secret={RFC_KEY}")
    assert pw.import_rows(pw.parse_csv(without), "Passwords") == (2, 0, 0)
    assert not any(e.get("otp") for e in pw.logins())
    assert pw.import_rows(pw.parse_csv(with_codes), "Passwords") == (0, 1, 1)  # (the same password, now with codes)
    assert [e["username"] for e in pw.codes_for_page("https://github.com/sessions/two-factor")] == ["arkan"]
    assert pw.import_rows(pw.parse_csv(without), "Passwords") == (0, 0, 2)  # (a file without codes keeps them)
    assert pw.codes_for_page("https://github.com/")


def _arc_encrypt(text, secret):
    key = hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1003, 16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + encryptor.update(data) + encryptor.finalize()


def test_reading_a_fake_arc_profile(tmp_path):
    secret = b"made-up-arc-key"
    for profile, rows in (("Default", [("https://github.com/login", "arkan", "arc-pass", 0),
                                       ("https://never.example/", "", "x", 1),  # (Never Save for this site)
                                       ("android://x@app/", "a", "b", 0)]),
                          ("Profile 1", [("https://work.example/", "me@work", "work-pass", 0)])):
        folder = tmp_path / profile
        folder.mkdir()
        with sqlite3.connect(folder / "Login Data") as db:
            db.execute("CREATE TABLE logins (origin_url TEXT, username_value TEXT, password_value BLOB, "
                       "blacklisted_by_user INTEGER)")
            db.executemany("INSERT INTO logins VALUES (?, ?, ?, ?)",
                           [(url, user, _arc_encrypt(password, secret), never) for url, user, password, never in rows])
    found = pw.read_browser("Arc", secret, tmp_path)
    assert [(r["url"], r["username"], r["password"]) for r in found] == [
        ("https://github.com/login", "arkan", "arc-pass"), ("https://work.example/", "me@work", "work-pass")]
    assert pw.read_browser("Arc", b"the wrong key", tmp_path) == []
    assert pw.chromium_decrypt(b"v11whatever", secret) is None


def test_only_your_account_can_read_the_files(data_dir):
    import os
    import stat
    (data_dir / "passwords").mkdir(parents=True)
    (data_dir / "passwords" / "logins.json").write_text("{}")  # (an older copy, made readable to all)
    os.chmod(data_dir / "passwords" / "logins.json", 0o644)
    (data_dir / "passwords" / "logins.json").unlink()
    pw.add("https://example.com", "me", "x")
    recovery.set_pin("a long enough pin")
    for path in ("passwords", "passwords/logins.json", "passwords/recovery.json", "recovery.json"):
        mode = stat.S_IMODE(os.stat(data_dir / path).st_mode)
        assert mode == (0o700 if path == "passwords" else 0o600), (path, oct(mode))


def test_reused_and_weak_passwords_are_pointed_out():
    a = pw.add("https://one.example", "me", "same-Pass-word-9")
    b = pw.add("https://two.example", "me", "same-Pass-word-9")
    c = pw.add("https://three.example", "me", "password1")
    d = pw.add("https://four.example", "me", "kQ7-mPz2-wXv9-rT4")
    e = pw.add("https://five.example", "me", "alllowercaseletters")  # (one kind, but long enough)
    found = pw.health(pw.logins())
    assert found[a["id"]] == ["reused"] and found[b["id"]] == ["reused"]
    assert found[c["id"]] == ["weak"] and d["id"] not in found and e["id"] not in found
    assert pw.weak("short1") and pw.weak("12345678901") and pw.weak("Football!") and not pw.weak("Tr0ub4dour&3x")
    pw.update(d["id"], url="https://renamed.example/login", password="new-one-entirely-7")
    assert next(x for x in pw.logins() if x["id"] == d["id"])["origin"] == "https://renamed.example"


def test_browsers_with_saved_passwords_are_found(tmp_path, monkeypatch):
    for name in ("Chrome", "Brave", "Edge"):
        (tmp_path / name / "Profile 1").mkdir(parents=True)
    (tmp_path / "Chrome" / "Profile 1" / "Login Data").write_bytes(b"")
    (tmp_path / "Edge" / "Profile 1" / "Login Data").write_bytes(b"")
    monkeypatch.setattr(pw, "CHROMIUM_BROWSERS", {name: (tmp_path / name, f"{name} Safe Storage")
                                                  for name in ("Arc", "Chrome", "Brave", "Edge")})
    assert pw.browsers_installed() == ["Chrome", "Edge"]  # (Brave's here, but has nothing saved; Arc isn't here)


def test_an_export_reads_back_the_same_and_only_you_can_open_it(tmp_path):
    import stat
    pw.import_rows(pw.parse_csv(APPLE), "Passwords")
    first = pw.logins()[0]
    pw.update(first["id"], otp=pw.otp_link(RFC_KEY), note='quotes " and, commas')
    path = tmp_path / "export.csv"
    path.write_text("old")
    path.chmod(0o644)
    pw.write_export(str(path), pw.logins())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    back = pw.parse_csv(path.read_text())
    key = lambda e: (pw._origin(e.get("url") or e["origin"]), e["username"])  # noqa: E731
    assert sorted((key(r), r["password"], r["note"], r["otp"] or "") for r in back) == sorted(
        (key(e), e["password"], e.get("note", ""), e.get("otp", "")) for e in pw.logins())


def test_the_breach_check_sends_only_hash_prefixes_and_matches_here():
    sha = lambda p: hashlib.sha1(p.encode()).hexdigest().upper()  # noqa: E731
    asked = []

    def service(prefix):
        asked.append(prefix)
        lines = [f"{'0' * 35}:0", f"{'F' * 35}:0"]  # (the padding: made-up hashes, seen 0 times)
        if prefix == sha("password1")[:5]:
            lines.append(f"{sha('password1')[5:]}:2413945")
        return "\r\n".join(lines)
    found = pw.breached(["password1", "a-strong-one-9Xq", "password1"], fetch=service)
    assert found == {"password1": 2413945}
    assert sorted(asked) == sorted({sha("password1")[:5], sha("a-strong-one-9Xq")[:5]})
    assert all(len(prefix) == 5 for prefix in asked)  # (never more of the hash, never the password)


def test_a_breached_password_is_flagged_first():
    entries = [{"id": "a", "origin": "https://a.example", "password": "Long-Unique-Pass-1"},
               {"id": "b", "origin": "https://b.example", "password": "short"}]
    assert pw.health(entries) == {"b": ["weak"]}
    found = {pw.breach_key("Long-Unique-Pass-1"): 12, pw.breach_key("short"): 3}
    assert pw.health(entries, found) == {"a": ["breached"], "b": ["breached", "weak"]}
    entries[0]["password"] = "Changed-Since-1"  # (changed after the check: not flagged any more)
    assert pw.health(entries, found) == {"b": ["breached", "weak"]}
