# Everything that touches the disk lives here: where the app keeps its data,
# reading/writing the session JSON, and a small favicon cache keyed by host.

import json
import os
import re
from pathlib import Path

from PyQt6.QtCore import QStandardPaths, QUrl
from PyQt6.QtGui import QIcon, QPixmap


def data_dir() -> Path:
    # PYBROWSER_DATA_DIR lets you run a throwaway profile without touching your real one
    override = os.environ.get("PYBROWSER_DATA_DIR")
    if override:
        path = Path(override)
    else:
        path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_path() -> Path:
    return data_dir() / "session.json"


def profile_dir(name="default") -> Path:
    # The default profile keeps its original location; extra ones live under profiles/
    path = data_dir() / "profile" if name == "default" else data_dir() / "profiles" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Keep the broken file around so nothing is silently lost, then start fresh
        path.replace(path.with_suffix(".corrupt.json"))
        return None


def private_folder(folder: Path) -> Path:
    """A folder only your account can open, and the files in it only you can read (made if it isn't there)."""
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    for item in folder.iterdir():
        if item.is_file():
            os.chmod(item, 0o600)
    return folder


def previous_copy(path: Path) -> Path:
    return path.with_suffix(".prev.json")


def write_json(path: Path, data: dict, keep_previous=False, private=False) -> None:
    # Write to a temp file and swap it in, so a crash mid-write can't corrupt the session.
    # keep_previous: the last good version stays next to it, in case this one turns out unreadable.
    # private: only your account can read it (passwords, passkeys: encrypted anyway, but nobody else's business)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(data, indent=1)
    if private:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # (whatever it was before, and whatever the umask says)
        with os.fdopen(fd, "w") as f:
            f.write(text)
    else:
        tmp.write_text(text)
    if keep_previous and path.exists():
        os.replace(path, previous_copy(path))
    os.replace(tmp, path)


# --- Favicons -----------------------------------------------------------------
#
# One icon per site, except where part of a site has its own: Google Maps' pin isn't Google's "G", and a
# Maps tab mustn't turn your Google Favorite into a pin. So a page whose icon differs from its site's keeps
# it for its section ("google.com/maps"); the site's own comes from its home page (or the first page seen).

_icons: dict[str, QIcon | None] = {}


def _host_key(url: str) -> str:
    host = QUrl(url).host().lower()
    return host.removeprefix("www.")


def _section_key(url: str) -> str:
    """"google.com/maps" for any page under google.com/maps/…, "reddit.com/r/python" for a subreddit; "" for a
    site's home page."""
    host = _host_key(url)
    parts = [p.lower() for p in QUrl(url).path().strip("/").split("/") if p]
    if not host or not parts or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", parts[0]):
        return ""
    if len(parts[0]) <= 2 and len(parts) > 1 and re.fullmatch(r"[a-z0-9_-]{1,40}", parts[1]):
        return f"{host}/{parts[0]}/{parts[1]}"  # ("reddit.com/r/python": each subreddit has its own)
    return f"{host}/{parts[0]}"


def _icon_path(key: str) -> Path:
    folder = data_dir() / "favicons"
    folder.mkdir(exist_ok=True)
    return folder / (re.sub(r"[^a-z0-9.-]", "_", key) + ".png")


def _cached(key: str) -> QIcon | None:
    if key not in _icons:
        path = _icon_path(key)
        _icons[key] = QIcon(str(path)) if path.exists() else None
    return _icons[key]


def favicon(url: str) -> QIcon | None:
    host, section = _host_key(url), _section_key(url)
    if not host:
        return None
    return (_cached(section) if section else None) or _cached(host)


def save_favicon_png(url: str, data: bytes) -> None:
    # For favicons that arrive as raw image bytes (e.g. imported from another browser)
    pixmap = QPixmap()
    if pixmap.loadFromData(data):
        save_favicon(url, QIcon(pixmap))


def save_favicon(url: str, icon: QIcon) -> None:
    host, section = _host_key(url), _section_key(url)
    if not host or icon.isNull():
        return
    pixmap = icon.pixmap(64, 64)
    if not section or _cached(host) is None:  # (the home page, or the first we've seen of the site)
        _write(host, pixmap)
    if section:
        site = _cached(host)
        if site is not None and _same(site.pixmap(64, 64), pixmap):
            if _cached(section) is not None:  # (no longer different: the site's own does)
                _icon_path(section).unlink(missing_ok=True)
                _icons[section] = None
        else:
            _write(section, pixmap)


def _same(a: QPixmap, b: QPixmap) -> bool:
    from PyQt6.QtGui import QImage
    fmt = QImage.Format.Format_ARGB32
    return a.toImage().convertToFormat(fmt) == b.toImage().convertToFormat(fmt)


def _write(key, pixmap):
    pixmap.save(str(_icon_path(key)), "PNG")
    _icons[key] = QIcon(pixmap)
