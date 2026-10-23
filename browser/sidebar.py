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

import sys

import qtawesome as qta
from PyQt6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from .browse import is_answer_page
from .models import FAVORITES, PINNED, TODAY, Folder, display_host, walk_folders, walk_tabs
from .theme import dot_icon, letter_icon

TAB_MIME = "application/x-pybrowser-tab"
FAVORITE_COLUMNS = 4
INDENT = 16  # per folder level


def tab_icon(tab, theme):
    if tab.url.startswith("note:"):
        return qta.icon("ph.note-pencil", color=theme.accent)
    if is_answer_page(tab.url):
        return qta.icon("ph.compass", color=theme.accent)
    return tab.icon or letter_icon(display_host(tab.url) or tab.label, theme.accent)


def repolish(widget, **props):
    # Qt only re-evaluates [prop="..."] stylesheet selectors after an unpolish/polish
    for name, value in props.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class IconButton(QToolButton):
    def __init__(self, icon_name, tooltip="", size=26, icon_size=16, parent=None):
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.icon_name = icon_name
        self.setToolTip(tooltip)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color):
        self.setIcon(qta.icon(self.icon_name, color=color, color_disabled=_with_alpha(color, 0.3)))


def _with_alpha(color, alpha):
    c = color.__class__(color)
    c.setAlphaF(alpha)
    return c


class ElidedLabel(QLabel):
    # A QLabel that shrinks with "…" instead of forcing the sidebar wider
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._refresh()

    def set_full_text(self, text):
        self._full = text
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 10)))


def start_drag(widget, node_id, press_pos):
    mime = QMimeData()
    mime.setData(TAB_MIME, node_id.encode())
    drag = QDrag(widget)
    drag.setMimeData(mime)
    drag.setPixmap(widget.grab())
    drag.setHotSpot(press_pos)
    split_drop = widget.browser.content.split_drop
    split_drop.activate(True)  # the page area accepts tabs too: drop one there to split
    drag.exec(Qt.DropAction.MoveAction)
    split_drop.activate(False)


class DraggableTab:
    # Mixin: a press activates the tab, dragging past the threshold starts a drag
    def _init_drag(self, browser, tab):
        self.browser = browser
        self.tab = tab
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self.browser.activate_tab(self.tab)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.browser.close_tab(self.tab)

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        press_pos, self._press_pos = self._press_pos, None
        start_drag(self, self.tab.id, press_pos)

    def mouseReleaseEvent(self, event):
        self._press_pos = None

    def contextMenuEvent(self, event):
        self.browser.show_tab_menu(self.tab, event.globalPos())


class TabRow(DraggableTab, QFrame):
    def __init__(self, browser, tab, kind, depth=0, parent=None):
        super().__init__(parent)
        self._init_drag(browser, tab)
        self.kind = kind
        self.setObjectName("TabRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10 + depth * INDENT, 0, 5, 0)
        layout.setSpacing(9)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.title_label = ElidedLabel()
        # Pinned tabs get "—" (unload) like Arc; Today tabs get "×" (close)
        self.close_btn = IconButton("ph.minus" if kind == PINNED else "ph.x", "Close", size=22, icon_size=13)
        self.close_btn.clicked.connect(lambda: browser.close_tab(tab))
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.close_btn)

        tab.changed.connect(self.refresh)
        self.refresh()

    @property
    def selected(self):
        return self.property("selected") is True

    def refresh(self):
        theme = self.browser.theme
        self.icon_label.setPixmap(tab_icon(self.tab, theme).pixmap(16, 16))
        self.title_label.set_full_text(self.tab.label)
        self.title_label.setToolTip(self.tab.url)
        repolish(self.title_label, unloaded=(self.kind == PINNED and self.tab.view is None))
        self.close_btn.set_color(theme.text)
        self._update_close()

    def set_selected(self, selected):
        if self.selected != selected:
            repolish(self, selected=selected)
        self._update_close()

    def _update_close(self):
        visible = self.selected or self.underMouse()
        if self.kind == PINNED:
            visible = visible and self.tab.view is not None
        self.close_btn.setVisible(visible)

    def enterEvent(self, event):
        self._update_close()

    def leaveEvent(self, event):
        self._update_close()


class FolderRow(QFrame):
    """A pinned folder: click to open/close, drag to move, right-click for options."""

    def __init__(self, browser, folder, depth=0, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.folder = folder
        self._press_pos = None
        self.setObjectName("TabRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10 + depth * INDENT, 0, 10, 0)
        layout.setSpacing(9)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.name_label = ElidedLabel(folder.name)
        self.count_label = QLabel()
        self.count_label.setProperty("dim", True)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.count_label)
        self.refresh()

    def refresh(self):
        color = _with_alpha(self.browser.theme.text, 0.75)
        icon = "ph.folder-open" if self.folder.expanded else "ph.folder"
        self.icon_label.setPixmap(qta.icon(icon, color=color).pixmap(16, 16))
        self.name_label.set_full_text(self.folder.name)
        # A collapsed folder shows how many tabs it hides
        self.count_label.setText("" if self.folder.expanded else str(sum(1 for _ in walk_tabs(self.folder.children))))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        press_pos, self._press_pos = self._press_pos, None
        start_drag(self, self.folder.id, press_pos)

    def mouseReleaseEvent(self, event):
        # A click (press + release without dragging) opens or closes the folder
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            self._press_pos = None
            self.browser.toggle_folder(self.folder)

    def contextMenuEvent(self, event):
        self.browser.show_folder_menu(self.folder, event.globalPos())


class FavoriteTile(DraggableTab, QToolButton):
    def __init__(self, browser, tab, parent=None):
        super().__init__(parent)
        self._init_drag(browser, tab)
        self.setObjectName("FavoriteTile")
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(20, 20))
        tab.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.setIcon(tab_icon(self.tab, self.browser.theme))
        self.setToolTip(self.tab.label)

    def set_selected(self, selected):
        if (self.property("selected") is True) != selected:
            repolish(self, selected=selected)


class DropZone(QWidget):
    """Base for areas that accept dragged tabs and folders.

    Subclasses implement drop_target(pos) -> (folder or None, index, indicator rect, into_folder).
    """

    def __init__(self, browser, kind, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.kind = kind
        self.setAcceptDrops(True)
        self.indicator = QFrame(self)
        self.indicator.setObjectName("DropLine")
        self.indicator.hide()
        self.box = QFrame(self)  # outlines a folder when dropping into it
        self.box.setObjectName("DropBox")
        self.box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.box.hide()

    def drop_target(self, pos):
        raise NotImplementedError

    def _dragged(self, event):
        node = self.browser.store.find_node(bytes(event.mimeData().data(TAB_MIME)).decode())
        if node is None or (isinstance(node, Folder) and self.kind != PINNED):
            return None, None
        target = self.drop_target(event.position().toPoint())
        folder = target[0]
        if isinstance(node, Folder) and folder is not None and (folder is node or folder in walk_folders(node.children)):
            return None, None
        return node, target

    def _hide_indicators(self):
        self.indicator.hide()
        self.box.hide()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        node, target = self._dragged(event)
        self._hide_indicators()
        if node is None:
            event.ignore()
            return
        _, _, rect, into_folder = target
        marker = self.box if into_folder else self.indicator
        marker.setGeometry(rect)
        marker.show()
        marker.raise_()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hide_indicators()

    def dropEvent(self, event):
        self._hide_indicators()
        node, target = self._dragged(event)
        if node is None:
            return
        event.acceptProposedAction()
        folder, index, _, _ = target
        # Defer so the drag's source widget isn't rebuilt while still inside drag.exec()
        QTimer.singleShot(0, lambda: self.browser.move_tab(node, self.kind, index, folder))


class TabSection(DropZone):
    def __init__(self, browser, kind, empty_hint="", parent=None):
        super().__init__(browser, kind, parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(2)
        self.nodes: list = []
        self.entries: list[tuple] = []  # (row widget, node, parent folder, depth), in display order
        self.hint = QLabel(empty_hint)
        self.hint.setProperty("dim", True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFixedHeight(30)
        self.layout_.addWidget(self.hint)

    @property
    def rows(self) -> list[TabRow]:
        return [row for row, *_ in self.entries if isinstance(row, TabRow)]

    def set_tabs(self, nodes):
        for row, *_ in self.entries:
            row.deleteLater()
        self.nodes = nodes
        self.entries = []
        self._add_rows(nodes, None, 0)
        self.hint.setVisible(not nodes and bool(self.hint.text()))
        self.setMinimumHeight(0 if nodes or self.hint.text() else 12)

    def _add_rows(self, nodes, parent, depth):
        for node in nodes:
            if isinstance(node, Folder):
                row = FolderRow(self.browser, node, depth, self)
            else:
                row = TabRow(self.browser, node, self.kind, depth, self)
            self.entries.append((row, node, parent, depth))
            self.layout_.addWidget(row)
            if isinstance(node, Folder) and node.expanded:
                self._add_rows(node.children, node, depth + 1)

    def _line(self, y, depth):
        x = 6 + depth * INDENT
        return QRect(x, max(y, 0), self.width() - x - 6, 2)

    def drop_target(self, pos):
        for row, node, parent, depth in self.entries:
            g = row.geometry()
            if pos.y() > g.bottom():
                continue
            siblings = parent.children if parent else self.nodes
            i = next(k for k, n in enumerate(siblings) if n is node)
            if isinstance(node, Folder):
                # The middle of a folder row means "put it inside"
                third = g.height() / 3
                if g.top() + third <= pos.y() <= g.bottom() - third:
                    return node, len(node.children), g.adjusted(1, 0, -1, 0), True
            if pos.y() < g.center().y():
                return parent, i, self._line(g.top() - 2, depth), False
            if isinstance(node, Folder) and node.expanded:
                return node, 0, self._line(g.bottom() + 1, depth + 1), False
            return parent, i + 1, self._line(g.bottom() + 1, depth), False
        if self.entries:
            return None, len(self.nodes), self._line(self.entries[-1][0].geometry().bottom() + 1, 0), False
        return None, 0, self._line(self.height() // 2, 0), False


class FavoritesGrid(DropZone):
    def __init__(self, browser, parent=None):
        super().__init__(browser, FAVORITES, parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.tiles: list[FavoriteTile] = []
        self.hint = QLabel("Drag tabs here to favorite")
        self.hint.setProperty("dim", True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFixedHeight(32)

    def set_tabs(self, tabs):
        for tile in self.tiles:
            tile.deleteLater()
        self.grid.removeWidget(self.hint)
        self.tiles = [FavoriteTile(self.browser, t, self) for t in tabs]
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // FAVORITE_COLUMNS, i % FAVORITE_COLUMNS)
        if self.tiles:
            self.hint.hide()
            # Keep columns equal width even when the last row isn't full
            for col in range(FAVORITE_COLUMNS):
                self.grid.setColumnStretch(col, 1)
        else:
            self.grid.addWidget(self.hint, 0, 0, 1, FAVORITE_COLUMNS)
            self.hint.show()

    def drop_target(self, pos):
        index = self._drop_index(pos)
        return None, index, self._indicator_rect(index), False

    def _drop_index(self, pos):
        for i, tile in enumerate(self.tiles):
            g = tile.geometry()
            same_row = g.top() <= pos.y() <= g.bottom() + self.grid.spacing()
            if same_row and pos.x() < g.center().x():
                return i
            if pos.y() < g.top():
                return i
        return len(self.tiles)

    def _indicator_rect(self, index):
        if not self.tiles:
            return QRect(6, self.height() // 2, self.width() - 12, 2)
        if index < len(self.tiles):
            g = self.tiles[index].geometry()
            return QRect(g.left() - 4, g.top() + 6, 2, g.height() - 12)
        g = self.tiles[-1].geometry()
        return QRect(g.right() + 2, g.top() + 6, 2, g.height() - 12)


class NewTabRow(QFrame):
    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("TabRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 5, 0)
        layout.setSpacing(9)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.text_label = QLabel("New Tab")
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label, 1)

    def set_color(self, color):
        self.icon_label.setPixmap(qta.icon("ph.plus", color=_with_alpha(color, 0.6)).pixmap(16, 16))
        self.text_label.setStyleSheet(f"color: rgba({color.red()},{color.green()},{color.blue()},150);")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.browser.open_command_bar("new")


class Sidebar(QWidget):
    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)  # only painted when floating
        self.setMinimumWidth(190)
        self.setMaximumWidth(420)
        self._swipe = 0.0
        self._swiped = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 4, 10)
        layout.setSpacing(8)

        # Top row: room for the macOS traffic lights, then the navigation buttons
        top = QHBoxLayout()
        top.setSpacing(2)
        if sys.platform == "darwin":
            top.addSpacing(66)
        self.toggle_btn = IconButton("msc.layout-sidebar-left", "Toggle Sidebar (⌘S)")
        self.back_btn = IconButton("ph.arrow-left", "Back (⌘[)")
        self.forward_btn = IconButton("ph.arrow-right", "Forward (⌘])")
        self.reload_btn = IconButton("ph.arrow-clockwise", "Reload (⌘R)")
        self.toggle_btn.clicked.connect(browser.toggle_sidebar)
        self.back_btn.clicked.connect(browser.go_back)
        self.forward_btn.clicked.connect(browser.go_forward)
        self.reload_btn.clicked.connect(browser.reload)
        top.addWidget(self.toggle_btn)
        top.addStretch(1)
        for btn in (self.back_btn, self.forward_btn, self.reload_btn):
            top.addWidget(btn)
        layout.addLayout(top)
        layout.addSpacing(2)

        self.url_pill = QPushButton()
        self.url_pill.setObjectName("UrlPill")
        self.url_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.url_pill.setIconSize(QSize(13, 13))
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
            lambda: browser.show_adblock_menu(self.shield_btn.mapToGlobal(QPoint(0, self.shield_btn.height()))))
        pill_layout.addWidget(self.shield_btn)
        layout.addWidget(self.url_pill)
        layout.addSpacing(2)

        self.favorites = FavoritesGrid(browser)
        layout.addWidget(self.favorites)
        layout.addSpacing(4)

        # Everything that belongs to the Space scrolls together
        body = QWidget()
        body.setObjectName("SidebarScrollBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(4)

        self.space_title = QLabel()
        self.space_title.setObjectName("SpaceTitle")
        self.space_title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.space_title.customContextMenuRequested.connect(
            lambda pos: browser.show_space_menu(browser.store.active_space, self.space_title.mapToGlobal(pos)))
        body_layout.addWidget(self.space_title)

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
        divider_row.addWidget(divider, 1, Qt.AlignmentFlag.AlignVCenter)
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
        layout.addWidget(self.scroll, 1)

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
        bottom.addStretch(1)
        bottom.addLayout(self.switcher)
        bottom.addStretch(1)
        bottom.addWidget(self.new_space_btn)
        layout.addLayout(bottom)

    # --- Rebuilding ----------------------------------------------------------

    def rebuild(self):
        store = self.browser.store
        space = store.active_space
        theme = self.browser.theme
        for btn in (self.toggle_btn, self.back_btn, self.forward_btn, self.reload_btn,
                    self.archive_btn, self.downloads_btn, self.new_space_btn):
            btn.set_color(theme.text)
        self.new_tab_row.set_color(theme.text)

        title = f"{space.emoji}  {space.name}" if space.emoji else space.name
        if space.profile != "default":
            title += f"   ·  {space.profile} profile"
        self.space_title.setText(title)
        self.favorites.set_tabs(store.favorites)
        self.pinned.set_tabs(space.pinned)
        self.today.set_tabs(space.today)
        self.clear_btn.setVisible(bool(space.today))
        self._rebuild_switcher()
        self.update_selection()
        self.update_nav()

    def _rebuild_switcher(self):
        for btn in self.space_buttons:
            btn.deleteLater()
        self.space_buttons = []
        store = self.browser.store
        for i, space in enumerate(store.spaces):
            btn = QToolButton()
            btn.setObjectName("SpaceDot")
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{space.name}  (⌃{i + 1})" if i < 9 else space.name)
            if space.emoji:
                btn.setText(space.emoji)
            else:
                btn.setIcon(dot_icon(space.color, 12))
                btn.setIconSize(QSize(12, 12))
            btn.setProperty("selected", space.id == store.active_space_id)
            btn.clicked.connect(lambda _, s=space: self.browser.switch_space(s))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, s=space, b=btn: self.browser.show_space_menu(s, b.mapToGlobal(pos)))
            self.switcher.addWidget(btn)
            self.space_buttons.append(btn)

    # --- Cheap updates (no widget rebuild) ---------------------------------------

    def update_selection(self):
        current = self.browser.current_tab
        split = (self.browser.store.split_of(current) or []) if current else []
        for tile in self.favorites.tiles:
            tile.set_selected(tile.tab is current)
        for row in self.pinned.rows + self.today.rows:
            row.set_selected(row.tab is current)
            in_split = row.tab is not current and any(t is row.tab for t in split)
            if (row.property("insplit") is True) != in_split:
                repolish(row, insplit=in_split)
            row.refresh()

    def update_nav(self):
        tab = self.browser.current_tab
        view = self.browser._current_view()  # None for notes, which have no history
        history = view.history() if view else None
        self.back_btn.setEnabled(bool(history and history.canGoBack()))
        self.forward_btn.setEnabled(bool(history and history.canGoForward()))
        self.reload_btn.setEnabled(view is not None)

        theme = self.browser.theme
        if tab and tab.url.startswith("note:"):
            self.url_pill.setIcon(qta.icon("ph.note-pencil", color=_with_alpha(theme.text, 0.55)))
            self.url_pill.setText("Note")
            self.url_pill.setToolTip("")
        elif tab and is_answer_page(tab.url):
            self.url_pill.setIcon(qta.icon("ph.compass", color=_with_alpha(theme.text, 0.55)))
            self.url_pill.setText("Browse for Me")
            self.url_pill.setToolTip("")
        elif tab and tab.url:
            secure = tab.url.startswith("https://")
            self.url_pill.setIcon(qta.icon("ph.lock-simple" if secure else "ph.globe-simple",
                                           color=_with_alpha(theme.text, 0.55)))
            self.url_pill.setText(display_host(tab.url) or tab.url)
            self.url_pill.setToolTip(tab.url)
        else:
            self.url_pill.setIcon(qta.icon("ph.magnifying-glass", color=_with_alpha(theme.text, 0.55)))
            self.url_pill.setText("Search or Enter URL…")
            self.url_pill.setToolTip("")
        self.update_shield()

    def update_shield(self):
        tab = self.browser.current_tab
        blocker = self.browser.adblock
        host = QUrl(tab.url).host() if tab and tab.url else ""
        if not host:
            self.shield_btn.hide()
            return
        color = _with_alpha(self.browser.theme.text, 0.55)
        if not blocker.enabled or blocker.is_allowed(host):
            self.shield_btn.setIcon(qta.icon("ph.shield-slash", color=color))
            self.shield_btn.setText("")
            self.shield_btn.setToolTip("Ad blocking is off here")
        else:
            count = blocker.count_for(tab.url)
            self.shield_btn.setIcon(qta.icon("ph.shield-check", color=color))
            self.shield_btn.setText(str(count) if count else "")
            self.shield_btn.setToolTip(f"{count} ads and trackers blocked on {display_host(tab.url)}")
        self.shield_btn.show()

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
        delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta() / 4
        if event.phase() in (Qt.ScrollPhase.ScrollBegin, Qt.ScrollPhase.NoScrollPhase):
            self._swipe, self._swiped = 0.0, False
        if abs(delta.x()) <= abs(delta.y()):
            return False
        self._swipe += delta.x()
        if not self._swiped and abs(self._swipe) > 90:
            self._swiped = True  # one switch per gesture
            self.browser.cycle_space(1 if self._swipe < 0 else -1)
        return True
