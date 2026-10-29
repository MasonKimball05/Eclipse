# Live tests drive the real browser window (it shows while they run) against pages served from
# tests/live/site.
# Run them with:  ./run.sh --live-tests   (or: python -m pytest tests/live --live)

import functools
import http.server
import math
import os
import ssl
import subprocess
import struct
import threading
import wave
from pathlib import Path

import pytest

from live_helpers import pause, wait_until  # noqa: F401

TONE_LEVEL = 20  # (of 32,767: the test tone's loudness, see site())
MEMORY_CAP_GB = float(os.environ.get("ECLIPSE_TEST_MEMORY_GB", 4))  # the run stops if the tests use more than this

SITE = Path(__file__).parent / "site"
_site_root = [""]  # where `site` copied the pages


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _keep_out_of_the_way(widget=None):
    """The live tests mustn't take over the screen while you work: the test app can't become the
    active app (no Dock icon, no focus taken), and its windows go behind everything else."""
    import ctypes
    from browser import macos
    if not macos._cocoa():
        return
    app = macos._send(macos._objc.objc_getClass(b"NSApplication"), "sharedApplication")
    macos._send(app, "setActivationPolicy:", 2, restype=ctypes.c_bool, argtypes=(ctypes.c_long,))  # prohibited
    if widget is not None:
        try:
            if not widget.isWindow() or widget.windowHandle() is None:
                return
        except RuntimeError:
            return  # (closed again already)
        window = macos._send(ctypes.c_void_p(int(widget.winId())), "window")
        if window:
            macos._send(window, "orderBack:", None, restype=None, argtypes=(ctypes.c_void_p,))


def _memory_gb() -> float:
    """How much memory this process (the tests and their browser window) holds now."""
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) / 2 ** 20
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def pytest_runtest_logfinish(nodeid, location):
    # A cap, so a run can never take the whole Mac with it (14 GB once, with other apps open): over it, the run
    # stops, saying which test it got to. ECLIPSE_TEST_MEMORY_GB=8 (say) raises it
    used = _memory_gb()
    if used > MEMORY_CAP_GB:
        pytest.exit(f"The live tests stopped: they were using {used:.1f} GB, over the {MEMORY_CAP_GB:g} GB cap, "
                    f"after {nodeid}", returncode=3)


_slot_errors = []  # errors raised in Qt's callbacks (timers, signals) during the test running now


def _slot_error(kind, value, trace):
    """sys.excepthook for errors in Qt's callbacks. Without one, PyQt aborts the whole run on the first."""
    if kind is RuntimeError and "has been deleted" in str(value):
        return  # (a timer of a window a finished test closed, firing late: that window's gone, as it should be)
    import traceback
    traceback.print_exception(kind, value, trace)
    _slot_errors.append(f"{kind.__name__}: {value}")


@pytest.fixture(scope="session", autouse=True)
def slot_errors_fail_their_test():
    import sys
    sys.excepthook = _slot_error
    yield
    sys.excepthook = sys.__excepthook__


@pytest.fixture(autouse=True)
def no_slot_errors():
    _slot_errors.clear()
    yield
    if _slot_errors:
        pytest.fail("An error in a Qt callback during this test: " + "; ".join(_slot_errors))


@pytest.fixture(scope="session", autouse=True)
def in_the_background(qapp):
    from PyQt6.QtCore import QEvent, QObject, QTimer

    class Behind(QObject):  # every window the tests open (private windows, Settings…) goes to the back too
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Show and getattr(obj, "isWindow", lambda: False)() and obj.isWindow():
                QTimer.singleShot(0, lambda w=obj: _keep_out_of_the_way(w))
            return False
    _keep_out_of_the_way()
    behind = Behind()
    qapp.installEventFilter(behind)
    yield
    qapp.removeEventFilter(behind)


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    """http://127.0.0.1:<port>/ serving tests/live/site (plus a generated sound for Now Playing)."""
    root = tmp_path_factory.mktemp("site")
    for path in SITE.iterdir():
        (root / path.name).write_bytes(path.read_bytes())
    # Three seconds of 440 Hz, looped by the page, so quiet you can't hear it (about -64 dB) but loud enough that
    # Chromium counts the tab as playing sound (it calls anything above about -72 dB audible)
    with wave.open(str(root / "tone.wav"), "wb") as tone:
        tone.setnchannels(1)
        tone.setsampwidth(2)
        tone.setframerate(8000)
        tone.writeframes(b"".join(struct.pack("<h", int(TONE_LEVEL * math.sin(i * 2 * math.pi * 440 / 8000)))
                                  for i in range(8000 * 3)))
    _site_root[:] = [str(root)]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="session")
def site_root(site):
    """The folder `site` serves, for a test that adds a file to it."""
    return _site_root[0]


@pytest.fixture(scope="session")
def bad_https(site, tmp_path_factory):
    """https://127.0.0.1:<port>/ with a self-signed certificate: the same pages, an untrusted connection."""
    folder = tmp_path_factory.mktemp("cert")
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
                    "/CN=eclipse-test", "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=_site_root[0]))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def window(data_dir, qapp):
    """A real MainWindow on a throwaway profile, closed again after the test."""
    from browser import storage
    from browser.models import Store
    from browser.webview import build_profile
    from browser.window import MainWindow
    store = Store(storage.session_path())
    store.is_new = False  # no first-launch Arc import offer
    store.settings["handoff"] = False  # nor test pages offered to your iPhone (Handoff)
    store.settings["power_saver"] = False  # nor your Mac's Low Power Mode changing how the tests run
    store.settings["memory_saver"] = False  # nor its memory running short (tabs would sleep mid-test)
    store.settings["up_next"] = False  # nor your calendar (a meeting coming up would show its card mid-test)
    store.settings["prepare_spaces"] = False  # nor pictures of the next Spaces made while idle (they switch Spaces, unseen)
    w = MainWindow(store, build_profile(qapp))
    if w.feature("github") is not None:
        w.feature("github").repos.fetched = float("inf")  # never ask GitHub (gh) for your repos from a test
    w.favicons.want = lambda urls: None  # nor fetch site icons from the internet
    w.resize(1100, 760)
    w.show()
    _keep_out_of_the_way(w)
    w.toasts = []
    w.real_toast = w.toast
    w.toast = lambda text, action=None, label="Undo": (w.toasts.append(text), setattr(w, "toast_action", action))
    wait_until(lambda: False, 0.3)
    yield w
    w.shutdown()
    w.close()
    w.deleteLater()
    pause(0.2)
    # Gone now, with its pages and pictures: a deleteLater only happens inside a running event loop, which the
    # tests never start, so every test's window was kept until the end of the run
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    import gc
    gc.collect()

