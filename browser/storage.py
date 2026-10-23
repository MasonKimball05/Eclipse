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


def write_json(path: Path, data: dict) -> None:
    # Write to a temp file and swap it in, so a crash mid-write can't corrupt the session
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


# --- Favicons -----------------------------------------------------------------

_icons: dict[str, QIcon | None] = {}


def _host_key(url: str) -> str:
    host = QUrl(url).host().lower()
    return host.removeprefix("www.")


def _icon_path(key: str) -> Path:
    folder = data_dir() / "favicons"
    folder.mkdir(exist_ok=True)
    return folder / (re.sub(r"[^a-z0-9.-]", "_", key) + ".png")


def favicon(url: str) -> QIcon | None:
    key = _host_key(url)
    if not key:
        return None
    if key not in _icons:
        path = _icon_path(key)
        _icons[key] = QIcon(str(path)) if path.exists() else None
    return _icons[key]


def save_favicon_png(url: str, data: bytes) -> None:
    # For favicons that arrive as raw image bytes (e.g. imported from another browser)
    key = _host_key(url)
    pixmap = QPixmap()
    if key and pixmap.loadFromData(data):
        pixmap.save(str(_icon_path(key)), "PNG")
        _icons[key] = QIcon(pixmap)


def save_favicon(url: str, icon: QIcon) -> None:
    key = _host_key(url)
    if not key or icon.isNull():
        return
    pixmap = icon.pixmap(64, 64)
    pixmap.save(str(_icon_path(key)), "PNG")
    _icons[key] = QIcon(pixmap)
