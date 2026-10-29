# Sharing a Space: its name, icon, colours and pinned tabs (in their folders) as a small file, "Work.eclipse-space",
# to AirDrop, message or mail to someone. Opening it in Eclipse (double-click, drag it onto the Dock icon, or
# File → Import Space…) adds the Space after asking. What's shared is only what you see: the addresses of the
# pinned tabs and their names. Not Today's tabs, not where you'd scrolled, not the profile (cookies, sign-ins),
# and not GitHub folders (they show your own pull requests). A feed Live Folder comes along as its feed, which
# fills itself on the other Mac.
#
# A shared file comes from someone else, so nothing in it is trusted: only web addresses, names cut to length
# and without control characters, colours that are colours, a known look.

import json
import re
import unicodedata
from pathlib import Path

from .models import Folder, Space, Tab, walk_tabs
from .page_actions import not_reserved
from .theme import DEFAULT_SPACE_THEME

FORMAT = "eclipse-space"
SUFFIX = ".eclipse-space"
TYPE_ID = "com.masonkimball.eclipse.space"  # (what macOS knows the files as: tools/build_app.py)
MAX_BYTES = 2_000_000
MAX_TABS = 500
MAX_DEPTH = 6
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
LINE_ICON = re.compile(r"ph\.[a-z0-9-]{1,40}")


def export(space: Space) -> dict:
    """What's shared of a Space."""
    def node(n):
        if isinstance(n, Folder):
            if n.live:
                if n.live.get("kind") != "feed" or not n.live.get("url"):
                    return None  # (GitHub folders are yours: the other Mac would ask GitHub as its own person)
                return {"type": "folder", "name": n.name, "live": {"kind": "feed", "url": n.live["url"]}}
            return {"type": "folder", "name": n.name, "children": [c for c in map(node, n.children) if c]}
        url = n.home_url or n.url
        return {"url": url, "title": n.custom_title or n.title} if url.startswith(("https://", "http://")) else None
    theme = {k: v for k, v in (space.theme or {}).items() if k in DEFAULT_SPACE_THEME}
    return {"format": FORMAT, "version": 1, "name": space.name, "icon": space.icon, "color": space.color,
            "theme": theme, "pinned": [c for c in map(node, space.pinned) if c]}


def write(space: Space, folder: Path) -> Path:
    """The Space as a file in `folder`, named after it."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', " ", space.name).strip(" .") or "Space"
    path = Path(folder) / f"{not_reserved(name[:60])}{SUFFIX}"  # ("Aux" is a device on Windows: "Aux_")
    path.write_text(json.dumps(export(space), ensure_ascii=False, indent=1))
    return path


def _text(value, limit) -> str:
    text = value if isinstance(value, str) else ""
    text = "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf"))  # (no control or bidi tricks)
    return " ".join(text.split())[:limit]


def _url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or not value.startswith(("https://", "http://")):
        return None  # (never javascript:, file:, data:, an app's address or an extension's page)
    return value if not any(c.isspace() or unicodedata.category(c) == "Cc" for c in value) else None


def parse(data) -> dict:
    """A shared Space made safe: {"name", "icon", "color", "theme", "pinned"}, the nodes as plain dicts. Raises
    ValueError if it isn't one."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("That isn't a shared Space.")
    count = [0]

    def nodes(items, depth):
        found = []
        for item in items if isinstance(items, list) else []:
            if count[0] >= MAX_TABS or not isinstance(item, dict):
                break
            if item.get("type") == "folder":
                live = item.get("live")
                if isinstance(live, dict):
                    url = _url(live.get("url"))
                    if url:
                        found.append({"type": "folder", "name": _text(item.get("name"), 60) or "Live Folder",
                                      "live": {"kind": "feed", "url": url}})
                elif depth < MAX_DEPTH:
                    found.append({"type": "folder", "name": _text(item.get("name"), 60) or "Folder",
                                  "children": nodes(item.get("children"), depth + 1)})
                continue
            url = _url(item.get("url"))
            if url:
                count[0] += 1
                found.append({"url": url, "title": _text(item.get("title"), 200)})
        return found
    return {"name": _text(data.get("name"), 60) or "Shared Space", "icon": icon_of(data.get("icon")),
            "color": color_of(data.get("color")), "theme": look_of(data.get("theme")), "pinned": nodes(data.get("pinned"), 1)}


def icon_of(value) -> str:
    """An emoji, or one of the line icons ("ph.planet"), or ""."""
    icon = value if isinstance(value, str) else ""
    return icon if LINE_ICON.fullmatch(icon) or (len(icon) <= 8 and not any(c.isascii() for c in icon)) else ""


def color_of(value) -> str | None:
    return value if isinstance(value, str) and COLOR.fullmatch(value) else None


def look_of(theme) -> dict:
    """A Space's look (theme.py's colours, intensity, light or dark, grain), only what makes sense."""
    theme = theme if isinstance(theme, dict) else {}
    look = {}
    colors = [c for c in theme.get("colors", []) if isinstance(c, str) and COLOR.fullmatch(c)][:4] \
        if isinstance(theme.get("colors"), list) else []
    if colors:
        look["colors"] = colors
    if isinstance(theme.get("intensity"), (int, float)) and not isinstance(theme["intensity"], bool):
        look["intensity"] = min(max(float(theme["intensity"]), 0.0), 1.0)
    if theme.get("mode") in ("auto", "light", "dark"):
        look["mode"] = theme["mode"]
    if isinstance(theme.get("grain"), bool):
        look["grain"] = theme["grain"]
    return look


def read(path: Path) -> dict:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("That file is too big to be a shared Space.")
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:  # (nested too deep to read)
        raise ValueError("That isn't a shared Space.") from error


def build(shared: dict, color: str) -> Space:
    """A new Space (with new ids throughout) from a parsed one. `color` if it didn't bring one."""
    def node(d):
        if d.get("type") == "folder":
            if d.get("live"):
                return Folder(d["name"], [], live={**d["live"], "seen": []})
            return Folder(d["name"], [node(c) for c in d["children"]])
        return Tab(d["url"], d["title"], home_url=d["url"])
    return Space(shared["name"], shared["color"] or color, shared["icon"], pinned=[node(d) for d in shared["pinned"]],
                 theme=shared["theme"] or None)


def summary(shared: dict) -> str:
    """"12 pinned tabs in 3 folders", for the question before it's added."""
    space = build(shared, "#000000")
    tabs = len(list(walk_tabs(space.pinned)))
    folders = sum(1 for d in _walk(shared["pinned"]) if d.get("type") == "folder")
    text = f"{tabs} pinned tab{'s' if tabs != 1 else ''}"
    return text + (f" in {folders} folder{'s' if folders != 1 else ''}" if folders else "")


def _walk(nodes):
    for d in nodes:
        yield d
        yield from _walk(d.get("children", []))
