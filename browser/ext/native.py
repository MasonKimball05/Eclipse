# Native messaging: an extension talking to a helper program on this Mac (a password manager's
# desktop app, Claude Code, …), exactly as Chrome does it.
#
# Helpers register themselves with a small JSON manifest in a NativeMessagingHosts folder: their
# name, the program to run, and which extensions may use them (allowed_origins). We read the same
# folders Chrome, Chromium, Arc, Edge and Brave use, plus our own. The program is started with the
# extension's origin as its argument, and messages go back and forth over stdin/stdout as JSON,
# each preceded by its length (4 bytes, little-endian).
#
# macOS refuses to start some of Apple's own helpers for most apps: Apple's Passwords helper (which
# iCloud Passwords needs) is killed on launch with "Launch Constraint Violation" unless the browser
# passes Apple's signing rules. Tested 23 Oct 2026: killed for PythonBrowser signed ad hoc and with an
# Apple Development certificate; Arc, a notarized Developer ID app, gets through. That's detected and
# reported as such.

import json
import re
import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from .. import storage

_SUPPORT = Path.home() / "Library" / "Application Support"
HOST_FOLDERS = [
    lambda: storage.data_dir() / "NativeMessagingHosts",  # ours
    lambda: _SUPPORT / "Google" / "Chrome" / "NativeMessagingHosts",
    lambda: Path("/Library/Google/Chrome/NativeMessagingHosts"),
    lambda: _SUPPORT / "Chromium" / "NativeMessagingHosts",
    lambda: Path("/Library/Application Support/Chromium/NativeMessagingHosts"),
    lambda: _SUPPORT / "Arc" / "User Data" / "NativeMessagingHosts",
    lambda: _SUPPORT / "Microsoft Edge" / "NativeMessagingHosts",
    lambda: _SUPPORT / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts",
]
_NAME = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)*")
MAX_FROM_HOST = 1024 * 1024  # Chrome's limit on one message from the helper
EARLY_DEATH_MS = 1500        # killed this soon after starting: macOS refused to run it

NOT_FOUND = "Specified native messaging host not found."
FORBIDDEN = "Access to the specified native messaging host is forbidden."
EXITED = "Native host has exited."
BROKEN = "Error when communicating with the native messaging host."
REFUSED_BY_MACOS = ("macOS refused to start this helper (\"Launch Constraint Violation\"): it only runs "
                    "for browsers that pass Apple's signing rules, and PythonBrowser doesn't.")


def find_host(name: str) -> dict | None:
    """The manifest registered for a helper name, from the first folder that has one."""
    if not _NAME.fullmatch(name or ""):
        return None
    for folder in HOST_FOLDERS:
        file = folder() / f"{name}.json"
        try:
            manifest = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("name") == name and manifest.get("type", "stdio") == "stdio" and manifest.get("path"):
            path = Path(manifest["path"])
            manifest["path"] = str(path if path.is_absolute() else file.parent / path)
            return manifest
    return None


def check_access(name: str, ext_id: str) -> tuple[dict | None, str | None]:
    """(manifest, None) if the extension may use this helper, else (None, Chrome's error message)."""
    manifest = find_host(name)
    if manifest is None:
        return None, NOT_FOUND
    if f"chrome-extension://{ext_id}/" not in manifest.get("allowed_origins", []):
        return None, FORBIDDEN
    return manifest, None


def encode(message) -> bytes:
    data = json.dumps(message, separators=(",", ":")).encode()
    return struct.pack("<I", len(data)) + data


def decode(buffer: bytearray) -> tuple[list, bytearray]:
    """Whole messages at the front of the buffer, and whatever's left over."""
    messages = []
    while len(buffer) >= 4:
        size = struct.unpack("<I", bytes(buffer[:4]))[0]
        if size > MAX_FROM_HOST:
            raise ValueError("message from the helper is too large")
        if len(buffer) < 4 + size:
            break
        messages.append(json.loads(bytes(buffer[4:4 + size]).decode("utf-8")))
        buffer = buffer[4 + size:]
    return messages, buffer


class NativePort(QObject):
    """One running helper program."""
    message = pyqtSignal(object)
    closed = pyqtSignal(str)  # "" for a normal end, else the error message

    def __init__(self, manifest, ext_id, parent=None):
        super().__init__(parent)
        self.label = f"{manifest.get('name')} (for {ext_id})"
        self._buffer = bytearray()
        self._done = False
        self._started_ok = False
        self.process = QProcess(self)
        self.process.setProgram(manifest["path"])
        self.process.setArguments([f"chrome-extension://{ext_id}/"])
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(lambda: self.process.readAllStandardError())  # don't let it fill up
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.process.start()
        QTimer.singleShot(EARLY_DEATH_MS, self._survived)

    def _survived(self):
        self._started_ok = True

    def send(self, message):
        if not self._done and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.write(encode(message))

    def _read(self):
        self._buffer.extend(bytes(self.process.readAllStandardOutput()))
        try:
            messages, self._buffer = decode(self._buffer)
        except (ValueError, UnicodeDecodeError):
            self._end(BROKEN)
            return
        for message in messages:
            self.message.emit(message)

    def _finished(self, code, status):
        killed = status == QProcess.ExitStatus.CrashExit and not self._started_ok
        if not self._done:  # it ended on its own: worth a line in the terminal when debugging
            print(f"[extensions] native helper {self.label} exited (code {code}, "
                  f"{'crashed/killed' if status == QProcess.ExitStatus.CrashExit else 'normal'})", file=sys.stderr)
        self._end(REFUSED_BY_MACOS if killed and self.process.program().startswith("/System/") else EXITED)

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._end(NOT_FOUND)

    def _end(self, reason):
        if self._done:
            return
        self._done = True
        self.closed.emit(reason)

    def stop(self):
        self._done = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.closeWriteChannel()
            self.process.kill()
            self.process.waitForFinished(500)
