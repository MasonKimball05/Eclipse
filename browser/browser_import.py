# Bookmarks and history from the browser you're coming from: Chrome and the browsers built like it (Brave,
# Edge, Vivaldi, Chromium), and Safari. (Arc has its own, fuller import: arc_import.py. Passwords are in
# password_store.py.)
#
# Each browser's bookmarks become a Space of its own: the bookmarks bar as pinned tabs, its folders as folders,
# the rest of the bookmarks in a folder after them. History goes into ⌘T's suggestions, visit counts and all.
# Everything is only ever read, from copies (the browser may have its files open).
#
# Safari keeps its bookmarks where macOS asks for Full Disk Access before any other app reads them. Rather than
# ask for that, Eclipse takes the file Safari exports (File → Export → Bookmarks), which is the same format
# Firefox and Chrome export too; with Full Disk Access granted it reads Safari's own, history included.

import json
import plistlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import storage
from .arc_import import HISTORY_IMPORT_LIMIT, _read_favicons, _read_history
from .models import Folder, Space, Tab, walk_tabs
from .password_store import CHROMIUM_BROWSERS
from .theme import PALETTE

SAFARI = Path.home() / "Library" / "Safari"
MAC_EPOCH_OFFSET = 978_307_200  # seconds between 1970-01-01 and 2001-01-01 (Safari's dates)
BAR_NAMES = {"bookmarks bar", "bookmarks toolbar", "favorites", "favourites", "favorites bar", "bookmarksbar"}


def _pruned(nodes) -> list:
    """Without empty folders (one holding only a javascript: bookmarklet, say) or a Reading List."""
    kept = []
    for node in nodes:
        if isinstance(node, Folder):
            node.children = _pruned(node.children)
            if not node.children or node.name in ("Reading List", "com.apple.ReadingList"):
                continue
        kept.append(node)
    return kept


@dataclass
class BrowserImport:
    name: str
    spaces: list[Space] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    favicons: dict[str, bytes] = field(default_factory=dict)

    @property
    def bookmark_count(self) -> int:
        return sum(len(list(walk_tabs(s.pinned))) for s in self.spaces)


# --- Chrome and the browsers built like it ------------------------------------------------------------

def chromium_installed() -> list[str]:
    """The Chromium browsers here with bookmarks or history (Arc left out: it has its own import)."""
    return [name for name in CHROMIUM_BROWSERS if name != "Arc" and chromium_profiles(name)]


def chromium_profiles(name: str, folder: Path | None = None) -> list[tuple[Path, str]]:
    """A browser's profiles: (folder, the name you gave it), the ones with bookmarks or history."""
    folder = folder or CHROMIUM_BROWSERS[name][0]
    try:
        names = json.loads((folder / "Local State").read_text()).get("profile", {}).get("info_cache", {})
    except (OSError, ValueError):
        names = {}
    try:
        found = [p for p in sorted(folder.iterdir()) if p.is_dir() and
                 ((p / "Bookmarks").exists() or (p / "History").exists())]
    except OSError:
        return []
    return [(p, (names.get(p.name) or {}).get("name") or p.name) for p in found]


def _chromium_nodes(children) -> list:
    nodes = []
    for child in children or []:
        if child.get("type") == "url" and str(child.get("url", "")).startswith(("http://", "https://")):
            nodes.append(Tab(child["url"], child.get("name") or "", home_url=child["url"],
                             custom_title=child.get("name") or None))
        elif child.get("type") == "folder":
            nodes.append(Folder(child.get("name") or "Folder", _chromium_nodes(child.get("children")),
                                expanded=False))
    return nodes


def chromium_bookmarks(path: Path) -> list:
    """A profile's bookmarks as pinned tabs and folders: the bar's first, then the rest in folders."""
    roots = json.loads(path.read_text()).get("roots", {})
    nodes = _chromium_nodes(roots.get("bookmark_bar", {}).get("children"))
    for key, title in (("other", "Other Bookmarks"), ("synced", "Mobile Bookmarks")):
        rest = _chromium_nodes(roots.get(key, {}).get("children"))
        if rest:
            nodes.append(Folder(title, rest, expanded=False))
    return _pruned(nodes)


def read_chromium(name: str, folder: Path | None = None) -> BrowserImport:
    result = BrowserImport(name)
    profiles = chromium_profiles(name, folder)
    with tempfile.TemporaryDirectory() as scratch:
        for index, (profile, label) in enumerate(profiles):
            try:
                pinned = chromium_bookmarks(profile / "Bookmarks") if (profile / "Bookmarks").exists() else []
            except (OSError, ValueError):
                pinned = []
            if pinned:
                title = name if len(profiles) == 1 else f"{name} · {label}"
                result.spaces.append(_space(title, len(result.spaces), f"{name}-{profile.name}", pinned))
            work = Path(scratch) / str(index)
            work.mkdir()
            try:
                result.history += _read_history(profile / "History", work)
            except (OSError, sqlite3.Error):
                pass  # (a history file it was halfway through writing: the bookmarks still come)
        urls = {t.url for s in result.spaces for t in walk_tabs(s.pinned)}
        if profiles:
            try:
                result.favicons = _read_favicons(profiles[0][0] / "Favicons", Path(scratch), urls)
            except (OSError, sqlite3.Error):
                pass
    return result


def _space(title, index, key, pinned) -> Space:
    slug = "".join(c if c.isalnum() else "-" for c in key.lower())
    return Space(title, PALETTE[(index + 2) % len(PALETTE)][1], "", space_id=f"import-{slug}", pinned=pinned)


# --- Safari -----------------------------------------------------------------------------------------------

def safari_installed() -> bool:
    return (Path("/Applications/Safari.app").exists() or SAFARI.exists())


def _safari_nodes(children) -> list:
    nodes = []
    for child in children or []:
        kind = child.get("WebBookmarkType")
        if kind == "WebBookmarkTypeLeaf" and str(child.get("URLString", "")).startswith(("http://", "https://")):
            title = (child.get("URIDictionary") or {}).get("title") or ""
            nodes.append(Tab(child["URLString"], title, home_url=child["URLString"], custom_title=title or None))
        elif kind == "WebBookmarkTypeList" and child.get("Title") != "com.apple.ReadingList":
            nodes.append(Folder(child.get("Title") or "Folder", _safari_nodes(child.get("Children")), expanded=False))
    return nodes


def safari_bookmarks(path: Path | None = None) -> list:
    """Safari's own bookmarks (raises PermissionError without Full Disk Access): Favorites, then the rest."""
    data = plistlib.loads((path or SAFARI / "Bookmarks.plist").read_bytes())
    nodes, rest = [], []
    for top in _safari_nodes(data.get("Children")):
        if isinstance(top, Folder) and top.name.lower() in BAR_NAMES:
            nodes = top.children + nodes
        elif isinstance(top, Folder) and top.name == "BookmarksMenu":
            rest += top.children
        else:
            rest.append(top)
    return _pruned(nodes + ([Folder("Bookmarks Menu", rest, expanded=False)] if rest else []))


def safari_history(path: Path | None = None) -> list[dict]:
    with tempfile.TemporaryDirectory() as scratch:
        db = path or SAFARI / "History.db"
        copy = Path(scratch) / "History.db"
        shutil.copy2(db, copy)
        for extra in ("-wal", "-shm"):  # (Safari writes ahead: the newest visits are in these)
            if Path(f"{db}{extra}").exists():
                shutil.copy2(f"{db}{extra}", f"{copy}{extra}")
        with sqlite3.connect(copy) as conn:
            rows = conn.execute(
                "SELECT i.url, v.title, MAX(v.visit_time), i.visit_count FROM history_items i "
                "JOIN history_visits v ON v.history_item = i.id GROUP BY i.id ORDER BY MAX(v.visit_time) DESC "
                "LIMIT ?", (HISTORY_IMPORT_LIMIT,)).fetchall()
    return [{"url": url, "title": title or "", "ts": t + MAC_EPOCH_OFFSET, "visits": max(count or 1, 1)}
            for url, title, t, count in rows if url.startswith(("http://", "https://"))]


def read_safari() -> BrowserImport:
    """Safari's bookmarks and history, read directly: PermissionError without Full Disk Access."""
    result = BrowserImport("Safari")
    pinned = safari_bookmarks()
    if pinned:
        result.spaces.append(_space("Safari", 0, "safari", pinned))
    try:
        result.history = safari_history()
    except (OSError, sqlite3.Error):
        pass
    return result


# --- An exported bookmarks file (Safari, Firefox, Chrome: all the same format) ----------------------------

class _Netscape(HTMLParser):
    """<DT><H3>Folder</H3><DL><DT><A HREF="…">Title</A>…</DL>: the bookmarks file every browser exports."""

    def __init__(self):
        super().__init__()
        self.stack = [[]]
        self.pending_folder = None
        self.text, self.href, self.into = "", None, None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href, self.text, self.into = dict(attrs).get("href") or "", "", "a"
        elif tag == "h3":
            self.text, self.into = "", "h3"
        elif tag == "dl":
            folder = Folder(self.pending_folder or "Folder", [], expanded=False)
            self.stack[-1].append(folder)
            self.stack.append(folder.children)
            self.pending_folder = None

    def handle_endtag(self, tag):
        if tag == "a" and self.into == "a":
            if self.href.startswith(("http://", "https://")):
                title = self.text.strip()
                self.stack[-1].append(Tab(self.href, title, home_url=self.href, custom_title=title or None))
            self.into = None
        elif tag == "h3" and self.into == "h3":
            self.pending_folder, self.into = self.text.strip(), None
        elif tag == "dl" and len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data):
        if self.into:
            self.text += data


def parse_bookmarks_html(text: str) -> list:
    parser = _Netscape()
    parser.feed(text)
    nodes = _pruned(parser.stack[0])
    # (the file's outermost list is a folder of its own: open it)
    while len(nodes) == 1 and isinstance(nodes[0], Folder) and nodes[0].name in ("Folder", "Bookmarks"):
        nodes = nodes[0].children
    bar = [n for n in nodes if isinstance(n, Folder) and n.name.lower() in BAR_NAMES]
    rest = [n for n in nodes if n not in bar]
    first = [child for folder in bar for child in folder.children]
    if not first or (len(rest) == 1 and isinstance(rest[0], Folder)):
        return first + rest  # (one folder needs no folder round it)
    return first + ([Folder("Other Bookmarks", rest, expanded=False)] if rest else [])


def read_bookmarks_file(path: Path, name="Safari") -> BrowserImport:
    result = BrowserImport(name)
    pinned = parse_bookmarks_html(path.read_text(encoding="utf-8", errors="replace"))
    if pinned:
        result.spaces.append(_space(name, 0, f"{name}-file", pinned))
    return result


# --- Bringing it in ---------------------------------------------------------------------------------------

def apply(store, imported: BrowserImport):
    """Add an import's Spaces and history. Importing again refreshes a Space's bookmarks (its pinned tabs) and
    keeps the rest: its Today tabs, name and colour. On a first launch's first import, the empty starter Spaces
    make way."""
    for page_url, image in imported.favicons.items():
        storage.save_favicon_png(page_url, image)
    if imported.spaces and store.is_new:
        store.spaces = [s for s in store.spaces if s.pinned or s.today or s.id.startswith(("arc-", "import-"))]
    for space in imported.spaces:
        existing = store.space(space.id)
        if existing is not None:
            existing.pinned = space.pinned
        else:
            store.spaces.append(space)
    store.merge_history(imported.history)
    if imported.spaces and store.is_new:
        store.active_space_id = imported.spaces[0].id
        store.is_new = False  # (once: an import later on leaves your own empty Spaces alone)
    store._changed()
