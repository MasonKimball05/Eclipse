# Trackpad gestures, Arc style.
#
#   - Two-finger swipe on a page: back (swipe right) and forward (swipe left). An arrow slides in
#     from the edge as you swipe and fills in once letting go will navigate.
#   - Two-finger swipe on the sidebar: the next or previous Space, sliding across.
#
# A page that scrolls sideways itself (a carousel, a code block, a map) keeps the swipe: before
# claiming one we ask the page whether anything under the pointer can still scroll that way, or
# whether the page took the wheel events for itself (preventDefault).

import time

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import QWidget

import qtawesome as qta

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
START_PX = 14         # sideways movement before a swipe counts as one
COMMIT_PX = 150       # how far to swipe for letting go to navigate
ARROW = 44
CONTENT_RADIUS = 10  # (content.RADIUS: the page's rounded corners)

# Notes, in the page, when it handled a sideways wheel itself (maps, sliders)
WATCH_WHEEL = """
(() => {
  if (window.__pbSwipeWatch) return;
  window.__pbSwipeWatch = true;
  window.__pbWheelTaken = 0;
  addEventListener('wheel', e => {
    if (e.defaultPrevented && Math.abs(e.deltaX) > Math.abs(e.deltaY)) window.__pbWheelTaken = Date.now();
  }, {passive: true});
})();
"""

# Can anything under (x, y) still scroll in the direction given (-1 left, 1 right)?
PROBE = """
((x, y, dir) => {
  if (Date.now() - (window.__pbWheelTaken || 0) < 600) return 'taken';
  let el = document.elementFromPoint(x, y);
  if (el && (el.tagName === 'IFRAME' || el.tagName === 'EMBED' || el.tagName === 'OBJECT')) return 'taken';
  for (; el; el = el.parentElement) {
    const style = getComputedStyle(el);
    const scrolls = el === document.scrollingElement || /(auto|scroll)/.test(style.overflowX);
    if (scrolls && el.scrollWidth > el.clientWidth + 1) {
      if (dir < 0 ? el.scrollLeft > 0 : el.scrollLeft + el.clientWidth < el.scrollWidth - 1) return 'scrolls';
    }
  }
  const root = document.scrollingElement;
  if (root && (dir < 0 ? scrollX > 0 : scrollX + innerWidth < root.scrollWidth - 1)) return 'scrolls';
  return 'free';
})(%f, %f, %d)
"""


def watch_script() -> QWebEngineScript:
    script = QWebEngineScript()
    script.setName("pb-swipe-watch")
    script.setSourceCode(WATCH_WHEEL)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(WORLD)
    script.setRunsOnSubFrames(False)
    return script


class SwipeArrow(QWidget):
    """The round arrow that slides in from the side of the page."""

    def __init__(self, parent, accent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.accent = QColor(accent)
        self.back = True
        self.progress = 0.0
        self.resize(ARROW + 24, ARROW + 24)
        self.hide()

    def show_progress(self, back: bool, progress: float):
        self.back, self.progress = back, max(0.0, min(progress, 1.0))
        host = self.parentWidget()
        travel = (ARROW + 30) * min(self.progress, 1.0)
        x = -ARROW - 12 + travel if back else host.width() - travel - 12
        self.move(int(x), (host.height() - self.height()) // 2)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ready = self.progress >= 1.0
        circle = QRectF(12, 12, ARROW, ARROW)
        path = QPainterPath()
        path.addEllipse(circle)
        p.fillPath(path, self.accent if ready else QColor(255, 255, 255, 235))
        p.setPen(QColor(0, 0, 0, 40))
        p.drawEllipse(circle)
        icon = qta.icon("ph.arrow-left-bold" if self.back else "ph.arrow-right-bold",
                        color="white" if ready else QColor(60, 60, 60))
        icon.paint(p, circle.adjusted(12, 12, -12, -12).toRect())
        p.end()


SNAPSHOTS = 12  # pictures of pages you've left, for swiping back to them (all tabs together)


class Snapshots:
    """What each page looked like when you left it, so swiping back can show it under your fingers.
    Full resolution (a softer picture under a sharp page looks wrong), so only the last few."""

    def __init__(self):
        self._shots = {}  # (id(view), history index, url) → QPixmap, oldest first

    @staticmethod
    def key(view, index, url):
        return (id(view), index, url.toString() if hasattr(url, "toString") else url)

    def take(self, view, image=None):
        history = view.history()
        item = history.currentItem()
        if not item.isValid() or view.width() < 50:
            return
        shot = image if image is not None else view.grab()
        if shot.isNull():
            return
        key = self.key(view, history.currentItemIndex(), item.url())
        self._shots.pop(key, None)
        self._shots[key] = shot
        while len(self._shots) > SNAPSHOTS:
            self._shots.pop(next(iter(self._shots)))

    def get(self, view, index, url):
        return self._shots.get(self.key(view, index, url))

    def forget(self, view):
        for key in [k for k in self._shots if k[0] == id(view)]:
            del self._shots[key]


class PageSlide(QWidget):
    """Swiping back or forward: the page follows your fingers. Going back, it slides off to the right
    to show the page you came from underneath; going forward, the next page slides in over it from
    the right. The page underneath sits a little further back (it moves slower, slightly shaded)."""

    SHADE = 0.22

    def __init__(self, view, back, current, other, title, icon, theme):
        super().__init__(view)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.back = back
        self.current = current  # this page, as it is now
        self.other = other or self._placeholder(view.size(), title, icon, theme)  # where you're going
        self.travel = 0.0
        self.fade = 1.0
        self.setGeometry(view.rect())
        self.show()
        self.raise_()

    @staticmethod
    def _placeholder(size, title, icon, theme):
        """No picture of the page (it changed without loading, say): its icon and name on a plain page."""
        from PyQt6.QtGui import QFont, QPixmap
        shot = QPixmap(size)
        shot.fill(QColor("#ffffff") if not theme.dark else QColor("#1c1c1f"))
        p = QPainter(shot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = QPointF(size.width() / 2, size.height() * 0.42)
        if icon is not None and not icon.isNull():
            icon.paint(p, QRectF(middle.x() - 24, middle.y() - 56, 48, 48).toRect())
        font = QFont()
        font.setPixelSize(15)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(QColor(0, 0, 0, 150) if not theme.dark else QColor(255, 255, 255, 170))
        p.drawText(QRectF(40, middle.y() + 4, size.width() - 80, 60),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, title)
        p.end()
        return shot

    @property
    def progress(self) -> float:
        return max(0.0, min(self.travel / max(self.width(), 1), 1.0))

    def set_travel(self, travel):
        self.travel = travel
        self.update()

    def run(self, target, ms, done=None):
        from .motion import animate
        animate(self, self.travel, target, self.set_travel, ms, done)

    def lift(self):
        """The page you went to is showing: fade the picture of it away."""
        from .motion import FAST, animate

        def step(value):
            self.fade = value
            self.update()
        animate(self, 1.0, 0.0, step, FAST, lambda: (self.hide(), self.deleteLater()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self.fade)
        w, t = self.width(), self.travel
        # (under, over): the page underneath moves at a third of the speed and is shaded while covered
        if self.back:
            under, under_x, over, over_x, covered = self.other, -w * 0.3 + t * 0.3, self.current, t, 1 - self.progress
        else:
            under, under_x, over, over_x, covered = self.current, -t * 0.3, self.other, w - t, self.progress
        target = QRectF(0, 0, w, self.height())
        p.drawPixmap(target.translated(under_x, 0), under, QRectF(under.rect()))
        if covered > 0:
            p.fillRect(target, QColor(0, 0, 0, int(255 * self.SHADE * covered * self.fade)))
        edge = over_x
        shadow = QLinearGradient(QPointF(edge - 18, 0), QPointF(edge, 0))  # the moving page's edge
        shadow.setColorAt(0, QColor(0, 0, 0, 0))
        shadow.setColorAt(1, QColor(0, 0, 0, int(60 * self.fade)))
        p.fillRect(QRectF(edge - 18, 0, 18, self.height()), shadow)
        p.drawPixmap(target.translated(over_x, 0), over, QRectF(over.rect()))
        p.end()


class _Gesture:
    def __init__(self):
        self.dx = self.dy = 0.0
        self.state = "new"  # new → probing → mine / page / vertical
        self.back = True
        self.released = False  # fingers already lifted while we were still asking the page
        self.velocity = _Velocity()
        self.slide = None


class SwipeNavigator(QObject):
    """Back/forward swipes on every web view (watching the widget Chromium draws into)."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._watched = {}   # Chromium's widget (its C++ address: Python wrappers come and go) → view
        self._gestures = {}  # id(view) → _Gesture
        self._arrows = {}    # id(view) → SwipeArrow
        self.snapshots = Snapshots()
        self._landing = {}   # id(view) → PageSlide waiting for the page it went to
        self._gliding = set()  # id(view)s whose swipe is over but whose trackpad glide isn't

    @property
    def enabled(self) -> bool:
        return self.browser.store.settings.get("swipe_navigation", True)

    def attach_profile(self, profile):
        if not any(s.name() == "pb-swipe-watch" for s in profile.scripts().toList()):
            profile.scripts().insert(watch_script())

    def attach(self, view):
        view.installEventFilter(self)
        self._watch_proxy(view)
        view.loadStarted.connect(lambda v=view: self._watch_proxy(v))
        view.loadStarted.connect(lambda v=view: self._leaving(v))
        view.loadFinished.connect(lambda _ok, v=view: self._arrived(v))
        view.destroyed.connect(lambda _=None, key=id(view): self._forget(key))

    def _watch_proxy(self, view, widget=None):
        widget = widget or view.focusProxy()
        key = sip.unwrapinstance(widget) if widget is not None else None
        if key is not None and key not in self._watched:
            self._watched[key] = view
            widget.installEventFilter(self)
            widget.destroyed.connect(lambda _=None, k=key: self._watched.pop(k, None))

    def _leaving(self, view):
        """A page is about to be replaced: remember how it looked (unless we're the ones sliding it)."""
        landing = self._landing.get(id(view))
        if landing is not None:
            landing.load_started = True
        elif not self._sliding(view) and view.isVisible():
            try:
                self.snapshots.take(view)
            except RuntimeError:
                pass

    def _sliding(self, view):
        gesture = self._gestures.get(id(view))
        return gesture is not None and gesture.slide is not None

    def _arrived(self, view):
        slide = self._landing.pop(id(view), None)
        if slide is not None:
            QTimer.singleShot(60, slide.lift)  # (a moment for its first paint)

    def _forget(self, key):
        self._gestures.pop(key, None)
        self._landing.pop(key, None)
        self.snapshots._shots = {k: v for k, v in self.snapshots._shots.items() if k[0] != key}
        arrow = self._arrows.pop(key, None)
        if arrow is not None:
            arrow.deleteLater()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.ChildAdded and obj is not None and hasattr(obj, "focusProxy"):
            # Chromium's widget arrives after the view, and is replaced when a navigation switches
            # renderer processes: watch whatever it adds
            child = event.child()
            if child is not None and child.isWidgetType() and not isinstance(child, SwipeArrow):
                self._watch_proxy(obj, child)
        elif kind == QEvent.Type.Wheel:
            view = self._watched.get(sip.unwrapinstance(obj))
            if view is not None and self.enabled:
                try:
                    self._wheel(view, event)
                except RuntimeError:
                    return False  # the view went away mid-gesture
                # Once it's plainly a swipe, the page stops seeing it (it would scroll under your fingers),
                # and the glide after it too (the page would have moved when the picture lifts)
                return self._sliding(view) or id(view) in self._gliding
        return False

    def _wheel(self, view, event):
        phase = event.phase()
        key = id(view)
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._gliding.discard(key)
            self._gestures[key] = _Gesture()
            return
        if key in self._gliding:
            if phase == Qt.ScrollPhase.ScrollEnd:
                self._gliding.discard(key)
            return
        gesture = self._gestures.get(key)
        if gesture is None:
            return
        if phase in (Qt.ScrollPhase.ScrollEnd, Qt.ScrollPhase.ScrollMomentum):
            self._release(view, gesture)
            return
        if phase != Qt.ScrollPhase.ScrollUpdate:
            return
        delta = event.pixelDelta()
        gesture.dx += delta.x()
        gesture.dy += delta.y()
        if gesture.state == "new":
            if abs(gesture.dy) > START_PX and abs(gesture.dy) >= abs(gesture.dx):
                gesture.state = "vertical"
            elif abs(gesture.dx) > START_PX and abs(gesture.dx) > abs(gesture.dy) * 1.5:
                gesture.back = gesture.dx > 0  # fingers to the right: back
                history = view.history()
                if not (history.canGoBack() if gesture.back else history.canGoForward()):
                    gesture.state = "page"
                    return
                gesture.state = "probing"
                pos = event.position()
                ratio = view.zoomFactor() or 1.0
                source = PROBE % (pos.x() / ratio, pos.y() / ratio, -1 if gesture.back else 1)
                view.page().runJavaScript(source, WORLD, lambda answer, g=gesture, v=view: self._probed(v, g, answer))
        elif gesture.state == "mine":
            self._show(view, gesture)

    def _probed(self, view, gesture, answer):
        if gesture.state != "probing":
            return
        gesture.state = "mine" if answer == "free" else "page"
        if gesture.released:  # a quick flick that ended before the page answered: decide now
            self._release(view, gesture)
        elif gesture.state == "mine" and self._gestures.get(id(view)) is gesture:
            self._show(view, gesture)

    def _progress(self, gesture) -> float:
        travel = gesture.dx if gesture.back else -gesture.dx
        return max(0.0, travel - START_PX) / COMMIT_PX

    def _arrow(self, view):
        arrow = self._arrows.get(id(view))
        if arrow is None:
            arrow = self._arrows[id(view)] = SwipeArrow(view, self.browser.theme.accent)
        return arrow

    def _show(self, view, gesture):
        from .motion import reduced
        if reduced():
            self._arrow(view).show_progress(gesture.back, self._progress(gesture))
            return
        if gesture.slide is None:
            gesture.slide = self._begin_slide(view, gesture.back)
        travel = self._travel(view, gesture)
        gesture.velocity.add(travel)
        gesture.slide.set_travel(travel)

    def _travel(self, view, gesture):
        travel = (gesture.dx if gesture.back else -gesture.dx) - START_PX
        width = max(view.width(), 1)
        if travel < 0:
            return 0.0
        return width + rubber_band(travel - width) if travel > width else travel

    def _begin_slide(self, view, back):
        landing = self._landing.pop(id(view), None)
        if landing is not None:
            landing.hide()
            landing.deleteLater()
        history = view.history()
        index = history.currentItemIndex() + (-1 if back else 1)
        item = history.backItem() if back else history.forwardItem()
        current = view.grab()
        other = self.snapshots.get(view, index, item.url())
        from . import storage
        title = item.title() or item.url().host() or item.url().toString()
        return PageSlide(view, back, current, other, title, storage.favicon(item.url().toString()),
                         self.browser.theme)

    def _release(self, view, gesture):
        if self._gestures.get(id(view)) is gesture:
            self._gestures.pop(id(view), None)
        if gesture.state == "probing":
            gesture.released = True  # the page's answer finishes this one
            return
        if gesture.slide is not None:
            self._release_slide(view, gesture)
            return
        arrow = self._arrows.get(id(view))
        if gesture.state == "mine" and self._progress(gesture) >= 1.0:
            view.back() if gesture.back else view.forward()
        if arrow is not None and arrow.isVisible():
            self._slide_away(arrow)

    def _release_slide(self, view, gesture):
        slide = gesture.slide
        self._gliding.add(id(view))
        width = max(view.width(), 1)
        speed = gesture.velocity.value()
        go = slide.travel > width * 0.35 and speed > -0.3 or speed > 0.5 and slide.travel > width * 0.05
        goal = width if go else 0.0
        ms = settle_ms(goal - slide.travel, abs(speed) if (goal > slide.travel) == (speed > 0) else 0)

        def done():
            if not go:
                slide.hide()
                slide.deleteLater()
                return
            self.snapshots.take(view, slide.current)  # this page, for swiping forward to it again
            self._landing[id(view)] = slide
            slide.load_started = False
            view.back() if gesture.back else view.forward()
            # A page that changes without loading (most single-page apps) never says it has finished
            QTimer.singleShot(150, lambda: self._landing.get(id(view)) is slide and not slide.load_started
                              and self._arrived(view))
            QTimer.singleShot(1500, lambda: self._landing.get(id(view)) is slide and self._arrived(view))
        slide.run(goal, ms, done)

    def _slide_away(self, arrow):
        start = arrow.progress
        animation = QVariantAnimation(arrow, startValue=start, endValue=0.0, duration=160)
        animation.valueChanged.connect(lambda value: arrow.show_progress(arrow.back, value))
        animation.finished.connect(arrow.hide)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


def rubber_band(x: float, limit: float = 70.0) -> float:
    """Past where something can go, it still gives a little, less and less (like pulling a page
    past its end on a Mac)."""
    if x <= 0:
        return 0.0
    return limit * (1 - 1 / (x * 0.55 / limit + 1))


class _Velocity:
    """How fast the fingers were going just before they lifted (points per millisecond)."""

    def __init__(self):
        self.samples = []  # (time, total travel)

    def add(self, travel):
        now = time.monotonic()
        self.samples.append((now, travel))
        self.samples = [(t, x) for t, x in self.samples if now - t < 0.08]

    def value(self) -> float:
        now = time.monotonic()
        recent = [(t, x) for t, x in self.samples if now - t < 0.08]  # resting before letting go: no flick
        if len(recent) < 2:
            return 0.0
        self.samples = recent
        (t0, x0), (t1, x1) = self.samples[0], self.samples[-1]
        return (x1 - x0) / max((t1 - t0) * 1000, 1)


def settle_ms(distance: float, speed: float) -> int:
    """How long the rest of a slide takes: carrying on at the speed the fingers left it (an
    ease-out starts at three times its average speed), but never sluggish or abrupt."""
    if speed <= 0.05:
        return int(max(150, min(300, 120 + abs(distance) * 0.6)))
    return int(max(110, min(320, 3 * abs(distance) / speed)))


def _smooth(t: float, start=0.2, end=0.8) -> float:
    """0 → 1 eased, over the middle of a slide: a word in the address bar is mostly one Space's or
    the other's, never both on top of each other for long."""
    t = max(0.0, min((t - start) / (end - start), 1.0))
    return t * t * (3 - 2 * t)


class SpaceSlide(QWidget):
    """Switching Spaces, the way Arc does it: the Space's own tabs (its name, Pinned and Today) slide
    across to the next Space's, while everything else (Favorites, the address, the bottom bar, the
    window's colours) blends over underneath. The page stays put until the switch lands, then
    crossfades. Driven by your fingers (set_travel) or on its own (run).

    The switch itself happens at the start, under this cover, which shows pictures of before and
    after; cancelling switches back before the cover lifts."""

    def __init__(self, window, direction):
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.window_ = window
        self.direction = direction  # 1: the next Space comes in from the right
        self.width_ = window.sidebar.width()  # how far the tabs travel
        self.content = QPainterPath()
        self.content.addRoundedRect(content_rect(window), CONTENT_RADIUS, CONTENT_RADIUS)
        self.lanes = [space_rect(window, w) for w in self._sliding_parts()]  # where each part slides
        self.before = self._capture()
        self.after = None
        self.travel = 0.0  # points the tabs have moved, toward the new Space
        self.page = 0.0  # how far the page has crossfaded (once the switch has landed)
        self.setGeometry(window.rect())
        self.show()
        self.raise_()

    def _sliding_parts(self):
        """What belongs to one Space and so slides: its tabs, and the address of its current tab.
        (Favorites are the same in every Space, so they simply stay.)"""
        sidebar = self.window_.sidebar
        return [sidebar.url_pill, sidebar.scroll]

    def _capture(self):
        """(the window without the sliding parts, [(each part on its own, nothing behind it, where it goes)])"""
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
        backdrop = self.window_.grab()
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

    def land(self, done):
        """The switch is staying: crossfade the page, then lift the cover."""
        from .motion import FAST, animate

        def step(value):
            self.page = value
            self.update()
        animate(self, 0.0, 1.0, step, FAST, done)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(QPointF(0, 0), self.before[0])
        if self.after is not None:
            everything = QPainterPath()
            everything.addRect(QRectF(self.rect()))
            p.save()
            p.setClipPath(everything.subtracted(self.content))  # round the page: blends as you go
            p.setOpacity(_smooth(self.progress))
            p.drawPixmap(QPointF(0, 0), self.after[0])
            p.restore()
            if self.page > 0:
                p.save()
                p.setClipPath(self.content)
                p.setOpacity(self.page)
                p.drawPixmap(QPointF(0, 0), self.after[0])
                p.restore()
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


class SpaceSwipe(QObject):
    """Two fingers sideways on the sidebar: the sidebar follows them to the next (or previous) Space,
    as far as you like and back again. Letting go past halfway, or with a flick, finishes the
    switch; otherwise it springs back. With one Space, it just gives a little and springs back."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.slide = None
        self.state = "idle"  # idle → deciding → mine / not-mine; settling once the fingers lift
        self.dx = self.dy = 0.0
        self.velocity = _Velocity()
        self.target = None  # the Space we're sliding to
        self.previous = None
        self._last = (None, False)

    def wheel(self, event) -> bool:
        """A wheel event on the sidebar: True if it was part of a Space swipe (and is used up)."""
        # The same event can arrive twice while it's being delivered (a child's event filter, then the
        # sidebar as it bubbles up): the same event object, so the same address, until the next pass
        # of the event loop
        key = sip.unwrapinstance(event)
        if key == self._last[0]:
            return self._last[1]
        answer = self._wheel(event)
        if self._last[0] is None:
            QTimer.singleShot(0, self._forget_last)
        self._last = (key, answer)
        return answer

    def _forget_last(self):
        self._last = (None, False)

    def _wheel(self, event) -> bool:
        phase = event.phase()
        if phase == Qt.ScrollPhase.NoScrollPhase:
            return self._mouse_wheel(event)
        if phase == Qt.ScrollPhase.ScrollBegin:
            if self.state == "mine":
                self._release()  # (the last one's fingers-up never came: decide it now)
            self._finish_now()
            self.state, self.dx, self.dy = "deciding", 0.0, 0.0
            self.velocity = _Velocity()
            return False
        used = ("settling", "switched", "glide")  # states whose scrolling still belongs to this swipe
        if phase == Qt.ScrollPhase.ScrollMomentum:
            # macOS's glide after the fingers lift (the "fingers up" ScrollEnd only comes when it
            # stops, a second or so later): the fingers are up, so decide now
            if self.state == "mine":
                self._release()
                return True
            return self.state in used
        if phase == Qt.ScrollPhase.ScrollEnd:
            if self.state == "mine":
                self._release()
                return True
            mine = self.state in used
            if self.state in ("switched", "glide"):
                self.state = "idle"
            return mine
        delta = event.pixelDelta()
        if self.state == "deciding":
            self.dx += delta.x()
            self.dy += delta.y()
            if abs(self.dy) > 6 and abs(self.dy) >= abs(self.dx):
                self.state = "not-mine"  # scrolling the tab list
            elif abs(self.dx) > 6 and abs(self.dx) > abs(self.dy):
                self._begin(1 if self.dx < 0 else -1)  # fingers to the left: the next Space
            return self.state in ("mine", "switched")
        if self.state == "mine":
            self.dx += delta.x()
            self._follow()
            return True
        return self.state in used

    def _mouse_wheel(self, event):
        # A mouse with a sideways wheel (or Shift-scrolling): one step, one Space
        delta = event.angleDelta()
        if abs(delta.x()) <= abs(delta.y()) or abs(delta.x()) < 60:
            return False
        self.browser.cycle_space(1 if delta.x() < 0 else -1)
        return True

    def _can_slide(self):
        return self.browser.sidebar.isVisible() and self.browser.isVisible()

    def _begin(self, direction):
        from .motion import reduced
        b = self.browser
        spaces = b.store.spaces
        if reduced() or not self._can_slide():
            self.state = "switched"  # no sliding: switch at once, and the rest of this swipe is used up
            b.cycle_space(direction)
            return
        self.state = "mine"
        self.previous = b.store.active_space
        self.target = spaces[(spaces.index(self.previous) + direction) % len(spaces)] if len(spaces) > 1 else None
        self.slide = SpaceSlide(b, direction)
        if self.target is not None:
            b._set_active_space(self.target)
            b._restore_space_tab()
            slide = self.slide
            QTimer.singleShot(0, lambda: slide.after is None and self.slide is slide and slide.take_after())
        self._follow()

    def _travel(self):
        travel = -self.dx * self.slide.direction  # toward the new Space
        width = self.slide.side.width()
        if self.target is None:
            return rubber_band(abs(travel)) * (1 if travel > 0 else -1)  # one Space: it only gives a little
        if travel < 0:
            return -rubber_band(-travel)
        if travel > width:
            return width + rubber_band(travel - width)
        return travel

    def _follow(self):
        travel = self._travel()
        self.velocity.add(travel)
        self.slide.set_travel(travel)

    def _release(self):
        slide = self.slide
        width = slide.side.width()
        speed = self.velocity.value()  # toward the new Space
        done = self.target is not None and (slide.travel > width * 0.5 and speed > -0.3 or
                                            speed > 0.45 and slide.travel > width * 0.06)
        self.state = "settling"
        goal = width if done else 0.0
        ms = settle_ms(goal - slide.travel, abs(speed) if (goal > slide.travel) == (speed > 0) else 0)
        slide.run(goal, ms, lambda: self._settled(slide, done))

    def _settled(self, slide, done):
        if self.slide is not slide:
            return
        self.slide = None
        if self.state == "settling":
            self.state = "glide"  # (until the trackpad's glide ends, its scrolling is still this swipe's)
        b = self.browser
        if not done and self.target is not None:
            b._set_active_space(self.previous)  # back where we were, still under the cover
            b._restore_space_tab()
            QTimer.singleShot(0, lambda: (slide.hide(), slide.deleteLater()))
        elif done:
            slide.land(lambda: (slide.hide(), slide.deleteLater()))
        else:
            slide.hide()
            slide.deleteLater()

    def _finish_now(self):
        """A new gesture while the last one is still settling: let that one land at once."""
        slide = self.slide
        if slide is not None and self.state == "settling":
            animation = getattr(slide, "_pb_animation", None)
            if animation is not None:
                animation.stop()
            done = slide.travel >= slide.side.width() * 0.5 and self.target is not None
            self._settled(slide, done)


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
        slide.run(slide.side.width(), NORMAL + 60,
                  lambda: slide.land(lambda: (slide.hide(), slide.deleteLater())))
    QTimer.singleShot(0, start)  # the sidebar rebuilds on the next pass of the event loop
