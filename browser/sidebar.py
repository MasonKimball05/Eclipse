# The Arc-style vertical sidebar:
#
#   [traffic lights]        [⊟] [←] [→] [⟳]
#   [ github.com                         ]   <- URL pill, opens the command bar
#   [fav][fav][fav][fav]                     <- Favorites (shared by all Spaces)
#   Personal                                 <- Space name
#   pinned tabs ...
#   ───────────────────────────── Clear
#   + New Tab
#   today tabs ...
#   [archive]      ● ● ●            [+]      <- Space switcher
#
# Widgets here never change data directly; they call back into the MainWindow
# (passed in as `browser`), which owns the Store and the web views.

import re
import sys

import qtawesome as qta
from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSpacerItem,
                             QToolButton, QVBoxLayout, QWidget)

from PyQt6 import sip

from . import macos
from .devmode import dev_label, is_dev_url
from .models import PINNED, TODAY, Tab, display_host
from .icons import is_symbol, space_icon
from .theme import overlay_scrollbar
# (the sidebar's parts, each a file of its own: here too, for whatever imports them from here)
from .sidebar_common import (COMPACT_WIDTH, ElidedLabel, FAVORITE_COLUMNS, FOLDER_COLORS, FOLDER_ICONS,
                             GITHUB_SECTION_ICONS, HoverFade, INDENT, IconButton, LIGHTS_GAP, LIGHTS_ROW_SPACING,
                             PILL_FONT_PX, PILL_ICON, PILL_ICON_DROP, PILL_MIN_FONT_PX, PILL_PADDING,
                             PILL_PLACEHOLDER, ROW_BUTTON, ROW_BUTTON_ICON, ROW_HEIGHT, ROW_SPACING, SPACE_BUTTON,
                             SPACE_BUTTON_SMALLEST, SPACE_MORE, SPACE_SHOWN, SpokenTooltips, TABS_MIME, TAB_MIME,
                             WAKE_AHEAD_MS, _row_icon, _row_icons, _with_alpha, compact, faded, home_icon,
                             is_answer_page, is_away, left_its_site, pill_icon, repolish, set_shown, site_labels,
                             spoken, tab_icon)
from .sidebar_rows import (DraggableTab, FavoriteTile, FolderRow, NewTabRow, SpaceDot, TabRow, dragged_tabs,
                           dragged_together, stack_picture, start_drag)
from .sidebar_sections import (DropGap, DropZone, FavoritesGrid, ListFade, RowReveal, TabSection, _runs)




TOP_MARGIN = 9  # (the top row's room above it, until it lines up with the traffic lights)


class Sidebar(QWidget):
    native = None  # (the Space part drawn by SwiftUI instead: sidebar_native.py, use_native)

    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)  # only painted when floating
        self.setMinimumWidth(190)
        self.setMaximumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, TOP_MARGIN, 4, 10)
        layout.setSpacing(8)

        # Top row: room for the macOS traffic lights, then the navigation buttons.
        # align_with_traffic_lights() fine-tunes this once the window exists.
        top = QHBoxLayout()
        top.setSpacing(0)
        # The room after the traffic lights never shrinks (squeezed, it put the first button over them); the
        # gaps between back, forward and reload give way instead when the sidebar is narrow
        self._lights_gap = QSpacerItem(66 if sys.platform == "darwin" else 0, 0, QSizePolicy.Policy.Fixed)
        top.addItem(self._lights_gap)
        # (the row keeps its height with its buttons hidden, the collapsed sidebar's: or what's below rose under the
        # traffic lights)
        top.addItem(QSpacerItem(0, 26, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed))
        self.toggle_btn = IconButton("msc.layout-sidebar-left", "Toggle Sidebar (⌘S)")
        # (Phosphor's arrows fill less of their square than the toggle and reload do: drawn a little bigger to match)
        self.back_btn = IconButton("ph.arrow-left", "Back (⌘[)", icon_size=19)
        self.forward_btn = IconButton("ph.arrow-right", "Forward (⌘])", icon_size=19)
        self.reload_btn = IconButton("ph.arrow-clockwise", "Reload (⌘R)")
        self.toggle_btn.clicked.connect(browser.toggle_sidebar)
        self.back_btn.clicked.connect(browser.go_back)
        self.forward_btn.clicked.connect(browser.go_forward)
        # Held down or right-clicked: the pages back (or forward), to jump several at once
        for button, forward in ((self.back_btn, False), (self.forward_btn, True)):
            menu = browser._menu(button)
            menu.aboutToShow.connect(lambda m=menu, f=forward: browser.fill_history_menu(m, f))
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
            button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, b=button: b.showMenu())
        self.reload_btn.clicked.connect(browser.reload)
        top.addWidget(self.toggle_btn)
        top.addStretch(1)
        for i, btn in enumerate((self.back_btn, self.forward_btn, self.reload_btn)):
            if i:
                top.addItem(QSpacerItem(LIGHTS_ROW_SPACING, 0, QSizePolicy.Policy.Preferred))  # (can give way)
            top.addWidget(btn)
        layout.addLayout(top)
        layout.addSpacing(6)

        self.url_pill = QPushButton()
        self.url_pill.setObjectName("UrlPill")
        self.url_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.url_pill.setIconSize(QSize(PILL_ICON, round(PILL_ICON + 2 * PILL_ICON_DROP)))
        self.url_pill.clicked.connect(lambda: browser.open_command_bar("current"))
        # Ad blocker badge, sitting inside the pill on the right
        pill_layout = QHBoxLayout(self.url_pill)
        pill_layout.setContentsMargins(0, 0, 6, 0)
        pill_layout.addStretch(1)
        self.shield_btn = QToolButton()
        self.shield_btn.setObjectName("ShieldButton")
        self.shield_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.shield_btn.setIconSize(QSize(12, 12))
        self.shield_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shield_btn.clicked.connect(
            lambda: browser.show_site_settings(self.shield_btn.mapToGlobal(QPoint(0, self.shield_btn.height()))))
        # Developer Mode: a DEV chip on localhost and file pages, with the error count; opens the console
        self.dev_btn = QToolButton()
        self.dev_btn.setObjectName("DevChip")
        self.dev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dev_btn.setToolTip("Developer Mode · click for the console (⌥⌘J)")
        self.dev_btn.clicked.connect(lambda: browser.devmode.toggle_drawer())
        self.dev_btn.hide()
        pill_layout.addWidget(self.dev_btn)
        browser.devmode.changed.connect(self.update_dev)
        # The features' chips (a pull request's checks, Save Job on a job posting…)
        self.feature_chips = browser.gather("pill_buttons")
        for chip in self.feature_chips:
            if not chip.property("pill_end"):
                pill_layout.addWidget(chip)
        # The page's zoom, when it isn't 100% (click to reset)
        self.zoom_btn = QToolButton()
        self.zoom_btn.setObjectName("ShieldButton")
        self.zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_btn.setToolTip("Zoom for this site · click to reset to 100%")
        self.zoom_btn.clicked.connect(lambda: browser.zoom(0))
        self.zoom_btn.hide()
        pill_layout.addWidget(self.zoom_btn)
        pill_layout.addWidget(self.shield_btn)
        # The features' chips that go at the end, after the shield (extensions' puzzle piece…)
        for chip in self.feature_chips:
            if chip.property("pill_end"):
                pill_layout.addWidget(chip)
        layout.addWidget(self.url_pill)
        # Collapsed, the pill gives way to a magnifying glass (⌘L, as the pill does)
        self.compact_search = IconButton("ph.magnifying-glass", "Search or Enter URL (⌘L)")
        self.compact_search.clicked.connect(lambda: browser.open_command_bar("current"))
        self.compact_search.hide()
        layout.addWidget(self.compact_search, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(2)

        self.favorites = FavoritesGrid(browser)
        layout.addWidget(self.favorites)
        layout.addSpacing(4)

        # Everything that belongs to the Space scrolls together
        body = QWidget()
        body.setObjectName("SidebarScrollBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)  # (the scrollbar floats over it: rows end with the pill)
        body_layout.setSpacing(4)

        # The Space's name and icon: click (or right-click) to edit the Space
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self.space_title = QPushButton()
        self.space_title.setObjectName("SpaceTitle")
        self.space_title.setIconSize(QSize(16, 16))
        self.space_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.space_title.setToolTip("Edit Space: name, icon, color, profile")
        self.space_title.clicked.connect(lambda: browser.edit_space())
        self.space_title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.space_title.customContextMenuRequested.connect(lambda _pos: browser.edit_space())
        self.profile_badge = QLabel()
        self.profile_badge.setObjectName("ProfileBadge")
        title_row.addWidget(self.space_title)
        title_row.addWidget(self.profile_badge)
        title_row.addStretch(1)
        body_layout.addLayout(title_row)
        self._title_row = title_row

        self.pinned = TabSection(browser, PINNED, "Drag tabs here to pin them")
        body_layout.addWidget(self.pinned)

        divider_row = QHBoxLayout()
        divider_row.setContentsMargins(6, 4, 0, 0)
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        self.clear_btn = QPushButton("↓ Clear")
        self.clear_btn.setObjectName("ClearButton")
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.setToolTip("Archive all of today's tabs in this Space")
        self.clear_btn.clicked.connect(browser.clear_today)
        self.tidy_btn = QPushButton()
        self.tidy_btn.setObjectName("ClearButton")
        self.tidy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tidy_btn.clicked.connect(self._tidy_clicked)
        self._tidy_state = "idle"
        self.set_tidy_state("idle")
        divider_row.addWidget(divider, 1, Qt.AlignmentFlag.AlignVCenter)
        divider_row.addWidget(self.tidy_btn)
        divider_row.addWidget(self.clear_btn)
        body_layout.addLayout(divider_row)

        self.new_tab_row = NewTabRow(browser)
        body_layout.addWidget(self.new_tab_row)
        self.today = TabSection(browser, TODAY)
        body_layout.addWidget(self.today)
        body_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidget(body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().installEventFilter(self)
        overlay_scrollbar(self.scroll)
        self.list_fade = ListFade(self, self.scroll)
        layout.addWidget(self.scroll, 1)

        self.cards = browser.gather("sidebar_cards")
        for card in self.cards:  # (Up Next, Now Playing…)
            layout.addWidget(card)

        # Bottom: archive, the Space switcher, and "new Space"
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        self.archive_btn = IconButton("ph.archive", "Archived tabs")
        self.archive_btn.clicked.connect(
            lambda: browser.show_archive_menu(self.archive_btn.mapToGlobal(QPoint(0, 0))))
        self.downloads_btn = IconButton("ph.download-simple", "Downloads (⌥⌘L)")
        self.downloads_btn.clicked.connect(lambda: browser.show_downloads())
        self.new_space_btn = IconButton("ph.plus", "New Space")
        self.new_space_btn.clicked.connect(browser.new_space)
        self.switcher = QHBoxLayout()
        self.switcher.setSpacing(2)
        self.space_buttons: list[QToolButton] = []
        bottom.addWidget(self.archive_btn)
        bottom.addWidget(self.downloads_btn)
        self._bottom_buttons = [self.archive_btn, self.downloads_btn, self.new_space_btn]
        for button in browser.gather("sidebar_buttons"):  # (a feature's own, beside Downloads)
            bottom.addWidget(button)
            self._bottom_buttons.append(button)
        bottom.addStretch(1)
        # With Spaces hidden (more than fit), an arrow on the side they're on: click it (or swipe) for them
        self.switcher_more = []
        for step, icon in ((-1, "ph.caret-left-bold"), (1, "ph.caret-right-bold")):
            more = QToolButton()
            more.setObjectName("SpaceMore")
            more.setProperty("step", step)
            more.setProperty("icon_name", icon)
            more.setFixedSize(SPACE_MORE, SPACE_BUTTON)
            more.setIconSize(QSize(8, 8))
            more.setCursor(Qt.CursorShape.PointingHandCursor)
            more.setStyleSheet("QToolButton#SpaceMore { background: transparent; border: none; padding: 0; }")
            more.clicked.connect(lambda _=False, st=step: self._turn_switcher_page(st))
            more.hide()
            self.switcher_more.append(more)
        bottom.addWidget(self.switcher_more[0])
        bottom.addLayout(self.switcher)
        bottom.addWidget(self.switcher_more[1])
        bottom.addStretch(1)
        bottom.addWidget(self.new_space_btn)
        layout.addLayout(bottom)
        # Icon-only buttons say what their tooltip says (less any shortcut), and keep saying it as
        # it changes (the shield's count, Homebase's status…)
        self._spoken = SpokenTooltips(self)
        for button in self.findChildren(QToolButton):
            self._spoken.watch(button)

    # --- Rebuilding ----------------------------------------------------------

    def align_with_traffic_lights(self, at: QPoint | None = None):
        """Center the top-row buttons on the traffic lights, just to their right (like Arc). `at`: where
        the sidebar will be in the window, if that isn't where it is yet (the hidden one, about to slide out)."""
        window = self.window()
        if window.isFullScreen() or getattr(window, "_fullscreen", False):
            # (full screen: the lights are up in the menu bar, not beside the row; lined up with where they were,
            # the row went to the bottom of the window)
            margins = self.layout().contentsMargins()
            self.layout().setContentsMargins(margins.left(), TOP_MARGIN, margins.right(), margins.bottom())
            self._lights_gap.changeSize(0, 0, QSizePolicy.Policy.Fixed)  # (and no room left for them beside it)
            self.layout().invalidate()
            return
        lights = macos.traffic_lights(self)
        if lights is None:
            return
        right, center = lights
        offset = at if at is not None else self.mapTo(self.window(), QPoint(0, 0))  # (it floats 6pt in when hidden)
        size = self.toggle_btn.height()
        layout = self.layout()
        margins = layout.contentsMargins()
        layout.setContentsMargins(margins.left(), max(round(center - offset.y() - size / 2), 0),
                                  margins.right(), margins.bottom())
        # A little more room after the lights than between them (~9pt), minus the button's padding (on the
        # right too: they move over into the sidebar), so the row doesn't feel crammed
        gap = max(round(right + LIGHTS_GAP - offset.x() - margins.left() - 4), 0)
        self._lights_gap.changeSize(gap, 0, QSizePolicy.Policy.Fixed)
        layout.invalidate()

    compact = False

    def set_compact(self, on: bool):
        """Collapsed to a strip of its tabs' icons (Zen's collapsed sidebar), or back. Rows draw just their icon;
        what needs words (the address, the Space's name, Clear, the cards, the buttons along the bottom) gives way,
        and the search button opens ⌘T in the pill's place."""
        self.compact = on
        self.setMinimumWidth(COMPACT_WIDTH if on else 190)
        self.setMaximumWidth(COMPACT_WIDTH if on else 420)
        for widget in (self.toggle_btn, self.back_btn, self.forward_btn, self.reload_btn, self.url_pill,
                       *self._bottom_buttons):
            widget.setVisible(not on)
        self.compact_search.setVisible(on)
        self.new_tab_row.text_label.setVisible(not on)
        self.pinned.hint.setText("" if on else "Drag tabs here to pin them")
        for card in self.cards:  # (they show and hide themselves: held at no height instead)
            card.setMaximumHeight(0 if on else 16777215)
        self.favorites._built = None  # (its columns change)
        # The Space's icon in the middle of the strip, as everything else in it is
        first = self._title_row.itemAt(0)
        if on and first.widget() is not None:
            self._title_row.insertStretch(0, 1)
        elif not on and first.widget() is None:
            self._title_row.takeAt(0)
        self.rebuild()
        for row in self.findChildren(TabRow) + self.findChildren(FolderRow):
            if hasattr(row, "_lay_out"):
                row._lay_out()
            row.refresh() if isinstance(row, FolderRow) else None
            row.update()
        self._show_native()

    def rebuild(self):
        if getattr(self, "drag_active", False):
            self._rebuild_after_drag = True  # (see start_drag)
            return
        store = self.browser.store
        space = store.active_space
        theme = self.browser.theme
        for btn in (self.toggle_btn, self.back_btn, self.forward_btn, self.reload_btn,
                    self.archive_btn, self.downloads_btn, self.new_space_btn, self.compact_search):
            btn.set_color(theme.text)
        self.new_tab_row.set_color(theme.text)

        preview = getattr(self.browser, "tab_preview", None)
        if preview is not None:
            preview.dismiss()  # (its row may be about to go)
        self.space_title.setText("" if self.compact else space.name)
        self.space_title.setIcon(space_icon(space, theme.text, 32))
        self.profile_badge.setText(space.profile)
        self.profile_badge.setToolTip(f"This Space uses the “{space.profile}” profile (its own logins and cookies)")
        self.profile_badge.setVisible(space.profile != "default" and not self.compact)
        self.favorites.set_tabs(store.favorites)
        # Rows slide in and out for changes within a Space; switching Spaces just shows the new one
        same_space = getattr(self, "_built_space", None) == space.id
        if not same_space:
            self.marked = []  # (a selection belongs to the Space it was made in)
        self._built_space = space.id
        native_shown = self.native is not None and self.native.container.isVisible()
        if native_shown and (not same_space or getattr(self, "_rows_due", None) is not None):
            # The hidden Qt rows (only read, never seen, while the native sidebar shows) are made when something
            # reads them (qt_rows): made on every switch of Space, they took 20–100 ms, held up the fade to the new
            # Space's colors, and, made a moment later instead, stalled the next swipe
            self._rows_due = space
        else:
            self._rows_due = None
            self.pinned.animate_changes = self.today.animate_changes = same_space
            self.pinned.set_tabs(space.pinned)
            self.today.set_tabs(space.today)
        self.clear_btn.setVisible(bool(space.today) and not self.compact)
        self._update_tidy_visibility()
        self._rebuild_switcher()
        self.update_selection()
        self.update_nav()
        if self.native is not None:
            self.native.schedule()

    def qt_rows(self) -> list:
        """The Qt sections' rows, pinned then Today, made now if a Space switch left them for later."""
        space, self._rows_due = getattr(self, "_rows_due", None), None
        if space is not None:
            if space is self.browser.store.active_space:
                self.pinned.animate_changes = self.today.animate_changes = False
                self.pinned.set_tabs(space.pinned)
                self.today.set_tabs(space.today)
                self.update_selection()
        return self.pinned.rows + self.today.rows

    def use_native(self, on: bool):
        """The Space part (its name, pinned tabs, New Tab, Today) drawn by SwiftUI (sidebar_native.py), or by
        Qt again. The Qt one is still made, hidden, for everything that reads it."""
        from . import sidebar_native
        if on and self.native is None:
            body = sidebar_native.NativeSidebarBody(self.browser, self)
            if not body.ok:
                body.close()
                body.deleteLater()
                return
            self.native = body
            layout = self.layout()
            layout.insertWidget(layout.indexOf(self.scroll), body.container, 1)
        elif not on and self.native is not None:
            body, self.native = self.native, None
            body.close()
            body.container.deleteLater()
            body.deleteLater()
        self._show_native()
        self.rebuild()

    def grab(self, rect=QRect(QPoint(0, 0), QSize(-1, -1))):
        """As Qt's, with the native view's picture where it is (Qt's own pictures can't see a native view: the
        floating sidebar's slide out, say, would show it empty)."""
        picture = super().grab(rect)
        body = self.native
        if body is not None and body.container.isVisible():
            shot = body.page.snapshot()
            if not shot.isNull():
                shot.setDevicePixelRatio(shot.width() / max(1, body.container.width()))
                area = rect if rect.isValid() else QRect(QPoint(0, 0), self.size())
                p = QPainter(picture)
                p.drawPixmap(body.container.geometry().topLeft() - area.topLeft(), shot)
                p.end()
        return picture

    def _show_native(self):
        native_shown = self.native is not None
        if self.native is not None:
            self.native.container.setVisible(native_shown)
        self.scroll.setVisible(not native_shown)
        self.list_fade.setVisible(not native_shown)

    # --- Tidy Tabs button: ✦ Tidy → ✦ Tidying… → ↶ Undo Tidy (for a few seconds) → ✦ Tidy -------

    def set_tidy_state(self, state):
        self._tidy_state = state
        text, tip = {"idle": ("✦ Tidy", "Let Claude group today's tabs into folders"),
                     "busy": ("✦ Tidying…", "Claude is grouping your tabs"),
                     "undo": ("↶ Undo Tidy", "Put your tabs back the way they were")}[state]
        self.tidy_btn.setText(text)
        self.tidy_btn.setToolTip(tip)
        self.tidy_btn.setEnabled(state != "busy")
        self._update_tidy_visibility()

    def _update_tidy_visibility(self):
        claude = self.browser.feature("claude")  # (Tidy is Claude's: no Claude, no Tidy)
        loose = sum(1 for n in self.browser.store.active_space.today if isinstance(n, Tab))
        self.tidy_btn.setVisible(claude is not None and not self.compact
                                 and (self._tidy_state != "idle" or claude.can_tidy(loose)))
        if self.native is not None:
            self.native.schedule()

    def _tidy_clicked(self):
        claude = self.browser.feature("claude")
        if claude is None:
            return
        if self._tidy_state == "undo":
            claude.undo_tidy()
        elif self._tidy_state == "idle":
            claude.tidy_today()

    def _rebuild_switcher(self):
        store = self.browser.store
        same = (tuple((s.id, s.name, s.icon, s.color) for s in store.spaces), store.active_space_id,
                self.browser.theme.text.name())
        if self.space_buttons and same == getattr(self, "_switcher_built", None):
            return  # (nothing about the Spaces changed: the buttons stay)
        self._switcher_built = same
        for btn in self.space_buttons:
            btn.hide()  # (gone now, not on a later pass: see TabSection.set_tabs)
            btn.setParent(None)
            btn.deleteLater()
        self.space_buttons = []
        store = self.browser.store
        text = self.browser.theme.text
        for i, space in enumerate(store.spaces):
            active = space.id == store.active_space_id
            btn = SpaceDot(self.browser, space)
            btn.setFixedSize(SPACE_BUTTON, SPACE_BUTTON)
            btn.setProperty("has_icon", bool(space.icon))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{space.name}  (⌃{i + 1})" if i < 9 else space.name)
            btn.setProperty("name_tip", btn.toolTip())
            btn.setAccessibleName(f"{space.name} Space" + (", current" if active else ""))
            # Quiet when inactive, full strength when it's the Space you're in
            icon = space_icon(space, text if active else _with_alpha(text, 0.5), 32)
            btn.setIcon(icon if active or is_symbol(space.icon) or not space.icon else faded(icon, 0.5))
            btn.setIconSize(QSize(16, 16) if space.icon else QSize(10, 10))
            btn.setProperty("selected", active)
            btn.clicked.connect(lambda _, s=space: self.browser.switch_space(s))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, s=space, b=btn: self.browser.show_space_menu(s, b.mapToGlobal(pos)))
            self.switcher.addWidget(btn)
            if self.isVisible():
                btn.show()  # (see TabSection.set_tabs)
            self.space_buttons.append(btn)
        self._fit_switcher()

    # --- Several tabs at once (⌘-click, ⇧-click), as in Arc ---------------------------

    marked: list = []

    def mark(self, tab, extend=False):
        """⌘-click adds a tab to the selection or takes it out; ⇧-click (extend) adds every tab from
        the last one picked to this one, in sidebar order. The tab you're on starts the selection."""
        order = self._order()
        current = self.browser.current_tab
        marked = list(self.marked)
        if not marked and any(t is current for t in order):
            marked = [current]
        index = {id(t): i for i, t in enumerate(order)}
        if extend and marked and id(marked[-1]) in index and id(tab) in index:
            a, b = sorted((index[id(marked[-1])], index[id(tab)]))
            have = {id(t) for t in marked}
            span = order[a:b + 1] if index[id(tab)] >= index[id(marked[-1])] else order[a:b + 1][::-1]
            marked += [t for t in span if id(t) not in have]
            marked = [t for t in marked if t is not tab] + [tab]  # (the next ⇧-click runs from here)
        elif any(t is tab for t in marked):
            marked = [t for t in marked if t is not tab]
        else:
            marked.append(tab)
        self.marked = marked
        self.update_selection()

    def _order(self):
        return [r.tab for r in self.qt_rows() if not getattr(r, "_pb_gone", False)]

    def _trimmed(self, marked):
        """One picked tab that's also the tab you're on is just the tab you're on, not a selection; one
        is kept when the tab you're on has no row to start from (a favorite, or inside a closed folder)."""
        current = self.browser.current_tab
        if len(marked) == 1 and (marked[0] is current or any(t is current for t in self._order())):
            return []
        return marked

    def clear_marks(self):
        if self.marked:
            self.marked = []
            self.update_selection()

    # --- Cheap updates (no widget rebuild) ---------------------------------------

    def update_selection(self):
        current = self.browser.current_tab
        split = (self.browser.store.split_of(current) or []) if current else []
        self.marked = self._trimmed([t for t in self.marked if self.browser.store.locate(t)])  # (closed ones drop out)
        marked = {id(t) for t in self.marked}
        for tile in self.favorites.tiles:
            tile.set_selected(tile.tab is current)
        for row in self.pinned.rows + self.today.rows:
            row.set_selected(row.tab is current)
            if (row.property("marked") is True) != (id(row.tab) in marked):
                repolish(row, marked=id(row.tab) in marked)
            in_split = row.tab is not current and any(t is row.tab for t in split)
            if (row.property("insplit") is True) != in_split:
                repolish(row, insplit=in_split)
            row.refresh()
        if self.native is not None:
            self.native.schedule()
        # A tab you switch to that's scrolled out of sight comes into view (only on switching: a refresh
        # doesn't pull the list back from where you've scrolled it)
        if current is not None and current is not getattr(self, "_scrolled_to", None):
            self._scrolled_to = current
            row = next((r for r in self.pinned.rows + self.today.rows if r.tab is current and r.isVisibleTo(self)), None)
            if row is not None:
                # (clear of the fade at the bottom of the list, too)
                QTimer.singleShot(0, lambda r=row: not sip.isdeleted(r) and self.scroll.ensureWidgetVisible(
                    r, 0, ListFade.HEIGHT + 6))

    def update_nav(self):
        tab = self.browser.current_tab
        view = self.browser._current_view()  # None for notes, which have no history
        history = view.history() if view else None
        self.back_btn.setEnabled(bool(history and history.canGoBack()))
        self.forward_btn.setEnabled(bool(history and history.canGoForward()))
        self.reload_btn.setEnabled(view is not None)

        theme = self.browser.theme
        if tab and tab.url.startswith(("note:", "easel:")):
            easel = tab.url.startswith("easel:")
            self.url_pill.setIcon(pill_icon("ph.chalkboard-simple" if easel else "ph.note-pencil",
                                           color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text("Easel" if easel else "Note")
            self.url_pill.setToolTip("")
        elif tab and is_answer_page(tab.url):
            self.url_pill.setIcon(pill_icon("ph.compass", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text("Browse for Me")
            self.url_pill.setToolTip("")
        elif tab and tab.url.startswith("pbext://"):
            host = getattr(self.browser.feature("extensions"), "host", None)
            ext = host.extensions.get(QUrl(tab.url).host()) if host is not None else None
            self.url_pill.setIcon(pill_icon("ph.puzzle-piece", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(ext.name if ext else "Extension")
            self.url_pill.setToolTip(tab.url)
        elif tab and tab.url and is_dev_url(tab.url):
            self.url_pill.setIcon(pill_icon("ph.code", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(dev_label(tab.url))  # the full address, port and path included
            self.url_pill.setToolTip(tab.url)
        elif tab and tab.url:
            secure = tab.url.startswith("https://")
            if secure and self.browser.certificates.overridden(tab.url):  # (you chose Visit Anyway)
                self.url_pill.setIcon(pill_icon("ph.warning", color=QColor("#ef4444")))
            else:
                self.url_pill.setIcon(pill_icon("ph.lock-simple" if secure else "ph.globe-simple",
                                               color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(display_host(tab.url) or tab.url, host=bool(display_host(tab.url)))
            self.url_pill.setToolTip(tab.url)
        else:
            self.url_pill.setIcon(pill_icon("ph.magnifying-glass", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(PILL_PLACEHOLDER)
            self.url_pill.setToolTip("")
        self.update_shield()
        self.update_zoom()
        self.update_dev()
        self.browser.emit("update_pill", tab)
        self._fit_pill_text()

    def update_dev(self):
        tab = self.browser.current_tab
        view = self.browser._current_view()
        dev = bool(tab and tab.url and is_dev_url(tab.url) and view is not None)
        errors = self._dev_errors = self.browser.devmode.errors(view) if dev else 0
        self.dev_btn.setText(f"DEV · {errors}" if errors else "DEV")
        self.dev_btn.setProperty("errors", errors > 0)
        self.dev_btn.setProperty("open", dev and self.browser.devmode.drawer_open())
        self.dev_btn.style().unpolish(self.dev_btn)
        self.dev_btn.style().polish(self.dev_btn)
        self.dev_btn.setVisible(dev)
        self._fit_pill_text()

    def update_zoom(self):
        view = self.browser._current_view()
        factor = view.zoomFactor() if view is not None else 1.0
        self.zoom_btn.setText(f"{round(factor * 100)}%")
        self.zoom_btn.setVisible(view is not None and abs(factor - 1.0) > 0.01)
        self._fit_pill_text()

    def fit_pill(self):
        """A feature's chip came or went: the address makes room."""
        self._fit_pill_text()

    def _set_pill_text(self, text, host=False):
        """host: it's a site's address (cut from the left when it doesn't fit, so whose site it is shows)."""
        self._pill_text = text
        self._pill_host = host
        self.url_pill.setText(text)

    def _fit_pill_text(self):
        # Long hosts are cut short with "…" instead of running under the shield and puzzle buttons
        text = getattr(self, "_pill_text", "")

        def room():
            # From where the text starts (the pill's padding, its icon and the gap after) to the first button
            # inside it, where the layout has put them (estimates cut addresses that fitted)
            pill = self.url_pill
            pill.layout().activate()
            buttons = [b for b in (*self.feature_chips, self.dev_btn, self.zoom_btn, self.shield_btn)
                       if b.isVisibleTo(self) and b.parentWidget() is pill]
            end = min((b.x() for b in buttons), default=pill.width() - PILL_PADDING)  # (a button's own padding's the gap)
            return end - (PILL_PADDING + pill.iconSize().width() + 6)
        if self.dev_btn.isVisibleTo(self):
            # A narrow sidebar: the address you're building says more than the chip's word, which shrinks to
            # a dot (or just the error count)
            errors = getattr(self, "_dev_errors", 0)
            self.dev_btn.setText(f"DEV · {errors}" if errors else "DEV")
            self.dev_btn.setMinimumSize(0, 0)
            self.dev_btn.setMaximumSize(16777215, 16777215)
            repolish(self.dev_btn, compact=False)
            host = text.split("/")[0]  # (the path can be cut short: the host and port are what matter)
            fits = lambda: self.url_pill.fontMetrics().horizontalAdvance(host + "…") <= room()
            if not fits():
                self.dev_btn.setText(str(errors) if errors else "")
                repolish(self.dev_btn, compact=True)
                if not errors:
                    self.dev_btn.setFixedSize(9, 9)
                # Still too tight: this Mac's own servers are told apart by their port (":5173/…")
                loopback = re.match(r"(?:127\.0\.0\.1|localhost|\[::1\])(:\d+)(.*)", text)
                if not fits() and loopback:
                    text = host = loopback.group(1)
                    text += loopback.group(2)
        room = room()
        if text == PILL_PLACEHOLDER and self.url_pill.fontMetrics().horizontalAdvance(text) > room:
            text = "Search…"  # (a narrow sidebar: a whole word, not half a sentence)
        # A site's address is cut from the left, so what's kept is the end, the part that says whose site it is:
        # "…login.evil.example", never "accounts.google.com.lo…"
        host = getattr(self, "_pill_host", False) and "/" not in text
        if host:
            # Too long: whole parts go from the front first ("dash.cloudflare.com" → "cloudflare.com", as Arc shows
            # it), never the site's own name and ending; only that, still too long, is cut (rather than mid-word)
            labels, keep = text.split("."), site_labels(text)
            metrics = self.url_pill.fontMetrics()
            while len(labels) > keep and metrics.horizontalAdvance(".".join(labels)) > room:
                labels.pop(0)
            text = ".".join(labels)
        elide = Qt.TextElideMode.ElideLeft if host else Qt.TextElideMode.ElideRight
        # Still too long: a little smaller (to 11px) before anything's cut; then cut from the left
        size = PILL_FONT_PX
        font = QFont(self.url_pill.font())
        font.setPixelSize(size)
        if host:
            while size > PILL_MIN_FONT_PX:
                font.setPixelSize(size)
                if QFontMetrics(font).horizontalAdvance(text) <= room:
                    break
                size -= 1
        if size != getattr(self, "_pill_font_px", PILL_FONT_PX):
            self._pill_font_px = size
            self.url_pill.setStyleSheet("" if size == PILL_FONT_PX else f"QPushButton#UrlPill {{ font-size: {size}px; }}")
        font.setPixelSize(size)
        metrics = QFontMetrics(font)
        self.url_pill.setText(metrics.elidedText(text, elide, max(room, 20)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pill_text()
        self._fit_switcher()
        self.favorites.refit()

    def _fit_switcher(self):
        """The Space buttons share what room the bottom bar has: full size when there's room, smaller and closer
        together (to two-thirds of it) when there are many Spaces or the sidebar's narrow, never on top of each
        other. At most SPACE_SHOWN at once: with more, the ones round the Space you're in show."""
        buttons = [b for b in self.space_buttons if not sip.isdeleted(b)]
        if not buttons:
            return
        margins = self.layout().contentsMargins()
        others = [b for b in self._bottom_buttons if not sip.isdeleted(b) and b.isVisibleTo(self)]
        room = self.width() - margins.left() - margins.right() - sum(b.sizeHint().width() for b in others) \
            - 2 * (len(others) + 1)
        fit = min(SPACE_SHOWN, max(1, room // (SPACE_BUTTON_SMALLEST + 1)))  # (how many fit at the smallest)
        if fit < len(buttons):  # (some will be hidden: room for the arrows that say so)
            room -= 2 * (SPACE_MORE + 2)
            fit = min(SPACE_SHOWN, max(1, room // (SPACE_BUTTON_SMALLEST + 1)))
        crowded = room < min(fit, len(buttons)) * (SPACE_BUTTON + 2)
        self.switcher.setSpacing(1 if crowded else 2)
        self._switcher_fit = fit
        active = next((i for i, b in enumerate(buttons) if b.property("selected")), 0)
        if active != getattr(self, "_switcher_active", None):
            self._switcher_active, self._switcher_first = active, None  # (a new Space: its page shows)
        first = self._switcher_first
        if first is None:
            first = (active // fit) * fit  # (in pages: 1–3, 4–6…)
        first = max(0, min(first, len(buttons) - fit))
        self._switcher_first = first
        shown = buttons[first:first + fit]
        for button in buttons:
            more = len(buttons) - len(shown)
            button.setToolTip(button.property("name_tip") + (f"\nSwipe here for the other {more} Space"
                                                              f"{'s' if more != 1 else ''}" if more else ""))
        hidden = {-1: first, 1: len(buttons) - first - len(shown)}  # (how many off each side)
        color = _with_alpha(self.browser.theme.text, 0.5)
        for more in self.switcher_more:
            count = hidden[more.property("step")]
            more.setVisible(count > 0)
            if count:
                more.setIcon(qta.icon(more.property("icon_name"), color=color))
                more.setToolTip(f"{count} more Space{'s' if count != 1 else ''}")
                more.setAccessibleName(f"Show {count} more Space{'s' if count != 1 else ''}")
        size = max(SPACE_BUTTON_SMALLEST, min(SPACE_BUTTON, room // len(shown) - (1 if crowded else 2)))
        for button in buttons:
            button.setVisible(button in shown)
            button.setFixedSize(size, size)
            full = 16 if button.property("has_icon") else 10
            small = max(12, min(full, size - 4))  # (less room round it, but never too small to tell apart)
            button.setIconSize(QSize(full, full) if not crowded else QSize(small, small))

    def refresh_folder(self, folder):
        for row in self.findChildren(FolderRow):
            if row.folder is folder:
                row.refresh()
        if self.native is not None:
            self.native.schedule()

    def space_title_rect(self) -> QRect:
        """The Space's name row, global (the native view's top, while it's shown)."""
        native = self.native
        if native is not None and native.container.isVisible():
            return QRect(native.container.mapToGlobal(QPoint(0, 2)), QSize(native.container.width(), 30))
        return QRect(self.space_title.mapToGlobal(QPoint(0, 0)), self.space_title.size())

    def space_title_anchor(self) -> QPoint:
        """Where the Space editor opens: under the Space's name (in the native view, while it's shown)."""
        if self.native is not None and self.native.container.isVisible():
            return self.native.anchor()
        return self.space_title.mapToGlobal(QPoint(0, self.space_title.height() + 4))

    def update_shield(self):
        tab = self.browser.current_tab
        blocker = self.browser.adblock
        host = QUrl(tab.url).host() if tab and tab.url else ""
        if not host or tab.url.startswith("pbext://"):  # extension pages aren't websites
            self.shield_btn.hide()
            return
        color = _with_alpha(self.browser.theme.text, 0.55)
        if not blocker.enabled or blocker.is_allowed(host):
            self.shield_btn.setIcon(qta.icon("ph.shield-slash", color=color))
            self.shield_btn.setText("")
            self.shield_btn.setToolTip("Ad blocking is off here · click for Site Settings")
        else:
            count = blocker.count_for(tab.url)
            self.shield_btn.setIcon(qta.icon("ph.shield-check", color=color))
            self.shield_btn.setText(str(count) if count else "")
            self.shield_btn.setToolTip(f"{count} ads and trackers blocked on {display_host(tab.url)} · click for Site "
                                       "Settings")
        self.shield_btn.show()
        self._fit_pill_text()

    def enterEvent(self, event):
        self.browser.sidebar_mouse_entered()

    def leaveEvent(self, event):
        self.browser.sidebar_mouse_left()

    # --- Two-finger horizontal swipe switches Spaces, like Arc ------------------

    def eventFilter(self, obj, event):
        if event.type() == event.Type.Wheel and self._handle_swipe(event):
            return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        if not self._handle_swipe(event):
            super().wheelEvent(event)

    def _handle_swipe(self, event) -> bool:
        if self._page_switcher(event):
            return True
        gestures = self.browser.feature("gestures")  # (the sidebar follows your fingers to the next Space)
        return gestures is not None and gestures.space_swipe.wheel(event)

    def _switcher_area(self) -> QRect:
        """Where the Space buttons showing are, with a little room round them."""
        shown = [b for b in self.space_buttons if not sip.isdeleted(b) and b.isVisible()]
        if not shown:
            return QRect()
        area = QRect()
        for button in shown:
            area = area.united(QRect(button.mapTo(self, QPoint(0, 0)), button.size()))
        return area.adjusted(-8, -6, 8, 6)

    def _page_switcher(self, event) -> bool:
        """Sideways over the Space buttons, when some are hidden (a narrow sidebar, many Spaces): the next or
        last page of them, without switching Space. One page a swipe; the buttons go the way your fingers do."""
        buttons = [b for b in self.space_buttons if not sip.isdeleted(b)]
        fit = getattr(self, "_switcher_fit", len(buttons))
        phase = event.phase()
        if phase == Qt.ScrollPhase.ScrollBegin:
            here = self.mapFromGlobal(event.globalPosition().toPoint())
            self._paging = len(buttons) > fit and self._switcher_area().contains(here)
            self._paged, self._page_dx = False, 0.0
            return self._paging
        if phase == Qt.ScrollPhase.NoScrollPhase:  # (a mouse wheel: a step, a page)
            here = self.mapFromGlobal(event.globalPosition().toPoint())
            if len(buttons) <= fit or not self._switcher_area().contains(here):
                return False
            delta = event.angleDelta()
            step = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
            if step:
                self._turn_switcher_page(1 if step < 0 else -1)
            return True
        if not getattr(self, "_paging", False):
            return False
        if phase in (Qt.ScrollPhase.ScrollEnd, Qt.ScrollPhase.ScrollMomentum):
            if phase == Qt.ScrollPhase.ScrollEnd:
                self._paging = False
            return True
        self._page_dx += event.pixelDelta().x()
        if not self._paged and abs(self._page_dx) >= 24:
            self._paged = True
            self._turn_switcher_page(1 if self._page_dx < 0 else -1)  # (fingers to the left: the ones on the right)
        return True

    def _turn_switcher_page(self, step):
        buttons = [b for b in self.space_buttons if not sip.isdeleted(b)]
        fit = getattr(self, "_switcher_fit", len(buttons))
        first = getattr(self, "_switcher_first", 0) or 0
        pages = list(range(0, len(buttons), fit))
        page = min(range(len(pages)), key=lambda i: abs(pages[i] - first))
        self._switcher_first = pages[max(0, min(page + step, len(pages) - 1))]
        self._fit_switcher()
