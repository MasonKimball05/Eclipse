# Native pages: windows drawn by SwiftUI (native/Sources) instead of Qt, on a Mac where the library is built.
# Python keeps each page's logic; the library only draws it. A page is opened with what it shows (JSON), updated
# with what changed, and tells Python what was done ("skip", "update", "close") through NativePage's signals.
#
# Everything here falls back quietly: off a Mac, with Qt not on Cocoa (offscreen tests), without the library (no
# Xcode tools), with an older library than this code expects, or with ECLIPSE_NATIVE=0, available() is False and
# callers use their Qt page.

import ctypes
import json
import os
import sys
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSignal

ABI = 2  # (must match native/Sources/Bridge.swift's)
LIBRARY_NAME = "libEclipseNative.dylib"
KEEP_BEHIND = False  # (the live tests: native windows open behind everything, as their Qt ones do)

_lib = None
_loaded = False
_pages: dict[int, "NativePage"] = {}
_callback = None  # (kept alive: ctypes frees a callback nothing refers to)


def library_paths() -> list[Path]:
    here = Path(__file__).resolve().parent
    paths = [here.parents[2] / "Frameworks" / LIBRARY_NAME,  # (the release app: Contents/Resources/app/browser)
             here.parent / "native" / "build" / LIBRARY_NAME]  # (a checkout: tools/build_native.py)
    if os.environ.get("ECLIPSE_NATIVE_LIB"):
        paths.insert(0, Path(os.environ["ECLIPSE_NATIVE_LIB"]))
    return paths


def _load():
    global _lib, _loaded, _callback
    if _loaded:
        return _lib
    _loaded = True
    path = next((p for p in library_paths() if p.exists()), None)
    if path is None:
        return None
    try:
        lib = ctypes.CDLL(str(path))
        lib.eclipse_native_abi.restype = ctypes.c_int32
        if lib.eclipse_native_abi() != ABI:
            return None  # (built from older or newer sources than this code: Qt's pages until it's rebuilt)
        lib.eclipse_native_open.argtypes = [ctypes.c_char_p]
        lib.eclipse_native_open.restype = ctypes.c_int32
        lib.eclipse_native_update.argtypes = [ctypes.c_int32, ctypes.c_char_p]
        lib.eclipse_native_update.restype = None
        lib.eclipse_native_front.argtypes = [ctypes.c_int32]
        lib.eclipse_native_front.restype = None
        lib.eclipse_native_close.argtypes = [ctypes.c_int32]
        lib.eclipse_native_close.restype = None
        lib.eclipse_native_is_open.argtypes = [ctypes.c_int32]
        lib.eclipse_native_is_open.restype = ctypes.c_bool
        lib.eclipse_native_key.argtypes = [ctypes.c_int32, ctypes.c_char_p, ctypes.c_bool]
        lib.eclipse_native_key.restype = ctypes.c_bool
        lib.eclipse_native_frame.argtypes = [ctypes.c_int32, ctypes.POINTER(ctypes.c_double)]
        lib.eclipse_native_frame.restype = ctypes.c_bool
        lib.eclipse_native_state.argtypes = [ctypes.c_int32]
        lib.eclipse_native_state.restype = ctypes.c_void_p  # (freed with free(), once read)
        lib.eclipse_native_window_number.argtypes = [ctypes.c_int32]
        lib.eclipse_native_window_number.restype = ctypes.c_long
        lib.eclipse_native_press.argtypes = [ctypes.c_int32, ctypes.c_char_p]
        lib.eclipse_native_press.restype = None
        lib.eclipse_native_embed.argtypes = [ctypes.c_char_p]
        lib.eclipse_native_embed.restype = ctypes.c_int32
        lib.eclipse_native_view.argtypes = [ctypes.c_int32]
        lib.eclipse_native_view.restype = ctypes.c_void_p
        lib.eclipse_native_ask.argtypes = [ctypes.c_char_p]
        lib.eclipse_native_ask.restype = ctypes.c_int32
        lib.eclipse_native_snapshot.argtypes = [ctypes.c_int32]
        lib.eclipse_native_snapshot.restype = ctypes.c_void_p  # (freed with free(), once read)
        callback_type = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
        _callback = callback_type(_on_event)
        lib.eclipse_native_set_callback.argtypes = [callback_type]
        lib.eclipse_native_set_callback.restype = None
        lib.eclipse_native_set_callback(_callback)
    except (OSError, AttributeError):
        return None
    _lib = lib
    return lib


def available() -> bool:
    if sys.platform != "darwin" or os.environ.get("ECLIPSE_NATIVE", "1") == "0":
        return False
    from . import macos
    return macos._cocoa() and _load() is not None


def _on_event(raw: bytes):
    try:
        event = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return
    page = _pages.get(event.get("page"))
    if page is None:
        return
    try:
        if event.get("event") == "closed":
            _pages.pop(page.id, None)
            page.id = 0
            page.closed.emit()
        elif event.get("event") == "action":
            page.action.emit(event.get("name", ""), event)
    except Exception:  # (an error mustn't unwind into Swift's stack: show it as Qt's callbacks would)
        sys.excepthook(*sys.exc_info())


def ask(spec: dict) -> int:
    """An alert answered before this returns (a sheet, the app's modal loop running meanwhile): the button's index,
    or -1 if it couldn't be shown."""
    lib = _load()
    return lib.eclipse_native_ask(json.dumps(spec).encode()) if lib else -1


def png(pixmap) -> str:
    """A picture (a QPixmap) as base64 PNG, for a native page."""
    import base64
    from PyQt6.QtCore import QBuffer, QIODevice
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode()


def theme_state(theme) -> dict:
    """What a page needs of the Space's look: light or dark, and its color (SwiftUI's tint)."""
    return {"dark": bool(theme.dark), "accent": theme.accent.name()}


def retheme(owner, theme):
    """The Space's look changed (its color, light or dark, another Space): every open page opened in it that belongs
    to `owner` (a form, What's New, the Update window…) takes the new one. Else a sheet stayed light over a window
    that had just gone dark."""
    state = theme_state(theme)
    for key, page in list(_pages.items()):
        if sip.isdeleted(page):  # (its owner gone without closing it: nothing of it to ask)
            _pages.pop(key, None)
            continue
        if not getattr(page, "follows_theme", False):
            continue
        parent = page.parent()
        while parent is not None and parent is not owner:
            parent = parent.parent()
        if parent is owner:
            page.update(state)


def note_sections(markdown: str) -> list[dict]:
    """Markdown notes ("## heading", "- line" with indented carry-on lines, plain lines) as the sections a native
    page lists: [{"heading": …, "lines": [{"text": inline Markdown, "bullet": bool}]}]."""
    sections = [{"heading": "", "lines": []}]
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            sections.append({"heading": line.lstrip("# ").strip(), "lines": []})
        elif line.startswith(("- ", "* ")):
            sections[-1]["lines"].append({"text": line[2:].strip(), "bullet": True})
        elif raw.startswith("  ") and sections[-1]["lines"]:
            sections[-1]["lines"][-1]["text"] += " " + line.strip()  # (a bullet carrying on)
        else:
            sections[-1]["lines"].append({"text": line.strip(), "bullet": False})
    return [s for s in sections if s["heading"] or s["lines"]]


class NativePage(QObject):
    """One open native page. action(name, event) for what's done on it; closed() once its window has gone."""
    action = pyqtSignal(str, dict)
    closed = pyqtSignal()

    def __init__(self, spec: dict, parent=None, embed=False):
        """`embed`: a page inside a Qt window (its view(), for QWindow.fromWinId), not a window of its own."""
        super().__init__(parent)
        lib = _load()
        if KEEP_BEHIND and not embed:
            spec = {**spec, "behind": True}
        opener = (lib.eclipse_native_embed if embed else lib.eclipse_native_open) if lib else None
        self.id = opener(json.dumps(spec).encode()) if opener else 0
        self.follows_theme = "dark" in spec and "accent" in spec  # (opened in the Space's look: it keeps to it)
        if self.id:
            _pages[self.id] = self

    def snapshot(self):
        """An embedded page as it looks now, as a QPixmap (null if it can't be had)."""
        import base64
        from PyQt6.QtGui import QPixmap
        picture = QPixmap()
        pointer = _lib.eclipse_native_snapshot(self.id) if self.id else None
        if pointer:
            try:
                picture.loadFromData(base64.b64decode(ctypes.string_at(pointer)), "PNG")
            finally:
                ctypes.CDLL(None).free(ctypes.c_void_p(pointer))
        return picture

    def view(self) -> int:
        """An embedded page's NSView, as a number (for QWindow.fromWinId), or 0."""
        return (_lib.eclipse_native_view(self.id) or 0) if self.id else 0

    def update(self, state: dict):
        if self.id:
            _lib.eclipse_native_update(self.id, json.dumps(state).encode())

    def front(self):
        if self.id:
            _lib.eclipse_native_front(self.id)

    def close(self):
        if self.id:
            _lib.eclipse_native_close(self.id)  # (closed() follows, from the window)

    def is_open(self) -> bool:
        return bool(self.id) and _lib.eclipse_native_is_open(self.id)

    def frame(self):
        """Where its window is, as a QRect (as Qt counts), or None."""
        from PyQt6.QtCore import QRect
        out = (ctypes.c_double * 4)()
        if not self.id or not _lib.eclipse_native_frame(self.id, out):
            return None
        return QRect(*(round(v) for v in out))

    # --- For tests ---------------------------------------------------------------------------------------------

    def state(self) -> dict:
        """What the page holds now (all Python sent it, merged)."""
        pointer = _lib.eclipse_native_state(self.id) if self.id else None
        if not pointer:
            return {}
        try:
            return json.loads(ctypes.string_at(pointer).decode())
        finally:
            ctypes.CDLL(None).free(ctypes.c_void_p(pointer))

    def window_number(self) -> int:
        return _lib.eclipse_native_window_number(self.id) if self.id else 0

    def press(self, name: str):
        """As if one of the page's buttons was pressed (its action goes to Python as a real press's does)."""
        if self.id:
            _lib.eclipse_native_press(self.id, name.encode())

    def key(self, key: str, shift=False) -> bool:
        """⌘ and `key` sent to its window as if pressed there; whether the window took it."""
        return bool(self.id) and _lib.eclipse_native_key(self.id, key.encode(), shift)
