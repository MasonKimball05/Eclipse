# Switching Spaces, Arc style: the Space's part of the sidebar slides across to the next one while the
# rest of the window blends over to its colours. Used by ⌃1–⌃9, the Space switcher and ⌥⌘←/→, and by a
# two-finger swipe on the sidebar (gestures.py, which drives the same slide with your fingers).
#
# Switching is the heavy part (the whole window takes the new Space's colours: tens of milliseconds, more on an
# older Mac), and doing it as a slide began held the slide up for its first frames. So the Spaces either side of
# the one you're in have their pictures ready (SpacePictures: the one you left, and the others made while
# nothing's happening), and a slide to one of them starts at once, the switch itself waiting until it lands,
# under the cover. Only those two are kept, so the memory stays small.

import json

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap, QRegion
from PyQt6.QtWidgets import QApplication, QWidget

CONTENT_RADIUS = 10  # (content.RADIUS: the page's rounded corners)


BLEND = (0.4, 0.7)  # how far across the colours (and anything else round the tabs) turn over: just past halfway


def _smooth(t: float, start=BLEND[0], end=BLEND[1]) -> float:
    """0 → 1 eased, over a stretch of a slide: the window looks like the Space you're leaving until you're past
    halfway to the next, and is never both for long."""
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
        self.interrupted = False  # (another was still under way: the window may be between two Spaces)
        for old in window.findChildren(SpaceSlide):
            self.interrupted = self.interrupted or old.isVisible()
            animation = getattr(old, "_pb_animation", None)
            if animation is not None:
                animation.stop()
            old.hide()
            old.deleteLater()
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Every point it covers, it paints (pictures of the window, which has no see-through parts): so Qt needn't
        # paint the window under it each frame too. Without this it did, all of it with no tab open, and a slide
        # managed about 30 frames a second. Not once it fades (lift), when what's under it shows through
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.window_ = window
        self.direction = direction  # 1: the next Space comes in from the right
        self.width_ = window.sidebar.width()  # how far the tabs travel
        self.regions, mask = cover_layout(window)
        self.setMask(mask) if mask is not None else self.clearMask()
        self.lanes = [space_rect(window, w) for w in sliding_parts(window)]  # where each part slides
        self.before = capture(window, self.regions)
        self.after = None
        self.travel = 0.0  # points the tabs have moved, toward the new Space
        self.opacity = 1.0  # fades out as it lifts
        self.setGeometry(window.rect())
        self.show()
        self.raise_()

    def take_after(self):
        self.hide()
        self.after = capture(self.window_, self.regions)
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
            # The new Space's tabs and address pictured again, as the sidebar is now: taken as the switch began (or
            # ready beforehand), they can be a few points off what's under it by now (the sidebar still settling), and
            # fading one over the other showed everything twice for a moment. Only those: picturing the whole
            # window again (all of it, with no tab open) took a frame and more, just as the slide landed
            self.after = (self.after[0], [(_picture(part), _in_window(self.window_, part).topLeft())
                                          for part in sliding_parts(self.window_)])
            self.update()

        def step(value):
            self.opacity = value
            self.update()
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)  # (see-through from here on)
        animate(self, 1.0, 0.0, step, FAST, lambda: (self.hide(), self.deleteLater()))

    def paintEvent(self, event):
        p = QPainter(self)
        if self.opacity >= 1.0:
            self._paint(p)
        else:
            # Fading out: the cover as one picture, faded as one. Faded piece by piece, the window round the tabs
            # (drawn without them) half covered the real tabs underneath, and they dimmed, then snapped back
            ratio = self.devicePixelRatioF()
            layer = QPixmap(self.size() * ratio)
            layer.setDevicePixelRatio(ratio)
            layer.fill(Qt.GlobalColor.transparent)
            inner = QPainter(layer)
            self._paint(inner)
            inner.end()
            p.setOpacity(self.opacity)
            p.drawPixmap(0, 0, layer)
        p.end()

    def _paint(self, p):
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        blend = _smooth(self.progress)
        for i, (region, picture) in enumerate(self.before[0]):
            p.setOpacity(1.0)
            p.drawPixmap(region.topLeft(), picture)
            if self.after is not None and blend > 0:
                p.setOpacity(blend)
                p.drawPixmap(region.topLeft(), self.after[0][i][1])
        p.setOpacity(1.0)
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


def cover_layout(window):
    """(the regions the cover holds pictures of, the mask that keeps the rest of the window live, or None)"""
    floating = getattr(window, "floating_sidebar", None) is not None and window.floating_sidebar.active
    if getattr(window.content, "blank", False):
        # No tab open: the "page" is the start view, in the Space's colours and with its name, so it's covered too
        # (left live, it turned to the new Space's the moment a swipe began)
        return [QRectF(window.rect())], None
    if floating:
        # The hidden sidebar come out over a page: the cover is the sidebar's card, the page all round it left live
        side = _in_window(window, window.sidebar)
        card = QPainterPath()
        card.addRoundedRect(side, CONTENT_RADIUS, CONTENT_RADIUS)
        return [side], QRegion(card.toFillPolygon().toPolygon())
    page = content_rect(window)
    everything = QRectF(window.rect())
    # The window round the page: the sidebar's column and the thin frame above, beside and below
    regions = [r for r in (
        QRectF(0, 0, page.left(), everything.height()),
        QRectF(page.left(), 0, everything.width() - page.left(), page.top()),
        QRectF(page.right(), page.top(), everything.width() - page.right(), page.height()),
        QRectF(page.left(), page.bottom(), everything.width() - page.left(), everything.height() - page.bottom()),
    ) if r.width() > 0 and r.height() > 0]
    hole = QPainterPath()
    hole.addRoundedRect(page, CONTENT_RADIUS, CONTENT_RADIUS)
    return regions, QRegion(window.rect()).subtracted(QRegion(hole.toFillPolygon().toPolygon()))


def sliding_parts(window):
    """What belongs to one Space and so slides: its tabs, and the address of its current tab.
    (Favorites are the same in every Space, so they simply stay.)"""
    return [window.sidebar.url_pill, window.sidebar.scroll]


def capture(window, regions):
    """([(each region round the page, without the sliding parts)], [(each part on its own, where it goes)])
    Small pictures only, never the page: they cost a millisecond or so each."""
    parts = sliding_parts(window)
    pictures = []
    for part in parts:
        policy = part.sizePolicy()
        if not policy.retainSizeWhenHidden():
            policy.setRetainSizeWhenHidden(True)  # hiding it for a moment mustn't move anything else
            part.setSizePolicy(policy)
        pictures.append((_picture(part), _in_window(window, part).topLeft()))
    for part in parts:
        part.hide()  # (not on screen in between: nothing repaints until this returns)
    backdrop = [(region, window.grab(region.toAlignedRect())) for region in regions]
    for part in parts:
        part.show()
    return backdrop, pictures


def _picture(widget) -> QPixmap:
    """The widget as it looks on screen, on nothing. grab() fills the whole rectangle with its background colour
    first, which a stylesheet's colour is, too: the address pill slid across as a box, not rounded."""
    ratio = widget.devicePixelRatioF()
    picture = QPixmap(widget.size() * ratio)
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.GlobalColor.transparent)
    widget.render(picture, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    return picture


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


def slide_region(window, widget, change, direction, target=None, choose=None):
    """Run change() (switching Spaces to target), sliding the Space's part of the sidebar across and blending
    the rest over. direction 1: the new one comes from the right. With target's picture ready, choose() (just
    which Space you're in: what comes next, a tab opened in it say, goes there) is run at once and the rest of
    change() (its colours and page) once the slide's landed."""
    from .motion import NORMAL
    slide = SpaceSlide(window, direction)  # holds the old look meanwhile
    pictures = space_pictures(window)
    ready = pictures.get(target) if target is not None else None
    if not slide.interrupted:
        pictures.keep(window.store.active_space, slide.before)
    if ready is not None:
        # Its picture's ready: slide at once, and switch once it's landed (under the cover)
        slide.after = ready
        if choose is not None:
            choose()

        def landed():
            change()
            QTimer.singleShot(0, slide.lift)  # (once the sidebar has caught up)
        slide.run(slide.side.width(), NORMAL + 60, landed)
        return
    change()

    def start():
        slide.take_after()
        slide.run(slide.side.width(), NORMAL + 60, slide.lift)
    QTimer.singleShot(0, start)  # the sidebar rebuilds on the next pass of the event loop


PREPARE_MS = 600  # how long nothing has to be happening (scrolling on the sidebar included) before the next Spaces'
# pictures are made: sooner, they were made just as the next swipe began, and it leapt; later, a quick second swipe
# found none ready, and switched Spaces mid-slide
PREPARE = "prepare_spaces"  # (a setting: on unless turned off)


class SpacePictures(QObject):
    """Pictures of the Spaces either side of the one you're in (as a slide shows them), ready before they're
    needed. A picture's only used while it still fits: the same window size and sidebar, the same kind of cover
    (a page open or not), the Space's colours unchanged."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.kept: dict[str, tuple] = {}  # Space id: (what it was taken for, the picture)
        self.stale: set[str] = set()  # Spaces whose pictures are kept, but to be made again (their tabs may differ)
        self._idle = QTimer(self, singleShot=True, interval=PREPARE_MS, timeout=self.prepare)
        window.store.structure_changed.connect(self.forget)

    def _fits(self, space):
        """What a picture of `space` has to have been taken for, to be used now."""
        w = self.window
        regions, _mask = cover_layout(w)
        lanes = [space_rect(w, part) for part in sliding_parts(w)]
        rects = tuple((r.x(), r.y(), r.width(), r.height()) for r in regions + lanes)
        return (w.width(), w.height(), w.sidebar.width(), rects, space.color, json.dumps(space.theme, sort_keys=True))

    def neighbours(self) -> list:
        spaces, here = self.window.store.spaces, self.window.store.active_space
        if here not in spaces or len(spaces) < 2:
            return []
        i = spaces.index(here)
        return list(dict.fromkeys(s for s in (spaces[i - 1], spaces[(i + 1) % len(spaces)]) if s is not here))

    def get(self, space):
        kept = self.kept.get(space.id) if space is not None else None
        return kept[1] if kept is not None and kept[0] == self._fits(space) else None

    def keep(self, space, picture):
        """A picture of `space` as it is now (the one a slide just took, leaving it)."""
        self.kept[space.id] = (self._fits(space), picture)
        self.stale.discard(space.id)
        self.soon()

    def forget(self):
        """Tabs moved, added or closed: the pictures may show the wrong ones, so they're made again soon. Until then
        they're still used: a Live Folder refreshing (or sync) did this every so often, and with them thrown away
        the next swipe switched Spaces mid-slide; a picture a tab out of date is gone the moment the slide lands."""
        self.stale = set(self.kept)
        self.soon()

    def soon(self):
        self._idle.start()

    def _quiet(self) -> bool:
        from .motion import reduced
        w = self.window
        return (w.isVisible() and w.sidebar.isVisible() and not reduced()
                and QApplication.mouseButtons() == Qt.MouseButton.NoButton
                and not any(c.isVisible() for c in w.findChildren(SpaceSlide)))

    def prepare(self):
        """Keep only the neighbours' pictures, and make any of theirs that's missing (together, when nothing else is
        happening)."""
        if not self.window.store.settings.get(PREPARE, True):
            return  # (off: the tests, which want a window that only does what they ask)
        if not self._quiet():
            self.soon()
            return
        wanted = self.neighbours()
        for space_id in [k for k in self.kept if k not in {s.id for s in wanted}]:
            del self.kept[space_id]  # (only the two either side: the memory stays small)
            self.stale.discard(space_id)
        missing = [s for s in wanted if self.get(s) is None or s.id in self.stale]
        if missing:
            fits = [self._fits(space) for space in missing]  # (as the window is now, in this Space)
            for space, fit, picture in zip(missing, fits, self.pictures_of(missing)):
                self.kept[space.id] = (fit, picture)
                self.stale.discard(space.id)

    def picture_of(self, space):
        return self.pictures_of([space])[0]

    def pictures_of(self, spaces) -> list:
        """Each of `spaces` as a slide to it would show it, made without showing it: the window's switched to each
        in turn, pictured, and switched back, all before anything's drawn on screen (both neighbours at once:
        one restyle fewer than each on its own, and both ready together)."""
        w = self.window
        store, sidebar = w.store, w.sidebar
        regions, _mask = cover_layout(w)
        real_space, real_tab = store.active_space_id, w.current_tab
        pictures = []
        try:
            for space in spaces:
                store.active_space_id = space.id
                w.current_tab = real_tab
                w.apply_theme()  # (its colours, and its tabs in the sidebar)
                tab = store.find_tab(space.active_tab_id)
                if tab in store.visible_tabs():
                    w.current_tab = tab  # (its own tab highlighted, its address in the pill: only for the picture)
                    sidebar.update_selection()
                    sidebar.update_nav()
                _lay_out(w)
                pictures.append(capture(w, regions))
        finally:
            store.active_space_id, w.current_tab = real_space, real_tab
            w.apply_theme()
            _lay_out(w)
        return pictures


def _lay_out(window):
    """Everything laid out now, as it would be on the next pass of the event loop (new rows, say)."""
    for widget in [window] + window.findChildren(QWidget):
        layout = widget.layout()
        if layout is not None:
            layout.activate()


def space_pictures(window) -> SpacePictures:
    pictures = getattr(window, "space_pictures", None)
    if pictures is None:
        pictures = window.space_pictures = SpacePictures(window)
    return pictures
