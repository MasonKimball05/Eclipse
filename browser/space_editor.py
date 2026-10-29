# The Space editor: a small popover (right-click a Space) to rename it, pick its icon and
# colour, choose its profile, and reach the theme editor. Changes apply as you make them.

import re
import sys

import qtawesome as qta
from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .icons import EMOJI, SYMBOLS, emoji_icon, is_symbol, space_icon
from .theme import PALETTE, dot_icon, stylesheet
from .webview import DEFAULT_PROFILE

COLUMNS = 8
NEW_PROFILE = "__new__"


class SpaceEditor(QWidget):
    def __init__(self, browser, space):
        super().__init__(browser, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.browser = browser
        self.space = space
        theme = browser.theme
        self.ink = theme.panel_text
        self.setStyleSheet(stylesheet(theme))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 22)  # room for the shadow
        card = QFrame()
        card.setObjectName("SpaceEditor")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 90))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(10)

        # Icon + name
        top = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(30, 30)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setObjectName("SpacePreview")
        self.name = QLineEdit(space.name)
        self.name.setObjectName("SpaceName")
        self.name.setPlaceholderText("Space name")
        self.name.editingFinished.connect(self._rename)
        top.addWidget(self.preview)
        top.addWidget(self.name, 1)
        layout.addLayout(top)

        # Icon picker: line icons or emoji
        tabs = QHBoxLayout()
        tabs.addWidget(self._section("Icon"), 1)
        self.tab_group = QButtonGroup(self)
        for i, label in enumerate(("Symbols", "Emoji")):
            btn = QPushButton(label)
            btn.setObjectName("MiniTab")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, i=i: self._show_page(i))
            self.tab_group.addButton(btn, i)
            tabs.addWidget(btn)
        layout.addLayout(tabs)
        self.pages = QStackedWidget()
        self.icon_buttons: dict[str, QToolButton] = {}
        self.pages.addWidget(self._grid(SYMBOLS + [""], lambda v: qta.icon(v, color=self.ink) if v else
                                        dot_icon(space.color, 12)))
        emoji_page = QWidget()
        emoji_layout = QVBoxLayout(emoji_page)
        emoji_layout.setContentsMargins(0, 0, 0, 0)
        emoji_layout.addWidget(self._grid(EMOJI, lambda v: emoji_icon(v, 22)))
        self.custom_emoji = QLineEdit()
        self.custom_emoji.setObjectName("SpaceName")
        self.custom_emoji.setPlaceholderText("Other emoji…")
        self.custom_emoji.setToolTip("Type or paste any emoji (" + ("⌃⌘Space opens the macOS picker)" if sys.platform == "darwin"
                                                                      else "Windows+. opens the emoji panel)"))
        self.custom_emoji.textEdited.connect(lambda t: t.strip() and self._set_icon(t.strip()[:4]))
        emoji_layout.addWidget(self.custom_emoji)
        self.pages.addWidget(emoji_page)
        layout.addWidget(self.pages)
        start = 1 if space.icon and not is_symbol(space.icon) else 0
        self.tab_group.button(start).setChecked(True)
        self._show_page(start)

        # Colour
        colour_row = QHBoxLayout()
        colour_row.setSpacing(6)
        self.color_buttons = {}
        for name, color in PALETTE:
            dot = QToolButton()
            dot.setObjectName("ColorDot")
            dot.setToolTip(name)
            dot.setFixedSize(22, 22)
            dot.setStyleSheet(f"QToolButton#ColorDot {{ background: {color}; }}")
            dot.clicked.connect(lambda _=False, c=color: self._set_color(c))
            self.color_buttons[color] = dot
            colour_row.addWidget(dot)
        colour_row.addStretch(1)
        colour_header = QHBoxLayout()
        colour_header.addWidget(self._section("Colour"), 1)
        theme_btn = QPushButton("Edit Theme…")
        theme_btn.setObjectName("LinkButton")
        theme_btn.setToolTip("Gradient, intensity, light/dark and grain")
        theme_btn.clicked.connect(self._edit_theme)
        colour_header.addWidget(theme_btn)
        layout.addLayout(colour_header)
        layout.addLayout(colour_row)

        # Profile
        layout.addWidget(self._section("Profile  ·  separate logins and cookies"))
        self.profile = QComboBox()
        self.profile.setObjectName("SpaceCombo")
        for name in browser.profile_names():
            self.profile.addItem("Default" if name == DEFAULT_PROFILE else name, name)
        self.profile.addItem("New Profile…", NEW_PROFILE)
        self.profile.setCurrentIndex(max(0, self.profile.findData(space.profile)))
        self.profile.activated.connect(self._profile_chosen)
        self.new_profile = QLineEdit()
        self.new_profile.setObjectName("SpaceName")
        self.new_profile.setPlaceholderText("New profile name, then Return")
        self.new_profile.returnPressed.connect(self._create_profile)
        self.new_profile.hide()
        layout.addWidget(self.profile)
        layout.addWidget(self.new_profile)

        # Actions
        line = QFrame()
        line.setObjectName("EditorLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        extra = [(icon, text, lambda _=False, act=act: (self.close(), act()), False)
                 for icon, text, act in browser.gather("space_actions", space)]  # (Space Rules…)
        for icon, text, slot, danger in (("ph.folder-plus", "New Folder", self._new_folder, False),
                                         ("ph.share-network", "Share Space…", self._share, False), *extra,
                                         ("ph.trash", "Delete Space…", self._delete, True)):
            btn = QPushButton(text)
            btn.setObjectName("DangerRow" if danger else "ActionRow")
            btn.setIcon(qta.icon(icon, color=QColor("#ef4444") if danger else self.ink))
            btn.setIconSize(QSize(15, 15))
            btn.clicked.connect(slot)
            layout.addWidget(btn)

        self.setFixedWidth(COLUMNS * 34 + 28 + 36)
        self._refresh()

    def _show_page(self, index):
        # The popover takes the height of whichever icon page is showing
        self.pages.setCurrentIndex(index)
        self.pages.setFixedHeight(self.pages.currentWidget().sizeHint().height())
        QTimer.singleShot(0, self._fit)  # once the layout has taken the new height

    def _fit(self):
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _section(self, text):
        label = QLabel(text.upper())
        label.setObjectName("EditorSection")
        return label

    def _grid(self, values, make_icon) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for i, value in enumerate(values):
            btn = QToolButton()
            btn.setObjectName("IconChoice")
            btn.setFixedSize(30, 30)
            btn.setIcon(make_icon(value))
            btn.setIconSize(QSize(20, 20) if value and not is_symbol(value) else QSize(18, 18))
            btn.setToolTip(value.removeprefix("ph.").replace("-", " ") if value else "No icon (colour dot)")
            btn.clicked.connect(lambda _=False, v=value: self._set_icon(v))
            self.icon_buttons[value] = btn
            grid.addWidget(btn, i // COLUMNS, i % COLUMNS)
        return page

    # --- Applying ---------------------------------------------------------------------------

    def _refresh(self):
        self.preview.setPixmap(space_icon(self.space, self.ink, 24).pixmap(22, 22))
        for value, btn in self.icon_buttons.items():
            btn.setProperty("selected", value == self.space.icon)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        for color, dot in self.color_buttons.items():
            dot.setProperty("selected", color.lower() == self.space.color.lower())
            dot.style().unpolish(dot)
            dot.style().polish(dot)

    def _changed(self, retheme=False):
        self.browser.space_edited(self.space, retheme)
        self._refresh()

    def _rename(self):
        name = self.name.text().strip()
        if name and name != self.space.name:
            self.space.name = name
            self._changed()

    def _set_icon(self, value):
        self.space.icon = value
        self._changed()

    def _set_color(self, color):
        self.space.color = color
        if self.space.theme.get("colors"):
            self.space.theme["colors"][0] = color
        self._changed(retheme=True)

    def _profile_chosen(self, index):
        name = self.profile.itemData(index)
        if name == NEW_PROFILE:
            self.new_profile.show()
            self.new_profile.setFocus()
        else:
            self.new_profile.hide()
            self.browser.set_space_profile(self.space, name)

    def _create_profile(self):
        name = re.sub(r"[^a-z0-9_-]+", "-", self.new_profile.text().strip().lower()).strip("-")
        if name:
            self.browser.set_space_profile(self.space, name)
            self.profile.insertItem(self.profile.count() - 1, name, name)
            self.profile.setCurrentIndex(self.profile.findData(name))
            self.new_profile.hide()

    def _edit_theme(self):
        self.close()
        self.browser.edit_space_theme(self.space)

    def _new_folder(self):
        self.close()
        self.browser.new_folder()

    def _share(self):
        self.close()
        self.browser.share_space(self.space)

    def _delete(self):
        self.close()
        self.browser.delete_space(self.space)

    def popup(self, pos: QPoint):
        self.adjustSize()
        screen = self.screen().availableGeometry()
        x = min(pos.x() - 18, screen.right() - self.width())
        y = min(pos.y() - 14, screen.bottom() - self.height())
        self.move(max(x, screen.left()), max(y, screen.top()))
        self.show()
        self.name.setFocus()
        self.name.selectAll()
