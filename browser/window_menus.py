# The menus: the menu bar, right-click menus' helpers, the archive's, the Window menu's and the Dock's, and the
# commands ⌘T and the menu bar share.
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

import qtawesome as qta
from PyQt6 import sip
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QAction, QColor, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenu

from . import brand, macos, prompt_keys, shortcuts, storage
from .mini_player import SKIP
from .models import TODAY, Tab
from .settings import ARCHIVE_CHOICES
from .theme import PlainMenu, dot_icon


class Menus:
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

    def restore_archived(self, entry):
        """An archived tab back: in Today of the Space it was archived from (or this one, if that's gone), and out of
        the archive. Opened as a new tab in whatever Space you were in, it stayed in the archive (a second click made
        a second tab), in a Space with another profile, maybe."""
        space = self.store.space(entry.get("space_id")) or self.store.active_space
        tab = Tab(entry["url"], entry.get("title") or "")
        self.store.archive = [a for a in self.store.archive if a != entry]
        self.store.add_tab(tab, TODAY, space, 0)
        if space is not self.store.active_space:
            self.switch_space(space)
        self.activate_tab(tab)

    def show_archive_menu(self, pos):
        menu = self._menu()
        header = menu.addAction("Archived Tabs")
        header.setEnabled(False)
        for entry in self.store.archive[:15]:
            title = entry["title"] if len(entry["title"]) < 48 else entry["title"][:47] + "…"
            menu.addAction(storage.favicon(entry["url"]) or dot_icon(self.theme.accent.name()), title,
                           lambda e=entry: self.restore_archived(e))
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

    def commands(self):
        from .window import COMPACT_SETTING  # (window imports this module)
        cmds = [
            ("New Space", "ph.plus", self.new_space),
            ("Toggle Sidebar", "ph.arrows-out-simple", self.toggle_sidebar),
            ("Expand Sidebar" if self.store.settings.get(COMPACT_SETTING) else "Collapse Sidebar to Icons",
             "ph.caret-double-left",  # (the setting: spread out on hover, the sidebar itself is full width for now)
             self.toggle_compact_sidebar),
            ("Move Sidebar to the Left" if self.sidebar_on_right else "Move Sidebar to the Right", "ph.arrows-left-right",
             lambda: self.set_sidebar_side("left" if self.sidebar_on_right else "right")),
            ("Copy URL", "ph.link", self.copy_url),
            ("Site Settings", "ph.sliders-horizontal", lambda: self.show_site_settings()),
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
            ("Edit Space (Name, Icon, Color, Profile)", "ph.pencil-simple", self.edit_space),
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
            ("Turn Sidebar Expand on Hover On / Off", "ph.arrows-out-line-horizontal", self.toggle_spread_sidebar),
        ]
        cmds += [(f"Switch to Space: {s.name}", "ph.dots-three", lambda s=s: self.switch_space(s))
                 for s in self.store.spaces]
        return cmds + self.gather("commands")

    def _build_menus(self):
        from .window import CONTROL, MAC, MINI_PLAYER_ITEMS  # (window imports this module)
        bar = self.menuBar()

        # What opens a window of its own: the rest bring the main window back first, if it was closed (on a Mac,
        # Eclipse keeps running, with its menus, when the window closes)
        own_window = {"New Little Window", "New Private Window", "Downloads", "Keyboard Shortcuts", "Library",
                      "Archived Tabs", "Notes", "Browse for Me Answers", "Minimize", "Zoom", "Toggle Full Screen",
                      "Send Feedback…", "What's New", "Show Crash Logs", "Back Up Everything…",
                      "Restore from Backup…", "Download Settings…", "Go to Prompt on Page", *MINI_PLAYER_ITEMS}

        def add(menu, text, shortcut, slot):
            action = QAction(text, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcuts.here(shortcut)))  # (Windows' own keys: shortcuts.here)
            if text not in own_window:
                slot = (lambda inner: lambda: (self.isHidden() and self.bring_to_front(), inner()))(slot)
                # With a Private or Little window in front, the item is that window's (or nothing): on a Mac its
                # keys still reach this window's menu bar, and ran on the window behind it
                slot = (lambda inner, text: lambda: self._menu_for_front(text, inner))(slot, text)
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
        add(view, "Collapse Sidebar to Icons", "Ctrl+Alt+S", self.toggle_compact_sidebar)
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

        add(view, "Go to Prompt on Page", prompt_keys.KEYS,  # (⌥⇧⌘A: in whichever window is in front)
            lambda: prompt_keys.focus_card(self._front_window()))
        # The mini player's controls, from the keyboard (and VoiceOver): over the video, they show only on hover
        mini = view.addMenu("Mini Player")
        player = self.mini_player
        for text, slot in zip(MINI_PLAYER_ITEMS, (player.toggle_play, lambda: player.skip(-SKIP),
                                                  lambda: player.skip(SKIP), player.next_video, player.back_to_tab,
                                                  player.close)):
            add(mini, text, "", slot)
        mini.aboutToShow.connect(lambda m=mini: self._fill_mini_player_menu(m))
        # (enabled again once it's closed: a disabled item's shortcut, one given it in Settings, wouldn't work)
        mini.aboutToHide.connect(lambda m=mini: [a.setEnabled(True) for a in m.actions()])
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
            add(spaces, f"Space {n}", f"Meta+{n}", lambda n=n: self.switch_to_space_number(n))  # (⌃n; Alt+n elsewhere)
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

    def _fill_mini_player_menu(self, menu):
        """Its items work while a video's in the mini player (Next Video, where the site has one)."""
        player = self.mini_player
        showing = player.tab is not None and player.window is not None
        for action in menu.actions():
            action.setEnabled(showing and (action.text() != "Next Video" or not player.window.next_btn.isHidden()))

    def _menu_for_front(self, text, run):
        """A menu item chosen with a Private or Little window in front: that window's, where it has it. An item about
        the page or tab you're on does nothing otherwise (it isn't the main window's tab you're looking at); the rest,
        a new tab say, bring the main window forward and do it there, as from any other app."""
        from .window import PAGE_ITEMS  # (window imports this module)
        front = QApplication.activeWindow()
        handler = getattr(front, "menu_action", None) if front is not None and front is not self else None
        if handler is None:
            run()
        elif not handler(text) and text not in PAGE_ITEMS and not text.startswith(("Tab ", "Space ")):
            self.bring_to_front()
            run()

    def _front_window(self):
        active = QApplication.activeWindow()
        return active if active is not None else self

    def _zoom_window(self):
        window = self._front_window()
        window.showNormal() if window.isMaximized() else window.showMaximized()

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
        from .window import MAC  # (window imports this module)
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
