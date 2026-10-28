import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMainWindow

from browser import shortcuts

CMD = Qt.KeyboardModifier.ControlModifier  # (⌘ on a Mac)
_windows = []  # (kept: a menu bar goes with its window)


def _window(qapp):
    window = QMainWindow()
    _windows.append(window)
    bar = window.menuBar()
    file = bar.addMenu("File")
    for text, keys in (("New Tab", "Ctrl+T"), ("Share…", ""), ("Close Tab", "Ctrl+W")):
        action = QAction(text, window)
        action.setShortcut(QKeySequence(keys))
        file.addAction(action)
    file.addSeparator()
    recent = file.addMenu("Recently Closed")
    recent.addAction("Some page")
    tabs = bar.addMenu("&Tabs")
    for keys in ("Meta+Tab", "Ctrl+Alt+Down"):
        action = QAction("Next Tab", window)
        action.setShortcut(QKeySequence(keys))
        tabs.addAction(action)
    return window


def _keys(bar):
    return {key: shortcuts.portable(action.shortcut()) for key, _g, _n, action in shortcuts.menu_items(bar)}


def test_every_menu_item_is_found_once_by_a_lasting_name(qapp):
    bar = _window(qapp).menuBar()
    items = shortcuts.menu_items(bar)
    assert [key for key, *_ in items] == ["File › New Tab", "File › Share…", "File › Close Tab",
                                          "Tabs › Next Tab", "Tabs › Next Tab 2"]
    assert items[3][1:3] == ("Tabs", "Next Tab")  # ("&" mnemonics left out)


def test_yours_replace_its_own_and_reset_puts_them_back(qapp):
    bar = _window(qapp).menuBar()
    assert shortcuts.apply(bar, {"File › New Tab": "Ctrl+Shift+N", "File › Share…": "Ctrl+Alt+S",
                                 "Tabs › Next Tab 2": "", "Gone › Old Item": "Ctrl+K"}) == 3
    keys = _keys(bar)
    assert keys["File › New Tab"] == "Ctrl+Shift+N" and keys["File › Share…"] == "Ctrl+Alt+S"
    assert keys["Tabs › Next Tab 2"] == "" and keys["File › Close Tab"] == "Ctrl+W"
    shortcuts.apply(bar, {})
    assert _keys(bar) == {"File › New Tab": "Ctrl+T", "File › Share…": "", "File › Close Tab": "Ctrl+W",
                          "Tabs › Next Tab": "Meta+Tab", "Tabs › Next Tab 2": "Ctrl+Alt+Down"}


def test_who_has_a_shortcut_and_what_cant_be_one(qapp):
    bar = _window(qapp).menuBar()
    shortcuts.apply(bar, {})
    assert shortcuts.user_of(bar, "Ctrl+W") == ("File › Close Tab", "Close Tab")
    assert shortcuts.user_of(bar, "Ctrl+W", skip="File › Close Tab") is None
    assert shortcuts.user_of(bar, "") is None and shortcuts.user_of(bar, "Ctrl+J") is None
    if sys.platform == "darwin":
        assert "macOS" in shortcuts.problem("Ctrl+Q") and "macOS" in shortcuts.problem("Ctrl+Space")
    assert shortcuts.problem("Ctrl+Shift+J") == ""


def test_going_back_to_its_own_shortcut_isnt_kept_as_a_change():
    mine = shortcuts.changed({}, "File › New Tab", "Ctrl+N", "Ctrl+T")
    assert mine == {"File › New Tab": "Ctrl+N"}
    assert shortcuts.changed(mine, "File › New Tab", "Ctrl+T", "Ctrl+T") == {}
    assert shortcuts.changed({}, "File › Share…", "", "") == {}


def test_the_recorder_takes_a_shortcut_typed_into_it(qapp):
    recorder = shortcuts.KeyRecorder("Ctrl+T")
    recorder.show()
    picked = []
    recorder.chosen.connect(picked.append)
    assert recorder.text() == shortcuts.native("Ctrl+T")
    recorder.click()
    assert recorder.recording and recorder.text() == "Type a shortcut…"
    QTest.keyClick(recorder, Qt.Key.Key_K)  # (no ⌘, ⌥ or ⌃: just typing)
    assert recorder.recording and picked == []
    QTest.keyClick(recorder, Qt.Key.Key_K, CMD | Qt.KeyboardModifier.ShiftModifier)
    assert picked == ["Ctrl+Shift+K"] and not recorder.recording
    recorder.click()
    QTest.keyClick(recorder, Qt.Key.Key_F5)  # (a function key needs nothing else)
    recorder.click()
    QTest.keyClick(recorder, Qt.Key.Key_Escape)  # (leaves it)
    recorder.click()
    QTest.keyClick(recorder, Qt.Key.Key_Backspace)  # (removes it)
    assert picked == ["Ctrl+Shift+K", "F5", ""] and recorder.text() == shortcuts.native("Ctrl+T")
    recorder.close()
