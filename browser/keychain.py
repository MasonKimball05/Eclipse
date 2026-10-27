# Secrets for your apps (API tokens for Live Folders and My Apps), in the macOS Keychain: through
# secure_store.py, so only Eclipse can read them (anything else asking gets macOS's prompt).
#
# Older versions kept them with the `security` command, whose items any program can read without asking.
# One of those is moved over the first time it's read: copied into Eclipse's own item, and the old one removed.
# (The old ones are only ever read and removed here, never made; a secret never goes on a command line.)

import subprocess
import sys

from . import secure_store

SERVICE = "PythonBrowser"  # (the old items' service: the internal name, so renaming the app never loses them)


def available() -> bool:
    return secure_store.available()  # (macOS's Keychain, or Windows Credential Manager)


def store(account: str, secret: str) -> bool:
    if not available() or not secret or "\n" in secret or "\r" in secret:
        return False
    if not secure_store.store(account, secret.encode()):
        return False
    _remove_old(account)  # (a newer secret replaces an old item too)
    return load(account) == secret


def load(account: str) -> str | None:
    if not available():
        return None
    found = secure_store.load(account)
    if found is not None:
        return found.decode(errors="replace")
    old = _old(account)
    if old is not None and secure_store.store(account, old.encode()):
        _remove_old(account)  # (moved: only Eclipse can read it now)
    return old


def delete(account: str):
    if available():
        secure_store.delete(account)
        _remove_old(account)


def _old(account: str) -> str | None:
    """An item made by an older version, with the `security` command."""
    if sys.platform != "darwin":
        return None
    try:
        done = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.rstrip("\n") if done.returncode == 0 else None


def _remove_old(account: str):
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass
