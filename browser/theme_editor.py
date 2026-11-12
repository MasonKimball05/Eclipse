# Arc's Space theme picker: up to three colors blended into the window, how strongly
# they tint it, light/dark per Space, and an optional grain texture. Every change
# previews live on the real window; Cancel puts the old theme back.

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QDialog, QHBoxLayout, QLabel, QPushButton,
                             QSlider, QVBoxLayout)

from .theme import DEFAULT_SPACE_THEME, PALETTE, stylesheet

MAX_COLORS = 3
DRAG_MS = 30  # while the intensity's being dragged, the window's restyled at most this often


def edit_theme(browser, space):
    """The theme editor: a native sheet previewing on the window as you go (forms.py), else Qt's dialog."""
    from . import forms
    original = (space.color, dict(space.theme))
    t = {**DEFAULT_SPACE_THEME, **space.theme}

    def apply(values):
        colors = [c for c in values.get("colors", []) if isinstance(c, str) and c.startswith("#")][:MAX_COLORS]
        if not colors:
            return
        mode = values.get("mode") if values.get("mode") in ("auto", "light", "dark") else "auto"
        space.color = colors[0]
        space.theme = {"colors": colors if len(colors) > 1 else [], "intensity": float(values.get("intensity", 0.5)),
                       "mode": mode, "grain": bool(values.get("grain"))}
        browser.apply_theme()

    def done(_, values):
        apply(values)
        browser.store.schedule_save()

    form = None

    def closed():
        if form is not None and not form.saved:  # (Cancel, or closed: the theme it had)
            space.color, space.theme = original
            browser.apply_theme()
    form = forms.open_form(
        browser, f"{space.name} · Theme",
        [forms.section({"id": "colors", "kind": "gradient", "label": "Colors", "max": MAX_COLORS,
                        "value": list(t["colors"]) or [space.color]},
                       {"id": "presets", "kind": "swatches", "label": "", "target": "colors",
                        "options": [{"value": c, "label": n} for n, c in PALETTE]},
                       footer="Drag a dot to change its color, or Tab to it and use the arrows (Return chooses "
                              "any color). Double-click the field to add one (up to three, blended into a gradient); "
                              "double-click a dot, or press Delete, to take it out."),
         forms.section({"id": "intensity", "kind": "slider", "label": "Intensity", "min": 0, "max": 1,
                        "value": float(t["intensity"])},
                       {"id": "mode", "kind": "menu", "label": "Appearance", "style": "segmented", "value": t["mode"],
                        "options": [{"value": "auto", "label": "Match System"}, {"value": "light", "label": "Light"},
                                    {"value": "dark", "label": "Dark"}]},
                       {"id": "grain", "kind": "toggle", "label": "Grain texture", "value": bool(t["grain"])})],
        [{"id": "cancel", "label": "Cancel", "role": "cancel"}, {"id": "done", "label": "Done", "role": "default"}],
        done, width=460, height=640, on_change=apply, on_closed=closed)
    if form is not None:
        return form
    ThemeEditor(browser, space).exec()
    return None


class ThemeEditor(QDialog):
    def __init__(self, browser, space):
        super().__init__(browser)
        self.browser = browser
        self.space = space
        self._original = (space.color, dict(space.theme))
        t = {**DEFAULT_SPACE_THEME, **space.theme}
        self.colors = list(t["colors"]) or [space.color]
        self.setObjectName("ThemeEditor")
        self.setProperty("panel", True)  # the shared panel styling (theme.py)
        self.setWindowTitle(f"{space.name} · Theme")
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(self._heading("Colors"))
        self.swatch_row = QHBoxLayout()
        self.swatch_row.setSpacing(8)
        layout.addLayout(self.swatch_row)
        hint = QLabel("Click a color to change it. Two or three colors blend into a gradient.")
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
        # Restyling the window takes a few tens of milliseconds, and a drag moves the slider more often than that:
        # each change applied as it came queued them up, so the window lagged and then jumped. The latest value,
        # every DRAG_MS, keeps up (and letting go applies where it ended)
        self._dragged = QTimer(self, singleShot=True, interval=DRAG_MS, timeout=self._apply)
        self.intensity.valueChanged.connect(lambda: self._dragged.isActive() or self._dragged.start())
        self.intensity.sliderReleased.connect(self._apply)
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
            swatch.setToolTip("Change color" + ("  (right-click to remove)" if len(self.colors) > 1 else ""))
            swatch.setStyleSheet(f"background: {color}; border-radius: 23px; border: 2px solid white;")
            swatch.clicked.connect(lambda _=False, i=i: self._pick(i))
            swatch.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            swatch.customContextMenuRequested.connect(lambda _pos, i=i: self._remove(i))
            self.swatch_row.addWidget(swatch)
        if len(self.colors) < MAX_COLORS:
            add = QPushButton("+")
            add.setFixedSize(46, 46)
            add.setToolTip("Add a color")
            add.setStyleSheet("border-radius: 23px; font-size: 20px;")
            add.clicked.connect(self._add)
            self.swatch_row.addWidget(add)
        self.swatch_row.addStretch(1)

    def _pick(self, i):
        color = QColorDialog.getColor(QColor(self.colors[i]), self, "Space Color")
        if color.isValid():
            self._set_color(i, color.name())

    def _set_color(self, i, color):
        self.colors[i] = color
        self._rebuild_swatches()
        self._apply()

    def _add(self):
        # Start the new color as a hue-shifted version of the last one, then let them tweak it
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
        self._dragged.stop()
        mode = next((b.property("mode") for b in self.mode_group.buttons() if b.isChecked()), "auto")
        self.space.color = self.colors[0]
        self.space.theme = {"colors": list(self.colors) if len(self.colors) > 1 else [],
                            "intensity": self.intensity.value() / 100, "mode": mode, "grain": self.grain.isChecked()}
        self.browser.apply_theme()
        self._restyle()

    def _restyle(self):
        self.setStyleSheet(stylesheet(self.browser.theme))

    def reject(self):
        self._dragged.stop()  # (a last bit of dragging still to show: not now)
        self.space.color, self.space.theme = self._original
        self.browser.apply_theme()
        super().reject()

    def accept(self):
        if self._dragged.isActive():
            self._apply()
        self.browser.store.schedule_save()
        super().accept()
