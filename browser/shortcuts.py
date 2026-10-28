# Your own keyboard shortcuts: any menu item's shortcut can be changed, removed, or given one it didn't have
# (Settings → Keyboard Shortcuts: click a shortcut and type the new one). Only the changes are kept, in the
# "shortcuts" setting, {"Menu › Item": "Ctrl+Shift+N"} ("" for none), so a new version's own shortcuts still
# reach the items you haven't touched.
#
# Each item remembers the shortcut it was built with, so Reset puts that back.

import sys

from PyQt6.QtCore import QKeyCombination, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QPushButton

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
