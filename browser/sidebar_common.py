# The sidebar's shared pieces: its sizes and colors, tab and site icons, hover fades, icon buttons, labels.
# (sidebar.py re-exports all of it, for whatever imports it from there.)

import re

import qtawesome as qta
from PyQt6.QtCore import QEvent, QObject, QRect, QRectF, QSize, QUrl, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QLabel, QSizePolicy, QToolButton

from .icons import symbol
from .models import display_host
from .theme import legible, letter_icon


TAB_MIME = "application/x-eclipse-tab"


TABS_MIME = "application/x-eclipse-tabs"  # several picked tabs dragged together: their ids, in order


PILL_ICON = 13


PILL_PADDING = 10       # the URL pill's padding (theme.py: QPushButton#UrlPill)


PILL_FONT_PX = 13       # its text's size there, and the smallest a long address is made before it's cut


PILL_MIN_FONT_PX = 11


PILL_ICON_DROP = 1.5  # the URL pill's icon sits this much lower than center, level with the lowercase text


def site_labels(host: str) -> int:
    """How many of a host's last labels name the site itself: cloudflare.com's 2, bbc.co.uk's 3 (a country's
    second level), an IP address's all."""
    labels = host.split(".")
    if all(part.isdigit() for part in labels) or ":" in host:
        return len(labels)
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"):
        return 3
    return min(2, len(labels))


def pill_icon(name, color) -> QIcon:
    """An icon for the URL pill, drawn PILL_ICON_DROP lower than centered: text's box has room above for
    capitals, so lowercase sits low in it, and a centered icon looked as if it floated above the address."""
    ratio = 3  # (sharp on any screen)
    pm = QPixmap(round(PILL_ICON * ratio), round((PILL_ICON + 2 * PILL_ICON_DROP) * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    qta.icon(name, color=color).paint(p, QRect(0, round(2 * PILL_ICON_DROP), PILL_ICON, PILL_ICON))
    p.end()
    return QIcon(pm)


# The GitHub Live Folder's sections (live_folders.SECTIONS); its other sub-folders are your repos
GITHUB_SECTION_ICONS = {"Review Requested": "ph.eye", "My Pull Requests": "ph.git-pull-request",
                        "Assigned to Me": "ph.user-circle", "Notifications": "ph.bell"}


FAVORITE_COLUMNS = 4


INDENT = 16  # per folder level


def is_away(tab) -> bool:
    """A pinned tab or favorite showing something other than the page it was pinned at."""
    if not tab.home_url or not tab.url:
        return False
    def place(u):
        # The site and path only: pages that add ?query or #fragment as they load haven't gone anywhere
        q = QUrl(u)
        return q.host().lower().removeprefix("www."), q.path().rstrip("/")
    return place(tab.url) != place(tab.home_url)


def is_answer_page(url: str) -> bool:
    """A Browse for Me answer (a page it wrote in the data folder's browse/): it gets a compass."""
    return url.startswith("file:") and "/browse/" in url and url.endswith(".html")


def tab_icon(tab, theme):
    if tab.url.startswith("note:"):
        return symbol("ph.note-pencil", theme.accent_text)
    if tab.url.startswith("easel:"):
        return symbol("ph.chalkboard-simple", theme.accent_text)
    if is_answer_page(tab.url):
        return symbol("ph.compass", theme.accent_text)
    if tab.icon:
        return legible(tab.icon, theme)
    host = display_host(tab.url)
    # A page on an address (127.0.0.1, 192.168…) or localhost: its title's first letter says more than "1"
    if not host or host[0].isdigit() or host.startswith("localhost") or host.startswith("["):
        host = tab.label
    return letter_icon(host, theme.accent)


def left_its_site(tab) -> bool:
    """A favorite (or pinned tab) whose page is on another site now, not just another page of its own."""
    return is_away(tab) and display_host(tab.url).lower() != display_host(tab.home_url).lower()


def home_icon(tab, theme):
    """The icon of the site a favorite was made for: its tile keeps it while its tab's elsewhere (YouTube's tile
    stays YouTube's after you've gone on to google.com in it), and is the page's own while it's on that site."""
    if not left_its_site(tab):
        return tab_icon(tab, theme)
    from . import storage
    icon = storage.favicon(tab.home_url)
    return legible(icon, theme) if icon is not None else letter_icon(display_host(tab.home_url), theme.accent)


def faded(icon, opacity, size=32):
    """A see-through copy of an icon (for inactive emoji, which can't be tinted)."""
    source = icon.pixmap(size, size)
    out = QPixmap(source.size())
    out.setDevicePixelRatio(source.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setOpacity(opacity)
    p.drawPixmap(0, 0, source)
    p.end()
    return QIcon(out)


def repolish(widget, **props):
    # Qt only re-evaluates [prop="..."] stylesheet selectors after an unpolish/polish (and that's
    # costly: skipped when nothing's changed, as on most sidebar refreshes)
    if all(widget.property(name) == value for name, value in props.items()):
        return
    for name, value in props.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_shown(widget, shown):
    """setVisible, skipped when it's already so (each call can restyle the widget)."""
    if widget.isHidden() == bool(shown):
        widget.setVisible(bool(shown))


def spoken(text: str) -> str:
    """A tooltip as VoiceOver should say it: without the keyboard shortcut in brackets."""
    return re.sub(r"\s*\((?:[⌘⌥⇧⌃]+.*?|.*?⌘.*?)\)\s*$", "", text).strip()


PILL_PLACEHOLDER = "Search or Enter URL…"


SPACE_BUTTON = 26  # (a Space's button in the bottom bar, when there's room)


SPACE_BUTTON_SMALLEST = 18  # (with many Spaces in a narrow sidebar: fewer show, rather than smaller)


SPACE_MORE = 10  # (the arrows beside them that say some are hidden, and where)


FOLDER_COLORS = [("Red", "#ef4444"), ("Orange", "#f97316"), ("Yellow", "#eab308"), ("Green", "#22c55e"),
                 ("Teal", "#14b8a6"), ("Blue", "#3b82f6"), ("Purple", "#8b5cf6"), ("Pink", "#ec4899")]


FOLDER_ICONS = [("Star", "ph.star"), ("Briefcase", "ph.briefcase"), ("Book", "ph.book-open"), ("Code", "ph.code"),
                ("Heart", "ph.heart"), ("Music", "ph.music-notes"), ("House", "ph.house"),
                ("Graduation", "ph.graduation-cap"), ("Flask", "ph.flask"), ("Game", "ph.game-controller"),
                ("Airplane", "ph.airplane"), ("Shopping", "ph.shopping-bag")]


COMPACT_WIDTH = 76  # the collapsed sidebar: just its tabs' icons (room for the traffic lights above them)


SPACE_SHOWN = 3  # (at most this many at once, at a size that's easy to hit; the arrows and swipes reach the rest)


class SpokenTooltips(QObject):
    """Keeps an icon-only button's name for VoiceOver in step with its tooltip."""

    def watch(self, button):
        button.installEventFilter(self)
        self._sync(button)

    def _sync(self, button):
        if not button.text():
            button.setAccessibleName(spoken(button.toolTip()))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ToolTipChange and isinstance(obj, QToolButton):
            self._sync(obj)
        return False


class HoverFade:
    """Mixin: the hover highlight fades in quickly and out a touch slower, as in Arc, instead of snapping
    on and off (a stylesheet :hover can only snap). Rows paint it over themselves (they're see-through);
    buttons (_hover_under) paint it under their icon, so the icon stays crisp."""

    _hover = 0.0
    _hover_radius = 9
    _hover_under = False

    def _fade_hover(self, target, ms):
        from . import motion

        def step(value):
            self._hover = value
            self.update()
        motion.animate(self, self._hover, target, step, ms)

    def enterEvent(self, event):
        self._fade_hover(1.0, 90)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._fade_hover(0.0, 160)
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._hover_under:
            self.paint_hover()
            super().paintEvent(event)
        else:
            super().paintEvent(event)  # (its stylesheet: the selected highlight, if it's selected)
            self.paint_hover()

    def paint_hover(self, painter=None):
        if self._hover <= 0.001 or self.property("selected") is True or not self.isEnabled():
            return
        from .theme import hover_color
        theme = getattr(getattr(self, "browser", None), "theme", None) or getattr(self.window(), "theme", None)
        if theme is None:
            return
        color = hover_color(theme)
        color.setAlphaF(color.alphaF() * self._hover)
        p = painter or QPainter(self)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(self.rect()), self._hover_radius, self._hover_radius)
        p.restore()
        if painter is None:
            p.end()


LIGHTS_GAP = 16          # after the traffic lights, before the first button


LIGHTS_ROW_SPACING = 8   # between the top row's buttons


class IconButton(HoverFade, QToolButton):
    _hover_radius, _hover_under = 6, True

    def __init__(self, icon_name, tooltip="", size=26, icon_size=16, parent=None):
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.icon_name = icon_name
        self.setToolTip(tooltip)
        self.setAccessibleName(spoken(tooltip))  # (an icon alone says nothing to VoiceOver)
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, color):
        key = (self.icon_name, QColor(color).rgba())
        if getattr(self, "_icon_key", None) != key:  # unchanged color: keep the icon we have
            self._icon_key = key
            self.setIcon(qta.icon(self.icon_name, color=color, color_disabled=_with_alpha(color, 0.3)))


def _with_alpha(color, alpha):
    c = color.__class__(color)
    c.setAlphaF(alpha)
    return c


class ElidedLabel(QLabel):
    # A QLabel that shrinks with "…" instead of forcing the sidebar wider
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        # Page and feed titles are text: "<img>: The Image Embed element" mustn't be drawn as HTML
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._refresh()

    def set_full_text(self, text):
        self._full = text
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 10)))


ROW_HEIGHT = 34
ROW_BUTTON, ROW_BUTTON_ICON = 22, 13  # (its close and mute buttons)


ROW_SPACING = 9


WAKE_AHEAD_MS = 300  # the pointer resting on a sleeping tab this long, and it starts waking


_row_icons = {}


def _row_icon(name, color) -> QIcon:
    """A row button's icon, made once per name and color (every row shares them)."""
    key = (name, QColor(color).rgba())
    if key not in _row_icons:
        _row_icons[key] = qta.icon(name, color=color)
    return _row_icons[key]


def compact(browser) -> bool:
    """Whether the sidebar's collapsed to its tabs' icons (Sidebar.set_compact)."""
    sidebar = getattr(browser, "sidebar", None)
    return bool(getattr(sidebar, "compact", False))
