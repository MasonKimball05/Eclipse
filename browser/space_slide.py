# Switching Spaces, Arc style: the Space's part of the sidebar slides across to the next one while the
# rest of the window blends over to its colours. Used by ⌃1–⌃9, the Space switcher and ⌥⌘←/→, and by a
# two-finger swipe on the sidebar (gestures.py, which drives the same slide with your fingers).

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QRegion
from PyQt6.QtWidgets import QWidget

CONTENT_RADIUS = 10  # (content.RADIUS: the page's rounded corners)


def _smooth(t: float, start=0.2, end=0.8) -> float:
    """0 → 1 eased, over the middle of a slide: a word in the address bar is mostly one Space's or
    the other's, never both on top of each other for long."""
    t = max(0.0, min((t - start) / (end - start), 1.0))
    return t * t * (3 - 2 * t)


class SpaceSlide(QWidget):
    """Switching Spaces, the way Arc does it: the Space's own tabs and address slide across to the next
    Space's, while everything else round the page (Favorites, the bottom bar, the window's colours)
    blends over underneath. The page itself isn't covered: it stays live, and changes once the switch
    lands. Driven by your fingers (set_travel) or on its own (run).

    The Space itself switches at the start, under this cover (which shows pictures of before and
    after); cancelling switches back before the cover lifts."""

    def __init__(self, window, direction):
        # The last switch's cover may still be fading out (swiping straight back, say): gone first,
        # or this one's pictures would have it, and its Space's text, baked in
        for old in window.findChildren(SpaceSlide):
            animation = getattr(old, "_pb_animation", None)
            if animation is not None:
                animation.stop()
            old.hide()
            old.deleteLater()
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.window_ = window
        self.direction = direction  # 1: the next Space comes in from the right
        self.width_ = window.sidebar.width()  # how far the tabs travel
        floating = getattr(window, "floating_sidebar", None) is not None and window.floating_sidebar.active
        if floating:
            # Out over the page (no tab open, or the hidden sidebar come out): the cover is the sidebar's card,
            # the page all round it left live
            side = _in_window(window, window.sidebar)
            self.regions = [side]
            card = QPainterPath()
            card.addRoundedRect(side, CONTENT_RADIUS, CONTENT_RADIUS)
            self.setMask(QRegion(card.toFillPolygon().toPolygon()))
        else:
            page = content_rect(window)
            everything = QRectF(window.rect())
            # The window round the page: the sidebar's column and the thin frame above, beside and below
            self.regions = [r for r in (
                QRectF(0, 0, page.left(), everything.height()),
                QRectF(page.left(), 0, everything.width() - page.left(), page.top()),
                QRectF(page.right(), page.top(), everything.width() - page.right(), page.height()),
                QRectF(page.left(), page.bottom(), everything.width() - page.left(), everything.height() - page.bottom()),
            ) if r.width() > 0 and r.height() > 0]
            hole = QPainterPath()
            hole.addRoundedRect(page, CONTENT_RADIUS, CONTENT_RADIUS)
            self.setMask(QRegion(window.rect()).subtracted(QRegion(hole.toFillPolygon().toPolygon())))
        self.lanes = [space_rect(window, w) for w in self._sliding_parts()]  # where each part slides
        self.before = self._capture()
        self.after = None
        self.travel = 0.0  # points the tabs have moved, toward the new Space
        self.opacity = 1.0  # fades out as it lifts
        self.setGeometry(window.rect())
        self.show()
        self.raise_()

    def _sliding_parts(self):
        """What belongs to one Space and so slides: its tabs, and the address of its current tab.
        (Favorites are the same in every Space, so they simply stay.)"""
        sidebar = self.window_.sidebar
        return [sidebar.url_pill, sidebar.scroll]

    def _capture(self):
        """([(each region round the page, without the sliding parts)], [(each part on its own, where it goes)])
        Small pictures only, never the page: they cost a millisecond or so each."""
        parts = self._sliding_parts()
        pictures = []
        for part in parts:
            policy = part.sizePolicy()
            if not policy.retainSizeWhenHidden():
                policy.setRetainSizeWhenHidden(True)  # hiding it for a moment mustn't move anything else
                part.setSizePolicy(policy)
            pictures.append((part.grab(), _in_window(self.window_, part).topLeft()))
        for part in parts:
            part.hide()  # (not on screen in between: nothing repaints until this returns)
        backdrop = [(region, self.window_.grab(region.toAlignedRect())) for region in self.regions]
        for part in parts:
            part.show()
        return backdrop, pictures

    def take_after(self):
        self.hide()
        self.after = self._capture()
        self.show()
        self.raise_()
        self.update()

    @property
    def side(self):  # (what the swipe measures against)
        return QRectF(0, 0, self.width_, 0)

    @property
    def progress(self) -> float:
        return max(0.0, min(self.travel / max(self.width_, 1), 1.0))

    def set_travel(self, travel):
        self.travel = travel
        self.update()

    def run(self, target, ms, done=None, easing=QEasingCurve.Type.OutCubic):
        from .motion import animate
        animate(self, self.travel, target, self.set_travel, ms, done, easing)

    def lift(self):
        """Fade away, showing the real thing underneath (the same, but for the new tab's highlight)."""
        from .motion import FAST, animate
        if not self.isVisible():
            return  # (already put away: a newer switch took over)
        if self.after is not None and self.progress >= 1.0:
            # The new Space's picture again, as the sidebar is now: taken as the switch began, it can be a few
            # points off what's under it by now (the sidebar still settling), and fading one over the other
            # showed everything twice for a moment
            self.take_after()

        def step(value):
            self.opacity = value
            self.update()
        animate(self, 1.0, 0.0, step, FAST, lambda: (self.hide(), self.deleteLater()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        blend = _smooth(self.progress)
        for i, (region, picture) in enumerate(self.before[0]):
            p.setOpacity(self.opacity)
            p.drawPixmap(region.topLeft(), picture)
            if self.after is not None and blend > 0:
                p.setOpacity(self.opacity * blend)
                p.drawPixmap(region.topLeft(), self.after[0][i][1])
        p.setOpacity(self.opacity)
        shift = self.travel * self.direction
        for i, lane in enumerate(self.lanes):
            p.save()
            p.setClipRect(lane)
            picture, at = self.before[1][i]
            p.drawPixmap(QPointF(at.x() - shift, at.y()), picture)
            if self.after is not None:
                picture, at = self.after[1][i]
                p.drawPixmap(QPointF(at.x() + self.width_ * self.direction - shift, at.y()), picture)
            p.restore()
        p.end()


def _in_window(window, widget) -> QRectF:
    rect = widget.rect()
    rect.moveTopLeft(widget.mapTo(window, rect.topLeft()))
    return QRectF(rect)


def space_rect(window, part) -> QRectF:
    """Where one of a Space's own parts slides: its height, the sidebar's whole width."""
    side, own = _in_window(window, window.sidebar), _in_window(window, part)
    return QRectF(side.x(), own.y(), side.width(), own.height())


def content_rect(window) -> QRectF:
    return _in_window(window, window.content)


def slide_region(window, widget, change, direction):
    """Run change() (switching Spaces), sliding the Space's part of the sidebar across and blending
    the rest over. direction 1: the new one comes from the right."""
    from .motion import NORMAL
    slide = SpaceSlide(window, direction)  # holds the old look meanwhile
    change()

    def start():
        slide.take_after()
        slide.run(slide.side.width(), NORMAL + 60, slide.lift)
    QTimer.singleShot(0, start)  # the sidebar rebuilds on the next pass of the event loop
