"""Passkeys and security keys: the card for each step of a request, with a stand-in for Qt's request."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLabel, QLineEdit, QPushButton, QWidget

from browser import passkeys
from browser.passkeys import Failure, Pin, PinError, State


class Request(QObject):
    stateChanged = pyqtSignal(object)

    def __init__(self, state, names=(), pin=None, failure=None):
        super().__init__()
        self._state, self.names, self.pin, self.failure = state, list(names), pin, failure
        self.picked = self.sent = None
        self.cancelled = self.retried = False

    def state(self):
        return self._state

    def go(self, state, **changes):
        self._state = state
        for key, value in changes.items():
            setattr(self, key, value)
        self.stateChanged.emit(state)

    def relyingPartyId(self):
        return "www.example.com"

    def userNames(self):
        return self.names

    def pinRequest(self):
        return self.pin

    def requestFailureReason(self):
        return self.failure

    def setSelectedAccount(self, name):
        self.picked = name

    def setPin(self, pin):
        self.sent = pin

    def cancel(self):
        self.cancelled = True

    def retry(self):
        self.retried = True


def _card(qapp, request):
    host = QWidget()
    host.resize(900, 600)
    view = QWidget(host)
    view.resize(900, 600)
    host.show()
    closed = []
    manager = SimpleNamespace(browser=SimpleNamespace(theme=SimpleNamespace(accent_text=QColor("#9d85ff"))),
                              close=closed.append)
    card = passkeys.PasskeyCard(manager, view, host, request)
    return card, closed, host


def _texts(card):
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()  # (a step's new widgets show on Qt's next pass, as on screen)
    return " ".join(label.text() for label in card.findChildren(QLabel) if label.isVisibleTo(card))


def _button(card, text):
    return next(b for b in card.findChildren(QPushButton) if b.text() == text and not b.isHidden())


def _pin(reason=Pin.Challenge, error=PinError.NoError, minimum=4, left=8):
    return SimpleNamespace(reason=reason, error=error, minPinLength=minimum, remainingAttempts=left)


def test_choosing_an_account(qapp):
    request = Request(State.SelectAccount, names=["arkan@example.com", "work@example.com"])
    card, _closed, _host = _card(qapp, request)
    assert "Sign in to <b>example.com</b>" in _texts(card)
    _button(card, "work@example.com").click()
    assert request.picked == "work@example.com"
    _button(card, "Cancel").click()
    assert request.cancelled


def test_the_pin_is_checked_before_its_sent_and_wrong_ones_say_how_many_tries_are_left(qapp):
    request = Request(State.CollectPin, pin=_pin(error=PinError.WrongPin, left=2))
    card, _closed, _host = _card(qapp, request)
    assert "isn't right. 2 tries left." in _texts(card)
    field = card.findChildren(QLineEdit)[0]
    assert field.echoMode() == QLineEdit.EchoMode.Password
    field.setText("12")
    _button(card, "Continue").click()
    assert request.sent is None and "at least 4" in _texts(card)
    field.setText("123456")
    field.returnPressed.emit()
    assert request.sent == "123456"


def test_a_new_pin_is_typed_twice(qapp):
    request = Request(State.CollectPin, pin=_pin(reason=Pin.Set, minimum=6))
    card, _closed, _host = _card(qapp, request)
    first, second = card.findChildren(QLineEdit)
    first.setText("135791")
    second.setText("135790")
    _button(card, "Continue").click()
    assert request.sent is None and "don't match" in _texts(card)
    second.setText("135791")
    _button(card, "Continue").click()
    assert request.sent == "135791"


def test_touch_the_key_then_a_failure_offers_try_again_and_finishing_closes_it(qapp):
    request = Request(State.FinishTokenCollection)
    card, closed, _host = _card(qapp, request)
    assert "Touch your security key" in _texts(card) and card.isVisible()
    request.go(State.RequestFailed, failure=Failure.KeyNotRegistered)
    assert "isn't registered with this site" in _texts(card)
    _button(card, "Try Again").click()
    assert request.retried
    request.go(State.Completed)
    assert closed == [card]


def test_pin_problems():
    assert passkeys.pin_problem("1234", None, 4) == ""
    assert "at least 6" in passkeys.pin_problem("1234", None, 6)
    assert "don't match" in passkeys.pin_problem("123456", "123457", 4)
    assert passkeys.pin_problem("", None, 0)  # (never an empty one)
    assert set(passkeys.FAILURES) == set(Failure)  # (something to say for every way it can fail)


def test_strong_passwords():
    import re
    from browser.passwords import strong_password
    made = {strong_password() for _ in range(200)}
    assert len(made) == 200
    for password in made:
        assert re.fullmatch(r"[a-zA-Z2-9]{6}-[a-zA-Z2-9]{6}-[a-zA-Z2-9]{6}", password)
        assert any(c.isupper() for c in password) and any(c.isdigit() for c in password)
        assert not set(password) & set("l01O")
