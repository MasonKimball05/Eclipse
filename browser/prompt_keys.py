# The cards a page puts up (a permission, opening an app, a passkey or password): reachable from the keyboard
# without stealing it. A card doesn't take the keyboard as it appears (you could be typing into the page, and a key
# meant for the page would answer it). VoiceOver says what it asks as it appears, and ⌥⇧⌘A, the key Chrome uses
# for the same thing, moves the keyboard to it: Tab between its buttons, Return or Space to answer, Esc to go back to
# the page with it still there. With no card up, ⌥⇧⌘A reaches a toast's button instead (Undo, say).

import re

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton

from . import macos, shortcuts

CARD = "PermissionPrompt"  # (every page card's object name: they share its look)
KEYS = "Ctrl+Alt+Shift+A"  # ⌥⇧⌘A


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def card_text(card) -> str:
    return " ".join(t for t in (_plain(label.text()) for label in card.findChildren(QLabel)) if t)


def shown(card):
    """A card has just appeared: VoiceOver says what it asks, and how to answer it."""
    macos.announce(f"{card_text(card)}. {shortcuts.for_here('⌥⇧⌘A')} to answer.")


def visible_cards(window) -> list:
    return [c for c in window.findChildren(QFrame, CARD) if c.isVisible()]


def focus_card(window) -> bool:
    """The keyboard to the card on screen (the newest, if there are several): its main button, or its first one
    while that can't be clicked yet."""
    cards = visible_cards(window)
    if not cards:
        content = getattr(window, "content", None)
        return bool(content is not None and content.focus_toast())  # (else a toast's button, like Undo)
    card = cards[-1]
    buttons = [b for b in card.findChildren(QPushButton) if b.isVisible() and b.isEnabled()]
    if not buttons:
        return False
    main = next((b for b in buttons if b.objectName() == "PermissionAllow"), None)
    keys = getattr(card, "_prompt_keys", None)
    if keys is None:
        keys = card._prompt_keys = _CardKeys(card, window)
    for button in card.findChildren(QPushButton):
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # (Tab reaches them, whatever Full Keyboard Access says)
        button.removeEventFilter(keys)
        button.installEventFilter(keys)
    (main or buttons[0]).setFocus(Qt.FocusReason.ShortcutFocusReason)
    return True


def back_to_page(window):
    """The keyboard back to the page in `window`: the main window's current one, or a Private or Little window's."""
    if hasattr(window, "focus_current"):
        window.focus_current()
        return
    view = getattr(window, "current", None) or getattr(window, "view", None)
    if view is not None and not sip.isdeleted(view):
        view.setFocus(Qt.FocusReason.OtherFocusReason)


class _CardKeys(QObject):
    """A focused card's buttons: Return answers with the focused one, Esc goes back to the page."""

    def __init__(self, card, window):
        super().__init__(card)
        self.window = window

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and isinstance(obj, QPushButton):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if obj.isEnabled():
                    obj.click()
                return True
            if event.key() == Qt.Key.Key_Escape:
                if not sip.isdeleted(self.window):
                    back_to_page(self.window)
                return True
        return False
