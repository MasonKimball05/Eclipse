# The main window doubles as the controller: it owns the Store (data), the
# web views, and every user action. Sidebar/command bar widgets call into it.

import html
import re
import secrets
import shutil
import sys
import time
from collections import OrderedDict
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QEvent, QPoint, QRect, QSize, QStandardPaths, Qt, QTimer, QUrl
from PyQt6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QGuiApplication, QIcon, QKeySequence,
                         QShortcut)
from PyQt6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEngineNewWindowRequest, QWebEnginePage,
                                   QWebEnginePermission, QWebEngineScript, QWebEngineSettings)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QColorDialog, QDialog, QFileDialog,
                             QHBoxLayout, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QSplitter,
                             QWidget)

import qtawesome as qta

from . import (backup, brand, clean_links, dark_pages, macos, motion, screenshots, shortcuts, space_share, spelling,
               storage)
from .adblock import AdBlocker
from .command_bar import CUSTOM_SETTING, CommandBar, use_custom_sites
from .content import ContentFrame
from .downloads import (BrowserDownload, DownloadSettings, DownloadsWindow, reveal_in_finder,
                        risky as downloads_risky, ytdlp_settings)
from .library import LibraryWindow
from .air_traffic import route
from .little import LittleWindow
from .mini_player import MiniPlayer
from .notify import NotificationCenter
from .devmode import DevMode, is_dev_url
from .cert_errors import CertificateGuard
from .crash import CrashWatcher, logs_dir as crash_logs_dir
from .page_index import PageIndex
from .private import PrivateSession
from .reader import ReaderView
from .external_links import ExternalLinks
from . import features as feature_loader
from .sidebar_edge import SidebarEdge
from .favicons import Favicons
from .floating_sidebar import EDGE as FLOAT_EDGE, FloatingSidebar
from .traffic_lights import TrafficLights
from .space_slide import slide_region
from .tab_preview import TabPreview
from .tab_switcher import TabSwitcher
from .zoom import remember_zoom, step_zoom, zoom_for, zoom_key
from .page_actions import markdown_link, safe_filename
from .passkeys import Passkeys
from .passwords import Passwords
from .permissions import KINDS, PermissionManager, origin_key
from .peek import PeekOverlay
from .split import MAX_PANES
from .models import FAVORITES, PINNED, TODAY, Folder, Tab, display_host, node_path, walk_folders, walk_tabs
from .settings import ARCHIVE_CHOICES, DEFAULT_SLEEP_MINUTES, SettingsWindow
from .sidebar import Sidebar
from .icons import space_icon
from .space_editor import SpaceEditor
from .theme import PALETTE, PlainMenu, ThemedBackground, dot_icon, stylesheet, theme_for_space
from .theme_editor import ThemeEditor
from .webview import DEFAULT_PROFILE, WebPage, WebView, build_profile
from .timefmt import portable

MAC = sys.platform == "darwin"
# Qt maps "Ctrl" to ⌘ on macOS; the physical Control key is "Meta" there
CONTROL = "Meta" if MAC else "Ctrl"
RECENT_PAGES = 12  # (at the bottom of the History menu)
DARK_PAGE_BLANK = "#1b1b1e"  # (theme.py's page-area colour on a dark Space)
KEEP_RUNNING = "keep_running"  # (closing the window leaves Eclipse open, on a Mac)
AUTOPLAY_SETTING = "media_waits_for_click"  # (Settings → Privacy: videos don't play until you click)
SIDEBAR_WIDTH = 250  # default; drag the sidebar's edge to change it
SIDEBAR_GAP = 12     # between the sidebar's contents and the page: all of it is the resize edge


def _save_editor(view):
    """A tab that isn't a web page (a note, an easel) keeps its work: saved before it goes."""
    if view is not None and not isinstance(view, QWebEngineView) and hasattr(view, "save"):
        view.save()


def _delete(obj):
    """Free a Qt object now, unless Qt already has (deleteLater may have beaten us to it)."""
    if obj is not None and not sip.isdeleted(obj):
        sip.delete(obj)


class MainWindow(QMainWindow):
    def __init__(self, store, profile):
        super().__init__()
        self.store = store
        self.profile = profile
        self.theme = theme_for_space(store.active_space)
        self.current_tab: Tab | None = None
        self._mru: list[str] = []                   # tab ids, most recently used first
        self._closed: list[tuple[dict, str]] = []   # (tab data, space id) for ⇧⌘T
        self._extra_windows: list[QWidget] = []     # Little windows and DevTools
        self._recent_downloads: dict[int, list[float]] = {}  # id(page) → when it last started downloads
        self._fullscreen = False
        self._rebuild_pending = False
        # Claude, Live Folders, extensions, Easels…: whichever features are here (features.py), each hooked in
        self._hovered_link = ""
        self._shift_alone = False

        self.adblock = AdBlocker(store, self)
        self._shield_timer = QTimer(self, singleShot=True, interval=300, timeout=lambda: self.sidebar.update_shield())
        self.adblock.blocked.connect(lambda _site: self._shield_timer.start())  # coalesce bursts
        self.mini_player = MiniPlayer(self)  # floats a playing video when you switch away from its tab
        self.permissions = PermissionManager(self)  # camera, microphone, screen sharing, location…
        self.passkeys = Passkeys(self)  # signing in with a security key: its PIN, "touch your key"
        self.passwords = Passwords(self)  # saved passwords: filling sign-in forms, offering to save
        self.external_links = ExternalLinks(self)  # links that open other apps: asked about first
        WebPage.external_link = lambda *asked: self.external_links.requested(*asked)
        self.notifications = NotificationCenter(self)  # site notifications and unread badges
        self.devmode = DevMode(self)  # localhost and file pages: console drawer, reload on save
        self.crashes = CrashWatcher(self)  # pages whose renderer crashed get a Reload card
        self.certificates = CertificateGuard(self)  # "This connection isn't private"
        self.private = PrivateSession(self)  # ⇧⌘P: windows that remember nothing
        self.page_index = PageIndex(self)  # the text of pages you've read, searchable from ⌘T
        use_custom_sites(store.settings.get(CUSTOM_SETTING, []))  # your own search shortcuts
        self._downloads: DownloadsWindow | None = None
        self._settings: SettingsWindow | None = None
        self._library: LibraryWindow | None = None
        self._page_shot = None   # a whole-page screenshot being taken
        self._keep_closed = 30   # how many closed tabs ⇧⌘T can bring back
        self._printing = None  # (view, printer) while a page prints
        self.traffic_lights = TrafficLights(self)  # with the sidebar, dim until pointed at
        self.floating_sidebar = FloatingSidebar(self)  # the hidden sidebar, out over the page on hover

        # The features (after the core's services, which they may use; before the first profile, which they hook)
        self.features = feature_loader.load(self)

        # One browser profile per name; Spaces can pick their own (separate logins)
        self._profiles = {}
        self._register_profile(DEFAULT_PROFILE, profile)

        if MAC:
            # Arc-style chrome: no title bar, the traffic lights sit inside the sidebar
            self.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
            self.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        self.setMinimumSize(640, 420)

        root = ThemedBackground(self)
        root.setObjectName("Root")
        root.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)

        self.sidebar = Sidebar(self)
        self.content = ContentFrame(self)
        self.reader = ReaderView(self, self.content)  # ⌥⌘R, over the page
        content_wrap = self.content_wrap = QWidget()  # (the page, and anything beside it: Ask About This Page)
        self.content_layout = QHBoxLayout(content_wrap)
        self.content_layout.setSpacing(SIDEBAR_GAP)
        self.content_layout.addWidget(self.content, 1)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("SidebarSplitter")
        # The sidebar's edge: drag to resize, double-click to reset. It's the whole gap between the sidebar's
        # contents and the page (the sidebar has no margin on that side), so wherever the sidebar looks like it
        # ends, that's where it takes hold.
        self.splitter.setHandleWidth(SIDEBAR_GAP)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(content_wrap)
        if self.sidebar_on_right:
            self.splitter.insertWidget(1, self.sidebar)
        self.splitter.setStretchFactor(self._content_index, 1)
        self._set_sidebar_size(store.settings["sidebar_width"])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self._dress_handle()
        root_layout.addWidget(self.splitter)
        self.setCentralWidget(root)

        self.peek = PeekOverlay(self, root)
        self.command_bar = CommandBar(self, root)
        self.tab_switcher = TabSwitcher(self, root)
        self.sidebar_edge = SidebarEdge(self)  # (the resize edge, on the page's edge)
        self.tab_preview = TabPreview(self)
        self.thumbnails: OrderedDict[str, object] = OrderedDict()  # tab id → a small picture of its page

        self.sidebar.setVisible(store.settings.get("sidebar_visible", True))
        self._update_content_margins()
        self.emit("ready")  # (the features: the window's there now)

        store.structure_changed.connect(self._schedule_rebuild)
        # Icons for tabs that haven't loaded yet (pinned tabs, Favorites…), looked for a moment after start
        self.favicons = Favicons(self, profile.httpUserAgent())
        self.favicons.fetched.connect(self._icon_fetched)
        self._icons_wanted = QTimer(self, singleShot=True, interval=1500, timeout=self._want_icons)
        store.structure_changed.connect(self._icons_wanted.start)
        self._icons_wanted.start()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        # macOS switching between light and dark: the window crossfades into its new colours
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: motion.crossfade(self.centralWidget(), self.centralWidget(), self.apply_theme, ms=280))
        self.apply_theme()
        # The native macOS menu bar is slow to build (~150 ms), so draw the window first
        QTimer.singleShot(0, self._build_menus)
        self.apply_keep_running()
        QApplication.instance().applicationStateChanged.connect(self._app_state_changed)

        if store.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(store.window_geometry.encode()))
        else:
            self.resize(1280, 820)

        self._archive_timer = QTimer(self, interval=15 * 60 * 1000, timeout=self.archive_stale_tabs)
        self._archive_timer.start()
        self._sleep_timer = QTimer(self, interval=60 * 1000, timeout=self.sleep_idle_tabs)
        self._sleep_timer.start()
        # Like Arc, a launch opens on your Space's colours, not on whatever you had open last: every tab's in
        # the sidebar, asleep, for when you want it. (Switching Spaces still brings back each one's tab.)
        last = self.store.find_tab(self.store.active_space.active_tab_id)
        # It (and any Split View it's in) isn't archived for being idle while nothing's open: it's still
        # "the tab you were on", until you open something
        self._kept_from_launch = (self.store.split_of(last) or [last]) if last is not None else []
        self._show_nothing()
        self.archive_stale_tabs()

        # Watch for Shift anywhere (including inside web pages) for link previews
        QApplication.instance().installEventFilter(self)


    # --- Features (features.py) -------------------------------------------------------------

    def feature(self, name):
        """A feature by name, or None if it isn't here."""
        return self.features.get(name)

    def emit(self, hook, *args):
        """Tell every feature something (a Feature hook)."""
        for feature in list(self.features.values()):
            getattr(feature, hook)(*args)

    def gather(self, hook, *args) -> list:
        """What every feature has to add (a Feature hook that returns a list)."""
        found = []
        for feature in list(self.features.values()):
            found += getattr(feature, hook)(*args) or []
        return found

    def first(self, hook, *args):
        """The first feature's answer that isn't None."""
        for feature in list(self.features.values()):
            answer = getattr(feature, hook)(*args)
            if answer is not None:
                return answer
        return None

    # --- Theme & layout ----------------------------------------------------------------

    def apply_theme(self):
        self.theme = theme_for_space(self.store.active_space)
        self.setStyleSheet(stylesheet(self.theme))
        if getattr(self, "features", None) is not None:
            self.emit("theme_changed")
        self.centralWidget().update()
        self.sidebar.rebuild()
        if self._downloads is not None:
            self._downloads.apply_theme()
        for panel in (self._settings, self._library):
            if panel is not None:
                panel.apply_theme()
        for corner in self.content.corners:
            corner.update()
        if self.content.blank:
            self.content.empty.refresh()  # (the start view, in the new Space's colours and name)

    def _schedule_rebuild(self):
        # Many store changes can land in one event loop pass; rebuild the sidebar once
        if not self._rebuild_pending:
            self._rebuild_pending = True
            QTimer.singleShot(0, self._rebuild_now)

    def _rebuild_now(self):
        self._rebuild_pending = False
        self.sidebar.rebuild()

    # --- Which side the sidebar is on ----------------------------------------------------------------

    @property
    def sidebar_on_right(self) -> bool:
        return self.store.settings.get("sidebar_side") == "right"

    @property
    def _sidebar_index(self) -> int:
        return 1 if self.sidebar_on_right else 0

    @property
    def _content_index(self) -> int:
        return 1 - self._sidebar_index

    def _dress_handle(self):
        # (a new handle object after the sidebar moves sides or docks again)
        self.splitter.handle(1).setCursor(Qt.CursorShape.SplitHCursor)
        self.splitter.handle(1).setToolTip("Drag to resize the sidebar · double-click to reset")

    def _set_sidebar_size(self, width):
        rest = max(self.splitter.width() - width, 1000)
        self.splitter.setSizes([rest, width] if self.sidebar_on_right else [width, rest])

    def set_sidebar_side(self, side):
        """"left" or "right", like Arc's Sidebar Position."""
        if side == ("right" if self.sidebar_on_right else "left"):
            return
        if self.floating_sidebar.active:
            self.sidebar.hide()
            self._dock_sidebar()
        self.store.settings["sidebar_side"] = side
        self.store.schedule_save()
        width = self.store.settings.get("sidebar_width", SIDEBAR_WIDTH)

        def change():
            self.splitter.insertWidget(self._sidebar_index, self.sidebar)
            self.splitter.setStretchFactor(self._sidebar_index, 0)
            self.splitter.setStretchFactor(self._content_index, 1)
            self._set_sidebar_size(width)
            self._dress_handle()
            self._update_content_margins()
            self.sidebar.align_with_traffic_lights()
        motion.crossfade(self.centralWidget(), self.splitter, change)

    def _sidebar_margins(self):
        """The sidebar's padding: none on the page's side when it's docked (the gap there is the resize
        edge); floating over the page, a little on both sides inside its panel."""
        floating = self.sidebar.property("floating") is True
        inner, outer = (4, 10) if floating else (0, 10)
        left, right = (inner, outer) if self.sidebar_on_right else (outer, inner)
        top = self.sidebar.layout().contentsMargins().top()  # (set to line up with the traffic lights: kept)
        self.sidebar.layout().setContentsMargins(left, top, right, 10)

    def _update_content_margins(self):
        self._sidebar_margins()
        if self._fullscreen:
            self.content_layout.setContentsMargins(0, 0, 0, 0)
        elif not self.sidebar.isHidden() and self.sidebar_on_right:
            self.content_layout.setContentsMargins(8, 8, 0, 8)
        elif not self.sidebar.isHidden():  # (not isVisible(): before the window first shows, nothing is)
            self.content_layout.setContentsMargins(0, 8, 8, 8)
        else:  # (the traffic lights go with the sidebar: the page has all of the window)
            self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.place_traffic_lights()
        if getattr(self, "sidebar_edge", None) is not None:
            QTimer.singleShot(0, self.sidebar_edge.place)

    def place_traffic_lights(self):
        """Arc's traffic lights: in the sidebar (moved over to it when it's on the right), hidden with it.
        The hidden sidebar brings them out with it when it slides out (see floating_sidebar.py)."""
        if not MAC or not self.isVisible():
            return  # (before it's shown: asking for the native window then would make it too soon)
        if self._fullscreen or self.isFullScreen():  # (full screen: where macOS puts them, in its menu bar)
            self.traffic_lights.place(True, 0, 0)
            return
        if self.floating_sidebar.active:
            return
        if self.sidebar.isHidden():
            self.traffic_lights.place(False)
        elif self.sidebar_on_right:
            self.traffic_lights.place(True, self.sidebar.mapTo(self, QPoint(0, 0)).x(), 0)
            QTimer.singleShot(0, self.sidebar.align_with_traffic_lights)  # (its buttons sit after them now)
        else:
            self.traffic_lights.place(True)

    def reset_sidebar_width(self):
        self._set_sidebar_size(SIDEBAR_WIDTH)
        self._on_splitter_moved()

    def _on_splitter_moved(self, *_):
        self.store.settings["sidebar_width"] = self.splitter.sizes()[self._sidebar_index]
        self.store.schedule_save()
        if self.sidebar_on_right:
            self.place_traffic_lights()  # (they're on the sidebar's left edge, which just moved)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.command_bar.setGeometry(self.centralWidget().rect())
        if self.peek.isVisible():
            self.peek.setGeometry(self.centralWidget().rect())
        if self.floating_sidebar.active:
            self._place_floating_sidebar()
        elif self.sidebar_on_right:
            self.place_traffic_lights()  # (the sidebar's edge, where they are, moved with the window's)
        self.notifications.relayout()

    def setWindowTitle(self, title):
        super().setWindowTitle(title)
        if getattr(self, "traffic_lights", None) is not None:
            self.traffic_lights.keep()  # (a new title has macOS lay the lights out again)

    def changeEvent(self, event):
        super().changeEvent(event)
        kind = event.type()
        if kind == QEvent.Type.ActivationChange:
            self.traffic_lights.activity()
        elif kind == QEvent.Type.WindowStateChange:  # (out of full screen: macOS lays the lights out anew)
            QTimer.singleShot(0, self.place_traffic_lights)
            QTimer.singleShot(800, self.place_traffic_lights)  # (and again once its animation's over)

    def showEvent(self, event):
        super().showEvent(event)
        if MAC:
            macos.hide_title_text(self)  # otherwise the page title is drawn over the sidebar
            QTimer.singleShot(0, self.place_traffic_lights)  # (now that there are native buttons to place)
            self.sidebar.align_with_traffic_lights()

    # Empty areas of the chrome drag the window, since there is no title bar
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            self.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # --- Web views -----------------------------------------------------------------------

    # --- Profiles -------------------------------------------------------------------------

    def _register_profile(self, name, profile):
        self._profiles[name] = profile
        profile.downloadRequested.connect(self._on_download)
        self.adblock.attach(profile)
        self.permissions.attach_profile(profile)
        self.passkeys.attach_profile(profile)
        self.passwords.attach_profile(profile)
        self.notifications.attach_profile(profile)
        self.emit("profile_ready", profile)
        spelling.apply([profile], self.store.settings)
        self.apply_autoplay([profile])

    def apply_autoplay(self, profiles=None):
        """Videos and sound wait for a click (Settings → Privacy), or play when a page starts them."""
        wait = self.store.settings.get(AUTOPLAY_SETTING, False)
        if profiles is None:
            profiles = list(self._profiles.values()) + ([self.private.profile] if self.private.profile else [])
        for profile in profiles:
            profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, wait)

    def apply_spelling(self) -> bool:
        profiles = list(self._profiles.values()) + ([self.private.profile] if self.private.profile else [])
        return spelling.apply(profiles, self.store.settings)

    def profile_named(self, name):
        if name not in self._profiles:
            self._register_profile(name, build_profile(QApplication.instance(), name))
        return self._profiles[name]

    def profile_for_tab(self, tab):
        loc = self.store.locate(tab)
        space = loc[1] if loc else None  # favorites use the default profile
        return self.profile_named(space.profile if space else DEFAULT_PROFILE)

    def profile_names(self) -> list[str]:
        return sorted({DEFAULT_PROFILE, *(s.profile for s in self.store.spaces), *self._profiles},
                      key=lambda n: (n != DEFAULT_PROFILE, n.lower()))

    def set_space_profile(self, space, name):
        if name is None:
            name, ok = QInputDialog.getText(self, "New Profile", "Profile name (e.g. Work, School):")
            name = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-") if ok else ""
            if not name:
                return
        if space.profile == name:
            return
        space.profile = name
        for tab in list(walk_tabs(space.pinned + space.today)):
            self._destroy_view(tab)  # pages reload with the new profile's cookies
        self.store._changed()
        if space is self.store.active_space:
            self._restore_space_tab()
        self.toast(f"{space.name} now uses the “{name}” profile")

    # --- Web views -----------------------------------------------------------------------

    def create_view(self, profile=None) -> WebView:
        view = WebView(profile or self.profile)
        view.page().dark_rule = lambda url: dark_pages.wanted(self.store.settings, url)
        self.permissions.attach(view)
        self.passkeys.attach(view)
        self.passwords.attach(view)
        self.devmode.attach(view)
        self.crashes.attach(view)
        self.certificates.attach(view)
        view.urlChanged.connect(lambda _url, v=view: self._apply_site_zoom(v))
        view.loadFinished.connect(lambda _ok, v=view: self._apply_site_zoom(v))  # pages can reset it
        self.emit("view_created", view, None)
        view.printRequested.connect(lambda v=view: self.print_page(v))  # a site's own Print button
        view.printRequestedByFrame.connect(lambda _frame, v=view: self.print_page(v))
        view.context_menu_hook = self._extend_context_menu
        self._count_loading(view)
        self._dark_until_painted(view)
        return view

    def _dark_until_painted(self, view):
        """A new page is blank until its first frame (half a second or more: longer for a big site), and Qt
        paints that blank white: on a dark Space, a white flash each time a Space's tab (or a sleeping one) opens.
        So until it's about to draw its first frame, the blank is the page area's own dark colour; from then it's
        the web's white again (the colour of pages that leave their background to the browser)."""
        if not self.theme.dark:
            return
        page = view.page()
        page.setBackgroundColor(QColor(DARK_PAGE_BLANK))

        def white():
            page.first_paint = None
            if not sip.isdeleted(page):
                page.setBackgroundColor(QColor(Qt.GlobalColor.white))
        page.first_paint = white

        def loaded(_ok):  # (a page in the background doesn't draw: white once it's loaded, whatever)
            view.loadFinished.disconnect(loaded)
            QTimer.singleShot(600, lambda: page.first_paint is not None and white())
        view.loadFinished.connect(loaded)

    @staticmethod
    def _count_loading(view):
        # While a page loads, macOS mustn't nap the app (see macos.busy); once per load, however it ends
        state = {"loading": False}

        def started():
            if not state["loading"]:
                state["loading"] = True
                macos.busy(True)

        def stopped(*_):
            if state["loading"]:
                state["loading"] = False
                macos.busy(False)
        view.loadStarted.connect(started)
        view.loadFinished.connect(stopped)
        view.destroyed.connect(stopped)

    def _extend_context_menu(self, view, menu, request):
        tab = next((t for t in self.store.all_tabs() if t.view is view), None)
        selected = " ".join(request.selectedText().split())
        if selected and not request.misspelledWord():  # (a typo's corrections stay first, as in any Mac app)
            self._selection_actions(view, menu, selected)
        self.emit("page_menu", menu, request, view, tab)
        link = request.linkUrl()
        copy_link = view.page().action(QWebEnginePage.WebAction.CopyLinkToClipboard)
        if link.isValid() and copy_link in menu.actions():  # copied without its tracking tags
            clean_copy = QAction(copy_link.text(), menu)
            clean_copy.triggered.connect(lambda _=False, u=bytes(link.toEncoded()).decode(): QGuiApplication.clipboard()
                                         .setText(clean_links.for_copy(u, self.store.settings)))
            menu.insertAction(copy_link, clean_copy)
            menu.removeAction(copy_link)
        if link.isValid() and link.scheme() in ("http", "https"):
            private = QAction("Open Link in Private Window", menu)
            private.triggered.connect(lambda _=False, u=link.toString(): self.private.new_window(u))
            actions = menu.actions()
            menu.insertAction(actions[2] if len(actions) > 2 else None, private)  # after "Open Link in New Tab/Window"

    def _selection_actions(self, view, menu, selected):
        """Right-clicking selected text: the features' (ask about it, translate it), then look it up, at the top
        of the menu."""
        short = selected if len(selected) <= 28 else selected[:26].rstrip() + "…"
        first = menu.actions()[0] if menu.actions() else None
        added = self.gather("selection_menu", menu, view, selected)
        if len(selected.split()) <= 3 and len(selected) <= 40:  # a word or a term: the Mac's own Dictionary
            action = QAction(f"Look Up “{short}” in Dictionary", menu)
            action.triggered.connect(lambda: QDesktopServices.openUrl(
                QUrl("dict://" + QUrl.toPercentEncoding(selected).data().decode())))
            added.append(action)
        for action in added:
            action.setText(action.text().replace("&", "&&"))
            menu.insertAction(first, action) if first else menu.addAction(action)
        if added and first:
            menu.insertSeparator(first)

    def _ensure_view(self, tab, load=True):
        if tab.view is None and not tab.url.startswith(("http://", "https://", "file:", "about:", "data:")):
            view = self.first("view_for", tab)  # (a feature's own kind of tab: a note, an easel)
            if view is not None:
                tab.view = view
                if hasattr(view, "title_changed"):
                    view.title_changed.connect(lambda title: self._on_title(tab, title))
                tab.title = getattr(view, "title", tab.title)
                self.content.stack.addWidget(view)
                tab.changed.emit()
        if tab.view is None:
            tab.view = self.create_view(self.profile_for_tab(tab))
            self._wire(tab, tab.view)
            self.content.stack.addWidget(tab.view)
            if load and tab.url:
                tab.view.load(QUrl(tab.url))
            tab.changed.emit()
        return tab.view

    def _wire(self, tab, view):
        page = view.page()
        view.urlChanged.connect(lambda url: self._on_url(tab, url))
        view.titleChanged.connect(lambda title: self._on_title(tab, title))
        view.iconChanged.connect(lambda icon: self._on_icon(tab, icon))
        view.loadStarted.connect(lambda: self._on_load_started(tab))
        view.loadProgress.connect(lambda p: tab is self.current_tab and self.content.set_progress(p))
        view.loadFinished.connect(lambda ok: self._on_load_finished(tab, ok))
        page.linkHovered.connect(lambda url: tab is self.current_tab and self._on_link_hovered(url))
        page.newWindowRequested.connect(lambda req: self.handle_new_window(tab, req))
        page.windowCloseRequested.connect(lambda: self.close_tab(tab))
        page.fullScreenRequested.connect(lambda request, v=view: self._on_fullscreen(request, v))
        page.console_message.connect(lambda message: self._on_console(tab, message))
        page.lifecycleStateChanged.connect(lambda _state: tab.changed.emit())  # dim/undim in the sidebar
        page.recentlyAudibleChanged.connect(lambda _audible: self._on_audible(tab))  # the speaker icon
        page.audioMutedChanged.connect(lambda _muted: tab.changed.emit())
        page.findTextFinished.connect(
            lambda r: tab is self.current_tab and self.content.find_bar.show_result(r.activeMatch(),
                                                                                   r.numberOfMatches()))

    def _destroy_view(self, tab):
        if self._fullscreen and tab is self.current_tab:
            self._set_fullscreen(False)  # its page was the one filling the screen
        self.thumbnails.pop(tab.id, None)
        self.mini_player.release(tab)
        self.emit("view_destroyed", tab)
        view, tab.view = tab.view, None
        _save_editor(view)
        if view is not None:
            self.content.remove_view(view)
            view.deleteLater()

    # --- Audio ---------------------------------------------------------------------------------

    def tab_audio(self, tab) -> tuple[bool, bool]:
        """(playing sound, muted) for a tab with a loaded page."""
        if not isinstance(tab.view, QWebEngineView):
            return False, False
        page = tab.view.page()
        return page.recentlyAudible(), page.isAudioMuted()

    def _ctrl_tab(self, direction):
        """⌃Tab: recent tabs (Arc's way, with the switcher) or plain sidebar order, per Settings."""
        if self.store.settings.get("ctrl_tab", "recent") == "recent":
            self.tab_switcher.step(direction)
        else:
            self.visible_tab_step(direction)

    def toggle_dnd(self):
        on = not self.store.settings.get("notifications_dnd", False)
        self.store.settings["notifications_dnd"] = on
        self.store.schedule_save()
        self.toast("Do Not Disturb is on: no notification banners" if on else "Notification banners are back on")

    def toggle_mute(self, tab=None):
        tab = tab or self.current_tab
        if tab is not None and isinstance(tab.view, QWebEngineView):
            page = tab.view.page()
            page.setAudioMuted(not page.isAudioMuted())

    def mute_other_tabs(self, tab=None):
        """Quiet everything else that's playing; Undo turns them back up."""
        tab = tab or self.current_tab
        muted = []
        for other in self.store.all_tabs():
            if other is not tab and isinstance(other.view, QWebEngineView):
                page = other.view.page()
                if page.recentlyAudible() and not page.isAudioMuted():
                    page.setAudioMuted(True)
                    muted.append(other)
        if not muted:
            self.toast("No other tabs are playing sound")
            return
        self.toast(f"Muted {len(muted)} tab{'s' if len(muted) != 1 else ''}", lambda: self._unmute(muted))

    def _unmute(self, tabs):
        for tab in tabs:
            if isinstance(tab.view, QWebEngineView):
                tab.view.page().setAudioMuted(False)

    def others_audible(self, tab) -> bool:
        return any(t is not tab and self.tab_audio(t) == (True, False) for t in self.store.all_tabs())

    def toggle_awake(self, tab=None):
        tab = tab or self.current_tab
        if tab is None:
            return
        tab.awake = not tab.awake
        self._wake(tab)
        tab.changed.emit()
        self.store.schedule_save()
        self.toast("This tab will stay awake" if tab.awake else "This tab can sleep again when it's idle")

    def reload_tabs(self, tabs):
        """Reload the open ones (a folder's): tabs that aren't loaded load fresh when you go to them anyway."""
        reloaded = 0
        for tab in tabs:
            if isinstance(tab.view, QWebEngineView) and not self.is_sleeping(tab):
                tab.view.reload()
                reloaded += 1
        self.toast(f"Reloaded {reloaded} tab{'s' if reloaded != 1 else ''}" if reloaded
                   else "None of these tabs are open")

    def _on_audible(self, tab):
        tab.changed.emit()
        self.mini_player.audible_changed(tab)
        self.emit("audible_changed", tab)

    def _on_url(self, tab, url):
        tab.url = url.toString()
        tab.changed.emit()
        if tab is self.current_tab:
            self.sidebar.update_nav()
        self.store.schedule_save()
        self.emit("url_changed", tab, url)

    def _on_title(self, tab, title):
        tab.title = title
        self.notifications.title_changed(tab)  # "(3) Inbox" → a badge on the tab, and on the Dock
        tab.changed.emit()
        if tab is self.current_tab:
            self.setWindowTitle(tab.label)
        self.store.schedule_save()

    def _want_icons(self):
        """Icons for your own tabs' sites: not for a Live Folder's items (a feed's links are sites you
        haven't chosen to visit), nor for pages you're building (localhost: one icon for every port)."""
        store = self.store

        def own(nodes):
            for node in nodes:
                if isinstance(node, Folder):
                    if not node.live:
                        yield from own(node.children)
                else:
                    yield node
        tabs = [*store.favorites, *(t for space in store.spaces for t in own(space.pinned)),
                *(t for space in store.spaces for t in own(space.today))]
        self.favicons.want([u for u in (t.home_url or t.url for t in tabs) if not is_dev_url(u)])

    def _icon_fetched(self, home):
        host = QUrl(home).host().lower().removeprefix("www.")
        for tab in self.store.all_tabs():
            if QUrl(tab.url).host().lower().removeprefix("www.") == host:
                tab.changed.emit()

    def _on_icon(self, tab, icon):
        storage.save_favicon(tab.url, icon)
        tab.changed.emit()

    def _on_load_started(self, tab):
        tab.loading = True
        if tab is self.current_tab:
            self.content.set_progress(5)

    def _note_scroll(self, tab):
        """Where its page is scrolled to, kept with the tab: a page that sleeps, or comes back after a
        restart, reloads at the top otherwise."""
        view = tab.view
        if not isinstance(view, QWebEngineView) or not tab.url:
            return
        if view.page().lifecycleState() != QWebEnginePage.LifecycleState.Active:
            return  # (asleep: what it noted before it slept still stands)
        at = view.page().scrollPosition()
        tab.scroll = {"url": view.url().toString(), "x": round(at.x()), "y": round(at.y())} if at.y() > 40 or at.x() > 40 else None

    def _put_scroll_back(self, tab):
        scroll, view = tab.scroll, tab.view
        if not scroll or not isinstance(view, QWebEngineView):
            return
        tab.scroll = None  # (once, on the first load: after that it's wherever you scroll it)
        if view.url().toString() != scroll.get("url"):
            return  # it ended up somewhere else
        x, y = int(scroll.get("x", 0)), int(scroll.get("y", 0))
        page = view.page()
        if page.scrollPosition().y() > 40:
            return  # it scrolled itself (an anchor, the site's own restore): leave it
        script = f"window.scrollTo({x}, {y})"
        page.runJavaScript(script)
        # A page that grows as it loads (images, feeds) may not be tall enough yet: once more, shortly
        QTimer.singleShot(450, lambda v=view: not sip.isdeleted(v) and v.page().scrollPosition().y() < y - 40
                          and v.page().runJavaScript(script))

    def _on_load_finished(self, tab, ok):
        tab.loading = False
        if ok and tab.scroll:
            self._put_scroll_back(tab)
        if ok:
            self.store.record_history(tab.url, tab.title)
        self.page_index.page_loaded(tab, ok)
        if tab is self.current_tab:
            self.content.set_progress(100)
            self.sidebar.update_nav()
        self.emit("load_finished", tab, ok)

    def handle_new_window(self, source_tab, request, profile=None):
        Dest = QWebEngineNewWindowRequest.DestinationType
        dest = request.destination()
        if request.requestedUrl().scheme() in ("note", "easel"):
            return  # (a web page doesn't get to open your notes or easels)
        opener = source_tab.view.url().scheme() if source_tab is not None and isinstance(source_tab.view, QWebEngineView) else ""
        if request.requestedUrl().scheme() in ("pbext", "file") and opener not in ("pbext", "file"):
            return  # (nor an extension's own pages or your files: kept as a tab, it'd load later as if you'd opened it)
        # The new page must share the opener's profile (and so its logins)
        if source_tab is not None and isinstance(source_tab.view, QWebEngineView):
            profile = source_tab.view.page().profile()
        profile = profile or self.profile
        if dest == Dest.InNewDialog:
            # window.open() popups (sign-in flows etc.) go to a Little window
            self._track(LittleWindow(self, request=request, profile=profile)).show()
            return
        loc = source_tab is not None and self.store.locate(source_tab)
        if (loc and loc[0] in (PINNED, FAVORITES) and dest in (Dest.InNewTab, Dest.InNewWindow)
                and self.store.settings.get("peek_enabled", True)):
            # Arc's Peek: links out of pinned tabs and favorites float over the page
            self.peek.open(profile, request=request)
            return
        tab = Tab(request.requestedUrl().toString())
        # In the opener's Space (it may be a page in another Space, loading in the background)
        located = self.store.locate(source_tab) if source_tab is not None else None
        space = located[1] if located and located[1] is not None else self.store.active_space
        self.store.add_tab(tab, TODAY, space, 0)
        tab.view = self.create_view(profile)
        self._wire(tab, tab.view)
        self.content.stack.addWidget(tab.view)
        request.openIn(tab.view.page())
        if dest != Dest.InNewBackgroundTab:
            self.activate_tab(tab)

    def adopt_view(self, view):
        """Take a live view from a Little window and make it a Today tab."""
        tab = Tab(view.url().toString(), view.title())
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        if view.page().profile() is not self.profile_for_tab(tab):
            # This Space signs in with another profile: the page opens again there, rather than its
            # sign-ins landing in the wrong one
            view.setParent(None)
            view.deleteLater()
            self.activate_tab(tab)
        else:
            tab.view = view
            self._wire(tab, view)
            self.content.stack.addWidget(view)
            self.activate_tab(tab)
        self.raise_()
        self.activateWindow()

    def _track(self, window):
        self._extra_windows.append(window)
        window.destroyed.connect(lambda: self._extra_windows.remove(window) if window in self._extra_windows else None)
        return window

    def _on_fullscreen(self, request, view=None):
        if request.toggleOn() and view is not None and not view.isVisible():
            request.reject()  # (only the page you're looking at: one in the background can't take over the screen)
            return
        request.accept()
        self._fullscreen_view = view if request.toggleOn() else None
        self._set_fullscreen(request.toggleOn())
        if request.toggleOn():
            # Said every time, so a page can't pass off its own drawing of a browser (or a password box) as real
            host = display_host(request.origin().toString()) or "This page"
            self.toast(f"{host} is full screen. Press Esc to leave.")

    def _leave_page_fullscreen(self):
        view = getattr(self, "_fullscreen_view", None)
        if view is not None and not sip.isdeleted(view):
            view.page().triggerAction(QWebEnginePage.WebAction.ExitFullScreen)
        else:
            self._set_fullscreen(False)

    def _set_fullscreen(self, on):
        self._fullscreen = on
        if self._fullscreen:
            # Whether the sidebar is meant to be there (the hidden one, slid out over the page, isn't)
            self._sidebar_before_fullscreen = self.store.settings.get("sidebar_visible", True)
            if self.floating_sidebar.active:
                self.sidebar.hide()
                self._dock_sidebar()
            self.sidebar.hide()
            self.showFullScreen()
        else:
            self.sidebar.setVisible(getattr(self, "_sidebar_before_fullscreen", True))
            self.showNormal()
        self.content.set_rounded(not self._fullscreen)
        self._update_content_margins()

    def _on_download(self, download):
        if download.isSavePageDownload():  # Save Page As: you already chose where it goes
            download.accept()
            download.isFinishedChanged.connect(lambda: self.toast(
                f"Saved {download.downloadFileName()}"
                if download.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted
                else "Couldn't save the page"))
            return
        if not self._download_allowed(download):
            download.cancel()
            return
        # Same "Save to" folder as yt-dlp downloads (Download Settings), ~/Downloads by default
        download.setDownloadDirectory(ytdlp_settings(self.store)["folder"])
        download.accept()
        item = BrowserDownload(download)
        item.private = self.private.is_private_download(download)  # noted now: the session forgets it when done
        self.downloads_window.add(item)
        download.isFinishedChanged.connect(lambda: self._download_finished(item, download))
        self.toast(f"Downloading {download.downloadFileName()}…  (⌥⌘L shows downloads)")

    def _download_allowed(self, download) -> bool:
        """Whether a page's download goes ahead. Not from a page that isn't in a tab or window (Browse for Me's and
        Live Folders' readers); a file that can run things on your Mac (an app, a script, an installer) asks first; and so
        does a page's second file within a few seconds, so a page can't fill your Downloads folder."""
        page = download.page()
        view = QWebEngineView.forPage(page) if page is not None else None
        if page is not None and view is None:  # (a tab in the background is still yours: ⌘-clicking a file's link)
            print(f"Refused a download from a page with no window: {download.url().toString()[:120]}")
            return False
        name = download.downloadFileName()
        host = display_host(download.url().toString()) or "A page"
        if downloads_risky(name):
            answer = QMessageBox.question(
                self, "Download File", f"“{name}” can run programs on your Mac. Download it from {host} anyway?\n\n"
                "Only download it if you trust where it came from.",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel)
            return answer == QMessageBox.StandardButton.Save
        now, key = time.monotonic(), id(page)
        recent = [t for t in self._recent_downloads.get(key, []) if now - t < 8]
        self._recent_downloads[key] = recent + [now]
        if recent:
            answer = QMessageBox.question(self, "Download Files", f"{host} wants to download several files. Allow "
                                          f"“{name}”?", QMessageBox.StandardButton.Cancel |
                                          QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel)
            return answer == QMessageBox.StandardButton.Save
        return True

    def _download_finished(self, item, download):
        # Tidy Downloads: Claude gives the file a readable name (not for a private window's: that would
        # send where it came from to Claude)
        private = getattr(item, "private", False)
        if download.state() == download.DownloadState.DownloadCompleted:
            macos.quarantine(item.path)  # (from the internet: Gatekeeper checks it before it opens; Qt doesn't mark it)
        if download.state() == download.DownloadState.DownloadCompleted:
            self.emit("download_done", item, download)  # (Claude's readable names, say)

    # --- Page actions: print, PDF, save, copy as Markdown -----------------------------------------

    def print_page(self, view=None):
        view = view if isinstance(view, QWebEngineView) else self._current_view()
        if view is None or self._printing is not None:
            return
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter  # only loaded when someone prints
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec() != QPrintDialog.DialogCode.Accepted:
            return
        self._printing = (view, printer)  # Qt needs the printer until it's done

        def finished(ok):
            view.printFinished.disconnect(finished)
            view.destroyed.disconnect(closed)
            self._printing = None
            self.toast("Sent to the printer" if ok else "Couldn't print this page")

        def closed(_=None):  # the tab closed mid-print: printFinished will never come
            self._printing = None
        view.printFinished.connect(finished)
        view.destroyed.connect(closed)
        view.print(printer)

    def _save_dialog(self, title, suffix, kind):
        tab = self.current_tab
        folder = Path(ytdlp_settings(self.store)["folder"])
        name = safe_filename(tab.label if tab else "") + suffix
        path, _ = QFileDialog.getSaveFileName(self, title, str(folder / name), kind)
        return path

    def save_pdf(self):
        view = self._current_view()
        if view is None:
            return
        path = self._save_dialog("Save as PDF", ".pdf", "PDF (*.pdf)")
        if not path:
            return
        from PyQt6.QtCore import QLocale, QMarginsF
        from PyQt6.QtGui import QPageLayout, QPageSize
        metric = QLocale().measurementSystem() == QLocale.MeasurementSystem.MetricSystem
        size = QPageSize(QPageSize.PageSizeId.A4 if metric else QPageSize.PageSizeId.Letter)
        layout = QPageLayout(size, QPageLayout.Orientation.Portrait, QMarginsF(12, 12, 12, 12),
                             QPageLayout.Unit.Millimeter)

        def finished(file, ok):
            view.pdfPrintingFinished.disconnect(finished)
            self.toast(f"Saved {Path(file).name}" if ok else "Couldn't save the PDF")
        view.pdfPrintingFinished.connect(finished)
        view.printToPdf(path, layout)

    def save_page(self):
        view = self._current_view()
        if view is None:
            return
        path = self._save_dialog("Save Page As", ".mhtml", "Web archive (*.mhtml)")
        if path:
            view.page().save(path, QWebEngineDownloadRequest.SavePageFormat.MimeHtmlSaveFormat)

    def copy_markdown_link(self, tab=None):
        tab = tab or self.current_tab
        if tab and tab.url:
            QApplication.clipboard().setText(markdown_link(tab.label, clean_links.for_copy(tab.url, self.store.settings)))
            self.toast("Copied as a Markdown link")

    def copy_links(self, tabs):
        """A Markdown list of links, one per tab: a folder's, Today's, a Space's."""
        tabs = [t for t in tabs if t.url.startswith(("http://", "https://", "file:"))]
        if not tabs:
            self.toast("There are no links to copy")
            return
        settings = self.store.settings
        QApplication.clipboard().setText("\n".join(
            f"- {markdown_link(t.label, clean_links.for_copy(t.url, settings))}" for t in tabs))
        self.toast(f"Copied {len(tabs)} link{'s' if len(tabs) != 1 else ''}")

    def copy_today_links(self):
        self.copy_links(walk_tabs(self.store.active_space.today))

    def copy_space_links(self):
        space = self.store.active_space
        self.copy_links([*walk_tabs(space.pinned), *walk_tabs(space.today)])

    def share_page(self, tab=None):
        """macOS's share menu for the page: AirDrop it to your phone, or send it to Messages, Mail, Notes…"""
        tab = tab or self.current_tab
        if tab is None or not tab.url.startswith(("http://", "https://")):
            self.toast("Open a web page to share it")
            return
        pill = self.sidebar.url_pill
        if pill.isVisible() and pill.window() is self:
            anchor = QRect(pill.mapTo(self, QPoint(0, 0)), pill.size())
        else:  # (the sidebar's hidden: from the top of the page)
            anchor = QRect(self.content.mapTo(self, QPoint(self.content.width() // 2, 0)), QSize(1, 1))
        if not macos.share(self, clean_links.for_copy(tab.url, self.store.settings), anchor):
            self.toast("Sharing isn't available here")

    # --- Screenshots -------------------------------------------------------------------------------

    def copy_screenshot(self):
        view = self._current_view()
        if view is None:
            self.toast("Screenshots work on web pages")
            return
        QApplication.clipboard().setImage(screenshots.visible(view))
        self.toast("Copied a screenshot of what's on screen")

    def save_page_screenshot(self):
        """The whole page, top to bottom, as a PNG in your downloads folder."""
        view, tab = self._current_view(), self.current_tab
        if view is None:
            self.toast("Screenshots work on web pages")
            return
        if self._page_shot is not None and not sip.isdeleted(self._page_shot):
            self.toast("Still taking the last screenshot")
            return
        shot = self._page_shot = screenshots.PageShot(view)
        name = safe_filename(tab.label if tab else "")
        shot.done.connect(lambda image, cut_off: self._save_screenshot(image, cut_off, name))
        shot.failed.connect(self._screenshot_failed)
        self.toast("Taking a screenshot of the whole page…")

    def _screenshot_failed(self, message):
        self._page_shot = None
        self.toast(message)

    def _new_file(self, name, extension) -> Path:
        """A name not taken yet in your downloads folder: "Name.png", else "Name 2.png", and so on."""
        folder = Path(ytdlp_settings(self.store)["folder"]).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # (an unplugged drive, say: saving then fails, and says so)
        path, n = folder / f"{name}.{extension}", 2
        while path.exists():
            path, n = folder / f"{name} {n}.{extension}", n + 1
        return path

    def _save_screenshot(self, image, cut_off, name):
        self._page_shot = None
        path = self._new_file(name, "png")
        if not image.save(str(path), "PNG"):
            self.toast("Couldn't save the screenshot")
            return
        note = " (the top of it: the page is very long)" if cut_off else ""
        self.toast(f"Saved {path.name}{note}", lambda: reveal_in_finder(str(path)), "Show")

    # --- Downloads & yt-dlp --------------------------------------------------------------------

    @property
    def downloads_window(self) -> DownloadsWindow:
        if self._downloads is None:
            self._downloads = DownloadsWindow(self)
        return self._downloads

    def assistant(self, other=False):
        """Who answers questions (features.py: Claude's), or None."""
        claude = self.feature("claude")
        return claude.assistant(other) if claude is not None else None

    def assistant_name(self, ai) -> str:
        claude = self.feature("claude")
        return claude.assistant_name(ai) if claude is not None else ""

    def show_downloads(self, url=""):
        self.downloads_window.open(url)

    def download_video(self, tab=None):
        tab = tab or self.current_tab
        self.show_downloads(tab.url if tab else "")

    def download_settings(self):
        DownloadSettings(self.store, self.theme, self).exec()

    # --- Ad blocking ----------------------------------------------------------------------------

    def apply_dark_pages(self):
        """Dark websites turned on or off (or a site changed): every open page, at once (no reload)."""
        views = [t.view for t in self.store.all_tabs()]
        for window in self.private.windows:  # (private windows' tabs, and Little windows' pages, too)
            views += list(getattr(window, "views", []))
        views += [getattr(window, "view", None) for window in self._extra_windows]
        for view in views:
            if isinstance(view, QWebEngineView) and not sip.isdeleted(view):
                dark_pages.apply(view.page(), self.store.settings)

    def _set_dark_here(self, host, dark):
        dark_pages.set_skipped(self.store.settings, host, not dark)
        self.store.schedule_save()
        self.apply_dark_pages()

    def show_adblock_menu(self, pos):
        tab = self.current_tab
        host = QUrl(tab.url).host() if tab else ""
        blocker = self.adblock
        menu = self._menu()
        if host and self.store.settings.get(dark_pages.SETTING, False):
            dark = menu.addAction(f"Dark Pages on {display_host(tab.url)}")
            dark.setCheckable(True)
            dark.setChecked(dark_pages.wanted(self.store.settings, tab.url))
            dark.triggered.connect(lambda on: self._set_dark_here(host, on))
        if host:
            self._site_permissions_menu(menu, tab)
            count = blocker.count_for(tab.url)
            site = display_host(tab.url)
            header = menu.addAction(f"{count:,} ad{'s' if count != 1 else ''} & tracker{'s' if count != 1 else ''} "
                                    f"blocked on {site}" if count else f"Nothing blocked on {site} yet")
            header.setEnabled(False)
            here = menu.addAction(f"Block on {display_host(tab.url)}")
            here.setCheckable(True)
            here.setChecked(not blocker.is_allowed(host))
            here.setEnabled(blocker.enabled)
            here.triggered.connect(lambda on: self._allow_site(host, not on))
            menu.addSeparator()
        everywhere = menu.addAction("Block Ads & Trackers")
        everywhere.setCheckable(True)
        everywhere.setChecked(blocker.enabled)
        everywhere.triggered.connect(lambda _=False: self.toggle_adblock())
        if host and self.page_index.enabled:
            self._act(menu, "ph.eye-slash", f"Keep {display_host(tab.url)} Out of Page Search",
                      lambda: (self.page_index.skip_site(host),
                               self.toast(f"Pages on {display_host(tab.url)} won't be searchable by their text")))
        banners = menu.addAction("Reject Cookie Banners")
        banners.setCheckable(True)
        banners.setChecked(blocker.reject_cookies)
        banners.triggered.connect(lambda on: (blocker.set_reject_cookies(on),
                                              self.toast("Cookie banners: rejected for you" if on else
                                                         "Cookie banners: left for you to answer")))
        info = menu.addAction(f"{len(blocker.domains):,} domains on the block lists")
        info.setEnabled(False)
        self._act(menu, "ph.arrows-clockwise", "Update Block Lists Now", lambda: (blocker.update_lists(force=True),
                                                          self.toast("Updating block lists…")))
        menu.exec(pos)

    def _site_permissions_menu(self, menu, tab):
        """Site Controls: what this site may use, changeable here."""
        view = tab.view if isinstance(tab.view, QWebEngineView) else None
        origin = origin_key(QUrl(tab.url))
        if view is None or not origin:
            return
        profile = view.page().profile()
        rules = self.permissions.rules_for(profile, origin)
        header = menu.addAction(f"Site Controls · {display_host(tab.url)}")
        header.setEnabled(False)
        if not rules:
            none = menu.addAction("This site hasn't asked for any permissions")
            none.setEnabled(False)
        for label, kind, value in rules:
            sub = self._submenu(menu, KINDS[kind][1], f"{label[0].upper()}{label[1:]}: "
                                f"{'Allowed' if value == 'allow' else 'Blocked'}")
            for text, choice in (("Allow", "allow"), ("Block", "block"), ("Ask Next Time", None)):
                action = sub.addAction(text, lambda c=choice, k=kind: self.permissions.set_rule(profile, origin, k, c))
                action.setCheckable(True)
                action.setChecked(choice == value)
        if rules:
            self._act(menu, "ph.arrow-counter-clockwise", "Reset Permissions for This Site",
                      lambda: (self.permissions.reset_site(profile, origin), self.toast("Permissions reset")))
        menu.addSeparator()

    def _allow_site(self, host, allowed):
        self.adblock.set_allowed(host, allowed)
        self.sidebar.update_shield()
        self.reload()  # the page has already loaded without (or with) its ads
        self.toast(f"Ads allowed on {host.removeprefix('www.')}" if allowed else f"Blocking ads on {host.removeprefix('www.')}")

    def toggle_adblock(self):
        enabled = not self.adblock.enabled
        self.adblock.set_enabled(enabled)
        self.adblock_action.setChecked(enabled)
        self.sidebar.update_shield()
        self.toast("Ad blocking on" if enabled else "Ad blocking off")

    def allow_ads_here(self):
        if self.current_tab and QUrl(self.current_tab.url).host():
            host = QUrl(self.current_tab.url).host()
            self._allow_site(host, not self.adblock.is_allowed(host))

    def toast(self, text, action=None, label="Undo"):
        self.content.show_toast(text, action, label)

    # --- Tabs ------------------------------------------------------------------------------

    def activate_tab(self, tab):
        loc = self.store.locate(tab)
        if loc is None:
            return
        self._kept_from_launch = []
        _, space = loc
        if space is not None and space.id != self.store.active_space_id:
            self._set_active_space(space)
        previous = self.current_tab
        if previous is not tab and previous is not None:
            self._note_scroll(previous)  # (to put it back if it sleeps, or after a restart)
        wanted = self.store.settings.get("tab_previews", True) or self.store.settings.get("ctrl_tab", "recent") == "recent"
        if (previous is not tab and isinstance(previous and previous.view, QWebEngineView) and previous.view.isVisible()
                and wanted):
            # A thumbnail of the page you're leaving, for the ⌃Tab switcher and tab previews
            self.thumbnails[previous.id] = previous.view.grab().scaledToWidth(
                520, Qt.TransformationMode.SmoothTransformation)  # (260 points: sharp in a preview)
            self.thumbnails.move_to_end(previous.id)
            while len(self.thumbnails) > 40:
                self.thumbnails.popitem(last=False)
        if tab.notified:
            tab.notified = False
            tab.changed.emit()
        self.mini_player.release(tab)  # coming back to the tab the mini player was showing
        if self.floating_sidebar.kept:
            self.floating_sidebar.kept = False  # (a tab's open: the hidden sidebar goes when the pointer does)
        view = self._ensure_view(tab)
        self._stamp_on_screen()  # (the tab you're leaving was in use until now, not since you opened it)
        self.current_tab = tab
        tab.last_active = time.time()
        self.store.active_space.active_tab_id = tab.id
        if tab.id in self._mru:
            self._mru.remove(tab.id)
        self._mru.insert(0, tab.id)

        self.content.find_bar.hide()
        split = self.store.split_of(tab)
        for shown in split or [tab]:
            self.mini_player.release(shown)  # a split partner floating in the mini player comes back too
            self._wake(shown)
            self.crashes.tab_shown(shown)  # its page crashed while you were away
        if split:
            for other in split:
                self._ensure_view(other)
            self.content.show_split(split, tab)
        else:
            self.content.show_view(view)
        for shown in split or [tab]:
            self.permissions.flush(shown.view)  # anything it asked for while you weren't looking
        self.external_links.view_shown()  # (a card asking to open an app shows only over its own page)
        self.passkeys.view_shown()  # (and a security key's card)
        self.passwords.view_shown()
        if not self.command_bar.isVisible() and not view.hasFocus():
            view.setFocus()
        self.setWindowTitle(tab.label)
        self.sidebar.update_selection()
        self.sidebar.update_nav()
        self.store.schedule_save()
        self.devmode.update_for_current()
        self.emit("tab_shown", tab)
        if not self.reader.is_open_for(tab):
            self.reader.close_reader()
        if previous is not tab:
            self.mini_player.left_tab(previous)
            self.emit("tab_left", previous)

    def _show_nothing(self):
        self.current_tab = None
        self.content.show_view(None)
        self.reader.close_reader()
        self.devmode.update_for_current()
        self.emit("tab_shown", None)
        self.setWindowTitle(brand.NAME)
        self.sidebar.update_selection()
        self.sidebar.update_nav()
        self._float_if_empty()

    def _float_if_empty(self, tries=3):
        """No tab open and the sidebar hidden: it comes out over the page, as if pointed at, and stays (there's
        nothing to hide it for). Not in full screen, where a hidden sidebar is meant to keep out of the way."""
        if self.current_tab is not None or not self.sidebar.isHidden() or self._fullscreen or self.isFullScreen():
            return
        if not self.isVisible():  # (still starting up: once the window's there)
            if tries:
                QTimer.singleShot(300, lambda: self._float_if_empty(tries - 1))
            return
        self.floating_sidebar.keep(True)

    def _activate_fallback(self):
        visible = self.store.visible_tabs()
        for tab_id in self._mru:
            tab = self.store.find_tab(tab_id)
            if tab in visible and tab.view is not None:
                self.activate_tab(tab)
                return
        self._show_nothing()

    def open_url(self, url, background=False):
        tab = Tab(url)
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        if background:
            self._ensure_view(tab)
        else:
            self.activate_tab(tab)
        return tab

    def navigate_current(self, url):
        if self._current_view() is None:  # nothing open, or a note (which can't load pages)
            self.open_url(url)
            return
        self._ensure_view(self.current_tab, load=False).load(QUrl(url))

    def close_tab(self, tab=None):
        tab = tab or self.current_tab
        loc = tab and self.store.locate(tab)
        if not loc:
            return
        kind, space = loc
        was_current = tab is self.current_tab
        self.tab_preview.dismiss()
        self.store.remove_from_split(tab)
        self._destroy_view(tab)
        self.emit("tab_closed", tab)
        if kind == TODAY:
            self._closed.append((tab.to_dict(), space.id))
            del self._closed[:-self._keep_closed]
            self.store.remove_tab(tab)
        else:
            # Arc: closing a pinned tab or favorite just unloads it and resets it to its home URL
            if tab.home_url and tab.url != tab.home_url:
                tab.url, tab.title = tab.home_url, ""
            tab.changed.emit()
            self.store.schedule_save()
        if was_current:
            self._activate_fallback()
        elif self.content.splitting and self.current_tab is not None:
            self.activate_tab(self.current_tab)  # lay the remaining panes out again
        else:
            self.sidebar.update_selection()

    def _remove_view(self, tab):
        """A tab that's going for good (not just unloaded): its page, and the features hear it's gone."""
        self._destroy_view(tab)
        self.emit("tab_closed", tab)

    def delete_tab(self, tab):
        was_current = tab is self.current_tab
        self.store.remove_from_split(tab)
        self._remove_view(tab)
        self.store.remove_tab(tab)
        if was_current:
            self._activate_fallback()
        else:
            self._relayout_split()

    def _relayout_split(self):
        # A tab in the visible Split View went away: lay the remaining panes out again
        if self.content.splitting and self.current_tab is not None and self.store.locate(self.current_tab):
            self.activate_tab(self.current_tab)

    def reopen_closed_tab(self, index=-1, activate=True):
        """Bring back the last tab you closed (or the one at `index` in the closed list)."""
        if not self._closed:
            return
        data, space_id = self._closed.pop(index)
        tab = Tab.from_dict(data)
        space = self.store.space(space_id) or self.store.active_space
        self.store.add_tab(tab, TODAY, space, 0)
        if activate:
            self.activate_tab(tab)

    def _fill_recently_closed(self, menu):
        menu.clear()
        if not self._closed:
            menu.addAction("Nothing closed yet").setEnabled(False)
            return
        for i in range(len(self._closed) - 1, max(len(self._closed) - 16, -1), -1):
            data = self._closed[i][0]
            url = QUrl(data.get("url", ""))
            where = display_host(url.toString()) + url.path().rstrip("/")  # never loaded: no title, so where it was
            title = (data.get("title") or where or "Untitled").replace("&", "&&")
            icon = storage.favicon(data.get("url", "")) or QIcon()
            entry = self._closed[i]  # by identity: the list may change before the click
            menu.addAction(icon, title[:70], lambda e=entry: e in self._closed and
                           self.reopen_closed_tab(self._closed.index(e)))
        menu.addSeparator()
        menu.addAction("Clear This List", self._closed.clear)

    def _fill_recent_pages(self, menu):
        """The bottom of the History menu, as Safari's: the pages you've been to lately, newest first (a click
        opens one in a new tab). Not in a private window's: those were never kept."""
        for action in menu.actions():
            if action.property(shortcuts.DYNAMIC):
                menu.removeAction(action)
                action.deleteLater()
        pages = self.store.history[:RECENT_PAGES]
        if not pages:
            return
        line = menu.addSeparator()
        line.setProperty(shortcuts.DYNAMIC, True)
        heading = menu.addAction("Recently Visited")
        heading.setEnabled(False)
        heading.setProperty(shortcuts.DYNAMIC, True)
        for entry in pages:
            title = (entry.get("title") or display_host(entry["url"]) or entry["url"]).replace("&", "&&")
            item = menu.addAction(storage.favicon(entry["url"]) or QIcon(), title[:70],
                                  lambda url=entry["url"]: self.open_url(url))
            item.setToolTip(entry["url"])
            item.setProperty(shortcuts.DYNAMIC, True)

    def move_tab(self, node, kind, index=None, folder=None):
        loc = self.store.locate(node)
        self.store.move_tab(node, kind, None if kind == FAVORITES else self.store.active_space, index, folder)
        if isinstance(node, Tab) and loc and loc[0] == TODAY and kind == PINNED:
            self.emit("tab_pinned", node)

    def move_tabs(self, tabs, kind, index=None, folder=None):
        """Several picked tabs dropped together: in their order, where the drop line was."""
        self.sidebar.clear_marks()
        space = None if kind == FAVORITES else self.store.active_space
        dest = folder.children if folder is not None and kind != FAVORITES else self.store.tab_list(kind, space)
        moving = {id(t) for t in tabs}
        # They go in before whatever was under the drop line (that isn't one of them), or at the end
        anchor = next((n for n in dest[index:] if id(n) not in moving), None) if index is not None else None
        for tab in tabs:
            if self.store.locate(tab):
                at = next(i for i, n in enumerate(dest) if n is anchor) if anchor is not None else None
                self.move_tab(tab, kind, at, folder)

    # --- Folders -----------------------------------------------------------------------------

    def toggle_folder(self, folder):
        """Open or close a folder: only its own list is redone (the whole sidebar, Favorites and Space buttons and
        all, took long enough to cost the animation its first frames)."""
        found = self.store.locate(folder)
        if not found or found[1] is not self.store.active_space or self._rebuild_pending \
                or getattr(self.sidebar, "drag_active", False):
            self.store.set_folder_expanded(folder, not folder.expanded)
            return
        kind, space = found
        folder.expanded = not folder.expanded
        self.store.schedule_save()
        section = self.sidebar.pinned if kind == PINNED else self.sidebar.today
        section.animate_changes = True
        section.set_tabs(space.pinned if kind == PINNED else space.today, refresh=False)
        self.sidebar.update_selection()

    def new_folder(self, tab=None):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        folder = self.store.add_folder(self.store.active_space, name.strip())
        if tab is not None:
            self.move_tab(tab, PINNED, None, folder)

    def rename_folder(self, folder):
        name, ok = QInputDialog.getText(self, "Rename Folder", "Folder name:", text=folder.name)
        if ok and name.strip():
            folder.name = name.strip()
            if folder.live:
                folder.live["named"] = True  # (a feed's own title mustn't put the old name back)
            self.store._changed()

    def delete_folder(self, folder):
        tabs = list(walk_tabs(folder.children))
        if tabs:
            answer = QMessageBox.question(self, "Delete Folder",
                                          f"Delete “{folder.name}” and the {len(tabs)} tabs in it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.emit("folder_deleted", folder)
        current_inside = self.current_tab in tabs
        for tab in tabs:
            self._remove_view(tab)
        self.store.remove_tab(folder)
        if current_inside:
            self._activate_fallback()
        else:
            self._relayout_split()

    def show_folder_menu(self, folder, pos):
        if self.first("own_folder_menu", folder, pos):
            return  # (a feature's folder, with a menu of its own: a Live Folder's sub-folder)
        menu = self._menu()
        self._act(menu, "ph.pencil-simple", "Rename…", lambda: self.rename_folder(folder))
        self._act(menu, "ph.folder" if folder.expanded else "ph.folder-open",
                  "Collapse" if folder.expanded else "Expand", lambda: self.toggle_folder(folder))
        self._act(menu, "ph.folder-plus", "New Folder…", self.new_folder)
        self._act(menu, "ph.list-bullets", "Copy Links", lambda: self.copy_links(walk_tabs(folder.children)))
        if not folder.live:
            self._act(menu, "ph.arrows-clockwise", "Reload Tabs", lambda: self.reload_tabs(walk_tabs(folder.children)))
        self.emit("folder_menu", menu, folder)  # (a Live Folder's Refresh Now, Mark All as Seen…)
        menu.addSeparator()
        self._act(menu, "ph.folder-minus", "Ungroup (Keep Tabs)", lambda: self.store.ungroup_folder(folder))
        self._act(menu, "ph.trash", "Delete Folder…", lambda: self.delete_folder(folder))
        menu.exec(pos)

    def toggle_pin(self, tab=None):
        tab = tab or self.current_tab
        loc = tab and self.store.locate(tab)
        if not loc:
            return
        space = self.store.active_space
        if loc[0] == TODAY:
            self.store.move_tab(tab, PINNED, loc[1], len(loc[1].pinned))
            self.toast("Pinned")
            self.emit("tab_pinned", tab)
        else:
            self.store.move_tab(tab, TODAY, loc[1] or space, 0)
            self.toast("Unpinned")

    def toggle_favorite(self, tab=None):
        tab = tab or self.current_tab
        loc = tab and self.store.locate(tab)
        if not loc:
            return
        if loc[0] == FAVORITES:
            self.store.move_tab(tab, PINNED, self.store.active_space)
        else:
            self.store.move_tab(tab, FAVORITES)

    def move_to_space(self, tab, space):
        located = self.store.locate(tab)
        if not located:
            return  # (closed while its menu was open)
        kind, _ = located
        was_current = tab is self.current_tab
        in_split = bool(self.store.split_of(tab))
        self.store.remove_from_split(tab)  # (a Split View doesn't span two Spaces)
        if isinstance(tab.view, QWebEngineView) and tab.view.page().profile() is not self.profile_named(space.profile):
            self._destroy_view(tab)  # reopen it with the other Space's profile
        self.store.move_tab(tab, PINNED if kind == PINNED else TODAY, space, 0 if kind == TODAY else None)
        if was_current:
            self.switch_space(space)
            self.activate_tab(tab)
        elif in_split:
            self._relayout_split()  # (the panes left behind)

    def rename_tab(self, tab):
        name, ok = QInputDialog.getText(self, "Rename Tab", "Name (leave blank to use the page title):",
                                        text=tab.custom_title or "")
        if ok:
            tab.custom_title = name.strip() or None
            tab.changed.emit()
            self.store.schedule_save()

    def visible_tab_step(self, step):
        tabs = self.store.visible_tabs()
        if not tabs:
            return
        i = tabs.index(self.current_tab) if self.current_tab in tabs else -1
        self.activate_tab(tabs[(i + step) % len(tabs)])

    def select_tab_number(self, n):
        tabs = self.store.visible_tabs()
        if tabs:
            self.activate_tab(tabs[-1] if n == 9 else tabs[min(n, len(tabs)) - 1])

    def clear_today(self):
        space = self.store.active_space
        tabs = list(walk_tabs(space.today))
        count = len(tabs)
        current_cleared = self.current_tab in tabs
        shape = self.store.today_shape(space)  # (to put it all back, folders and all, if you Undo)
        current = self.current_tab
        archived = []
        for tab in tabs:
            self._remove_view(tab)
            entry = self.store.archive_tab(tab, space)
            if entry is not None:  # (exactly these: an empty tab adds none, so a count would take older ones)
                archived.append(entry)
        if current_cleared:
            self._activate_fallback()
        else:
            self._relayout_split()
        if count:
            self.toast(f"Archived {count} tab{'s' if count != 1 else ''}",
                       lambda: self._undo_clear(space, shape, archived, current if current_cleared else None))

    def _undo_clear(self, space, shape, archived, current):
        """Clear, taken back: Today as it was (anything opened since stays on top), out of the archive."""
        self.store.restore_today(space, shape, archived)
        if current is not None and space is self.store.active_space:
            self.activate_tab(current)
        self.toast("Brought your tabs back")

    def archive_stale_tabs(self):
        on_screen = self.store.split_of(self.current_tab) or [self.current_tab] if self.current_tab else []
        on_screen = [*on_screen, *getattr(self, "_kept_from_launch", [])]  # (what you were on before a launch)
        # Nor anything playing sound, or kept awake (a dashboard, a chat): they're in use without being looked at
        busy = [t for t in self.store.all_tabs() if t.awake or (isinstance(t.view, QWebEngineView)
                                                                and t.view.page().recentlyAudible())]
        stale = self.store.stale_today_tabs(exclude=[*on_screen, *busy])
        for tab, space in stale:
            self._remove_view(tab)
            self.store.archive_tab(tab, space)
        if stale:
            self._relayout_split()
            self.toast(f"Archived {len(stale)} inactive tab{'s' if len(stale) != 1 else ''}")

    def show_tab_menu(self, tab, pos):
        kind, space = self.store.locate(tab)
        menu = self._menu()
        self._act(menu, "ph.link", "Copy Link", lambda: self.copy_url(tab))
        self._act(menu, "ph.link-simple", "Copy as Markdown Link", lambda: self.copy_markdown_link(tab))
        if tab.url.startswith(("http://", "https://")):
            self._act(menu, "ph.share", "Share…", lambda: self.share_page(tab))
        if not tab.url.startswith(("note:", "easel:")):  # two editors on one file would fight over it
            self._act(menu, "ph.copy", "Duplicate", lambda: self.open_url(tab.url))
        self._act(menu, "ph.download-simple", "Download Video…", lambda: self.download_video(tab))
        split = self.store.split_of(tab)
        if split:
            self._act(menu, "ph.square-half", "Remove from Split View", lambda: self.remove_from_split(tab))
        elif self.current_tab is not None and tab is not self.current_tab:
            self._act(menu, "ph.columns", "Open in Split View", lambda: self.add_to_split(tab))
        audible, muted = self.tab_audio(tab)
        if audible or muted:
            self._act(menu, "ph.speaker-slash" if not muted else "ph.speaker-high",
                      "Unmute Tab" if muted else "Mute Tab", lambda: self.toggle_mute(tab))
        if self.others_audible(tab):
            self._act(menu, "ph.speaker-x", "Mute Other Tabs", lambda: self.mute_other_tabs(tab))
        self._act(menu, "ph.moon-stars" if tab.awake else "ph.sun", "Let Tab Sleep" if tab.awake else "Keep Tab Awake",
                  lambda: self.toggle_awake(tab))
        menu.addSeparator()
        if kind == TODAY:
            self._act(menu, "ph.push-pin", "Pin Tab", lambda: self.toggle_pin(tab))
        if kind == PINNED:
            self._act(menu, "ph.push-pin-slash", "Unpin Tab", lambda: self.toggle_pin(tab))
        self._act(menu, "ph.star", "Remove from Favorites" if kind == FAVORITES else "Add to Favorites",
                  lambda: self.toggle_favorite(tab))
        self.emit("tab_menu", menu, tab, kind)
        if kind != TODAY:
            self._act(menu, "ph.pencil-simple", "Rename…", lambda: self.rename_tab(tab))
            if tab.home_url and tab.url != tab.home_url:
                self._act(menu, "ph.arrow-u-up-left", "Go Back to Pinned URL", lambda: self.go_home(tab))
        if kind != FAVORITES:
            folders = [f for f in walk_folders(self.store.active_space.pinned) if not f.live]  # feeds fill those
            into = self._submenu(menu, "ph.folder", "Move to Folder")
            for folder in folders:
                if folder is not self.store.folder_of(tab):
                    into.addAction(folder.name, lambda f=folder: self.move_tab(tab, PINNED, None, f))
            if folders:
                into.addSeparator()
            self._act(into, "ph.folder-plus", "New Folder…", lambda: self.new_folder(tab))
        others = [s for s in self.store.spaces if s is not space]
        if kind != FAVORITES and others:
            move = self._submenu(menu, "ph.arrows-left-right", "Move to Space")
            for other in others:
                move.addAction(space_icon(other, self.theme.panel_text, 16), other.name,
                               lambda s=other: self.move_to_space(tab, s))
        menu.addSeparator()
        if kind == TODAY:
            self._act(menu, "ph.x-circle", "Close Tab", lambda: self.close_tab(tab))
            below = self._tabs_below(tab, space)
            if below:
                self._act(menu, "ph.arrow-line-down", "Close Tabs Below", lambda: self.close_tabs(below))
        else:
            if tab.view is not None:
                self._act(menu, "ph.minus-circle", "Unload Tab", lambda: self.close_tab(tab))
            self._act(menu, "ph.trash", "Delete", lambda: self.delete_tab(tab))
        menu.exec(pos)

    def _tabs_below(self, tab, space):
        today = list(walk_tabs(space.today))
        index = next((i for i, t in enumerate(today) if t is tab), None)
        return today[index + 1:] if index is not None else []

    def show_tabs_menu(self, tabs, pos):
        """Right-click on several tabs picked with ⌘-click or ⇧-click."""
        tabs = [t for t in tabs if self.store.locate(t)]
        menu = self._menu()
        header = menu.addAction(f"{len(tabs)} Tabs")
        header.setEnabled(False)
        if len(tabs) <= MAX_PANES:
            self._act(menu, "ph.columns", "Open in Split View", lambda: self.split_tabs(tabs))
        self._act(menu, "ph.list-bullets", "Copy Links", lambda: self.copy_links(tabs))
        if self._located(tabs, TODAY):
            self._act(menu, "ph.push-pin", "Pin Tabs", lambda: self._for_marked(
                lambda: [self.move_tab(t, PINNED) for t in self._located(tabs, TODAY)]))
        self._act(menu, "ph.folder-plus", "New Folder with These Tabs…", lambda: self.folder_from(tabs))
        others = [s for s in self.store.spaces if s is not self.store.active_space]
        if others:
            move = self._submenu(menu, "ph.arrows-left-right", "Move to Space")
            for other in others:
                move.addAction(space_icon(other, self.theme.panel_text, 16), other.name,
                               lambda s=other: self._for_marked(lambda: self.move_tabs_to_space(tabs, s)))
        menu.addSeparator()
        self._act(menu, "ph.x-circle", f"Close {len(tabs)} Tabs", lambda: self.close_tabs(tabs))
        menu.exec(pos)

    def _located(self, tabs, kind=None):
        """The tabs that are still there (a menu runs its own event loop: one may close meanwhile)."""
        return [t for t in tabs if (loc := self.store.locate(t)) and (kind is None or loc[0] == kind)]

    def move_tabs_to_space(self, tabs, space):
        # Today tabs each go on top, so the last first; pinned ones go on the end, so in order
        for tab in [*reversed(self._located(tabs, TODAY)), *self._located(tabs, PINNED)]:
            self.move_to_space(tab, space)

    def _for_marked(self, action):
        self.sidebar.clear_marks()
        action()

    def close_tabs(self, tabs):
        """Close (or, for pinned tabs, unload) several tabs; Today's come back with Undo, in their folders."""
        self.sidebar.clear_marks()
        tabs = [t for t in tabs if self.store.locate(t)]
        space = self.store.active_space
        place = {id(t): i for i, t in enumerate(walk_tabs(space.today))}
        paths = {id(t): node_path(space.today, t, leaving=tabs) for t in tabs}
        closed = []
        self._keep_closed = max(30, len(tabs))  # (Undo brings back all of them, however many)
        for tab in sorted(tabs, key=lambda t: t is self.current_tab):  # yours last: one move to the next
            today = self.store.locate(tab)[0] == TODAY
            self.close_tab(tab)
            if today and paths[id(tab)]:
                closed.append((place.get(id(tab), 0), self._closed[-1], paths[id(tab)]))
        closed = [(entry, path) for _, entry, path in sorted(closed, key=lambda c: c[0])]  # (in sidebar order)
        if closed:
            self.toast(f"Closed {len(closed)} tab{'s' if len(closed) != 1 else ''}",
                       lambda: self._reopen_entries(space.id, closed))

    def _reopen_entries(self, space_id, entries):
        """Undo for close_tabs: each tab back where it was, folders and all."""
        space = self.store.space(space_id)
        for entry, path in entries:  # (top down, so each lands where it was)
            if entry not in self._closed:
                continue  # (brought back already, with ⇧⌘T)
            if space is None:  # the Space has gone since: back like ⇧⌘T, on top of the one you're in
                self.reopen_closed_tab(self._closed.index(entry), activate=False)
                continue
            self._closed.remove(entry)
            self.store.put_back(space, path, Tab.from_dict(entry[0]))
        self._keep_closed = 30
        today = (space or self.store.active_space).today
        if self.current_tab is None and entries:  # (you'd closed everything)
            first = next(iter(walk_tabs(today)), None)
            if first is not None:
                self.activate_tab(first)

    def split_tabs(self, tabs):
        self.sidebar.clear_marks()
        tabs = self._located(tabs)
        if not tabs:
            return
        base, rest = tabs[0], tabs[1:]

        def change():
            if not all([self.store.add_to_split(base, tab, MAX_PANES) for tab in rest]):
                self.toast(f"Split View holds up to {MAX_PANES} tabs")
            self.activate_tab(base)
        motion.crossfade(self.centralWidget(), self.content, change)

    def folder_from(self, tabs):
        self.sidebar.clear_marks()
        name, ok = QInputDialog.getText(self, "New Folder", f"Name for a folder of {len(tabs)} tabs:")
        if not ok or not name.strip():
            return
        folder = self.store.add_folder(self.store.active_space, name.strip())
        for tab in tabs:
            if self.store.locate(tab):
                self.move_tab(tab, PINNED, None, folder)

    def go_home(self, tab):
        """Back to the page a pinned tab or favorite was pinned at."""
        self.activate_tab(tab)
        if tab.view is not None and hasattr(tab.view, "load"):
            tab.view.load(QUrl(tab.home_url))

    # --- Current-page actions -----------------------------------------------------------------

    def _current_view(self) -> QWebEngineView | None:
        """The current tab's web page (None for notes and empty tabs)."""
        view = self.current_tab.view if self.current_tab else None
        return view if isinstance(view, QWebEngineView) else None

    def focus_current(self):
        if self.current_tab is not None and self.current_tab.view is not None:
            self.current_tab.view.setFocus()

    def go_back(self):
        if view := self._current_view():
            view.back()

    def go_forward(self):
        if view := self._current_view():
            view.forward()

    def reload(self):
        if view := self._current_view():
            view.reload()

    def hard_reload(self):
        if view := self._current_view():
            view.page().triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)

    def zoom(self, direction, view=None):
        """⌘+ / ⌘- / ⌘0. The level is remembered for the site, so it opens that way from now on."""
        view = view if isinstance(view, QWebEngineView) else self._current_view()
        if not view:
            return
        factor = step_zoom(view.zoomFactor(), direction)
        if factor is None:
            return
        view.setZoomFactor(factor)
        remember_zoom(self.store.settings, view.url(), factor)
        view.zoom_site = zoom_key(view.url())
        self.store.schedule_save()
        self.sidebar.update_zoom()
        site = zoom_key(view.url())
        self.toast(f"Zoom {round(factor * 100)}%" + (f" for {site.split(':')[0]}" if site else ""))

    def _apply_site_zoom(self, view):
        """A page arrived at a (new) site: use that site's remembered zoom."""
        site = zoom_key(view.url())
        target = zoom_for(self.store.settings, view.url())
        if abs(view.zoomFactor() - target) > 0.001:  # Qt keeps a view's zoom across sites; we don't
            view.setZoomFactor(target)
        view.zoom_site = site
        if view is self._current_view():
            self.sidebar.update_zoom()

    def copy_url(self, tab=None):
        tab = tab or self.current_tab
        if tab and tab.url:
            QGuiApplication.clipboard().setText(clean_links.for_copy(tab.url, self.store.settings))
            self.toast("Copied URL to clipboard")

    def open_find(self):
        if self._current_view():
            self.content.find_bar.open()

    def find_in_page(self, text, backward=False):
        if view := self._current_view():
            flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
            view.page().findText(text, flags)

    def show_whats_new(self):
        from .whats_new import WhatsNew
        self._whats_new = WhatsNew(self)
        self._whats_new.show()

    def show_crash_logs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(crash_logs_dir())))

    def send_feedback(self, crashed=False):
        from .feedback import FeedbackWindow
        self._feedback = FeedbackWindow(self, crashed)
        self._feedback.show()

    def toggle_reader(self):
        if self.reader.isVisible():
            self.reader.close_reader()
            return
        view = self._current_view()
        if view is None:
            return
        self.reader.open_for(self.current_tab, view)

    def toggle_dev_console(self):
        view = self._current_view()
        if view is None or not is_dev_url(view.url().toString()):
            self.toast("The console drawer is for pages you're building (localhost, .test, files). "
                       "Use Developer Tools (⌥⌘I) here.")
            return
        self.devmode.toggle_drawer()

    def open_devtools(self):
        view = self._current_view()
        if not view:
            return
        dev = QWebEngineView()
        dev.setPage(QWebEnginePage(self.profile, dev))
        dev.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dev.setWindowTitle(f"Developer Tools — {self.current_tab.label}")
        dev.resize(1000, 650)
        view.page().setDevToolsPage(dev.page())
        self._track(dev).show()

    def open_command_bar(self, mode="new"):
        text = self.current_tab.url if mode == "current" and self.current_tab else ""
        self.command_bar.open(mode, text)

    def new_little_window(self, url=None):
        window = self._track(LittleWindow(self, url=url))
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    # --- Links from other apps -------------------------------------------------------------------

    def open_external(self, url):
        """A link clicked in another app (or passed on the command line). Like Arc, it opens in a
        Little window by default; promote it with ⌘O if you want to keep it."""
        space = self.store.space(route(self.store.settings.get("air_traffic", []), url)) if url else None
        if not url:
            self.bring_to_front()
        elif space is not None:  # Air Traffic Control sends it to a particular Space
            tab = Tab(url)
            self.store.add_tab(tab, TODAY, space, 0)
            self.activate_tab(tab)
            self.bring_to_front()
            self.toast(f"Opened in {space.name}")
        elif self.store.settings.get("external_links", "little") == "little":
            self.new_little_window(url)
        else:
            self.open_url(url)
            self.bring_to_front()

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        macos.activate_app()  # (Qt's own way doesn't make the app active on macOS 14+)
        self.activateWindow()

    def _set_external_links(self, mode):
        self.store.settings["external_links"] = mode
        self.store.schedule_save()

    def set_as_default_browser(self):
        if not macos.is_built_app():
            QMessageBox.information(
                self, "Set as Default Browser",
                "macOS only lets an installed app be the default browser.\n\n"
                "Build it once with:   ./run.sh --build-app\n\n"
                f"then open {brand.NAME} from your Applications folder and choose this again.")
            return
        if macos.set_default_browser(macos.bundle_identifier()):  # (Eclipse, or Eclipse Dev: whichever this is)
            self.toast("macOS will ask you to confirm the change")
        else:
            self.toast("macOS didn't accept the change")

    def toggle_sidebar(self):
        if self.floating_sidebar.active:
            self._dock_sidebar()
            visible = True
        else:
            visible = not self.sidebar.isVisible()
        root = self.centralWidget()
        animate = self.isVisible() and visible != self.sidebar.isVisible() and not self._fullscreen
        if animate:
            # The strip the sidebar takes up, as it looks now…
            width = self.store.settings.get("sidebar_width", SIDEBAR_WIDTH)
            left = self.splitter.width() - width if self.sidebar_on_right else 0
            rect = QRect(self.splitter.mapTo(root, QPoint(left, 0)), QSize(width, self.splitter.height()))
            first = root.grab(rect)
        self.sidebar.setVisible(visible)
        self.store.settings["sidebar_visible"] = visible
        self.store.schedule_save()
        self._update_content_margins()
        if animate:
            # …and after: then the sidebar slides over (or out of) the page that's there now
            if not visible:  # (over the page itself: see SidebarSlide)
                motion.SidebarSlide(root, rect, first, None, showing=False, right=self.sidebar_on_right,
                                    done=self._float_if_empty)  # (no tab open: it comes straight back, floating)
                return
            # Coming back it has to be laid out before it can be pictured: hold the old look a moment
            hold = motion.SidebarSlide(root, rect, first, first, showing=False, hold=True, right=self.sidebar_on_right)

            def slide_in():
                hold.cover.hide()
                arrived = root.grab(rect)
                hold.cover.deleteLater()
                motion.SidebarSlide(root, rect, arrived, first, showing=True, right=self.sidebar_on_right)
            QTimer.singleShot(0, slide_in)

    # --- Claude features (Arc Max) -----------------------------------------------------------

    def open_library(self, section="history"):
        if self._library is None:
            self._library = LibraryWindow(self)
        self._library.open(section)

    def open_settings(self, page="general"):
        if self._settings is None:
            self._settings = SettingsWindow(self)
        self._settings.open(page)

    def _on_link_hovered(self, url):
        self.content.show_link(url)
        self._hovered_link = url
        card = self.content.preview_card
        if url and url == card.url:
            card.keep()
        else:
            card.schedule_hide()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.KeyPress and not event.isAutoRepeat():
            # A preview needs Shift tapped on its own, so typing a capital letter while
            # the mouse happens to rest on a link doesn't trigger one
            self._shift_alone = event.key() == Qt.Key.Key_Shift
            zapping = getattr(self.feature("boosts"), "zap_tab", None) is not None  # (Esc is Zap's then)
            if event.key() == Qt.Key.Key_Escape and self._fullscreen:
                self._leave_page_fullscreen()  # (always, as the notice says: a page can't keep you in)
                return True
            if event.key() == Qt.Key.Key_Escape and self.peek.isVisible() and not zapping:
                self.peek.close_peek()
                return True
            if (event.key() == Qt.Key.Key_Escape and self.sidebar.marked and QApplication.activePopupWidget() is None
                    and QApplication.activeModalWidget() is None):
                # Esc stops picking tabs; it's only kept from going further when it was meant for the
                # sidebar (in a page, the command bar or the find bar it still does its own job too)
                self.sidebar.clear_marks()
                if isinstance(obj, QWidget) and (obj is self.sidebar or self.sidebar.isAncestorOf(obj)):
                    return True
        elif kind == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Shift and not event.isAutoRepeat():
            if self._shift_alone and self._hovered_link:
                self.emit("preview_link", self._hovered_link)  # (a summary of where it goes)
            self._shift_alone = False
        elif kind == QEvent.Type.MouseButtonDblClick and obj is self.splitter.handle(1):
            self.reset_sidebar_width()
            return True
        elif kind == QEvent.Type.MouseButtonPress and self.content.splitting and isinstance(obj, QWidget):
            # A click anywhere in a Split View pane makes that tab current (focus alone isn't
            # reliable: it doesn't move when the window isn't the active one)
            pane = self.content.split_view.pane_containing(obj)
            if pane is not None and pane.tab is not self.current_tab:
                QTimer.singleShot(0, lambda: self.activate_tab(pane.tab))
        elif kind == QEvent.Type.MouseMove and not self.sidebar.isVisible() and not self._fullscreen:
            # Arc: with the sidebar hidden, touching its edge of the window slides it out
            pos = self.mapFromGlobal(QCursor.pos())
            from_edge = self.width() - pos.x() if self.sidebar_on_right else pos.x()
            if 0 <= from_edge <= FLOAT_EDGE and 0 <= pos.y() <= self.height() and self.isActiveWindow():
                self._reveal_sidebar()
        return False

    # --- Hover-to-reveal sidebar ---------------------------------------------------------------

    def _reveal_sidebar(self):
        self.floating_sidebar.edge_touched()  # (floating_sidebar.py: sliding out, staying, going back)

    def _float_sidebar(self):
        """Take the sidebar out of the layout to float over the page."""
        self.sidebar.setParent(self.centralWidget())
        self.sidebar.setProperty("floating", True)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        self._sidebar_margins()

    def _place_floating_sidebar(self):
        self.floating_sidebar.place()

    def sidebar_mouse_left(self):
        pass  # (the floating sidebar watches the pointer itself)

    def _dock_sidebar(self):
        # Put it back in its normal place in the layout (hidden or shown by the caller)
        self.floating_sidebar.finish()
        self.sidebar.setProperty("floating", False)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        self._sidebar_margins()
        self.splitter.insertWidget(self._sidebar_index, self.sidebar)
        self._set_sidebar_size(self.store.settings.get("sidebar_width", SIDEBAR_WIDTH))
        self._dress_handle()
        self.place_traffic_lights()
        self.sidebar.align_with_traffic_lights()

    # --- Tidy Tabs (Arc Max) ----------------------------------------------------------------------

    # --- Tab sleeping ----------------------------------------------------------------------------

    def is_sleeping(self, tab) -> bool:
        view = tab.view
        return (isinstance(view, QWebEngineView)
                and view.page().lifecycleState() == QWebEnginePage.LifecycleState.Discarded)

    def _wake(self, tab):
        if self.is_sleeping(tab):
            tab.view.page().setLifecycleState(QWebEnginePage.LifecycleState.Active)  # reloads the page

    def sleep_idle_tabs(self, force=False):
        """Discard background pages nobody has looked at for a while, freeing their memory.
        They reload when clicked. Never: the current tab, visible split panes, or anything playing audio."""
        minutes = self.store.settings.get("sleep_after_minutes", DEFAULT_SLEEP_MINUTES)
        if not minutes and not force:
            return 0
        cutoff = time.time() - minutes * 60
        keep_awake = self.store.split_of(self.current_tab) or [self.current_tab] if self.current_tab else []
        slept = 0
        for tab in self.store.all_tabs():
            view = tab.view
            if not isinstance(view, QWebEngineView) or any(tab is t for t in keep_awake) or view.isVisible():
                continue
            if tab.awake:  # you asked for it to stay awake
                continue
            page = view.page()
            if page.lifecycleState() != QWebEnginePage.LifecycleState.Active or page.recentlyAudible():
                continue
            if self.permissions.is_capturing(view):  # on a call, or sharing the screen
                continue
            notifier = page.profile().queryPermission(QUrl(origin_key(QUrl(tab.url))),
                                                       QWebEnginePermission.PermissionType.Notifications)
            if notifier.state() == QWebEnginePermission.State.Granted:  # asleep, it couldn't notify you
                continue
            if force or tab.last_active < cutoff:
                self._note_scroll(tab)
                page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)
                slept += 1
        return slept

    def sleep_other_tabs(self):
        slept = self.sleep_idle_tabs(force=True)
        self.toast(f"Put {slept} tab{'s' if slept != 1 else ''} to sleep" if slept else "No other tabs are awake")

    # --- Split View ------------------------------------------------------------------------------

    def add_to_split(self, tab, base=None):
        base = base or self.current_tab
        if base is None or tab is base:
            self.activate_tab(tab)
            return
        def change():
            if not self.store.add_to_split(base, tab, MAX_PANES):
                self.toast(f"Split View holds up to {MAX_PANES} tabs")
                return
            self.activate_tab(tab)
        motion.crossfade(self.centralWidget(), self.content, change)  # (the panes settle in, not jump)

    def open_in_split(self, url):
        base = self.current_tab
        tab = Tab(url)
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        self.add_to_split(tab, base)

    def remove_from_split(self, tab):
        def change():
            self.store.remove_from_split(tab)
            current = self.current_tab
            if current is not None:
                self.activate_tab(tab if tab is current else current)
        motion.crossfade(self.centralWidget(), self.content, change)

    def separate_split(self):
        if self.current_tab is not None and self.store.split_of(self.current_tab):
            def change():
                self.store.separate_split(self.current_tab)
                self.activate_tab(self.current_tab)
            motion.crossfade(self.centralWidget(), self.content, change)

    def _on_focus_changed(self, old, new):
        # Clicking into another pane of a Split View makes that tab the current one
        if new is None or not self.content.splitting:
            return
        pane = self.content.split_view.pane_containing(new)
        if pane is not None and pane.tab is not self.current_tab:
            self.activate_tab(pane.tab)

    # --- Browse for Me -----------------------------------------------------------------------------

    def _stop_watching(self, slot):
        try:
            self.store.structure_changed.disconnect(slot)
        except TypeError:
            pass  # (already done)

    def _on_console(self, tab, message):
        """A page said something on the console that might be for us (Zap's picks, the store's Add button)."""
        for feature in list(self.features.values()):
            if feature.console_message(tab, message):
                return

    def toggle_peek(self):
        enabled = not self.store.settings.get("peek_enabled", True)
        self.store.settings["peek_enabled"] = enabled
        self.store.schedule_save()
        self.toast("Peek on: links from pinned tabs open in a preview" if enabled else "Peek off")

    # --- Import ------------------------------------------------------------------------------

    # --- Backup & restore ------------------------------------------------------------------------

    def back_up(self):
        name = time.strftime(f"{brand.NAME} Backup %Y-%m-%d.zip")
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _ = QFileDialog.getSaveFileName(self, f"Back Up {brand.NAME}", f"{folder}/{name}", "Backups (*.zip)")
        if not path:
            return
        # Everything on disk first: the session, open notes, extensions' storage
        self.store.save()
        for tab in self.store.all_tabs():
            _save_editor(tab.view)
        self.emit("before_backup")  # (extensions' storage, say)
        try:
            count = backup.create(Path(path))
        except OSError as error:
            QMessageBox.warning(self, "Backup Failed", str(error))
            return
        self.toast(f"Backed up {count} file{'' if count == 1 else 's'} to {Path(path).name}")

    def restore_backup(self):
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _ = QFileDialog.getOpenFileName(self, "Restore from Backup", folder, "Backups (*.zip)")
        if not path:
            return
        try:
            info = backup.describe(Path(path))
        except ValueError as error:
            QMessageBox.warning(self, "Can't Restore", str(error))
            return
        made = time.strftime(portable("%-d %B %Y at %-I:%M %p"), time.localtime(info["created"]))
        code = [f"{n} {what}{'s' if n != 1 else ''}" for n, what in
                ((info["extensions"], "extension"), (info["userscripts"], "userscript"),
                 (info["native"], "app extensions can talk to")) if n]
        box = QMessageBox(QMessageBox.Icon.Question, "Restore from Backup",
                          f"Replace your Spaces, tabs, settings and notes with this backup from {made}?", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setInformativeText(
            f"It has {len(info['spaces'])} Space(s) ({', '.join(info['spaces'][:5])}) and {info['tabs']} tabs. "
            + (f"It also has {', '.join(code)}: code that can read and change the sites you visit. Only restore "
               "those from a backup you made yourself. " if code else "")
            + "What you have now is kept in a folder, not deleted. Sign-ins and cookies aren't affected.\n\n"
            f"{brand.NAME} will quit; open it again to finish.")
        everything = box.addButton("Restore Everything" if code else "Restore", QMessageBox.ButtonRole.AcceptRole)
        without = box.addButton("Restore Without Extensions", QMessageBox.ButtonRole.AcceptRole) if code else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(without or everything)
        box.exec()
        if box.clickedButton() not in (everything, without):
            return
        try:
            backup.stage(Path(path), with_code=box.clickedButton() is everything)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Can't Restore", str(error))
            return
        QApplication.instance().quit()

    # --- Spaces --------------------------------------------------------------------------------

    def _set_active_space(self, space):
        self.store.active_space_id = space.id
        self.store.schedule_save()
        self.apply_theme()

    def _restore_space_tab(self):
        tab = self.store.find_tab(self.store.active_space.active_tab_id)
        if tab in self.store.visible_tabs():
            self.activate_tab(tab)
        else:
            self._show_nothing()

    def switch_space(self, space, direction=None):
        if space.id == self.store.active_space_id:
            return
        self.tab_preview.dismiss()
        spaces = self.store.spaces

        def change():
            self._set_active_space(space)
            self._restore_space_tab()
        gestures = self.feature("gestures")
        if gestures is not None:
            gestures.space_swipe.abandon()  # (a swipe under way gives way to this switch)
        if self.sidebar.isVisible() and self.isVisible() and self.store.active_space in spaces:
            # The sidebar slides over to the new Space, like Arc's
            direction = direction or (1 if spaces.index(space) > spaces.index(self.store.active_space) else -1)
            slide_region(self, self.sidebar, change, direction)
        else:
            change()

    def cycle_space(self, step):
        spaces = self.store.spaces
        i = spaces.index(self.store.active_space)
        self.switch_space(spaces[(i + step) % len(spaces)], direction=1 if step > 0 else -1)

    def switch_to_space_number(self, n):
        if n <= len(self.store.spaces):
            self.switch_space(self.store.spaces[n - 1])

    def new_space(self):
        name, ok = QInputDialog.getText(self, "New Space", "Space name:")
        if not ok or not name.strip():
            return
        used = {s.color for s in self.store.spaces}
        color = next((c for _, c in PALETTE if c not in used), PALETTE[len(self.store.spaces) % len(PALETTE)][1])
        self.switch_space(self.store.add_space(name.strip(), color))

    # --- Sharing a Space (space_share.py) ---------------------------------------------------------

    def share_space(self, space=None):
        """The Space as a file, in macOS's share menu (AirDrop, Messages, Mail…), or saved where you choose."""
        space = space or self.store.active_space
        if not list(walk_tabs(space.pinned)) and not any(f.live for f in walk_folders(space.pinned)):
            self.toast(f"“{space.name}” has no pinned tabs to share")
            return
        folder = storage.data_dir() / "shared spaces"
        folder.mkdir(exist_ok=True)
        for old in folder.glob("*" + space_share.SUFFIX):
            old.unlink(missing_ok=True)  # (only the latest is kept: it's there to be sent)
        path = space_share.write(space, folder)
        title = self.sidebar.space_title
        anchor = QRect(title.mapTo(self, QPoint(0, 0)), title.size()) if title.isVisible() else \
            QRect(self.content.mapTo(self, QPoint(self.content.width() // 2, 0)), QSize(1, 1))
        if macos.share(self, bytes(QUrl.fromLocalFile(str(path)).toEncoded()).decode(), anchor):
            return
        start = str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / path.name)
        dest, _ = QFileDialog.getSaveFileName(self, "Share Space", start, f"Shared Spaces (*{space_share.SUFFIX})")
        if dest:
            shutil.copyfile(path, dest)
            self.toast(f"Saved “{space.name}” as {Path(dest).name}: send it to anyone with {brand.NAME}")

    def import_space(self):
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        path, _ = QFileDialog.getOpenFileName(self, "Import Space", folder, f"Shared Spaces (*{space_share.SUFFIX})")
        if path:
            self.import_space_file(path)

    def import_space_file(self, path):
        """A shared Space someone sent: added as a new Space, after asking."""
        self.bring_to_front()
        try:
            shared = space_share.read(Path(path))
        except ValueError as error:
            QMessageBox.warning(self, "Can't Import Space", str(error))
            return
        box = QMessageBox(QMessageBox.Icon.Question, "Import Space",
                          f"Add the Space “{shared['name']}”, with {space_share.summary(shared)}?", parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)  # (its name is whatever the file says)
        box.setInformativeText("It brings only addresses and names: you sign in to its sites yourself.")
        add = box.addButton("Add Space", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(add)
        box.exec()
        if box.clickedButton() is not add:
            return
        used = {s.color for s in self.store.spaces}
        color = next((c for _, c in PALETTE if c not in used), PALETTE[len(self.store.spaces) % len(PALETTE)][1])
        space = space_share.build(shared, color)
        self.store.spaces.append(space)
        self.store._changed()
        self.switch_space(space)
        live = self.feature("live_folders")
        for folder in walk_folders(space.pinned):
            if folder.live and live is not None:
                live.folders.refresh(folder)
        self.toast(f"Added “{space.name}”")

    def rename_space(self, space):
        name, ok = QInputDialog.getText(self, "Rename Space", "Space name:", text=space.name)
        if ok and name.strip():
            space.name = name.strip()
            self.store.schedule_save()
            self.sidebar.rebuild()

    def set_space_color(self, space, color=None):
        if color is None:
            picked = QColorDialog.getColor(parent=self, title="Space Colour")
            if not picked.isValid():
                return
            color = picked.name()
        space.color = color
        if space.theme.get("colors"):
            space.theme["colors"][0] = color
        self.store.schedule_save()
        if space is self.store.active_space:
            self.apply_theme()
        else:
            self.sidebar.rebuild()

    def edit_space_theme(self, space=None):
        space = space or self.store.active_space
        if space is not self.store.active_space:
            self.switch_space(space)  # so the editor previews on the real window
        ThemeEditor(self, space).exec()

    def delete_space(self, space):
        if len(self.store.spaces) == 1:
            self.toast("You need at least one Space")
            return
        answer = QMessageBox.question(
            self, "Delete Space", f"Delete “{space.name}” and its {len(list(walk_tabs(space.pinned + space.today)))} tabs?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_active = space is self.store.active_space
        for tab in list(walk_tabs(space.pinned + space.today)):
            self._remove_view(tab)
        self.store.remove_space(space)
        if was_active:
            self.apply_theme()
            self._restore_space_tab()

    def show_space_menu(self, space, pos):
        SpaceEditor(self, space).popup(pos)

    def edit_space(self, space=None):
        """Open the Space editor next to the Space's name in the sidebar."""
        space = space or self.store.active_space
        if space is not self.store.active_space:
            self.switch_space(space)
        title = self.sidebar.space_title
        self.show_space_menu(space, title.mapToGlobal(QPoint(0, title.height() + 4)))

    def space_edited(self, space, retheme=False):
        self.store.schedule_save()
        if retheme and space is self.store.active_space:
            self.apply_theme()
        else:
            self.sidebar.rebuild()

    # --- Menus ------------------------------------------------------------------------------------

    def _menu(self, parent=None) -> QMenu:
        """A right-click menu in the app's own style: rounded, themed, with icons."""
        menu = PlainMenu(parent or self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # lets the corners be round
        return menu

    def _act(self, menu, icon, text, slot):
        return menu.addAction(qta.icon(icon, color=self.theme.panel_text, color_active=QColor("white")), text, slot)

    def _submenu(self, menu, icon, text) -> QMenu:
        sub = self._menu(menu)
        sub.setTitle(text)
        sub.setIcon(qta.icon(icon, color=self.theme.panel_text, color_active=QColor("white")))
        menu.addMenu(sub)
        return sub

    def show_archive_menu(self, pos):
        menu = self._menu()
        header = menu.addAction("Archived Tabs")
        header.setEnabled(False)
        for entry in self.store.archive[:15]:
            title = entry["title"] if len(entry["title"]) < 48 else entry["title"][:47] + "…"
            menu.addAction(storage.favicon(entry["url"]) or dot_icon(self.theme.accent.name()), title,
                           lambda url=entry["url"]: self.open_url(url))
        if not self.store.archive:
            empty = menu.addAction("Nothing archived yet")
            empty.setEnabled(False)
        menu.addSeparator()
        self._act(menu, "ph.books", "Show All in Library…", lambda: self.open_library("archive"))
        self._act(menu, "ph.list-bullets", "Copy Links of Today's Tabs", self.copy_today_links)
        after = self._submenu(menu, "ph.clock", "Archive Today Tabs After")
        current = self.store.settings.get("archive_after_hours", 12)
        for hours, label in ARCHIVE_CHOICES:
            act = after.addAction(label, lambda h=hours: self._set_archive_after(h))
            act.setCheckable(True)
            act.setChecked(hours == current)
        menu.exec(pos - QPoint(0, menu.sizeHint().height() + 4))

    def _set_archive_after(self, hours):
        self.store.settings["archive_after_hours"] = hours
        self.store.schedule_save()

    # --- Commands (menu bar + command bar) ---------------------------------------------------

    def commands(self):
        cmds = [
            ("New Space", "ph.plus", self.new_space),
            ("Toggle Sidebar", "ph.arrows-out-simple", self.toggle_sidebar),
            ("Move Sidebar to the Left" if self.sidebar_on_right else "Move Sidebar to the Right", "ph.arrows-left-right",
             lambda: self.set_sidebar_side("left" if self.sidebar_on_right else "right")),
            ("Copy URL", "ph.link", self.copy_url),
            ("Copy as Markdown Link", "ph.link-simple", self.copy_markdown_link),
            ("Print…", "ph.printer", self.print_page),
            ("Save as PDF", "ph.file-pdf", self.save_pdf),
            ("Save Page As", "ph.floppy-disk", self.save_page),
            ("Share Page (AirDrop, Messages, Mail…)", "ph.share", self.share_page),
            ("Copy Screenshot of What's on Screen", "ph.camera", self.copy_screenshot),
            ("Save Screenshot of the Whole Page", "ph.camera", self.save_page_screenshot),
            ("Copy Links of Today's Tabs", "ph.list-bullets", self.copy_today_links),
            ("Copy Links of All Tabs in This Space", "ph.list-bullets", self.copy_space_links),
            ("Pin / Unpin Tab", "ph.push-pin", self.toggle_pin),
            ("Add / Remove Favorite", "ph.star", self.toggle_favorite),
            ("Clear Today's Tabs", "ph.archive", self.clear_today),
            ("Put Other Tabs to Sleep", "ph.moon-stars", self.sleep_other_tabs),
            ("Mute / Unmute Tab", "ph.speaker-slash", self.toggle_mute),
            ("Mute Other Tabs", "ph.speaker-x", self.mute_other_tabs),
            ("Keep This Tab Awake / Let It Sleep", "ph.sun", self.toggle_awake),
            ("Do Not Disturb On / Off (Site Notifications)", "ph.bell-slash", self.toggle_dnd),
            ("New Little Window", "ph.app-window", self.new_little_window),
            ("New Private Window", "ph.ghost", lambda: self.private.new_window()),
            ("Reopen Closed Tab", "ph.clock-counter-clockwise", self.reopen_closed_tab),
            ("Reader View", "ph.book-open-text", self.toggle_reader),
            ("What's New", "ph.gift", self.show_whats_new),
            ("Show Crash Logs", "ph.bug", self.show_crash_logs),
            ("Send Feedback…", "ph.chat-teardrop-text", self.send_feedback),
            ("Open Developer Tools", "ph.terminal-window", self.open_devtools),
            ("Edit Space (Name, Icon, Colour, Profile)", "ph.pencil-simple", self.edit_space),
            ("Edit Space Theme", "ph.paint-brush", self.edit_space_theme),
            ("Settings", "ph.gear-six", self.open_settings),
            ("Library (History, Archive, Notes)", "ph.books", self.open_library),
            ("Keyboard Shortcuts", "ph.keyboard", lambda: self.open_settings("shortcuts")),
            ("Back Up Everything", "ph.archive-box", self.back_up),
            ("Restore from Backup", "ph.clock-counter-clockwise", self.restore_backup),
            ("Download Video from This Page", "ph.film-strip", self.download_video),
            ("Show Downloads", "ph.download-simple", self.show_downloads),
            ("Download Settings", "ph.gear-six", self.download_settings),
            ("Turn Ad Blocking On / Off", "ph.shield-check", self.toggle_adblock),
            ("Allow / Block Ads on This Site", "ph.shield-slash", self.allow_ads_here),
            ("New Folder", "ph.folder-plus", self.new_folder),
            ("Split View with a New Tab", "ph.columns", lambda: self.open_command_bar("split")),
            ("Separate Split View", "ph.square-half", self.separate_split),
            ("Turn Peek On / Off", "ph.eye", self.toggle_peek),
        ]
        cmds += [(f"Switch to Space: {s.name}", "ph.dots-three", lambda s=s: self.switch_space(s))
                 for s in self.store.spaces]
        return cmds + self.gather("commands")

    def _build_menus(self):
        bar = self.menuBar()

        # What opens a window of its own: the rest bring the main window back first, if it was closed (on a Mac,
        # Eclipse keeps running, with its menus, when the window closes)
        own_window = {"New Little Window", "New Private Window", "Downloads", "Keyboard Shortcuts", "Library",
                      "Archived Tabs", "Notes", "Browse for Me Answers", "Minimize", "Zoom", "Toggle Full Screen",
                      "Send Feedback…", "What's New", "Show Crash Logs", "Back Up Everything…",
                      "Restore from Backup…", "Download Settings…"}

        def add(menu, text, shortcut, slot):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            if text not in own_window:
                slot = (lambda inner: lambda: (self.isHidden() and self.bring_to_front(), inner()))(slot)
            action.triggered.connect(lambda _=False: slot())
            menu.addAction(action)
            self.addAction(action)  # keeps the shortcut working when the menu bar isn't native
            return action

        file = bar.addMenu("File")
        add(file, "New Tab", "Ctrl+T", lambda: self.open_command_bar("new"))
        add(file, "New Little Window", "Ctrl+Alt+N", self.new_little_window)
        add(file, "New Private Window", "Ctrl+Shift+P", lambda: self.private.new_window())
        add(file, "Open Location…", "Ctrl+L", lambda: self.open_command_bar("current"))
        add(file, "Reopen Closed Tab", "Ctrl+Shift+T", self.reopen_closed_tab)
        file.addSeparator()
        add(file, "Downloads", "Ctrl+Alt+L", self.show_downloads)
        add(file, "Download Video…", "Ctrl+Shift+D", self.download_video)
        file.addSeparator()
        add(file, "Print…", "Ctrl+P", self.print_page)
        add(file, "Save as PDF…", "Ctrl+Alt+P", self.save_pdf)
        add(file, "Save Page As…", "Ctrl+Shift+S", self.save_page)
        add(file, "Share…", "", self.share_page)
        add(file, "Copy Screenshot", "Ctrl+Alt+Shift+4", self.copy_screenshot)
        add(file, "Save Screenshot of Whole Page", "Ctrl+Alt+Shift+3", self.save_page_screenshot)
        add(file, "Download Settings…", "", self.download_settings)
        file.addSeparator()
        add(file, "Back Up Everything…", "", self.back_up)
        add(file, "Restore from Backup…", "", self.restore_backup)
        prefs = QAction("Settings…", self)
        prefs.setMenuRole(QAction.MenuRole.PreferencesRole)  # the app menu's "Settings…" (⌘,) on macOS
        prefs.setShortcut(QKeySequence("Ctrl+,"))
        prefs.triggered.connect(lambda _=False: self.open_settings())
        file.addAction(prefs)
        self.addAction(prefs)
        default = QAction("Set as Default Browser…", self)
        default.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)  # lives in the app menu on macOS
        default.triggered.connect(lambda _=False: self.set_as_default_browser())
        file.addAction(default)
        links = file.addMenu("Open Links from Other Apps In")
        for mode, label in (("little", "Little Window"), ("tab", "New Tab")):
            act = links.addAction(label, lambda m=mode: self._set_external_links(m))
            act.setCheckable(True)
            act.setChecked(self.store.settings.get("external_links", "little") == mode)
        links.aboutToShow.connect(lambda: [a.setChecked(self.store.settings.get("external_links", "little") ==
                                                        ("little" if a.text() == "Little Window" else "tab"))
                                           for a in links.actions()])
        file.addSeparator()
        add(file, "Close Tab", "Ctrl+W", self.close_tab)

        edit = bar.addMenu("Edit")
        add(edit, "Copy URL", "Ctrl+Shift+C", self.copy_url)
        add(edit, "Copy as Markdown Link", "Ctrl+Alt+Shift+C", self.copy_markdown_link)
        edit.addSeparator()
        add(edit, "Find…", "Ctrl+F", self.open_find)
        add(edit, "Find Next", "Ctrl+G", lambda: self.content.find_bar.find())
        add(edit, "Find Previous", "Ctrl+Shift+G", lambda: self.content.find_bar.find(backward=True))

        view = bar.addMenu("View")
        add(view, "Toggle Sidebar", "Ctrl+S", self.toggle_sidebar)
        view.addSeparator()
        add(view, "Reload", "Ctrl+R", self.reload)
        add(view, "Hard Reload", "Ctrl+Shift+R", self.hard_reload)
        view.addSeparator()
        add(view, "Zoom In", "Ctrl+=", lambda: self.zoom(1))
        add(view, "Zoom Out", "Ctrl+-", lambda: self.zoom(-1))
        add(view, "Actual Size", "Ctrl+0", lambda: self.zoom(0))
        view.addSeparator()
        add(view, "Reader View", "Ctrl+Alt+R", self.toggle_reader)
        add(view, "Developer Tools", "Ctrl+Alt+I", self.open_devtools)
        add(view, "Developer Console", "Ctrl+Alt+J", self.toggle_dev_console)
        view.addSeparator()
        self.adblock_action = QAction("Block Ads & Trackers", self, checkable=True)
        self.adblock_action.setChecked(self.adblock.enabled)
        self.adblock_action.triggered.connect(lambda _=False: self.toggle_adblock())
        view.addAction(self.adblock_action)

        add(view, "Keyboard Shortcuts", "Ctrl+/", lambda: self.open_settings("shortcuts"))

        history = bar.addMenu("History")
        add(history, "Back", "Ctrl+[", self.go_back)
        add(history, "Forward", "Ctrl+]", self.go_forward)
        history.addSeparator()
        add(history, "Library", "Ctrl+Y", self.open_library)
        add(history, "Archived Tabs", "", lambda: self.open_library("archive"))
        add(history, "Notes", "", lambda: self.open_library("notes"))
        add(history, "Browse for Me Answers", "", lambda: self.open_library("answers"))
        history.addSeparator()
        recent = history.addMenu("Recently Closed")
        recent.aboutToShow.connect(lambda m=recent: self._fill_recently_closed(m))
        self._fill_recently_closed(recent)  # (macOS reads the menu before it's first shown)
        history.aboutToShow.connect(lambda m=history: self._fill_recent_pages(m))
        self._fill_recent_pages(history)

        tabs = bar.addMenu("Tabs")
        add(tabs, "Pin / Unpin Tab", "Ctrl+D", self.toggle_pin)
        add(tabs, "Add / Remove Favorite", "", self.toggle_favorite)
        add(tabs, "Clear Today's Tabs", "Ctrl+Shift+K", self.clear_today)
        add(tabs, "Put Other Tabs to Sleep", "", self.sleep_other_tabs)
        add(tabs, "New Folder…", "", self.new_folder)
        tabs.addSeparator()
        add(tabs, "Split View…", f"{CONTROL}+Shift+=", lambda: self.open_command_bar("split"))
        add(tabs, "Separate Split View", f"{CONTROL}+Shift+-", self.separate_split)
        tabs.addSeparator()
        add(tabs, "Next Tab", f"{CONTROL}+Tab", lambda: self._ctrl_tab(1))
        add(tabs, "Previous Tab", f"{CONTROL}+Shift+Tab", lambda: self._ctrl_tab(-1))
        add(tabs, "Next Tab", "Ctrl+Alt+Down", lambda: self.visible_tab_step(1))
        add(tabs, "Previous Tab", "Ctrl+Alt+Up", lambda: self.visible_tab_step(-1))
        for n in range(1, 10):
            add(tabs, f"Tab {n}" if n < 9 else "Last Tab", f"Ctrl+{n}", lambda n=n: self.select_tab_number(n))

        spaces = bar.addMenu("Spaces")
        add(spaces, "New Space…", "", self.new_space)
        add(spaces, "Share This Space…", "", self.share_space)
        add(spaces, "Import Space…", "", self.import_space)
        add(spaces, "Next Space", "Ctrl+Alt+Right", lambda: self.cycle_space(1))
        add(spaces, "Previous Space", "Ctrl+Alt+Left", lambda: self.cycle_space(-1))
        spaces.addSeparator()
        for n in range(1, 10):
            add(spaces, f"Space {n}", f"{CONTROL}+{n}", lambda n=n: self.switch_to_space_number(n))
        windows = bar.addMenu("Window")
        for text, keys, slot in (("Minimize", "Ctrl+M", lambda: self._front_window().showMinimized()),
                                 ("Zoom", "", self._zoom_window),
                                 ("Toggle Full Screen", f"Ctrl+{CONTROL}+F" if MAC else "F11", self._toggle_full_screen)):
            add(windows, text, keys, slot).setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)  # (any window)
        windows.addSeparator()
        windows.aboutToShow.connect(lambda m=windows: self._fill_window_menu(m))
        self._fill_window_menu(windows)  # (macOS reads the menu before it's first shown)
        self._build_dock_menu()
        help_menu = bar.addMenu("Help")
        add(help_menu, "Send Feedback…", "", self.send_feedback)
        add(help_menu, "What's New", "", self.show_whats_new)
        add(help_menu, "Show Crash Logs", "", self.show_crash_logs)
        # The features' own items (and menus)
        menus = {"File": file, "Edit": edit, "View": view, "History": history, "Tabs": tabs, "Spaces": spaces,
                 "Help": help_menu}
        self.emit("menus", add, menus, bar)
        self.apply_shortcuts()

    def _open_windows(self) -> list:
        """Eclipse's windows on screen: this one, then private and Little windows (and DevTools)."""
        return [w for w in [self, *self.private.windows, *self._extra_windows] if not sip.isdeleted(w) and w.isVisible()]

    def _front_window(self):
        active = QApplication.activeWindow()
        return active if active is not None else self

    def _zoom_window(self):
        window = self._front_window()
        window.showNormal() if window.isMaximized() else window.showMaximized()

    def _toggle_full_screen(self):
        window = self._front_window()
        if window is self and self._fullscreen:
            self._set_fullscreen(False)  # (a video that went full screen)
        elif window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()

    def _fill_window_menu(self, menu):
        """The Window menu's list of windows (a tick on the one in front), refilled each time it opens."""
        for action in menu.actions():
            if action.property(shortcuts.DYNAMIC):
                menu.removeAction(action)
                action.deleteLater()
        front = QApplication.activeWindow()
        for window in self._open_windows():
            item = menu.addAction(window.windowTitle() or brand.NAME)
            item.setProperty(shortcuts.DYNAMIC, True)
            item.setCheckable(True)
            item.setChecked(window is front)
            item.triggered.connect(lambda _=False, w=window: self._raise_window(w))
        tail = menu.addSeparator()
        tail.setProperty(shortcuts.DYNAMIC, True)
        everything = menu.addAction("Bring All to Front", lambda: [self._raise_window(w) for w in self._open_windows()])
        everything.setProperty(shortcuts.DYNAMIC, True)

    def _raise_window(self, window):
        if window.isMinimized():
            window.showNormal()
        window.raise_()
        window.activateWindow()
        macos.activate_app()

    def _build_dock_menu(self):
        """Right-click Eclipse in the Dock: a new tab or window, and the Spaces (macOS lists the windows itself)."""
        if not MAC:
            return
        menu = QMenu(self)

        def front_then(slot):
            self.bring_to_front()
            slot()
        menu.addAction("New Tab", lambda: front_then(lambda: self.open_command_bar("new")))
        menu.addAction("New Little Window", lambda: front_then(self.new_little_window))
        menu.addAction("New Private Window", lambda: front_then(self.private.new_window))
        spaces = menu.addMenu("Switch to Space")
        spaces.aboutToShow.connect(lambda: self._fill_dock_spaces(spaces))
        self._fill_dock_spaces(spaces)
        menu.setAsDockMenu()
        self._dock_menu = menu

    def _fill_dock_spaces(self, menu):
        menu.clear()
        for space in self.store.spaces:
            item = menu.addAction(space.name, lambda s=space: (self.bring_to_front(), self.switch_space(s)))
            item.setCheckable(True)
            item.setChecked(space is self.store.active_space)

    def apply_shortcuts(self):
        """The menu items' shortcuts, with yours (Settings → Keyboard Shortcuts) in place of their own."""
        shortcuts.apply(self.menuBar(), self.store.settings.get(shortcuts.SETTING, {}))
        self.emit("shortcuts_changed")

    # --- Shutdown --------------------------------------------------------------------------

    def _stamp_on_screen(self):
        """The tabs on screen now (the current one, and its Split View panes) are in use as of now:
        sleeping and archiving go by when a tab was last used, which is when you left it."""
        current = self.current_tab
        if current is None:
            return
        now = time.time()
        for tab in self.store.split_of(current) or [current]:
            tab.last_active = now

    def closeEvent(self, event):
        self._stamp_on_screen()
        for tab in self.store.all_tabs():
            self._note_scroll(tab)
        self.store.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.store.save()
        # The red button (a close from the window system, not from quitting): on a Mac, Eclipse stays open, in
        # the Dock and the menu bar, and the window comes back from the Dock as you left it
        if event.spontaneous() and self.keeps_running():
            event.ignore()
            self._put_away()
            return
        super().closeEvent(event)

    def keeps_running(self) -> bool:
        return MAC and self.store.settings.get(KEEP_RUNNING, True)

    def apply_keep_running(self):
        QApplication.instance().setQuitOnLastWindowClosed(not self.keeps_running())  # (⌘Q still quits)

    def _put_away(self):
        """The window closed while Eclipse keeps running: hidden, its tabs kept, but nothing goes on playing in
        a window you've closed."""
        for tab in self.store.all_tabs():
            if isinstance(tab.view, QWebEngineView):
                tab.view.page().runJavaScript("document.querySelectorAll('video, audio').forEach(m => m.pause())")
        if self._fullscreen:
            self._set_fullscreen(False)
        elif self.isFullScreen():
            self.showNormal()
        self.peek.close_peek() if self.peek.isVisible() else None
        self.hide()
        self.emit("put_away")

    def _app_state_changed(self, state):
        """Clicking the Dock icon (Qt says the app is active then, every click) brings a closed window back,
        unless another of Eclipse's windows is open, as with any Mac app."""
        if state != Qt.ApplicationState.ApplicationActive or not self.isHidden() or not self.keeps_running():
            return
        if any(w.isVisible() for w in QApplication.topLevelWidgets() if w is not self and w.isWindow()
               and not w.windowFlags() & (Qt.WindowType.Popup | Qt.WindowType.ToolTip)
               and w.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)):
            return
        self.bring_to_front()

    def shutdown(self):
        if self._downloads is not None:
            self._downloads.shutdown()
            sip.delete(self._downloads)
        for panel in (self._settings, self._library):
            if panel is not None:
                sip.delete(panel)
        self.adblock.detach_all()
        self.mini_player.shutdown()
        self.private.shutdown()
        self.page_index.shutdown()
        self.devmode.shutdown()
        self.emit("shutdown")
        # Pages must be destroyed before their profile, or Qt warns and may leak the profile
        if self.peek.view is not None:
            _delete(self.peek.view)
        for window in list(self._extra_windows):
            _delete(window)
        for tab in self.store.all_tabs():
            _save_editor(tab.view)
            if tab.view is not None:
                _delete(tab.view)
                tab.view = None
