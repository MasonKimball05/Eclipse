# Space icons: either a line icon from the Phosphor set ("ph.planet") or an emoji ("🎮").
# Line icons are drawn in the theme's text colour, so a row of them stays calm and readable;
# emoji keep their own colours. No icon falls back to a dot in the Space's colour.

from functools import lru_cache

import qtawesome as qta
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from .theme import dot_icon

# Curated for Spaces, in the order the picker shows them
SYMBOLS = [
    "ph.house", "ph.briefcase", "ph.code", "ph.terminal-window", "ph.book-open", "ph.graduation-cap",
    "ph.notebook", "ph.pencil", "ph.palette", "ph.paint-brush-broad", "ph.camera", "ph.film-strip",
    "ph.music-notes", "ph.headphones", "ph.game-controller", "ph.trophy", "ph.barbell", "ph.bicycle",
    "ph.heart", "ph.star", "ph.sparkle", "ph.crown", "ph.diamond", "ph.gift",
    "ph.planet", "ph.globe", "ph.rocket", "ph.lightning", "ph.fire", "ph.sun",
    "ph.moon", "ph.leaf", "ph.tree", "ph.coffee", "ph.pizza", "ph.shopping-cart",
    "ph.airplane", "ph.car", "ph.compass", "ph.anchor", "ph.flag", "ph.chart-line",
    "ph.currency-dollar", "ph.bank", "ph.buildings", "ph.flask", "ph.atom", "ph.cpu",
    "ph.wrench", "ph.bug", "ph.newspaper", "ph.calendar", "ph.chat-circle", "ph.envelope",
    "ph.users", "ph.user", "ph.dog", "ph.cat", "ph.first-aid", "ph.smiley",
]

EMOJI = ["🏠", "💼", "💻", "📚", "🎓", "🎨", "🎵", "🎮", "⚽️", "🏋️", "✈️", "🌍",
         "🔥", "⭐️", "🚀", "💡", "🧪", "💰", "🛒", "🍕", "☕️", "🌱", "❤️", "✝️"]


def is_symbol(value: str) -> bool:
    return value.startswith("ph.")


@lru_cache(maxsize=256)  # drawing emoji is slow (Apple Color Emoji), and the same few repeat
def emoji_icon(text: str, size: int = 32) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    font = QFont()
    font.setPixelSize(int(size * 0.82))
    p.setFont(font)
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=1024)
def _symbol(name: str, rgba: int, active_rgba: int | None) -> QIcon:
    kwargs = {"color": QColor.fromRgba(rgba)}
    if active_rgba is not None:
        kwargs["color_active"] = QColor.fromRgba(active_rgba)
    return qta.icon(name, **kwargs)


def symbol(name: str, color, active=None) -> QIcon:
    """A cached line icon. The sidebar asks for the same few icons on every tab switch."""
    color = QColor(color)
    return _symbol(name, color.rgba(), QColor(active).rgba() if active is not None else None)


def space_icon(space, color: QColor, size: int = 32) -> QIcon:
    """The icon for a Space: its symbol (tinted), its emoji, or a dot in its colour."""
    value = getattr(space, "icon", "") or ""
    if is_symbol(value):
        try:
            return symbol(value, color)
        except Exception:  # an icon name from a newer icon set; fall through to the dot
            pass
    elif value:
        return emoji_icon(value, size)
    return dot_icon(space.color, max(size // 2, 10))
