# The main window doubles as the controller: it owns the Store (data), the
# web views, and every user action. Sidebar/command bar widgets call into it.

import html
import re
import sys
import time

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QEvent, QPoint, QStandardPaths, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QCursor, QGuiApplication, QKeySequence
from PyQt6.QtWebEngineCore import QWebEngineNewWindowRequest, QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QApplication, QColorDialog, QGraphicsDropShadowEffect, QHBoxLayout, QInputDialog,
                             QMainWindow, QMenu, QMessageBox, QSplitter, QVBoxLayout, QWidget)

from . import arc_import, macos, storage
from .adblock import AdBlocker
from .ai import Claude, page_block
from .browse import BrowseForMe, PageReader, answer_path, js_set, new_run_id, render_page
from .boosts import ZAP_DONE, ZAP_MESSAGE, ZAP_SCRIPT, Boost, BoostEditor, BoostManager, site_of
from .command_bar import CommandBar
from .content import ContentFrame
from .downloads import BrowserDownload, DownloadSettings, DownloadsWindow, ytdlp_settings
from .little import LittleWindow
from .notes import NoteEditor, is_note, new_note_url
from .peek import PeekOverlay
from .split import MAX_PANES
from .models import FAVORITES, PINNED, TODAY, Folder, Tab, display_host, walk_folders, walk_tabs
from .sidebar import Sidebar
from .theme import PALETTE, ThemedBackground, dot_icon, stylesheet, theme_for_space
from .theme_editor import ThemeEditor
from .webview import DEFAULT_PROFILE, WebView, build_profile

MAC = sys.platform == "darwin"
# Qt maps "Ctrl" to ⌘ on macOS; the physical Control key is "Meta" there
CONTROL = "Meta" if MAC else "Ctrl"
ARCHIVE_CHOICES = [(12, "12 hours"), (24, "1 day"), (24 * 7, "7 days"), (24 * 30, "30 days"), (0, "Never")]
ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]


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
        self.claude = Claude(self)
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
        self._downloads: DownloadsWindow | None = None
        self._zap_tab: Tab | None = None
        self._browse_runs: set = set()
        self._sidebar_floating = False

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
        content_wrap = QWidget()
        self.content_layout = QVBoxLayout(content_wrap)
        self.content_layout.addWidget(self.content)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(4)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(content_wrap)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([store.settings["sidebar_width"], 1000])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        root_layout.addWidget(self.splitter)
        self.setCentralWidget(root)

        self.peek = PeekOverlay(self, root)
        self.command_bar = CommandBar(self, root)

        self.sidebar.setVisible(store.settings.get("sidebar_visible", True))
        self._update_content_margins()

        store.structure_changed.connect(self._schedule_rebuild)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self._build_menus()
        self.apply_theme()

        if store.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(store.window_geometry.encode()))
        else:
            self.resize(1280, 820)

        self._archive_timer = QTimer(self, interval=15 * 60 * 1000, timeout=self.archive_stale_tabs)
        self._archive_timer.start()
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
        elif self.sidebar.isVisible():
            self.content_layout.setContentsMargins(0, 8, 8, 8)
        else:
            # Leave a strip at the top for the traffic lights
            self.content_layout.setContentsMargins(8, 32 if MAC else 8, 8, 8)

    def _on_splitter_moved(self, *_):
        self.store.settings["sidebar_width"] = self.splitter.sizes()[0]
        self.store.schedule_save()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.command_bar.setGeometry(self.centralWidget().rect())
        if self.peek.isVisible():
            self.peek.setGeometry(self.centralWidget().rect())
        if self._sidebar_floating:
            self._place_floating_sidebar()

    def showEvent(self, event):
        super().showEvent(event)
        if MAC:
            macos.hide_title_text(self)  # otherwise the page title is drawn over the sidebar

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
        for tab in list(walk_tabs(space.pinned)) + space.today:
            self._destroy_view(tab)  # pages reload with the new profile's cookies
        self.store._changed()
        if space is self.store.active_space:
            self._restore_space_tab()
        self.toast(f"{space.name} now uses the “{name}” profile")

    # --- Web views -----------------------------------------------------------------------

    def create_view(self, profile=None) -> WebView:
        return WebView(profile or self.profile)

    def _ensure_view(self, tab, load=True):
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
        page.findTextFinished.connect(
            lambda r: tab is self.current_tab and self.content.find_bar.show_result(r.activeMatch(),
                                                                                   r.numberOfMatches()))

    def _destroy_view(self, tab):
        view, tab.view = tab.view, None
        if isinstance(view, NoteEditor):
            view.save()
        if view is not None:
            self.content.remove_view(view)
            view.deleteLater()

    def _on_url(self, tab, url):
        tab.url = url.toString()
        tab.changed.emit()
        if tab is self.current_tab:
            self.sidebar.update_nav()
        self.store.schedule_save()

    def _on_title(self, tab, title):
        tab.title = title
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
        if tab is self.current_tab:
            self.content.set_progress(100)
            self.sidebar.update_nav()

    def handle_new_window(self, source_tab, request, profile=None):
        Dest = QWebEngineNewWindowRequest.DestinationType
        dest = request.destination()
        # The new page must share the opener's profile (and so its logins)
        if source_tab is not None and isinstance(source_tab.view, QWebEngineView):
            profile = source_tab.view.page().profile()
        profile = profile or self.profile
        if dest == Dest.InNewDialog:
            # window.open() popups (sign-in flows etc.) go to a Little window
            self._track(LittleWindow(self, request=request)).show()
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
        self._fullscreen = request.toggleOn()
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
        # Same "Save to" folder as yt-dlp downloads (Download Settings), ~/Downloads by default
        download.setDownloadDirectory(ytdlp_settings(self.store)["folder"])
        download.accept()
        self.downloads_window.add(BrowserDownload(download))
        self.toast(f"Downloading {download.downloadFileName()}…  (⌥⌘L shows downloads)")

    # --- Downloads & yt-dlp --------------------------------------------------------------------

    @property
    def downloads_window(self) -> DownloadsWindow:
        if self._downloads is None:
            self._downloads = DownloadsWindow(self)
        return self._downloads

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
        menu = QMenu(self)
        if host:
            count = blocker.count_for(tab.url)
            header = menu.addAction(f"{count} ads & trackers blocked on {display_host(tab.url)}")
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
        info = menu.addAction(f"{len(blocker.domains):,} domains on the block lists")
        info.setEnabled(False)
        menu.addAction("Update Block Lists Now", lambda: (blocker.update_lists(force=True),
                                                          self.toast("Updating block lists…")))
        menu.exec(pos)

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

    def toast(self, text):
        self.content.show_toast(text)

    # --- Tabs ------------------------------------------------------------------------------

    def activate_tab(self, tab):
        loc = self.store.locate(tab)
        if loc is None:
            return
        _, space = loc
        if space is not None and space.id != self.store.active_space_id:
            self._set_active_space(space)
        view = self._ensure_view(tab)
        self.current_tab = tab
        tab.last_active = time.time()
        self.store.active_space.active_tab_id = tab.id
        if tab.id in self._mru:
            self._mru.remove(tab.id)
        self._mru.insert(0, tab.id)

        self.content.find_bar.hide()
        split = self.store.split_of(tab)
        if split:
            for other in split:
                self._ensure_view(other)
            self.content.show_split(split, tab)
        else:
            self.content.show_view(view)
        if not self.command_bar.isVisible() and not view.hasFocus():
            view.setFocus()
        self.setWindowTitle(tab.label)
        self.sidebar.update_selection()
        self.sidebar.update_nav()
        self.store.schedule_save()

    def _show_nothing(self):
        self.current_tab = None
        self.content.show_view(None)
        self.setWindowTitle("PythonBrowser")
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

    def navigate_current(self, url):
        if self.current_tab is None:
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
        self._destroy_view(tab)
        if kind == TODAY:
            self.store.remove_from_split(tab)
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

    def reopen_closed_tab(self):
        if not self._closed:
            return
        data, space_id = self._closed.pop()
        tab = Tab.from_dict(data)
        space = self.store.space(space_id) or self.store.active_space
        self.store.add_tab(tab, TODAY, space, 0)
        self.activate_tab(tab)

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
        current_inside = self.current_tab in tabs
        for tab in tabs:
            self._destroy_view(tab)
        self.store.remove_tab(folder)
        if current_inside:
            self._activate_fallback()

    def show_folder_menu(self, folder, pos):
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self.rename_folder(folder))
        menu.addAction("Collapse" if folder.expanded else "Expand", lambda: self.toggle_folder(folder))
        menu.addAction("New Folder…", self.new_folder)
        menu.addSeparator()
        menu.addAction("Ungroup (Keep Tabs)", lambda: self.store.ungroup_folder(folder))
        menu.addAction("Delete Folder…", lambda: self.delete_folder(folder))
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
        count = len(space.today)
        current_cleared = self.current_tab in space.today
        for tab in list(space.today):
            self._destroy_view(tab)
            self.store.archive_tab(tab, space)
        if current_cleared:
            self._activate_fallback()
        if count:
            self.toast(f"Archived {count} tab{'s' if count != 1 else ''}")

    def archive_stale_tabs(self):
        stale = self.store.stale_today_tabs(exclude=self.current_tab)
        for tab, space in stale:
            self._destroy_view(tab)
            self.store.archive_tab(tab, space)
        if stale:
            self.toast(f"Archived {len(stale)} inactive tab{'s' if len(stale) != 1 else ''}")

    def show_tab_menu(self, tab, pos):
        kind, space = self.store.locate(tab)
        menu = QMenu(self)
        menu.addAction("Copy Link", lambda: self.copy_url(tab))
        menu.addAction("Duplicate", lambda: self.open_url(tab.url))
        menu.addAction("Download Video…", lambda: self.download_video(tab))
        split = self.store.split_of(tab)
        if split:
            menu.addAction("Remove from Split View", lambda: self.remove_from_split(tab))
        elif self.current_tab is not None and tab is not self.current_tab:
            menu.addAction("Open in Split View", lambda: self.add_to_split(tab))
        menu.addSeparator()
        if kind == TODAY:
            menu.addAction("Pin Tab", lambda: self.toggle_pin(tab))
        if kind == PINNED:
            menu.addAction("Unpin Tab", lambda: self.toggle_pin(tab))
        menu.addAction("Remove from Favorites" if kind == FAVORITES else "Add to Favorites",
                       lambda: self.toggle_favorite(tab))
        if kind != TODAY:
            menu.addAction("Rename…", lambda: self.rename_tab(tab))
            if tab.home_url and tab.url != tab.home_url:
                menu.addAction("Go Back to Pinned URL", lambda: self._go_home(tab))
        if kind != FAVORITES:
            folders = list(walk_folders(self.store.active_space.pinned))
            into = menu.addMenu("Move to Folder")
            for folder in folders:
                if folder is not self.store.folder_of(tab):
                    into.addAction(folder.name, lambda f=folder: self.move_tab(tab, PINNED, None, f))
            if folders:
                into.addSeparator()
            into.addAction("New Folder…", lambda: self.new_folder(tab))
        others = [s for s in self.store.spaces if s is not space]
        if kind != FAVORITES and others:
            move = menu.addMenu("Move to Space")
            for other in others:
                move.addAction(dot_icon(other.color), other.name, lambda s=other: self.move_to_space(tab, s))
        menu.addSeparator()
        if kind == TODAY:
            menu.addAction("Close Tab", lambda: self.close_tab(tab))
        else:
            if tab.view is not None:
                menu.addAction("Unload Tab", lambda: self.close_tab(tab))
            menu.addAction("Delete", lambda: self.delete_tab(tab))
        menu.exec(pos)

    def _go_home(self, tab):
        self.activate_tab(tab)
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

    def zoom(self, direction):
        view = self._current_view()
        if not view:
            return
        if direction == 0:
            factor = 1.0
        else:
            current = view.zoomFactor()
            steps = [z for z in ZOOM_STEPS if (z > current + 0.01 if direction > 0 else z < current - 0.01)]
            if not steps:
                return
            factor = steps[0] if direction > 0 else steps[-1]
        view.setZoomFactor(factor)
        self.toast(f"Zoom {round(factor * 100)}%")

    def copy_url(self, tab=None):
        tab = tab or self.current_tab
        if tab and tab.url:
            QGuiApplication.clipboard().setText(tab.url)
            self.toast("Copied URL to clipboard")

    def open_find(self):
        if self._current_view():
            self.content.find_bar.open()

    def find_in_page(self, text, backward=False):
        if view := self._current_view():
            flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
            view.page().findText(text, flags)

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
        self._track(LittleWindow(self, url=url)).show()

    def toggle_sidebar(self):
        if self._sidebar_floating:
            self._dock_sidebar()
            visible = True
        else:
            visible = not self.sidebar.isVisible()
        self.sidebar.setVisible(visible)
        self.store.settings["sidebar_visible"] = visible
        self.store.schedule_save()
        self._update_content_margins()

    # --- Claude features (Arc Max) -----------------------------------------------------------

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
        def got_text(text):
            on_request(self.claude.ask(
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
        elif kind == QEvent.Type.MouseButtonPress and self.content.splitting and isinstance(obj, QWidget):
            # A click anywhere in a Split View pane makes that tab current (focus alone isn't
            # reliable: it doesn't move when the window isn't the active one)
            pane = self.content.split_view.pane_containing(obj)
            if pane is not None and pane.tab is not self.current_tab:
                QTimer.singleShot(0, lambda: self.activate_tab(pane.tab))
        elif kind == QEvent.Type.MouseMove and not self.sidebar.isVisible() and not self._fullscreen:
            # Arc: with the sidebar hidden, touching the left edge slides it out
            pos = self.mapFromGlobal(QCursor.pos())
            if 0 <= pos.x() <= 6 and 0 <= pos.y() <= self.height() and self.isActiveWindow():
                self._reveal_sidebar()
        return False

    # --- Hover-to-reveal sidebar ---------------------------------------------------------------

    def _reveal_sidebar(self):
        if self._sidebar_floating or self.command_bar.isVisible():
            return
        self._sidebar_floating = True
        self.sidebar.setParent(self.centralWidget())
        self.sidebar.setProperty("floating", True)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        shadow = QGraphicsDropShadowEffect(self.sidebar)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.sidebar.setGraphicsEffect(shadow)
        self._place_floating_sidebar()
        self.sidebar.show()
        self.sidebar.raise_()

    def _place_floating_sidebar(self):
        root = self.centralWidget()
        self.sidebar.setGeometry(6, 6, self.store.settings.get("sidebar_width", 250), root.height() - 12)

    def sidebar_mouse_left(self):
        if self._sidebar_floating:
            QTimer.singleShot(350, self._maybe_hide_floating)

    def _maybe_hide_floating(self):
        busy = QApplication.activePopupWidget() or QApplication.activeModalWidget()
        if self._sidebar_floating and not self.sidebar.underMouse() and not busy:
            self._dock_sidebar()
            self.sidebar.hide()

    def _dock_sidebar(self):
        # Put it back in its normal place in the layout (hidden or shown by the caller)
        self._sidebar_floating = False
        self.sidebar.setGraphicsEffect(None)
        self.sidebar.setProperty("floating", False)
        self.sidebar.style().unpolish(self.sidebar)
        self.sidebar.style().polish(self.sidebar)
        self.splitter.insertWidget(0, self.sidebar)
        self.splitter.setSizes([self.store.settings.get("sidebar_width", 250), 1000])

    # --- Split View ------------------------------------------------------------------------------

    def add_to_split(self, tab, base=None):
        base = base or self.current_tab
        if base is None or tab is base:
            self.activate_tab(tab)
            return
        if not self.store.add_to_split(base, tab, MAX_PANES):
            self.toast(f"Split View holds up to {MAX_PANES} tabs")
            return
        self.activate_tab(tab)

    def open_in_split(self, url):
        base = self.current_tab
        tab = Tab(url)
        self.store.add_tab(tab, TODAY, self.store.active_space, 0)
        self.add_to_split(tab, base)

    def remove_from_split(self, tab):
        self.store.remove_from_split(tab)
        current = self.current_tab
        if current is not None:
            self.activate_tab(tab if tab is current else current)

    def separate_split(self):
        if self.current_tab is not None and self.store.split_of(self.current_tab):
            self.store.separate_split(self.current_tab)
            self.activate_tab(self.current_tab)

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
            f"• {len(arc.spaces)} Spaces: " + ", ".join(f"{s.emoji} {s.name}".strip() for s in arc.spaces),
            f"• {arc.tab_count} tabs, including {len(arc.favorites)} Favorites, in {arc.folder_count} folders",
            f"• {len(arc.history):,} history entries (for ⌘T suggestions) and site icons",
        ]
        note = ("You'll need to sign in to sites again: Arc's logins and cookies are encrypted "
                "with its own key and can't be copied.")
        if arc.extensions:
            note += ("\n\nExtensions can't come across yet (Qt 6.11 crashes when one is enabled), so these "
                     "are skipped: " + ", ".join(arc.extensions) + ".")
        box = QMessageBox(self)
        box.setWindowTitle("Import from Arc")
        box.setText("Bring your Arc setup into PythonBrowser?")
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

    def switch_space(self, space):
        if space.id == self.store.active_space_id:
            return
        self._set_active_space(space)
        self._restore_space_tab()

    def cycle_space(self, step):
        spaces = self.store.spaces
        i = spaces.index(self.store.active_space)
        self.switch_space(spaces[(i + step) % len(spaces)])

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

    def set_space_icon(self, space):
        emoji, ok = QInputDialog.getText(self, "Space Icon", "Emoji (leave blank for a coloured dot):",
                                         text=space.emoji)
        if ok:
            space.emoji = emoji.strip()[:8]
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
            self, "Delete Space", f"Delete “{space.name}” and its {len(space.pinned) + len(space.today)} tabs?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        was_active = space is self.store.active_space
        for tab in list(walk_tabs(space.pinned)) + space.today:
            self._destroy_view(tab)
        self.store.remove_space(space)
        if was_active:
            self.apply_theme()
            self._restore_space_tab()

    def show_space_menu(self, space, pos):
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self.rename_space(space))
        menu.addAction("Change Icon…", lambda: self.set_space_icon(space))
        menu.addAction("New Folder…", self.new_folder)
        profiles = menu.addMenu("Profile")
        for name in self.profile_names():
            act = profiles.addAction(name.capitalize() if name == DEFAULT_PROFILE else name,
                                     lambda n=name: self.set_space_profile(space, n))
            act.setCheckable(True)
            act.setChecked(space.profile == name)
        profiles.addSeparator()
        profiles.addAction("New Profile…", lambda: self.set_space_profile(space, None))
        menu.addAction("Edit Theme…", lambda: self.edit_space_theme(space))
        colors = menu.addMenu("Colour")
        for name, color in PALETTE:
            colors.addAction(dot_icon(color), name, lambda c=color: self.set_space_color(space, c))
        colors.addSeparator()
        colors.addAction("Custom…", lambda: self.set_space_color(space))
        menu.addSeparator()
        menu.addAction("Delete Space…", lambda: self.delete_space(space))
        menu.exec(pos)

    def show_archive_menu(self, pos):
        menu = QMenu(self)
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
        after = menu.addMenu("Archive Today Tabs After")
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
            ("Pin / Unpin Tab", "ph.push-pin", self.toggle_pin),
            ("Add / Remove Favorite", "ph.star", self.toggle_favorite),
            ("Clear Today's Tabs", "ph.archive", self.clear_today),
            ("New Little Window", "ph.app-window", self.new_little_window),
            ("Reopen Closed Tab", "ph.clock-counter-clockwise", self.reopen_closed_tab),
            ("Open Developer Tools", "ph.terminal-window", self.open_devtools),
            ("Rename Space", "ph.pencil-simple", lambda: self.rename_space(self.store.active_space)),
            ("Edit Space Theme", "ph.paint-brush", self.edit_space_theme),
            ("Import from Arc", "ph.arrow-square-in", self.import_from_arc),
            ("Download Video from This Page", "ph.film-strip", self.download_video),
            ("Show Downloads", "ph.download-simple", self.show_downloads),
            ("Download Settings", "ph.gear-six", self.download_settings),
            ("Turn Ad Blocking On / Off", "ph.shield-check", self.toggle_adblock),
            ("Allow / Block Ads on This Site", "ph.shield-slash", self.allow_ads_here),
            ("New Folder", "ph.folder-plus", self.new_folder),
            ("New Note", "ph.note-pencil", self.new_note),
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
        add(file, "Open Location…", "Ctrl+L", lambda: self.open_command_bar("current"))
        add(file, "Reopen Closed Tab", "Ctrl+Shift+T", self.reopen_closed_tab)
        add(file, "New Note", "Ctrl+Shift+N", self.new_note)
        file.addSeparator()
        add(file, "Downloads", "Ctrl+Alt+L", self.show_downloads)
        add(file, "Download Video…", "Ctrl+Shift+D", self.download_video)
        add(file, "Download Settings…", "", self.download_settings)
        file.addSeparator()
        add(file, "Import from Arc…", "", self.import_from_arc)
        file.addSeparator()
        add(file, "Close Tab", "Ctrl+W", self.close_tab)

        edit = bar.addMenu("Edit")
        add(edit, "Copy URL", "Ctrl+Shift+C", self.copy_url)
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
        add(view, "Developer Tools", "Ctrl+Alt+I", self.open_devtools)
        view.addSeparator()
        self.claude_action = QAction("Claude Features", self, checkable=True)
        self.claude_action.setChecked(self.claude.available)
        self.claude_action.triggered.connect(lambda _=False: self.toggle_claude())
        view.addAction(self.claude_action)
        self.adblock_action = QAction("Block Ads & Trackers", self, checkable=True)
        self.adblock_action.setChecked(self.adblock.enabled)
        self.adblock_action.triggered.connect(lambda _=False: self.toggle_adblock())
        view.addAction(self.adblock_action)

        history = bar.addMenu("History")
        add(history, "Back", "Ctrl+[", self.go_back)
        add(history, "Forward", "Ctrl+]", self.go_forward)

        tabs = bar.addMenu("Tabs")
        add(tabs, "Pin / Unpin Tab", "Ctrl+D", self.toggle_pin)
        add(tabs, "Add / Remove Favorite", "", self.toggle_favorite)
        add(tabs, "Tidy Tab Title", "", lambda: self.tidy_title(self.current_tab, force=True))
        add(tabs, "Clear Today's Tabs", "Ctrl+Shift+K", self.clear_today)
        add(tabs, "New Folder…", "", self.new_folder)
        tabs.addSeparator()
        add(tabs, "Split View…", f"{CONTROL}+Shift+=", lambda: self.open_command_bar("split"))
        add(tabs, "Separate Split View", f"{CONTROL}+Shift+-", self.separate_split)
        tabs.addSeparator()
        add(tabs, "Next Tab", f"{CONTROL}+Tab", lambda: self.visible_tab_step(1))
        add(tabs, "Previous Tab", f"{CONTROL}+Shift+Tab", lambda: self.visible_tab_step(-1))
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
        self.adblock.detach_all()
        # Pages must be destroyed before their profile, or Qt warns and may leak the profile
        if self._preview_page is not None:
            self._preview_page.cancel()
            sip.delete(self._preview_page.page)
        if self.peek.view is not None:
            sip.delete(self.peek.view)
        for window in list(self._extra_windows):
            sip.delete(window)
        for tab in self.store.all_tabs():
            if isinstance(tab.view, NoteEditor):
                tab.view.save()
            if tab.view is not None:
                sip.delete(tab.view)
                tab.view = None
