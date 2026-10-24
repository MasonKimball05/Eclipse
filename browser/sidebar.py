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
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QSizePolicy, QSpacerItem, QToolButton, QVBoxLayout, QWidget)

from . import macos
from .browse import is_answer_page
from .devmode import dev_label, is_dev_url
from .integrations import config as integration_config, is_job_posting, summarize
from .now_playing import NowPlayingCard
from .upnext import UpNextCard
from .models import FAVORITES, PINNED, TODAY, Folder, Tab, display_host, walk_folders, walk_tabs
from .icons import is_symbol, space_icon, symbol
from .theme import legible, letter_icon, quiet_scrollbars

TAB_MIME = "application/x-pybrowser-tab"
FAVORITE_COLUMNS = 4
INDENT = 16  # per folder level


def is_away(tab) -> bool:
    """A pinned tab or favorite showing something other than the page it was pinned at."""
    if not tab.home_url or not tab.url:
        return False
    def place(u):
        # The site and path only: pages that add ?query or #fragment as they load haven't gone anywhere
        q = QUrl(u)
        return q.host().lower().removeprefix("www."), q.path().rstrip("/")
    return place(tab.url) != place(tab.home_url)


def tab_icon(tab, theme):
    if tab.url.startswith("note:"):
        return symbol("ph.note-pencil", theme.accent)
    if tab.url.startswith("easel:"):
        return symbol("ph.chalkboard-simple", theme.accent)
    if is_answer_page(tab.url):
        return symbol("ph.compass", theme.accent)
    return legible(tab.icon, theme) if tab.icon else letter_icon(display_host(tab.url) or tab.label, theme.accent)


def faded(icon, opacity, size=32):
    """A see-through copy of an icon (for inactive emoji, which can't be tinted)."""
    source = icon.pixmap(size, size)
    out = QPixmap(source.size())
    out.setDevicePixelRatio(source.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setOpacity(opacity)
    p.drawPixmap(0, 0, source)
    p.end()
    return QIcon(out)


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
        key = (self.icon_name, QColor(color).rgba())
        if getattr(self, "_icon_key", None) != key:  # unchanged colour: keep the icon we have
            self._icon_key = key
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
        self.icon_label.installEventFilter(self)
        # A pinned tab that's wandered off its pinned page shows "/" after its icon, like Arc; the icon
        # then turns into "back to the pinned page" when you point at it
        self.away_label = QLabel("/")
        self.away_label.setProperty("dim", True)
        self.away_label.hide()
        self.title_label = ElidedLabel()
        # Pinned tabs get "—" (unload) like Arc; Today tabs get "×" (close)
        self.close_btn = IconButton("ph.minus" if kind == PINNED else "ph.x", "Close", size=22, icon_size=13)
        self.close_btn.clicked.connect(lambda: browser.close_tab(tab))
        # Unread count from the site's title ("(3) Inbox"), or a dot after a notification
        self.unread_label = QLabel()
        self.unread_label.setObjectName("UnreadBadge")
        self.unread_label.setFixedHeight(16)
        self.unread_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unread_label.hide()
        # A speaker while the tab plays sound (or is muted): click to mute / unmute, like Arc
        self.audio_btn = IconButton("ph.speaker-high", "Mute Tab", size=22, icon_size=13)
        self.audio_btn.clicked.connect(lambda: browser.toggle_mute(tab))
        layout.addWidget(self.icon_label)
        layout.addWidget(self.away_label)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.unread_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.audio_btn)
        layout.addWidget(self.close_btn)

        tab.changed.connect(self.refresh)
        self.refresh()

    @property
    def selected(self):
        return self.property("selected") is True

    @property
    def away(self) -> bool:
        return self.kind != TODAY and is_away(self.tab)

    def refresh(self):
        theme = self.browser.theme
        self._paint_icon()
        self.away_label.setVisible(self.away)
        self.icon_label.setToolTip("Back to the pinned page" if self.away else "")
        self.icon_label.setCursor(Qt.CursorShape.PointingHandCursor if self.away else Qt.CursorShape.ArrowCursor)
        self.title_label.set_full_text(self.tab.label)
        sleeping = self.browser.is_sleeping(self.tab)
        self.title_label.setToolTip(self.tab.url + ("\nSleeping — click to wake" if sleeping else ""))
        repolish(self.title_label, unloaded=(self.kind == PINNED and self.tab.view is None) or sleeping,
                 fresh=self.tab.fresh)
        self.close_btn.set_color(theme.text)
        badge = self.tab.unread or self.tab.app_unread or ("•" if self.tab.notified else "")
        self.unread_label.setText(badge)
        self.unread_label.setVisible(bool(badge))
        audible, muted = self.browser.tab_audio(self.tab)
        self.audio_btn.setVisible(audible or muted)
        if audible or muted:
            self.audio_btn.icon_name = "ph.speaker-slash" if muted else "ph.speaker-high"
            self.audio_btn.setToolTip("Unmute Tab" if muted else "Mute Tab")
            self.audio_btn.set_color(theme.text)
        self._update_close()

    def _paint_icon(self, hovered=False):
        theme = self.browser.theme
        if hovered and self.away:
            icon = qta.icon("ph.arrow-u-up-left", color=theme.text)
        else:
            icon = tab_icon(self.tab, theme)
        self.icon_label.setPixmap(icon.pixmap(16, 16))

    def eventFilter(self, obj, event):
        if obj is self.icon_label and self.away:
            kind = event.type()
            if kind == QEvent.Type.Enter:
                self._paint_icon(hovered=True)
            elif kind == QEvent.Type.Leave:
                self._paint_icon()
            elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                return True  # not the start of a drag, or a click on the row
            elif kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.browser.go_home(self.tab)
                return True
        return super().eventFilter(obj, event)

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
        live = self.folder.live or {}
        if live:  # a Live Folder: what it follows, and how many items are new
            icon = "ph.github-logo" if live.get("kind") == "github" else "ph.rss"
            self.icon_label.setPixmap(qta.icon(icon, color=self.browser.theme.accent).pixmap(16, 16))
        else:
            icon = "ph.folder-open" if self.folder.expanded else "ph.folder"
            self.icon_label.setPixmap(qta.icon(icon, color=color).pixmap(16, 16))
        self.name_label.set_full_text(self.folder.name)
        fresh = sum(1 for t in walk_tabs(self.folder.children) if t.fresh)
        # Only a Live Folder's new items get a count, like Arc; how many tabs a folder holds is in its tooltip
        self.count_label.setText(f"{fresh} new" if fresh else "")
        count = sum(1 for _ in walk_tabs(self.folder.children))
        self.setToolTip(f"{count} tab{'s' if count != 1 else ''}" if not self.folder.expanded else "")

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
        self.update()  # the unread badge

    def set_selected(self, selected):
        if (self.property("selected") is True) != selected:
            repolish(self, selected=selected)

    def paintEvent(self, event):
        super().paintEvent(event)
        badge = self.tab.unread or self.tab.app_unread or ("" if not self.tab.notified else "•")
        if not badge:
            return
        # Arc's unread marker: a small pill on the icon's top-right corner
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = p.font()
        font.setPixelSize(9)
        font.setBold(True)
        p.setFont(font)
        text = "" if badge == "•" else badge
        w = max(10, p.fontMetrics().horizontalAdvance(text) + 7) if text else 9
        center = self.rect().center()
        rect = QRect(center.x() + 4, center.y() - 15, w, 13 if text else 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.browser.theme.accent))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if text:
            p.setPen(QColor("white"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


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
        if node is None or (isinstance(node, Folder) and self.kind == FAVORITES):
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

        # Top row: room for the macOS traffic lights, then the navigation buttons.
        # align_with_traffic_lights() fine-tunes this once the window exists.
        top = QHBoxLayout()
        top.setSpacing(2)
        self._lights_gap = QSpacerItem(66 if sys.platform == "darwin" else 0, 0)
        top.addItem(self._lights_gap)
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
        # Developer Mode: a DEV chip on localhost and file pages, with the error count; opens the console
        self.dev_btn = QToolButton()
        self.dev_btn.setObjectName("DevChip")
        self.dev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dev_btn.setToolTip("Developer Mode · click for the console (⌥⌘J)")
        self.dev_btn.clicked.connect(lambda: browser.devmode.toggle_drawer())
        self.dev_btn.hide()
        pill_layout.addWidget(self.dev_btn)
        browser.devmode.changed.connect(self.update_dev)
        # On a job posting: send it to Job Tracker in one click (My Apps)
        self.job_btn = QToolButton()
        self.job_btn.setObjectName("DevChip")
        self.job_btn.setText("Save Job")
        self.job_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.job_btn.setToolTip("Save this posting to Job Tracker")
        self.job_btn.clicked.connect(lambda: browser.save_job_posting())
        self.job_btn.hide()
        pill_layout.addWidget(self.job_btn)
        # The page's zoom, when it isn't 100% (click to reset)
        self.zoom_btn = QToolButton()
        self.zoom_btn.setObjectName("ShieldButton")
        self.zoom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_btn.setToolTip("Zoom for this site · click to reset to 100%")
        self.zoom_btn.clicked.connect(lambda: browser.zoom(0))
        self.zoom_btn.hide()
        pill_layout.addWidget(self.zoom_btn)
        pill_layout.addWidget(self.shield_btn)
        # Extensions (puzzle piece), shown once anything is installed; carries a badge like Chrome's
        self.ext_btn = QToolButton()
        self.ext_btn.setObjectName("ExtButton")
        self.ext_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.ext_btn.setIconSize(QSize(12, 12))
        self.ext_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ext_btn.setToolTip("Extensions")
        self.ext_btn.clicked.connect(
            lambda: browser.show_extensions_menu(self.ext_btn.mapToGlobal(QPoint(0, self.ext_btn.height()))))
        # Pinned extensions get their own buttons, just left of the puzzle piece
        self.pinned_ext_layout = QHBoxLayout()
        self.pinned_ext_layout.setContentsMargins(0, 0, 0, 0)
        self.pinned_ext_layout.setSpacing(0)
        self.pinned_ext_buttons: list[QToolButton] = []
        pill_layout.insertLayout(pill_layout.indexOf(self.ext_btn), self.pinned_ext_layout)
        pill_layout.addWidget(self.ext_btn)
        browser.extensions.badges_changed.connect(self.update_extensions)
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

        # The Space's name and icon: click (or right-click) to edit the Space
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self.space_title = QPushButton()
        self.space_title.setObjectName("SpaceTitle")
        self.space_title.setIconSize(QSize(16, 16))
        self.space_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.space_title.setToolTip("Edit Space: name, icon, colour, profile")
        self.space_title.clicked.connect(lambda: browser.edit_space())
        self.space_title.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.space_title.customContextMenuRequested.connect(lambda _pos: browser.edit_space())
        self.profile_badge = QLabel()
        self.profile_badge.setObjectName("ProfileBadge")
        title_row.addWidget(self.space_title)
        title_row.addWidget(self.profile_badge)
        title_row.addStretch(1)
        body_layout.addLayout(title_row)

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
        quiet_scrollbars(self.scroll)
        layout.addWidget(self.scroll, 1)

        self.up_next = UpNextCard(browser.up_next)
        browser.up_next.card = self.up_next
        layout.addWidget(self.up_next)
        self.now_playing = NowPlayingCard(browser.now_playing)
        browser.now_playing.card = self.now_playing
        layout.addWidget(self.now_playing)

        # Bottom: archive, the Space switcher, and "new Space"
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        self.archive_btn = IconButton("ph.archive", "Archived tabs")
        self.archive_btn.clicked.connect(
            lambda: browser.show_archive_menu(self.archive_btn.mapToGlobal(QPoint(0, 0))))
        self.downloads_btn = IconButton("ph.download-simple", "Downloads (⌥⌘L)")
        self.downloads_btn.clicked.connect(lambda: browser.show_downloads())
        # Homebase (your desktop's apps): a server with a dot that says how they're doing
        self.homebase_btn = QToolButton()
        self.homebase_btn.setObjectName("IconButton")
        self.homebase_btn.setFixedSize(26, 26)
        self.homebase_btn.setIconSize(QSize(18, 18))
        self.homebase_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.homebase_btn.clicked.connect(
            lambda: browser.show_homebase_menu(self.homebase_btn.mapToGlobal(QPoint(0, 0))))
        self.homebase_btn.hide()
        browser.homebase.changed.connect(self.update_homebase)
        self.new_space_btn = IconButton("ph.plus", "New Space")
        self.new_space_btn.clicked.connect(browser.new_space)
        self.switcher = QHBoxLayout()
        self.switcher.setSpacing(2)
        self.space_buttons: list[QToolButton] = []
        bottom.addWidget(self.archive_btn)
        bottom.addWidget(self.downloads_btn)
        bottom.addWidget(self.homebase_btn)
        bottom.addStretch(1)
        bottom.addLayout(self.switcher)
        bottom.addStretch(1)
        bottom.addWidget(self.new_space_btn)
        layout.addLayout(bottom)

    # --- Rebuilding ----------------------------------------------------------

    def align_with_traffic_lights(self):
        """Centre the top-row buttons on the traffic lights, just to their right (like Arc)."""
        lights = macos.traffic_lights(self)
        if lights is None:
            return
        right, centre = lights
        offset = self.mapTo(self.window(), QPoint(0, 0))  # the sidebar floats 6pt in when hidden
        size = self.toggle_btn.height()
        layout = self.layout()
        margins = layout.contentsMargins()
        layout.setContentsMargins(margins.left(), max(round(centre - offset.y() - size / 2), 0),
                                  margins.right(), margins.bottom())
        # The gap after the lights matches the lights' own spacing (~9pt), minus the button's padding
        self._lights_gap.changeSize(max(round(right + 9 - offset.x() - margins.left() - 4), 0), 0)
        layout.invalidate()

    def rebuild(self):
        store = self.browser.store
        space = store.active_space
        theme = self.browser.theme
        for btn in (self.toggle_btn, self.back_btn, self.forward_btn, self.reload_btn,
                    self.archive_btn, self.downloads_btn, self.new_space_btn):
            btn.set_color(theme.text)
        self.new_tab_row.set_color(theme.text)

        self.space_title.setText(space.name)
        self.space_title.setIcon(space_icon(space, theme.text, 32))
        self.profile_badge.setText(space.profile)
        self.profile_badge.setToolTip(f"This Space uses the “{space.profile}” profile (its own logins and cookies)")
        self.profile_badge.setVisible(space.profile != "default")
        self.favorites.set_tabs(store.favorites)
        self.pinned.set_tabs(space.pinned)
        self.today.set_tabs(space.today)
        self.clear_btn.setVisible(bool(space.today))
        self._update_tidy_visibility()
        self._rebuild_switcher()
        self.update_selection()
        self.update_nav()

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
        from .tidy import MIN_TABS
        loose = sum(1 for n in self.browser.store.active_space.today if isinstance(n, Tab))
        self.tidy_btn.setVisible(self._tidy_state != "idle" or
                                 (self.browser.claude.available and loose >= MIN_TABS))

    def _tidy_clicked(self):
        if self._tidy_state == "undo":
            self.browser.undo_tidy()
        elif self._tidy_state == "idle":
            self.browser.tidy_today()

    def _rebuild_switcher(self):
        for btn in self.space_buttons:
            btn.deleteLater()
        self.space_buttons = []
        store = self.browser.store
        text = self.browser.theme.text
        for i, space in enumerate(store.spaces):
            active = space.id == store.active_space_id
            btn = QToolButton()
            btn.setObjectName("SpaceDot")
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{space.name}  (⌃{i + 1})" if i < 9 else space.name)
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
        if tab and tab.url.startswith(("note:", "easel:")):
            easel = tab.url.startswith("easel:")
            self.url_pill.setIcon(qta.icon("ph.chalkboard-simple" if easel else "ph.note-pencil",
                                           color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text("Easel" if easel else "Note")
            self.url_pill.setToolTip("")
        elif tab and is_answer_page(tab.url):
            self.url_pill.setIcon(qta.icon("ph.compass", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text("Browse for Me")
            self.url_pill.setToolTip("")
        elif tab and tab.url.startswith("pbext://"):
            ext = self.browser.extensions.extensions.get(QUrl(tab.url).host())
            self.url_pill.setIcon(qta.icon("ph.puzzle-piece", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(ext.name if ext else "Extension")
            self.url_pill.setToolTip(tab.url)
        elif tab and tab.url and is_dev_url(tab.url):
            self.url_pill.setIcon(qta.icon("ph.code", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(dev_label(tab.url))  # the full address, port and path included
            self.url_pill.setToolTip(tab.url)
        elif tab and tab.url:
            secure = tab.url.startswith("https://")
            self.url_pill.setIcon(qta.icon("ph.lock-simple" if secure else "ph.globe-simple",
                                           color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text(display_host(tab.url) or tab.url)
            self.url_pill.setToolTip(tab.url)
        else:
            self.url_pill.setIcon(qta.icon("ph.magnifying-glass", color=_with_alpha(theme.text, 0.55)))
            self._set_pill_text("Search or Enter URL…")
            self.url_pill.setToolTip("")
        self.update_shield()
        self.update_extensions()
        self.update_zoom()
        self.update_dev()
        cfg = integration_config(self.browser.store.settings)
        self.job_btn.setVisible(bool(tab and cfg["jobtracker"] and is_job_posting(tab.url)))
        self._fit_pill_text()

    def update_homebase(self):
        hb = self.browser.homebase
        if not hb.url:
            self.homebase_btn.hide()
            return
        theme = self.browser.theme
        if hb.status is None:
            dot, tip = QColor("#8e8e93"), f"Homebase isn't reachable ({hb.error or 'offline'})"
        else:
            up, total, _apps = summarize(hb.status)
            dot = QColor("#30d158") if up == total else QColor("#ff9f0a") if up else QColor("#ff453a")
            tip = f"Homebase · {hb.status.get('host', '')} · {up} of {total} apps up"
        pm = QPixmap(52, 52)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        qta.icon("ph.hard-drives", color=theme.text).paint(p, 1, 1, 22, 22)
        p.setPen(QColor(theme.top))  # a ring in the sidebar's colour, so the dot stands off the icon
        p.setBrush(dot)
        p.drawEllipse(QRectF(16, 16, 9, 9))
        p.end()
        self.homebase_btn.setIcon(QIcon(pm))
        self.homebase_btn.setToolTip(tip)
        self.homebase_btn.show()

    def update_dev(self):
        tab = self.browser.current_tab
        view = self.browser._current_view()
        dev = bool(tab and tab.url and is_dev_url(tab.url) and view is not None)
        errors = self.browser.devmode.errors(view) if dev else 0
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

    def _set_pill_text(self, text):
        self._pill_text = text
        self.url_pill.setText(text)

    def _fit_pill_text(self):
        # Long hosts are cut short with "…" instead of running under the shield and puzzle buttons
        text = getattr(self, "_pill_text", "")
        buttons = sum(b.sizeHint().width() + 4 for b in (self.job_btn, self.dev_btn, self.zoom_btn, self.shield_btn, self.ext_btn, *self.pinned_ext_buttons)
                      if b.isVisibleTo(self) or b in self.pinned_ext_buttons)
        room = self.url_pill.width() - self.url_pill.iconSize().width() - 34 - buttons
        self.url_pill.setText(self.url_pill.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(room, 20)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pill_text()

    def refresh_folder(self, folder):
        for row in self.findChildren(FolderRow):
            if row.folder is folder:
                row.refresh()

    def update_extensions(self):
        host = self.browser.extensions
        for button in self.pinned_ext_buttons:
            button.deleteLater()
        self.pinned_ext_buttons = []
        if not host.extensions and not host.scripts:
            self.ext_btn.hide()
            self._fit_pill_text()
            return
        tab = self.browser.current_tab
        url = tab.url if tab else ""
        pinned = self.browser.pinned_extensions()
        for ext in pinned:
            button = QToolButton()
            button.setObjectName("ExtButton")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(14, 14))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(QIcon(str(ext.icon_file)) if ext.icon_file else
                           letter_icon(ext.name, self.browser.theme.accent))
            button.setText(host.badge_text(ext, url))
            button.setToolTip(ext.name)
            button.clicked.connect(lambda _=False, e=ext, b=button: self.browser.click_extension(e, b))
            self.pinned_ext_layout.addWidget(button)
            self.pinned_ext_buttons.append(button)
        # The puzzle piece shows a badge only for extensions that aren't pinned (those show their own)
        badge = next((b for b in (host.badge_text(e, url) for e in host.extensions.values()
                                  if e.enabled and e not in pinned) if b), "")
        self.ext_btn.setIcon(qta.icon("ph.puzzle-piece", color=_with_alpha(self.browser.theme.text, 0.55)))
        self.ext_btn.setText(badge)
        self.ext_btn.show()
        self._fit_pill_text()

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
            self.shield_btn.setToolTip("Ad blocking is off here")
        else:
            count = blocker.count_for(tab.url)
            self.shield_btn.setIcon(qta.icon("ph.shield-check", color=color))
            self.shield_btn.setText(str(count) if count else "")
            self.shield_btn.setToolTip(f"{count} ads and trackers blocked on {display_host(tab.url)}")
        self.shield_btn.show()
        self._fit_pill_text()

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
