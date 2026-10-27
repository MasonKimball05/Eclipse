# The hidden sidebar, Arc style: touch the window's left edge and it slides out over the page; move
# away and it slides back. It stays out while the pointer is anywhere between the edge and just past
# the sidebar (or a menu from it is open), and reverses smoothly from wherever it is if you come back
# while it's on its way out.
#
# Whether the pointer is still "in" is checked a few times a second instead of from enter/leave
# events: those don't come when the sidebar slides in under a pointer that hasn't moved, or when the
# pointer goes onto a menu.
#
# With no tab open there's nothing to hide it for: it comes out by itself and stays (keep), and goes back
# to coming and going with the pointer once a tab opens.

import time

from PyQt6.QtCore import QAbstractAnimation, QPoint, QRectF, Qt, QTimer
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
        self.kept = False  # (out while no tab is open, wherever the pointer is)
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

    @property
    def _right(self) -> bool:
        return self.window.sidebar_on_right

    def _out_x(self):
        """Where it sits when it's out."""
        root = self.window.centralWidget()
        return root.width() - self.sidebar.width() - MARGIN if self._right else MARGIN

    def _away_x(self):
        """Just past the window's edge, shadow and all."""
        root = self.window.centralWidget()
        return root.width() + Shadow.SPREAD if self._right else -self.sidebar.width() - Shadow.SPREAD

    def _x(self, x):
        self.sidebar.move(round(x), MARGIN)
        if self.shadow is not None:
            self.shadow.follow(self.sidebar.geometry())

    def edge_touched(self):
        """The pointer reached the window's edge (the sidebar's side)."""
        w = self.window
        if self.state == "in" or w.command_bar.isVisible() or not w.isActiveWindow():
            return
        self._come_out()

    def keep(self, on: bool):
        """Out and staying out (no tab is open), or back to following the pointer."""
        self.kept = on
        if on and self.state != "in":
            self._come_out()

    def _come_out(self):
        w = self.window
        if self.state is None:
            w._float_sidebar()  # out of the layout, over the page
            self.shadow = Shadow(w.centralWidget())
            self.place()
            self._x(self._away_x())
            self.shadow.show()
            self.sidebar.show()
            self.shadow.stackUnder(self.sidebar)
            self.sidebar.raise_()
            self._lights(False)  # (where they'll be once it's out, so its buttons line up with them)
            self.sidebar.align_with_traffic_lights(QPoint(round(self._out_x()), MARGIN))
            self.timer.start()
        self.state = "in"
        self._away_since = None
        motion.animate(self.sidebar, self.sidebar.x(), self._out_x(), self._x, motion.NORMAL,  # (from wherever it is)
                       lambda: self.state == "in" and self._lights(True))

    def place(self):
        root = self.window.centralWidget()
        width = self.window.store.settings.get("sidebar_width", 250)
        moving = getattr(getattr(self.sidebar, "_pb_animation", None), "state", lambda: None)() == \
            QAbstractAnimation.State.Running
        x = self.sidebar.x() if moving or self.state == "out" else (
            root.width() - width - MARGIN if self._right else MARGIN)
        self.sidebar.setGeometry(x, MARGIN, width, root.height() - 2 * MARGIN)
        if moving and self.state == "in":  # the window changed size on its way out: aim for the new spot
            motion.animate(self.sidebar, x, self._out_x(), self._x, motion.FAST,
                           lambda: self.state == "in" and self._lights(True))  # (this replaced the one that would)
        if self.shadow is not None:
            self.shadow.follow(self.sidebar.geometry())
        if self.state == "in":  # (a resize: macOS may have laid the lights out again, and it's moved)
            showing = not moving and self.window.traffic_lights.showing
            self._lights(showing)
            QTimer.singleShot(0, lambda: self.state == "in" and self._lights(showing))

    def _lights(self, showing):
        """The traffic lights, in the sidebar as it sits when it's out: where they are on the docked
        sidebar, but MARGIN in from its corner with it (rather than squeezed into its rounded corner).
        They come out once it has, and go as it leaves."""
        if self.window.isFullScreen():  # (full screen: macOS shows them in its menu bar, where they are)
            self.window.traffic_lights.place(True, 0, 0)
            return
        self.window.traffic_lights.place(showing, self._out_x(), MARGIN)

    def _wanted(self) -> bool:
        if self.kept:
            return True  # (no tab open: nothing to hide it for)
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return True  # a menu from it (or a dialog) is open
        pos = self.window.centralWidget().mapFromGlobal(QCursor.pos())
        inside_window = -4 <= pos.y() <= self.window.centralWidget().height() + 4
        if self._right:
            return inside_window and self.sidebar.geometry().left() - LINGER <= pos.x() <= self.window.centralWidget().width() + 40
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
        self._lights(False)
        motion.animate(self.sidebar, self.sidebar.x(), self._away_x(), self._x,
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
        self.kept = False
        self.timer.stop()
        animation = getattr(self.sidebar, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        if self.shadow is not None:
            self.shadow.hide()
            self.shadow.deleteLater()
            self.shadow = None
