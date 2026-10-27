"""Secrets only Eclipse can read: against a throwaway keychain file, never your login keychain."""

import subprocess

import pytest

from browser import secure_store


@pytest.fixture
def keychain(tmp_path):
    path = str(tmp_path / "test.keychain-db")
    subprocess.run(["security", "create-keychain", "-p", "test", path], check=True, capture_output=True)
    subprocess.run(["security", "unlock-keychain", "-p", "test", path], check=True, capture_output=True)
    yield secure_store.open_keychain(path), path
    subprocess.run(["security", "delete-keychain", path], capture_output=True)


def test_store_load_replace_and_delete(keychain):
    ref, _path = keychain
    assert secure_store.load("key", ref) is None
    assert secure_store.store("key", b"\x00first\xff", ref) and secure_store.load("key", ref) == b"\x00first\xff"
    assert secure_store.store("key", b"second", ref) and secure_store.load("key", ref) == b"second"
    assert secure_store.load("other", ref) is None
    assert secure_store.delete("key", ref) and secure_store.load("key", ref) is None
    assert secure_store.delete("key", ref)  # (already gone: fine)
    assert not secure_store.store("key", b"", ref)


def _trusted(dump, service):
    """The apps an item trusts, from `security dump-keychain -a` (one block per item)."""
    block = next(b for b in dump.split("keychain: ") if f'"svce"<blob>="{service}"' in b)
    return [line.split(":", 1)[1].strip() for line in block.splitlines() if line.strip()[:3] in ("0: ", "1: ")]


def test_only_eclipse_is_trusted_with_it_not_the_security_command(keychain):
    ref, path = keychain
    secure_store.store("key", b"secret", ref)
    subprocess.run(["security", "add-generic-password", "-s", "made-by-security", "-a", "x", "-w", "y", path],
                   check=True, capture_output=True)
    dump = subprocess.run(["security", "dump-keychain", "-a", path], capture_output=True, text=True).stdout
    assert any(app.startswith("/usr/bin/security") for app in _trusted(dump, "made-by-security"))
    ours = _trusted(dump, secure_store.SERVICE)
    assert ours and not any("/usr/bin/security" in app for app in ours)  # (anything else gets macOS's prompt)
