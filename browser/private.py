# Private windows (⇧⌘P): browsing that leaves nothing behind.
#
# Every private window shares one off-the-record profile: cookies, logins, cache and site data live
# in memory only, and go when the last private window closes. Nothing is added to history, the
# command bar's suggestions, site icons, zoom levels, remembered permissions or the page-text index,
# and extensions don't run here (Chrome's default too). Ads are still blocked and cookie banners
# still rejected. Downloads are kept: they're files you asked for.
#
# The window is a small, dark browser of its own: a sidebar of tabs on the left, the page on the
# right, the same rounded card as the main window.

import sys

import qtawesome as qta
from PyQt6.QtCore import QObject, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QKeySequence, QPainter, QShortcut
from PyQt6.QtWebEngineCore import QWebEngineNewWindowRequest, QWebEngineProfile
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget,
                             QToolButton, QVBoxLayout, QWidget)

from . import macos
from .command_bar import search_url, to_url
from .content import RADIUS, Corner
from .models import display_host
from .theme import letter_icon, make_theme, quiet_scrollbars, rgba, stylesheet

MAC = sys.platform == "darwin"
THEME = make_theme("#7c5cff", dark=True, colors=["#4b3a8c", "#191427"], intensity=0.55)
ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

PRIVATE_STYLE = f"""
    #PrivateSidebar QLabel#PrivateBadge {{ color: {rgba(THEME.text)}; font-size: 13px; font-weight: 700; }}
    QLineEdit#PrivateUrl {{
        background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.06); border-radius: 9px;
        color: {rgba(THEME.text)}; padding: 7px 10px; font-size: 13px;
        selection-background-color: {rgba(THEME.accent, 0.6)};
    }}
    QLineEdit#PrivateUrl:focus {{ background: rgba(255,255,255,0.13); border-color: {rgba(THEME.accent, 0.7)}; }}
    QPushButton#PrivateNewTab {{
        background: transparent; border: none; border-radius: 8px; color: rgba(242,242,244,0.7);
        text-align: left; padding: 7px 8px; font-size: 13px;
    }}
    QPushButton#PrivateNewTab:hover {{ background: rgba(255,255,255,0.07); color: {rgba(THEME.text)}; }}
    QFrame#PrivateTab {{ border-radius: 8px; }}
    QFrame#PrivateTab:hover {{ background: rgba(255,255,255,0.06); }}
    QFrame#PrivateTab[current="true"] {{ background: rgba(255,255,255,0.14); }}
    QFrame#PrivateTab QLabel {{ color: {rgba(THEME.text)}; font-size: 13px; }}
    QToolButton#PrivateClose {{ background: transparent; border: none; border-radius: 5px; padding: 2px; }}
    QToolButton#PrivateClose:hover {{ background: rgba(255,255,255,0.12); }}
    QLabel#PrivateHint {{ color: rgba(242,242,244,0.45); font-size: 11px; }}
    QFrame#PrivateCard {{ background: #1c1c1e; border-radius: {RADIUS}px; }}
"""


class _Background(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), THEME.gradient(self.width(), self.height()))


class _ThemeHolder:
    """Corner() reads browser.theme; private windows have their own."""
    theme = THEME


class TabRow(QFrame):
    def __init__(self, window, view):
        super().__init__()
        self.window_, self.view = window, view
        self.setObjectName("PrivateTab")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 4, 0)
        row.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(16, 16)
        self.title = QLabel("New Tab")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("PrivateClose")
        self.close_btn.setIcon(qta.icon("ph.x", color=THEME.text))
        self.close_btn.setIconSize(QSize(11, 11))
        self.close_btn.clicked.connect(lambda: window.close_tab(view))
        self.close_btn.hide()
        row.addWidget(self.icon)
        row.addWidget(self.title, 1)
        row.addWidget(self.close_btn)
        self.set_icon(None)

    def set_icon(self, icon):
        if icon is None or icon.isNull():
            host = display_host(self.view.url().toString())
            icon = letter_icon(host, THEME.accent) if host else qta.icon("ph.globe-simple", color=THEME.text)
        self.icon.setPixmap(icon.pixmap(16, 16))

    def set_title(self, text):
        self.title.setText(self.title.fontMetrics().elidedText(text or "New Tab", Qt.TextElideMode.ElideRight, 150))
        self.setToolTip(text)

    def set_current(self, current):
        self.setProperty("current", current)
        self.style().unpolish(self)
        self.style().polish(self)
        self.close_btn.setVisible(current)

    def enterEvent(self, event):
        self.close_btn.show()

    def leaveEvent(self, event):
        self.close_btn.setVisible(self.property("current") is True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_.select(self.view)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.window_.close_tab(self.view)


class PrivateWindow(QWidget):
    def __init__(self, session, url=None, request=None):
        super().__init__(None, Qt.WindowType.Window)
        self.session, self.browser = session, session.browser
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if MAC:
            self.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
            self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self.setWindowTitle("Private")
        self.setStyleSheet(stylesheet(THEME) + PRIVATE_STYLE)
        self.setMinimumSize(640, 420)
        self.views = []
        self.rows = {}
        self.current = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = _Background()
        outer.addWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(0)

        side = QWidget()
        side.setObjectName("PrivateSidebar")
        side.setFixedWidth(232)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 0, 8, 10)
        sv.setSpacing(6)
        self.top = QWidget()
        self.top.setFixedHeight(40)
        top = QHBoxLayout(self.top)
        top.setContentsMargins(76 if MAC else 4, 0, 4, 0)
        top.addStretch(1)
        badge_icon = QLabel()
        badge_icon.setPixmap(qta.icon("ph.ghost", color=THEME.accent.lighter(150)).pixmap(16, 16))
        badge = QLabel("Private")
        badge.setObjectName("PrivateBadge")
        top.addWidget(badge_icon)
        top.addWidget(badge)
        sv.addWidget(self.top)
        self.url = QLineEdit()
        self.url.setObjectName("PrivateUrl")
        self.url.setPlaceholderText("Search or Enter URL…")
        self.url.returnPressed.connect(self._go)
        self.url.installEventFilter(self)
        sv.addWidget(self.url)
        new_tab = QPushButton("  New Tab")
        new_tab.setObjectName("PrivateNewTab")
        new_tab.setIcon(qta.icon("ph.plus", color=THEME.text))
        new_tab.setCursor(Qt.CursorShape.PointingHandCursor)
        new_tab.clicked.connect(lambda: self.new_tab())
        sv.addWidget(new_tab)
        tabs = QWidget()
        self.tab_list = QVBoxLayout(tabs)
        self.tab_list.setContentsMargins(0, 0, 0, 0)
        self.tab_list.setSpacing(2)
        self.tab_list.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(tabs)
        quiet_scrollbars(scroll)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        tabs.setAutoFillBackground(False)
        sv.addWidget(scroll, 1)
        hint = QLabel("Nothing here is saved: history, cookies and logins are forgotten when you close "
                      "the last private window.")
        hint.setObjectName("PrivateHint")
        hint.setWordWrap(True)
        sv.addWidget(hint)
        layout.addWidget(side)

        self.card = QFrame()
        self.card.setObjectName("PrivateCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        card_layout.addWidget(self.stack)
        self.corners = [Corner(_ThemeHolder, w, self.card) for w in ("tl", "tr", "bl", "br")]
        self.progress = QFrame(self.card)
        self.progress.setObjectName("ProgressBar")
        self.progress.hide()
        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 8, 0, 0)
        wrap.addWidget(self.card)
        layout.addLayout(wrap, 1)

        for keys, slot in (("Ctrl+T", lambda: self.new_tab()), ("Ctrl+W", lambda: self.close_tab(self.current)),
                           ("Ctrl+L", self.focus_url), ("Ctrl+R", lambda: self.current and self.current.reload()),
                           ("Ctrl+[", lambda: self.current and self.current.back()),
                           ("Ctrl+]", lambda: self.current and self.current.forward()),
                           ("Ctrl+=", lambda: self.zoom(1)), ("Ctrl+-", lambda: self.zoom(-1)),
                           ("Ctrl+0", lambda: self.zoom(0)), ("Meta+Tab", lambda: self.cycle(1)),
                           ("Meta+Shift+Tab", lambda: self.cycle(-1))):  # ⇧⌘P is the File menu's, from anywhere
            QShortcut(QKeySequence(keys), self).activated.connect(slot)
        for n in range(1, 10):
            QShortcut(QKeySequence(f"Ctrl+{n}"), self).activated.connect(lambda n=n: self._jump(n))

        self._place()
        if request is not None:
            view = self.new_tab(select=True, load=False)
            request.openIn(view.page())
        else:
            self.new_tab(url)

    # --- Tabs ----------------------------------------------------------------------------------

    def new_tab(self, url=None, select=True, load=True):
        view = self.browser.create_view(self.session.profile)
        page = view.page()
        row = TabRow(self, view)
        self.rows[id(view)] = row
        self.views.append(view)
        self.tab_list.insertWidget(self.tab_list.count() - 1, row)
        self.stack.addWidget(view)
        view.titleChanged.connect(lambda title, v=view: self._titled(v, title))
        view.iconChanged.connect(lambda icon, v=view: self.rows.get(id(v)) and self.rows[id(v)].set_icon(icon))
        view.urlChanged.connect(lambda _url, v=view: v is self.current and self._show_url())
        view.loadProgress.connect(lambda value, v=view: v is self.current and self._progress(value))
        page.newWindowRequested.connect(self._new_window)
        page.windowCloseRequested.connect(lambda v=view: self.close_tab(v))
        if select:
            self.select(view)
        if url and load:
            view.load(QUrl(url))
        elif load:
            QTimer.singleShot(0, self.focus_url)
        return view

    def _new_window(self, request):
        # Links and popups stay private: a new tab here, never the main window or a Little window
        background = request.destination() == QWebEngineNewWindowRequest.DestinationType.InNewBackgroundTab
        view = self.new_tab(select=not background, load=False)
        request.openIn(view.page())

    def select(self, view):
        if view is None:
            return
        self.current = view
        self.stack.setCurrentWidget(view)
        for v in self.views:
            self.rows[id(v)].set_current(v is view)
        self._show_url()
        self._progress(100)
        self.setWindowTitle(f"{view.title() or 'New Tab'} — Private")
        if not view.url().isEmpty():
            view.setFocus()

    def close_tab(self, view):
        if view is None or view not in self.views:
            return
        i = self.views.index(view)
        self.views.remove(view)
        row = self.rows.pop(id(view))
        row.deleteLater()
        self.stack.removeWidget(view)
        view.deleteLater()
        if not self.views:
            self.close()
            return
        if view is self.current:
            self.select(self.views[min(i, len(self.views) - 1)])

    def cycle(self, step):
        if self.current in self.views:
            self.select(self.views[(self.views.index(self.current) + step) % len(self.views)])

    def _jump(self, n):
        if self.views:
            self.select(self.views[-1] if n == 9 else self.views[min(n, len(self.views)) - 1])

    def _titled(self, view, title):
        row = self.rows.get(id(view))
        if row is not None:
            row.set_title(title)
            row.set_icon(view.icon())
        if view is self.current:
            self.setWindowTitle(f"{title or 'New Tab'} — Private")

    # --- Address & zoom ---------------------------------------------------------------------------

    def focus_url(self):
        self.url.setFocus()
        self.url.selectAll()

    def _show_url(self):
        # Unless you're typing in it (the field can have focus just because the window opened)
        if self.current is not None and not (self.url.hasFocus() and self.url.isModified()):
            url = self.current.url().toString()
            if url in ("", "about:blank"):
                self.url.setText("")
            elif self.url.hasFocus():
                self.url.setText(url)
            else:  # at rest, the site and path, as the main window's URL pill shows it
                q = self.current.url()
                self.url.setText(q.host().removeprefix("www.") + q.path().rstrip("/") if q.host() else url)
            self.url.setCursorPosition(0)

    def eventFilter(self, obj, event):
        if obj is self.url and event.type() == event.Type.FocusIn and self.current is not None:
            url = self.current.url().toString()
            if url not in ("", "about:blank") and not self.url.isModified():
                self.url.setText(url)  # editing: the whole address
                QTimer.singleShot(0, self.url.selectAll)
        elif obj is self.url and event.type() == event.Type.FocusOut and not self.url.isModified():
            self._show_url()
        return super().eventFilter(obj, event)

    def _go(self):
        text = self.url.text().strip()
        if not text:
            return
        if self.current is None:
            self.new_tab()
        engine = self.browser.store.settings.get("search_engine", "google")
        self.current.load(QUrl(to_url(text) or search_url(text, engine)))
        self.url.setModified(False)
        self.current.setFocus()

    def zoom(self, direction):
        # Not remembered per site, unlike the main window: that would be a record of where you went
        if self.current is None:
            return
        factor = self.current.zoomFactor()
        if direction == 0:
            factor = 1.0
        else:
            steps = [z for z in ZOOM_STEPS if (z > factor + 0.01 if direction > 0 else z < factor - 0.01)]
            factor = (min(steps) if direction > 0 else max(steps)) if steps else factor
        self.current.setZoomFactor(factor)

    def _progress(self, value):
        if value >= 100:
            self.progress.hide()
            return
        self.progress.setGeometry(0, 0, max(int(self.card.width() * value / 100), 8), 2)
        self.progress.show()
        self.progress.raise_()

    # --- Window ------------------------------------------------------------------------------------

    def _place(self):
        main = self.browser.geometry()
        width, height = int(main.width() * 0.85), int(main.height() * 0.85)
        offset = 30 * (len(self.session.windows) % 5)
        self.setGeometry(main.center().x() - width // 2 + offset, main.center().y() - height // 2 + offset,
                         width, height)

    def showEvent(self, event):
        super().showEvent(event)
        if MAC:
            macos.hide_title_text(self)
            lights = macos.traffic_lights(self)
            if lights:
                right, centre = lights
                self.top.setFixedHeight(max(round(centre * 2), 32))
                self.top.layout().setContentsMargins(round(right + 12), 0, 4, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._place_corners)

    def _place_corners(self):
        w, h = self.card.width(), self.card.height()
        spots = {"tl": (0, 0), "tr": (w - RADIUS, 0), "bl": (0, h - RADIUS), "br": (w - RADIUS, h - RADIUS)}
        for corner in self.corners:
            corner.move(*spots[corner.which])
            corner.raise_()

    def closeEvent(self, event):
        for view in list(self.views):
            self.stack.removeWidget(view)
            view.deleteLater()  # before the profile goes, or Qt warns and may keep it alive
        self.views.clear()
        super().closeEvent(event)


class PrivateSession(QObject):
    """The private profile, shared by every private window, and forgotten with the last one."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.profile = None
        self.windows = []
        self.downloads = set()  # downloads from private windows still in progress

    def new_window(self, url=None, request=None):
        if self.profile is None:
            self.profile = QWebEngineProfile(self.browser)  # no storage name: off the record
            self.profile.setHttpUserAgent(self.browser.profile.httpUserAgent())
            self.browser.adblock.attach(self.profile)  # ads still blocked, cookie banners still rejected
            self.profile.downloadRequested.connect(self._download_started)
            self.profile.downloadRequested.connect(self.browser._on_download)
        window = PrivateWindow(self, url=url, request=request)
        self.windows.append(window)
        window.destroyed.connect(lambda _=None, w=window: self._closed(w))
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def _download_started(self, download):
        # Kept until it finishes: the profile (which owns it) waits for these before it goes
        self.downloads.add(download)
        download.isFinishedChanged.connect(lambda d=download: d.isFinished() and self._download_done(d))

    def _download_done(self, download):
        self.downloads.discard(download)
        if not self.windows:
            self._release()

    def is_private_download(self, download) -> bool:
        return download in self.downloads

    def _closed(self, window):
        if window in self.windows:
            self.windows.remove(window)
        if not self.windows:
            self._release()

    def _release(self):
        if self.downloads:
            return  # a download is still running: the profile has to outlive it
        if self.profile is not None:
            profile, self.profile = self.profile, None
            self.browser.adblock.detach(profile)
            # Once its pages are gone (deleteLater runs first), the profile and all it held go too
            QTimer.singleShot(0, profile.deleteLater)

    def shutdown(self):
        for window in list(self.windows):
            window.close()

    @staticmethod
    def is_private(view) -> bool:
        return view.page().profile().isOffTheRecord()

