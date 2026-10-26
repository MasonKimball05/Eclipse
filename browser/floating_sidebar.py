# The hidden sidebar, Arc style: touch the window's left edge and it slides out over the page; move
# away and it slides back. It stays out while the pointer is anywhere between the edge and just past
# the sidebar (or a menu from it is open), and reverses smoothly from wherever it is if you come back
# while it's on its way out.
#
# Whether the pointer is still "in" is checked a few times a second instead of from enter/leave
# events: those don't come when the sidebar slides in under a pointer that hasn't moved, or when the
# pointer goes onto a menu.

import time

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from . import motion

EDGE = 8          # points from the window's edge that bring it out
MARGIN = 6        # where it sits: this far in from the window's edges
LINGER = 16       # points past its right edge the pointer can stray before it goes
HIDE_DELAY = 320  # ms out of bounds before it goes


class Shadow(QWidget):
    """A soft shadow under the floating sidebar, painted once (not a blur effect redrawing the whole
    sidebar on every frame of its slide)."""

    SPREAD = 36

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._picture = None

    def follow(self, rect):
        s = self.SPREAD
        self.setGeometry(rect.adjusted(-s, -s + 8, s, s + 8))

    def resizeEvent(self, event):
        self._picture = None

    def paintEvent(self, event):
        if self._picture is None or self._picture.size() != self.size() * self.devicePixelRatioF():
            self._picture = self._paint()
        QPainter(self).drawPixmap(0, 0, self._picture)

    def _paint(self):
        ratio = self.devicePixelRatioF()
        picture = QPixmap(self.size() * ratio)
        picture.setDevicePixelRatio(ratio)
        picture.fill(Qt.GlobalColor.transparent)
        p = QPainter(picture)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        s = self.SPREAD
        steps = 14
        for i in range(steps):  # rings of faint shadow, each a little smaller and darker: a soft edge
            inset = s * i / steps
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), 14 + s - inset, 14 + s - inset)
            p.fillPath(shape, QColor(0, 0, 0, 5))
        p.end()
        return picture


class FloatingSidebar:
    def __init__(self, window):
        self.window = window
        self.state = None  # None (docked, or hidden) · "in" (coming out, or out) · "out" (going back)
        self.shadow = None
        self._away_since = None
        self.timer = QTimer(window)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._check)

    @property
    def active(self) -> bool:
        return self.state is not None

    @property
    def sidebar(self):
        return self.window.sidebar

    def _x(self, x):
        self.sidebar.move(round(x), MARGIN)
        if self.shadow is not None:
            self.shadow.follow(self.sidebar.geometry())

    def edge_touched(self):
        """The pointer reached the window's left edge."""
        w = self.window
        if self.state == "in" or w.command_bar.isVisible() or not w.isActiveWindow():
            return
        if self.state is None:
            w._float_sidebar()  # out of the layout, over the page
            self.shadow = Shadow(w.centralWidget())
            self.place()
            self._x(-self.sidebar.width() - Shadow.SPREAD)
            self.shadow.show()
            self.sidebar.show()
            self.shadow.stackUnder(self.sidebar)
            self.sidebar.raise_()
            self.sidebar.align_with_traffic_lights()
            self.timer.start()
        self.state = "in"
        self._away_since = None
        motion.animate(self.sidebar, self.sidebar.x(), MARGIN, self._x, motion.NORMAL)  # (from wherever it is)

    def place(self):
        root = self.window.centralWidget()
        width = self.window.store.settings.get("sidebar_width", 250)
        self.sidebar.setGeometry(self.sidebar.x() if self.state else MARGIN, MARGIN, width, root.height() - 2 * MARGIN)
        if self.shadow is not None:
            self.shadow.follow(self.sidebar.geometry())

    def _wanted(self) -> bool:
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return True  # a menu from it (or a dialog) is open
        pos = self.window.centralWidget().mapFromGlobal(QCursor.pos())
        inside_window = -4 <= pos.y() <= self.window.centralWidget().height() + 4
        return inside_window and -40 <= pos.x() <= self.sidebar.geometry().right() + LINGER

    def _check(self):
        if self.state is None:
            self.timer.stop()
            return
        wanted = self._wanted()
        if self.state == "out":
            if wanted:
                self.edge_touched()  # came back on its way out: turn round
            return
        if wanted:
            self._away_since = None
            return
        now = time.monotonic()
        if self._away_since is None:
            self._away_since = now
        elif (now - self._away_since) * 1000 >= HIDE_DELAY:
            self.slide_away()

    def slide_away(self):
        if self.state != "in":
            return
        self.state = "out"
        motion.animate(self.sidebar, self.sidebar.x(), -self.sidebar.width() - Shadow.SPREAD, self._x,
                       motion.FAST + 40, self._gone)

    def _gone(self):
        if self.state != "out":
            return
        self.sidebar.hide()
        self.finish()
        self.window._dock_sidebar()

    def finish(self):
        """Back to normal (docked by the window, or about to be)."""
        self.state = None
        self.timer.stop()
        animation = getattr(self.sidebar, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        if self.shadow is not None:
            self.shadow.hide()
            self.shadow.deleteLater()
            self.shadow = None
