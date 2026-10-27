"""Does Eclipse start here, and load a page? (For checking a platform there's no Mac to try it on: Windows, in
GitHub Actions. It works anywhere.)

    python tools/smoke.py

Offscreen (no windows) and with a throwaway profile, it imports every module, opens the real browser window,
loads a page in a tab, keeps a secret in this system's own vault (Windows Credential Manager; on a Mac the
Keychain has its own tests, on a throwaway keychain file) and gets it back, then quits. It says what it did, and exits non-zero at the first thing that doesn't work.
"""

import importlib
import os
import pkgutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ECLIPSE_DATA_DIR"] = tempfile.mkdtemp(prefix="eclipse-smoke-")


def step(text):
    print(f"✓ {text}", flush=True)


def main():
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 (Qt WebEngine first, as the app does)
    from PyQt6.QtWidgets import QApplication

    import browser
    from browser import brand, features, spelling, storage
    QApplication.setApplicationName(brand.INTERNAL_NAME)
    QApplication.setOrganizationName(brand.ORGANIZATION)
    features.before_app()
    spelling.prepare()
    app = QApplication(["eclipse-smoke"])
    for found in pkgutil.walk_packages(browser.__path__, "browser."):
        importlib.import_module(found.name)
    step(f"every module imports ({sys.platform}, Python {sys.version.split()[0]})")

    from browser import secure_store
    if sys.platform == "darwin":  # (never your real Keychain: tests/test_secure_store.py checks it on a keychain file)
        print("- the Keychain is checked by its own tests", flush=True)
    elif secure_store.available():
        account = "smoke-test-key"
        assert secure_store.store(account, b"\x00secret\xff"), "couldn't keep a secret"
        assert secure_store.load(account) == b"\x00secret\xff", "the secret didn't come back"
        assert secure_store.delete(account) and secure_store.load(account) is None, "the secret wasn't removed"
        step("the system vault keeps a secret and gives it back")
    else:
        print("- no system vault here: passwords can't be saved", flush=True)

    from browser.models import Store
    from browser.webview import build_profile
    from browser.window import MainWindow
    store = Store(storage.session_path())
    store.is_new = False
    window = MainWindow(store, build_profile(app))
    window.show()
    app.processEvents()
    step(f"the window opens, with {len(window.features)} features")

    loaded = []
    tab = window.open_url("data:text/html,<title>Smoke</title><p>Eclipse</p>")
    tab.view.loadFinished.connect(lambda ok: (loaded.append(ok), app.quit()))
    QTimer.singleShot(60_000, app.quit)
    app.exec()
    assert loaded == [True], "the page didn't load"
    assert tab.view.title() == "Smoke", f"the page's title is {tab.view.title()!r}"
    step("a page loads in a tab")
    window.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
