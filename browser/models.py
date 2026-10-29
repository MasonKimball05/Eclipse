# The data side of the browser, kept free of any widgets.
#
# Arc's model, which we copy:
#   - Favorites: a small grid of tabs shared by every Space
#   - Spaces: each has its own name, colour, pinned tabs and "Today" tabs
#   - Pinned tabs remember a home URL; closing one just unloads it
#   - Today tabs are ephemeral and get archived after sitting unused for a while
#   - Pinned and Today tabs can be grouped into (nested) folders (Tidy Tabs makes Today ones)

import time
import uuid

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from . import storage
from .theme import PALETTE

FAVORITES, PINNED, TODAY = "favorites", "pinned", "today"

HISTORY_LIMIT = 5000
ARCHIVE_LIMIT = 300


def frecency(entry: dict, now: float | None = None) -> float:
    """How likely you want this history entry: visits, weighted by how recently (like Firefox's frecency)."""
    age_days = ((now or time.time()) - entry.get("ts", 0)) / 86400
    weight = 100 if age_days <= 4 else 70 if age_days <= 14 else 50 if age_days <= 31 else 30 if age_days <= 90 else 10
    return entry.get("visits", 1) * weight


def new_id() -> str:
    return uuid.uuid4().hex[:12]


DEFAULT_FAVORITES = [
    ("https://www.google.com/", "Google"),
    ("https://github.com/", "GitHub"),
    ("https://www.youtube.com/", "YouTube"),
    ("https://en.wikipedia.org/", "Wikipedia"),
]


def display_host(url: str) -> str:
    return QUrl(url).host().removeprefix("www.")


class Tab(QObject):
    # Emitted whenever anything the sidebar draws (title, icon, loaded state) changes
    changed = pyqtSignal()

    def __init__(self, url="", title="", home_url=None, custom_title=None, tab_id=None, last_active=None):
        super().__init__()
        self.id = tab_id or new_id()
        self.url = url
        self.title = title
        self.home_url = home_url          # only pinned tabs and favorites have one
        self.custom_title = custom_title  # set when the user renames a pinned tab
        self.last_active = last_active or time.time()
        self.view = None                  # the WebView, created lazily on first activation
        self.loading = False
        self.fresh = False                # new in a Live Folder, not opened yet (shows a dot)
        self.unread = None                # unread count from the page title ("3", "99+", "•"), not saved
        self.app_unread = None            # the same, from the site's own API (Parliament), not saved
        self.notified = False             # sent a notification you haven't looked at yet
        self.scroll = None                # {"url", "x", "y"}: where its page was scrolled, to go back to
        self.awake = False                # never put to sleep (a dashboard you keep an eye on)

    @property
    def label(self) -> str:
        return self.custom_title or self.title or display_host(self.url) or "New Tab"

    @property
    def icon(self):
        return storage.favicon(self.url)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "title": self.title, "home_url": self.home_url,
            "custom_title": self.custom_title, "last_active": self.last_active,
            **({"scroll": self.scroll} if self.scroll else {}),
            **({"awake": True} if self.awake else {}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Tab":
        tab = cls(d.get("url", ""), d.get("title", ""), d.get("home_url"), d.get("custom_title"),
                  d.get("id"), d.get("last_active"))
        scroll = d.get("scroll")
        if isinstance(scroll, dict) and isinstance(scroll.get("y"), (int, float)):
            tab.scroll = scroll
        tab.awake = d.get("awake") is True
        return tab


class Folder:
    """A group of tabs (and other folders), in a Space's Pinned or Today section."""

    def __init__(self, name="New Folder", children=None, folder_id=None, expanded=True, live=None):
        self.id = folder_id or new_id()
        self.name = name
        self.children: list = children or []
        self.expanded = expanded
        # A Live Folder's source, e.g. {"kind": "feed", "url": …} or {"kind": "github", "query": "review"},
        # plus what's been seen (see live_folders.py). None for an ordinary folder.
        self.live: dict | None = live
        if live:
            seen = set(live.get("seen", []))
            for child in walk_tabs(self.children):  # (its sub-folders' too: a GitHub folder has some)
                child.fresh = (child.home_url or child.url) not in seen

    def to_dict(self) -> dict:
        d = {"type": "folder", "id": self.id, "name": self.name, "expanded": self.expanded,
             "children": [n.to_dict() for n in self.children]}
        if self.live:
            d["live"] = self.live
        return d


def node_from_dict(d: dict):
    if d.get("type") == "folder":
        return Folder(d.get("name", "Folder"), [node_from_dict(c) for c in d.get("children", [])],
                      d.get("id"), d.get("expanded", True), d.get("live"))
    return Tab.from_dict(d)


def walk_tabs(nodes):
    for node in nodes:
        if isinstance(node, Folder):
            yield from walk_tabs(node.children)
        else:
            yield node


def walk_folders(nodes):
    for node in nodes:
        if isinstance(node, Folder):
            yield node
            yield from walk_folders(node.children)


def node_path(nodes, node, leaving=()) -> list[tuple[int, object, object]] | None:
    """Where `node` is in a tree, top down: [(index, folder, next), …, (index, node, next)]. `next` is what
    comes after it there and stays when the tabs in `leaving` go (a folder stays if it keeps a tab), so it
    can go back before that again, whatever has opened or closed elsewhere since."""
    going = {id(t) for t in leaving}

    def stays(n):
        return any(id(t) not in going for t in walk_tabs(n.children)) if isinstance(n, Folder) else id(n) not in going
    for i, n in enumerate(nodes):
        after = next((m for m in nodes[i + 1:] if stays(m)), None)
        if n is node:
            return [(i, n, after)]
        if isinstance(n, Folder):
            below = node_path(n.children, node, leaving)
            if below is not None:
                return [(i, n, after), *below]
    return None


def _containing_list(nodes, node):
    # The list (top-level or a folder's children) that directly holds `node`
    if any(n is node for n in nodes):
        return nodes
    for n in nodes:
        if isinstance(n, Folder):
            found = _containing_list(n.children, node)
            if found is not None:
                return found
    return None


class Space:
    def __init__(self, name, color, icon="", space_id=None, pinned=None, today=None, active_tab_id=None,
                 profile="default", theme=None, start_view=None):
        self.id = space_id or new_id()
        self.start_view = start_view  # its own choice of start view (True/False), or None for Settings' default
        self.theme: dict = theme or {}  # extra colours, intensity, light/dark, grain (see theme.py)
        self.profile = profile  # which browser profile (cookies, logins) this Space's tabs use
        self.name = name
        self.color = color
        self.icon = icon  # an emoji, or a line-icon name like "ph.planet" (see icons.py)
        self.pinned: list = pinned or []  # Tabs and Folders
        self.today: list = today or []  # Tabs and Folders
        self.active_tab_id = active_tab_id

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "color": self.color, "icon": self.icon,
            "active_tab_id": self.active_tab_id, "profile": self.profile, "theme": self.theme,
            "start_view": self.start_view,
            "pinned": [t.to_dict() for t in self.pinned],
            "today": [t.to_dict() for t in self.today],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Space":
        return cls(d.get("name", "Space"), d.get("color", PALETTE[0][1]), d.get("icon", d.get("emoji", "")), d.get("id"),
                   [node_from_dict(t) for t in d.get("pinned", [])],
                   [node_from_dict(t) for t in d.get("today", [])],
                   d.get("active_tab_id"), d.get("profile", "default"), d.get("theme"), d.get("start_view"))


class Store(QObject):
    # Tabs/spaces were added, removed or reordered: the sidebar needs a rebuild
    structure_changed = pyqtSignal()

    def __init__(self, path):
        super().__init__()
        self.path = path
        self._save_timer = QTimer(self, singleShot=True, interval=500, timeout=self.save)

        # If the session file is unreadable (or missing after a crash mid-save), the copy before it
        data = storage.read_json(path) or storage.read_json(storage.previous_copy(path)) or {}
        self.is_new = not data  # first launch: nothing saved yet
        if data.get("spaces"):
            self.spaces = [Space.from_dict(s) for s in data["spaces"]]
            self.favorites = [Tab.from_dict(t) for t in data.get("favorites", [])]
        else:
            self._seed_defaults()
        self.active_space_id = data.get("active_space_id", self.spaces[0].id)
        if not self.space(self.active_space_id):
            self.active_space_id = self.spaces[0].id
        self.archive: list[dict] = data.get("archive", [])
        self.history: list[dict] = data.get("history", [])
        self.settings: dict = {"archive_after_hours": 12, "sidebar_visible": True, "sidebar_width": 250,
                               **data.get("settings", {})}
        self.window_geometry: str | None = data.get("window_geometry")
        self.splits: list[list[str]] = data.get("splits", [])  # Split View groups of tab ids

    def _seed_defaults(self):
        self.spaces = [Space("Personal", PALETTE[0][1]), Space("Work", PALETTE[1][1])]
        self.favorites = [
Tab(url, title, home_url=url) for url, title in DEFAULT_FAVORITES]

    # --- Persistence ---------------------------------------------------------

    def schedule_save(self):
        # Coalesce bursts of changes (e.g. a page updating its title repeatedly) into one write
        self._save_timer.start()

    def save(self):
        self._save_timer.stop()
        storage.write_json(self.path, {
            "version": 1,
            "active_space_id": self.active_space_id,
            "favorites": [t.to_dict() for t in self.favorites],
            "spaces": [s.to_dict() for s in self.spaces],
            "archive": self.archive[:ARCHIVE_LIMIT],
            "history": self.history[:HISTORY_LIMIT],
            "settings": self.settings,
            "window_geometry": self.window_geometry,
            "splits": [g for g in ([i for i in g if self.find_tab(i)] for g in self.splits) if len(g) >= 2],
        }, keep_previous=True)

    def _changed(self):
        self.structure_changed.emit()
        self.schedule_save()

    # --- Lookups ---------------------------------------------------------------

    @property
    def active_space(self) -> Space:
        return self.space(self.active_space_id)

    def space(self, space_id) -> Space | None:
        return next((s for s in self.spaces if s.id == space_id), None)

    def tab_list(self, kind, space=None) -> list[Tab]:
        return self.favorites if kind == FAVORITES else getattr(space, kind)

    def _parent(self, node) -> tuple[list, str, Space | None] | None:
        """(the list holding node, which section it's in, its Space)."""
        if any(n is node for n in self.favorites):
            return self.favorites, FAVORITES, None
        for space in self.spaces:
            parent = _containing_list(space.pinned, node)
            if parent is not None:
                return parent, PINNED, space
            parent = _containing_list(space.today, node)
            if parent is not None:
                return parent, TODAY, space
        return None

    def locate(self, node) -> tuple[str, Space | None] | None:
        found = self._parent(node)
        return found[1:] if found else None

    def all_tabs(self):
        yield from self.favorites
        for space in self.spaces:
            yield from walk_tabs(space.pinned)
            yield from walk_tabs(space.today)

    def find_tab(self, tab_id) -> Tab | None:
        return next((t for t in self.all_tabs() if t.id == tab_id), None)

    def find_node(self, node_id):
        """A Tab or a Folder by id."""
        for space in self.spaces:
            for folder in walk_folders(space.pinned + space.today):
                if folder.id == node_id:
                    return folder
        return self.find_tab(node_id)

    def folder_of(self, node) -> Folder | None:
        for space in self.spaces:
            for folder in walk_folders(space.pinned + space.today):
                if any(n is node for n in folder.children):
                    return folder
        return None

    def live_folder_of(self, node) -> Folder | None:
        """The Live Folder holding `node`, however deep (in one of its sub-folders, say), or None."""
        def find(nodes, live):  # one walk, knowing the nearest Live Folder above
            for n in nodes:
                if n is node:
                    return live
                if isinstance(n, Folder):
                    found = find(n.children, n if n.live else live)
                    if found is not False:
                        return found
            return False
        for space in self.spaces:
            for section in (space.pinned, space.today):
                found = find(section, None)
                if found is not False:
                    return found
        return None

    def visible_tabs(self) -> list[Tab]:
        space = self.active_space
        return self.favorites + list(walk_tabs(space.pinned)) + list(walk_tabs(space.today))

    # --- Mutations -------------------------------------------------------------

    def add_tab(self, tab, kind=TODAY, space=None, index=0):
        if kind != TODAY and tab.home_url is None:
            tab.home_url = tab.url
        self.tab_list(kind, space or self.active_space).insert(index, tab)
        self._changed()

    def move_tab(self, node, kind, space=None, index=None, folder=None):
        """Move a Tab or Folder. `folder` (pinned only) puts it inside that folder."""
        if folder is not None and (folder.live or self.live_folder_of(folder)):
            return  # a Live Folder's contents come from its feed; refreshing would drop anything added
        if isinstance(node, Folder) and self.live_folder_of(node):
            return  # (one of its sub-folders: the next refresh would only make it again)
        space = space or self.active_space
        if isinstance(node, Folder):
            # Folders live in Pinned or Today (not Favorites), and never inside themselves
            if kind == FAVORITES or (folder is not None and (folder is node or folder in walk_folders(node.children))):
                return
        dest = folder.children if folder is not None and kind != FAVORITES else self.tab_list(kind, space)
        found = self._parent(node)
        holder = self.folder_of(node)  # (the folder it leaves: a Today one goes when it's emptied)
        if found:
            src = found[0]
            old = next(i for i, n in enumerate(src) if n is node)
            del src[old]
            if src is dest and index is not None and old < index:
                index -= 1
        index = len(dest) if index is None else max(0, min(index, len(dest)))
        dest.insert(index, node)
        if isinstance(node, Tab):
            converted = [node]
        elif found and found[1] != kind:  # a folder changing section: its tabs change with it
            converted = list(walk_tabs(node.children))
        else:
            converted = []
        for tab in converted:
            if kind == TODAY:  # (Today tabs have no home page, nor a pinned name)
                tab.home_url = None
                tab.custom_title = None
            elif tab.home_url is None:
                tab.home_url = tab.url
        if holder is not None and holder is not folder and not holder.children and \
                (self.locate(holder) or (None,))[0] == TODAY:
            parent = self._parent(holder)
            parent[0][:] = [n for n in parent[0] if n is not holder]  # (closing its last tab does the same)
        if folder is not None:
            folder.expanded = True
        self._changed()

    def remove_tab(self, node):
        """Remove a Tab or a Folder (with everything inside it)."""
        found = self._parent(node)
        if found:
            found[0][:] = [n for n in found[0] if n is not node]
            if found[1] == TODAY:
                self._prune_empty_folders(found[2].today)  # a Today folder goes away with its last tab
            self._changed()

    def _prune_empty_folders(self, nodes):
        for folder in [n for n in nodes if isinstance(n, Folder)]:
            self._prune_empty_folders(folder.children)
        nodes[:] = [n for n in nodes if not (isinstance(n, Folder) and not n.children)]

    def add_folder(self, space, name, index=None, kind=PINNED) -> Folder:
        folder = Folder(name)
        nodes = self.tab_list(kind, space)
        nodes.insert(len(nodes) if index is None else index, folder)
        self._changed()
        return folder

    def ungroup_folder(self, folder):
        found = self._parent(folder)
        if found:
            parent = found[0]
            i = next(i for i, n in enumerate(parent) if n is folder)
            parent[i:i + 1] = folder.children
            self._changed()

    def set_folder_expanded(self, folder, expanded):
        folder.expanded = expanded
        self._changed()

    def today_shape(self, space):
        """Today's tabs and folders as they are now, nested (folders lose children as tabs go)."""
        def shape(nodes):
            return [(node, shape(node.children) if isinstance(node, Folder) else None) for node in nodes]
        return shape(space.today)

    def restore_today(self, space, shape, archived=()):
        """Put Today back the way today_shape() saw it, keeping anything added since (on top), and take
        those tabs out of the archive again."""
        def rebuild(saved):
            nodes = []
            for node, children in saved:
                if isinstance(node, Folder):
                    node.children = rebuild(children)
                nodes.append(node)
            return nodes
        restored = rebuild(shape)
        keep = {id(n) for n, _ in shape}
        space.today[:] = [n for n in space.today if id(n) not in keep] + restored
        gone = {id(entry) for entry in archived}
        self.archive[:] = [entry for entry in self.archive if id(entry) not in gone]
        self._changed()

    def put_back(self, space, path, node):
        """Put `node` back in Today where node_path() saw it: in the same folders (each brought back too if
        it went away with its last tab), before what came after it (or where it was, if that's gone too)."""
        nodes = space.today
        for depth, (index, item, after) in enumerate(path):
            where = next((i for i, n in enumerate(nodes) if n is after), None) if after is not None else len(nodes)
            where = min(index, len(nodes)) if where is None else where
            if depth == len(path) - 1:
                nodes.insert(where, node)
                break
            found = self._parent(item)
            if found is None:  # (emptied, so it went: back it comes, empty, ready for the tab)
                item.children = []
                nodes.insert(where, item)
            elif found[0] is not nodes:  # the folder's been moved somewhere else since: Today's top
                space.today.insert(0, node)
                break
            nodes = item.children
        self._changed()

    def archive_tab(self, tab, space) -> dict | None:
        """Into the archive (a tab with no address just closes); returns its archive entry, if any."""
        entry = None
        if tab.url:
            entry = {"url": tab.url, "title": tab.label, "space_id": space.id, "archived_at": time.time()}
            self.archive.insert(0, entry)
            del self.archive[ARCHIVE_LIMIT:]
        self.remove_tab(tab)
        return entry

    def stale_today_tabs(self, exclude=()) -> list[tuple[Tab, Space]]:
        """Today tabs idle for longer than the archive setting. `exclude`: tabs to keep (on screen)."""
        hours = self.settings.get("archive_after_hours", 12)
        if not hours:
            return []
        cutoff = time.time() - hours * 3600
        keep = {id(t) for t in exclude if t is not None}
        return [(t, s) for s in self.spaces for t in walk_tabs(s.today) if id(t) not in keep and t.last_active < cutoff]

    def add_space(self, name, color, icon="") -> Space:
        space = Space(name, color, icon)
        self.spaces.append(space)
        self._changed()
        return space

    def remove_space(self, space):
        self.spaces.remove(space)
        if self.active_space_id == space.id:
            self.active_space_id = self.spaces[0].id
        self._changed()

    # --- Split View ----------------------------------------------------------------------

    def split_of(self, tab) -> list[Tab] | None:
        """The tabs shown side by side with `tab`, in order, or None if it isn't split."""
        for group in self.splits:
            if tab.id in group:
                tabs = [t for t in map(self.find_tab, group) if t is not None]
                return tabs if len(tabs) >= 2 else None
        return None

    def add_to_split(self, base, tab, limit=4) -> bool:
        group = next((g for g in self.splits if base.id in g), None)
        if group is not None and tab.id in group:
            return True
        if group is not None and len(group) >= limit:
            return False
        self._drop_from_splits(tab)
        if group is None:
            group = [base.id]
            self.splits.append(group)
        group.append(tab.id)
        self._changed()
        return True

    def remove_from_split(self, tab):
        self._drop_from_splits(tab)
        self._changed()

    def separate_split(self, tab):
        self.splits = [g for g in self.splits if tab.id not in g]
        self._changed()

    def _drop_from_splits(self, tab):
        for group in self.splits:
            if tab.id in group:
                group.remove(tab.id)
        self.splits = [g for g in self.splits if len(g) >= 2]

    def merge_history(self, entries):
        """Add entries ({url, title, ts}) from elsewhere, keeping the newest per URL."""
        newest = {}
        for entry in self.history + entries:
            old = newest.get(entry["url"])
            if old is None or entry["ts"] > old["ts"]:
                visits = max(entry.get("visits", 1), old.get("visits", 1) if old else 1)
                newest[entry["url"]] = {**entry, "visits": visits}
            else:
                old["visits"] = max(old.get("visits", 1), entry.get("visits", 1))
        self.history = sorted(newest.values(), key=lambda e: e["ts"], reverse=True)[:HISTORY_LIMIT]
        self.schedule_save()

    def record_history(self, url, title):
        if not url.startswith(("http://", "https://")):
            return
        # Most recent first, one entry per URL, counting visits (for ranking command bar suggestions)
        old = next((h for h in self.history if h["url"] == url), None)
        self.history = [h for h in self.history if h["url"] != url]
        self.history.insert(0, {"url": url, "title": title, "ts": time.time(),
                                "visits": (old or {}).get("visits", 0) + 1})
        del self.history[HISTORY_LIMIT:]
        self.schedule_save()
