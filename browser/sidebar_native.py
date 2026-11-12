# The sidebar's Space part drawn by SwiftUI (native/Sources/SidebarBody.swift), inside the Qt sidebar, in place of
# its scroll area: each Space's name, pinned tabs and folders, the divider with Tidy and Clear, New Tab and Today.
# Everything else in the sidebar stays Qt's. Settings → General → Native sidebar, where native pages are built.
#
# The Qt sections (sidebar.pinned, sidebar.today) are still made and kept up to date, hidden, so everything that
# reads them (the order of tabs, marks, tidying) works as before; this reads the store itself and sends every
# Space's tabs as JSON whenever the Qt sidebar would have redrawn, with each icon sent once. What's done on it comes
# back as actions, done the way the Qt rows do them.
#
# Switching Spaces is the native view's own scroll (a swipe follows your fingers; ⌃2 and the switcher animate it),
# so the Qt slide (space_slide.py) isn't used while this is on.

import ctypes
import hashlib
import weakref

from PyQt6.QtCore import QObject, QPoint, QTimer
from PyQt6.QtGui import QColor, QCursor, QWindow
from PyQt6.QtWidgets import QWidget

from . import native
from .models import PINNED, TODAY, Folder, Tab, walk_tabs

SETTING = "native_sidebar"
PUSH_AFTER_MS = 30  # (changes this close together go to the sidebar as one)
ICON_KEYS_KEPT = 2000  # (pictures remembered by their Qt key: more, and they start again)


def available() -> bool:
    """Native pages built, on macOS 14 or later (the view's paging needs it)."""
    import platform
    return native.available() and int((platform.mac_ver()[0] or "0").split(".")[0]) >= 14


def wanted(store) -> bool:
    return bool(store.settings.get(SETTING, True)) and available()  # (on unless turned off in Settings)


LIVE_SOURCES = {"github": "GitHub", "repos": "your repositories", "calendar": "a calendar", "json": "a feed",
                "page": "a page"}


def _live_tip(folder) -> str:
    """What a Live Folder's mark says: that it fills itself, and from where."""
    if not folder.live:
        return ""
    source = LIVE_SOURCES.get(folder.live.get("kind", ""), "a feed")
    return f"Live Folder: keeps itself up to date from {source}"


def _rgba(color) -> list[float]:
    c = QColor(color)
    return [round(c.redF(), 4), round(c.greenF(), 4), round(c.blueF(), 4), round(c.alphaF(), 4)]


class NativeSidebarBody(QObject):
    def __init__(self, browser, sidebar):
        super().__init__(sidebar)
        self.browser = browser
        self.sidebar = sidebar
        self._sent_icons: set[str] = set()
        self._icon_keys: dict[int, str] = {}
        self._themes: dict[str, tuple] = {}
        self._watched = weakref.WeakSet()  # (tabs, not their ids: a closed tab's id can come back)
        self._pending_icons: dict[str, str] = {}
        self._go = 0
        self._last = None
        # (a moment, not the next pass: ten tabs loading, or a title ticking, sent once, not once each)
        self._timer = QTimer(self, singleShot=True, interval=PUSH_AFTER_MS, timeout=self.push)
        self._waking = QTimer(self, singleShot=True, interval=300, timeout=self._wake)  # (as a Qt row's WAKE_AHEAD_MS)
        self._resting = None
        self._hovered = None  # (the row the pointer's on, for its tab preview: RowPlace)
        self._dragging = QTimer(self, interval=100, timeout=self._drag_check)
        self._shown = browser.store.active_space_id  # (the Space it was last told to show)
        self._split: set[int] = set()
        self.page = native.NativePage({"page": "sidebar_body", **self.state(), **native.theme_state(browser.theme),
                                       "icons_add": self._take_icons(), "go": 0}, self, embed=True)
        self.container = None
        if self.page.id and self.page.view():
            self._window = QWindow.fromWinId(self.page.view())
            self.container = QWidget.createWindowContainer(self._window, sidebar)
            self.container.setObjectName("NativeSidebarBody")
            self.container.setMinimumHeight(60)
        self.page.action.connect(self._act)

    @property
    def ok(self) -> bool:
        return self.container is not None

    def close(self):
        self.page.close()

    # --- What it shows -------------------------------------------------------------------------------------

    def schedule(self):
        """Something changed: sent once the event loop's next pass comes (many changes, one message)."""
        self._timer.start()

    def push(self):
        if not self.page.id:
            return
        state = {**self.state(), **native.theme_state(self.browser.theme)}
        icons = self._take_icons()
        if state == self._last and not icons:
            return
        self._last = state
        change = {**state, **({"icons_add": icons} if icons else {})}
        if state["current"] != self._shown:
            # A Space switched without go() (while the sidebar was hidden, by a Space Rule…): there at once, since
            # the view only scrolls when "go" changes
            self._go += 1
            self._shown = state["current"]
            change.update({"go": self._go, "animate": False, "measure": None})
        self.page.update(change)

    def state(self) -> dict:
        b, store = self.browser, self.browser.store
        theme = b.theme
        side = self.sidebar
        self._watch_tabs()
        current = b.current_tab
        self._split = {id(t) for t in (store.split_of(current) or []) if t is not current} if current else set()
        state = {
            "spaces": [self._space(s) for s in store.spaces], "current": store.active_space_id,
            "selected_id": current.id if current is not None else "",
            "colors": self._colors(theme),
            "compact": bool(side.compact),
            "marked": [t.id for t in side.marked],
        }
        return state

    @staticmethod
    def _colors(theme) -> dict:
        from .theme import hover_color
        selected = QColor("white")
        selected.setAlphaF(0.14 if theme.dark else 0.82)
        return {"text": _rgba(theme.text), "accent": _rgba(theme.accent), "accent_text": _rgba(theme.accent_text),
                "hover": _rgba(hover_color(theme)), "selected": _rgba(selected)}

    def _theme(self, space):
        """The Space's own look (each page is drawn in its own Space's colors, so one sliding in looks as it will)."""
        if space is self.browser.store.active_space:
            return self.browser.theme
        from .theme import theme_for_space
        import json
        key = (space.color, json.dumps(space.theme, sort_keys=True))
        cached = self._themes.get(space.id)
        if cached is None or cached[0] != key:
            cached = self._themes[space.id] = (key, theme_for_space(space))
        return cached[1]

    def _space(self, space) -> dict:
        from .icons import is_symbol, space_icon
        theme = self._theme(space)
        value = getattr(space, "icon", "") or ""
        icon = "" if value and not is_symbol(value) else self._icon(space_icon(space, theme.text, 32))
        side = self.sidebar
        active = space is self.browser.store.active_space
        # (the divider's Tidy and Clear are the Space you're on's: a page sliding in shows its own, none)
        tidy = side.tidy_btn
        return {"id": space.id, "name": space.name, "emoji": value if value and not is_symbol(value) else "",
                "icon": icon, "profile": space.profile if space.profile != "default" else "",
                "profile_tip": f"This Space uses the “{space.profile}” profile (its own logins and cookies)",
                "hint": "" if side.compact else "Drag tabs here to pin them", "colors": self._colors(theme),
                "clear": active and bool(space.today) and not side.compact,
                "tidy": {"label": tidy.text() if active and not tidy.isHidden() else "",
                         "enabled": tidy.isEnabled(), "tip": tidy.toolTip()},
                "pinned": [self._node(n, PINNED, theme) for n in space.pinned],
                "today": [self._node(n, TODAY, theme) for n in space.today]}

    def _node(self, node, kind, theme) -> dict:
        b = self.browser
        if isinstance(node, Folder):
            icon, count = self._folder_look(node, theme)
            tabs = sum(1 for _ in walk_tabs(node.children))
            return {"id": node.id, "kind": "folder", "name": node.name, "icon": icon, "count": count,
                    "expanded": node.expanded, "fixed": b.store.live_folder_of(node) is not None,
                    # (a Live Folder's contents are its feed's: nothing's dropped in)
                    "locked": bool(node.live or b.store.live_folder_of(node)), "size": len(node.children),
                    "live": bool(node.live), "live_tip": _live_tip(node),
                    "tip": "" if node.expanded else f"{tabs} tab{'s' if tabs != 1 else ''}",
                    "children": [self._node(c, kind, theme) for c in node.children] if node.expanded else []}
        from .sidebar import is_away, tab_icon
        from .tab_titles import shown_title
        current = b.current_tab
        sleeping = b.is_sleeping(node)
        audible, muted = b.tab_audio(node)
        marked = {id(t) for t in self.sidebar.marked}
        away = kind != TODAY and is_away(node)
        shown = shown_title(node, b.store.settings)
        badge = str(node.unread or node.app_unread or ("•" if node.notified else ""))
        # As a Qt row's: its tooltip only when there's no preview to say it (and none on the tab you're on)
        previews = b.store.settings.get("tab_previews", True) and node is not current
        tip = "" if previews else ((node.label + "\n" if shown != node.label else "") + node.url
                                   + ("\nSleeping — click to wake" if sleeping else "")
                                   + ("\nKept awake" if node.awake else ""))
        # What VoiceOver says: its title and badges, and what its menu can do (its buttons are drawn)
        extras = [f"{badge} unread" if badge and badge != "•" else "new notification" if badge else "",
                  "muted" if muted else "playing sound" if audible else "", "sleeping" if sleeping else ""]
        can = ["unload it" if kind == PINNED else "close it"]
        if audible or muted:
            can.append("unmute it" if muted else "mute it")
        if away:
            can.append("take it back to its pinned page")
        return {"id": node.id, "kind": "tab", "title": shown,
                "icon": self._icon(tab_icon(node, theme)), "away": away,
                "dim": (kind == PINNED and node.view is None) or sleeping, "fresh": bool(node.fresh),
                "badge": badge, "audio": "muted" if muted else "playing" if audible else "",
                "selected": node is current, "marked": id(node) in marked, "insplit": id(node) in self._split,
                "tip": tip, "label": ", ".join([node.label] + [e for e in extras if e]),
                "hint": "Its menu can " + ", ".join(can[:-1]) + (" or " if len(can) > 1 else "") + can[-1],
                # (an unloaded pinned tab has nothing to unload)
                "close": ("unload" if node.view is not None else "") if kind == PINNED else "close"}

    def _folder_look(self, folder, theme):
        from .sidebar import GITHUB_SECTION_ICONS, _row_icon, _with_alpha
        live = folder.live or {}
        parent = self.browser.store.live_folder_of(folder)
        if live:
            icon = _row_icon("ph.github-logo" if live.get("kind") == "github" else "ph.rss", theme.accent_text)
        elif parent is not None and parent.live.get("kind") == "github":
            icon = _row_icon(GITHUB_SECTION_ICONS.get(folder.name, "ph.book-bookmark"), _with_alpha(theme.text, 0.75))
        else:
            tint = QColor(folder.color) if folder.color else _with_alpha(theme.text, 0.75)
            icon = _row_icon(folder.icon or ("ph.folder-open" if folder.expanded else "ph.folder"), tint)
        fresh = sum(1 for t in walk_tabs(folder.children) if t.fresh)
        return self._icon(icon), f"{fresh} new" if fresh else ""

    def _icon(self, icon) -> str:
        """A key for an icon's picture, its PNG kept to send once."""
        if icon is None or icon.isNull():
            return ""
        cache = icon.cacheKey()
        key = self._icon_keys.get(cache)
        if key is None:
            if len(self._icon_keys) > ICON_KEYS_KEPT:
                self._icon_keys.clear()  # (an inbox's icon changing all day: a new picture each time, kept for good)
            data = native.png(icon.pixmap(32, 32))
            key = hashlib.sha1(data.encode()).hexdigest()[:16]
            self._icon_keys[cache] = key
            if key not in self._sent_icons:
                self._pending_icons[key] = data
        return key

    def _take_icons(self) -> dict:
        icons, self._pending_icons = self._pending_icons, {}
        self._sent_icons.update(icons)
        return icons

    def _watch_tabs(self):
        """Each tab's own changes (its title, icon, sound) redraw it, as they do a Qt row."""
        for space in self.browser.store.spaces:
            for tab in walk_tabs(space.pinned + space.today):
                if tab not in self._watched:
                    self._watched.add(tab)
                    tab.changed.connect(self.schedule)

    # --- Switching Spaces ---------------------------------------------------------------------------------

    # The window takes the new Space's colors and page once the scroll's landed (it's 300 ms): done at once, its
    # 25–45 ms of work held the scroll's start back about 50 ms; after, the scroll starts in about 15 and every frame
    # of it comes at the display's rate (tests/live/test_spaces_benchmark.py)
    CHANGE_AFTER_MS = 320

    def go(self, space, animate=True, measure=None):
        """Show `space`: its page scrolled into view (animated, unless not). `measure`: seconds of frames to count
        (the benchmark's), sent back as a "bench" action."""
        self._timer.stop()
        self.push()  # (what's waiting first: a Space just made is in the view before it's scrolled to)
        self._shown = space.id
        self._go += 1
        self._last = None
        self.page.update({"current": space.id, "go": self._go, "animate": animate,
                          **({"measure": measure} if measure else {"measure": None})})

    # --- What's done on it --------------------------------------------------------------------------------

    def _act(self, name, event):
        b, store = self.browser, self.browser.store
        first = str(event.get("id", "")).split(",")[0]
        node = store.find_node(first) if first else None
        if name in ("activate", "middle", "close", "audio", "home", "menu", "folder", "new_tab", "space_title",
                    "clear", "tidy", "drag"):
            spread = getattr(b, "spread_sidebar", None)
            if spread is not None:
                spread.pressed()  # (a click on the collapsed strip: it's being used as it is, as Qt's sees)
        if name == "activate" and isinstance(node, Tab):
            b.tab_preview.dismiss()
            if event.get("mark"):
                self.sidebar.mark(node, extend=event["mark"] == "extend")
                return
            self.sidebar.clear_marks()
            b.activate_tab(node)
        elif name == "middle" and isinstance(node, Tab):
            b.close_tab(node)
        elif name == "close" and isinstance(node, Tab):
            b.close_tab(node)
        elif name == "audio" and isinstance(node, Tab):
            b.toggle_mute(node)
        elif name == "home" and isinstance(node, Tab):
            b.go_home(node)
        elif name == "menu" and node is not None:
            pos = QCursor.pos()
            if isinstance(node, Folder):
                b.show_folder_menu(node, pos)
            elif len(self.sidebar.marked) > 1 and any(t is node for t in self.sidebar.marked):
                b.show_tabs_menu(list(self.sidebar.marked), pos)
            else:
                b.show_tab_menu(node, pos)
        elif name == "folder" and isinstance(node, Folder):
            b.toggle_folder(node)
        elif name == "new_tab":
            b.open_command_bar("new")
        elif name == "space_title":
            b.edit_space()
        elif name == "clear":
            b.clear_today()
        elif name == "tidy":
            self.sidebar._tidy_clicked()
        elif name == "heading":
            # A swipe on its way to a Space: its tab starts loading now, before it's there (window.prepare_space)
            space = next((s for s in store.spaces if s.id == event.get("id")), None)
            if space is not None:
                b.prepare_space(space)
            return
        elif name == "swiped":
            space = next((s for s in store.spaces if s.id == event.get("id")), None)
            if space is not None and space is not store.active_space:
                b.switch_space(space, swiped=True)
        elif name == "move" and node is not None:
            self._move(node, event)
        elif name == "drag":
            # A row's being dragged: the page takes tabs too (drop one there to split), until the button's let go
            b.tab_preview.dismiss()
            b.content.split_drop.activate(True)
            self._dragging.start()
            return
        elif name == "hover":
            # Resting on a sleeping tab: it starts waking, so it's there (or nearly) by the click
            self._resting = node if isinstance(node, Tab) and b.is_sleeping(node) else None
            self._waking.start() if self._resting is not None else self._waking.stop()
            # Its preview beside the sidebar, as a Qt row's (TabPreview reads a row's tab and place)
            if self._hovered is not None:
                b.tab_preview.leave(self._hovered)
                self._hovered.deleteLater()
                self._hovered = None
            if isinstance(node, Tab):
                self._hovered = RowPlace(self, node, float(event.get("y", 0)), float(event.get("height", 34)))
                b.tab_preview.hover(self._hovered)
            return
        self.schedule()

    def _drag_check(self):
        """A drag from the native view is the system's, which says nothing to Qt when it ends: over once no mouse
        button's down."""
        from . import macos
        event = macos._objc.objc_getClass(b"NSEvent") if macos._objc is not None else None
        if event is None or not macos._send(event, "pressedMouseButtons", restype=ctypes.c_ulong):
            self._dragging.stop()
            self.browser.content.split_drop.activate(False)
            self.page.update({"dragging": ""})  # (the row it came from, faded while it was carried, back)

    def _wake(self):
        if self._resting is not None and self.browser.store.locate(self._resting):
            self.browser.wake_ahead(self._resting)

    def _move(self, node, event):
        """A row dropped on another (before it), or at the end of a section: as a Qt drop does. Several dragged
        together (from Qt, or picked here) come as their ids, comma-separated."""
        b, store = self.browser, self.browser.store
        ids = str(event.get("id", "")).split(",")
        if len(ids) > 1:
            tabs = [t for t in (store.find_tab(i) for i in ids) if t is not None]
            if tabs and event.get("space") == store.active_space_id:
                kind = PINNED if event.get("section") == "pinned" else TODAY
                folder = store.find_node(event["folder"]) if event.get("folder") else None
                b.move_tabs(tabs, kind, int(event.get("index", 0)), folder if isinstance(folder, Folder) else None)
            return
        if event.get("space") != store.active_space_id:
            return
        kind = PINNED if event.get("section") == "pinned" else TODAY
        folder = store.find_node(event["folder"]) if event.get("folder") else None
        if folder is not None and not isinstance(folder, Folder):
            folder = None
        if isinstance(node, Folder) and (folder is node or any(f is folder for f in walk_folders(node))):
            return  # (a folder into itself)
        if folder is not None and (folder.live or store.live_folder_of(folder)):
            return  # (a Live Folder's contents are its feed's)
        dest = folder.children if folder is not None else store.tab_list(kind, store.active_space)
        index = max(0, min(int(event.get("index", len(dest))), len(dest)))  # (where the line was: the store
        # allows for it leaving the same list, as a Qt drop's is)
        if isinstance(node, Tab) and len(self.sidebar.marked) > 1 and any(t is node for t in self.sidebar.marked):
            b.move_tabs(list(self.sidebar.marked), kind, index, folder)
        else:
            b.move_tab(node, kind, index, folder)

    def anchor(self) -> QPoint:
        """Where the Space editor opens: under the Space's name, at the top of this view."""
        return self.container.mapToGlobal(QPoint(8, 34))


class RowPlace(QObject):
    """A native row, as TabPreview reads a Qt one: its tab, whether it's showing, and where it is."""

    def __init__(self, body, tab, y, height):
        super().__init__(body)
        self.body, self.tab, self._y, self._height = body, tab, round(y), round(height)

    def isVisible(self) -> bool:
        return self.body.container is not None and self.body.container.isVisible()

    def height(self) -> int:
        return self._height

    def mapTo(self, widget, point: QPoint) -> QPoint:
        return self.body.container.mapTo(widget, QPoint(point.x(), self._y + point.y()))


def walk_folders(folder):
    for child in folder.children:
        if isinstance(child, Folder):
            yield child
            yield from walk_folders(child)
