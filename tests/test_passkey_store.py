"""Passkeys Eclipse keeps: what a site gets back checks out as a server would check it, and the recovery copy
brings them back with the PIN (never your real Keychain: a stand-in)."""

import hashlib
import json
import struct

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PyQt6.QtCore import QUrl

from browser import passkey_store as ps
from browser import recovery


@pytest.fixture(autouse=True)
def keychain(monkeypatch, data_dir):
    kept = {}
    monkeypatch.setattr(ps.secure_store, "store", lambda account, secret: kept.__setitem__(account, secret) or True)
    monkeypatch.setattr(ps.secure_store, "load", lambda account: kept.get(account))
    monkeypatch.setattr(ps.secure_store, "delete", lambda account: kept.pop(account, None))
    monkeypatch.setattr(recovery, "SCRYPT", {"n": 2 ** 10, "r": 8, "p": 1})  # (quick, for tests)
    return kept


def _new(rp="example.com", user="dXNlcjE", name="arkan@example.com", verified=True):
    return ps.create(rp, "Example", user, name, "Arkan", "Y2hhbGxlbmdl", "https://" + rp, verified)


def test_cbor_matches_the_spec():
    assert ps.cbor({"fmt": "none", "attStmt": {}}) == bytes.fromhex("a263666d74646e6f6e656761747453746d74a0")
    assert ps.cbor(-7) == b"\x26" and ps.cbor(24) == b"\x18\x18" and ps.cbor(b"\x01") == b"\x41\x01"


def test_a_new_passkey_is_what_a_server_expects():
    made = _new()
    auth = ps.unb64url(made["authenticatorData"])
    assert auth[:32] == hashlib.sha256(b"example.com").digest()
    assert auth[32] & (ps.UP | ps.UV | ps.AT | ps.BE) == ps.UP | ps.UV | ps.AT | ps.BE
    assert not auth[32] & ps.BS  # (no recovery PIN yet: not backed up)
    length = struct.unpack(">H", auth[53:55])[0]
    assert auth[55:55 + length] == ps.unb64url(made["id"])
    assert ps.unb64url(made["attestationObject"]).startswith(bytes.fromhex("a363666d74646e6f6e65"))
    client = json.loads(ps.unb64url(made["clientDataJSON"]))
    assert client == {"type": "webauthn.create", "challenge": "Y2hhbGxlbmdl", "origin": "https://example.com",
                      "crossOrigin": False}
    assert [p["user_name"] for p in ps.for_site("example.com")] == ["arkan@example.com"]


def test_signing_in_verifies_with_the_public_key_the_site_got():
    made = _new()
    public = serialization.load_der_public_key(ps.unb64url(made["publicKey"]))
    signed = ps.sign_in(made["id"], "c2lnbiBpbg", "https://example.com", verified=False)
    auth, data = ps.unb64url(signed["authenticatorData"]), ps.unb64url(signed["clientDataJSON"])
    public.verify(ps.unb64url(signed["signature"]), auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
    assert auth[32] & ps.UV == 0 and json.loads(data)["type"] == "webauthn.get"
    assert signed["userHandle"] == "dXNlcjE"
    assert ps.for_site("example.com")[0]["last_used"]


def test_one_passkey_per_account_and_deleting_one():
    first = _new()
    second = _new()  # (the same account again: it replaces the first)
    _new(user="b3RoZXI", name="other@example.com")
    site = ps.for_site("example.com")
    assert len(site) == 2 and second["id"] in {p["id"] for p in site}
    assert {p["user_name"] for p in site} == {"arkan@example.com", "other@example.com"}
    assert first["id"] not in {p["id"] for p in ps.all_passkeys()}
    assert ps.for_site("example.com", allowed=[second["id"]])[0]["id"] == second["id"]
    ps.delete(second["id"])
    assert [p["user_name"] for p in ps.for_site("example.com")] == ["other@example.com"]


def test_which_sites_may_use_which_passkeys():
    assert ps.rp_id_allowed("example.com", "example.com")
    assert ps.rp_id_allowed("example.com", "accounts.example.com")
    assert not ps.rp_id_allowed("example.com", "badexample.com")
    assert not ps.rp_id_allowed("other.com", "example.com")
    assert not ps.rp_id_allowed("co.uk", "shop.co.uk") and not ps.rp_id_allowed("com", "example.com")
    assert not ps.rp_id_allowed("github.io", "someone.github.io")
    assert ps.secure(QUrl("https://a.com")) and ps.secure(QUrl("http://localhost:8000"))
    assert not ps.secure(QUrl("http://a.com"))
    assert ps.origin_of(QUrl("https://a.com:443/x")) == "https://a.com"
    assert ps.origin_of(QUrl("http://localhost:8000/")) == "http://localhost:8000"


def test_the_recovery_pin_brings_passkeys_back(keychain):
    assert recovery.pin_problem("1234567") and not recovery.pin_problem("correct horse")
    before = _new()
    assert recovery.set_pin("correct horse") and recovery.is_set()
    after = _new(user="b3RoZXI", name="other@example.com")  # (joins the copy without asking for the PIN)
    assert ps.unb64url(after["authenticatorData"])[32] & ps.BS
    # Another Mac: the list and the copy came over in a backup, the Keychain didn't
    keychain.clear()
    assert {p["id"] for p in ps.missing()} == {before["id"], after["id"]}
    with pytest.raises(OSError):
        ps.sign_in(before["id"], "eA", "https://example.com", verified=True)
    assert recovery.unlock("wrong horse") is None
    assert recovery.unlock("correct horse") == 2 and not ps.missing()
    assert ps.sign_in(after["id"], "eA", "https://example.com", verified=True)["signature"]


def test_a_new_pin_keeps_every_passkey():
    made = _new()
    recovery.set_pin("first pin 1")
    recovery.set_pin("second pin 2")
    ps.secure_store.delete(ps.KEY_ACCOUNT.format(made["id"]))
    assert recovery.unlock("first pin 1") is None and recovery.unlock("second pin 2") == 1
