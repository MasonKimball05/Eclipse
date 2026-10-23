import sys

# QtWebEngine must be imported before the QApplication is created
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QApplication

from . import storage
from .models import Store
from .webview import build_profile
from .window import MainWindow


def run() -> int:
    QApplication.setApplicationName("PythonBrowser")
    QApplication.setOrganizationName("MasonKimball05")
    app = QApplication(sys.argv)

    profile = build_profile(app)
    store = Store(storage.session_path())
    window = MainWindow(store, profile)
    window.show()

    code = app.exec()
    window.shutdown()
    return code
