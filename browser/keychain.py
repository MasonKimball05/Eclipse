# Secrets (API tokens for your own apps) live in the macOS Keychain, not in the session file or
# backups: `security` stores them as generic passwords under the service "PythonBrowser".
#
# The secret goes to `security` on its stdin (its interactive mode), never as an argument, so it
# can't be seen in the process list or end up in an error message.

import subprocess
import sys

SERVICE = "PythonBrowser"  # (the internal name: renaming the app mustn't lose your tokens)


def available() -> bool:
    return sys.platform == "darwin"


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def store(account: str, secret: str) -> bool:
    if not available() or not secret or "\n" in secret or "\r" in secret:
        return False
    command = f"add-generic-password -U -s {_quoted(SERVICE)} -a {_quoted(account)} -w {_quoted(secret)}\n"
    try:
        done = subprocess.run(["security", "-i"], input=command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False  # (never log it: the input holds the secret)
    return done.returncode == 0 and load(account) == secret


def load(account: str) -> str | None:
    if not available():
        return None
    try:
        done = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.rstrip("\n") if done.returncode == 0 else None


def delete(account: str):
    if available():
        try:
            subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", account],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
