# The Space editor: a small popover (right-click a Space) to rename it, pick its icon and
# color, choose its profile, and reach the theme editor. Changes apply as you make them.
# It also has this Space's "just the background" switch for the start view (as right-clicking that has),
# being where you'd look while picking a background.

import re
import sys

import qtawesome as qta
from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import screens
from .icons import EMOJI, SYMBOLS, emoji_icon, is_symbol, space_icon
from .theme import PALETTE, dot_icon, stylesheet
from .webview import DEFAULT_PROFILE

COLUMNS = 8
NEW_PROFILE = "__new__"


_PICTURES: dict = {}  # {ink: {key: PNG}}: the icon grid's pictures, made once per color


def native_space_editor(browser, space, pos, beside=None):
    """The Space editor as a native popover from the Space's name (forms.py), changes applying as they're made; None
    where it can't be (Qt's popover then)."""
    from . import forms
    from .start_view import shown_in
    ink = browser.theme.panel_text
    symbols = _PICTURES.get(ink.name())
    if symbols is None:
        from .native import png
        symbols = _PICTURES[ink.name()] = {f"s{i}": png(qta.icon(v, color=ink).pixmap(36, 36))
                                           for i, v in enumerate(SYMBOLS)}
    keys = {v: f"s{i}" for i, v in enumerate(SYMBOLS)}
    profiles = browser.profile_names()
    own_emoji = space.icon if space.icon and not is_symbol(space.icon) and space.icon not in EMOJI else ""
    state = {"name": space.name, "icon": space.icon, "color": space.color, "profile": space.profile,
             "plain": not shown_in(browser.store, space), "emoji": own_emoji}  # (as the field starts)

    def changed(values):
        name = str(values.get("name", "")).strip()
        if name and name != space.name:
            space.name = name
            browser.space_edited(space)
        icon = str(values.get("icon", ""))
        custom = str(values.get("emoji", "")).strip()[:4]
        if custom and custom != state.get("emoji"):
            icon = custom
        state["emoji"] = custom
        if icon != state["icon"] and (icon in SYMBOLS or icon in EMOJI or icon == custom or icon == ""):
            state["icon"] = space.icon = icon
            browser.space_edited(space)
        color = str(values.get("color", ""))
        if color.startswith("#") and color.lower() != space.color.lower():
            space.color = color
            if space.theme.get("colors"):
                space.theme["colors"][0] = color
            browser.space_edited(space, retheme=True)
        profile = values.get("profile")
        if profile in profiles and profile != space.profile:
            browser.set_space_profile(space, profile)
        plain = bool(values.get("plain"))
        if plain != state["plain"]:
            state["plain"] = plain
            browser.content.empty.set_shown(not plain, space)

    form = None

    def act(_form, field, values):
        if field == "create":
            made = re.sub(r"[^a-z0-9_-]+", "-", str(values.get("new_profile", "")).strip().lower()).strip("-")
            if made:
                browser.set_space_profile(space, made)
            return
        form.close()
        extra = {f"x{i}": action for i, (_icon, _text, action) in enumerate(browser.gather("space_actions", space))}
        {"theme": lambda: browser.edit_space_theme(space), "folder": lambda: browser.new_folder(space=space),
         "share": lambda: browser.share_space(space), "delete": lambda: browser.delete_space(space),
         **extra}.get(field, lambda: None)()

    extras = [{"id": f"x{i}", "kind": "action", "label": "", "title": text}
              for i, (_icon, text, _action) in enumerate(browser.gather("space_actions", space))]
    choices = [{"value": v, "image": keys[v], "label": v.removeprefix("ph.").replace("-", " ")} for v in SYMBOLS] + \
        [{"value": e, "text": e, "label": e} for e in EMOJI] + [{"value": "", "text": "●", "label": "No icon (color dot)"}]
    form = forms.open_form(
        browser, "Edit Space",
        [forms.section({"id": "name", "kind": "text", "label": "Name", "value": space.name}),
         forms.section({"id": "icon", "kind": "choice", "columns": COLUMNS, "value": space.icon, "options": choices},
                       {"id": "emoji", "kind": "text", "label": "Other emoji", "placeholder": "⌃⌘Space",
                        "value": own_emoji}, header="Icon"),
         forms.section({"id": "color", "kind": "color", "label": "Color", "value": space.color},
                       {"id": "palette", "kind": "swatches", "label": "", "target": "color",
                        "options": [{"value": c, "label": n} for n, c in PALETTE]},
                       {"id": "theme", "kind": "action", "label": "", "title": "Edit Theme…"}),
         forms.section({"id": "profile", "kind": "menu", "label": "Profile", "value": space.profile,
                        "options": [{"value": n, "label": "Default" if n == DEFAULT_PROFILE else n} for n in profiles]
                        + [{"value": NEW_PROFILE, "label": "New Profile…"}]},
                       {"id": "new_profile", "kind": "text", "label": "New profile",
                        "shown_when": {"id": "profile", "is": [NEW_PROFILE]}},
                       {"id": "create", "kind": "action", "label": "", "title": "Create Profile",
                        "shown_when": {"id": "profile", "is": [NEW_PROFILE]}},
                       footer="A profile has its own logins and cookies."),
         forms.section({"id": "plain", "kind": "toggle", "label": "Just the background with no tab open",
                        "value": state["plain"]}),
         forms.section({"id": "folder", "kind": "action", "label": "", "title": "New Folder"},
                       {"id": "share", "kind": "action", "label": "", "title": "Share Space…"}, *extras,
                       {"id": "delete", "kind": "action", "label": "", "title": "Delete Space…"})],
        [], lambda *_: None, heading="", width=340, height=640, on_change=changed, on_field=act, popover_at=pos,
        popover_beside=beside, images=symbols)
    return form


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

        # Color
        color_row = QHBoxLayout()
        color_row.setSpacing(6)
        self.color_buttons = {}
        for name, color in PALETTE:
            dot = QToolButton()
            dot.setObjectName("ColorDot")
            dot.setToolTip(name)
            dot.setFixedSize(22, 22)
            dot.setStyleSheet(f"QToolButton#ColorDot {{ background: {color}; }}")
            dot.clicked.connect(lambda _=False, c=color: self._set_color(c))
            self.color_buttons[color] = dot
            color_row.addWidget(dot)
        color_row.addStretch(1)
        color_header = QHBoxLayout()
        color_header.addWidget(self._section("Color"), 1)
        theme_btn = QPushButton("Edit Theme…")
        theme_btn.setObjectName("LinkButton")
        theme_btn.setToolTip("Gradient, intensity, light/dark and grain")
        theme_btn.clicked.connect(self._edit_theme)
        color_header.addWidget(theme_btn)
        layout.addLayout(color_header)
        layout.addLayout(color_row)

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

        # The start view (with no tab open): the search and sites, or just the background
        from .start_view import shown_in
        self.plain = QCheckBox("Just the background with no tab open")
        self.plain.setToolTip("No search, most visited or recently closed tabs in this Space: just its colors. "
                              "Right-clicking the start view does the same.")
        self.plain.setChecked(not shown_in(browser.store, space))
        self.plain.toggled.connect(lambda on: browser.content.empty.set_shown(not on, space))
        layout.addWidget(self.plain)

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
            btn.setToolTip(value.removeprefix("ph.").replace("-", " ") if value else "No icon (color dot)")
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
        self.browser.new_folder(space=self.space)  # (this Space's: asked from another's menu, it went in yours)

    def _share(self):
        self.close()
        self.browser.share_space(self.space)

    def _delete(self):
        self.close()
        self.browser.delete_space(self.space)

    def popup(self, pos: QPoint):
        self.adjustSize()
        screen = screens.area_at(pos, self)  # (the screen you clicked on, not the window's)
        x = min(pos.x() - 18, screen.right() - self.width())
        y = min(pos.y() - 14, screen.bottom() - self.height())
        self.move(max(x, screen.left()), max(y, screen.top()))
        self.show()
        self.name.setFocus()
        self.name.selectAll()
