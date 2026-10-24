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
def bad_https(site, tmp_path_factory):
    """https://127.0.0.1:<port>/ with a self-signed certificate: the same pages, an untrusted connection."""
    folder = tmp_path_factory.mktemp("cert")
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
                    "/CN=pythonbrowser-test", "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
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
    w.resize(1100, 760)
    w.show()
    w.toasts = []
    w.toast = lambda text: w.toasts.append(text)
    wait_until(lambda: False, 0.3)
    yield w
    w.shutdown()
    w.close()
    w.deleteLater()
    pause(0.2)

