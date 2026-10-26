# The main window doubles as the controller: it owns the Store (data), the
# web views, and every user action. Sidebar/command bar widgets call into it.

import html
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QEvent, QPoint, QRect, QSize, QStandardPaths, Qt, QTimer, QUrl
from PyQt6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QGuiApplication, QIcon, QKeySequence,
                         QShortcut)
from PyQt6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEngineNewWindowRequest, QWebEnginePage,
                                   QWebEnginePermission)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QColorDialog, QDialog, QFileDialog,
                             QHBoxLayout, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QSplitter,
                             QVBoxLayout, QWidget)

import qtawesome as qta

from . import arc_import, backup, brand, clean_links, macos, motion, spelling, storage
from .adblock import AdBlocker
from .ai import Claude, page_block
from .browse import BrowseForMe, PageReader, answer_path, js_set, new_run_id, render_page
from .boosts import ZAP_DONE, ZAP_MESSAGE, ZAP_SCRIPT, Boost, BoostEditor, BoostManager, site_of
from .command_bar import CUSTOM_SETTING, CommandBar, use_custom_sites
from .content import ContentFrame
from .downloads import BrowserDownload, DownloadSettings, DownloadsWindow, ytdlp_settings
from .ext.host import STORE_MESSAGE, ExtensionHost
from .ext.ui import ExtensionPopup, Installer, extensions_menu
from .ext.updates import Updater
from .library import LibraryWindow
from .air_traffic import route
from .little import LittleWindow
from .live_folders import LiveFolders, feed_name, mark_seen
from .mini_player import MiniPlayer
from .notify import NotificationCenter
from .devmode import DevMode, is_dev_url
from .now_playing import NowPlaying
from .cert_errors import CertificateGuard
from .crash import CrashWatcher, logs_dir as crash_logs_dir
from . import integrations
from .integrations import Homebase, LocalAI
from .integrations_ui import AppsUI
from .upnext import UpNext
from .live_folder_dialog import LiveFolderDialog
from .page_index import PageIndex
from .private import PrivateSession
from .reader import ReaderView
from .self_update import SelfUpdater
from .translate import Translator, target_language
from .floating_sidebar import EDGE as FLOAT_EDGE, FloatingSidebar
from .gestures import SpaceSwipe, SwipeNavigator, slide_region
from .tab_switcher import TabSwitcher
from .zoom import remember_zoom, step_zoom, zoom_for, zoom_key
from .page_actions import markdown_link, safe_filename
from .permissions import KINDS, PermissionManager, origin_key
from .easel import CaptureOverlay, EaselView, is_easel, new_easel_url
from .notes import NoteEditor, is_note, new_note_url
from .peek import PeekOverlay
from .split import MAX_PANES
from .models import FAVORITES, PINNED, TODAY, Folder, Tab, display_host, walk_folders, walk_tabs
from .settings import ARCHIVE_CHOICES, DEFAULT_SLEEP_MINUTES, SettingsWindow
from .sidebar import Sidebar
from .icons import space_icon
from .space_editor import SpaceEditor
from .tidy_downloads import tidy as tidy_download
from .tidy import MIN_TABS as TIDY_MIN_TABS, build_prompt as build_tidy_prompt, parse_groups as parse_tidy_groups
from .theme import PALETTE, PlainMenu, ThemedBackground, dot_icon, stylesheet, theme_for_space
from .theme_editor import ThemeEditor
from .webview import DEFAULT_PROFILE, WebView, build_profile

MAC = sys.platform == "darwin"
# Qt maps "Ctrl" to ⌘ on macOS; the physical Control key is "Meta" there
CONTROL = "Meta" if MAC else "Ctrl"
SIDEBAR_WIDTH = 250  # default; drag the sidebar's edge to change it


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
        self._fullscreen = False
        self._rebuild_pending = False

        # Claude (Arc Max-style features) via the local Claude Code CLI
        self.claude = Claude(self, store.settings.get("claude_model", "haiku"))
        self.claude.enabled = store.settings.get("claude_enabled", True)
        self._hovered_link = ""
        self._shift_alone = False
        self._preview_page = None
        self._preview_request = None
        self._preview_cache: dict[str, tuple[str, str]] = {}

        self.adblock = AdBlocker(store, self)
        self._shield_timer = QTimer(self, singleShot=True, interval=300, timeout=lambda: self.sidebar.update_shield())
        self.adblock.blocked.connect(lambda _site: self._shield_timer.start())  # coalesce bursts
        self.boosts = BoostManager(store, self)
        self.live_folders = LiveFolders(self)  # folders that fill themselves from a feed or GitHub
        self.mini_player = MiniPlayer(self)  # floats a playing video when you switch away from its tab
        self.permissions = PermissionManager(self)  # camera, microphone, screen sharing, location…
        self.notifications = NotificationCenter(self)  # site notifications and unread badges
        self.swipes = SwipeNavigator(self)  # two-finger back/forward
        self.space_swipe = SpaceSwipe(self)  # two fingers on the sidebar: the next Space
        self.devmode = DevMode(self)  # localhost and file pages: console drawer, reload on save
        self.now_playing = NowPlaying(self)  # the sidebar's card for a background tab playing audio
        self.translator = Translator(self)  # Translate Page, with the Claude CLI
        self.crashes = CrashWatcher(self)  # pages whose renderer crashed get a Reload card
        self.certificates = CertificateGuard(self)  # "This connection isn't private"
        self.private = PrivateSession(self)  # ⇧⌘P: windows that remember nothing
        self.page_index = PageIndex(self)  # the text of pages you've read, searchable from ⌘T
        self.homebase = Homebase(self)  # your desktop's apps (status, start/stop, logs)
        self.homebase.alert.connect(self._homebase_alert)
        self.local_ai = LocalAI(self)  # the model homebase serves (Qwen), for questions
        self.apps = AppsUI(self)  # Send To, the homebase menu, My Apps presets
        self.up_next = UpNext(self)  # your next event today, from Daybook
        self.parliament_badge = integrations.ParliamentBadge(self)  # its unread count on its tabs
        use_custom_sites(store.settings.get(CUSTOM_SETTING, []))  # your own search shortcuts
        self.self_updater = SelfUpdater(self)  # a daily look for a newer version on GitHub
        self.extensions = ExtensionHost(self)  # userscripts and Chrome extensions (our own runtime)
        self.installer = Installer(self)
        self.ext_updates = Updater(self)  # daily update checks for extensions and userscripts
        self._extension_shortcuts = []
        self.extensions.changed.connect(self.install_extension_shortcuts)
        self._downloads: DownloadsWindow | None = None
        self._settings: SettingsWindow | None = None
        self._library: LibraryWindow | None = None
        self._zap_tab: Tab | None = None
        self._tidy_undo = None  # (space, Today's top-level nodes before the last Tidy)
        self._last_easel = None  # where Capture (⇧⌘2) puts things
        self._printing = None  # (view, printer) while a page prints
        self._browse_runs: set = set()
        self.floating_sidebar = FloatingSidebar(self)  # the hidden sidebar, out over the page on hover

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
        content_wrap = QWidget()
        self.content_layout = QVBoxLayout(content_wrap)
        self.content_layout.addWidget(self.content)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(8)  # the sidebar's edge: drag to resize, double-click to reset
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(content_wrap)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([store.settings["sidebar_width"], 1000])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self.splitter.handle(1).setCursor(Qt.CursorShape.SplitHCursor)
        self.splitter.handle(1).setToolTip("Drag to resize the sidebar · double-click to reset")
        root_layout.addWidget(self.splitter)
        self.setCentralWidget(root)

        self.peek = PeekOverlay(self, root)
        self.command_bar = CommandBar(self, root)
        self.tab_switcher = TabSwitcher(self, root)
        self.thumbnails: OrderedDict[str, object] = OrderedDict()  # tab id → a small picture of its page

        self.sidebar.setVisible(store.settings.get("sidebar_visible", True))
        self._update_content_margins()

        store.structure_changed.connect(self._schedule_rebuild)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        # macOS switching between light and dark: the window crossfades into its new colours
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _: motion.crossfade(self.centralWidget(), self.centralWidget(), self.apply_theme, ms=280))
        self.apply_theme()
        # The native macOS menu bar is slow to build (~150 ms), so draw the window first
        QTimer.singleShot(0, self._build_menus)

        if store.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(store.window_geometry.encode()))
        else:
            self.resize(1280, 820)

        self._archive_timer = QTimer(self, interval=15 * 60 * 1000, timeout=self.archive_stale_tabs)
        self._archive_timer.start()
        self._sleep_timer = QTimer(self, interval=60 * 1000, timeout=self.sleep_idle_tabs)
        self._sleep_timer.start()
        self.archive_stale_tabs()
        self._restore_space_tab()

        # Watch for Shift anywhere (including inside web pages) for link previews
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(2000, self.claude.warm_up)

        # First launch on a Mac that has Arc: offer to bring everything over
        if store.is_new and arc_import.arc_installed():
            QTimer.singleShot(800, self.import_from_arc)

    # --- Theme & layout ----------------------------------------------------------------

    def apply_theme(self):
        self.theme = theme_for_space(self.store.active_space)
        self.setStyleSheet(stylesheet(self.theme))
        self.centralWidget().update()
        self.sidebar.rebuild()
        if self._downloads is not None:
            self._downloads.apply_theme()
        for panel in (self._settings, self._library):
            if panel is not None:
                panel.apply_theme()
        for corner in self.content.corners:
            corner.update()

    def _schedule_rebuild(self):
        # Many store changes can land in one event loop pass; rebuild the sidebar once
        if not self._rebuild_pending:
            self._rebuild_pending = True
            QTimer.singleShot(0, self._rebuild_now)

    def _rebuild_now(self):
        self._rebuild_pending = False
        self.sidebar.rebuild()

    def _update_content_margins(self):
        if self._fullscreen:
            self.content_layout.setContentsMargins(0, 0, 0, 0)
        elif not self.sidebar.isHidden():  # (not isVisible(): before the window first shows, nothing is)
            self.content_layout.setContentsMargins(0, 8, 8, 8)
        else:
            # Leave a strip at the top for the traffic lights
            self.content_layout.setContentsMargins(8, 32 if MAC else 8, 8, 8)

    def reset_sidebar_width(self):
        self.splitter.setSizes([SIDEBAR_WIDTH, self.splitter.width() - SIDEBAR_WIDTH])
        self._on_splitter_moved()

    def _on_splitter_moved(self, *_):
        self.store.settings["sidebar_width"] = self.splitter.sizes()[0]
        self.store.schedule_save()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.command_bar.setGeometry(self.centralWidget().rect())
        if self.peek.isVisible():
            self.peek.setGeometry(self.centralWidget().rect())
        if self.floating_sidebar.active:
            self._place_floating_sidebar()
        self.notifications.relayout()

    def showEvent(self, event):
        super().showEvent(event)
        if MAC:
            macos.hide_title_text(self)  # otherwise the page title is drawn over the sidebar
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
        self.boosts.attach(profile)
        self.extensions.attach_profile(profile)
        self.permissions.attach_profile(profile)
        self.notifications.attach_profile(profile)
        self.swipes.attach_profile(profile)
        self.now_playing.attach_profile(profile)
        spelling.apply([profile], self.store.settings)

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
        self.extensions.attach_page(view.page())
        self.permissions.attach(view)
        self.swipes.attach(view)
        self.devmode.attach(view)
        self.crashes.attach(view)
        self.certificates.attach(view)
        view.urlChanged.connect(lambda _url, v=view: self._apply_site_zoom(v))
        view.loadFinished.connect(lambda _ok, v=view: self._apply_site_zoom(v))  # pages can reset it
        view.printRequested.connect(lambda v=view: self.print_page(v))  # a site's own Print button
        view.printRequestedByFrame.connect(lambda _frame, v=view: self.print_page(v))
        view.context_menu_hook = self._extend_context_menu
        self._count_loading(view)
        return view

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
        actions = (self.apps.send_to_actions(tab) if tab is not None and not request.linkUrl().isValid()
                   and not selected else [])
        if actions:
            send = PlainMenu("Send Page To", menu)
            for label, _icon, run in actions:
                send.addAction(label, run)
            first = menu.actions()[0] if menu.actions() else None
            menu.insertMenu(first, send) if first else menu.addMenu(send)
            menu.insertSeparator(first) if first else None
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
        self.extensions.extend_context_menu(view, menu, request)

    def _selection_actions(self, view, menu, selected):
        """Right-clicking selected text: ask about it, translate it, look it up — at the top of the menu."""
        short = selected if len(selected) <= 28 else selected[:26].rstrip() + "…"
        title = view.title() or display_host(view.url().toString())
        page = page_block(title, view.url().toString(), selected[:6000])
        explain = (page + "\n\nExplain the passage above (from that page) plainly and briefly, in under 150 "
                   "words: what it means, and any term in it a reader might not know. Use Markdown where it helps.")
        language = target_language(self.store.settings)
        translate = (page + f"\n\nTranslate the passage above into {language}. Answer with only the "
                     "translation (if it's already in that language, say so in one line).")
        first = menu.actions()[0] if menu.actions() else None
        added = []
        for ai in (self.assistant(), self.assistant(other=True)):
            if ai is not None:
                action = QAction(f"Ask {self.assistant_name(ai)} About “{short}”", menu)
                action.triggered.connect(lambda _=False, a=ai: self.command_bar.ask_about(selected, explain, a))
                added.append(action)
        if self.assistant() is not None:
            action = QAction(f"Translate “{short}”", menu)
            action.triggered.connect(lambda: self.command_bar.ask_about(selected, translate))
            added.append(action)
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
        if tab.view is None and is_easel(tab.url):
            tab.view = EaselView(tab.url)
            tab.view.title_changed.connect(lambda title: self._on_title(tab, title))
            tab.view.open_link.connect(lambda url: self.open_url(url))
            tab.title = tab.view.title
            self.content.stack.addWidget(tab.view)
            tab.changed.emit()
        if tab.view is None and is_note(tab.url):
            tab.view = NoteEditor(tab.url)
            tab.view.title_changed.connect(lambda title: self._on_title(tab, title))
            tab.title = tab.view.title
            self.content.stack.addWidget(tab.view)
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
        page.fullScreenRequested.connect(self._on_fullscreen)
        page.console_message.connect(lambda message: self._on_console(tab, message))
        page.lifecycleStateChanged.connect(lambda _state: tab.changed.emit())  # dim/undim in the sidebar
        page.recentlyAudibleChanged.connect(lambda _audible: self._on_audible(tab))  # the speaker icon
        page.audioMutedChanged.connect(lambda _muted: tab.changed.emit())
        page.findTextFinished.connect(
            lambda r: tab is self.current_tab and self.content.find_bar.show_result(r.activeMatch(),
                                                                                   r.numberOfMatches()))

    def _destroy_view(self, tab):
        if tab is self._zap_tab:
            self._zap_tab = None
        if self._fullscreen and tab is self.current_tab:
            self._set_fullscreen(False)  # its page was the one filling the screen
        self.thumbnails.pop(tab.id, None)
        self.mini_player.release(tab)
        self.now_playing.forget(tab)
        view, tab.view = tab.view, None
        if isinstance(view, (NoteEditor, EaselView)):
            view.save()
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

    def _on_audible(self, tab):
        tab.changed.emit()
        self.mini_player.audible_changed(tab)
        self.now_playing.audible_changed(tab)

    def _on_url(self, tab, url):
        if tab is self._zap_tab:
            self._zap_tab = None  # navigating away ends Zap (the new page has no picker)
        tab.url = url.toString()
        tab.changed.emit()
        if tab is self.current_tab:
            self.sidebar.update_nav()
        self.store.schedule_save()
        self.extensions.tab_updated(tab, url=tab.url)

    def _on_title(self, tab, title):
        tab.title = title
        self.notifications.title_changed(tab)  # "(3) Inbox" → a badge on the tab, and on the Dock
        tab.changed.emit()
        if tab is self.current_tab:
            self.setWindowTitle(tab.label)
        self.store.schedule_save()

    def _on_icon(self, tab, icon):
        storage.save_favicon(tab.url, icon)
        tab.changed.emit()

    def _on_load_started(self, tab):
        tab.loading = True
        if tab is self.current_tab:
            self.content.set_progress(5)

    def _on_load_finished(self, tab, ok):
        tab.loading = False
        if ok:
            self.store.record_history(tab.url, tab.title)
        self.page_index.page_loaded(tab, ok)
        if tab is self.current_tab:
            self.content.set_progress(100)
            self.sidebar.update_nav()
        self.extensions.tab_updated(tab, status="complete")

    def handle_new_window(self, source_tab, request, profile=None):
        Dest = QWebEngineNewWindowRequest.DestinationType
        dest = request.destination()
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
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
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

    def _on_fullscreen(self, request):
        request.accept()
        self._set_fullscreen(request.toggleOn())

    def _set_fullscreen(self, on):
        self._fullscreen = on
        if self._fullscreen:
            self._sidebar_before_fullscreen = self.sidebar.isVisible()
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
        # Same "Save to" folder as yt-dlp downloads (Download Settings), ~/Downloads by default
        download.setDownloadDirectory(ytdlp_settings(self.store)["folder"])
        download.accept()
        item = BrowserDownload(download)
        item.private = self.private.is_private_download(download)  # noted now: the session forgets it when done
        self.downloads_window.add(item)
        download.isFinishedChanged.connect(lambda: self._download_finished(item, download))
        self.toast(f"Downloading {download.downloadFileName()}…  (⌥⌘L shows downloads)")

    def _download_finished(self, item, download):
        # Tidy Downloads: Claude gives the file a readable name (not for a private window's: that would
        # send where it came from to Claude)
        private = getattr(item, "private", False)
        if (download.state() == download.DownloadState.DownloadCompleted and not private
                and self.store.settings.get("tidy_downloads", True)):
            tidy_download(self, item, download)

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

    # --- Downloads & yt-dlp --------------------------------------------------------------------

    @property
    def downloads_window(self) -> DownloadsWindow:
        if self._downloads is None:
            self._downloads = DownloadsWindow(self)
        return self._downloads

    def save_job_posting(self, tab=None):
        tab = tab or self.current_tab
        if tab is not None:
            self.open_url(integrations.job_tracker_url(integrations.config(self.store.settings), tab.url))

    def _homebase_alert(self, text):
        self.toast(text)
        if not self.isActiveWindow() and "down" in text:
            QApplication.alert(self, 0)  # bounce the Dock icon: something you run needs you

    def show_homebase_menu(self, pos):
        self.apps.homebase_menu(pos)

    def assistant(self, other=False):
        """Who answers questions: Claude, or the local model on homebase (Settings → My Apps).
        other: the one that isn't chosen, for the second row in ⌘T. None if it can't answer."""
        local = integrations.config(self.store.settings)["ai"] == "local"
        if other:
            local = not local
        if local:
            return self.local_ai if self.local_ai.available else None
        return self.claude if self.claude.available else None

    def assistant_name(self, ai) -> str:
        return f"{self.local_ai.name} (local)" if ai is self.local_ai else "Claude"

    def show_downloads(self, url=""):
        self.downloads_window.open(url)

    def download_video(self, tab=None):
        tab = tab or self.current_tab
        self.show_downloads(tab.url if tab else "")

    def download_settings(self):
        DownloadSettings(self.store, self.theme, self).exec()

    # --- Ad blocking ----------------------------------------------------------------------------

    def show_adblock_menu(self, pos):
        tab = self.current_tab
        host = QUrl(tab.url).host() if tab else ""
        blocker = self.adblock
        menu = self._menu()
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

    # --- Extensions --------------------------------------------------------------------------

    def show_extensions_menu(self, pos):
        menu = self._menu()
        extensions_menu(self, menu)
        menu.exec(pos)

    def pinned_extensions(self):
        pinned = self.store.settings.get("pinned_extensions", [])
        return [self.extensions.extensions[i] for i in pinned
                if i in self.extensions.extensions and self.extensions.extensions[i].enabled]

    def toggle_pinned_extension(self, ext):
        pinned = self.store.settings.setdefault("pinned_extensions", [])
        if ext.id in pinned:
            pinned.remove(ext.id)
        else:
            pinned.append(ext.id)
        self.store.schedule_save()
        self.sidebar.update_extensions()

    def click_extension(self, ext, button=None):
        if ext.popup:
            button = button or self.sidebar.ext_btn
            anchor = button.mapToGlobal(QPoint(0, button.height() + 4))
            ExtensionPopup(self, ext, anchor)  # shows itself once sized
        else:  # no popup: the extension handles the click itself (chrome.action.onClicked)
            tab = self.current_tab
            self.extensions.broadcast("action.clicked:" + ext.id,
                                      [self.extensions.tab_info(tab) if tab else None], only_extension=ext.id)

    def install_extension_shortcuts(self):
        """Keyboard shortcuts from extensions' manifests (e.g. ⌘⇧X opens Proton Pass), unless one would
        take over a shortcut the browser already uses."""
        for shortcut in self._extension_shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._extension_shortcuts = []
        taken = {a.shortcut().toString() for a in self.findChildren(QAction) if not a.shortcut().isEmpty()}
        for ext in self.extensions.extensions.values():
            if not ext.enabled:
                continue
            for name, keys, _description in ext.commands:
                sequence = QKeySequence(keys)
                if sequence.isEmpty() or sequence.toString() in taken:
                    continue
                taken.add(sequence.toString())
                shortcut = QShortcut(sequence, self)
                shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
                shortcut.activated.connect(lambda e=ext, n=name: self._extension_command(e, n))
                self._extension_shortcuts.append(shortcut)

    def _extension_command(self, ext, name):
        if name in ("_execute_action", "_execute_browser_action", "_execute_page_action"):
            self.click_extension(ext)
            return
        tab = self.current_tab
        self.extensions.broadcast("commands:" + ext.id, [name, self.extensions.tab_info(tab) if tab else None],
                                  only_extension=ext.id)

    def offer_userscript(self, url):
        self.installer.userscript_from_url(url)

    # --- Tabs ------------------------------------------------------------------------------

    def activate_tab(self, tab):
        loc = self.store.locate(tab)
        if loc is None:
            return
        _, space = loc
        if space is not None and space.id != self.store.active_space_id:
            self._set_active_space(space)
        previous = self.current_tab
        if (previous is not tab and isinstance(previous and previous.view, QWebEngineView) and previous.view.isVisible()
                and self.store.settings.get("ctrl_tab", "recent") == "recent"):
            # A thumbnail of the page you're leaving, for the ⌃Tab switcher
            self.thumbnails[previous.id] = previous.view.grab().scaledToWidth(
                400, Qt.TransformationMode.SmoothTransformation)
            self.thumbnails.move_to_end(previous.id)
            while len(self.thumbnails) > 12:
                self.thumbnails.popitem(last=False)
        if tab.notified:
            tab.notified = False
            tab.changed.emit()
        if is_easel(tab.url):
            self._last_easel = tab  # where captures go
        if tab.fresh:  # opening a new Live Folder item clears its dot
            folder = self.store.folder_of(tab)
            if folder is not None and folder.live:
                mark_seen(folder, tab)
                tab.changed.emit()
                self.sidebar.refresh_folder(folder)
                self.store.schedule_save()
        self.mini_player.release(tab)  # coming back to the tab the mini player was showing
        view = self._ensure_view(tab)
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
        if not self.command_bar.isVisible() and not view.hasFocus():
            view.setFocus()
        self.setWindowTitle(tab.label)
        self.sidebar.update_selection()
        self.sidebar.update_nav()
        self.store.schedule_save()
        self.extensions.tab_activated(tab)
        self.devmode.update_for_current()
        self.now_playing.tab_shown(tab)
        if not self.reader.is_open_for(tab):
            self.reader.close_reader()
        if previous is not tab:
            self.mini_player.left_tab(previous)
            self.now_playing.left_tab(previous)

    def _show_nothing(self):
        self.current_tab = None
        self.content.show_view(None)
        self.reader.close_reader()
        self.devmode.update_for_current()
        self.setWindowTitle(brand.NAME)
        self.sidebar.update_selection()
        self.sidebar.update_nav()

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
        self.store.remove_from_split(tab)
        self._destroy_view(tab)
        self.extensions.tab_removed(tab)
        if kind == TODAY:
            self._closed.append((tab.to_dict(), space.id))
            del self._closed[:-30]
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

    def delete_tab(self, tab):
        was_current = tab is self.current_tab
        self.store.remove_from_split(tab)
        self._destroy_view(tab)
        self.store.remove_tab(tab)
        if was_current:
            self._activate_fallback()
        else:
            self._relayout_split()

    def _relayout_split(self):
        # A tab in the visible Split View went away: lay the remaining panes out again
        if self.content.splitting and self.current_tab is not None and self.store.locate(self.current_tab):
            self.activate_tab(self.current_tab)

    def reopen_closed_tab(self, index=-1):
        """Bring back the last tab you closed (or the one at `index` in the closed list)."""
        if not self._closed:
            return
        data, space_id = self._closed.pop(index)
        tab = Tab.from_dict(data)
        space = self.store.space(space_id) or self.store.active_space
        self.store.add_tab(tab, TODAY, space, 0)
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

    def move_tab(self, node, kind, index=None, folder=None):
        loc = self.store.locate(node)
        self.store.move_tab(node, kind, None if kind == FAVORITES else self.store.active_space, index, folder)
        if isinstance(node, Tab) and loc and loc[0] == TODAY and kind == PINNED:
            self.tidy_title(node)

    # --- Folders -----------------------------------------------------------------------------

    def toggle_folder(self, folder):
        self.store.set_folder_expanded(folder, not folder.expanded)

    def new_folder(self, tab=None):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        folder = self.store.add_folder(self.store.active_space, name.strip())
        if tab is not None:
            self.move_tab(tab, PINNED, None, folder)

    def new_live_folder(self, preset=None):
        dialog = LiveFolderDialog(self, preset)
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            result = dialog.result_live()
            if result is not None:
                break
            self.toast("Fill in the address (and a token, if you chose one)")
        live, name, token = result
        if name:
            live["named"] = True
        self.create_live_folder(live, name or (feed_name(live["url"]) if live.get("url") else "Live Folder"), token)

    def create_live_folder(self, live, name, token=None):
        folder = self.store.add_folder(self.store.active_space, name)
        folder.live = live
        if token and not AppsUI.set_token(folder, token):  # into the Keychain, never the session file
            self.toast("Couldn't keep the token in the Keychain: set it from the folder's menu")
        self.store._changed()
        self.toast(f"Filling “{name}”…")
        self.live_folders.refresh(folder, lambda error: self.toast(
            f"Couldn't update “{folder.name}”: {error}" if error else f"“{folder.name}” is up to date"))

    def rename_folder(self, folder):
        name, ok = QInputDialog.getText(self, "Rename Folder", "Folder name:", text=folder.name)
        if ok and name.strip():
            folder.name = name.strip()
            self.store._changed()

    def delete_folder(self, folder):
        tabs = list(walk_tabs(folder.children))
        if tabs:
            answer = QMessageBox.question(self, "Delete Folder",
                                          f"Delete “{folder.name}” and the {len(tabs)} tabs in it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        if folder.live and folder.live.get("auth") == "token":
            AppsUI.forget_token(folder)  # nothing left behind in the Keychain
        current_inside = self.current_tab in tabs
        for tab in tabs:
            self._destroy_view(tab)
        self.store.remove_tab(folder)
        if current_inside:
            self._activate_fallback()
        else:
            self._relayout_split()

    def show_folder_menu(self, folder, pos):
        menu = self._menu()
        self._act(menu, "ph.pencil-simple", "Rename…", lambda: self.rename_folder(folder))
        self._act(menu, "ph.folder" if folder.expanded else "ph.folder-open",
                  "Collapse" if folder.expanded else "Expand", lambda: self.toggle_folder(folder))
        self._act(menu, "ph.folder-plus", "New Folder…", self.new_folder)
        if folder.live:
            self._act(menu, "ph.arrows-clockwise", "Refresh Now", lambda: self.live_folders.refresh(
                folder, lambda error: self.toast(f"Couldn't update “{folder.name}”: {error}" if error
                                                 else f"“{folder.name}” is up to date")))
            self._act(menu, "ph.checks", "Mark All as Seen", lambda: self._mark_folder_seen(folder))
            if folder.live.get("auth") == "token":
                self._act(menu, "ph.key", "Set Token…", lambda: self._set_folder_token(folder))
        menu.addSeparator()
        self._act(menu, "ph.folder-minus", "Ungroup (Keep Tabs)", lambda: self.store.ungroup_folder(folder))
        self._act(menu, "ph.trash", "Delete Folder…", lambda: self.delete_folder(folder))
        menu.exec(pos)

    def _set_folder_token(self, folder):
        token, ok = QInputDialog.getText(self, "Set Token", f"Token for “{folder.name}” (kept in the Keychain):",
                                         QLineEdit.EchoMode.Password)
        if not ok or not token.strip():
            return
        if AppsUI.set_token(folder, token.strip()):
            self.live_folders.refresh(folder, lambda error: self.toast(
                f"Couldn't update “{folder.name}”: {error}" if error else f"“{folder.name}” is up to date"))
        else:
            self.toast("Couldn't keep the token in the Keychain")

    def _mark_folder_seen(self, folder):
        for tab in folder.children:
            if isinstance(tab, Tab) and tab.fresh:
                mark_seen(folder, tab)
                tab.changed.emit()
        self.store._changed()

    def toggle_pin(self, tab=None):
        tab = tab or self.current_tab
        loc = tab and self.store.locate(tab)
        if not loc:
            return
        space = self.store.active_space
        if loc[0] == TODAY:
            self.store.move_tab(tab, PINNED, loc[1], len(loc[1].pinned))
            self.toast("Pinned")
            self.tidy_title(tab)
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
        kind, _ = self.store.locate(tab)
        was_current = tab is self.current_tab
        if isinstance(tab.view, QWebEngineView) and tab.view.page().profile() is not self.profile_named(space.profile):
            self._destroy_view(tab)  # reopen it with the other Space's profile
        self.store.move_tab(tab, PINNED if kind == PINNED else TODAY, space, 0 if kind == TODAY else None)
        if was_current:
            self.switch_space(space)
            self.activate_tab(tab)

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
        for tab in tabs:
            self._destroy_view(tab)
            self.store.archive_tab(tab, space)
        archived = self.store.archive[:count]
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
        stale = self.store.stale_today_tabs(exclude=on_screen)
        for tab, space in stale:
            self._destroy_view(tab)
            self.store.archive_tab(tab, space)
        if stale:
            self._relayout_split()
            self.toast(f"Archived {len(stale)} inactive tab{'s' if len(stale) != 1 else ''}")

    def show_tab_menu(self, tab, pos):
        kind, space = self.store.locate(tab)
        menu = self._menu()
        self._act(menu, "ph.link", "Copy Link", lambda: self.copy_url(tab))
        self._act(menu, "ph.link-simple", "Copy as Markdown Link", lambda: self.copy_markdown_link(tab))
        if not is_note(tab.url) and not is_easel(tab.url):  # two editors on one file would fight over it
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
        menu.addSeparator()
        if kind == TODAY:
            self._act(menu, "ph.push-pin", "Pin Tab", lambda: self.toggle_pin(tab))
        if kind == PINNED:
            self._act(menu, "ph.push-pin-slash", "Unpin Tab", lambda: self.toggle_pin(tab))
        self._act(menu, "ph.star", "Remove from Favorites" if kind == FAVORITES else "Add to Favorites",
                  lambda: self.toggle_favorite(tab))
        repos_root = integrations.config(self.store.settings)["repos_root"]
        clone = integrations.local_clone(tab.url or tab.home_url or "", repos_root)
        if clone:  # a repo you have here (a GitHub page, or a Repo Radar item)
            self._act(menu, "ph.code", "Open Repo in Loom", lambda: integrations.open_in_app("Loom", clone))
            self._act(menu, "ph.folder-open", "Show Repo in Finder",
                      lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(clone)))
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
        else:
            if tab.view is not None:
                self._act(menu, "ph.minus-circle", "Unload Tab", lambda: self.close_tab(tab))
            self._act(menu, "ph.trash", "Delete", lambda: self.delete_tab(tab))
        menu.exec(pos)

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

    def toggle_reader(self):
        if self.reader.isVisible():
            self.reader.close_reader()
            return
        view = self._current_view()
        if view is None:
            return
        self.reader.open_for(self.current_tab, view)

    def toggle_translate(self):
        if self.reader.is_open_for(self.current_tab):
            self.translator.toggle(self.reader.view, self.current_tab, reader=True)
        else:
            self.translator.toggle(self._current_view(), self.current_tab)

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
        self.activateWindow()

    def _set_external_links(self, mode):
        self.store.settings["external_links"] = mode
        self.store.schedule_save()

    def set_as_default_browser(self):
        if macos.bundle_identifier() != macos.APP_BUNDLE_ID:
            QMessageBox.information(
                self, "Set as Default Browser",
                "macOS only lets an installed app be the default browser.\n\n"
                "Build it once with:   ./run.sh --build-app\n\n"
                f"then open {brand.NAME} from your Applications folder and choose this again.")
            return
        if macos.set_default_browser(macos.APP_BUNDLE_ID):
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
            rect = QRect(self.splitter.mapTo(root, QPoint(0, 0)), QSize(width, self.splitter.height()))
            first = root.grab(rect)
        self.sidebar.setVisible(visible)
        self.store.settings["sidebar_visible"] = visible
        self.store.schedule_save()
        self._update_content_margins()
        if animate:
            # …and after: then the sidebar slides over (or out of) the page that's there now
            if not visible:
                motion.SidebarSlide(root, rect, first, root.grab(rect), showing=False)
                return
            # Coming back it has to be laid out before it can be pictured: hold the old look a moment
            hold = motion.SidebarSlide(root, rect, first, first, showing=False, hold=True)

            def slide_in():
                hold.cover.hide()
                arrived = root.grab(rect)
                hold.cover.deleteLater()
                motion.SidebarSlide(root, rect, arrived, first, showing=True)
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

    def set_claude_model(self, model):
        self.store.settings["claude_model"] = model
        self.store.schedule_save()
        self.claude.shutdown()  # the warm process was started with the old model
        self.claude.model = model
        self.claude.warm_up()
        self.toast(f"Claude features now use {model.capitalize()}")

    def toggle_claude(self):
        if self.claude.exe is None:
            self.toast("Claude Code CLI not found — install it, then run `claude` once to log in")
            return
        self.claude.enabled = not self.claude.enabled
        self.store.settings["claude_enabled"] = self.claude.enabled
        self.store.schedule_save()
        self.claude_action.setChecked(self.claude.enabled)
        if self.claude.enabled:
            self.claude.warm_up()
        else:
            self.claude.shutdown()
        self.toast("Claude features on" if self.claude.enabled else "Claude features off")

    def tidy_title(self, tab, force=False):
        """Arc's "Tidy Tab Titles": give a pinned tab a short, human name."""
        if not tab or not tab.url or not self.claude.available or (tab.custom_title and not force):
            return
        request = self.claude.ask(
            page_block(tab.title or "(none)", tab.url, "") +
            "\n\nRename this browser tab to a short, clean label of 1-4 words, the way a person would "
            "name a bookmark (for example 'GitHub Issues' or 'Q3 Budget'). Reply with the label only.")
        request.finished.connect(lambda name: self._apply_tidy(tab, name, force))
        if force:
            request.failed.connect(self.toast)

    def _apply_tidy(self, tab, name, force):
        lines = name.strip().splitlines()
        name = lines[0].strip(" \"'“”‘’`*.") if lines else ""
        loc = self.store.locate(tab)
        if not name or len(name) > 40 or not loc or (tab.custom_title and not force):
            return
        if loc[0] == TODAY and not force:
            return  # it was unpinned while Claude was thinking
        tab.custom_title = name
        tab.changed.emit()
        self.store.schedule_save()

    def ask_page(self, question, on_request):
        """Arc's "Ask on Page": read the current page's text, then ask Claude about it."""
        tab = self.current_tab
        if self._current_view() is None:
            return
        ai = self.assistant() or self.claude

        def got_text(text):
            on_request(ai.ask(
                page_block(tab.label, tab.url, text) +
                "\n\nUsing the page above, answer the question below in at most 150 words, using Markdown "
                "where it helps. If the page doesn't cover it, say so briefly, then answer from general "
                "knowledge if you can.\n\nQuestion: " + question))
        tab.view.page().toPlainText(got_text)

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
            if event.key() == Qt.Key.Key_Escape and self.peek.isVisible() and self._zap_tab is None:
                self.peek.close_peek()
                return True
        elif kind == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Shift and not event.isAutoRepeat():
            if self._shift_alone and self._hovered_link:
                self.preview_link(self._hovered_link)
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
            # Arc: with the sidebar hidden, touching the left edge slides it out
            pos = self.mapFromGlobal(QCursor.pos())
            if 0 <= pos.x() <= FLOAT_EDGE and 0 <= pos.y() <= self.height() and self.isActiveWindow():
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
        self.splitter.insertWidget(0, self.sidebar)
        self.splitter.setSizes([self.store.settings.get("sidebar_width", 250), 1000])
        self.sidebar.align_with_traffic_lights()

    # --- Tidy Tabs (Arc Max) ----------------------------------------------------------------------

    def tidy_today(self):
        """Ask Claude to group the loose Today tabs into a few topic folders."""
        space = self.store.active_space
        loose = [n for n in space.today if isinstance(n, Tab)]
        if len(loose) < TIDY_MIN_TABS:
            self.toast(f"Tidy needs at least {TIDY_MIN_TABS} loose tabs in Today")
            return
        if not self.claude.available:
            self.toast("Tidy Tabs needs Claude features turned on (Settings → Claude)")
            return
        self.sidebar.set_tidy_state("busy")
        request = self.claude.ask(build_tidy_prompt(loose))
        request.finished.connect(lambda reply: self._apply_tidy_groups(space, loose, reply))
        request.failed.connect(lambda message: (self.sidebar.set_tidy_state("idle"),
                                                self.toast(f"Couldn't tidy: {message}")))

    def _apply_tidy_groups(self, space, tabs, reply):
        before = list(space.today)
        still_loose = {id(n) for n in before}
        # Tabs closed or moved while Claude was thinking are left alone
        groups = [(name, [tabs[i] for i in indexes if id(tabs[i]) in still_loose])
                  for name, indexes in parse_tidy_groups(reply, len(tabs))]
        groups = [(name, members) for name, members in groups if len(members) >= 2]
        if not groups:
            self.sidebar.set_tidy_state("idle")
            self.toast("Your tabs already look tidy")
            return
        grouped = {id(t) for _, members in groups for t in members}
        space.today[:] = ([Folder(name, members, expanded=False) for name, members in groups] +
                          [n for n in before if id(n) not in grouped])
        self._tidy_undo = (space, before)
        self.store._changed()
        self.sidebar.set_tidy_state("undo")
        QTimer.singleShot(15_000, self._expire_tidy_undo)
        self.toast(f"Tidied {len(grouped)} tabs into {len(groups)} folders")

    def undo_tidy(self):
        if not self._tidy_undo:
            return
        space, before = self._tidy_undo
        self._tidy_undo = None
        alive = {id(t) for t in walk_tabs(space.today)}
        restored = [n for n in before if isinstance(n, Folder) or id(n) in alive]  # closed tabs stay closed
        known = {id(t) for t in walk_tabs(restored)}
        opened_since = [t for t in walk_tabs(space.today) if id(t) not in known]
        space.today[:] = opened_since + restored
        self.store._prune_empty_folders(space.today)
        self.store._changed()
        self.sidebar.set_tidy_state("idle")
        self.toast("Put your tabs back")

    def _expire_tidy_undo(self):
        if self._tidy_undo:
            self._tidy_undo = None
            self.sidebar.set_tidy_state("idle")

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

    def browse_for_me(self, query):
        """Arc Max's "Browse for Me": read the top search results and open one answer page."""
        query = query.strip()
        if not query:
            return
        if not self.claude.available:
            self.toast("Browse for Me needs Claude features turned on (View menu)")
            return
        path = answer_path(new_run_id())
        state = {"sources": [], "status": "Searching…", "body": ""}

        def write():
            path.write_text(render_page(query, state["sources"], state["body"], state["status"],
                                        self.theme.accent.name()))
        write()
        tab = Tab(QUrl.fromLocalFile(str(path)).toString(), "✦ " + query)
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        self.activate_tab(tab)

        def update(element, content):
            view = tab.view
            if isinstance(view, QWebEngineView) and view.url().toLocalFile() == str(path):
                view.page().runJavaScript(js_set(element, content))

        def set_status(text):
            state["status"] = text
            update("status", html.escape(text))

        def got_sources(sources):
            state["sources"] = sources
            write()
            if isinstance(tab.view, QWebEngineView):
                tab.view.reload()  # show the source cards

        def finish(body, status=""):
            state["body"], state["status"] = body, status
            write()  # the saved page has the final answer, for reloads and restarts
            update("answer", body)
            update("status", html.escape(status))
            self._browse_runs.discard(run)

        run = BrowseForMe(self.claude, self.profile_for_tab(tab), query, self)
        self._browse_runs.add(run)
        run.status.connect(set_status)
        run.sources_found.connect(got_sources)
        run.partial.connect(lambda body: (state.__setitem__("body", body), update("answer", body)))
        run.finished.connect(finish)
        run.failed.connect(lambda message: finish(state["body"], f"Couldn't finish: {message}"))
        run.start()

    # --- Notes ------------------------------------------------------------------------------------

    def new_note(self):
        tab = Tab(new_note_url(), "Untitled Note")
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        self.activate_tab(tab)

    # --- Easels ------------------------------------------------------------------------------------

    def new_easel(self, activate=True):
        tab = Tab(new_easel_url(), "Untitled Easel")
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        if activate:
            self.activate_tab(tab)
        else:
            self._ensure_view(tab)
        return tab

    def capture_to_easel(self):
        """Arc's Capture (⇧⌘2): drag a box over the page; that part goes onto your easel."""
        view, tab = self._current_view(), self.current_tab
        if view is None:
            self.toast("Capture works on web pages")
            return
        overlay = CaptureOverlay(view)
        overlay.captured.connect(lambda image, ratio: self._add_capture(image, ratio, tab.url, tab.label))

    def _add_capture(self, image, ratio, url, title):
        # The easel you used last, if it's still around; otherwise a new one
        easel = self._last_easel if self._last_easel is not None and self.store.locate(self._last_easel) else None
        if easel is None:
            easel = self._last_easel = self.new_easel(activate=False)
        self._ensure_view(easel).add_image(image, source=url, caption=title, pixel_ratio=ratio)
        self.toast(f"Captured to “{easel.label}”")

    # --- Boosts -----------------------------------------------------------------------------------

    def boost_site(self, boost=None):
        if boost is None:
            site = site_of(self.current_tab.url) if self.current_tab else ""
            if not site:
                self.toast("Open a website first, then boost it")
                return
            boost = next(iter(self.boosts.for_site(site)), None) or Boost(site)
        if BoostEditor(self.boosts, boost, self.theme, self).exec():
            self.reload()

    def start_zap(self):
        view = self._current_view()
        if view is None or not site_of(self.current_tab.url):
            self.toast("Open a website first, then zap it")
            return
        self._zap_tab = self.current_tab
        view.setFocus()
        view.page().runJavaScript(ZAP_SCRIPT)

    def _on_console(self, tab, message):
        if message.startswith(STORE_MESSAGE):
            # "Add to Eclipse" on a Chrome Web Store page: only our button knows the secret, and
            # only on the store itself
            prefix = self.extensions.store_prefix
            if (message.startswith(prefix) and QUrl(tab.url).host() == "chromewebstore.google.com"
                    and not self.installer.busy):
                self.installer.extension_from_store(message[len(prefix):])
            return
        if tab is not self._zap_tab:
            return  # only listen while the Zap tool is running on that tab
        if message.startswith(ZAP_MESSAGE):
            boost = self.boosts.add_zapped(site_of(tab.url), message[len(ZAP_MESSAGE):])
            self.toast(f"Zapped · {len(boost.zapped)} hidden on {boost.site}")
        elif message == ZAP_DONE:
            self._zap_tab = None

    def _fill_boosts_menu(self, menu):
        menu.clear()
        menu.addAction("Boost This Site…", self.boost_site)
        menu.addAction("Zap Elements on This Page", self.start_zap)
        if self.boosts.boosts:
            menu.addSeparator()
            for boost in self.boosts.boosts:
                action = menu.addAction(boost.label + ("" if boost.enabled else "  (off)"),
                                        lambda b=boost: self.boost_site(b))
                action.setToolTip(boost.site)

    def toggle_peek(self):
        enabled = not self.store.settings.get("peek_enabled", True)
        self.store.settings["peek_enabled"] = enabled
        self.store.schedule_save()
        self.toast("Peek on: links from pinned tabs open in a preview" if enabled else "Peek off")

    def preview_link(self, url):
        """Arc's "5-second previews": hover a link and tap Shift to see a summary of where it goes."""
        card = self.content.preview_card
        if not url.startswith(("http://", "https://")) or not self.claude.available:
            return
        if url == card.url and card.isVisible():
            card.keep()
            return
        if self._preview_request is not None:
            self._preview_request.cancel()
            self._preview_request = None
        card.show_for(url)
        if url in self._preview_cache:
            card.set_content(*self._preview_cache[url])
            return

        # Read the page off-screen (same cookies as your tabs), then hand its text to Claude
        if self._preview_page is not None:
            self._preview_page.cancel()
        reader = self._preview_page = PageReader(self.profile, url, self, timeout_ms=8_000, enough_chars=800)
        reader.done.connect(lambda title, text: self._summarize_preview(url, title or display_host(url), text))

    def _summarize_preview(self, url, title, text):
        card = self.content.preview_card
        if card.url != url:
            return
        if not text.strip():
            card.set_content(title, "Couldn't read this page.")
            return
        card.set_content(title, "Summarizing…")
        request = self.claude.ask(
            page_block(title, url, text) +
            "\n\nWrite a link preview for this page: 2-3 short sentences on what it is and its key "
            "takeaway. Plain text only, no preamble.")
        self._preview_request = request
        request.partial.connect(lambda t: card.url == url and card.set_content(None, t))
        request.finished.connect(lambda t: self._finish_preview(url, title, t))
        request.failed.connect(lambda m: card.url == url and card.set_content(None, f"Couldn't summarize: {m}"))

    def _finish_preview(self, url, title, summary):
        self._preview_cache[url] = (title, summary)
        if self.content.preview_card.url == url:
            self.content.preview_card.set_content(title, summary)

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
            if isinstance(tab.view, (NoteEditor, EaselView)):
                tab.view.save()
        self.extensions._flush_storage()
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
        made = time.strftime("%-d %B %Y at %-I:%M %p", time.localtime(info["created"]))
        answer = QMessageBox.question(
            self, "Restore from Backup",
            f"Replace your Spaces, tabs, settings, notes and extensions with this backup from {made}?\n\n"
            f"It has {len(info['spaces'])} Space(s) ({', '.join(info['spaces'][:5])}) and {info['tabs']} tabs. "
            "What you have now is kept in a folder, not deleted. Sign-ins and cookies aren't affected.\n\n"
            f"{brand.NAME} will quit; open it again to finish.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup.stage(Path(path))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Can't Restore", str(error))
            return
        QApplication.instance().quit()

    def import_from_arc(self):
        if not arc_import.arc_installed():
            self.toast("Couldn't find Arc's data on this Mac")
            return
        try:
            arc = arc_import.read_arc()
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(self, "Import from Arc", f"Couldn't read Arc's data:\n{error}")
            return

        lines = [
            f"• {len(arc.spaces)} Spaces: " + ", ".join(s.name for s in arc.spaces),
            f"• {arc.tab_count} tabs, including {len(arc.favorites)} Favorites, in {arc.folder_count} folders",
            f"• {len(arc.history):,} history entries (for ⌘T suggestions) and site icons",
        ]
        note = ("You'll need to sign in to sites again: Arc's logins and cookies are encrypted "
                "with its own key and can't be copied.")
        if arc.extensions:
            note += ("\n\nYour extensions (" + ", ".join(arc.extensions) + ") come separately: Settings → "
                     "Extensions → Add Extension → Import from Arc. Some may not fully work here.")
        box = QMessageBox(self)
        box.setWindowTitle("Import from Arc")
        box.setText(f"Bring your Arc setup into {brand.NAME}?")
        box.setInformativeText("\n".join(lines) + "\n\n" + note +
                               "\n\nImporting again later replaces these Spaces with Arc's current state.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        import_btn = box.button(QMessageBox.StandardButton.Yes)
        import_btn.setText("Import")
        box.setDefaultButton(import_btn)
        box.exec()
        if box.clickedButton() is not import_btn:
            return

        before = list(self.store.all_tabs())
        arc_import.apply(self.store, arc)
        remaining = set(map(id, self.store.all_tabs()))
        for tab in before:
            if id(tab) not in remaining:
                self._destroy_view(tab)
        if self.current_tab is not None and id(self.current_tab) not in remaining:
            self.current_tab = None
        self.apply_theme()
        self._restore_space_tab()
        self.toast(f"Imported {len(arc.spaces)} Spaces and {arc.tab_count} tabs from Arc")

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
        spaces = self.store.spaces

        def change():
            self._set_active_space(space)
            self._restore_space_tab()
        self.space_swipe.abandon()  # (a swipe under way gives way to this switch)
        if (self.sidebar.isVisible() and self.isVisible() and self.store.active_space in spaces
                and not self.floating_sidebar.active):
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
            self._destroy_view(tab)
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
            ("Copy URL", "ph.link", self.copy_url),
            ("Copy as Markdown Link", "ph.link-simple", self.copy_markdown_link),
            ("Print…", "ph.printer", self.print_page),
            ("Save as PDF", "ph.file-pdf", self.save_pdf),
            ("Save Page As", "ph.floppy-disk", self.save_page),
            ("Pin / Unpin Tab", "ph.push-pin", self.toggle_pin),
            ("Add / Remove Favorite", "ph.star", self.toggle_favorite),
            ("Clear Today's Tabs", "ph.archive", self.clear_today),
            ("Put Other Tabs to Sleep", "ph.moon-stars", self.sleep_other_tabs),
            ("Mute / Unmute Tab", "ph.speaker-slash", self.toggle_mute),
            ("Do Not Disturb On / Off (Site Notifications)", "ph.bell-slash", self.toggle_dnd),
            ("Tidy Today's Tabs", "ph.sparkle", self.tidy_today),
            ("New Little Window", "ph.app-window", self.new_little_window),
            ("New Private Window", "ph.ghost", lambda: self.private.new_window()),
            ("Reopen Closed Tab", "ph.clock-counter-clockwise", self.reopen_closed_tab),
            ("Reader View", "ph.book-open-text", self.toggle_reader),
            (f"Check for {brand.NAME} Updates", "ph.arrows-clockwise", lambda: self.self_updater.check(manual=True)),
            ("What's New", "ph.gift", self.show_whats_new),
            ("Show Crash Logs", "ph.bug", self.show_crash_logs),
            ("Translate Page / Show Original", "ph.translate", self.toggle_translate),
            ("Open Developer Tools", "ph.terminal-window", self.open_devtools),
            ("Edit Space (Name, Icon, Colour, Profile)", "ph.pencil-simple", self.edit_space),
            ("Edit Space Theme", "ph.paint-brush", self.edit_space_theme),
            ("Settings", "ph.gear-six", self.open_settings),
            ("Library (History, Archive, Notes)", "ph.books", self.open_library),
            ("Keyboard Shortcuts", "ph.keyboard", lambda: self.open_settings("shortcuts")),
            ("Import from Arc", "ph.arrow-square-in", self.import_from_arc),
            ("Back Up Everything", "ph.archive-box", self.back_up),
            ("Restore from Backup", "ph.clock-counter-clockwise", self.restore_backup),
            ("Extensions & Userscripts", "ph.puzzle-piece", lambda: self.open_settings("extensions")),
            ("Install Extension from Chrome Web Store", "ph.puzzle-piece",
             lambda: self.installer.extension_from_store()),
            ("Import Extensions from Arc", "ph.arrow-square-in", self.installer.import_from_arc),
            ("Download Video from This Page", "ph.film-strip", self.download_video),
            ("Show Downloads", "ph.download-simple", self.show_downloads),
            ("Download Settings", "ph.gear-six", self.download_settings),
            ("Turn Ad Blocking On / Off", "ph.shield-check", self.toggle_adblock),
            ("Allow / Block Ads on This Site", "ph.shield-slash", self.allow_ads_here),
            ("New Folder", "ph.folder-plus", self.new_folder),
            ("New Live Folder (Feed or GitHub)", "ph.rss", self.new_live_folder),
            ("New Note", "ph.note-pencil", self.new_note),
            ("New Easel", "ph.chalkboard-simple", self.new_easel),
            ("Capture Part of This Page to an Easel", "ph.selection-plus", self.capture_to_easel),
            ("Split View with a New Tab", "ph.columns", lambda: self.open_command_bar("split")),
            ("Separate Split View", "ph.square-half", self.separate_split),
            ("Boost This Site", "ph.rocket-launch", self.boost_site),
            ("Zap Elements on This Page", "ph.lightning", self.start_zap),
            ("Turn Peek On / Off", "ph.eye", self.toggle_peek),
            ("Tidy Tab Title", "ph.magic-wand", lambda: self.tidy_title(self.current_tab, force=True)),
            ("Turn Claude Features On / Off", "ph.sparkle", self.toggle_claude),
        ]
        cmds += [(f"Switch to Space: {s.name}", "ph.dots-three", lambda s=s: self.switch_space(s))
                 for s in self.store.spaces]
        # Your own apps: the current page's Send To actions, and homebase
        cmds += [(label, icon, run) for label, icon, run in self.apps.send_to_actions(self.current_tab)]
        if self.homebase.url:
            cmds += [("Homebase Dashboard", "ph.gauge", lambda: self.open_url(self.homebase.url + "/")),
                     ("Add Live Folders for My Apps", "ph.folders", self.apps.add_live_folders)]
            # Each app homebase runs, by name: "restart job tracker", "logs gradtrack"
            for app in (self.homebase.status or {}).get("apps") or []:
                title, name = app.get("title") or app.get("name"), app.get("name")
                if app.get("url"):
                    cmds.append((f"Open {title}", "ph.arrow-square-out", lambda u=app["url"]: self.open_url(u)))
                if app.get("state") != "external":
                    cmds.append((f"Restart {title}", "ph.arrows-clockwise", lambda n=name: self.apps._act(n, "restart")))
                cmds.append((f"Logs: {title}", "ph.file-text", lambda n=name, t=title: self.apps.show_logs(n, t)))
        return cmds

    def _build_menus(self):
        bar = self.menuBar()

        def add(menu, text, shortcut, slot):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False: slot())
            menu.addAction(action)
            self.addAction(action)  # keeps the shortcut working when the menu bar isn't native

        file = bar.addMenu("File")
        add(file, "New Tab", "Ctrl+T", lambda: self.open_command_bar("new"))
        add(file, "New Little Window", "Ctrl+Alt+N", self.new_little_window)
        add(file, "New Private Window", "Ctrl+Shift+P", lambda: self.private.new_window())
        add(file, "Open Location…", "Ctrl+L", lambda: self.open_command_bar("current"))
        add(file, "Reopen Closed Tab", "Ctrl+Shift+T", self.reopen_closed_tab)
        add(file, "New Note", "Ctrl+Shift+N", self.new_note)
        add(file, "New Easel", "", self.new_easel)
        add(file, "Capture to Easel", "Ctrl+Shift+2", self.capture_to_easel)
        file.addSeparator()
        add(file, "Downloads", "Ctrl+Alt+L", self.show_downloads)
        add(file, "Download Video…", "Ctrl+Shift+D", self.download_video)
        file.addSeparator()
        add(file, "Print…", "Ctrl+P", self.print_page)
        add(file, "Save as PDF…", "Ctrl+Alt+P", self.save_pdf)
        add(file, "Save Page As…", "Ctrl+Shift+S", self.save_page)
        add(file, "Download Settings…", "", self.download_settings)
        file.addSeparator()
        add(file, "Import from Arc…", "", self.import_from_arc)
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
        add(view, "Translate Page / Show Original", "", self.toggle_translate)
        add(view, "Developer Tools", "Ctrl+Alt+I", self.open_devtools)
        add(view, "Developer Console", "Ctrl+Alt+J", self.toggle_dev_console)
        view.addSeparator()
        self.claude_action = QAction("Claude Features", self, checkable=True)
        self.claude_action.setChecked(self.claude.available)
        self.claude_action.triggered.connect(lambda _=False: self.toggle_claude())
        view.addAction(self.claude_action)
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

        tabs = bar.addMenu("Tabs")
        add(tabs, "Pin / Unpin Tab", "Ctrl+D", self.toggle_pin)
        add(tabs, "Add / Remove Favorite", "", self.toggle_favorite)
        add(tabs, "Tidy Tab Title", "", lambda: self.tidy_title(self.current_tab, force=True))
        add(tabs, "Clear Today's Tabs", "Ctrl+Shift+K", self.clear_today)
        add(tabs, "Put Other Tabs to Sleep", "", self.sleep_other_tabs)
        add(tabs, "Tidy Today's Tabs", "", self.tidy_today)
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

        boosts = bar.addMenu("Boosts")
        boosts.aboutToShow.connect(lambda: self._fill_boosts_menu(boosts))
        self._fill_boosts_menu(boosts)

        spaces = bar.addMenu("Spaces")
        add(spaces, "New Space…", "", self.new_space)
        add(spaces, "Next Space", "Ctrl+Alt+Right", lambda: self.cycle_space(1))
        add(spaces, "Previous Space", "Ctrl+Alt+Left", lambda: self.cycle_space(-1))
        spaces.addSeparator()
        for n in range(1, 10):
            add(spaces, f"Space {n}", f"{CONTROL}+{n}", lambda n=n: self.switch_to_space_number(n))
        self.install_extension_shortcuts()  # after the menus, so they can't steal a browser shortcut

    # --- Shutdown --------------------------------------------------------------------------

    def closeEvent(self, event):
        self.store.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.store.save()
        super().closeEvent(event)

    def shutdown(self):
        self.claude.shutdown()
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
        self.extensions.shutdown()
        # Pages must be destroyed before their profile, or Qt warns and may leak the profile
        if self._preview_page is not None:
            self._preview_page.cancel()
            _delete(self._preview_page.page)
        if self.peek.view is not None:
            _delete(self.peek.view)
        for window in list(self._extra_windows):
            _delete(window)
        for tab in self.store.all_tabs():
            if isinstance(tab.view, (NoteEditor, EaselView)):
                tab.view.save()
            if tab.view is not None:
                _delete(tab.view)
                tab.view = None
