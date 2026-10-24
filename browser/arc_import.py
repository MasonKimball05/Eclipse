# Import your setup from Arc before it shuts down.
#
# Arc keeps everything locally in ~/Library/Application Support/Arc:
#   StorableSidebar.json      Spaces, pinned tabs, folders, favorites, today tabs
#   User Data/Default/History   Chromium history (SQLite)
#   User Data/Default/Favicons  Chromium favicon cache (SQLite)
# We only ever read these. The databases are copied first, since Arc may have them open.
#
# Not imported: logins/cookies (they're encrypted with Arc's own keychain key; just
# sign in again) and extensions (Qt 6.11's extension support crashes when enabling one).

import json
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QUrl

from . import storage
from .models import DEFAULT_FAVORITES, Folder, Space, Tab, walk_tabs
from .theme import PALETTE

ARC_DIR = Path.home() / "Library" / "Application Support" / "Arc"
CHROME_EPOCH_OFFSET = 11_644_473_600  # seconds between 1601-01-01 and 1970-01-01
HISTORY_IMPORT_LIMIT = 5000

# Arc's built-in Space icons are SF Symbols, which Qt can't draw; use the closest line icon
SF_SYMBOLS = {
    "planet": "ph.planet", "globe": "ph.globe", "star": "ph.star", "sparkles": "ph.sparkle", "heart": "ph.heart",
    "house": "ph.house", "briefcase": "ph.briefcase", "book": "ph.book-open", "books": "ph.book-open",
    "graduationcap": "ph.graduation-cap", "gamecontroller": "ph.game-controller", "music": "ph.music-notes",
    "headphones": "ph.headphones", "bolt": "ph.lightning", "leaf": "ph.leaf", "flame": "ph.fire", "sun": "ph.sun",
    "moon": "ph.moon", "camera": "ph.camera", "cart": "ph.shopping-cart", "bag": "ph.shopping-cart",
    "chevron.left.forwardslash.chevron.right": "ph.code", "terminal": "ph.terminal-window", "paintbrush": "ph.palette",
    "paintpalette": "ph.palette", "pencil": "ph.pencil", "airplane": "ph.airplane", "car": "ph.car",
    "dollarsign": "ph.currency-dollar", "building": "ph.buildings", "person": "ph.user", "flag": "ph.flag",
    "cup": "ph.coffee", "fork": "ph.pizza", "tv": "ph.film-strip", "film": "ph.film-strip", "lightbulb": "ph.lightning",
    "trophy": "ph.trophy", "gift": "ph.gift", "hammer": "ph.wrench", "wrench": "ph.wrench", "construction": "ph.wrench",
    "chart": "ph.chart-line", "newspaper": "ph.newspaper", "mail": "ph.envelope", "phone": "ph.chat-circle",
    "folder": "ph.notebook", "bookmark": "ph.flag", "rocket": "ph.rocket", "cloud": "ph.planet",
}


@dataclass
class ArcImport:
    spaces: list[Space] = field(default_factory=list)
    favorites: list[Tab] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)  # names only; reported as skipped
    favicons: dict[str, bytes] = field(default_factory=dict)  # page URL -> PNG

    @property
    def tab_count(self) -> int:
        return len(self.favorites) + sum(len(list(walk_tabs(s.pinned))) + len(s.today) for s in self.spaces)

    @property
    def folder_count(self) -> int:
        def count(nodes):
            return sum(1 + count(n.children) for n in nodes if isinstance(n, Folder))
        return sum(count(s.pinned) for s in self.spaces)


def arc_installed() -> bool:
    return (ARC_DIR / "StorableSidebar.json").exists()


def read_arc() -> ArcImport:
    result = ArcImport()
    _read_sidebar(result)
    profile = ARC_DIR / "User Data" / "Default"
    with tempfile.TemporaryDirectory() as tmp:
        result.history = _read_history(profile / "History", Path(tmp))
        urls = {t.url for t in result.favorites} | {t.url for s in result.spaces for t in walk_tabs(s.pinned + s.today)}
        result.favicons = _read_favicons(profile / "Favicons", Path(tmp), urls)
    result.extensions = _extension_names(profile / "Extensions")
    return result


# --- Sidebar ---------------------------------------------------------------------------

def _pairs(flat: list) -> dict:
    # Arc stores maps as [id, value, id, value, ...]
    return {flat[i]: flat[i + 1] for i in range(0, len(flat) - 1, 2) if isinstance(flat[i], str)}


def _color(space: dict, index: int) -> str:
    palette = space.get("customInfo", {}).get("windowTheme", {}).get("primaryColorPalette", {})
    rgb = palette.get("midTone")
    if not rgb:
        return PALETTE[index % len(PALETTE)][1]
    channel = lambda v: max(0, min(255, round(v * 255)))
    return "#{:02x}{:02x}{:02x}".format(channel(rgb["red"]), channel(rgb["green"]), channel(rgb["blue"]))


def _icon(space: dict) -> str:
    icon = space.get("customInfo", {}).get("iconType", {})
    if isinstance(icon.get("emoji_v2"), str):
        return icon["emoji_v2"]
    if isinstance(icon.get("emoji"), int):
        return chr(icon["emoji"])
    name = str(icon.get("icon", ""))
    return SF_SYMBOLS.get(name, SF_SYMBOLS.get(name.split(".")[0], ""))


def _read_sidebar(result: ArcImport):
    data = json.loads((ARC_DIR / "StorableSidebar.json").read_text())
    container = data["sidebar"]["containers"][1]
    items = _pairs(container.get("items", []))
    now = time.time()

    def tab_from(item, pinned):
        tab = item["data"]["tab"]
        url = tab.get("savedURL") or ""
        return Tab(url, tab.get("savedTitle") or "", home_url=url if pinned else None,
                   custom_title=(item.get("title") or None) if pinned else None,
                   tab_id=item["id"], last_active=now)  # "now", so Today tabs aren't archived on arrival

    def build(item_id, pinned):
        nodes = []
        for child_id in items.get(item_id, {}).get("childrenIds", []):
            item = items.get(child_id)
            if not item:
                continue
            kind = item.get("data", {})
            if "tab" in kind and kind["tab"].get("savedURL"):
                nodes.append(tab_from(item, pinned))
            elif "list" in kind:
                children = build(child_id, pinned)
                if pinned:
                    nodes.append(Folder(item.get("title") or "Folder", children, item["id"], expanded=False))
                else:
                    nodes.extend(walk_tabs(children))  # Today has no folders
        return nodes

    for index, (space_id, space) in enumerate(_pairs(container.get("spaces", [])).items()):
        containers = _pairs(space.get("containerIDs", []))
        result.spaces.append(Space(
            space.get("title") or f"Space {index + 1}", _color(space, index), _icon(space),
            space_id=f"arc-{space_id}",
            pinned=build(containers.get("pinned"), pinned=True),
            today=build(containers.get("unpinned"), pinned=False),
        ))

    for entry in container.get("topAppsContainerIDs", []):
        if isinstance(entry, str):
            result.favorites = [n for n in build(entry, pinned=True) if isinstance(n, Tab)]
            break


# --- Chromium databases ------------------------------------------------------------------

def _query(db: Path, tmp: Path, sql: str, params=()) -> list:
    if not db.exists():
        return []
    copy = tmp / db.name
    shutil.copy2(db, copy)
    with sqlite3.connect(f"file:{copy}?mode=ro", uri=True) as conn:
        return conn.execute(sql, params).fetchall()


def _read_history(db: Path, tmp: Path) -> list[dict]:
    rows = _query(db, tmp, "SELECT url, title, last_visit_time, visit_count FROM urls WHERE hidden = 0 "
                           "ORDER BY last_visit_time DESC LIMIT ?", (HISTORY_IMPORT_LIMIT,))
    # Visit counts come too, so the command bar ranks your usual sites well from day one
    return [{"url": url, "title": title or "", "ts": t / 1_000_000 - CHROME_EPOCH_OFFSET, "visits": max(count or 1, 1)}
            for url, title, t, count in rows if url.startswith(("http://", "https://"))]


def _read_favicons(db: Path, tmp: Path, urls: set[str]) -> dict[str, bytes]:
    hosts = {QUrl(u).host() for u in urls}
    rows = _query(db, tmp, "SELECT m.page_url, b.image_data, b.width FROM icon_mapping m "
                           "JOIN favicon_bitmaps b ON b.icon_id = m.icon_id ORDER BY b.width")
    icons = {}
    for page_url, image, _ in rows:  # sorted by width, so larger icons win
        host = QUrl(page_url).host()
        if host in hosts and image:
            icons[host] = (page_url, image)
    return {page_url: image for page_url, image in icons.values()}


def _extension_names(folder: Path) -> list[str]:
    names = []
    for manifest in sorted(folder.glob("*/*/manifest.json")):
        try:
            name = json.loads(manifest.read_text()).get("name", "")
        except (OSError, ValueError):
            continue
        if name.startswith("__MSG_"):  # localized: look it up in the extension's messages
            messages = manifest.parent / "_locales" / "en" / "messages.json"
            try:
                name = json.loads(messages.read_text())[name[6:-2]]["message"]
            except (OSError, ValueError, KeyError):
                name = manifest.parent.parent.name
        if name not in names:
            names.append(name)
    return names


# --- Applying -------------------------------------------------------------------------------

def apply(store, arc: ArcImport):
    """Merge an import into the store. Re-importing replaces the Spaces imported last time."""
    for page_url, image in arc.favicons.items():
        storage.save_favicon_png(page_url, image)

    # Drop the untouched starter Spaces so your Arc setup isn't mixed with empty placeholders
    if arc.spaces:
        store.spaces = [s for s in store.spaces if s.pinned or s.today or s.id.startswith("arc-")]
    for space in arc.spaces:
        existing = store.space(space.id)
        if existing is not None:
            store.spaces[store.spaces.index(existing)] = space
        else:
            store.spaces.append(space)

    if arc.favorites:
        seeds = {url for url, _ in DEFAULT_FAVORITES}
        arc_urls = {t.url for t in arc.favorites}
        keep = [t for t in store.favorites if t.url not in seeds and t.url not in arc_urls]
        store.favorites = arc.favorites + keep

    store.merge_history(arc.history)
    if arc.spaces:
        store.active_space_id = arc.spaces[0].id
    store._changed()
