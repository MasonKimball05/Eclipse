# Small animations, so things slide and fade instead of jumping: the sidebar, the command bar,
# toasts. Short (under a quarter of a second) and eased, the way Arc moves. With "Reduce motion"
# turned on in macOS's Accessibility settings, everything just appears.

import subprocess
import sys

from PyQt6.QtCore import QEasingCurve, Qt, QVariantAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect

FAST, NORMAL = 140, 200


def _reduce_motion() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
                             capture_output=True, text=True, timeout=1).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out == "1"


_reduced = None  # looked up the first time something animates (asking `defaults` costs ~10 ms)


def reduced() -> bool:
    global _reduced
    if _reduced is None:
        _reduced = _reduce_motion()
    return _reduced


def animate(owner, start, end, on_value, ms=NORMAL, done=None, easing=QEasingCurve.Type.OutCubic):
    """Run on_value(v) from start to end over ms, then done(). Replaces any animation `owner` was
    already running, so a quick second click picks up from wherever the first one got to."""
    previous = getattr(owner, "_pb_animation", None)
    if previous is not None:
        previous.stop()
    if ms <= 0 or reduced():
        on_value(end)
        if done:
            done()
        return None
    animation = QVariantAnimation(owner, startValue=float(start), endValue=float(end), duration=ms)
    animation.setEasingCurve(easing)
    animation.valueChanged.connect(on_value)
    if done:
        animation.finished.connect(done)
    owner._pb_animation = animation
    animation.start()
    return animation


def fade(widget, show, ms=FAST, done=None):
    """Fade a widget in (showing it first) or out (hiding it at the end). Reversing half-way picks up
    from the current opacity."""
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        if effect is not None:  # it already has another effect (a shadow): no fading, just show/hide
            widget.setVisible(show)
            if done:
                done()
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    was_fading_out = getattr(widget, "_pb_fade_target", True) is False
    if show:
        start = effect.opacity() if widget.isVisible() and was_fading_out else 1.0 if widget.isVisible() else 0.0
        widget.show()
    else:
        start = effect.opacity()
    widget._pb_fade_target = show

    def finished():
        if not show:
            widget.hide()
            effect.setOpacity(1.0)
        if done:
            done()
    animate(widget, start, 1.0 if show else 0.0, effect.setOpacity, ms, finished)


class SidebarSlide:
    """The sidebar sliding away (or back): a picture of it moves over a picture of what's left
    behind, while the real layout has already changed underneath. Moving pictures instead of the
    widgets keeps the tab titles from squashing and reflowing on the way."""

    def __init__(self, window, rect, sidebar_image, behind_image, showing, done=None, hold=False, right=False):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPainter
        from PyQt6.QtWidgets import QWidget

        class Cover(QWidget):
            def paintEvent(cover, event):
                p = QPainter(cover)
                p.drawPixmap(QPointF(0, 0), behind_image)
                p.drawPixmap(QPointF(cover.offset if right else -cover.offset, 0), sidebar_image)  # (off its own edge)
                p.end()

        self.cover = Cover(window)
        self.cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.cover.offset = rect.width() if showing else 0
        self.cover.setGeometry(rect)
        self.cover.show()
        self.cover.raise_()

        def step(value):
            self.cover.offset = value
            self.cover.update()

        def finished():
            self.cover.hide()
            self.cover.deleteLater()
            if done:
                done()
        if not hold:  # hold: just stand still over the strip until the caller replaces it
            animate(self.cover, self.cover.offset, 0 if showing else rect.width(), step, NORMAL, finished)


def crossfade(host, widget, change, ms=FAST + 40):
    """Run change() (which rearranges `widget`), fading a picture of how it looked away over the
    new arrangement, so it doesn't jump (or flash half laid out on the way)."""
    if reduced() or not widget.isVisible() or not host.isVisible():
        change()
        return
    from PyQt6.QtCore import QPoint, QRect
    from PyQt6.QtGui import QPainter
    from PyQt6.QtWidgets import QWidget

    rect = QRect(widget.mapTo(host, QPoint(0, 0)), widget.size())
    picture = host.grab(rect)

    class Cover(QWidget):
        opacity = 1.0

        def paintEvent(cover, event):
            p = QPainter(cover)
            p.setOpacity(cover.opacity)
            p.drawPixmap(0, 0, picture)
            p.end()

    cover = Cover(host)
    cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    cover.setGeometry(rect)
    cover.show()
    cover.raise_()
    change()

    def step(value):
        cover.opacity = value
        cover.update()
    animate(cover, 1.0, 0.0, step, ms, lambda: (cover.hide(), cover.deleteLater()))


class FadesIn:
    """Mixin for panel windows (Settings, the Library, Downloads…): a quick fade in when one appears,
    instead of popping up. It starts see-through before it's ever drawn, so nothing flashes."""

    def setVisible(self, visible):
        appearing = visible and not self.isVisible() and self.isWindow() and not reduced()
        if appearing:
            self.setWindowOpacity(0.0)
        super().setVisible(visible)
        if appearing:
            animate(self, 0.0, 1.0, self.setWindowOpacity, FAST + 30)
