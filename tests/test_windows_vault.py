"""Windows Credential Manager, where passwords' and passkeys' keys are kept on Windows (runs on Windows only)."""

import sys

import pytest

from browser import secure_store, windows_vault

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")


def test_keep_replace_and_remove_a_secret():
    account = "eclipse-test-vault"
    try:
        assert windows_vault.available() and secure_store.available()
        assert secure_store.store(account, b"\x00first\xff") and secure_store.load(account) == b"\x00first\xff"
        assert secure_store.store(account, b"second") and secure_store.load(account) == b"second"
        assert secure_store.load("eclipse-test-nothing") is None
    finally:
        assert secure_store.delete(account)
    assert secure_store.load(account) is None and secure_store.delete(account)  # (already gone: fine)
    assert not windows_vault.store(account, b"x" * (windows_vault.MAX_BLOB + 1))
