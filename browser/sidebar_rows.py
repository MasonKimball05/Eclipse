# The sidebar's rows: a tab, a folder, a Favorite's tile, a Space's dot, New Tab, and dragging them.
# (sidebar.py re-exports all of it, for whatever imports it from there.)

import qtawesome as qta
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QMimeData, QObject, QPointF, QRect, QRectF, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QDrag, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QToolTip

from .models import PINNED, TODAY, display_host, walk_tabs
from .sidebar_common import (GITHUB_SECTION_ICONS, HoverFade, INDENT, ROW_BUTTON, ROW_BUTTON_ICON, ROW_HEIGHT,
                             ROW_SPACING, TABS_MIME, TAB_MIME, WAKE_AHEAD_MS, _row_icon, _with_alpha, compact,
                             home_icon, is_away, left_its_site, repolish, tab_icon)
from .tab_titles import shown_title
from .theme import plain_tip


def dragged_together(widget) -> list:
    """The picked tabs, in sidebar order, when the drag starts on one of them (else [])."""
    sidebar = widget.browser.sidebar
    tab = getattr(widget, "tab", None)
    if not isinstance(widget, TabRow) or len(sidebar.marked) < 2 or not any(t is tab for t in sidebar.marked):
        return []
    marked = {id(t) for t in sidebar.marked}
    return [r for r in sidebar.qt_rows()
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
    from .sidebar_sections import TabSection  # (imports this one: so not at the top)
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
    """A tab in the sidebar. One widget that draws its own icon, title, unread badge and buttons: a row made
    of seven widgets (labels, buttons) made every restyle and every Space switch slower with each tab."""

    def __init__(self, browser, tab, kind, depth=0, parent=None):
        super().__init__(parent)
        self._init_drag(browser, tab)
        self.kind = kind
        self.depth = depth
        self.setObjectName("TabRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)  # (which of its buttons the pointer is over)
        self.setFixedHeight(ROW_HEIGHT)
        self.shows_away = False
        self.icon_picture = QPixmap()
        self._title = self._badge = self._title_tip = ""
        self._unloaded = self._fresh = self._audible = self._muted = False
        self._show_close = False
        self._part = None      # the button (or away icon) under the pointer: "close", "audio" or "home"
        self._pressed = None   # the one pressed, till the release
        self._part_hover = 0.0
        self._part_fader = QObject(self)  # (its own animation, apart from the row's hover fade)
        self._elided = None
        self.parts = {}
        tab.changed.connect(self.refresh)
        self.refresh()

    @property
    def selected(self):
        return self.property("selected") is True

    @property
    def away(self) -> bool:
        return self.kind != TODAY and is_away(self.tab)

    def refresh(self):
        self.shows_away = self.away
        self._paint_icon()
        shown = shown_title(self.tab, self.browser.store.settings)
        sleeping = self.browser.is_sleeping(self.tab)
        # The preview says it all, except on the tab you're on, which has none
        previews = (self.browser.store.settings.get("tab_previews", True)
                    and self.tab is not getattr(self.browser, "current_tab", None))
        whole = self.tab.label + "\n" if shown != self.tab.label else ""  # (the title, if it's been tidied)
        self._title_tip = "" if previews else (whole + self.tab.url + ("\nSleeping — click to wake" if sleeping else "")
                                               + ("\nKept awake" if self.tab.awake else ""))
        self._title = shown
        self._unloaded = (self.kind == PINNED and self.tab.view is None) or sleeping
        self._fresh = bool(self.tab.fresh)
        self._badge = self.tab.unread or self.tab.app_unread or ("•" if self.tab.notified else "")
        self._audible, self._muted = self.browser.tab_audio(self.tab)
        # What VoiceOver says for the row: its title, and anything its badges show
        badge = self._badge
        extras = [f"{badge} unread" if badge and badge != "•" else "new notification" if badge else "",
                  "muted" if self._muted else "playing sound" if self._audible else "", "sleeping" if sleeping else ""]
        self.setAccessibleName(", ".join([self.tab.label] + [e for e in extras if e]))
        # Its × and speaker are drawn, not buttons VoiceOver can reach on their own: their actions are in the row's
        # menu (VoiceOver's VO-Shift-M opens it, as a right-click does), which this says
        can = ["unload it" if self.kind == PINNED else "close it"]
        if self._audible or self._muted:
            can.append("unmute it" if self._muted else "mute it")
        if self.shows_away:
            can.append("take it back to its pinned page")
        self.setAccessibleDescription("Its menu can " + ", ".join(can[:-1]) + (" or " if len(can) > 1 else "") + can[-1])
        self._update_close()

    def _paint_icon(self):
        theme = self.browser.theme
        if self._part == "home" and self.shows_away:  # (pointing at it: back to the pinned page)
            icon = _row_icon("ph.arrow-u-up-left", theme.text)
        else:
            icon = tab_icon(self.tab, theme)
        self.icon_picture = icon.pixmap(16, 16)
        self.update()

    def _fonts(self):
        title = QFont(self.font())
        title.setPixelSize(13)
        title.setWeight(QFont.Weight.Bold if self._fresh else QFont.Weight.Medium)
        small = QFont(self.font())
        small.setPixelSize(11)
        badge = QFont(self.font())
        badge.setPixelSize(10)
        badge.setWeight(QFont.Weight.Bold)
        return title, small, badge

    def _lay_out(self):
        """Where everything goes, as the old row's layout put it: icon, "/", title, then the badge and buttons
        on the right (a hidden one takes no room)."""
        _, small, badge_font = self._fonts()
        mid = ROW_HEIGHT // 2
        if compact(self.browser):  # (the collapsed sidebar: the icon alone, in the middle; its preview says the rest)
            self.parts = {"icon": QRect(self.width() // 2 - 8, mid - 8, 16, 16)}
            return
        x = 10 + self.depth * INDENT
        parts = {"icon": QRect(x, mid - 8, 16, 16)}
        x += 16 + ROW_SPACING
        if self.shows_away:
            width = QFontMetrics(small).horizontalAdvance("/")
            parts["slash"] = QRect(x, 0, width, ROW_HEIGHT)
            x += width + ROW_SPACING
        right = self.width() - 5
        for name, shown in (("close", self._show_close), ("audio", self._audible or self._muted)):
            if shown:
                parts[name] = QRect(right - ROW_BUTTON, mid - ROW_BUTTON // 2, ROW_BUTTON, ROW_BUTTON)
                right -= ROW_BUTTON + ROW_SPACING
        if self._badge:
            width = max(16, QFontMetrics(badge_font).horizontalAdvance(self._badge) + 10)
            parts["badge"] = QRect(right - width, mid - 8, width, 16)
            right -= width + ROW_SPACING
        parts["title"] = QRect(x, 0, max(10, right - x), ROW_HEIGHT)
        self.parts = parts

    def _part_at(self, pos):
        for name in ("close", "audio"):
            if name in self.parts and self.parts[name].contains(pos):
                return name
        if self.shows_away and self.parts.get("icon", QRect()).contains(pos):
            return "home"
        return None

    def _set_part(self, part):
        if part == self._part:
            return
        self._part = part
        self.setCursor(Qt.CursorShape.PointingHandCursor if part else Qt.CursorShape.ArrowCursor)
        if self.shows_away:
            self._paint_icon()
        from . import motion

        def step(value):
            self._part_hover = value
            self.update()
        self._part_hover = 0.0 if part else self._part_hover
        motion.animate(self._part_fader, self._part_hover, 1.0 if part in ("close", "audio") else 0.0, step, 90)

    def paintEvent(self, event):
        super().paintEvent(event)  # (its stylesheet's highlight, and the hover fading over it)
        theme = self.browser.theme
        title_font, small, badge_font = self._fonts()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        parts = self.parts
        p.drawPixmap(parts["icon"], self.icon_picture)
        if "title" not in parts:  # (collapsed: an unread badge is a dot on the icon's corner)
            if self._badge:
                corner = parts["icon"].topRight()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.accent))
                p.drawEllipse(QRectF(corner.x() - 3, corner.y() - 3, 7, 7))
            p.end()
            return
        if "slash" in parts:
            p.setFont(small)
            p.setPen(_with_alpha(QColor(theme.text), 0.55))
            p.drawText(parts["slash"], Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "/")
        title = parts["title"]
        key = (self._title, title.width(), self._fresh)
        if self._elided is None or self._elided[0] != key:
            text = QFontMetrics(title_font).elidedText(self._title, Qt.TextElideMode.ElideRight, title.width())
            self._elided = (key, text)
        p.setFont(title_font)
        p.setPen(_with_alpha(QColor(theme.text), 0.72) if self._unloaded else QColor(theme.text))
        p.drawText(title, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._elided[1])
        if "badge" in parts:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.accent))
            p.drawRoundedRect(QRectF(parts["badge"]), 8, 8)
            p.setFont(badge_font)
            p.setPen(QColor(theme.on_accent))
            p.drawText(parts["badge"], Qt.AlignmentFlag.AlignCenter, self._badge)
        for name in ("audio", "close"):
            if name not in parts:
                continue
            area = QRectF(parts[name])
            if self._pressed == name and self._part == name:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_with_alpha(QColor(theme.text), 0.10))
                p.drawRoundedRect(area, 6, 6)
            elif self._part == name and self._part_hover > 0.001:
                from .theme import hover_color
                color = hover_color(theme)
                color.setAlphaF(color.alphaF() * self._part_hover)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(area, 6, 6)
            if name == "close":
                icon = "ph.minus" if self.kind == PINNED else "ph.x"
            else:
                icon = "ph.speaker-slash" if self._muted else "ph.speaker-high"
            inset = (ROW_BUTTON - ROW_BUTTON_ICON) / 2
            _row_icon(icon, theme.text).paint(p, area.adjusted(inset, inset, -inset, -inset).toRect())
        p.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._lay_out()

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            pos = event.pos()
            part = self._part_at(pos)
            tip = {"close": ("Unload Tab" if self.kind == PINNED else "Close Tab"),
                   "audio": "Unmute Tab" if self._muted else "Mute Tab",
                   "home": "Back to the pinned page"}.get(part)
            if tip is None and (self.parts.get("title", QRect()).contains(pos) or "title" not in self.parts):
                tip = self._title_tip or ("" if "title" in self.parts or self.browser.store.settings.get(
                    "tab_previews", True) else self.tab.label)  # (collapsed, with no previews: its title)
            if tip:
                QToolTip.showText(event.globalPos(), tip, self, self.parts.get(part or "title", QRect()))
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mousePressEvent(self, event):
        part = self._part_at(event.position().toPoint())
        if part and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = part  # (a button's click, not the start of a drag or a click on the row)
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._set_part(self._part_at(event.position().toPoint()))
        if self._pressed:
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, None
        if pressed:
            self.update()
            if event.button() == Qt.MouseButton.LeftButton and self._part_at(event.position().toPoint()) == pressed:
                {"close": lambda: self.browser.close_tab(self.tab),
                 "audio": lambda: self.browser.toggle_mute(self.tab),
                 "home": lambda: self.browser.go_home(self.tab)}[pressed]()
            return
        super().mouseReleaseEvent(event)

    def set_selected(self, selected):
        if self.selected != selected:
            repolish(self, selected=selected)
        self._update_close()

    def _update_close(self):
        visible = self.selected or self.underMouse()
        if self.kind == PINNED:
            visible = visible and self.tab.view is not None
        self._show_close = visible
        self._lay_out()
        self.update()

    def enterEvent(self, event):
        self._update_close()
        self.browser.tab_preview.hover(self)
        if self.browser.is_sleeping(self.tab):
            # Resting on a sleeping tab: it starts waking now, so it's there (or nearly) by the click
            self._waking = QTimer(self, singleShot=True, interval=WAKE_AHEAD_MS,
                                  timeout=lambda: self.browser.wake_ahead(self.tab))
            self._waking.start()
        super().enterEvent(event)  # (HoverFade)

    def leaveEvent(self, event):
        self._set_part(None)
        self._update_close()
        self.browser.tab_preview.leave(self)
        waking = getattr(self, "_waking", None)
        if waking is not None:
            waking.stop()  # (only passing over it)
        super().leaveEvent(event)


class FolderRow(HoverFade, QFrame):
    """A pinned folder: click to open/close, drag to move, right-click for options. Like a TabRow, it draws its
    own icon, name and count (one widget, not four)."""

    def __init__(self, browser, folder, depth=0, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.folder = folder
        self.depth = depth
        self._press_pos = None
        self.setObjectName("TabRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(ROW_HEIGHT)
        self.icon_picture = QPixmap()
        self.count_text = ""
        self._elided = None
        self.refresh()

    def refresh(self):
        theme = self.browser.theme
        color = _with_alpha(theme.text, 0.75)
        live = self.folder.live or {}
        if live:  # a Live Folder: what it follows, and how many items are new
            icon = _row_icon("ph.github-logo" if live.get("kind") == "github" else "ph.rss", theme.accent_text)
        elif self._github_section():  # a section of the GitHub folder, or one of your repos
            icon = _row_icon(GITHUB_SECTION_ICONS.get(self.folder.name, "ph.book-bookmark"), color)
        else:  # (its own color and symbol, if you've picked them in its menu)
            tint = QColor(self.folder.color) if self.folder.color else color
            name = self.folder.icon or ("ph.folder-open" if self.folder.expanded else "ph.folder")
            icon = _row_icon(name, tint)
        self.icon_picture = icon.pixmap(16, 16)
        self.setAccessibleName(f"{self.folder.name}, folder, {'open' if self.folder.expanded else 'closed'}")
        fresh = sum(1 for t in walk_tabs(self.folder.children) if t.fresh)
        # Only a Live Folder's new items get a count, like Arc; how many tabs a folder holds is in its tooltip
        self.count_text = f"{fresh} new" if fresh else ""
        count = sum(1 for _ in walk_tabs(self.folder.children))
        tip = f"{count} tab{'s' if count != 1 else ''}" if not self.folder.expanded else ""
        self.setToolTip(f"{self.folder.name}\n{tip}".strip() if compact(self.browser) else tip)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)  # (its stylesheet's highlight, and the hover fading over it)
        text = QColor(self.browser.theme.text)
        name_font = QFont(self.font())
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.Weight.Medium)
        small = QFont(self.font())
        small.setPixelSize(11)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if compact(self.browser):  # (collapsed: its icon alone, and a dot if it has something new)
            icon = QRect(self.width() // 2 - 8, ROW_HEIGHT // 2 - 8, 16, 16)
            p.drawPixmap(icon, self.icon_picture)
            if self.count_text:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self.browser.theme.accent))
                p.drawEllipse(QRectF(icon.right() - 3, icon.top() - 3, 7, 7))
            p.end()
            return
        x = 10 + self.depth * INDENT
        p.drawPixmap(QRect(x, ROW_HEIGHT // 2 - 8, 16, 16), self.icon_picture)
        x += 16 + ROW_SPACING
        right = self.width() - 10
        if self.count_text:
            width = QFontMetrics(small).horizontalAdvance(self.count_text)
            p.setFont(small)
            p.setPen(_with_alpha(text, 0.55))
            p.drawText(QRect(right - width, 0, width, ROW_HEIGHT), Qt.AlignmentFlag.AlignVCenter, self.count_text)
            right -= width + ROW_SPACING
        width = max(10, right - x)
        key = (self.folder.name, width)
        if self._elided is None or self._elided[0] != key:
            self._elided = (key, QFontMetrics(name_font).elidedText(self.folder.name, Qt.TextElideMode.ElideRight, width))
        p.setFont(name_font)
        p.setPen(text)
        p.drawText(QRect(x, 0, width, ROW_HEIGHT), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._elided[1])
        p.end()

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
        self.setIcon(home_icon(self.tab, self.browser.theme))
        tip = (f"{display_host(self.tab.home_url)}\nNow: {self.tab.label}" if left_its_site(self.tab)
               else self.tab.label)
        self.setToolTip(plain_tip(tip))
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


class SpaceDot(QToolButton):
    """A Space's button at the bottom of the sidebar. Drop a tab (or several picked tabs) on it to move them to
    that Space, as in Arc; it lights up while one's held over it."""

    def __init__(self, browser, space):
        super().__init__()
        self.browser, self.space = browser, space
        self.setObjectName("SpaceDot")
        self.setAcceptDrops(True)

    def _tabs(self, event) -> list:
        """The tabs being dragged, if they can go to this Space (not Favorites: they're in every Space)."""
        if self.space.id == self.browser.store.active_space_id or not event.mimeData().hasFormat(TAB_MIME):
            return []
        tabs = dragged_tabs(self.browser, event) or [
            self.browser.store.find_tab(bytes(event.mimeData().data(TAB_MIME)).decode())]
        return [t for t in tabs if t is not None and (self.browser.store.locate(t) or (None,))[0] in (TODAY, PINNED)]

    def dragEnterEvent(self, event):
        if self._tabs(event):
            event.acceptProposedAction()
            repolish(self, drop=True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction() if self.property("drop") else event.ignore()

    def dragLeaveEvent(self, event):
        repolish(self, drop=False)

    def dropEvent(self, event):
        repolish(self, drop=False)
        tabs = self._tabs(event)
        if not tabs:
            event.ignore()
            return
        event.acceptProposedAction()
        space = self.space
        # (once the drag's over: moving a tab mid-drag rebuilt the list under it)
        QTimer.singleShot(0, lambda: self.browser.move_tabs_to_space(tabs, space))
        count = len(tabs)
        self.browser.toast(f"Moved {'a tab' if count == 1 else f'{count} tabs'} to {space.name}")


def dragged_tabs(browser, event) -> list:
    """The tabs in a drag of several picked tabs (else [])."""
    data = event.mimeData()
    if not data.hasFormat(TABS_MIME):
        return []
    found = (browser.store.find_tab(i) for i in bytes(data.data(TABS_MIME)).decode().split(","))
    return [t for t in found if t is not None]


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
