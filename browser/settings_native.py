# Native Settings: the Settings window drawn by SwiftUI (native/Sources/Settings.swift), on a Mac where the
# native pages are built.
#
# It mirrors Qt's Settings rather than repeating it. The SettingsWindow (settings.py, and every feature's page)
# is still built, but never shown: it keeps all of Settings' logic. Its pages are read into a description (pages →
# cards → rows: title, description, and the control: a switch, a menu, buttons, a text field…), which SwiftUI
# draws; what's done in the native window is done to the Qt control it came from (a switch flipped is the checkbox
# clicked), so its own handlers run exactly as before. Then it's read again, and what changed is sent over.
#
# A control it doesn't know is shown as a picture of itself, so a page is never missing a piece.

import json

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QTextDocument
from PyQt6.QtWidgets import (QAbstractButton, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
                             QScrollArea, QToolButton, QWidget)

from . import native
from .native import png

POLL_MS = 1000  # (while it's open: what changed by itself, a check finishing, shows within a second)
# Each page's icon as an SF Symbol (Qt's are Phosphor's)
SYMBOLS = {"ph.gear-six": "gearshape", "ph.squares-four": "square.grid.2x2", "ph.shield-check": "hand.raised",
           "ph.key": "key", "ph.download-simple": "arrow.down.circle", "ph.user-circle": "person.crop.circle",
           "ph.airplane-tilt": "airplane", "ph.keyboard": "keyboard", "ph.address-book": "person.text.rectangle",
           "ph.sparkle": "sparkles", "ph.hard-drives": "square.stack.3d.up", "ph.lightning": "bolt",
           "ph.identification-card": "person.crop.rectangle", "ph.bell-ringing": "bell", "ph.puzzle-piece":
           "puzzlepiece.extension"}


def hidden(widget) -> bool:
    """Hidden on purpose. (Qt's pages are never shown, and a widget added to a parent that hasn't been shown counts
    as hidden too until it is, so isHidden() alone would leave out a status line added to a row after it was made.)"""
    return widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)


def pictured(label) -> bool:
    """A label showing a picture (an empty QPixmap, a text label's, counts as true)."""
    return label.pixmap() is not None and not label.pixmap().isNull()


def container(widget) -> bool:
    """A widget that only holds others (laid out, drawing nothing itself): read for what it holds."""
    if widget.layout() is None or type(widget).__module__.startswith("PyQt6") and type(widget) not in (QWidget, QFrame):
        return False  # (a Qt control of its own: a scroll area, a tab bar…)
    return not any("paintEvent" in vars(kind) for kind in type(widget).__mro__ if not kind.__module__.startswith("PyQt6"))


def plain(text: str) -> str:
    """A label's text as words: rich text (a link, bold) without its tags, & for &&."""
    if Qt.mightBeRichText(text):
        document = QTextDocument()
        document.setHtml(text)
        text = document.toPlainText()
    return text.replace("&&", "&")


def mac_keys(event: dict) -> str | None:
    """A key pressed in native Settings' shortcut recorder as Qt's portable text ("Ctrl+Shift+T" for ⇧⌘T), or
    None if it can't be a shortcut (it needs ⌘, ⌥ or ⌃, or a function key)."""
    key = event.get("key", "")
    named = {"left": "Left", "right": "Right", "up": "Up", "down": "Down", "return": "Return", "tab": "Tab",
             "space": "Space", "escape": "Esc", "delete": "Backspace", "forwarddelete": "Del", "home": "Home",
             "end": "End", "pageup": "PgUp", "pagedown": "PgDown"}
    name = named.get(key) or (key.upper() if len(key) == 1 else key.upper() if key.lower().startswith("f") else key)
    function = len(name) > 1 and name[0] == "F" and name[1:].isdigit()
    if not name or not (function or event.get("cmd") or event.get("alt") or event.get("ctrl")):
        return None
    parts = [m for m, on in (("Meta", event.get("ctrl")), ("Ctrl", event.get("cmd")), ("Alt", event.get("alt")),
                             ("Shift", event.get("shift"))) if on]  # (Qt on a Mac: Ctrl is ⌘, Meta is ⌃)
    return QKeySequence("+".join(parts + [name])).toString(QKeySequence.SequenceFormat.PortableText) or None


class NativeSettings(QObject):
    """Settings, drawn natively. open(page) as SettingsWindow's; apply_theme() when the Space's look changes."""

    def __init__(self, browser):
        super().__init__(browser)
        from .settings import SettingsWindow
        self.browser = browser
        self.qt = SettingsWindow(browser)  # (never shown: it holds Settings' logic, and its pages are read)
        self.qt.resize(860, 640)
        self.page = None
        self._widgets: dict[int, QWidget] = {}
        self._icons: dict[int, str] = {}  # (an icon's picture, by its cacheKey: the same twelve every time)
        self._sent = ""
        self._poll = QTimer(self, interval=POLL_MS, timeout=self.refresh)

    # --- The window ------------------------------------------------------------------------------------------

    def open(self, page="general"):
        qt = self.qt
        qt.nav.setCurrentRow(qt.page_index.get(page, 0))
        qt._refresh()
        if self.page is not None and self.page.is_open():
            self.page.update({"selected": self.current_key()})
            self.refresh()
            self.page.front()
            return
        pages = self.describe()
        self._sent = json.dumps(pages)
        self.page = native.NativePage({
            "page": "settings", "title": "Settings", "pages": pages, "selected": self.current_key(),
            "classic": True, "show_title": True, "width": 860, "height": 640, "min_width": 700, "min_height": 460,
            **native.theme_state(self.browser.theme)}, self)
        self.qt.native_page = self.page  # (its questions: sheets on the native window, prompts.py)
        self.page.action.connect(self._act)
        self.page.closed.connect(self._closed)
        self._poll.start()

    def _closed(self):
        self._poll.stop()
        # A field typed in and the window closed (⌘W) before Return or a click elsewhere: what's typed is saved, as
        # it is when you leave the field. Only done with then, those fields (a GitHub name, a server) kept nothing
        for widget in self.__dict__.pop("_unfinished", []):
            if not sip.isdeleted(widget):
                widget.editingFinished.emit()
        self.page = None

    def isVisible(self) -> bool:
        return self.page is not None and self.page.is_open()

    def close(self):
        if self.page is not None:
            self.page.close()

    def apply_theme(self):
        self.qt.apply_theme()
        if self.page is not None:
            self.page.update(native.theme_state(self.browser.theme))

    def current_key(self) -> str:
        row = self.qt.nav.currentRow()
        return self.qt.page_list[row][0] if 0 <= row < len(self.qt.page_list) else "general"

    def refresh(self):
        """Read the pages again, and send them if anything changed."""
        if self.page is None or sip.isdeleted(self.qt):
            return
        pages = self.describe()
        text = json.dumps(pages)
        if text != self._sent:
            self._sent = text
            self.page.update({"pages": pages})

    # --- Reading Qt's pages --------------------------------------------------------------------------------

    def describe(self) -> list[dict]:
        self._widgets = {}
        pages = []
        for key, icon, title in self.qt.page_list:
            scroll = self.qt.pages.widget(self.qt.page_index[key])
            body = scroll.widget() if isinstance(scroll, QScrollArea) else scroll
            items = []
            for child in self._layout_widgets(body):
                if not (isinstance(child, QLabel) and child.objectName() == "PanelTitle"):
                    items += self._items(child)
            pages.append({"key": key, "title": title, "symbol": SYMBOLS.get(icon, "circle"), "items": items})
        return pages

    def _is_named_line(self, widget) -> bool:
        if not container(widget) or not isinstance(widget.layout(), QHBoxLayout):
            return False
        children = self._layout_widgets(widget)
        return (len(children) >= 2 and isinstance(children[0], QLabel) and not children[0].objectName()
                and widget.layout().stretch(0) > 0)

    def _layout_widgets(self, widget) -> list[QWidget]:
        return self._layout_widgets_of(widget.layout()) if widget.layout() is not None else []

    def _layout_widgets_of(self, top) -> list[QWidget]:
        """A layout's widgets in order (nested layouts included), the hidden ones left out."""
        found = []

        def walk(layout):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item.widget() is not None:
                    if not hidden(item.widget()):
                        found.append(item.widget())
                elif item.layout() is not None:
                    walk(item.layout())
        if top is not None:
            walk(top)
        return found

    def _id(self, widget) -> int:
        self._widgets[id(widget)] = widget
        return id(widget)

    def _items(self, widget) -> list[dict]:
        """What a part of a page is, for the native page: a card of rows, a row, a heading, a note, controls."""
        if hidden(widget):
            return []
        setting = getattr(widget, "setting", None)
        if setting is not None:
            title, _description, control = setting
            # Under the title: its description, and whatever was added there since (a status line, a progress bar)
            column = widget.layout().itemAt(0).layout()
            below = [w for w in self._layout_widgets_of(column) if w is not title]
            lines = [plain(w.text()) for w in below if isinstance(w, QLabel) and not pictured(w) and w.text().strip()]
            extras = [self._control(w) for w in below if not isinstance(w, QLabel) or pictured(w)]
            return [{"kind": "row", "title": plain(title.text()), "description": "\n".join(lines), "extras": extras,
                     "control": self._control(control) if control is not None and not hidden(control) else None}]
        name = widget.objectName()
        if self._is_named_line(widget):
            # A line of its own making with a name at the start (a shortcut's row): as a row, the rest its control
            first, *rest = self._layout_widgets(widget)
            control = (self._control(rest[0]) if len(rest) == 1 else
                       {"id": self._id(widget), "type": "group", "axis": "h", "enabled": True, "tip": "",
                        "items": [self._control(w) for w in rest]}) if rest else None
            return [{"kind": "row", "title": plain(first.text()), "description": "", "extras": [], "control": control}]
        if name == "OptionsCard":
            return [{"kind": "card", "items": [i for child in self._layout_widgets(widget) for i in self._items(child)]}]
        if name == "SettingsLine" or (isinstance(widget, QFrame) and type(widget) is QFrame and widget.layout() is None):
            return []  # (a card's dividing line: SwiftUI draws its own)
        if isinstance(widget, QLabel):
            if name == "EditorSection":
                return [{"kind": "heading", "text": plain(widget.text())}]
            if pictured(widget):
                return [{"kind": "control", "control": self._picture(widget)}]
            text = plain(widget.text())
            return [{"kind": "note", "text": text, "dim": name == "Hint"}] if text.strip() else []
        if container(widget) and not isinstance(widget.layout(), (QHBoxLayout, QGridLayout)) and \
                not isinstance(widget, (QAbstractButton, QComboBox, QLineEdit, QListWidget, QLabel)):
            return [i for child in self._layout_widgets(widget) for i in self._items(child)]
        return [{"kind": "control", "control": self._control(widget)}]

    def _control(self, widget) -> dict:
        from .shortcuts import KeyRecorder
        enabled = widget.isEnabled()
        base = {"id": self._id(widget), "enabled": enabled, "tip": plain(widget.toolTip())}
        if isinstance(widget, QCheckBox) or isinstance(widget, QRadioButton):
            return {**base, "type": "toggle", "checked": widget.isChecked(), "text": plain(widget.text())}
        if isinstance(widget, QComboBox):
            return {**base, "type": "menu", "index": widget.currentIndex(),
                    "options": [plain(widget.itemText(i)) for i in range(widget.count())]}
        if isinstance(widget, KeyRecorder):
            return {**base, "type": "keys", "text": widget.text()}
        if isinstance(widget, QToolButton) and not widget.icon().isNull() and not widget.text():
            return {**base, "type": "icon", "png": self._icon(widget), "checked": widget.isChecked(),
                    "size": widget.iconSize().width(), "text": plain(widget.toolTip())}
        if isinstance(widget, QAbstractButton):
            menu = widget.menu() if isinstance(widget, (QPushButton, QToolButton)) else None
            style = ("primary" if widget.objectName() == "PermissionAllow" else
                     "link" if widget.objectName() in ("Link", "LinkButton") else "plain")
            text = plain(widget.text()).removesuffix("▾").strip() if menu else plain(widget.text())
            return {**base, "type": "button", "text": text or plain(widget.toolTip()), "style": style,
                    "menu": [plain(a.text()) for a in menu.actions() if not a.isSeparator()] if menu else None}
        if isinstance(widget, QLineEdit):
            return {**base, "type": "text", "text": widget.text(), "placeholder": widget.placeholderText(),
                    "secure": widget.echoMode() != QLineEdit.EchoMode.Normal, "readonly": widget.isReadOnly()}
        if isinstance(widget, QPlainTextEdit):
            return {**base, "type": "textarea", "text": widget.toPlainText(), "readonly": widget.isReadOnly()}
        if isinstance(widget, QProgressBar):
            return {**base, "type": "progress", "value": widget.value(), "total": max(1, widget.maximum())}
        if isinstance(widget, QListWidget):
            current = widget.currentRow()
            return {**base, "type": "list", "rows": [{"text": plain(widget.item(i).text()), "selected": i == current}
                                                       for i in range(widget.count()) if not widget.item(i).isHidden()]}
        if isinstance(widget, QLabel):
            if pictured(widget):
                return self._picture(widget)
            return {**base, "type": "label", "text": plain(widget.text()), "dim": widget.objectName() == "Hint",
                    "keycap": widget.objectName() == "KeyCap"}
        if container(widget):
            layout = widget.layout()
            children = [self._control(child) for child in self._layout_widgets(widget)]
            if isinstance(layout, QGridLayout):
                return {**base, "type": "group", "axis": "grid", "columns": max(1, min(layout.columnCount(), 6)),
                        "items": children}
            return {**base, "type": "group", "axis": "h" if isinstance(layout, QHBoxLayout) else "v",
                    "items": children}
        return self._picture(widget)

    def _icon(self, button) -> str:
        key = button.icon().cacheKey()
        if key not in self._icons:
            size = button.iconSize()
            self._icons[key] = png(button.icon().pixmap(size, 2.0))
        return self._icons[key]

    def _picture(self, widget) -> dict:
        """A part it doesn't know (a drawn card, say), as a picture of itself."""
        size = widget.sizeHint() if widget.width() < 10 else widget.size()
        if widget.size() != size:
            widget.resize(size)
        image = widget.grab()
        return {"id": self._id(widget), "type": "picture", "png": png(image), "width": size.width(),
                "height": size.height(), "enabled": True, "tip": plain(widget.toolTip())}

    # --- What's done in the native window ------------------------------------------------------------------

    def _act(self, name, event):
        if name == "select":
            keys = [k for k, _, _ in self.qt.page_list]
            if event.get("key") in keys:
                self.qt.nav.setCurrentRow(keys.index(event["key"]))  # (its page's status: refreshed as it shows)
        elif name == "classic":
            self.close()
            self.qt.open(self.current_key())  # (the Qt window itself, for anything not drawn here yet)
            return
        elif name == "focus":
            self.qt._refresh()
        else:
            widget = self._widgets.get(event.get("id"))
            if widget is None or sip.isdeleted(widget) or not widget.isEnabled():
                return
            if (frame := self.page.frame()) is not None:
                self.qt.setGeometry(frame)  # (a question it asks, or a file picker, opens over the native window)
            self._do(widget, name, event)
        QTimer.singleShot(0, self.refresh)
        QTimer.singleShot(300, self.refresh)  # (and once what it started has had a moment)

    def _do(self, widget, name, event):
        value = event.get("value")
        if name == "toggle" and isinstance(widget, QAbstractButton):
            if widget.isChecked() != bool(value):
                widget.click()  # (clicked and toggled, as a real click: handlers use either)
        elif name == "choose" and isinstance(widget, QComboBox) and isinstance(value, int):
            if 0 <= value < widget.count() and value != widget.currentIndex():
                widget.setCurrentIndex(value)
                widget.activated.emit(value)
        elif name == "press" and isinstance(widget, QAbstractButton):
            widget.click()
        elif name == "menu_item" and isinstance(widget, (QPushButton, QToolButton)) and widget.menu() is not None:
            actions = [a for a in widget.menu().actions() if not a.isSeparator()]
            if isinstance(value, int) and 0 <= value < len(actions):
                actions[value].trigger()
        elif name == "text" and isinstance(widget, QLineEdit) and isinstance(value, str):
            if value != widget.text():
                widget.setText(value)
                widget.textEdited.emit(value)
            unfinished = self.__dict__.setdefault("_unfinished", [])
            if event.get("finished", True):
                if widget in unfinished:
                    unfinished.remove(widget)
                widget.editingFinished.emit()
            elif widget not in unfinished:
                unfinished.append(widget)  # (typed, not yet done with: finished as the window closes, if not before)
            if event.get("submit"):
                widget.returnPressed.emit()
        elif name == "text" and isinstance(widget, QPlainTextEdit) and isinstance(value, str):
            if value != widget.toPlainText():
                widget.setPlainText(value)
        elif name == "select_row" and isinstance(widget, QListWidget) and isinstance(value, int):
            shown = [i for i in range(widget.count()) if not widget.item(i).isHidden()]
            row = shown[value] if 0 <= value < len(shown) else None
            text = event.get("text")
            if isinstance(text, str) and text and (row is None or plain(widget.item(row).text()) != text):
                # The list changed since it was drawn (a site got a permission, say): the row clicked, by its words,
                # not whatever's in its place now; or nothing, if it's gone
                row = next((i for i in shown if plain(widget.item(i).text()) == text), None)
            if row is not None:
                widget.setCurrentRow(row)
                if event.get("open"):
                    widget.itemDoubleClicked.emit(widget.item(row))
        elif name == "keys":
            from .shortcuts import KeyRecorder
            if not isinstance(widget, KeyRecorder):
                return
            if event.get("key") == "delete" and not any(event.get(m) for m in ("cmd", "alt", "ctrl", "shift")):
                keys = ""  # (Delete on its own: no shortcut)
            else:
                keys = mac_keys(event)
            if keys is not None and keys != widget.keys:
                widget.chosen.emit(keys)


def window_for(browser):
    """Settings, natively where that's built, else Qt's window."""
    if native.available():
        return NativeSettings(browser)
    from .settings import SettingsWindow
    return SettingsWindow(browser)

