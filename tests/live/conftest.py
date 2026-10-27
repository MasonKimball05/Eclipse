# Live tests drive the real browser window (it shows while they run) against pages served from
# tests/live/site.
# Run them with:  ./run.sh --live-tests   (or: python -m pytest tests/live --live)

import functools
import http.server
import math
import ssl
import subprocess
import struct
import threading
import wave
from pathlib import Path

import pytest

from live_helpers import pause, wait_until  # noqa: F401

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
    with wave.open(str(root / "tone.wav"), "wb") as tone:  # three seconds of 440 Hz, looped by the page
        tone.setnchannels(1)
        tone.setsampwidth(2)
        tone.setframerate(8000)
        tone.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(i * 2 * math.pi * 440 / 8000)))
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
    w = MainWindow(store, build_profile(qapp))
    if w.feature("github") is not None:
        w.feature("github").repos.fetched = float("inf")  # never ask GitHub (gh) for your repos from a test
    w.favicons.want = lambda urls: None  # nor fetch site icons from the internet
    w.resize(1100, 760)
    w.show()
    _keep_out_of_the_way(w)
    w.toasts = []
    w.real_toast = w.toast
    w.toast = lambda text, action=None, label="Undo", seconds=None: (w.toasts.append(text),
                                                                     setattr(w, "toast_action", action))
    wait_until(lambda: False, 0.3)
    yield w
    w.shutdown()
    w.close()
    w.deleteLater()
    pause(0.2)

