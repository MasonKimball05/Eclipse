# Settings (⌘,): every preference in one window, plus the keyboard shortcut cheat sheet (⌘/).
#
# Most settings live in Store.settings and take effect immediately; the window calls back into
# MainWindow for anything that has to change live (ad blocking, Claude, default browser, …).

import qtawesome as qta
from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QIcon, QKeySequence, QPixmap
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
                             QWidget)

from . import brand, clean_links, dark_pages, macos, passwords, spelling, tab_titles, verified
from .command_bar import CUSTOM_SETTING, SEARCH_ENGINES, use_custom_sites
from .downloads import DownloadSettingsForm, find_tool
from .external_links import all_rules as external_rules, set_allowed as set_external
from .motion import FadesIn
from .permissions import set_capture_rule
from .theme import FittedLabel, quiet_scrollbars, stylesheet
from .webview import DEFAULT_PROFILE

ARCHIVE_CHOICES = [(12, "12 hours"), (24, "1 day"), (24 * 7, "7 days"), (24 * 30, "30 days"), (0, "Never")]
SLEEP_CHOICES = [(15, "15 minutes"), (30, "30 minutes"), (60, "1 hour"), (240, "4 hours"), (0, "Never")]
DEFAULT_SLEEP_MINUTES = 60

PAGES = [
    ("general", "ph.gear-six", "General"),
    ("tabs", "ph.squares-four", "Tabs"),
    ("privacy", "ph.shield-check", "Privacy & Ad Blocking"),
    ("passwords", "ph.key", "Passwords & Passkeys"),
    ("downloads", "ph.download-simple", "Downloads"),
    ("profiles", "ph.user-circle", "Profiles"),
    ("air", "ph.airplane-tilt", "Air Traffic Control"),
    ("shortcuts", "ph.keyboard", "Keyboard Shortcuts"),
]

# Shortcuts that aren't menu items (the menu-bar ones are read straight from the menus)
EXTRA_SHORTCUTS = [
    ("Command Bar", "⌘↩", "Browse for Me with what you've typed"),
    ("Command Bar", "↑ ↓  ↩", "Pick a result"),
    ("Pages", "Tap ⇧ on a link", "Preview where the link goes (Claude)"),
    ("Pages", "⌘F, a question, ↩", "Ask Claude about the page"),
    ("Pages", "⇧⌘A", "Ask About This Page (a conversation beside it)"),
    ("Pages", "Esc", "Close Peek, or finish zapping"),
    ("Tabs", "Hold ⌃, tap Tab", "Go back through recent tabs (let go to switch)"),
    ("Sidebar", "Two-finger swipe", "Switch Space"),
    ("Sidebar", "Drag a tab onto the page", "Open it in Split View"),
    ("Sidebar", "Middle-click a tab", "Close it"),
    ("Little Window", "⌘O", "Open in the main window"),
    ("Little Window", "⌘L", "Edit the address"),
    ("Little Window", "⇧⌘C", "Copy the link"),
    ("Notes", "⌘B  ⌘I  ⌘U", "Bold, italic, underline"),
]


def _combo(options, current):
    box = QComboBox()
    for value, label in options:
        box.addItem(label, value)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


class SettingsWindow(FadesIn, QWidget):
    def __init__(self, browser):
        super().__init__(None, Qt.WindowType.Window)
        self.browser = browser
        self.store = browser.store
        self.setObjectName("SettingsWindow")
        self.setProperty("panel", True)
        self.setWindowTitle("Settings")
        self.resize(860, 640)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        side = QFrame()
        side.setObjectName("SettingsSide")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 40, 0, 0)
        sv.setSpacing(6)
        # Search every page's settings: the pages without a match step aside, the matches light up
        self.search = QLineEdit()
        self.search.setObjectName("SettingsSearch")
        self.search.setPlaceholderText("Search settings")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search)
        self.search.returnPressed.connect(self._jump_to_match)
        sv.addWidget(self.search)
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setIconSize(QSize(16, 16))
        self.nav.currentRowChanged.connect(self._show_page)
        sv.addWidget(self.nav, 1)
        layout.addWidget(side)
        self._matches: dict[int, list[QWidget]] = {}

        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.page_index = {}
        builders = {"general": self._general, "tabs": self._tabs, "privacy": self._privacy,
                    "passwords": lambda: passwords.settings_page(self),
                    "downloads": self._downloads, "profiles": self._profiles, "air": self._air_traffic,
                    "shortcuts": self._shortcuts}
        # The core's pages, and the features' (each where it asks to be: before one of the core's)
        self.page_list = [(key, icon, title) for key, icon, title in PAGES]
        for key, icon, title, build, before in self.browser.gather("settings_pages"):
            keys = [k for k, _, _ in self.page_list]
            self.page_list.insert(keys.index(before) if before in keys else len(self.page_list), (key, icon, title))
            builders[key] = lambda b=build: b(self)
        for key, icon, title in self.page_list:
            self.nav.addItem(QListWidgetItem(title))
            self.page_index[key] = self.pages.count()
            self.pages.addWidget(self._page(title, builders[key]()))
        self.apply_theme()
        self.nav.setCurrentRow(self.page_index["general"])

    def apply_theme(self):
        theme = self.browser.theme
        self.setStyleSheet(stylesheet(theme))
        for i, (_, icon, _) in enumerate(self.page_list):
            self.nav.item(i).setIcon(qta.icon(icon, color=theme.panel_text, color_active=theme.on_accent))

    def open(self, page="general"):
        self.nav.setCurrentRow(self.page_index.get(page, 0))
        self._refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_page(self, row):
        if row < 0:
            return
        self.pages.setCurrentIndex(row)
        self._refresh()

    # --- Search -------------------------------------------------------------------------------

    def _search(self, text):
        query = text.strip().lower()
        for widgets in self._matches.values():
            for widget in widgets:
                widget.setProperty("match", False)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self._matches = {}
        for i in range(self.pages.count()):
            page = self.pages.widget(i)
            hits = []
            if query:
                title = self.page_list[i][2].lower()
                for label in page.findChildren(QLabel):
                    if label.objectName() in ("SettingTitle", "") and query in label.text().lower():
                        hits.append(label.parentWidget() if label.objectName() == "SettingTitle" else label)
                for label in page.findChildren(QLabel, "Hint"):  # a word only the description has
                    row = label.parentWidget()
                    if query in label.text().lower() and row not in hits:
                        hits.append(row)
                for box in page.findChildren(QCheckBox) + page.findChildren(QPushButton):
                    if box.text() and query in box.text().lower():
                        hits.append(box)
                for combo in page.findChildren(QComboBox):  # what a choice is set to ("qwen", "google")
                    if any(query in combo.itemText(n).lower() for n in range(combo.count())):
                        hits.append(combo)
                if not hits and query in title:
                    hits = [page]  # the page itself is what you meant ("privacy")
            self._matches[i] = [h for h in hits if h is not page]
            for widget in self._matches[i]:
                widget.setProperty("match", True)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            self.nav.setRowHidden(i, bool(query) and not hits)
        if query and self.nav.isRowHidden(self.nav.currentRow()):
            first = next((i for i in range(self.nav.count()) if not self.nav.isRowHidden(i)), None)
            if first is not None:
                self.nav.setCurrentRow(first)

    def _jump_to_match(self):
        row = self.nav.currentRow()
        widgets = self._matches.get(row) or []
        if widgets:
            scroll = self.pages.widget(row)
            if isinstance(scroll, QScrollArea):
                scroll.ensureWidgetVisible(widgets[0], 40, 80)

    # --- Building blocks -----------------------------------------------------------------

    def _page(self, title, content) -> QWidget:
        body = QWidget()
        body.setObjectName("SettingsBody")
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("PanelTitle")
        v.addWidget(heading)
        v.addWidget(content)
        v.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return quiet_scrollbars(scroll)

    def _card(self, rows) -> QFrame:
        card = QFrame()
        card.setObjectName("OptionsCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 6, 16, 6)
        v.setSpacing(0)
        for i, row in enumerate(rows):
            if i:
                line = QFrame()
                line.setObjectName("SettingsLine")
                line.setFixedHeight(1)
                v.addWidget(line)
            v.addWidget(row)  # (first: until it's in the layout, a row counts as hidden)
            if i:
                row.separator = line  # (hidden with the row: a hidden row leaves no stray line)
                line.setVisible(not row.isHidden())
        return card

    def _row(self, title, description, control=None) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 10, 0, 10)
        h.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("SettingTitle")
        text.addWidget(label)
        if description:
            desc = QLabel(description)
            desc.setObjectName("Hint")
            desc.setWordWrap(True)
            text.addWidget(desc)
        h.addLayout(text, 1)
        if control is not None:
            h.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _stack(self, *widgets) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        for w in widgets:
            v.addWidget(w)
        return box

    def _setting(self, key, value):
        self.store.settings[key] = value
        self.store.schedule_save()

    def _side_combo(self):
        box = _combo([("left", "On the left"), ("right", "On the right")],
                     "right" if self.browser.sidebar_on_right else "left")
        box.currentIndexChanged.connect(lambda: self.browser.set_sidebar_side(box.currentData()))
        self.side_box = box
        return box

    def _dark_switch(self):
        box = QCheckBox()
        box.setChecked(self.store.settings.get(dark_pages.SETTING, False))
        box.toggled.connect(lambda on: (self._setting(dark_pages.SETTING, on), self.browser.apply_dark_pages()))
        self.dark_box = box
        return box

    def _icon_choices(self):
        """Every look of the eclipse, to pick the app's icon from."""
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 10)
        grid.setSpacing(6)
        self.icon_group = QButtonGroup(box)
        self.icon_group.setExclusive(True)
        current = brand.chosen_look(self.store.settings)
        for i, look in enumerate(brand.LOOKS):
            button = QToolButton()
            button.setObjectName("IconChoice")
            button.setCheckable(True)
            button.setChecked(look == current)
            button.setIcon(QIcon(QPixmap.fromImage(brand.icon_image(96, look))))
            button.setIconSize(QSize(48, 48))
            button.setToolTip(brand.LABELS[look])
            button.setAccessibleName(f"{brand.LABELS[look]} icon")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, chosen=look: self._choose_icon(chosen))
            self.icon_group.addButton(button)
            grid.addWidget(button, i // 6, i % 6)
        grid.setColumnStretch(6, 1)
        return box

    def _choose_icon(self, look):
        from PyQt6.QtWidgets import QApplication
        self._setting(brand.SETTING, look)
        brand.apply_icon(QApplication.instance(), self.store.settings)

    def _toggle_spelling(self, on):
        lang = spelling.language(self.store.settings.get("spell_check_language"))
        if on and lang is None:
            self._spell_note("There's no dictionary for your Mac's language yet.")
            self.spell_box.setChecked(False)
            return
        self._setting(spelling.SETTING, on)
        if not on or spelling.installed(lang):
            self._spell_note("")
            self.browser.apply_spelling()
            return
        if self._spell_download is not None:
            return
        self._spell_note("Downloading the dictionary (once, a few hundred KB)…")
        self._spell_download = download = spelling.DictionaryDownload(lang, self)

        def done(ok, message):
            self._spell_download = None
            download.deleteLater()
            if ok and self.store.settings.get(spelling.SETTING):
                self.browser.apply_spelling()
            self._spell_note(message if not ok else "")
            if not ok:
                self.spell_box.setChecked(False)
        download.finished.connect(done)

    # --- Pages ---------------------------------------------------------------------------

    def _updates_row(self, feature):
        """Updates: the version that's running, what the last look found, and (while it comes) the download."""
        from PyQt6 import sip
        from PyQt6.QtWidgets import QProgressBar
        from .self_update import this_version
        updater = feature.updater
        control = QWidget()
        h = QHBoxLayout(control)
        h.setContentsMargins(0, 0, 0, 0)
        auto = QCheckBox("Automatically")
        auto.setChecked(self.store.settings.get("app_updates", True))
        auto.toggled.connect(lambda on: self._setting("app_updates", on))
        check = QPushButton("Check Now")
        check.clicked.connect(feature.check)
        h.addWidget(auto)
        h.addWidget(check)
        row = self._row("Updates", " ", control)
        line = row.findChildren(QLabel, "Hint")[0]
        # (under the words, while there's an update: its button, then the download's progress)
        bar = QProgressBar()
        bar.setFixedSize(220, 6)
        bar.setTextVisible(False)
        install = QPushButton("Update && Restart")
        install.setObjectName("PermissionAllow")
        install.clicked.connect(updater.update_and_restart)
        below = QWidget()
        b = QHBoxLayout(below)
        b.setContentsMargins(0, 6, 0, 0)
        b.addWidget(install)
        b.addWidget(bar)
        b.addStretch(1)
        row.layout().itemAt(0).layout().addWidget(below)
        version = this_version()

        def show():
            if sip.isdeleted(line):
                return
            status = updater.status
            state = status.get("state")
            found = (f"{status.get('version') or 'A new version'} is ready" if "build" in status else
                     f"{status.get('behind', 0)} new change{'s' if status.get('behind') != 1 else ''} on GitHub")
            words = {
                "idle": f"Once a day, {brand.NAME} looks for a newer version and offers it.",
                "checking": "Checking…",
                "current": "Up to date.",
                "available": found + (": " + "; ".join(status["subjects"][:3]) if status.get("subjects") else "") + ".",
                "blocked": f"{found}, but it can't update itself: {status.get('reason', '')}",
                "unavailable": status.get("reason", "Couldn't check."),
                "downloading": f"Downloading {status.get('version', 'the update')}… {status.get('percent', 0)}%",
                "failed": status.get("reason", "The update didn't work."),
            }
            line.setText(f"{version}\n{words.get(state, '')}")
            bar.setVisible(state == "downloading")
            bar.setValue(status.get("percent", 0))
            install.setVisible(state == "available")
            below.setVisible(state in ("available", "downloading"))
            check.setEnabled(state not in ("checking", "downloading"))
        updater.status_changed.connect(show)
        show()
        return row

    def _verified_row(self):
        """This copy of Eclipse: Verified (a signed release, unchanged), or why not."""
        seal = QLabel()
        word = QLabel()
        word.setObjectName("SettingTitle")
        control = QWidget()
        h = QHBoxLayout(control)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(seal)
        h.addWidget(word)
        row = self._row(f"This copy of {brand.NAME}", " ", control)
        line = row.findChildren(QLabel, "Hint")[0]
        check = verified.this_copy()

        def show():
            from PyQt6 import sip
            if sip.isdeleted(word):
                return
            state = check.state
            heading, about = verified.WORDS[state]
            icon, color = {verified.VERIFIED: ("ph.shield-check-fill", "#30d158"),
                           verified.CHANGED: ("ph.shield-warning-fill", "#ff9f0a"),
                           verified.CHECKING: ("ph.hourglass", "#8e8e93")}.get(state, ("ph.shield",
                                                                                          "#8e8e93"))
            seal.setPixmap(qta.icon(icon, color=color).pixmap(QSize(20, 20)))
            word.setText(heading)
            line.setText(about)
        check.changed.connect(show)
        show()
        return row

    def _general(self):
        self.default_status = QLabel()
        self.default_status.setObjectName("Hint")
        self.default_btn = QPushButton("Make Default")
        self.default_btn.clicked.connect(self.browser.set_as_default_browser)
        default_row = self._row("Default browser", "", self.default_btn)
        default_row.layout().itemAt(0).layout().addWidget(self.default_status)

        links = _combo([("little", "Little Window"), ("tab", "New Tab")],
                       self.store.settings.get("external_links", "little"))
        links.currentIndexChanged.connect(lambda: self._setting("external_links", links.currentData()))
        engine = _combo([(k, name) for k, (name, _) in SEARCH_ENGINES.items()],
                        self.store.settings.get("search_engine", "google"))
        engine.currentIndexChanged.connect(lambda: self._setting("search_engine", engine.currentData()))

        space_btn = QPushButton("Edit Space…")
        space_btn.clicked.connect(lambda: (self.browser.bring_to_front(), self.browser.edit_space()))
        theme_btn = QPushButton("Edit Theme…")
        theme_btn.clicked.connect(lambda: self.browser.edit_space_theme())
        appearance = QWidget()
        h = QHBoxLayout(appearance)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(space_btn)
        h.addWidget(theme_btn)
        backup_box = QWidget()
        b = QHBoxLayout(backup_box)
        b.setContentsMargins(0, 0, 0, 0)
        for label, slot in (("Back Up…", self.browser.back_up), ("Restore…", self.browser.restore_backup)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            b.addWidget(button)

        updater = self.browser.feature("self_update")

        self.spell_box = QCheckBox()
        self.spell_box.setChecked(self.store.settings.get(spelling.SETTING, False))
        self.spell_box.toggled.connect(self._toggle_spelling)
        self.spell_status = QLabel()
        self.spell_status.setObjectName("Hint")
        self.spell_status.hide()  # (only while there's something to say: an empty line left a gap)
        spell_row = self._row("Check spelling while typing", "Underlines misspelled words in text fields; "
                                                             "right-click one for corrections. Checked on this Mac.",
                              self.spell_box)
        spell_row.layout().itemAt(0).layout().addWidget(self.spell_status)
        self._spell_download = None

        self.shortcut_list = QListWidget(self)  # (a parent from the start: shown, it's never a window of its own)
        self.shortcut_list.setObjectName("SettingsList")
        add_shortcut = QPushButton("Add…")
        add_shortcut.clicked.connect(self._add_search_shortcut)
        self.remove_shortcut = QPushButton("Remove")
        self.remove_shortcut.clicked.connect(self._remove_search_shortcut)
        shortcut_buttons = QWidget()
        sb = QHBoxLayout(shortcut_buttons)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.addWidget(add_shortcut)
        sb.addWidget(self.remove_shortcut)
        self._fill_search_shortcuts()

        return self._stack(
            self._card([self._verified_row()]),
            self._card([default_row,
                        self._row("Open links from other apps in",
                                  "Like Arc, a Little window is quick to glance at and close. ⌘O keeps it.", links),
                        self._row("Search engine", "Used by the command bar and Little windows.", engine),
                        spell_row]),
            self._card([self._row("Search shortcuts", "Your own sites to search from ⌘T: type the keyword and a "
                                                      "space (\u201cparl budget\u201d), or the keyword and Tab. "
                                                      "Built-in ones: yt, gh, w, r, x, az, so, npm, pypi…",
                                  shortcut_buttons),
                        self.shortcut_list]),
            self._card([self._row("Look", "Each Space has its own name, icon, colour and theme. "
                                          "Right-click a Space to change it.", appearance),
                        self._row("Sidebar", "Which side of the window the sidebar is on.", self._side_combo()),
                        self._row("Dark websites", "Pages that only come in light are darkened (images stay as "
                                                   "they are). Leave a site light from the shield in the address "
                                                   "bar.", self._dark_switch()),
                        self._row("App icon", "Changes in the Dock right away; Finder and Launchpad show it "
                                              "after the next app build.", None),
                        self._icon_choices()]),
            self._card([self._row("Backup", "Spaces, tabs, settings, notes, Boosts, userscripts, extensions, "
                                            "passwords and passkeys (locked with your recovery PIN) in one file. "
                                            "Cookies aren't included.", backup_box),
                        *([self._updates_row(updater)] if updater is not None else [])]),
        )

    def _tabs(self):
        archive = _combo(ARCHIVE_CHOICES, self.store.settings.get("archive_after_hours", 12))
        archive.currentIndexChanged.connect(lambda: self._setting("archive_after_hours", archive.currentData()))
        sleep = _combo(SLEEP_CHOICES, self.store.settings.get("sleep_after_minutes", DEFAULT_SLEEP_MINUTES))
        sleep.currentIndexChanged.connect(lambda: self._setting("sleep_after_minutes", sleep.currentData()))
        peek = QCheckBox()
        peek.setChecked(self.store.settings.get("peek_enabled", True))
        peek.toggled.connect(lambda on: self._setting("peek_enabled", on))
        tidy = QCheckBox()
        tidy.setChecked(self.store.settings.get(tab_titles.SETTING, True))
        tidy.toggled.connect(lambda on: (self._setting(tab_titles.SETTING, on), self.browser.sidebar.rebuild()))
        previews = QCheckBox()
        previews.setChecked(self.store.settings.get("tab_previews", True))
        previews.toggled.connect(lambda on: self._setting("tab_previews", on))
        mini = QCheckBox()
        mini.setChecked(self.store.settings.get("mini_player_enabled", True))
        mini.toggled.connect(lambda on: self._setting("mini_player_enabled", on))
        ctrl_tab = _combo([("recent", "Recent tabs (like Arc)"), ("sidebar", "Sidebar order")],
                          self.store.settings.get("ctrl_tab", "recent"))
        ctrl_tab.currentIndexChanged.connect(lambda: self._setting("ctrl_tab", ctrl_tab.currentData()))
        swipes = QCheckBox()
        swipes.setChecked(self.store.settings.get("swipe_navigation", True))
        swipes.toggled.connect(lambda on: self._setting("swipe_navigation", on))
        start = QCheckBox()
        start.setChecked(self.store.settings.get("start_view", True))
        start.toggled.connect(lambda on: (self._setting("start_view", on), self.browser.content.empty.refresh()))
        banners = QCheckBox()
        banners.setChecked(not self.store.settings.get("notifications_dnd", False))
        banners.toggled.connect(lambda on: self._setting("notifications_dnd", not on))
        return self._stack(self._card([
            self._row("Archive Today tabs after", "Unpinned tabs you haven't used for this long move to the "
                                                  "archive (the box icon at the bottom of the sidebar).", archive),
            self._row("Put idle tabs to sleep after", "Sleeping tabs free their memory and reload, where you'd "
                                                      "scrolled to, when you click them. Tabs playing audio never "
                                                      "sleep.", sleep),
            self._row("Peek", "Links opened from pinned tabs and favorites float over the page instead of "
                              "opening a new tab.", peek),
            self._row("Start view", "With no tab open, the page area shows your most-visited sites and recently "
                                    "closed tabs. Off, it's empty, like Arc's.", start),
            self._row("Tidy tab titles", "Like Arc Max: the sidebar leaves the site's name out of a tab's title "
                                         "(its icon says it), so “Q3 plan - Google Docs” shows as “Q3 plan”.",
                      tidy),
            self._row("Tab previews", "Rest the pointer on a tab in the sidebar to see a picture of its page, "
                                      "as it was when you last left it.", previews),
            self._row("Mini player", "Switch away from a tab that's playing a video and it keeps playing in a "
                                     "small floating window.", mini),
            self._row("⌃Tab switches between", "Recent tabs: hold ⌃ and tap Tab to go back through the tabs you "
                                               "used last, then let go.", ctrl_tab),
            *([self._row("Swipe to go back", "Swipe right with two fingers on a page to go back, left to go "
                                          "forward. Pages that scroll sideways keep the swipe.", swipes)] if self.browser.feature("gestures") else []),
            self._row("Notification banners", "Sites you've allowed to notify you show a banner in the corner "
                                              "of the window. Off is Do Not Disturb: unread badges still update.",
                      banners),
        ]))

    def _autoplay_box(self):
        from .window import AUTOPLAY_SETTING
        box = QCheckBox()
        box.setChecked(self.store.settings.get(AUTOPLAY_SETTING, False))
        box.toggled.connect(lambda on: (self._setting(AUTOPLAY_SETTING, on), self.browser.apply_autoplay()))
        return box

    def _privacy(self):
        blocker = self.browser.adblock
        index_box = QCheckBox()
        index_box.setChecked(self.browser.page_index.enabled)
        index_box.toggled.connect(lambda on: self._setting("page_index", on))
        clear_index = QPushButton("Clear")
        clear_index.clicked.connect(lambda: (self.browser.page_index.clear(),
                                             self.browser.toast("Cleared the text of pages you've read")))
        index_row = QWidget()
        ir = QHBoxLayout(index_row)
        ir.setContentsMargins(0, 0, 0, 0)
        ir.addWidget(index_box)
        ir.addWidget(clear_index)
        cookie_box = QCheckBox()
        cookie_box.setChecked(blocker.reject_cookies)
        cookie_box.toggled.connect(blocker.set_reject_cookies)
        clean_box = QCheckBox()
        clean_box.setChecked(self.browser.store.settings.get(clean_links.SETTING, True))
        clean_box.toggled.connect(lambda on: (self.browser.store.settings.__setitem__(clean_links.SETTING, on),
                                              self.browser.store.schedule_save()))
        self.block_box = QCheckBox()
        self.block_box.setChecked(blocker.enabled)
        self.block_box.toggled.connect(lambda on: on != blocker.enabled and self.browser.toggle_adblock())
        self.block_info = QLabel()
        self.block_info.setObjectName("Hint")
        update = QPushButton("Update Now")
        update.clicked.connect(lambda: (blocker.update_lists(force=True), self.block_info.setText("Updating…")))
        lists_row = self._row("Block lists", "Peter Lowe's list and the StevenBlack hosts list, refreshed weekly.",
                              update)
        lists_row.layout().itemAt(0).layout().addWidget(self.block_info)

        self.allowed = QListWidget()
        self.allowed.setObjectName("SettingsList")
        self.allowed_empty = QLabel("None — ads are blocked everywhere.")
        self.allowed_empty.setObjectName("Hint")
        self.unallow_btn = QPushButton("Block Again")
        self.unallow_btn.clicked.connect(self._unallow_site)
        allowed_box = QWidget()
        v = QVBoxLayout(allowed_box)
        v.setContentsMargins(0, 0, 0, 10)
        v.addWidget(self.allowed_empty)
        v.addWidget(self.allowed)
        v.addWidget(self.unallow_btn, 0, Qt.AlignmentFlag.AlignRight)

        clear_history = QPushButton("Clear History…")
        clear_history.clicked.connect(self._clear_history)

        # Site permissions: everything allowed or blocked, from any profile
        self.perm_list = QListWidget()
        self.perm_list.setObjectName("SettingsList")
        self.perm_empty = QLabel("No site has asked for anything yet.")
        self.perm_empty.setObjectName("Hint")
        self.perm_remove = QPushButton("Remove")
        self.perm_remove.clicked.connect(self._remove_permission)
        perm_box = QWidget()
        pv = QVBoxLayout(perm_box)
        pv.setContentsMargins(0, 0, 0, 10)
        pv.addWidget(self.perm_empty)
        pv.addWidget(self.perm_list)
        pv.addWidget(self.perm_remove, 0, Qt.AlignmentFlag.AlignRight)
        return self._stack(
            self._card([self._row("Site permissions", "Camera, microphone, location, notifications and links that "
                                                      "open other apps you've allowed or blocked. Removing one "
                                                      "means the site asks again. "
                                                      "Also in Site Controls (the shield in the URL pill).", None),
                        perm_box]),
            self._card([self._row("Block ads & trackers", "Requests to known ad and tracking domains from other "
                                                          "sites are cancelled. YouTube ads are skipped.",
                                  self.block_box), lists_row,
                        self._row("Sites where ads are allowed", "Added from the shield in the address bar.", None),
                        allowed_box,
                        self._row("Reject cookie banners", "Answers “Reject all” (or “Only necessary”) on cookie "
                                                           "pop-ups for you. Never clicks Accept. Sites where ads "
                                                           "are allowed are left alone.", cookie_box),
                        self._row("Clean links", "Takes tracking tags (utm_source, fbclid, gclid…) off links "
                                                 "before they open, and off links you copy.", clean_box),
                        self._row("Videos wait for a click", "Video and sound don't start by themselves: nothing "
                                  "plays until you click it (muted background videos wait too).", self._autoplay_box())]),
            self._card([self._row("Browsing history", "Used for command bar suggestions. Also browsable in the "
                                                      "Library (⌘Y).", clear_history),
                        self._row("Search what you've read", "Keeps the text of pages you visit on this Mac so ⌘T "
                                                             "can find them by what they said. Never private "
                                                             "windows or sign-in pages.", index_row),
                        self._row("Cookies and logins", "Each profile keeps its own. Clear them on the Profiles "
                                                        "page.", None)]),
        )

    def _downloads(self):
        # The Download Video window's form, its controls laid out as this window's rows (they save as they change)
        form = self._download_form = DownloadSettingsForm(self.store, autosave=True)
        form.hide()

        def control(widget, width=240):
            widget.setParent(None)
            widget.show()  # (out of the hidden form, it would stay hidden)
            if isinstance(widget, (QLineEdit, QComboBox, QPushButton)):
                widget.setFixedWidth(width)
                # (the folder button ignores its size, to stretch in a form: in a row that put it past the edge)
                widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            return widget
        form.subs.setPlaceholderText("en, es")
        form.rate.setPlaceholderText("5M")

        def switch(box):
            title = box.text()
            box.setText("")
            return title, control(box)
        ytdlp = find_tool("yt-dlp")
        tools = (f"yt-dlp: {ytdlp}" if ytdlp else "yt-dlp isn't installed: brew install yt-dlp") + "  ·  ffmpeg: " + (
            "found" if find_tool("ffmpeg") else "missing (brew install ffmpeg)")
        where = self._card([self._row("Save to", "Where downloads go: files from pages, and videos from "
                                                 "File → Download Video (⇧⌘D).", control(form.folder))])
        rows = [self._row("Download", "What File → Download Video saves by default. " + tools, control(form.kind)),
                self._row("Video format", None, control(form.video_format)),
                self._row("Video quality", None, control(form.max_height)),
                self._row("Audio format", None, control(form.audio_format)),
                self._row("File names", "yt-dlp's output template: %(uploader)s/%(title)s.%(ext)s makes a folder "
                                        "per channel.", control(form.template)),
                self._row("Subtitles", "Language codes, like en, es (blank for none).", control(form.subs))]
        rows += [self._row(title, None, box) for title, box in (switch(form.embed_subs), switch(form.embed_metadata),
                                                                switch(form.embed_thumbnail))]
        rows += [self._row("Cookies from", "Lets yt-dlp use that browser's logins for age-restricted or members-only "
                                           "videos. Safari needs Full Disk Access for this.", control(form.cookies)),
                 self._row("Speed limit", "Like 5M (blank for no limit).", control(form.rate))]
        card = self._stack(where, self._card(rows))
        tidy = QCheckBox()
        tidy.setChecked(self.store.settings.get("tidy_downloads", True))
        tidy.toggled.connect(lambda on: self._setting("tidy_downloads", on))
        tidy_card = self._card([self._row("Tidy Downloads", "When a file finishes downloading, Claude renames it to "
                                          "something readable (\"IMG_3847.pdf\" → \"Acme Invoice March 2026.pdf\"). "
                                          "Needs Claude features on.", tidy)])
        if self.browser.feature("claude") is None:
            return card  # (Tidy Downloads is Claude's)
        return self._stack(card, tidy_card)

    def _profiles(self):
        self.profile_list = QListWidget()
        self.profile_list.setObjectName("SettingsList")
        self.profile_list.setWordWrap(True)
        self.profile_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        clear = QPushButton("Clear Cookies && Site Data…")  # && shows a literal "&" on a button
        clear.clicked.connect(self._clear_profile)
        heading = self._row("Profiles", "A profile is a separate set of cookies, logins and site data. Give Work and "
                                        "Personal their own to be signed in to different accounts; pick a Space's "
                                        "by right-clicking the Space. Clearing works on the one picked below.", clear)
        return self._card([heading, self.profile_list])

    def _air_traffic(self):
        self.air_rows = QVBoxLayout()
        self.air_rows.setContentsMargins(0, 0, 0, 0)
        self.air_rows.setSpacing(8)
        add = QPushButton("Add Rule")
        add.clicked.connect(lambda: self._add_air_rule({"match": "", "space": self.store.active_space.id}, focus=True))
        heading = self._row("Rules", "Links you click in other apps (Slack, Mail, Messages…) normally open in a Little "
                                     "window. A rule sends matching links straight to a Space instead: a domain "
                                     "(\"zoom.us\" covers its subdomains too) or any text the link contains. The "
                                     "first that matches wins.", add)
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 8, 0, 8)
        self.air_empty = QLabel("No rules yet: links from other apps all open in a Little window.")
        self.air_empty.setObjectName("Hint")
        v.addWidget(self.air_empty)
        v.addLayout(self.air_rows)
        return self._card([heading, box])

    def _fill_air_rules(self):
        # Rebuilt each time Settings opens, so new Spaces show up in the lists
        while self.air_rows.count():
            widget = self.air_rows.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for rule in self.store.settings.get("air_traffic", []):
            self._add_air_rule(rule)
        self.air_empty.setVisible(not self.store.settings.get("air_traffic"))

    def _add_air_rule(self, rule, focus=False):
        self.air_empty.hide()
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        match = QLineEdit(rule.get("match", ""))
        match.setPlaceholderText("zoom.us, figma.com/file, …")
        h.addWidget(QLabel("Links matching"))
        h.addWidget(match, 1)
        h.addWidget(QLabel("open in"))
        space = _combo([(s.id, s.name) for s in self.store.spaces], rule.get("space"))
        h.addWidget(space)
        remove = QPushButton("Remove")
        h.addWidget(remove)
        row.air = (match, space)
        self.air_rows.addWidget(row)
        match.textChanged.connect(self._save_air_rules)
        space.currentIndexChanged.connect(self._save_air_rules)
        remove.clicked.connect(lambda: (row.setParent(None), row.deleteLater(), self._save_air_rules()))
        if focus:
            match.setFocus()
            self._save_air_rules()

    def _save_air_rules(self):
        rules = []
        for i in range(self.air_rows.count()):
            row = self.air_rows.itemAt(i).widget()
            if row is not None and row.parent() is not None and hasattr(row, "air"):
                match, space = row.air
                rules.append({"match": match.text().strip(), "space": space.currentData()})
        self._setting("air_traffic", rules)
        self.air_empty.setVisible(not rules)

    def _shortcuts(self):
        self.shortcut_filter = QLineEdit()
        self.shortcut_filter.setPlaceholderText("Search shortcuts…")
        self.shortcut_filter.textChanged.connect(self._fill_shortcuts)
        self.shortcut_box = QWidget()
        self.shortcut_layout = QVBoxLayout(self.shortcut_box)
        self.shortcut_layout.setContentsMargins(0, 0, 0, 0)
        self.shortcut_layout.setSpacing(14)
        return self._stack(self.shortcut_filter, self.shortcut_box)

    # --- Live state ------------------------------------------------------------------------

    def _refresh(self):
        current = macos.default_browser()
        mine = macos.APP_BUNDLE_ID
        if current == mine:
            self.default_status.setText(f"{brand.NAME} is your default browser.")
            self.default_btn.hide()
        else:
            name = {"company.thebrowser.browser": "Arc", "com.apple.safari": "Safari",
                    "com.google.chrome": "Chrome"}.get((current or "").lower(), current or "another browser")
            self.default_status.setText(f"{name} is your default browser right now.")
            self.default_btn.show()

        self.block_info.setText(f"{len(self.browser.adblock.domains):,} domains blocked")
        sites = self.browser.adblock.allowed_sites
        self.allowed.clear()
        self.allowed.addItems(sites)
        self.allowed.setFixedHeight(min(len(sites), 5) * 28 + 10)
        for widget in (self.allowed, self.unallow_btn):
            widget.setVisible(bool(sites))
        self.allowed_empty.setVisible(not sites)

        self.browser.emit("settings_shown", self)  # (the features' pages: Claude's status…)

        self.profile_list.clear()
        for name in self.browser.profile_names():
            spaces = [s.name for s in self.store.spaces if s.profile == name]
            label = "Default" if name == DEFAULT_PROFILE else name
            used = ", ".join(spaces) if spaces else "no Spaces"
            if name == DEFAULT_PROFILE:
                used += " · also Favorites and Little windows"
            item = QListWidgetItem(f"{label}  ·  used by {used}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.profile_list.addItem(item)
        self.profile_list.setCurrentRow(0)
        rows = [self.profile_list.sizeHintForRow(i) for i in range(min(self.profile_list.count(), 4))]
        self.profile_list.setFixedHeight(sum(rows) + 14)  # (its rows and its padding: no empty space under them)
        self._fill_air_rules()
        self._fill_permissions()
        self._fill_shortcuts()

    def _fill_permissions(self):
        self.perm_list.clear()
        for origin, label, kind, value, profile in self.browser.permissions.all_rules():
            item = QListWidgetItem(f"{origin.split('://', 1)[-1]}  ·  {label}  ·  "
                                   f"{'Allowed' if value == 'allow' else 'Blocked'}")
            item.setData(Qt.ItemDataRole.UserRole, (origin, kind, profile))
            self.perm_list.addItem(item)
        for origin, scheme in external_rules(self.store.settings):  # links it may open in other apps
            item = QListWidgetItem(f"{origin.split('://', 1)[-1]}  ·  open {scheme}: links  ·  Allowed")
            item.setData(Qt.ItemDataRole.UserRole, (origin, scheme, "external"))
            self.perm_list.addItem(item)
        has = self.perm_list.count() > 0
        self.perm_list.setVisible(has)
        self.perm_remove.setVisible(has)
        self.perm_empty.setVisible(not has)
        self.perm_list.setFixedHeight(min(self.perm_list.count(), 6) * 28 + 10)

    def _remove_permission(self):
        for item in self.perm_list.selectedItems():
            origin, kind, profile = item.data(Qt.ItemDataRole.UserRole)
            if profile == "external":
                set_external(self.store.settings, origin, kind, False)
                self.store.schedule_save()
            elif profile is None:
                set_capture_rule(self.store.settings, origin, kind.name, None)
                self.store.schedule_save()
            else:
                profile.queryPermission(QUrl(origin), kind).reset()
        self._fill_permissions()

    def _unallow_site(self):
        for item in self.allowed.selectedItems():
            self.browser.adblock.set_allowed(item.text(), False)
        self._refresh()

    def _spell_note(self, text):
        self.spell_status.setText(text)
        self.spell_status.setVisible(bool(text))

    def _fill_search_shortcuts(self):
        self.shortcut_list.clear()
        entries = self.store.settings.get(CUSTOM_SETTING, [])
        for entry in entries:
            item = QListWidgetItem(f"{entry['keyword']}  ·  {entry.get('name') or entry['keyword']}  ·  {entry['url']}")
            self.shortcut_list.addItem(item)
        self.shortcut_list.setVisible(bool(entries))
        if getattr(self.shortcut_list, "separator", None) is not None:
            self.shortcut_list.separator.setVisible(bool(entries))
        self.shortcut_list.setFixedHeight(min(len(entries), 6) * 28 + 10)
        self.remove_shortcut.setVisible(bool(entries))

    def _add_search_shortcut(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Add Search Shortcut")
        dialog.setStyleSheet(stylesheet(self.browser.theme))
        dialog.setProperty("panel", True)
        form = QFormLayout(dialog)
        name, keyword, url = QLineEdit(), QLineEdit(), QLineEdit()
        name.setPlaceholderText("Parliament")
        keyword.setPlaceholderText("parl")
        url.setPlaceholderText("https://example.com/search?q=%s")
        hint = FittedLabel("Search the site once, copy the address, and put %s where your search words were.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        form.addRow("Name", name)
        form.addRow("Keyword", keyword)
        form.addRow("Address", url)
        form.addRow("", hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        dialog.resize(460, 0)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entry = {"name": name.text().strip(), "keyword": keyword.text().strip().lower().split(" ")[0],
                 "url": url.text().strip()}
        if not entry["keyword"] or "%s" not in entry["url"] or not entry["url"].startswith(("http://", "https://")):
            QMessageBox.information(self, "Search Shortcut", "It needs a keyword and a web address with %s in it.")
            return
        entries = [e for e in self.store.settings.get(CUSTOM_SETTING, []) if e["keyword"] != entry["keyword"]]
        self.store.settings[CUSTOM_SETTING] = entries + [entry]
        self.store.schedule_save()
        use_custom_sites(self.store.settings[CUSTOM_SETTING])
        self._fill_search_shortcuts()

    def _remove_search_shortcut(self):
        row = self.shortcut_list.currentRow()
        entries = self.store.settings.get(CUSTOM_SETTING, [])
        if 0 <= row < len(entries):
            del entries[row]
            self.store.schedule_save()
            use_custom_sites(entries)
            self._fill_search_shortcuts()

    def _clear_history(self):
        answer = QMessageBox.question(self, "Clear History", f"Delete all {len(self.store.history):,} history "
                                                             "entries? Tabs, Spaces and the archive are kept.")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.history = []
            self.store.schedule_save()
            self.browser.page_index.clear()  # what you read goes with where you went

    def _clear_profile(self):
        item = self.profile_list.currentItem()
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(self, "Clear Cookies & Site Data",
                                      f"Sign out of every site in the “{name}” profile and delete its cookies, "
                                      "cache and site data?")
        if answer == QMessageBox.StandardButton.Yes:
            profile = self.browser.profile_named(name)
            profile.cookieStore().deleteAllCookies()
            profile.clearHttpCache()
            profile.clearAllVisitedLinks()
            self.browser.toast(f"Cleared the “{name}” profile")

    def _fill_shortcuts(self, *_):
        while self.shortcut_layout.count():
            item = self.shortcut_layout.takeAt(0)
            if item.widget():
                item.widget().hide()  # gone at once: deleteLater alone leaves it drawn under the new rows
                item.widget().deleteLater()
        query = self.shortcut_filter.text().strip().lower()
        groups: dict[str, list[tuple[str, str]]] = {}
        for group, name, keys in self._menu_shortcuts() + [(g, d, k) for g, k, d in EXTRA_SHORTCUTS]:
            if not query or query in name.lower() or query in group.lower() or query in keys.lower():
                groups.setdefault(group, []).append((name, keys))
        for group, rows in groups.items():
            title = QLabel(group.upper())
            title.setObjectName("EditorSection")
            self.shortcut_layout.addWidget(title)
            self.shortcut_layout.addWidget(self._card([self._shortcut_row(n, k) for n, k in rows]))
        if not groups:
            empty = QLabel("No shortcuts match.")
            empty.setObjectName("Hint")
            self.shortcut_layout.addWidget(empty)

    def _shortcut_row(self, name, keys) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 4)
        label = QLabel(name)
        h.addWidget(label, 1)
        for part in keys.split(" / "):
            cap = QLabel(part)
            cap.setObjectName("KeyCap")
            h.addWidget(cap)
        return row

    def _menu_shortcuts(self) -> list[tuple[str, str, str]]:
        """(menu, action, keys) for every menu-bar item that has a shortcut, merged by name."""
        found: dict[tuple[str, str], list[str]] = {}

        def walk(menu, group):
            for action in menu.actions():
                if action.menu() is not None:
                    walk(action.menu(), group)
                elif not action.shortcut().isEmpty():
                    keys = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                    found.setdefault((group, action.text().replace("&", "")), []).append(keys)
        for action in self.browser.menuBar().actions():
            if action.menu() is not None:
                walk(action.menu(), action.text().replace("&", ""))
        return [(group, name, " / ".join(dict.fromkeys(keys))) for (group, name), keys in found.items()]
