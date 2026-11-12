# The small questions Eclipse asks (a name for a new Space, "Delete this folder?", a note that something didn't
# work) as macOS's own alert, a sheet on the window that asks (native/Sources/Alert.swift), where native pages are
# built and the window is showing. Otherwise Qt's QInputDialog and QMessageBox, as before.
#
# A native alert doesn't wait: the answer comes later, to the function given. Qt's does, so with it the function
# is called before the ask returns. Callers do what follows in that function either way.

import functools

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from . import native

def _plain(text: str) -> str:
    """Text for Qt's boxes, shown as it is (Qt takes anything that looks like HTML as HTML: theme.plain_tip)."""
    from .theme import plain_tip
    return plain_tip(text) if "<" in text else text


_open: set = set()  # (alerts still up: kept, so their answers have somewhere to go)
_asking = False  # (confirm_now waiting for its answer)


def _where(parent) -> dict | None:
    """Where an alert goes for `parent`: a sheet on its window (or on the native window a hidden Qt one stands
    behind), or None if there's nowhere showing."""
    top = parent.window()
    mirror = getattr(top, "native_page", None)  # (a Qt window kept hidden behind a native one: Settings, Library)
    if mirror is not None and mirror.is_open():
        return {"sheet_page": mirror.id}
    if top.isVisible():
        return {"sheet_view": int(top.winId())}
    return None


def _dark(parent) -> dict:
    top = parent.window()
    theme = getattr(top, "theme", None) or getattr(getattr(top, "browser", None), "theme", None)
    return {"dark": bool(theme.dark)} if theme is not None else {}


def confirm_now(parent, title: str, text: str, *, action: str = "OK") -> bool:
    """A question answered before this returns, for a caller that can't wait (a download Qt needs a yes or no to
    within its signal): still a sheet where it can be, else Qt's. One asked while another's waiting (a page starting
    downloads one after another) is a no, not a second box inside the first's wait."""
    global _asking
    if _asking:
        return False
    if parent is not None and native.available() and (where := _where(parent)) is not None:
        _asking = True
        try:
            answer = native.ask({**where, **_dark(parent), "title": title, "text": text,
                                 "buttons": [action, "Cancel"], "style": "warning"})
        finally:
            _asking = False
        if answer >= 0:
            return answer == 0
    return QMessageBox.question(parent, title, _plain(text), QMessageBox.StandardButton.Cancel |
                                QMessageBox.StandardButton.Save, QMessageBox.StandardButton.Cancel) \
        == QMessageBox.StandardButton.Save


def _let_go():
    """The alerts put away let go of (not a lambda holding its page: a cycle only the garbage collector breaks)."""
    _open.difference_update([page for page in _open if not page.id])
    run_waiting()


# A question asked while another's up, or from a form's button before its sheet's away (a wrong PIN, a note once
# something's imported), waits its turn: a window holds one sheet at a time, and it'd be Qt's box otherwise
_waiting: list = []  # [(parent, ask)]


def _busy() -> bool:
    from . import forms
    # An alert whose window has gone (closed, or put away) is in nobody's way: if its sheet never said it closed
    # (whether macOS says so then isn't sure), every question after it waited, unasked, till Eclipse was restarted
    _open.difference_update([page for page in _open if page.id and not _still_up(page)])
    return forms.answering() or any(page.id for page in _open)


def _still_up(page) -> bool:
    top = getattr(page, "asked_from", None)
    if top is None:
        return True
    if sip.isdeleted(top):
        return False
    mirror = getattr(top, "native_page", None)
    return top.isVisible() or (mirror is not None and mirror.is_open())


def run_waiting():
    """The next question waiting, once nothing's in its way (on the event loop's next pass: out of Swift's)."""
    if _waiting:
        QTimer.singleShot(0, _next)


def _next():
    while _waiting and not _busy():
        parent, ask = _waiting.pop(0)
        if not sip.isdeleted(parent):
            ask()
            return


def _in_turn(function):
    @functools.wraps(function)
    def asked(parent, *args, **kwargs):
        if parent is not None and native.available() and (_busy() or _waiting):
            _waiting.append((parent, functools.partial(function, parent, *args, **kwargs)))
            return None
        return function(parent, *args, **kwargs)
    return asked


def _alert(parent, spec: dict, on_button) -> "native.NativePage | None":
    if parent is None or not native.available():
        return None
    where = _where(parent)
    if where is None:
        return None
    spec = {"page": "alert", **where, **spec, **_dark(parent)}
    page = native.NativePage(spec)
    if not page.id:
        return None  # (the window has a sheet up already, say: Qt's asks instead)
    page.asked_from = parent.window()  # (where its sheet is: gone, it's in nobody's way, _busy)
    _open.add(page)
    page.action.connect(lambda name, event: on_button(event.get("index", -1), event.get("text", ""))
                        if name == "button" else None)
    page.closed.connect(_let_go)
    return page


@_in_turn
def ask_text(parent, title: str, label: str, on_done, *, text: str = "", placeholder: str = "", action: str = "OK",
             secret: bool = False):
    """A line typed (a name…; `secret`: shown as dots, a token): on_done(text) once it's given (not if cancelled)."""
    page = _alert(parent, {"title": title, "text": label, "buttons": [action, "Cancel"],
                           "field": {"value": text, "placeholder": placeholder, "secret": secret}},
                  lambda index, typed: on_done(typed) if index == 0 else None)
    if page is not None:
        return page
    echo = QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal
    typed, ok = QInputDialog.getText(parent, title, _plain(label), echo, text)
    if ok:
        on_done(typed)
    return None


@_in_turn
def confirm(parent, title: str, text: str, on_yes, *, action: str = "OK", destructive: bool = False):
    """A question: on_yes() if it's answered with `action` (Qt's: Yes)."""
    page = _alert(parent, {"title": title, "text": text, "buttons": [action, "Cancel"], "style": "warning",
                           **({"destructive": 0} if destructive else {})},
                  lambda index, _: on_yes() if index == 0 else None)
    if page is not None:
        return page
    if QMessageBox.question(parent, title, _plain(text)) == QMessageBox.StandardButton.Yes:
        on_yes()
    return None


@_in_turn
def tell(parent, title: str, text: str, *, warning: bool = False, on_done=None):
    """A note, with OK: on_done() once it's put away, for a caller that mustn't go on before it's read."""
    page = _alert(parent, {"title": title, "text": text, "buttons": ["OK"],
                           **({"style": "warning"} if warning else {})}, lambda *_: None)
    if page is not None:
        if on_done is not None:
            page.closed.connect(on_done)  # (OK pressed, or put away with the window: once either way)
        return page
    (QMessageBox.warning if warning else QMessageBox.information)(parent, title, _plain(text))
    if on_done is not None:
        on_done()
    return None


@_in_turn
def choose(parent, title: str, text: str, buttons: list[str], on_choice, *, detail: str = "",
           destructive: int | None = None):
    """A question with its own answers (the first the default) and Cancel: on_choice(index) unless cancelled.
    `text` is shown as plain text (a name in it is whatever a file said)."""
    page = _alert(parent, {"title": text, "text": detail, "buttons": [*buttons, "Cancel"],
                           **({"destructive": destructive} if destructive is not None else {})},
                  lambda index, _: on_choice(index) if 0 <= index < len(buttons) else None)
    if page is not None:
        return page
    box = QMessageBox(QMessageBox.Icon.Question, title, text, parent=parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    if detail:
        box.setInformativeText(detail)
    made = [box.addButton(label, QMessageBox.ButtonRole.AcceptRole) for label in buttons]
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(made[0])
    box.exec()
    if box.clickedButton() in made:
        on_choice(made.index(box.clickedButton()))
    return None
