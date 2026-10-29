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
from PyQt6.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDrag, QIcon, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QSizePolicy, QSpacerItem, QToolButton, QVBoxLayout, QWidget)

from PyQt6 import sip

from . import macos
from .devmode import dev_label, is_dev_url
from .tab_titles import shown_title
from .models import FAVORITES, PINNED, TODAY, Folder, Tab, display_host, walk_folders, walk_tabs
from .icons import is_symbol, space_icon, symbol
from .theme import legible, letter_icon, noise_pixmap, overlay_scrollbar, plain_tip

TAB_MIME = "application/x-eclipse-tab"
TABS_MIME = "application/x-eclipse-tabs"  # several picked tabs dragged together: their ids, in order
PILL_ICON = 13
PILL_ICON_DROP = 1.5  # the URL pill's icon sits this much lower than centre, level with the lowercase text


def pill_icon(name, color) -> QIcon:
    """An icon for the URL pill, drawn PILL_ICON_DROP lower than centred: text's box has room above for
    capitals, so lowercase sits low in it, and a centred icon looked as if it floated above the address."""
    ratio = 3  # (sharp on any screen)
    pm = QPixmap(round(PILL_ICON * ratio), round((PILL_ICON + 2 * PILL_ICON_DROP) * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    qta.icon(name, color=color).paint(p, QRect(0, round(2 * PILL_ICON_DROP), PILL_ICON, PILL_ICON))
    p.end()
    return QIcon(pm)


# The GitHub Live Folder's sections (live_folders.SECTIONS); its other sub-folders are your repos
GITHUB_SECTION_ICONS = {"Review Requested": "ph.eye", "My Pull Requests": "ph.git-pull-request",
                        "Assigned to Me": "ph.user-circle", "Notifications": "ph.bell"}
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


def is_answer_page(url: str) -> bool:
    """A Browse for Me answer (a page it wrote in the data folder's browse/): it gets a compass."""
    return url.startswith("file:") and "/browse/" in url and url.endswith(".html")


def tab_icon(tab, theme):
    if tab.url.startswith("note:"):
        return symbol("ph.note-pencil", theme.accent_text)
    if tab.url.startswith("easel:"):
        return symbol("ph.chalkboard-simple", theme.accent_text)
    if is_answer_page(tab.url):
        return symbol("ph.compass", theme.accent_text)
    if tab.icon:
        return legible(tab.icon, theme)
    host = display_host(tab.url)
    # A page on an address (127.0.0.1, 192.168…) or localhost: its title's first letter says more than "1"
    if not host or host[0].isdigit() or host.startswith("localhost") or host.startswith("["):
        host = tab.label
    return letter_icon(host, theme.accent)


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
    # Qt only re-evaluates [prop="..."] stylesheet selectors after an unpolish/polish (and that's
    # costly: skipped when nothing's changed, as on most sidebar refreshes)
    if all(widget.property(name) == value for name, value in props.items()):
        return
    for name, value in props.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_shown(widget, shown):
    """setVisible, skipped when it's already so (each call can restyle the widget)."""
    if widget.isHidden() == bool(shown):
        widget.setVisible(bool(shown))


def spoken(text: str) -> str:
    """A tooltip as VoiceOver should say it: without the keyboard shortcut in brackets."""
    return re.sub(r"\s*\((?:[⌘⌥⇧⌃]+.*?|.*?⌘.*?)\)\s*$", "", text).strip()


PILL_PLACEHOLDER = "Search or Enter URL…"
SPACE_BUTTON = 26  # (a Space's button in the bottom bar, when there's room)
SPACE_BUTTON_SMALLEST = 18  # (with many Spaces in a narrow sidebar: fewer show, rather than smaller)
SPACE_MORE = 10  # (the arrows beside them that say some are hidden, and where)

class SpokenTooltips(QObject):
    """Keeps an icon-only button's name for VoiceOver in step with its tooltip."""

    def watch(self, button):
        button.installEventFilter(self)
        self._sync(button)

    def _sync(self, button):
        if not button.text():
            button.setAccessibleName(spoken(button.toolTip()))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTipChange and isinstance(obj, QToolButton):
            self._sync(obj)
        return False


class HoverFade:
    """Mixin: the hover highlight fades in quickly and out a touch slower, as in Arc, instead of snapping
    on and off (a stylesheet :hover can only snap). Rows paint it over themselves (they're see-through);
    buttons (_hover_under) paint it under their icon, so the icon stays crisp."""

    _hover = 0.0
    _hover_radius = 9
    _hover_under = False

    def _fade_hover(self, target, ms):
        from . import motion

        def step(value):
            self._hover = value
            self.update()
        motion.animate(self, self._hover, target, step, ms)

    def enterEvent(self, event):
        self._fade_hover(1.0, 90)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._fade_hover(0.0, 160)
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._hover_under:
            self.paint_hover()
            super().paintEvent(event)
        else:
            super().paintEvent(event)  # (its stylesheet: the selected highlight, if it's selected)
            self.paint_hover()

    def paint_hover(self, painter=None):
        if self._hover <= 0.001 or self.property("selected") is True or not self.isEnabled():
            return
        from .theme import hover_color
        theme = getattr(getattr(self, "browser", None), "theme", None) or getattr(self.window(), "theme", None)
        if theme is None:
            return
        color = hover_color(theme)
        color.setAlphaF(color.alphaF() * self._hover)
        p = painter or QPainter(self)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(self.rect()), self._hover_radius, self._hover_radius)
        p.restore()
        if painter is None:
            p.end()


LIGHTS_GAP = 16          # after the traffic lights, before the first button
LIGHTS_ROW_SPACING = 8   # between the top row's buttons


class IconButton(HoverFade, QToolButton):
    _hover_radius, _hover_under = 6, True

    def __init__(self, icon_name, tooltip="", size=26, icon_size=16, parent=None):
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.icon_name = icon_name
        self.setToolTip(tooltip)
        self.setAccessibleName(spoken(tooltip))  # (an icon alone says nothing to VoiceOver)
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
        # Page and feed titles are text: "<img>: The Image Embed element" mustn't be drawn as HTML
        self.setTextFormat(Qt.TextFormat.PlainText)
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


def dragged_together(widget) -> list:
    """The picked tabs, in sidebar order, when the drag starts on one of them (else [])."""
    sidebar = widget.browser.sidebar
    tab = getattr(widget, "tab", None)
    if not isinstance(widget, TabRow) or len(sidebar.marked) < 2 or not any(t is tab for t in sidebar.marked):
        return []
    marked = {id(t) for t in sidebar.marked}
    return [r for r in sidebar.pinned.rows + sidebar.today.rows
            if id(r.tab) in marked and not getattr(r, "_pb_gone", False)]


def stack_picture(row, count):
    """What several dragged tabs look like: the row you grabbed on two cards behind it, and a count."""
    face = row.grab()
    ratio = face.devicePixelRatio()
    w, h = face.width() / ratio, face.height() / ratio
    pixmap = QPixmap(int((w + 8) * ratio), int((h + 8) * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    theme = row.browser.theme
    p.setPen(Qt.PenStyle.NoPen)
    for offset, alpha in ((8, 0.10), (4, 0.18)):
        card = QColor(theme.text)
        card.setAlphaF(alpha)
        p.setBrush(card)
        p.drawRoundedRect(QRectF(offset, offset, w, h), 9, 9)
    p.drawPixmap(QPointF(0, 0), face)
    badge = QRectF(w - 30, 7, 24, 20)
    p.setBrush(QColor(theme.accent))
    p.drawRoundedRect(badge, 10, 10)
    p.setPen(QColor(theme.on_accent))
    font = p.font()
    font.setPixelSize(12)
    font.setBold(True)
    p.setFont(font)
    p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(count))
    p.end()
    return pixmap


def start_drag(widget, node_id, press_pos):
    together = dragged_together(widget)
    widget.browser.tab_preview.dismiss()
    mime = QMimeData()
    mime.setData(TAB_MIME, node_id.encode())
    if together:
        mime.setData(TABS_MIME, ",".join(r.tab.id for r in together).encode())
    drag = QDrag(widget)
    drag.setMimeData(mime)
    drag.setPixmap(stack_picture(widget, len(together)) if together else widget.grab())
    drag.setHotSpot(press_pos)
    split_drop = widget.browser.content.split_drop
    sidebar = widget.browser.sidebar
    lifted = []
    # Whatever happens, the drag's clean-up happens: left up, the page area's drop zone would take every click
    # meant for the page, and the sidebar would wait for ever to rebuild (folders wouldn't open), until a restart
    try:
        split_drop.activate(True)  # the page area accepts tabs too: drop one there to split
        lifted = [(r, r.parentWidget()) for r in together or [widget] if not sip.isdeleted(r)]
        for row, section in lifted:
            if isinstance(section, TabSection):
                # (once the drag has its picture of the row)
                QTimer.singleShot(0, lambda s=section, r=row: not sip.isdeleted(r) and s.lift(r))
        sidebar.drag_active = True  # rebuilds wait till it's over (they'd delete the row being dragged)
        drag.exec(Qt.DropAction.MoveAction)
    finally:
        sidebar.drag_active = False
        split_drop.activate(False)
    if getattr(sidebar, "_rebuild_after_drag", False):
        sidebar._rebuild_after_drag = False
        QTimer.singleShot(0, sidebar.rebuild)
    if isinstance(widget, FavoriteTile) and not sip.isdeleted(widget) and widget.press != 1.0:
        from PyQt6.QtCore import QEasingCurve
        widget._squeeze(1.0, 220, QEasingCurve.Type.OutBack)  # (the release went to the drag, not the tile)
    for row, section in lifted:
        if isinstance(section, TabSection):
            # After the drop has had its chance to rebuild the list (it moves the tab a pass later)
            QTimer.singleShot(120, lambda s=section, r=row: not sip.isdeleted(s) and s.drag_ended(r))


class DraggableTab:
    # Mixin: a press activates the tab, dragging past the threshold starts a drag
    def _init_drag(self, browser, tab):
        self.browser = browser
        self.tab = tab
        self._press_pos = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            command, shift = (event.modifiers() & m for m in (Qt.KeyboardModifier.ControlModifier,
                                                               Qt.KeyboardModifier.ShiftModifier))
            if isinstance(self, TabRow) and (command or shift):
                self.browser.sidebar.mark(self.tab, extend=bool(shift))  # ⌘-click / ⇧-click: several tabs
                return
            self._press_pos = event.position().toPoint()
            self.browser.tab_preview.dismiss()
            if dragged_together(self):
                self._pick_on_release = True  # it may be the start of dragging them all
                return
            self.browser.sidebar.clear_marks()
            self.browser.activate_tab(self.tab)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.browser.close_tab(self.tab)

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        press_pos, self._press_pos = self._press_pos, None
        self._pick_on_release = False
        start_drag(self, self.tab.id, press_pos)

    def mouseReleaseEvent(self, event):
        clicked = self._press_pos is not None  # (a drag sets it to None when it starts)
        self._press_pos = None
        if getattr(self, "_pick_on_release", False):
            self._pick_on_release = False
            if clicked:  # just a click on one of the picked tabs: go to it, and stop picking
                self.browser.sidebar.clear_marks()
                self.browser.activate_tab(self.tab)

    def contextMenuEvent(self, event):
        marked = self.browser.sidebar.marked
        if len(marked) > 1 and any(t is self.tab for t in marked):
            self.browser.show_tabs_menu(list(marked), event.globalPos())
        else:
            self.browser.show_tab_menu(self.tab, event.globalPos())


class TabRow(HoverFade, DraggableTab, QFrame):
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
        set_shown(self.away_label, self.away)
        self.icon_label.setToolTip("Back to the pinned page" if self.away else "")
        self.icon_label.setCursor(Qt.CursorShape.PointingHandCursor if self.away else Qt.CursorShape.ArrowCursor)
        shown = shown_title(self.tab, self.browser.store.settings)
        self.title_label.set_full_text(shown)
        sleeping = self.browser.is_sleeping(self.tab)
        # The preview says it all, except on the tab you're on, which has none
        previews = (self.browser.store.settings.get("tab_previews", True)
                    and self.tab is not getattr(self.browser, "current_tab", None))
        whole = self.tab.label + "\n" if shown != self.tab.label else ""  # (the title, if it's been tidied)
        self.title_label.setToolTip("" if previews else whole + self.tab.url
                                    + ("\nSleeping — click to wake" if sleeping else "")
                                    + ("\nKept awake" if self.tab.awake else ""))
        repolish(self.title_label, unloaded=(self.kind == PINNED and self.tab.view is None) or sleeping,
                 fresh=self.tab.fresh)
        self.close_btn.set_color(theme.text)
        badge = self.tab.unread or self.tab.app_unread or ("•" if self.tab.notified else "")
        self.unread_label.setText(badge)
        set_shown(self.unread_label, badge)
        audible, muted = self.browser.tab_audio(self.tab)
        # What VoiceOver says for the row: its title, and anything its badges show
        extras = [f"{badge} unread" if badge and badge != "•" else "new notification" if badge else "",
                  "muted" if muted else "playing sound" if audible else "", "sleeping" if sleeping else ""]
        self.setAccessibleName(", ".join([self.tab.label] + [e for e in extras if e]))
        self.close_btn.setAccessibleName(("Unload " if self.kind == PINNED else "Close ") + self.tab.label)
        set_shown(self.audio_btn, audible or muted)
        if audible or muted:
            self.audio_btn.icon_name = "ph.speaker-slash" if muted else "ph.speaker-high"
            self.audio_btn.setToolTip("Unmute Tab" if muted else "Mute Tab")
            self.audio_btn.setAccessibleName(self.audio_btn.toolTip())
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
        set_shown(self.close_btn, visible)

    def enterEvent(self, event):
        self._update_close()
        self.browser.tab_preview.hover(self)
        super().enterEvent(event)  # (HoverFade)

    def leaveEvent(self, event):
        self._update_close()
        self.browser.tab_preview.leave(self)
        super().leaveEvent(event)


class FolderRow(HoverFade, QFrame):
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
            self.icon_label.setPixmap(qta.icon(icon, color=self.browser.theme.accent_text).pixmap(16, 16))
        elif self._github_section():  # a section of the GitHub folder, or one of your repos
            icon = GITHUB_SECTION_ICONS.get(self.folder.name, "ph.book-bookmark")
            self.icon_label.setPixmap(qta.icon(icon, color=color).pixmap(16, 16))
        else:
            icon = "ph.folder-open" if self.folder.expanded else "ph.folder"
            self.icon_label.setPixmap(qta.icon(icon, color=color).pixmap(16, 16))
        self.name_label.set_full_text(self.folder.name)
        self.setAccessibleName(f"{self.folder.name}, folder, {'open' if self.folder.expanded else 'closed'}")
        fresh = sum(1 for t in walk_tabs(self.folder.children) if t.fresh)
        # Only a Live Folder's new items get a count, like Arc; how many tabs a folder holds is in its tooltip
        self.count_label.setText(f"{fresh} new" if fresh else "")
        count = sum(1 for _ in walk_tabs(self.folder.children))
        self.setToolTip(f"{count} tab{'s' if count != 1 else ''}" if not self.folder.expanded else "")

    def _github_section(self) -> bool:
        parent = self.browser.store.live_folder_of(self.folder)
        return parent is not None and parent.live.get("kind") == "github"

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        press_pos, self._press_pos = self._press_pos, None
        if self.browser.store.live_folder_of(self.folder):
            return  # (a Live Folder's sub-folder stays where its feed puts it)
        start_drag(self, self.folder.id, press_pos)

    def mouseReleaseEvent(self, event):
        # A click (press + release without dragging) opens or closes the folder
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            self._press_pos = None
            self.browser.toggle_folder(self.folder)

    def contextMenuEvent(self, event):
        self.browser.show_folder_menu(self.folder, event.globalPos())


class FavoriteTile(HoverFade, DraggableTab, QToolButton):
    _hover_radius = 10

    def __init__(self, browser, tab, parent=None):
        super().__init__(parent)
        self._init_drag(browser, tab)
        self.setObjectName("FavoriteTile")
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(20, 20))
        self.press = 1.0  # the icon's size while pressed: a little smaller, springing back on release
        tab.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.setIcon(tab_icon(self.tab, self.browser.theme))
        self.setToolTip(plain_tip(self.tab.label))
        self.setAccessibleName(self.tab.label)
        self.update()  # the unread badge

    def _squeeze(self, size, ms, easing):
        from . import motion

        def step(v):
            self.press = v
            self.update()
        motion.animate(self, self.press, size, step, ms, easing=easing)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            from PyQt6.QtCore import QEasingCurve
            self._squeeze(0.84, 90, QEasingCurve.Type.OutQuad)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        from PyQt6.QtCore import QEasingCurve
        self._squeeze(1.0, 260, QEasingCurve.Type.OutBack)  # (a small overshoot: it springs)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self.press != 1.0:  # (dragged away instead of clicked)
            from PyQt6.QtCore import QEasingCurve
            self._squeeze(1.0, 200, QEasingCurve.Type.OutCubic)
        super().leaveEvent(event)

    def set_selected(self, selected):
        if (self.property("selected") is True) != selected:
            repolish(self, selected=selected)

    def paintEvent(self, event):
        # The tile as styled, then its icon drawn by hand: scaled smoothly while pressed (Qt's own
        # icon sizes are whole points, too coarse for a 3-point squeeze)
        from PyQt6.QtWidgets import QStyle, QStyleOptionToolButton
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        icon = self.icon()
        option.icon = QIcon()
        painter = QPainter(self)
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option, painter, self)
        self.paint_hover(painter)  # (over the tile, under the icon, fading in and out)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = 20 * self.press
        center = QRectF(self.rect()).center()
        target = QRectF(center.x() - size / 2, center.y() - size / 2, size, size)
        ratio = self.devicePixelRatioF()
        picture = icon.pixmap(QSize(20, 20), ratio)
        painter.drawPixmap(target, picture, QRectF(picture.rect()))  # (source: the picture's own pixels)
        painter.end()
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
            p.setPen(QColor(self.browser.theme.on_accent))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


def dragged_tabs(browser, event) -> list:
    """The tabs in a drag of several picked tabs (else [])."""
    data = event.mimeData()
    if not data.hasFormat(TABS_MIME):
        return []
    found = (browser.store.find_tab(i) for i in bytes(data.data(TABS_MIME)).decode().split(","))
    return [t for t in found if t is not None]


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
        store = self.browser.store
        # Not into a Live Folder (its feed fills it), nor one of its sub-folders out of it: the store would
        # refuse, and the tab would fold back with nothing said
        if folder is not None and (folder.live or store.live_folder_of(folder)):
            return None, None
        if isinstance(node, Folder) and store.live_folder_of(node):
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
        together = dragged_tabs(self.browser, event)
        # Defer so the drag's source widget isn't rebuilt while still inside drag.exec()
        if together:
            QTimer.singleShot(0, lambda: self.browser.move_tabs(together, self.kind, index, folder))
        else:
            QTimer.singleShot(0, lambda: self.browser.move_tab(node, self.kind, index, folder))


def _runs(indices) -> int:
    """How many separate runs of neighbouring positions: [3, 4, 5, 9] is two."""
    indices = sorted(indices)
    return sum(1 for n, i in enumerate(indices) if n == 0 or i != indices[n - 1] + 1)


class RowReveal(QWidget):
    """A row growing into the list (a new tab, a folder opening) or shrinking out of it (closed,
    archived, picked up to drag): the row keeps its full size inside, clipped, while this box's height
    changes, so the rows below glide instead of jumping. Fades as it goes; a row leaving also drifts
    down a little, the way Arc's Clear sweeps tabs away. `keep`: stay folded up when done (a row
    being dragged), ready to unfold again if it isn't dropped anywhere.

    Several rows that come or go together (a folder's tabs, as it opens or closes) move as one block, in one
    box: one height changing a frame, not one per row (which made a folder of tabs judder open)."""

    MS = 190

    def __init__(self, section, row, growing, delay=0, keep=False):
        super().__init__(section)
        self.rows = list(row) if isinstance(row, (list, tuple)) else [row]
        self.row = self.rows[0]
        self.section, self.keep = section, keep
        # Rows of a block sit as far apart as the list puts them (its spacing between them too), so when they go
        # back into it at the end nothing moves: without it they were a few pixels short, and jumped into place
        self.gap = section.layout_.spacing() if len(self.rows) > 1 else 0
        self.full = sum(r.height() or 34 for r in self.rows) + self.gap * (len(self.rows) - 1)
        self.shown = 0.0 if growing else 1.0
        self.growing = growing
        y = 0
        for each in self.rows:
            each.setParent(self)
            each.move(0, y)
            each.show()
            y += (each.height() or 34) + self.gap
        self.fade = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fade)
        self._size(self.shown)
        target = 1.0 if growing else 0.0
        # The clock starts once this pass is over (the list laid out, its first frame drawn): started at once, the
        # time that takes was already "spent", and the first frame drawn was half-way, a leap
        QTimer.singleShot(delay, lambda: not sip.isdeleted(self) and self._go(target))

    def _go(self, target):
        from . import motion
        self.growing = target > self.shown
        ms = max(1, round(self.MS * abs(target - self.shown)))
        motion.animate(self, self.shown, target, self._set, ms, self._done)

    def _size(self, shown):
        """The room this box takes in the list, `shown` of the way open: its height and the list's gap after it,
        together. A box with no height still has that gap, so it's out of the list (hidden) for the last
        point instead: closing, the rows below used to jump up those 2 points as the box came out, after
        everything else had come to rest."""
        spacing = self.section.layout_.spacing()
        room = (self.full + spacing) * shown  # (what it should take, gap and all)
        height = room - spacing
        if room < spacing / 2:  # (nearer nothing than a gap: nothing, never more than a point out)
            self.setFixedHeight(0)
            self.hide()
        else:
            self.setFixedHeight(max(0, round(height)))
            if self.isHidden():
                self.show()
        self.fade.setOpacity(shown)

    def _set(self, shown):
        if any(sip.isdeleted(r) for r in self.rows):
            return
        self.shown = shown
        self._size(shown)
        # (one row leaving drifts down a little as it goes, like Arc's Clear; a folder's rows, going together, don't:
        # moving down while the rows below came up looked like a wobble)
        y, drift = 0, 0 if self.growing or len(self.rows) > 1 else round(6 * (1 - shown))
        for each in self.rows:
            each.move(0, y + drift)
            y += (each.height() or 34) + self.gap

    def unfold(self):
        """A dragged row that wasn't dropped anywhere: back where it was."""
        self.keep = False
        self._go(1.0)

    def resizeEvent(self, event):
        for each in self.rows:
            if not sip.isdeleted(each):
                each.resize(self.width(), each.height() or 34)

    def _done(self):
        if self.keep and self.shown == 0:
            return  # (folded up out of the way until the drag ends)
        self.section._reveal_done(self)

    def settle(self):
        """Jump to the end now (the list is being rebuilt again)."""
        animation = getattr(self, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        self.keep = False
        self.section._reveal_done(self)


class DropGap(QWidget):
    """Room opening up where a dragged tab will land, the rows either side parting for it: an empty,
    faintly drawn slot, indented to the folder depth it'll land at."""

    MS = 150

    def __init__(self, section, indent):
        super().__init__(section)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.section = section
        self.indent = indent
        self.setFixedHeight(0)

    def open(self):
        self._go(1.0)

    def close(self, done=None):
        self._go(0.0, done)

    def _go(self, target, done=None):
        from . import motion
        start = self.height() / 34
        motion.animate(self, start, target, self._set, max(1, round(self.MS * abs(target - start))), done)

    def _set(self, value):
        self.setFixedHeight(round(34 * value))
        self.update()

    def paintEvent(self, event):
        if self.height() < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.section.browser.theme.text
        slot = QRectF(self.indent + 0.5, 1.5, self.width() - self.indent - 7, self.height() - 3.5)
        p.setOpacity(self.height() / 34)
        p.setPen(QPen(_with_alpha(text, 0.22), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(_with_alpha(text, 0.06))
        p.drawRoundedRect(slot, 9, 9)
        p.end()


class TabSection(DropZone):
    def __init__(self, browser, kind, empty_hint="", parent=None):
        super().__init__(browser, kind, parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(2)
        # Rows packed at the top: while a folder closes, the list is for a moment taller than its rows (its own
        # height catches up a frame later), and spread over that room the rows bounced up and down
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.nodes: list = []
        self.entries: list[tuple] = []  # (row widget, node, parent folder, depth), in display order
        self.hint = QLabel(empty_hint)
        self.hint.setProperty("dim", True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFixedHeight(30)
        self.layout_.addWidget(self.hint)
        self.animate_changes = False  # set by the sidebar: only for changes within one Space
        self._reveals = []
        self._gap = None  # where a dragged tab would land (opening), with any older ones closing
        self._gaps = []
        self.dragging = []  # the nodes being dragged out of this list, folded up while they are
        self.just_dropped = None  # the node dropped here, which takes its gap's place as the list rebuilds

    @property
    def rows(self) -> list[TabRow]:
        return [row for row, *_ in self.entries if isinstance(row, TabRow)]

    def set_tabs(self, nodes, refresh=True):
        """The list's rows for these tabs and folders. refresh=False: the rows still here are left as they are
        (opening a folder changes nothing about them)."""
        for reveal in list(self._reveals):
            reveal.settle()
        self._remove_gaps()  # (a dropped tab takes its gap's place in this same pass: nothing jumps)
        self.dragging = []
        dropped, self.just_dropped = self.just_dropped, None
        old = [e for e in self.entries if not sip.isdeleted(e[0]) and not getattr(e[0], "_pb_gone", False)]
        # Rows for tabs and folders that are still here are kept (refreshed, and put back in order)
        # rather than built again: cheaper, and nothing about them flickers
        reusable = {id(node): (row, depth) for row, node, _parent, depth in old}
        self.nodes = nodes
        self.entries = []
        self._add_rows(nodes, None, 0, reusable, refresh)
        reused = {id(row) for row, *_ in self.entries}
        listed = {id(row) for row, *_ in self.entries}
        for row, *_ in old:  # (rows going: out of the order first, so the ones staying keep their places)
            if id(row) not in listed and not sip.isdeleted(row):
                self.layout_.removeWidget(row)
        # Into order, moving only the rows not already where they belong (taking every row out and putting it
        # back was most of the time it took a folder to open)
        first = self.layout_.indexOf(self.hint) + 1
        for i, (row, *_) in enumerate(self.entries):
            if self.layout_.indexOf(row) != first + i:
                self.layout_.removeWidget(row)
                self.layout_.insertWidget(first + i, row)
            if row.isHidden() and self.isVisible():
                row.show()  # now, in the same pass the old rows went (a layout would only show it on the next)
        kept = {id(node) for _, node, *_ in self.entries}
        before = {id(node) for _, node, *_ in old}
        # (a tab just dropped here appears in its gap at once, rather than growing into it)
        added = [row for row, node, *_ in self.entries if id(node) not in before and node is not dropped]
        gone = [(i, row) for i, (row, node, *_) in enumerate(old) if id(node) not in kept]
        from . import motion
        # (a big change, many rows in many places, just shows; a folder of any size opening or closing is one
        # block, and glides)
        animate = (self.animate_changes and old and not motion.reduced() and self.isVisible()
                   and _runs([self.layout_.indexOf(r) for r in added]) <= 8 and len(added) <= 60
                   and (_runs([i for i, _ in gone]) <= 8 or not self.entries) and len(gone) <= 60)
        for i, (row, node, *_) in enumerate(old):
            if id(row) in reused or (animate and (i, row) in gone):
                continue  # still in the list, or shrinks away below
            # Out of sight and out of the layout now: deleteLater only happens on a later pass, and
            # until then the old rows would still show (and take up room) under the new ones
            row.hide()
            self.layout_.removeWidget(row)
            row.deleteLater()
        if animate:
            self._animate(old, kept, added, gone)
        self.hint.setVisible(not nodes and bool(self.hint.text()) and not (animate and gone))
        self.setMinimumHeight(0 if nodes or self.hint.text() else 12)
        self._lay_out_now()

    def _lay_out_now(self):
        """Laid out now, before anything's drawn, this list and then what holds it: otherwise, for a frame, a
        new box (a folder's rows on their way) sat at the list's top-left, and the list was sized from how it
        was a moment before (a closing folder's final height, its rows still all there), everything in it
        squeezed on top of each other."""
        self.layout_.invalidate()
        self.updateGeometry()
        holder = self.parentWidget()
        if holder is not None and holder.layout() is not None:
            holder.layout().invalidate()
            holder.layout().activate()
        self.layout_.activate()

    def _animate(self, old, kept, added, gone):
        # Rows next to each other in the list grow in together, as one block (a folder's tabs as it opens)
        blocks = []
        for row in added:
            index = self.layout_.indexOf(row)
            if blocks and blocks[-1][0] + len(blocks[-1][1]) == index:
                blocks[-1][1].append(row)
            else:
                blocks.append((index, [row]))
        for index, rows in reversed(blocks):  # (from the bottom up: the indices above stay true)
            for row in rows:
                self.layout_.removeWidget(row)
            reveal = RowReveal(self, rows, growing=True)
            self.layout_.insertWidget(index, reveal)
            self._reveals.append(reveal)
        # A row that's gone shrinks where it was: after the nearest row above it that's still here
        position = {id(node): row for row, node, *_ in self.entries}
        clearing = not self.entries  # everything going at once (Clear): a quick sweep, top to bottom
        inserted_after = {}
        # Rows that were next to each other and go together shrink away as one block (a folder closing); Clear's
        # sweep keeps them one at a time
        groups = []
        for i, row in gone:
            if not clearing and groups and groups[-1][0] + len(groups[-1][1]) == i:
                groups[-1][1].append(row)
            else:
                groups.append((i, [row]))
        for n, (i, rows) in enumerate(groups):
            anchor = next((position[id(old[k][1])] for k in range(i - 1, -1, -1) if id(old[k][1]) in kept), None)
            for row in rows:
                self.layout_.removeWidget(row)
            base = self.layout_.indexOf(anchor) + 1 if anchor is not None else 1  # (1: after the hint)
            offset = inserted_after.get(id(anchor), 0)
            inserted_after[id(anchor)] = offset + 1
            reveal = RowReveal(self, rows, growing=False, delay=n * 22 if clearing else 0)
            self.layout_.insertWidget(base + offset, reveal)
            self._reveals.append(reveal)

    def _reveal_done(self, reveal):
        if reveal not in self._reveals:
            return
        self._reveals.remove(reveal)
        index = self.layout_.indexOf(reveal)
        # The box out of the list first, then its rows in its place, and the list laid out now: the other way
        # round, for a frame both took up room and everything below dropped a block's height and came back
        reveal.hide()
        self.layout_.removeWidget(reveal)
        listed = {id(r) for r, *_ in self.entries}
        for row in reveal.rows:
            if sip.isdeleted(row):
                continue
            if reveal.growing and id(row) in listed:
                row.setParent(self)
                self.layout_.insertWidget(index, row)  # back into the list where it grew in
                index += 1
                row.show()
            else:
                row.hide()
                row._pb_gone = True  # (deleted on a later pass: set_tabs mustn't count it as still here)
                row.deleteLater()
        self.layout_.activate()
        reveal.setGraphicsEffect(None)
        reveal.deleteLater()
        if not self._reveals:
            self.hint.setVisible(not self.nodes and bool(self.hint.text()))

    def _add_rows(self, nodes, parent, depth, reusable=None, refresh=True):
        for node in nodes:
            kind = FolderRow if isinstance(node, Folder) else TabRow
            row, was_depth = (reusable or {}).get(id(node), (None, None))
            if row is not None and isinstance(row, kind) and was_depth == depth and row.parentWidget() is self:
                if refresh or isinstance(row, FolderRow):  # (a folder's own row shows it open or closed)
                    row.refresh()
            elif kind is FolderRow:
                row = FolderRow(self.browser, node, depth, self)
            else:
                row = TabRow(self.browser, node, self.kind, depth, self)
            self.entries.append((row, node, parent, depth))
            if isinstance(node, Folder) and node.expanded:
                self._add_rows(node.children, node, depth + 1, reusable, refresh)

    def _line(self, y, depth):
        x = 6 + depth * INDENT
        return QRect(x, max(y, 0), self.width() - x - 6, 2)

    # --- Dragging: the row lifts out, and the others part where it'll land -----------------------

    def lift(self, row):
        """A row picked up to drag: it folds out of the list (and unfolds if it's let go of nowhere)."""
        entry = next((e for e in self.entries if e[0] is row), None)
        if entry is None or sip.isdeleted(row) or isinstance(row.parentWidget(), RowReveal):
            return
        from . import motion
        if motion.reduced():
            return
        self.dragging = [*self.dragging, entry[1]]
        index = self.layout_.indexOf(row)
        self.layout_.removeWidget(row)
        reveal = RowReveal(self, row, growing=False, keep=True)
        self.layout_.insertWidget(index, reveal)
        self._reveals.append(reveal)

    def drag_ended(self, row):
        """The drag is over. If nothing rebuilt this list (it wasn't moved), the row comes back."""
        self.dragging = []
        if not sip.isdeleted(row) and isinstance(row.parentWidget(), RowReveal) and row.parentWidget().keep:
            row.parentWidget().unfold()
        self._close_gaps()

    def _holder(self, row):
        parent = row.parentWidget()
        return parent if isinstance(parent, RowReveal) else row

    def _rect(self, row):
        """Where a row is in this list, even while it's in a block growing in or shrinking away."""
        holder = self._holder(row)
        if holder is row:
            return row.geometry()
        return QRect(holder.geometry().topLeft() + row.pos(), QSize(holder.width(), row.height()))

    def _places(self):
        """[(row, node, parent, depth, rect)] where each row would be with no gap open (so the target
        doesn't jump about as the gap moves the rows), leaving out the one being dragged."""
        gaps = [g for g in self._gaps if not sip.isdeleted(g)]
        out = []
        for row, node, parent, depth in self.entries:
            if any(node is n for n in self.dragging) or sip.isdeleted(row):
                continue
            rect = self._rect(row)
            above = sum(g.height() + self.layout_.spacing() * (g.height() > 0) for g in gaps
                        if g.geometry().top() < rect.top())
            out.append((row, node, parent, depth, rect.translated(0, -above)))
        return out

    def drop_target(self, pos):
        places = self._places()
        for row, node, parent, depth, g in places:
            if pos.y() > g.bottom():
                continue
            siblings = parent.children if parent else self.nodes
            i = next((k for k, n in enumerate(siblings) if n is node), None)
            if i is None:
                continue  # (gone from the store since the rows were laid out: a Live Folder refreshed mid-drag)
            if isinstance(node, Folder):
                # The middle of a folder row means "put it inside"
                third = g.height() / 3
                if g.top() + third <= pos.y() <= g.bottom() - third:
                    return node, len(node.children), self._holder(row).geometry().adjusted(1, 0, -1, 0), True
            if pos.y() < g.center().y():
                return parent, i, self._line(g.top() - 2, depth), False
            if isinstance(node, Folder) and node.expanded:
                return node, 0, self._line(g.bottom() + 1, depth + 1), False
            return parent, i + 1, self._line(g.bottom() + 1, depth), False
        if places:
            return None, len(self.nodes), self._line(places[-1][4].bottom() + 1, 0), False
        return None, 0, self._line(self.height() // 2, 0), False

    def dragMoveEvent(self, event):
        node, target = self._dragged(event)
        self._hide_indicators()
        if node is None:
            self._close_gaps()
            event.ignore()
            return
        _, _, rect, into_folder = target
        if into_folder:
            self._close_gaps()
            self.box.setGeometry(rect)
            self.box.show()
            self.box.raise_()
        else:
            # The gap goes before the first row that would sit below the line
            after = next((self._holder(row) for row, _n, _p, _d, g in self._places() if g.top() >= rect.top()), None)
            self._open_gap(after, rect.left())
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hide_indicators()
        self._close_gaps()

    def dropEvent(self, event):
        node, _target = self._dragged(event)
        self.just_dropped = node
        super().dropEvent(event)
        # The move rebuilds this list a moment later, and the tab takes the gap's place then; a drop
        # that changes nothing (it was already there) still closes it
        QTimer.singleShot(200, self._close_gaps)

    def _open_gap(self, before, indent):
        gap = self._gap
        if gap is not None and not sip.isdeleted(gap) and gap.anchor is before and gap.indent == indent:
            return
        self._close_gaps()
        gap = DropGap(self, indent)
        gap.anchor = before
        index = self.layout_.indexOf(before) if before is not None else -1
        self.layout_.insertWidget(index if index >= 0 else self.layout_.count(), gap)
        gap.show()
        gap.open()
        self._gap = gap
        self._gaps.append(gap)

    def _close_gaps(self):
        self._gap = None
        for gap in list(self._gaps):
            if not sip.isdeleted(gap):
                gap.close(lambda g=gap: self._drop_gap(g))

    def _drop_gap(self, gap):
        if gap in self._gaps:
            self._gaps.remove(gap)
        if not sip.isdeleted(gap):
            gap.hide()
            self.layout_.removeWidget(gap)
            gap.deleteLater()

    def _remove_gaps(self):
        self._gap = None
        for gap in list(self._gaps):
            self._drop_gap(gap)


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
        # The same Favorites in the same look: kept, just refreshed (making them again is most of a rebuild's time)
        self._tabs = tabs
        same = (tuple(map(id, tabs)), self.browser.theme.text.name(), self.browser.theme.accent.name(),
                self._columns_that_fit())
        if self.tiles and same == getattr(self, "_built", None) and all(not sip.isdeleted(t) for t in self.tiles):
            for tile in self.tiles:
                tile.refresh()
            return
        self._built = same
        for tile in self.tiles:
            tile.hide()  # (see TabSection.set_tabs: gone now, not on a later pass)
            self.grid.removeWidget(tile)
            tile.deleteLater()
        self.grid.removeWidget(self.hint)
        # A fresh grid each time: a QGridLayout never forgets a column it once had, so going from four
        # Favorites to three would leave an empty quarter of the row
        QWidget().setLayout(self.grid)  # (the old one goes with that throwaway widget)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.tiles = [FavoriteTile(self.browser, t, self) for t in tabs]
        # (fewer than a row's worth share the row; a narrow sidebar has fewer columns, never tiles cut off)
        columns = max(1, min(len(self.tiles), self._columns_that_fit()))
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // columns, i % columns)
            if self.isVisible():
                tile.show()  # (see TabSection.set_tabs)
        if self.tiles:
            self.hint.hide()
            for col in range(columns):  # equal widths, even when the last row isn't full
                self.grid.setColumnStretch(col, 1)
        else:
            self.grid.addWidget(self.hint, 0, 0)
            self.hint.show()

    def _columns_that_fit(self) -> int:
        """How many tiles fit side by side at this width (up to FAVORITE_COLUMNS)."""
        sidebar = self.browser.sidebar if hasattr(self.browser, "sidebar") else None
        if sidebar is None or sidebar.width() <= 0 or not self.tiles:
            return FAVORITE_COLUMNS
        margins = sidebar.layout().contentsMargins()
        room = sidebar.width() - margins.left() - margins.right()  # (not this grid's own: too many columns widen it)
        tile = max(t.minimumSizeHint().width() for t in self.tiles if not sip.isdeleted(t)) if any(
            not sip.isdeleted(t) for t in self.tiles) else 0
        if tile <= 0:
            return FAVORITE_COLUMNS
        return max(1, min(FAVORITE_COLUMNS, (room + 6) // (tile + 6)))

    def refit(self):
        """The sidebar changed width: as many columns as fit now."""
        tabs = getattr(self, "_tabs", None)
        built = getattr(self, "_built", None)
        if tabs is not None and built is not None and built[-1] != self._columns_that_fit():
            QTimer.singleShot(0, lambda: not sip.isdeleted(self) and self.set_tabs(self._tabs))

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


class NewTabRow(HoverFade, QFrame):
    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("TabRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("New Tab")
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


class ListFade(QWidget):
    """The bottom of the tab list fades into the sidebar while there's more below, rather than ending on a
    row cut in half (by the Up Next card, say). Drawn with the sidebar's own background, fading in."""
    HEIGHT = 24

    def __init__(self, sidebar, scroll):
        super().__init__(scroll)
        self.sidebar, self.scroll = sidebar, scroll
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bar = scroll.verticalScrollBar()
        bar.valueChanged.connect(self.place)
        bar.rangeChanged.connect(self.place)
        scroll.installEventFilter(self)

    def place(self, *_):
        bar = self.scroll.verticalScrollBar()
        self.setGeometry(0, self.scroll.height() - self.HEIGHT, self.scroll.width(), self.HEIGHT)
        self.setVisible(bar.value() < bar.maximum())
        self.raise_()
        overlay = getattr(self.scroll, "overlay_scrollbar", None)
        if overlay is not None:
            overlay.raise_()  # (the scrollbar stays on top of it)
        self.update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False

    def paintEvent(self, event):
        theme = self.sidebar.browser.theme
        # Docked, the background is the window's; slid out over the page, it's the sidebar's own
        floating = self.sidebar.property("floating") is True
        surface = self.sidebar if floating else self.window()
        offset = self.mapTo(surface, QPoint(0, 0))
        ratio = self.devicePixelRatioF()
        image = QImage(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.fillRect(self.rect(), theme.gradient(surface.width(), surface.height(), offset.x(), offset.y()))
        if theme.grain and not floating:
            p.drawTiledPixmap(self.rect(), noise_pixmap(), QPoint(offset.x() % 128, offset.y() % 128))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        ramp = QLinearGradient(0, 0, 0, self.height())
        ramp.setColorAt(0, QColor(0, 0, 0, 0))
        ramp.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(self.rect(), ramp)
        p.end()
        painter = QPainter(self)
        painter.drawImage(0, 0, image)
        painter.end()


class Sidebar(QWidget):
    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)  # only painted when floating
        self.setMinimumWidth(190)
        self.setMaximumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 4, 10)
        layout.setSpacing(8)

        # Top row: room for the macOS traffic lights, then the navigation buttons.
        # align_with_traffic_lights() fine-tunes this once the window exists.
        top = QHBoxLayout()
        top.setSpacing(0)
        # The room after the traffic lights never shrinks (squeezed, it put the first button over them); the
        # gaps between back, forward and reload give way instead when the sidebar is narrow
        self._lights_gap = QSpacerItem(66 if sys.platform == "darwin" else 0, 0, QSizePolicy.Policy.Fixed)
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
        overlay_scrollbar(self.scroll)
        self.list_fade = ListFade(self, self.scroll)
        layout.addWidget(self.scroll, 1)

        for card in browser.gather("sidebar_cards"):  # (Up Next, Now Playing…)
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
        for button in browser.gather("sidebar_buttons"):  # (Homebase…)
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
        """Centre the top-row buttons on the traffic lights, just to their right (like Arc). `at`: where
        the sidebar will be in the window, if that isn't where it is yet (the hidden one, about to slide out)."""
        lights = macos.traffic_lights(self)
        if lights is None:
            return
        right, centre = lights
        offset = at if at is not None else self.mapTo(self.window(), QPoint(0, 0))  # (it floats 6pt in when hidden)
        size = self.toggle_btn.height()
        layout = self.layout()
        margins = layout.contentsMargins()
        layout.setContentsMargins(margins.left(), max(round(centre - offset.y() - size / 2), 0),
                                  margins.right(), margins.bottom())
        # A little more room after the lights than between them (~9pt), minus the button's padding (on the
        # right too: they move over into the sidebar), so the row doesn't feel crammed
        gap = max(round(right + LIGHTS_GAP - offset.x() - margins.left() - 4), 0)
        self._lights_gap.changeSize(gap, 0, QSizePolicy.Policy.Fixed)
        layout.invalidate()

    def rebuild(self):
        if getattr(self, "drag_active", False):
            self._rebuild_after_drag = True  # (see start_drag)
            return
        store = self.browser.store
        space = store.active_space
        theme = self.browser.theme
        for btn in (self.toggle_btn, self.back_btn, self.forward_btn, self.reload_btn,
                    self.archive_btn, self.downloads_btn, self.new_space_btn):
            btn.set_color(theme.text)
        self.new_tab_row.set_color(theme.text)

        preview = getattr(self.browser, "tab_preview", None)
        if preview is not None:
            preview.dismiss()  # (its row may be about to go)
        self.space_title.setText(space.name)
        self.space_title.setIcon(space_icon(space, theme.text, 32))
        self.profile_badge.setText(space.profile)
        self.profile_badge.setToolTip(f"This Space uses the “{space.profile}” profile (its own logins and cookies)")
        self.profile_badge.setVisible(space.profile != "default")
        self.favorites.set_tabs(store.favorites)
        # Rows slide in and out for changes within a Space; switching Spaces just shows the new one
        same_space = getattr(self, "_built_space", None) == space.id
        if not same_space:
            self.marked = []  # (a selection belongs to the Space it was made in)
        self._built_space = space.id
        self.pinned.animate_changes = self.today.animate_changes = same_space
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
        claude = self.browser.feature("claude")  # (Tidy is Claude's: no Claude, no Tidy)
        loose = sum(1 for n in self.browser.store.active_space.today if isinstance(n, Tab))
        self.tidy_btn.setVisible(claude is not None and (self._tidy_state != "idle" or claude.can_tidy(loose)))

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
            btn = QToolButton()
            btn.setObjectName("SpaceDot")
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
        return [r.tab for r in self.pinned.rows + self.today.rows if not getattr(r, "_pb_gone", False)]

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
            buttons = sum(b.sizeHint().width() + 4 for b in (*self.feature_chips, self.dev_btn, self.zoom_btn, self.shield_btn)
                          if b.isVisibleTo(self))
            return self.url_pill.width() - self.url_pill.iconSize().width() - 34 - buttons
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
        elide = Qt.TextElideMode.ElideLeft if host else Qt.TextElideMode.ElideRight
        self.url_pill.setText(self.url_pill.fontMetrics().elidedText(text, elide, max(room, 20)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_pill_text()
        self._fit_switcher()
        self.favorites.refit()

    def _fit_switcher(self):
        """The Space buttons share what room the bottom bar has: full size when there's room, smaller and closer
        together (to two-thirds of it) when there are many Spaces or the sidebar's narrow, never on top of each
        other. With too many to fit even so, the ones round the Space you're in show."""
        buttons = [b for b in self.space_buttons if not sip.isdeleted(b)]
        if not buttons:
            return
        margins = self.layout().contentsMargins()
        others = [b for b in self._bottom_buttons if not sip.isdeleted(b) and b.isVisibleTo(self)]
        room = self.width() - margins.left() - margins.right() - sum(b.sizeHint().width() for b in others) \
            - 2 * (len(others) + 1)
        crowded = room < len(buttons) * (SPACE_BUTTON + 2)
        self.switcher.setSpacing(1 if crowded else 2)
        fit = max(1, room // (SPACE_BUTTON_SMALLEST + 1))  # (how many fit at the smallest)
        if fit < len(buttons):  # (some will be hidden: room for the arrows that say so)
            room -= 2 * (SPACE_MORE + 2)
            fit = max(1, room // (SPACE_BUTTON_SMALLEST + 1))
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
