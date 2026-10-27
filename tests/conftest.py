import os
import sys
import tempfile
from pathlib import Path

import pytest

# Spell-check dictionaries: somewhere throwaway, set before Qt WebEngine starts (see browser/spelling.py)
os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = tempfile.mkdtemp(prefix="eclipse-dictionaries-")
# Headless: no windows pop up while testing. Not for the live tests: Qt Quick's software renderer
# (what "offscreen" uses) crashes when a web view is destroyed, which the real app never sees
if "--live" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (must load before the QApplication)
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    if QApplication.instance() is None:
        from browser import features
        features.before_app()  # (pbext://, for extension files, must be known before the app exists, as in the app)
    # (Qt WebEngine needs a program name to start Chromium: never an empty argv)
    return QApplication.instance() or QApplication(["pytest"])


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    # Every test gets its own empty data folder, never your real session
    monkeypatch.setenv("ECLIPSE_DATA_DIR", str(tmp_path / "data"))
    from browser import storage
    storage._icons.clear()
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def no_real_keychain(monkeypatch):
    """Secrets go to a dict in every test, never your login Keychain (a test that passes its own throwaway
    keychain file still reaches the real thing, in that file)."""
    from browser import secure_store
    kept = {}
    real = (secure_store.store, secure_store.load, secure_store.delete)
    monkeypatch.setattr(secure_store, "store", lambda account, secret, keychain=None: real[0](account, secret, keychain)
                        if keychain is not None else (bool(secret) and kept.__setitem__(account, secret) is None))
    monkeypatch.setattr(secure_store, "load", lambda account, keychain=None: real[1](account, keychain)
                        if keychain is not None else kept.get(account))
    monkeypatch.setattr(secure_store, "delete", lambda account, keychain=None: real[2](account, keychain)
                        if keychain is not None else (kept.pop(account, None), True)[1])
    return kept


@pytest.fixture
def store(data_dir):
    from browser import storage
    from browser.models import Store
    return Store(storage.session_path())


# --- Live tests (tests/live): the real window, real pages from a local server. Slower, so only with
# `pytest --live` (or ./run.sh --live-tests); plain `pytest` skips them.

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="also run the live tests in tests/live")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live test: run with --live")
    for item in items:
        if "tests/live/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)
