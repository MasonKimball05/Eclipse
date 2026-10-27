# Run by test_live_core.py in a process of its own: Eclipse with none of its features (features.py), and
# every module that belongs to one made impossible to import, so nothing in the core leans on them.
import importlib.abc
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYBROWSER_DATA_DIR"] = tempfile.mkdtemp()

from browser.features import FEATURE_MODULES  # noqa: E402

# The features, and the modules only they use
PRIVATE = {f"browser.{m}" for m in FEATURE_MODULES} | {
    "browser.ext", "browser.ai", "browser.browse", "browser.translate", "browser.tidy", "browser.live_folders",
    "browser.integrations"}


class Blocked(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in PRIVATE or any(name.startswith(p + ".") for p in PRIVATE):
            raise ImportError(f"{name} is a feature's: the core mustn't need it")
        return None


sys.meta_path.insert(0, Blocked())

import PyQt6.QtWebEngineWidgets  # noqa: E402,F401
from PyQt6.QtCore import QObject, QEvent, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu  # noqa: E402

app = QApplication(sys.argv)
from conftest import _keep_out_of_the_way  # noqa: E402

_keep_out_of_the_way()


class Behind(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and getattr(obj, "isWindow", lambda: False)() and obj.isWindow():
            _keep_out_of_the_way(obj)
            QTimer.singleShot(0, lambda w=obj: _keep_out_of_the_way(w))
        return False


behind = Behind()
app.installEventFilter(behind)
QMenu.exec = lambda self, *a, **k: None  # (menus are built, never shown)
from browser import storage  # noqa: E402
from browser.models import Store  # noqa: E402
from browser.webview import build_profile  # noqa: E402
from browser.window import MainWindow  # noqa: E402

errors = []  # (anything that goes wrong in a slot, from here on)


def _caught(kind, value, tb):
    text = "".join(__import__("traceback").format_exception(kind, value, tb))
    errors.append(text)
    print(text, file=sys.stderr, flush=True)


sys.excepthook = _caught


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


store = Store(storage.session_path())
store.is_new = False
w = MainWindow(store, build_profile(app))
w.favicons.want = lambda urls: None
w.show()
_keep_out_of_the_way(w)
pump(0.5)
assert w.features == {}, w.features
w.open_url(sys.argv[1])
pump(1.5)
tab = w.current_tab
assert tab is not None and tab.view is not None
w.open_command_bar("new")
w.command_bar.input.setText("what is a test")  # (a question: no Ask rows without Claude, but searching works)
pump(0.4)
assert w.command_bar.list.count() > 0
w.command_bar.close_bar() if hasattr(w.command_bar, "close_bar") else w.command_bar.hide()
commands = w.commands()
assert commands, "no ⌘T commands"
w.open_settings()
pump(0.3)
settings = next(x for x in app.topLevelWidgets() if x.__class__.__name__ == "SettingsWindow")
for row in range(settings.nav.count()):
    settings.nav.setCurrentRow(row)
    pump(0.05)
from PyQt6.QtCore import QPoint  # noqa: E402
w.show_tab_menu(tab, QPoint(10, 10))
w.sidebar.rebuild()
pump(0.3)
w.close_tab(tab)
pump(0.3)
w.shutdown()
w.close()
pump(0.2)
if errors:
    print("ERRORS:", *errors, sep="\n", flush=True)
    sys.exit(1)
print("core ok", len(commands), "commands", flush=True)
os._exit(0)
