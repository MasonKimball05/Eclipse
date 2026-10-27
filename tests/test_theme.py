

def test_letter_tiles_stay_readable_on_pale_colours(qapp):
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


def test_a_dark_grey_icon_gets_a_tile_on_a_dark_space_but_a_colourful_one_doesnt(qapp):
    from PyQt6.QtGui import QColor, QIcon, QPixmap
    from browser.theme import legible, make_theme
    dark = make_theme("#7c5cff", dark=True)

    def solid(color):
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)
    octocat, youtube = solid("#24292e"), solid("#ff0000")
    assert legible(octocat, dark) is not octocat  # (GitHub's grey: tiled)
    assert legible(youtube, dark) is youtube
