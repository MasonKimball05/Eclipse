# Where a window or popup goes: on the screen it's meant for, and all of it on that screen (not under the Dock or
# the menu bar, nor across onto the next display).

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication


def area_at(pos: QPoint, widget=None) -> QRect | None:
    """The usable part (without the menu bar and Dock) of the screen at `pos`, a global point: the one you clicked
    on, which needn't be the one most of the window is on. Else `widget`'s screen's, else the primary's."""
    screen = QGuiApplication.screenAt(pos) or (widget.screen() if widget is not None else None) \
        or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def fit(rect: QRect, area: QRect | None) -> QRect:
    """`rect`, no bigger than `area` and moved in so all of it is inside."""
    if area is None:
        return QRect(rect)
    width, height = min(rect.width(), area.width()), min(rect.height(), area.height())
    x = max(area.left(), min(rect.left(), area.right() + 1 - width))
    y = max(area.top(), min(rect.top(), area.bottom() + 1 - height))
    return QRect(x, y, width, height)
