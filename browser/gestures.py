# Trackpad gestures, Arc style.
#
#   - Two-finger swipe on a page: back (swipe right) and forward (swipe left). An arrow slides in
#     from the edge as you swipe and fills in once letting go will navigate.
#   - Two-finger swipe on the sidebar: the next or previous Space, sliding across.
#
# A page that scrolls sideways itself (a carousel, a code block, a map) keeps the swipe: before
# claiming one we ask the page whether anything under the pointer can still scroll that way, or
# whether the page took the wheel events for itself (preventDefault).

from PyQt6 import sip
from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import QWidget

import qtawesome as qta

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
START_PX = 14         # sideways movement before a swipe counts as one
COMMIT_PX = 150       # how far to swipe for letting go to navigate
ARROW = 44

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


class _Gesture:
    def __init__(self):
        self.dx = self.dy = 0.0
        self.state = "new"  # new → probing → mine / page / vertical
        self.back = True
        self.released = False  # fingers already lifted while we were still asking the page


class SwipeNavigator(QObject):
    """Back/forward swipes on every web view (watching the widget Chromium draws into)."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._watched = {}   # Chromium's widget (its C++ address: Python wrappers come and go) → view
        self._gestures = {}  # id(view) → _Gesture
        self._arrows = {}    # id(view) → SwipeArrow

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
        view.destroyed.connect(lambda _=None, key=id(view): self._forget(key))

    def _watch_proxy(self, view, widget=None):
        widget = widget or view.focusProxy()
        key = sip.unwrapinstance(widget) if widget is not None else None
        if key is not None and key not in self._watched:
            self._watched[key] = view
            widget.installEventFilter(self)
            widget.destroyed.connect(lambda _=None, k=key: self._watched.pop(k, None))

    def _forget(self, key):
        self._gestures.pop(key, None)
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
                    pass  # the view went away mid-gesture
        return False  # the page always sees its wheel events too

    def _wheel(self, view, event):
        phase = event.phase()
        key = id(view)
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._gestures[key] = _Gesture()
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
        self._arrow(view).show_progress(gesture.back, self._progress(gesture))

    def _release(self, view, gesture):
        if self._gestures.get(id(view)) is gesture:
            self._gestures.pop(id(view), None)
        if gesture.state == "probing":
            gesture.released = True  # the page's answer finishes this one
            return
        arrow = self._arrows.get(id(view))
        if gesture.state == "mine" and self._progress(gesture) >= 1.0:
            view.back() if gesture.back else view.forward()
        if arrow is not None and arrow.isVisible():
            self._slide_away(arrow)

    def _slide_away(self, arrow):
        start = arrow.progress
        animation = QVariantAnimation(arrow, startValue=start, endValue=0.0, duration=160)
        animation.valueChanged.connect(lambda value: arrow.show_progress(arrow.back, value))
        animation.finished.connect(arrow.hide)
        animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


class SlideTransition(QWidget):
    """Slides one picture of a region out and another in (switching Spaces)."""

    def __init__(self, host, rect, before, direction):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.before, self.after, self.direction = before, before, direction
        self.t = 0.0
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=240)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._step)
        self.animation.finished.connect(self.deleteLater)

    def start(self, after):
        self.after = after
        self.animation.start()

    def _step(self, value):
        self.t = value
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w = self.width()
        shift = self.t * w * self.direction
        p.drawPixmap(QPointF(-shift, 0), self.before)
        p.drawPixmap(QPointF(w * self.direction - shift, 0), self.after)
        p.end()


def slide_region(window, widget, change, direction):
    """Run change() (which redraws `widget`), sliding the old look out and the new one in.
    direction 1: the new one comes from the right."""
    rect = widget.geometry()
    rect.moveTopLeft(widget.parentWidget().mapTo(window, rect.topLeft()) if widget.parentWidget() else rect.topLeft())
    cover = SlideTransition(window, rect, window.grab(rect), direction)  # holds the old look meanwhile
    change()

    def start():
        cover.hide()
        after = window.grab(rect)
        cover.show()
        cover.raise_()
        cover.start(after)
    QTimer.singleShot(0, start)  # the sidebar rebuilds on the next pass of the event loop
