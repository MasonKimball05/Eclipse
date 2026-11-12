# "Little Arc": a small, quick window for a one-off page: links from other apps, a page's
# popup, or ⌥⌘N. It's deliberately minimal: a slim header tinted with your Space theme,
# the site's name, and an "Open" button. If it turns out you want to keep the page,
# Open (⌘O) moves it, still loaded, into the main window as a Today tab.

import sys

import qtawesome as qta
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from . import clean_links, macos, motion, screens, storage
from .command_bar import search_url, to_url
from .content import RADIUS, Corner
from .models import display_host
from .sidebar import IconButton, _with_alpha
from .theme import ThemedBackground, letter_icon, stylesheet

MAC = sys.platform == "darwin"


class DragArea(QWidget):
    """Empty header space moves the window, since there's no title bar."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()


class _Quitting(QObject):
    """Notes that Eclipse is quitting: the app's told so (⌘Q, the Dock's Quit, quit() after restoring a backup) just
    before every window's asked to close, and any window that won't closes nothing, the quit given up."""
    now = False

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Quit and obj is QApplication.instance():
            _Quitting.now = True
            QTimer.singleShot(0, lambda: setattr(_Quitting, "now", False))  # (and if it's given up, not any more)
        return False


def _watch_for_quitting():
    app = QApplication.instance()
    if app is not None and not getattr(app, "_eclipse_quit_watch", None):
        app._eclipse_quit_watch = _Quitting(app)
        app.installEventFilter(app._eclipse_quit_watch)


class LittleWindow(QWidget):
    def __init__(self, browser, url=None, request=None, profile=None):
        super().__init__(None, Qt.WindowType.Window)
        _watch_for_quitting()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if MAC:
            self.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
            self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self.browser = browser
        self._appeared = self._closing = False
        if not motion.reduced():
            self.setWindowOpacity(0.0)  # (it fades in as it opens: see showEvent)
        theme = browser.theme
        self.setWindowTitle("Little Window")
        self.setMinimumSize(380, 320)
        self.setStyleSheet(stylesheet(theme))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = ThemedBackground(browser)
        root.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        outer.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 0, 6, 6)
        layout.setSpacing(0)

        # Header: [traffic lights] [favicon] site name ……… [←] [⟳] [link] [Open]
        header = self.header = DragArea()
        header.setFixedHeight(40)
        row = QHBoxLayout(header)
        row.setContentsMargins(76 if MAC else 8, 0, 2, 0)
        row.setSpacing(4)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.host = QPushButton()
        self.host.setObjectName("LittleHost")
        self.host.setCursor(Qt.CursorShape.PointingHandCursor)
        self.host.clicked.connect(self.edit_url)
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("LittleUrl")
        self.url_edit.setPlaceholderText("Search or Enter URL…")
        self.url_edit.returnPressed.connect(self._go)
        self.url_edit.installEventFilter(self)
        self.url_edit.hide()
        self.back_btn = IconButton("ph.arrow-left", "Back (⌘[)", size=26, icon_size=14)
        self.reload_btn = IconButton("ph.arrow-clockwise", "Reload (⌘R)", size=26, icon_size=14)
        self.copy_btn = IconButton("ph.link", "Copy Link (⇧⌘C)", size=26, icon_size=14)
        for btn in (self.back_btn, self.reload_btn, self.copy_btn):
            btn.set_color(theme.text)
        self.open_btn = QPushButton("Open")
        self.open_btn.setObjectName("LittleOpen")
        self.open_btn.setIcon(qta.icon("ph.arrows-out-simple", color=theme.text))
        self.open_btn.setIconSize(QSize(13, 13))
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setToolTip("Open in Main Window (⌘O)")
        self.open_btn.clicked.connect(self.promote)
        row.addWidget(self.icon)
        row.addWidget(self.host, 1)
        row.addWidget(self.url_edit, 1)
        for btn in (self.back_btn, self.reload_btn, self.copy_btn):
            row.addWidget(btn)
        row.addSpacing(2)
        row.addWidget(self.open_btn)
        layout.addWidget(header)

        # The page, as a rounded card (same look as the main window)
        self.card = QFrame()
        self.card.setObjectName("LittleCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        self.view = browser.create_view(profile)  # a popup must share its opener's profile
        card_layout.addWidget(self.view)
        self.corners = [Corner(browser, w, self.card) for w in ("tl", "tr", "bl", "br")]
        self.progress = QFrame(self.card)
        self.progress.setObjectName("ProgressBar")
        self.progress.hide()
        self.toast = QLabel(self.card)
        self.toast.setObjectName("Toast")
        self.toast.hide()
        layout.addWidget(self.card, 1)

        view = self.view
        page = view.page()
        # Kept so promote() can disconnect them: once the page moves to the main window, this
        # window is gone and nothing of it may run (lambdas aren't disconnected automatically)
        self._links = [
            view.titleChanged.connect(self._on_title),
            view.urlChanged.connect(lambda _url: self._update_header()),
            view.iconChanged.connect(self._on_icon),
            view.loadProgress.connect(self._on_progress),
            view.loadFinished.connect(lambda _ok: self._on_progress(100)),
            page.windowCloseRequested.connect(self.close),
            # A video's full screen button: it goes to a tab of its own, and fills the screen from there
            page.fullScreenRequested.connect(self._full_screen),
            page.newWindowRequested.connect(lambda req: browser.handle_new_window(None, req, page.profile())),
        ]
        self.back_btn.clicked.connect(lambda: self.view and self.view.back())
        self.reload_btn.clicked.connect(lambda: self.view and self.view.reload())
        self.copy_btn.clicked.connect(self.copy_link)

        for keys, slot in (("Ctrl+O", self.promote), ("Ctrl+L", self.edit_url), ("Ctrl+W", self.close),
                           ("Ctrl+Shift+C", self.copy_link), ("Ctrl+R", view.reload), ("Ctrl+[", view.back),
                           ("Ctrl+=", lambda: self._zoom(1)), ("Ctrl+-", lambda: self._zoom(-1)),
                           ("Ctrl+0", lambda: self._zoom(0))):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)

        self._place()
        self._update_header()
        if request is not None:
            request.openIn(view.page())
        elif url:
            view.load(QUrl(url))
        else:
            QTimer.singleShot(0, self.edit_url)

    # --- Placement & chrome -------------------------------------------------------------------

    def _place(self):
        # Centered on the main window's screen, like Arc; extra ones cascade so none hides another
        screen = (self.browser.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        width, height = min(680, int(screen.width() * 0.5)), min(820, int(screen.height() * 0.82))
        others = sum(1 for w in self.browser._extra_windows if isinstance(w, LittleWindow))
        offset = 28 * (others % 6)
        self.setGeometry(screens.fit(QRect(screen.center().x() - width // 2 + offset,  # (the cascade kept on screen)
                                           screen.center().y() - height // 2 + offset, width, height), screen))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._appeared:  # Arc's little window: a quick fade in, rising a touch into place
            self._appeared = True
            home = self.pos()

            def step(t):
                self.setWindowOpacity(min(t, 1.0))
                self.move(home.x(), round(home.y() + 14 * (1 - t)))
            motion.animate(self, 0.0, 1.0, step, motion.NORMAL, easing=motion.spring())
        if MAC:
            macos.hide_title_text(self)
            lights = macos.traffic_lights(self)
            if lights:  # center the header on the traffic lights, just to their right
                right, center = lights
                self.header.setFixedHeight(max(round(center * 2), 32))
                self.header.layout().setContentsMargins(round(right + 12), 0, 2, 0)

    def closeEvent(self, event):
        # (quitting, it closes at once: kept open to fade, it called the quit off, and Eclipse stayed open)
        if self._closing or motion.reduced() or not self.isVisible() or _Quitting.now:
            super().closeEvent(event)
            return
        event.ignore()  # a quick fade out first, then closed for real
        self._closing = True
        motion.animate(self, self.windowOpacity(), 0.0, self.setWindowOpacity, motion.FAST, self.close)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._place_corners)  # once the card has its new size

    def _place_corners(self):
        w, h = self.card.width(), self.card.height()
        spots = {"tl": (0, 0), "tr": (w - RADIUS, 0), "bl": (0, h - RADIUS), "br": (w - RADIUS, h - RADIUS)}
        for corner in self.corners:
            corner.move(*spots[corner.which])
            corner.raise_()

    # --- Header -------------------------------------------------------------------------------

    def _update_header(self):
        if self.view is None:
            return
        url = self.view.url().toString()
        theme = self.browser.theme
        host = display_host(url)
        self.host.setText(host or self.view.title() or "New Page")
        self.host.setToolTip((url + "\n" if url else "") + "Click to edit (⌘L)")
        icon = storage.favicon(url) or (letter_icon(host, theme.accent) if host else
                                        qta.icon("ph.globe-simple", color=_with_alpha(theme.text, 0.6)))
        self.icon.setPixmap(icon.pixmap(16, 16))
        self.back_btn.setVisible(self.view.history().canGoBack())

    def _on_title(self, title):
        self.setWindowTitle(title or "Little Window")
        self._update_header()

    def _on_icon(self, icon):
        if self.view is not None:
            storage.save_favicon(self.view.url().toString(), icon)
        self._update_header()

    def _on_progress(self, value):
        if value >= 100:
            self.progress.hide()
            self._update_header()
            return
        self.progress.setGeometry(0, 0, max(int(self.card.width() * value / 100), 8), 2)
        self.progress.show()
        self.progress.raise_()

    def edit_url(self):
        self.host.hide()
        self.url_edit.setText(self.view.url().toString() if self.view else "")
        self.url_edit.show()
        self.url_edit.setFocus()
        self.url_edit.selectAll()

    def _end_edit(self):
        self.url_edit.hide()
        self.host.show()

    def eventFilter(self, obj, event):
        if obj is self.url_edit and getattr(self, "view", None) is not None:  # events arrive during setup too
            if event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._end_edit()
                self.view.setFocus()
                return True
            if event.type() == event.Type.FocusOut and not self.view.url().isEmpty():
                self._end_edit()
        return super().eventFilter(obj, event)

    def _go(self):
        text = self.url_edit.text().strip()
        if text and self.view is not None:
            engine = self.browser.store.settings.get("search_engine", "google")
            self.view.load(QUrl(to_url(text) or search_url(text, engine)))
            self._end_edit()
            self.view.setFocus()

    def menu_action(self, text):
        """The main menu's item `text`, chosen (by its keys, most likely) with this window in front: this window's,
        where it has one (True). (Else the main window decides: _menu_for_front.)"""
        view = self.view
        actions = {"Close Tab": self.close, "Open Location…": self.edit_url, "Copy URL": self.copy_link,
                   "Reload": lambda: view.reload(), "Back": lambda: view.back(), "Forward": lambda: view.forward(),
                   "Zoom In": lambda: self._zoom(1), "Zoom Out": lambda: self._zoom(-1),
                   "Actual Size": lambda: self._zoom(0), "Print…": lambda: self.browser.print_page(view)}
        action = actions.get(text)
        if action is None:
            return False
        if view is not None or text == "Close Tab":
            action()
        return True

    def copy_link(self):
        if self.view is not None and not self.view.url().isEmpty():
            QGuiApplication.clipboard().setText(clean_links.for_copy(self.view.url().toString(),
                                                                    self.browser.store.settings))
            self._say("Copied link")

    def _zoom(self, direction):
        if self.view is None:
            return
        self.browser.zoom(direction, self.view)  # (remembered for the site, as in the main window)
        self._say(f"Zoom {round(self.view.zoomFactor() * 100)}%")  # (said here: the main window said it, behind)

    def _say(self, text):
        self.toast.setText(text)
        self.toast.adjustSize()
        self.toast.move((self.card.width() - self.toast.width()) // 2, self.card.height() - self.toast.height() - 16)
        self.toast.show()
        self.toast.raise_()
        QTimer.singleShot(1400, self.toast.hide)

    def _full_screen(self, request):
        view = self.view
        if not request.toggleOn() or view is None:
            request.accept()
            return
        self.promote()
        if sip.isdeleted(view) or view.parentWidget() is None:
            request.reject()  # (it opened again in another profile's tab: that one can go full screen)
            return
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.browser._on_fullscreen(request, view)

    def promote(self):
        view, self.view = self.view, None
        if view is not None and not view.url().isEmpty():
            for link in self._links:
                QObject.disconnect(link)
            self.browser.adopt_view(view)
        self.close()
