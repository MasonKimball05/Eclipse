# Your own keyboard shortcuts: any menu item's shortcut can be changed, removed, or given one it didn't have
# (Settings → Keyboard Shortcuts: click a shortcut and type the new one). Only the changes are kept, in the
# "shortcuts" setting, {"Menu › Item": "Ctrl+Shift+N"} ("" for none), so a new version's own shortcuts still
# reach the items you haven't touched.
#
# Each item remembers the shortcut it was built with, so Reset puts that back.
#
# Shortcuts are written the Mac's way ("Ctrl" is ⌘, "Meta" the Control key), and here() makes them Windows' (and
# Linux's): there's no ⌘, so the Control key's ones become Ctrl, except ⌃1–⌃9 (the Spaces), which become Alt+1–9
# since Ctrl+1–9 are the tabs, as in every browser; and the Library is Ctrl+H, as Ctrl+Y is Redo there. Text that
# names a key ("Reopen it with ⇧⌘T") goes through for_here(), which says it as the key really is there
# ("Ctrl+Shift+T"), along with what Windows calls a Mac's things (Finder, the Keychain).

import re
import sys

from PyQt6.QtCore import QEvent, QKeyCombination, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QPushButton, QToolTip, QWidget

SETTING = "shortcuts"
DEFAULT = "eclipse_default_shortcut"
DYNAMIC = "eclipse_menu_list"  # (an item of a list a menu fills itself, like the Window menu's windows: no shortcut)
# macOS's own (quit, hide, minimise, switching apps and windows, Spotlight): Eclipse never sees some of them,
# and taking the rest would surprise you. ("Ctrl" is ⌘ on a Mac.)
RESERVED = {"Ctrl+Q", "Ctrl+H", "Ctrl+Alt+H", "Ctrl+M", "Ctrl+Tab", "Ctrl+Shift+Tab", "Ctrl+Space", "Ctrl+`",
            "Ctrl+Shift+3", "Ctrl+Shift+4", "Ctrl+Shift+5"} if sys.platform == "darwin" else {"Alt+F4", "Alt+Tab"}
MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
                 Qt.Key.Key_CapsLock}
ANY_MODIFIER = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier |
                Qt.KeyboardModifier.MetaModifier)


# A Mac shortcut's other key on Windows, where it would be taken (the Mac's key → Windows')
WINDOWS_INSTEAD = {"Ctrl+Y": "Ctrl+H"}


def here(keys: str) -> str:
    """A shortcut, written the Mac's way, as it's bound on this system (see the top)."""
    if sys.platform == "darwin" or not keys:
        return keys
    parts = keys.split("+")
    if "Meta" in parts:
        key = parts[-1]
        parts = [("Alt" if key.isdigit() and len(key) == 1 else "Ctrl") if p == "Meta" else p for p in parts]
        parts = [p for i, p in enumerate(parts) if p not in parts[:i] or i == len(parts) - 1]  # (Ctrl once)
    keys = "+".join(parts)
    return WINDOWS_INSTEAD.get(keys, keys)


# Mac key symbols in text: the modifiers (in any order), then a key, or none ("Hold ⌃, tap Tab")
_MODIFIERS = {"⌃": "Meta", "⌥": "Alt", "⇧": "Shift", "⌘": "Ctrl"}
_KEY_NAMES = {"↩": "Enter", "⌫": "Backspace", "⌦": "Delete", "⎋": "Esc", "⇥": "Tab"}
_RUN = re.compile(r"([⌃⌥⇧⌘]+)(-(?:click|drag|scroll)|Tab|Space|Return|Enter|Esc|F\d{1,2}|[A-Za-z0-9]\b|"
                  r"[\[\]=\-/`'\\.]|,(?!\s)|[↩⌫⌦⎋⇥←→↑↓])?")
_WORDS = [(re.compile(r"\bthe Keychain\b"), "Credential Manager"), (re.compile(r"\bKeychain\b"), "Credential Manager"),
          (re.compile(r"\bFinder\b"), "File Explorer"), (re.compile(r"\bthis Mac\b"), "this PC"),
          (re.compile(r"\byour Mac\b"), "your PC")]
_ORDER = ["Ctrl", "Alt", "Shift"]


def _said(match) -> str:
    mods = [_MODIFIERS[c] for c in match.group(1)]
    key = match.group(2)
    if not key:
        return "+".join(dict.fromkeys(here("+".join(mods + ["X"])).split("+")[:-1]))  # ("Hold ⌃": Hold Ctrl)
    if key.startswith("-") and len(key) > 1:  # ("⌘-click"; a lone "-" is the key itself: ⌘- zooms out)
        return "+".join(dict.fromkeys(here("+".join(mods + ["X"])).split("+")[:-1])) + "+" + key[1:]
    bound = here("+".join(mods + [_KEY_NAMES.get(key, key.upper() if len(key) == 1 else key)])).split("+")
    return "+".join(sorted(bound[:-1], key=lambda m: _ORDER.index(m) if m in _ORDER else 9) + bound[-1:])


def for_here(text: str) -> str:
    """Text written for a Mac, as it reads on this system: "⇧⌘T" is "Ctrl+Shift+T" on Windows (the key as it's
    really bound there: here()), "↩" is Enter, Finder is File Explorer. Unchanged on a Mac."""
    if sys.platform == "darwin" or not text:
        return text
    text = _RUN.sub(_said, text)
    text = re.sub(r"↩", "Enter", text)
    for pattern, words in _WORDS:
        text = pattern.sub(words, text)
    return text


class NativeTooltips(QObject):
    """Every tooltip as for_here() says it, off a Mac (installed on the app: app.py), so a button's "Reload (⌘R)"
    reads "Reload (Ctrl+R)" on Windows without each one being written twice."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTip and isinstance(obj, QWidget):
            tip = obj.toolTip()
            said = for_here(tip)
            if said != tip:
                QToolTip.showText(event.globalPos(), said, obj)
                return True
        return False


def portable(sequence: QKeySequence) -> str:
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def native(keys: str) -> str:
    """How a shortcut reads on this system: "⇧⌘N" for "Ctrl+Shift+N" on a Mac."""
    return QKeySequence(keys).toString(QKeySequence.SequenceFormat.NativeText)


def menu_items(bar) -> list[tuple[str, str, str, object]]:
    """(key, menu, name, action) for every item of the menu bar that can have a shortcut, in menu order. A
    second item with the same name in a menu (Tabs has two Next Tabs) is "Name 2"."""
    found, seen = [], {}

    def walk(menu, group, depth):
        for action in menu.actions():
            if action.isSeparator():
                continue
            if action.menu() is not None:
                walk(action.menu(), group, depth + 1)
                continue
            if action.property(DYNAMIC):
                continue
            if depth > 1 and action.property(DEFAULT) is None and action.shortcut().isEmpty():
                continue  # (a sub-menu's items come and go: Recently Closed, Open Links In)
            name = action.text().replace("&", "")
            base = f"{group} › {name}"
            seen[base] = seen.get(base, 0) + 1
            found.append((base if seen[base] == 1 else f"{base} {seen[base]}", group, name, action))
    for top in bar.actions():
        if top.menu() is not None:
            walk(top.menu(), top.text().replace("&", ""), 1)
    return found


def default_of(action) -> str:
    value = action.property(DEFAULT)
    return portable(action.shortcut()) if value is None else value


def apply(bar, overrides: dict) -> int:
    """Give every menu item its shortcut: yours if you changed it, else the one it was built with. How many
    are yours."""
    changed = 0
    for key, _group, _name, action in menu_items(bar):
        if action.property(DEFAULT) is None:
            action.setProperty(DEFAULT, portable(action.shortcut()))
        keys = overrides.get(key, action.property(DEFAULT))
        if key in overrides:
            changed += 1
        if portable(action.shortcut()) != keys:
            action.setShortcut(QKeySequence(keys))
    return changed


def user_of(bar, keys: str, skip: str = "") -> tuple[str, str] | None:
    """(key, name) of the menu item that has this shortcut now, other than `skip`."""
    if not keys:
        return None
    for key, _group, name, action in menu_items(bar):
        if key != skip and portable(action.shortcut()) == keys:
            return key, name
    return None


def problem(keys: str) -> str:
    """Why this can't be a shortcut ("" if it can)."""
    if keys in RESERVED:
        return f"{native(keys)} belongs to {'macOS' if sys.platform == 'darwin' else 'Windows'}."
    return ""


def changed(overrides: dict, key: str, keys: str, default: str) -> dict:
    """The setting after giving `key` this shortcut: a change back to its own default isn't kept."""
    updated = dict(overrides)
    if keys == default:
        updated.pop(key, None)
    else:
        updated[key] = keys
    return updated


class KeyRecorder(QPushButton):
    """A key cap you click and then type a new shortcut into. Esc leaves it as it was; Delete (on its own)
    removes it. A shortcut needs ⌘, ⌥ or ⌃ (or is a function key), so ordinary typing can't be one."""

    chosen = pyqtSignal(str)

    def __init__(self, keys: str, parent=None):
        super().__init__(parent)
        self.setObjectName("KeyCap")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.keys = keys
        self.recording = False
        self._show(native(keys) if keys else "None")
        self.clicked.connect(self.start)

    def _show(self, text):
        self.setText(text)
        self.setProperty("recording", self.recording)
        self.style().unpolish(self)
        self.style().polish(self)

    def start(self):
        if self.recording:
            return
        self.recording = True
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.grabKeyboard()
        self._show("Type a shortcut…")

    def stop(self, keys=None):
        if not self.recording:
            return
        self.recording = False
        self.releaseKeyboard()
        self._show(native(self.keys) if self.keys else "None")
        if keys is not None and keys != self.keys:
            self.chosen.emit(keys)

    def event(self, event):
        if getattr(self, "recording", False) and event.type() == event.Type.ShortcutOverride:
            event.accept()  # (the keys come here, not to the menu item that has them now)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        key, mods = Qt.Key(event.key()), event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key in MODIFIER_KEYS:
            held = QKeySequence(QKeyCombination(mods, Qt.Key.Key_unknown)).toString(
                QKeySequence.SequenceFormat.NativeText).replace("Unknown", "").rstrip("+")
            self._show(f"{held}…" if held else "Type a shortcut…")
            return
        if key == Qt.Key.Key_Escape and not mods:
            return self.stop()
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not mods:
            return self.stop("")
        function_key = Qt.Key.Key_F1 <= key <= Qt.Key.Key_F20
        if not (mods & ANY_MODIFIER) and not function_key:
            self._show("Use ⌘, ⌥ or ⌃ too…" if sys.platform == "darwin" else "Use Ctrl or Alt too…")
            return
        self.stop(portable(QKeySequence(QKeyCombination(mods, key))))

    def keyReleaseEvent(self, event):
        if not self.recording:
            super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.stop()
        super().focusOutEvent(event)
