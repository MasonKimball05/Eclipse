# The download arc: as a download starts, its file's icon flies in an arc from where you clicked to the Downloads
# button at the foot of the sidebar, shrinking as it goes, and the button gives a little bounce as it lands. So you
# see where it went without reading a toast. (An idea from Zen, which does the same.)
#
# Not with the sidebar hidden (there's no button to fly to), not for a private window's downloads (they're not in
# this window), and not with macOS's Reduce Motion, or Low Power Mode, on (motion.reduced()).

import math

from PyQt6.QtCore import QEasingCurve, QFileInfo, QPoint, QPointF, QRect, QSize, Qt
from PyQt6.QtGui import QCursor, QPainter
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFileIconProvider, QWidget

from . import motion
from .features import Feature

FLIGHT_MS = 620
BOUNCE_MS = 260
ICON = 30       # the icon's size as it sets off; it lands at LANDED of that
LANDED = 0.45
LIFT = 110      # how far above the higher end the arc rises


def arc_point(start: QPointF, end: QPointF, t: float, lift: float = LIFT) -> QPointF:
    """A point t (0 to 1) along the arc: a curve through a point above both ends."""
    top = min(start.y(), end.y()) - lift
    control = QPointF((start.x() + end.x()) / 2, top)
    u = 1 - t
    return start * (u * u) + control * (2 * u * t) + end * (t * t)


class Flight(QWidget):
    """The icon on its way: a see-through widget over the window, moved and shrunk each frame."""

    def __init__(self, parent, pixmap, start: QPointF, end: QPointF, done):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.pixmap, self.start, self.end, self.done_ = pixmap, start, end, done
        self.t = 0.0
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        motion.animate(self, 0.0, 1.0, self._step, FLIGHT_MS, self._landed, QEasingCurve.Type.InOutCubic)

    def _step(self, t):
        self.t = t
        self.update()

    def _landed(self):
        self.hide()
        self.deleteLater()
        self.done_()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = ICON * (1 - (1 - LANDED) * self.t)
        at = arc_point(self.start, self.end, self.t)
        painter.setOpacity(1.0 - 0.35 * self.t)
        painter.drawPixmap(QRect(round(at.x() - size / 2), round(at.y() - size / 2), round(size), round(size)),
                           self.pixmap)


def bounce(button, ms=BOUNCE_MS):
    """The button's icon grows a little and settles back, as something lands in it."""
    base = button.iconSize()

    def step(t):
        grow = math.sin(math.pi * t) * 5
        button.setIconSize(QSize(round(base.width() + grow), round(base.height() + grow)))
    motion.animate(button, 0.0, 1.0, step, ms, lambda: button.setIconSize(base))


class DownloadArcFeature(Feature):
    name = "download_arc"

    def download_started(self, item, download):
        window = self.window
        button = window.sidebar.downloads_btn
        page = download.page()
        view = QWebEngineView.forPage(page) if page is not None else None
        if motion.reduced() or not button.isVisible() or view is None or view.window() is not window:
            return None
        host = window.centralWidget()
        start = QPointF(host.mapFromGlobal(QCursor.pos()))
        if not host.rect().contains(start.toPoint()):  # (a download that started without a click: from the page)
            start = QPointF(host.rect().center())
        end = QPointF(button.mapTo(host, QPoint(button.width() // 2, button.height() // 2)))
        icon = QFileIconProvider().icon(QFileInfo(download.downloadFileName() or "file"))
        pixmap = icon.pixmap(ICON * 2, ICON * 2)
        return Flight(host, pixmap, start, end, lambda: bounce(button))


FEATURE = DownloadArcFeature
