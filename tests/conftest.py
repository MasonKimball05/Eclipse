import os
import sys
from pathlib import Path

import pytest

# Headless: no windows pop up while testing
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (must load before the QApplication)
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


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
