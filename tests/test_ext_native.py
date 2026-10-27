import json
import stat
import sys
from pathlib import Path

from PyQt6.QtCore import QEventLoop, QTimer

from browser.ext import native

ECHO = Path(__file__).parent / "fixtures" / "native_echo.py"


def register(data_dir, name="eclipse.echo", origins=("chrome-extension://demo/",)):
    """Register the echo helper in our own NativeMessagingHosts folder (the throwaway data dir)."""
    folder = data_dir / "NativeMessagingHosts"
    folder.mkdir(parents=True, exist_ok=True)
    launcher = folder / "echo.sh"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ECHO}" "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    (folder / f"{name}.json").write_text(json.dumps(
        {"name": name, "description": "test", "path": "echo.sh", "type": "stdio", "allowed_origins": list(origins)}))
    return launcher


def test_wire_format():
    data = native.encode({"a": 1}) + native.encode("two")
    messages, rest = native.decode(bytearray(data[:-1]))
    assert messages == [{"a": 1}] and len(rest) == len(native.encode("two")) - 1
    messages, rest = native.decode(bytearray(data))
    assert messages == [{"a": 1}, "two"] and not rest


def test_finding_and_allowing(data_dir):
    launcher = register(data_dir)
    manifest = native.find_host("eclipse.echo")
    assert manifest["path"] == str(launcher)  # relative paths resolve next to the manifest
    assert native.check_access("eclipse.echo", "demo")[1] is None
    assert native.check_access("eclipse.echo", "someone-else") == (None, native.FORBIDDEN)
    assert native.check_access("no.such.host", "demo") == (None, native.NOT_FOUND)
    assert native.find_host("../../etc/passwd") is None  # names can't be paths


def test_round_trip_with_a_real_helper(data_dir):
    register(data_dir)
    port = native.NativePort(native.find_host("eclipse.echo"), "demo")
    got, closed = [], []
    loop = QEventLoop()
    port.message.connect(lambda m: (got.append(m), port.send("quit")))
    port.closed.connect(lambda reason: (closed.append(reason), loop.quit()))
    port.send({"hello": "helper"})
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert got == [{"echo": {"hello": "helper"}, "origin": "chrome-extension://demo/"}]
    assert closed == [native.EXITED]
