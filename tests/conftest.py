import os
import sys
import tempfile
from pathlib import Path

import pytest

# Spell-check dictionaries: somewhere throwaway, set before Qt WebEngine starts (see browser/spelling.py)
os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = tempfile.mkdtemp(prefix="pythonbrowser-dictionaries-")
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
        from browser.ext.chrome import register_scheme
        register_scheme()  # pbext:// must be known before the app exists, as in the real app
    # (Qt WebEngine needs a program name to start Chromium: never an empty argv)
    return QApplication.instance() or QApplication(["pytest"])


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    # Every test gets its own empty data folder, never your real session
    monkeypatch.setenv("PYBROWSER_DATA_DIR", str(tmp_path / "data"))
    from browser import storage
    storage._icons.clear()
    return tmp_path / "data"


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
