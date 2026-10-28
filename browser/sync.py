# Sync: your Spaces (name, icon, colours, look), their pinned tabs and folders, and your Favorites, the same on
# each Mac (and PC) you turn it on on, through iCloud Drive: no account of Eclipse's, no server. Today's tabs
# stay on each Mac, as in Arc. Settings → General → Sync.
#
# Each device writes its own file in iCloud Drive's "Eclipse Sync" folder (so two never write the same file), and
# reads the others'. What's compared is a Space at a time (and Favorites, and the Spaces' order, each as one):
# whichever device changed it last wins, and a deletion counts as a change. Change one Space on one Mac and
# another on the other, and both changes stay; change the same Space on both before they've synced, and the
# later one wins. Before it first changes anything after Eclipse opens, it keeps a backup (in the data folder's
# "sync backups", the last five), restorable from File → Restore from Backup….
#
# The merging is plain data (state, snapshots, plans) so it can be tested as two Macs; SyncFeature applies a
# plan to the open window, keeping the pages of tabs that are still there.

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

from . import storage
from .features import Feature
from .models import FAVORITES, PINNED, Folder, Space, Tab, new_id, walk_tabs
from .space_share import _text, _url, color_of, icon_of, look_of

SETTING = "sync"
FORMAT = "eclipse-sync"
FOLDER = "Eclipse Sync"
ORDER = "order"  # (the Spaces' order: synced as an item of its own)
EVERY_MS = 20_000
BACKUPS = 5


# --- Where ------------------------------------------------------------------------------------------------

def cloud_folder() -> Path | None:
    """iCloud Drive's folder on this computer, or None if it isn't set up."""
    if os.environ.get("ECLIPSE_SYNC_DIR"):
        return Path(os.environ["ECLIPSE_SYNC_DIR"])
    home = Path.home()
    found = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" if sys.platform == "darwin" else \
        home / "iCloudDrive"  # (iCloud for Windows)
    return found if found.is_dir() else None


def sync_folder() -> Path | None:
    base = cloud_folder()
    return base / FOLDER if base is not None else None


def device_name() -> str:
    name = socket.gethostname().removesuffix(".local").replace("-", " ")
    return name[:60] or "This computer"


# --- What's synced ----------------------------------------------------------------------------------------

def _node(n) -> dict:
    if isinstance(n, Folder):
        d = {"type": "folder", "id": n.id, "name": n.name}
        if n.live:
            d["live"] = {k: v for k, v in n.live.items() if k != "seen"}  # (its items fill themselves on each Mac)
        else:
            d["children"] = [_node(c) for c in n.children]
        return d
    return {"id": n.id, "url": n.home_url or n.url, "title": n.title, "custom_title": n.custom_title}


def snapshot(store) -> dict:
    """{key: data} for everything synced: "space:<id>" for each Space, FAVORITES, and ORDER."""
    items = {f"space:{s.id}": {"name": s.name, "color": s.color, "icon": s.icon, "theme": s.theme,
                               "pinned": [_node(n) for n in s.pinned]} for s in store.spaces}
    items[FAVORITES] = [_node(t) for t in store.favorites]
    items[ORDER] = [s.id for s in store.spaces]
    return items


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def new_state() -> dict:
    return {"device": new_id(), "known": {}, "deleted": {}}


def note_local(state: dict, items: dict, now: float) -> bool:
    """What changed here since last time (each changed item, or deleted one, stamped `now`). True if anything."""
    changed = False
    for key, data in items.items():
        h = digest(data)
        if state["known"].get(key, {}).get("hash") != h:
            state["known"][key] = {"hash": h, "modified": now}
            state["deleted"].pop(key, None)
            changed = True
    for key in [k for k in state["known"] if k not in items]:
        state["deleted"][key] = now
        del state["known"][key]
        changed = True
    return changed


def device_file(state: dict, items: dict, now: float) -> dict:
    return {"format": FORMAT, "version": 1, "device": state["device"], "name": device_name(), "written": now,
            "items": {k: {"modified": state["known"][k]["modified"], "data": d} for k, d in items.items()},
            "deleted": dict(state["deleted"])}


def merge(state: dict, remotes: list[dict]) -> dict:
    """What to change here from the other devices' files: {"apply": {key: data}, "delete": [keys]}, newest change
    of each item winning. The state is updated as if it's done (so the next look doesn't do it again)."""
    best = {}  # key → (modified, data or None for deleted, device)
    for remote in remotes:
        for key, item in (remote.get("items") or {}).items():
            when = item.get("modified") if isinstance(item, dict) else None
            if isinstance(when, (int, float)) and (key not in best or when > best[key][0]):
                best[key] = (when, item.get("data"), remote.get("device"))
        for key, when in (remote.get("deleted") or {}).items():
            if isinstance(when, (int, float)) and (key not in best or when > best[key][0]):
                best[key] = (when, None, remote.get("device"))
    plan = {"apply": {}, "delete": []}
    for key, (when, data, _device) in best.items():
        mine = state["known"].get(key, {}).get("modified", state["deleted"].get(key, float("-inf")))
        if when <= mine:
            continue
        if data is None:
            if key in state["known"]:
                plan["delete"].append(key)
                del state["known"][key]
            state["deleted"][key] = when
        else:
            h = digest(data)
            if state["known"].get(key, {}).get("hash") != h:
                plan["apply"][key] = data
            state["known"][key] = {"hash": h, "modified": when}
            state["deleted"].pop(key, None)
    return plan


# --- Reading another device's (made safe: it's yours, but it came through the cloud) ---------------------

def _clean_node(d, depth=0):
    if not isinstance(d, dict) or not isinstance(d.get("id"), str) or depth > 8:
        return None
    if d.get("type") == "folder":
        out = {"type": "folder", "id": d["id"][:64], "name": _text(d.get("name"), 80) or "Folder"}
        if isinstance(d.get("live"), dict):
            live = {k: v for k, v in d["live"].items() if isinstance(k, str) and isinstance(v, (str, int, float, bool))}
            if live.get("kind") == "feed" and not _url(live.get("url")):
                return None
            out["live"] = live
        else:
            out["children"] = [c for c in (_clean_node(c, depth + 1) for c in d.get("children") or []) if c]
        return out
    url = _url(d.get("url"))
    if url is None:
        return None
    custom = d.get("custom_title")
    return {"id": d["id"][:64], "url": url, "title": _text(d.get("title"), 300),
            "custom_title": _text(custom, 300) if isinstance(custom, str) else None}


def clean_item(key: str, data):
    """An item from another device's file, checked, or None if it doesn't make sense."""
    if key == ORDER:
        return [i[:64] for i in data if isinstance(i, str)] if isinstance(data, list) else None
    if key == FAVORITES:
        return [n for n in (_clean_node(t) for t in data) if n and "url" in n] if isinstance(data, list) else None
    if key.startswith("space:") and isinstance(data, dict):
        return {"name": _text(data.get("name"), 60) or "Space", "color": color_of(data.get("color")) or "#6b7280",
                "icon": icon_of(data.get("icon")), "theme": look_of(data.get("theme")),
                "pinned": [n for n in (_clean_node(c) for c in data.get("pinned") or []) if n]}
    return None


def read_remotes(folder: Path, mine: str) -> list[dict]:
    """The other devices' files in the sync folder (not yet downloaded ones are asked for, and read next time)."""
    found = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if path.stem == mine:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.stat().st_size < 20_000_000 else None
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            continue
        items = {}
        for key, item in (data.get("items") or {}).items():
            clean = clean_item(key, item.get("data")) if isinstance(item, dict) else None
            if clean is not None and isinstance(item.get("modified"), (int, float)):
                items[key] = {"modified": item["modified"], "data": clean}
        deleted = {k: v for k, v in (data.get("deleted") or {}).items() if isinstance(v, (int, float))}
        found.append({"device": data.get("device"), "name": _text(data.get("name"), 60), "items": items,
                      "deleted": deleted, "written": data.get("written", 0)})
    if sys.platform == "darwin" and os.path.exists("/usr/bin/brctl"):
        for placeholder in folder.glob(".*.json.icloud") if folder.is_dir() else []:
            subprocess.Popen(["/usr/bin/brctl", "download", str(placeholder)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)  # (a list, never a shell: the name came from elsewhere)
    return found


# --- Applying to the open window ---------------------------------------------------------------------------

def build_nodes(nodes: list, pool: dict, live_children: dict, avoid=frozenset()) -> list:
    """Tabs and folders from synced data, reusing the tab objects (and so the open pages) already here. A new
    tab whose id a tab here already has (one of Today's) gets an id of its own."""
    out = []
    for d in nodes:
        if d.get("type") == "folder":
            if d.get("live"):
                live = {**d["live"], "seen": []}
                old = live_children.get(d["id"])
                folder = Folder(d["name"], old[0] if old else [], d["id"], True, old[1] if old else live)
                if old:
                    folder.live = {**old[1], **d["live"]}
            else:
                folder = Folder(d["name"], build_nodes(d.get("children", []), pool, live_children, avoid), d["id"])
            out.append(folder)
            continue
        tab = pool.pop(d["id"], None)
        if tab is None:
            tab = Tab(d["url"], d["title"], home_url=d["url"], custom_title=d.get("custom_title"),
                      tab_id=new_id() if d["id"] in avoid else d["id"])
        else:
            if tab.view is None:
                tab.url = d["url"]  # (asleep or never opened: it opens where it's pinned to)
            tab.home_url = d["url"]
            tab.custom_title = d.get("custom_title")
            tab.title = tab.title or d["title"]
        out.append(tab)
    return out


class Sync(QObject):
    """Turned on: looks every 20 seconds (and soon after opening) for changes here and on other devices."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.store = window.store
        self.state = storage.read_json(self._state_path()) or new_state()
        self.last = None  # (when it last synced, and with which devices)
        self.devices: list[str] = []
        self._backed_up = False
        self._timer = QTimer(self, interval=EVERY_MS, timeout=self.run)
        self.refresh_setting()

    @staticmethod
    def _state_path():
        return storage.data_dir() / "sync-state.json"

    def on(self) -> bool:
        return bool(self.store.settings.get(SETTING)) and sync_folder() is not None

    def refresh_setting(self):
        if self.on():
            self._timer.start()
            QTimer.singleShot(3000, self.run)
        else:
            self._timer.stop()

    def others(self) -> list[dict]:
        folder = sync_folder()
        return read_remotes(folder, self.state["device"]) if folder is not None else []

    def run(self, send_only=False):
        """One look: what changed here goes up, what changed elsewhere comes down (not when quitting)."""
        folder = sync_folder()
        if not self.on() or folder is None:
            return
        now = time.time()
        if note_local(self.state, snapshot(self.store), now) or not (folder / f"{self.state['device']}.json").exists():
            self._write(folder, now)
        if send_only:
            storage.write_json(self._state_path(), self.state)
            return
        remotes = self.others()
        self.devices = [r["name"] for r in remotes if r.get("name")]
        plan = merge(self.state, remotes)
        if plan["apply"] or plan["delete"]:
            self._back_up()
            self.apply(plan)
            note_local(self.state, snapshot(self.store), now)  # (what's here now is what came: not a change of ours)
            self._write(folder, now)
        storage.write_json(self._state_path(), self.state)
        self.last = now

    def first_look(self) -> dict | None:
        """Turning sync on: if another device already syncs, {"name", "spaces"} of the one that wrote last, to
        ask whether to use its Spaces here or combine them with these."""
        if self.state["known"]:
            return None  # (synced before: carries on)
        remotes = [r for r in self.others() if any(k.startswith("space:") for k in r["items"])]
        if not remotes:
            return None
        latest = max(remotes, key=lambda r: r.get("written") or 0)
        return {"name": latest["name"] or "your other computer",
                "spaces": [i["data"]["name"] for k, i in latest["items"].items() if k.startswith("space:")]}

    def adopt(self):
        """Use the other devices' Spaces here in place of these (kept in a backup first)."""
        remotes = self.others()
        plan = merge(self.state, remotes)
        theirs = {k for r in remotes for k in r["items"] if k.startswith("space:")}
        plan["delete"] = [f"space:{s.id}" for s in self.store.spaces if f"space:{s.id}" not in theirs]
        self._back_up()
        self.apply(plan)
        self.state["known"] = {}  # (what's here now is what they have: stamped as theirs below)
        note_local(self.state, snapshot(self.store), 0)
        storage.write_json(self._state_path(), self.state)

    def _write(self, folder, now):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.state['device']}.json"
        temporary = path.with_suffix(".writing")
        temporary.write_text(json.dumps(device_file(self.state, snapshot(self.store), now), ensure_ascii=False))
        os.replace(temporary, path)  # (never half a file for another device to read)

    def _back_up(self):
        if self._backed_up:
            return
        from . import backup
        folder = storage.data_dir() / "sync backups"
        folder.mkdir(exist_ok=True)
        self.store.save()
        backup.create(folder / time.strftime("before-sync-%Y-%m-%d-%H%M%S.zip"))
        for old in sorted(folder.glob("before-sync-*.zip"))[:-BACKUPS]:
            old.unlink(missing_ok=True)
        self._backed_up = True

    def apply(self, plan: dict):
        """Change the window's Spaces and Favorites to what came, keeping the pages of tabs that are still there."""
        store, window = self.store, self.window
        was_active = store.active_space_id
        before = {t.id: t for t in list(store.favorites) + [t for s in store.spaces for t in walk_tabs(s.pinned)]}
        live_children = {f.id: (f.children, f.live) for s in store.spaces
                         for f in _folders(s.pinned) if f.live}
        pool = dict(before)
        avoid = {t.id for s in store.spaces for t in walk_tabs(s.today)}
        # Tabs staying where they are (in Spaces and Favorites nothing changed in) aren't free to be reused
        changed = set(plan["apply"]) | set(plan["delete"])
        for space in store.spaces:
            if f"space:{space.id}" not in changed:
                for t in walk_tabs(space.pinned):
                    pool.pop(t.id, None)
        if FAVORITES not in changed:
            for t in store.favorites:
                pool.pop(t.id, None)
        for key, data in plan["apply"].items():
            if key == FAVORITES:
                store.favorites = build_nodes(data, pool, live_children, avoid)
            elif key.startswith("space:"):
                space_id = key.removeprefix("space:")
                space = store.space(space_id)
                if space is None:
                    space = Space(data["name"], data["color"], data["icon"], space_id)
                    store.spaces.append(space)
                space.name, space.color, space.icon = data["name"], data["color"], data["icon"]
                space.theme = data.get("theme") or {}
                space.pinned = build_nodes(data["pinned"], pool, live_children, avoid)
        # (after what came is in: so a Space can go even when it was the last one here, replaced)
        for key in plan["delete"]:
            space = store.space(key.removeprefix("space:")) if key.startswith("space:") else None
            if space is not None and len(store.spaces) > 1:
                if space is store.active_space:
                    other = next(s for s in store.spaces if s is not space)
                    store.active_space_id = other.id
                for tab in walk_tabs(space.today):
                    window._remove_view(tab)
                store.spaces.remove(space)
        if ORDER in plan["apply"]:
            order = {space_id: i for i, space_id in enumerate(plan["apply"][ORDER])}
            store.spaces.sort(key=lambda s: order.get(s.id, len(order)))
        # Tabs that went everywhere else: their pages go too
        current = window.current_tab
        gone = [t for t in pool.values() if t.id in before]
        for tab in gone:
            store.remove_from_split(tab)
            window._remove_view(tab)
        if store.active_space is None:
            store.active_space_id = store.spaces[0].id
        store._changed()
        window.apply_theme()
        if store.active_space_id != was_active:
            window._restore_space_tab()  # (the Space you were in went: you're in another now, with its tab)
        elif current is not None and current in gone:
            window._activate_fallback()


def _folders(nodes):
    for n in nodes:
        if isinstance(n, Folder):
            yield n
            yield from _folders(n.children)


class SyncFeature(Feature):
    name = "sync"

    def __init__(self, window):
        super().__init__(window)
        self.sync = Sync(window)

    def shutdown(self):
        if self.sync.on():
            self.sync.run(send_only=True)  # (the last changes go up before quitting)


FEATURE = SyncFeature
