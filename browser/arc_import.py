# Import your setup from Arc before it shuts down.
#
# Arc keeps everything locally in ~/Library/Application Support/Arc:
#   StorableSidebar.json        Spaces, pinned tabs, folders, favorites, today tabs, each Space's profile
#   StorableArchiveItems.json   archived tabs
#   StorableLinkRouting.json    Air Traffic Control's rules
#   User Data/Local State       the profiles' names
#   User Data/Default/History   Chromium history (SQLite)
#   User Data/Default/Favicons  Chromium favicon cache (SQLite)
# and its settings in ~/Library/Preferences/company.thebrowser.Browser.plist (auto-archive, links from other
# apps, tidier download names and tab titles). We only ever read these. The databases are copied first, since
# Arc may have them open.
#
# Not imported: logins/cookies (they're encrypted with Arc's own keychain key; just
# sign in again) and extensions (Qt 6.11's extension support crashes when enabling one).

import json
import plistlib
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import QMessageBox

from . import brand, storage
from .features import Feature
from .models import ARCHIVE_LIMIT, DEFAULT_FAVORITES, Folder, Space, Tab, walk_tabs
from .webview import DEFAULT_PROFILE
from .theme import PALETTE

ARC_DIR = Path.home() / "Library" / "Application Support" / "Arc"
ARC_PREFERENCES = Path.home() / "Library" / "Preferences" / "company.thebrowser.Browser.plist"
APPLE_EPOCH_OFFSET = 978_307_200  # seconds between 1970-01-01 and 2001-01-01 (Arc's times count from 2001)
OFFERED = "arc_import_offered"  # (asked once, on a launch that missed the welcome: see ArcImportFeature.ready)
ARCHIVE_HOURS = {"twelveHours": 12, "twentyFourHours": 24, "oneDay": 24, "sevenDays": 24 * 7, "oneWeek": 24 * 7,
                 "thirtyDays": 24 * 30, "oneMonth": 24 * 30, "never": 0}
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
    settings: dict = field(default_factory=dict)  # Eclipse's settings, from Arc's
    archive: list[dict] = field(default_factory=list)  # archived tabs, newest first, as Store.archive keeps them
    routes: list[dict] = field(default_factory=list)  # Air Traffic Control rules: {"match", "space"}

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
    result.settings = _read_settings()
    result.archive = _read_archive()
    result.routes = _read_routes({s.id for s in result.spaces})
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

    names = _profile_names()
    for index, (space_id, space) in enumerate(_pairs(container.get("spaces", [])).items()):
        containers = _pairs(space.get("containerIDs", []))
        result.spaces.append(Space(
            space.get("title") or f"Space {index + 1}", _color(space, index), _icon(space),
            space_id=f"arc-{space_id}",
            pinned=build(containers.get("pinned"), pinned=True),
            today=build(containers.get("unpinned"), pinned=False),
            profile=_profile(space, names),
        ))

    for entry in container.get("topAppsContainerIDs", []):
        if isinstance(entry, str):
            result.favorites = [n for n in build(entry, pinned=True) if isinstance(n, Tab)]
            break


def _profile_names() -> dict[str, str]:
    """Arc's profiles: {folder ("Profile 1"): its name ("Work")}."""
    try:
        state = json.loads((ARC_DIR / "User Data" / "Local State").read_text())
    except (OSError, ValueError):
        return {}
    cache = state.get("profile", {}).get("info_cache", {})
    return {folder: info.get("name") or folder for folder, info in cache.items() if isinstance(info, dict)}


def _profile(space: dict, names: dict) -> str:
    """The Eclipse profile for an Arc Space: "default" for Arc's own, else one named like Arc's ("work")."""
    profile = space.get("profile") or {}
    custom = profile.get("custom", {}).get("_0", {}) if isinstance(profile.get("custom"), dict) else {}
    folder = custom.get("directoryBasename") if isinstance(custom, dict) else None
    if not folder or folder == "Default":
        return DEFAULT_PROFILE
    name = re.sub(r"[^a-z0-9_-]+", "-", str(names.get(folder, folder)).lower()).strip("-")
    return name or DEFAULT_PROFILE


def _read_settings() -> dict:
    """Arc's settings that Eclipse has too, as Eclipse's."""
    try:
        prefs = plistlib.loads(ARC_PREFERENCES.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}
    found = {}
    if prefs.get("autoArchiveTimeThreshold") in ARCHIVE_HOURS:
        found["archive_after_hours"] = ARCHIVE_HOURS[prefs["autoArchiveTimeThreshold"]]
    if isinstance(prefs.get("openExternalLinksInLittleBrowserEnabled"), bool):
        found["external_links"] = "little" if prefs["openExternalLinksInLittleBrowserEnabled"] else "tab"
    if isinstance(prefs.get("renameDownloadsEnabled"), bool):
        found["tidy_downloads"] = prefs["renameDownloadsEnabled"]
    if isinstance(prefs.get("renamePinnedTabsEnabled"), bool):
        found["tidy_titles"] = prefs["renamePinnedTabsEnabled"]
    return found


def _read_archive() -> list[dict]:
    """Arc's archived tabs, newest first (as many as Eclipse keeps)."""
    try:
        data = json.loads((ARC_DIR / "StorableArchiveItems.json").read_text())
    except (OSError, ValueError):
        return []
    found = []
    for entry in _pairs(data.get("items", [])).values():
        if not isinstance(entry, dict):
            continue
        tab = (entry.get("sidebarItem") or {}).get("data", {}).get("tab", {})
        url = tab.get("savedURL") or ""
        if not url.startswith(("https://", "http://")):
            continue
        space = (entry.get("source") or {}).get("space", {}).get("_0")
        when = entry.get("archivedAt")
        found.append({"url": url, "title": tab.get("savedTitle") or url,
                      "space_id": f"arc-{space}" if isinstance(space, str) else "",
                      "archived_at": when + APPLE_EPOCH_OFFSET if isinstance(when, (int, float)) else 0})
    found.sort(key=lambda e: e["archived_at"], reverse=True)
    return found[:ARCHIVE_LIMIT]


def _read_routes(space_ids: set[str]) -> list[dict]:
    """Air Traffic Control's rules that send a site to a particular Space (not "whichever's open", which
    Eclipse's rules don't have)."""
    try:
        data = json.loads((ARC_DIR / "StorableLinkRouting.json").read_text())
    except (OSError, ValueError):
        return []
    found = []
    for rule in data.get("rules", []) if isinstance(data.get("rules"), list) else []:
        space = (rule.get("destination") or {}).get("space", {}).get("_0")
        if not isinstance(space, str) or f"arc-{space}" not in space_ids:
            continue
        for part in rule.get("sourceComponents") or []:
            match = (part.get("componentType") or {}).get("urlMatch", {}).get("_1")
            if isinstance(match, str) and match.strip():
                found.append({"match": match.strip(), "space": f"arc-{space}"})
    return found


# --- Chromium databases ------------------------------------------------------------------

def _query(db: Path, tmp: Path, sql: str, params=()) -> list:
    if not db.exists():
        return []
    copy = tmp / db.name
    shutil.copy2(db, copy)
    # (closed after: `with sqlite3.connect()` only ends a transaction, and Windows can't delete an open copy)
    with closing(sqlite3.connect(f"file:{copy}?mode=ro", uri=True)) as conn:
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
    store.settings.update(arc.settings)
    known = {(e.get("url"), e.get("archived_at")) for e in store.archive}
    store.archive[:] = sorted(store.archive + [e for e in arc.archive if (e["url"], e["archived_at"]) not in known],
                              key=lambda e: e.get("archived_at") or 0, reverse=True)[:ARCHIVE_LIMIT]
    rules = store.settings.setdefault("air_traffic", [])
    rules += [r for r in arc.routes if r not in rules]
    store.settings[OFFERED] = True
    if arc.spaces:
        store.active_space_id = arc.spaces[0].id
    store._changed()


def _settings_summary(settings: dict) -> str:
    parts = []
    if "archive_after_hours" in settings:
        hours = settings["archive_after_hours"]
        parts.append("tabs archive " + ("never" if not hours else f"after {hours // 24} days" if hours >= 48
                                        else f"after {hours} hours"))
    if "external_links" in settings:
        parts.append("links from other apps open in " + ("a Little window" if settings["external_links"] == "little"
                                                         else "a new tab"))
    if settings.get("tidy_downloads") is not None:
        parts.append("tidier download names " + ("on" if settings["tidy_downloads"] else "off"))
    if settings.get("tidy_titles") is not None:
        parts.append("tidier tab titles " + ("on" if settings["tidy_titles"] else "off"))
    return "; ".join(parts)


class ArcImportFeature(Feature):
    """Bring Spaces, tabs, Favorites, history and icons over from Arc: from the welcome on a first launch
    (welcome.py), and any time from the File menu or ⌘T."""
    name = "arc_import"

    def ready(self):
        """A launch that missed the welcome (Eclipse had been opened before, from source say, with nothing of
        yours in it yet) still gets asked about Arc: once."""
        store = self.window.store
        if store.is_new or store.settings.get(OFFERED) or storage.throwaway() or not arc_installed():
            return
        if any(s.pinned or s.today for s in store.spaces) or any(s.id.startswith("arc-") for s in store.spaces):
            return  # (your own Spaces are here already: File → Import from Arc… whenever you like)
        store.settings[OFFERED] = True
        store.schedule_save()
        QTimer.singleShot(1500, self.import_from_arc)

    def commands(self):
        return [("Import from Arc", "ph.arrow-square-in", self.import_from_arc)]

    def menus(self, add, menus, bar):
        add(menus["File"], "Import from Arc…", "", self.import_from_arc)

    def import_from_arc(self):
        window = self.window
        if not arc_installed():
            window.toast("Couldn't find Arc's data on this Mac")
            return
        try:
            arc = read_arc()
        except (OSError, ValueError, KeyError) as error:
            QMessageBox.warning(window, "Import from Arc", f"Couldn't read Arc's data:\n{error}")
            return

        lines = [
            f"• {len(arc.spaces)} Spaces: " + ", ".join(s.name for s in arc.spaces),
            f"• {arc.tab_count} tabs, including {len(arc.favorites)} Favorites, in {arc.folder_count} folders",
            f"• {len(arc.history):,} history entries (for ⌘T suggestions) and site icons",
        ]
        if arc.archive:
            lines.append(f"• {len(arc.archive)} archived tabs (the latest), in Library → Archived Tabs")
        if arc.settings:
            lines.append("• Your settings: " + _settings_summary(arc.settings))
        if arc.routes:
            lines.append(f"• {len(arc.routes)} Air Traffic Control rule{'s' if len(arc.routes) != 1 else ''}")
        profiles = sorted({s.profile for s in arc.spaces} - {DEFAULT_PROFILE})
        if profiles:
            lines.append("• Profiles (their Spaces' own sign-ins, empty to start): " + ", ".join(profiles))
        note = ("You'll need to sign in to sites again: Arc's logins and cookies are encrypted "
                "with its own key and can't be copied.")
        if arc.extensions and window.feature("extensions") is not None:  # (somewhere for them to go)
            note += ("\n\nYour extensions (" + ", ".join(arc.extensions) + ") come separately: Settings → "
                     "Extensions → Add Extension → Import from Arc. Some may not fully work here.")
        box = QMessageBox(window)
        box.setWindowTitle("Import from Arc")
        box.setText(f"Bring your Arc setup into {brand.NAME}?")
        box.setInformativeText("\n".join(lines) + "\n\n" + note +
                               "\n\nImporting again later replaces these Spaces with Arc's current state.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        import_btn = box.button(QMessageBox.StandardButton.Yes)
        import_btn.setText("Import")
        box.setDefaultButton(import_btn)
        box.exec()
        if box.clickedButton() is not import_btn:
            return
        self.bring_in(arc)

    def bring_in(self, arc: ArcImport):
        """Put a read import in place: the tabs it replaces lose their pages, and the window catches up."""
        window = self.window
        before = list(window.store.all_tabs())
        apply(window.store, arc)
        remaining = set(map(id, window.store.all_tabs()))
        for tab in before:
            if id(tab) not in remaining:
                window._remove_view(tab)
        if window.current_tab is not None and id(window.current_tab) not in remaining:
            window.current_tab = None
        window.apply_theme()
        window._restore_space_tab()
        window.toast(f"Imported {len(arc.spaces)} Spaces and {arc.tab_count} tabs from Arc")


FEATURE = ArcImportFeature
