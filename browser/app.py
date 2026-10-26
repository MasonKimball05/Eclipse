import hashlib
import sys
from pathlib import Path

# QtWebEngine must be imported before the QApplication is created
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtCore import QEvent, QObject, QTimer, QUrl
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication

from . import backup, brand, crash, macos, spelling, storage, whats_new
from .models import Store
from .ext.chrome import register_scheme
from .webview import build_profile
from .window import MainWindow

LINK_SCHEMES = ("http://", "https://", "file://")
# Local files worth opening as a page (Finder's "Open With"); anything else is ignored, since
# macOS also turns plain command-line arguments (like a folder path) into "open" events
OPENABLE_FILES = {".html", ".htm", ".xhtml", ".pdf", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt"}


def _server_name() -> str:
    # One running browser per data folder (a throwaway --fresh profile gets its own)
    return "pythonbrowser-" + hashlib.sha1(str(storage.data_dir()).encode()).hexdigest()[:12]


def _links(args) -> list[str]:
    return [a for a in args if a.startswith(LINK_SCHEMES)]


def _hand_off(links) -> bool:
    """If a browser is already running, give it our links (or just wake it) and step aside."""
    socket = QLocalSocket()
    socket.connectToServer(_server_name())
    if not socket.waitForConnected(300):
        return False
    socket.write(("\n".join(links) + "\n").encode())
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    return True


class LinkReceiver(QObject):
    """Links arriving from outside: other apps via macOS (once this is the default browser),
    Finder's "Open With", and second launches handing over their links."""

    def __init__(self, window, server):
        super().__init__(window)
        self.window = window
        self.server = server
        server.newConnection.connect(self._accept)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FileOpen:
            url = event.url()
            if url.isEmpty() and event.file():
                url = QUrl.fromLocalFile(event.file())
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() not in OPENABLE_FILES:
                return True
            self.window.open_external(url.toString())
            return True
        return False

    def _accept(self):
        socket = self.server.nextPendingConnection()
        buffer = bytearray()
        socket.readyRead.connect(lambda: buffer.extend(bytes(socket.readAll())))

        def done():
            buffer.extend(bytes(socket.readAll()))
            links = _links(buffer.decode(errors="replace").splitlines())
            for link in links:
                self.window.open_external(link)
            if not links:
                self.window.bring_to_front()
            socket.deleteLater()
        socket.disconnected.connect(done)


def run() -> int:
    # PyQt aborts the whole app when an exception escapes a Qt callback and no hook is set; one
    # bad callback (a closed popup, an odd web page) shouldn't take every tab down with it
    sys.excepthook = crash.log_exception
    QApplication.setApplicationName(brand.INTERNAL_NAME)  # where your data lives: never renamed
    QApplication.setApplicationDisplayName(brand.NAME)
    QApplication.setOrganizationName(brand.ORGANIZATION)
    register_scheme()  # pbext:// (extension files) has to be known before the app starts
    spelling.prepare()  # where spell-check dictionaries live, also before the app starts
    macos.name_in_menu_bar(brand.NAME)
    app = QApplication(sys.argv)

    links = _links(sys.argv[1:])
    if _hand_off(links):
        return 0
    QLocalServer.removeServer(_server_name())  # left behind if the last run crashed
    server = QLocalServer(app)
    server.listen(_server_name())

    restored = backup.apply_pending()  # a backup chosen last time, swapped in before anything loads
    crashed = crash.start()  # did the last run quit properly? (and catch hard crashes from here on)
    profile = build_profile(app)
    store = Store(storage.session_path())
    if not macos.is_built_app() or brand.chosen_look(store.settings) != brand.LOOK:
        brand.apply_icon(app, store.settings)  # (the built app has the default in its bundle already)
    window = MainWindow(store, profile)
    app.installEventFilter(LinkReceiver(window, server))
    window.show()
    if restored is not None:
        window.toast(f"Restored your backup · what you had before is in {restored.name}")
    elif crashed:
        window.toast(f"{brand.NAME} didn't quit properly last time. Your tabs are back "
                     "(⌘T → Show Crash Logs for details).")
    for link in links:
        window.open_external(link)
    # After an update with something new, a short tour of it (once)
    QTimer.singleShot(1500, lambda: setattr(window, "_whats_new", whats_new.show_if_new(window)))

    code = app.exec()
    window.shutdown()
    crash.clean_exit()
    from .self_update import precompile
    precompile()  # (what changed while it ran, pulled from GitHub say: so the next launch is quick)
    return code
