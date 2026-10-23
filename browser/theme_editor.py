# Arc's Space theme picker: up to three colours blended into the window, how strongly
# they tint it, light/dark per Space, and an optional grain texture. Every change
# previews live on the real window; Cancel puts the old theme back.

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QDialog, QHBoxLayout, QLabel, QPushButton,
                             QSlider, QVBoxLayout)

from .theme import DEFAULT_SPACE_THEME, PALETTE, stylesheet

MAX_COLORS = 3


class ThemeEditor(QDialog):
    def __init__(self, browser, space):
        super().__init__(browser)
        self.browser = browser
        self.space = space
        self._original = (space.color, dict(space.theme))
        t = {**DEFAULT_SPACE_THEME, **space.theme}
        self.colors = list(t["colors"]) or [space.color]
        self.setObjectName("DownloadSettings")  # shares the panel styling
        self.setWindowTitle(f"{space.name} · Theme")
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self._heading("Colours"))
        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(8)
        layout.addLayout(self.swatch_row)
        hint = QLabel("Click a colour to change it. Two or three colours blend into a gradient.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        for name, color in PALETTE:
            dot = QPushButton()
            dot.setToolTip(name)
            dot.setFixedSize(22, 22)
            dot.setStyleSheet(f"background: {color}; border-radius: 11px; padding: 0;")
            dot.clicked.connect(lambda _=False, c=color: self._set_color(0, c))
            presets.addWidget(dot)
        presets.addStretch(1)
        layout.addLayout(presets)

        layout.addWidget(self._heading("Intensity"))
        self.intensity = QSlider(Qt.Orientation.Horizontal)
        self.intensity.setRange(0, 100)
        self.intensity.setValue(round(t["intensity"] * 100))
        self.intensity.valueChanged.connect(self._apply)
        layout.addWidget(self.intensity)

        layout.addWidget(self._heading("Appearance"))
        modes = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for mode, label in (("auto", "Match System"), ("light", "Light"), ("dark", "Dark")):
            btn = QPushButton(label)
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            btn.setChecked(t["mode"] == mode)
            btn.setProperty("mode", mode)
            self.mode_group.addButton(btn)
            modes.addWidget(btn)
        self.mode_group.buttonToggled.connect(lambda _btn, on: on and self._apply())
        layout.addLayout(modes)

        self.grain = QCheckBox("Grain texture")
        self.grain.setChecked(t["grain"])
        self.grain.toggled.connect(self._apply)
        layout.addWidget(self.grain)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        done = QPushButton("Done")
        done.setObjectName("Primary")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(done)
        layout.addSpacing(6)
        layout.addLayout(buttons)

        self._rebuild_swatches()
        self._restyle()

    def _heading(self, text):
        label = QLabel(text)
        label.setObjectName("InfoTitle")
        return label

    def _rebuild_swatches(self):
        while self.swatch_row.count():
            item = self.swatch_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, color in enumerate(self.colors):
            swatch = QPushButton()
            swatch.setFixedSize(46, 46)
            swatch.setToolTip("Change colour" + ("  (right-click to remove)" if len(self.colors) > 1 else ""))
            swatch.setStyleSheet(f"background: {color}; border-radius: 23px; border: 2px solid white;")
            swatch.clicked.connect(lambda _=False, i=i: self._pick(i))
            swatch.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            swatch.customContextMenuRequested.connect(lambda _pos, i=i: self._remove(i))
            self.swatch_row.addWidget(swatch)
        if len(self.colors) < MAX_COLORS:
            add = QPushButton("+")
            add.setFixedSize(46, 46)
            add.setToolTip("Add a colour")
            add.setStyleSheet("border-radius: 23px; font-size: 20px;")
            add.clicked.connect(self._add)
            self.swatch_row.addWidget(add)
        self.swatch_row.addStretch(1)

    def _pick(self, i):
        color = QColorDialog.getColor(QColor(self.colors[i]), self, "Space Colour")
        if color.isValid():
            self._set_color(i, color.name())

    def _set_color(self, i, color):
        self.colors[i] = color
        self._rebuild_swatches()
        self._apply()

    def _add(self):
        # Start the new colour as a hue-shifted version of the last one, then let them tweak it
        last = QColor(self.colors[-1])
        self.colors.append(QColor.fromHsv((last.hsvHue() + 60) % 360, last.hsvSaturation(), last.value()).name())
        self._rebuild_swatches()
        self._apply()

    def _remove(self, i):
        if len(self.colors) > 1:
            del self.colors[i]
            self._rebuild_swatches()
            self._apply()

    def _apply(self, *_):
        mode = next((b.property("mode") for b in self.mode_group.buttons() if b.isChecked()), "auto")
        self.space.color = self.colors[0]
        self.space.theme = {"colors": list(self.colors) if len(self.colors) > 1 else [],
                            "intensity": self.intensity.value() / 100, "mode": mode, "grain": self.grain.isChecked()}
        self.browser.apply_theme()
        self._restyle()

    def _restyle(self):
        self.setStyleSheet(stylesheet(self.browser.theme))

    def reject(self):
        self.space.color, self.space.theme = self._original
        self.browser.apply_theme()
        super().reject()

    def accept(self):
        self.browser.store.schedule_save()
        super().accept()
