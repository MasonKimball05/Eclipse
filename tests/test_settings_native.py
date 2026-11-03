from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


def _reader():
    from browser.settings_native import NativeSettings
    reader = NativeSettings.__new__(NativeSettings)  # (just its reading: no browser, no window)
    QObject.__init__(reader)
    reader._widgets, reader._icons = {}, {}
    return reader


def test_native_settings_read_qt_pages_as_cards_rows_and_controls():
    from browser.settings import SettingsWindow
    box, menu = QCheckBox(), QComboBox()
    menu.addItems(["Google", "DuckDuckGo"])
    menu.setCurrentIndex(1)
    status_row = SettingsWindow._row(None, "Default browser", "", QPushButton("Make Default"))
    status = QLabel("Arc is your default browser right now.")
    status_row.layout().itemAt(0).layout().addWidget(status)  # (added under the title after it was made)
    gone = SettingsWindow._row(None, "Hidden", "Not shown", QCheckBox())
    card = SettingsWindow._card(None, [SettingsWindow._row(None, "Peek", "Links float", box),
                                       SettingsWindow._row(None, "Search engine", "", menu), status_row, gone])
    gone.hide()
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.addWidget(card)
    note = QLabel("A note under the card.")
    note.setObjectName("Hint")
    layout.addWidget(note)
    line = QWidget()  # (a line of its own making with a name at its start: a shortcut's row)
    h = QHBoxLayout(line)
    h.addWidget(QLabel("New Tab"), 1)
    h.addWidget(QPushButton("⌘T"))
    layout.addWidget(line)
    reader = _reader()
    items = [i for w in reader._layout_widgets(page) for i in reader._items(w)]
    assert [i["kind"] for i in items] == ["card", "note", "row"]
    rows = items[0]["items"]
    assert [r["title"] for r in rows] == ["Peek", "Search engine", "Default browser"]  # (the hidden one left out)
    assert rows[0]["description"] == "Links float" and rows[0]["control"]["type"] == "toggle"
    assert rows[1]["control"]["options"] == ["Google", "DuckDuckGo"] and rows[1]["control"]["index"] == 1
    assert rows[2]["description"] == "Arc is your default browser right now."
    assert items[1] == {"kind": "note", "text": "A note under the card.", "dim": True}
    assert items[2]["title"] == "New Tab" and items[2]["control"]["text"] == "⌘T"

    reader._do(reader._widgets[rows[0]["control"]["id"]], "toggle", {"value": True})  # done to the Qt control
    assert box.isChecked()
    reader._do(menu, "choose", {"value": 0})
    assert menu.currentIndex() == 0
    field, typed = QLineEdit(), []
    field.textChanged.connect(typed.append)
    finished = []
    field.editingFinished.connect(lambda: finished.append(field.text()))
    reader._do(field, "text", {"value": "abc", "finished": False})  # (as it's typed: no editingFinished yet)
    assert typed == ["abc"] and finished == []
    reader._do(field, "text", {"value": "abc", "finished": True})
    assert finished == ["abc"]


def test_native_settings_turn_typed_keys_into_qt_shortcuts():
    from browser.settings_native import mac_keys
    assert mac_keys({"key": "t", "cmd": True, "shift": True}) == "Ctrl+Shift+T"
    assert mac_keys({"key": "left", "alt": True, "ctrl": True}) == "Meta+Alt+Left"
    assert mac_keys({"key": "F5"}) == "F5"
    assert mac_keys({"key": "t"}) is None and mac_keys({"key": "t", "shift": True}) is None  # (plain typing)
