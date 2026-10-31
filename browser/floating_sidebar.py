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
#
# The sidebar collapsed to its icons spreads out to the same place when the pointer rests on it (SpreadSidebar).

import sys
import time

from PyQt6.QtCore import QAbstractAnimation, QEvent, QPoint, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

from . import motion

MAC_LIGHTS = sys.platform == "darwin"  # (the traffic lights are only macOS's)

EDGE = 8          # points from the window's edge that bring it out
MARGIN = 6        # where it sits: this far in from the window's edges
LINGER = 16       # points past its right edge the pointer can stray before it goes
HIDE_DELAY = 320  # ms out of bounds before it goes


class Shadow(QWidget):
    """A soft shadow under the floating sidebar, painted once for each size (not a blur effect redrawing the whole
    sidebar on every frame of its slide; nor drawn again every time it comes out, the same size)."""

    SPREAD = 36
    _pictures = {}  # (width, height, ratio): its picture

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def follow(self, rect):
        s = self.SPREAD
        self.setGeometry(rect.adjusted(-s, -s + 8, s, s + 8))

    def paintEvent(self, event):
        QPainter(self).drawPixmap(0, 0, self.picture())

    def picture(self):
        key = (self.width(), self.height(), self.devicePixelRatioF())
        if key not in Shadow._pictures:
            if len(Shadow._pictures) > 8:  # (sizes from window sizes gone by)
                Shadow._pictures.clear()
            Shadow._pictures[key] = self._paint()
        return Shadow._pictures[key]

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
        width = self.sidebar.minimumWidth() if self.sidebar.compact else self.window.store.settings.get("sidebar_width", 250)
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


REST_MS = 350  # resting on the collapsed strip this long spreads it out


def held() -> bool:
    """A mouse button's down (on its own, so the tests can say what yours are doing)."""
    return QApplication.mouseButtons() != Qt.MouseButton.NoButton
SPREAD_SETTING = "sidebar_spread"  # (whether it does: on unless turned off)
SHOWN_BY = 0.35  # how far through unfolding it's all there (it fades in from the strip as it grows)


class Unfold(QWidget):
    """The collapsed sidebar spreading out, or folding back: its card, pictured, uncovered from the strip's width to
    its own over a picture of what's beside it, so nothing is laid out on the way (a crossfade washed it out)."""

    def __init__(self, parent, area, under, card, card_rect, shadow, shadow_rect, start, right):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(area)
        self.under, self.card, self.shadow = under, card, shadow
        self.card_rect = QRectF(card_rect.translated(-area.topLeft()))
        self.shadow_rect = QRectF(shadow_rect.translated(-area.topLeft()))
        self.start, self.right = start, right
        self.t = 0.0
        self.show()
        self.raise_()

    def step(self, t):
        self.t = t
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self.under)
        card = self.card_rect
        width = self.start + (card.width() - self.start) * self.t
        shown = QRectF(card)
        if self.right:
            shown.setLeft(card.right() - width)
        else:
            shown.setWidth(width)
        p.setOpacity(min(1.0, self.t / SHOWN_BY))
        grow = shown.width() - card.width()  # (its shadow, as wide as the card is so far)
        p.drawPixmap(self.shadow_rect.adjusted(-grow if self.right else 0, 0, 0 if self.right else grow, 0),
                     self.shadow, QRectF(self.shadow.rect()))
        edge = QPainterPath()
        edge.addRoundedRect(shown, 12, 12)
        p.setClipPath(edge)
        p.drawPixmap(card.topLeft(), self.card)
        p.end()


class SpreadSidebar:
    """Collapsed to its icons (Zen's expand on hover): rest the pointer on the strip and the whole sidebar spreads out
    over the page, where the hidden one slides out to; move off it and it folds back to the strip. A placeholder
    keeps the strip's room in the layout meanwhile, so the page beside it doesn't move. A click on the strip first
    means it's being used as it is: it waits till the pointer comes back."""

    def __init__(self, window):
        self.window = window
        self.active = False
        self.shadow = None
        self.placeholder = None
        self.unfolding = None
        self._away_since = None
        self.rest = QTimer(window)
        self.rest.setSingleShot(True)
        self.rest.setInterval(REST_MS)
        self.rest.timeout.connect(self._rested)
        self.timer = QTimer(window)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._check)

    @property
    def sidebar(self):
        return self.window.sidebar

    def _can(self) -> bool:
        w, s = self.window, self.sidebar
        return (s.compact and not self.active and not w.floating_sidebar.active and s.isVisible()
                and not w._fullscreen and w.isActiveWindow() and not w.command_bar.isVisible()
                and not getattr(s, "drag_active", False) and not held()
                and QApplication.activePopupWidget() is None and QApplication.activeModalWidget() is None
                and w.store.settings.get(SPREAD_SETTING, True))

    def entered(self):
        """The pointer came onto the docked strip."""
        if self._can():
            self.rest.start()
            self.timer.start()

    def pressed(self):
        self.rest.stop()

    def _over_strip(self) -> bool:
        return self.sidebar.rect().contains(self.sidebar.mapFromGlobal(QCursor.pos()))

    def _rested(self):
        if self._can() and self._over_strip():
            self.spread()

    def _rect(self):
        root = self.window.centralWidget()
        width = self.window.store.settings.get("sidebar_width", 250)
        x = root.width() - width - MARGIN if self.window.sidebar_on_right else MARGIN
        return QRect(x, MARGIN, width, root.height() - 2 * MARGIN)

    def spread(self):
        w, s = self.window, self.sidebar
        root = w.centralWidget()
        rect = self._rect()
        strip = QRect(s.mapTo(root, QPoint(0, 0)), s.size())
        self._stop_unfolding()
        area = self._area(strip, rect)
        under = root.grab(area) if self._animating() else None
        self.placeholder = QWidget()
        self.placeholder.setFixedWidth(s.width())
        w.splitter.replaceWidget(w._sidebar_index, self.placeholder)
        s.set_compact(False)
        w._float_sidebar()
        s.setGeometry(rect)
        self.shadow = Shadow(root)
        self.shadow.follow(rect)
        self.shadow.show()
        s.show()
        self.shadow.stackUnder(s)
        s.raise_()
        self._lights()
        s.align_with_traffic_lights(rect.topLeft())
        self.active = True
        self._away_since = None
        self.timer.start()
        if under is not None:
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)  # (laid out as it'll be, to be pictured)
            self._unfold(area, under, s.grab(QRect(QPoint(0, 0), s.size())), rect, strip, 0.0, 1.0)

    def _animating(self) -> bool:
        return not motion.reduced() and self.window.isVisible()

    def _area(self, strip, card):
        return strip.united(card).adjusted(-Shadow.SPREAD, 0, Shadow.SPREAD, Shadow.SPREAD).intersected(
            self.window.centralWidget().rect())

    def _unfold(self, area, under, picture, card, strip, start, end, done=None):
        """Uncover the card (start 0 → end 1), or cover it back up to the strip (1 → 0)."""
        right = self.window.sidebar_on_right
        across = card.right() - strip.left() + 1 if right else strip.right() - card.x() + 1  # (the strip's width of it)
        self.unfolding = Unfold(self.window.centralWidget(), area, under, picture, card, self._shadow_picture(card),
                                self._shadow_rect(card), max(across, 0), right)
        self.unfolding.step(start)
        unfolding = self.unfolding
        motion.animate(unfolding, start, end, unfolding.step, motion.NORMAL,
                       lambda: unfolding is self.unfolding and (self._stop_unfolding(), done and done()))

    @staticmethod
    def _shadow_rect(card):
        spread = Shadow.SPREAD
        return card.adjusted(-spread, -spread + 8, spread, spread + 8)

    def _shadow_picture(self, card):
        shadow = Shadow(None)  # (the picture's the one the shadow under it shows: drawn once for its size)
        shadow.resize(self._shadow_rect(card).size())
        picture = shadow.picture()
        shadow.deleteLater()
        return picture

    def _stop_unfolding(self):
        unfolding, self.unfolding = self.unfolding, None
        if unfolding is None:
            return
        # Its animation stopped first, while everything's still there: deleted running (with the picture, later), Qt
        # would call its finish from inside the deletion, a crash (as when ⌥⌘S folds it before it's quite out)
        animation = getattr(unfolding, "_pb_animation", None)
        if animation is not None:
            animation.stop()  # (its finish: `unfolding is self.unfolding` is no longer so, and nothing happens)
        unfolding.hide()
        unfolding.deleteLater()

    def _lights(self):
        if MAC_LIGHTS and not self.window.isFullScreen():
            self.window.traffic_lights.place(self.window.traffic_lights.showing, self.sidebar.x(), MARGIN)

    def place(self):
        """The window changed size while it's out."""
        rect = self._rect()
        self.sidebar.setGeometry(rect)
        self.shadow.follow(rect)
        self._lights()

    def _wanted(self) -> bool:
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return True  # a menu from it (or a dialog) is open
        if getattr(self.sidebar, "drag_active", False):
            return True
        root = self.window.centralWidget()
        pos = root.mapFromGlobal(QCursor.pos())
        card = self.sidebar.geometry()
        if self.window.sidebar_on_right:
            return -4 <= pos.y() <= root.height() + 4 and card.left() - LINGER <= pos.x() <= root.width() + 40
        return -4 <= pos.y() <= root.height() + 4 and -40 <= pos.x() <= card.right() + LINGER

    def _check(self):
        if not self.active:  # (waiting to spread: off the strip, or a click on it, and it doesn't)
            if not self._over_strip() or held():
                self.rest.stop()
            if not self.rest.isActive():
                self.timer.stop()
            return
        if self._wanted():
            self._away_since = None
            return
        now = time.monotonic()
        if self._away_since is None:
            self._away_since = now
        elif (now - self._away_since) * 1000 >= HIDE_DELAY:
            self.fold()

    def fold(self, animate=True):
        """Back to the strip (at once, for something about to change the sidebar itself)."""
        if not self.active:
            return
        w, s = self.window, self.sidebar
        root = w.centralWidget()
        self._stop_unfolding()
        strip = QRect(self.placeholder.mapTo(root, QPoint(0, 0)), self.placeholder.size())
        card = s.geometry()
        animate = animate and self._animating()
        if animate:  # (the card as it is, to fold back over the strip once that's there again)
            picture = s.grab(QRect(QPoint(0, 0), s.size()))
        self.active = False
        self.timer.stop()
        self.shadow.hide()
        self.shadow.deleteLater()
        self.shadow = None
        s.setProperty("floating", False)
        s.style().unpolish(s)
        s.style().polish(s)
        s.set_compact(True)
        w.splitter.replaceWidget(w._sidebar_index, s)
        s.show()
        self.placeholder.deleteLater()
        self.placeholder = None
        w._sidebar_margins()
        w._set_sidebar_size(s.width())
        w._dress_handle()
        if animate and MAC_LIGHTS and not w.isFullScreen():
            # The lights go as it folds, as they do with the hidden one sliding away (rather than jumping to the
            # strip ahead of it), and come back where they belong once it has
            w.traffic_lights.place(False, card.x(), MARGIN)
        else:
            w.place_traffic_lights()
        s.align_with_traffic_lights()
        if animate:
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            area = self._area(strip, card)
            self._unfold(area, root.grab(area), picture, card, strip, 1.0, 0.0,
                         done=lambda: not self.active and (w.place_traffic_lights(), s.align_with_traffic_lights()))
