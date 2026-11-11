

def test_letter_tiles_stay_readable_on_pale_colors(qapp):
    from PyQt6.QtGui import QColor
    from browser.theme import _luminance, letter_icon
    for pale in ("#f4d4c4", "#fde68a", "#bbf7d0", "#ffffff"):
        tile = letter_icon("G", QColor(pale)).pixmap(32, 32).toImage().pixelColor(3, 16)  # (the tile, off the letter)
        white_contrast = 1.05 / (_luminance(tile) + 0.05)
        assert white_contrast >= 3, (pale, tile.name(), white_contrast)
    purple = letter_icon("G", QColor("#8b7cf6")).pixmap(32, 32).toImage().pixelColor(3, 16)
    assert purple.name() == "#8b7cf6"  # (already readable: left as it is)


def test_accent_text_reads_on_panels(qapp):
    from browser.theme import contrast, make_theme, stylesheet
    for color, mode in (("#f4d4c4", "light"), ("#fde68a", "light"), ("#1e1b4b", "dark"), ("#8b7cf6", "dark")):
        theme = make_theme(color, mode=mode)
        assert contrast(theme.accent_text, theme.panel) >= 4.5, (color, mode, theme.accent_text.name())
        css = stylesheet(theme)
        assert "selection-color" in css


def test_text_on_the_accent_reads(qapp):
    from browser.theme import contrast, make_theme, stylesheet
    for color in ("#f4d4c4", "#fde68a", "#bbf7d0", "#8b7cf6", "#1d4ed8"):
        theme = make_theme(color, mode="light")
        assert contrast(theme.on_accent, theme.accent) >= 2.6, (color, theme.on_accent.name())
        assert "color: white" not in "".join(l for l in stylesheet(theme).splitlines() if "rgba(" in l and "accent" in l)
    assert make_theme("#1d4ed8", mode="dark").on_accent.name() == "#ffffff"


def test_a_wrapped_note_in_a_form_is_as_tall_as_its_text(qapp):
    from PyQt6.QtWidgets import QFormLayout, QLineEdit, QVBoxLayout, QWidget
    from browser.theme import FittedLabel
    host = QWidget()
    outer = QVBoxLayout(host)
    form = QFormLayout()
    outer.addLayout(form)
    outer.addStretch(1)
    form.addRow("Model", QLineEdit())
    note = FittedLabel("For questions and Ask on Page. The other one is always a row below in the command bar. "
                       "Tidy, Translate and Browse for Me stay with Claude.")
    form.addRow("", note)
    host.resize(900, 600)
    host.show()
    for _ in range(5):
        qapp.processEvents()
    assert note.height() == note.heightForWidth(note.width())  # (no empty lines under it)
    host.close()


def test_a_dark_gray_icon_gets_a_tile_on_a_dark_space_but_a_colorful_one_doesnt(qapp):
    from PyQt6.QtGui import QColor, QIcon, QPixmap
    from browser.theme import legible, make_theme
    dark = make_theme("#7c5cff", dark=True)

    def solid(color):
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)
    octocat, youtube = solid("#24292e"), solid("#ff0000")
    assert legible(octocat, dark) is not octocat  # (GitHub's gray: tiled)
    assert legible(youtube, dark) is youtube


def test_a_menu_opened_by_a_click_stays_open_when_the_click_lets_go(qapp, monkeypatch):
    """A page's right-click menu opens as the button goes down, its corner under the pointer. A click drifts a
    point or two as it goes, onto the first item: Qt ran that item on the release and closed the menu. The click's
    own release (within MENU_HOLD_S, or having moved no more than MENU_SLOP) is let go; held down and moved onto an
    item, letting go picks it."""
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QMenu
    from browser import theme
    now = [100.0]
    monkeypatch.setattr(theme.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(theme.QCursor, "pos", staticmethod(lambda: QPoint(300, 200)))

    def menu():
        m = QMenu()
        theme.held_open(m)
        return m, next(c for c in m.children() if isinstance(c, theme._HeldOpen))

    def event(kind, at):
        held = Qt.MouseButton.RightButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.NoButton
        return QMouseEvent(kind, QPointF(0, 0), QPointF(at), Qt.MouseButton.RightButton, held,
                           Qt.KeyboardModifier.NoModifier)
    # A click: drifts 3 points, lets go at once: not a pick
    m, guard = menu()
    for x in (301, 302, 303):
        assert guard.eventFilter(m, event(QEvent.Type.MouseMove, QPoint(x, 201))) is False
    assert guard.eventFilter(m, event(QEvent.Type.MouseButtonRelease, QPoint(303, 201))) is True
    # Only the click's own: the next release is the menu's
    assert guard.eventFilter(m, event(QEvent.Type.MouseButtonRelease, QPoint(303, 201))) is False
    # A slow click (past MENU_HOLD_S), still hardly moved: not a pick either
    m, guard = menu()
    now[0] += theme.MENU_HOLD_S + 0.3
    assert guard.eventFilter(m, event(QEvent.Type.MouseButtonRelease, QPoint(304, 203))) is True
    # Held down and moved onto an item (well past the slop and the hold): letting go picks it
    m, guard = menu()
    guard.eventFilter(m, event(QEvent.Type.MouseMove, QPoint(300, 240)))
    now[0] += theme.MENU_HOLD_S + 0.1
    assert guard.eventFilter(m, event(QEvent.Type.MouseButtonRelease, QPoint(300, 240))) is False


def test_a_pages_right_click_menu_opens_as_the_button_comes_up(qapp):
    """A Mac asks for a page's menu as the right button goes down. Shown then, the menu took the mouse and the page
    never saw the button come up: Qt Quick took the press as held and closed the menu at 800 ms (and telling the page
    the button came up closed it at once). It opens once the button comes up, or after WAIT_S if that's missed."""
    import time
    from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication, QMenu
    from browser import theme, webview

    def menu():
        m = QMenu()
        shown = []
        m.popup = lambda at, *a: shown.append(at)
        return m, shown

    def settle(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            qapp.processEvents()
    m, shown = menu()
    webview._OnRelease(m, QPoint(40, 50))  # (nothing keeps it: the menu has it)
    settle(0.1)
    assert shown == []  # (the button still down)
    QApplication.sendEvent(m, QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(40, 50),
                                          Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton,
                                          Qt.KeyboardModifier.NoModifier))
    settle(0.1)
    assert shown == []  # (another button's release)
    QApplication.sendEvent(m, QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(0, 0), QPointF(40, 50),
                                          Qt.MouseButton.RightButton, Qt.MouseButton.NoButton,
                                          Qt.KeyboardModifier.NoModifier))
    settle(0.1)
    assert shown == [QPoint(40, 50)]
    assert not any(isinstance(c, theme._HeldOpen) for c in m.children())  # (let go already: the next click picks)
    # The release missed: it opens anyway, the click's own release still picking nothing
    m, shown = menu()
    on_release = webview._OnRelease(m, QPoint(1, 2))
    on_release.timer.setInterval(50)
    on_release.timer.start()
    settle(0.3)
    assert shown == [QPoint(1, 2)] and any(isinstance(c, theme._HeldOpen) for c in m.children())
