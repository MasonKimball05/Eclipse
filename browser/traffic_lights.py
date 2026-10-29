# The traffic lights, Arc's way: they belong to the sidebar. They're there while it is (on the right
# too: they move over into it), gone while it's hidden, and come out with it when it slides out over
# the page. They stay dim until the pointer comes near them, then light up.
#
# They're drawn by Eclipse, as Arc draws its own: macOS's buttons stay where they are (moved with the sidebar,
# so everything that lines up with them still can) but hidden, and three circles of ours sit exactly on top.
# Dimming macOS's own buttons had them jump: while their brightness changed, macOS drew them a few points up
# and to the left. Ours dim and light up by being painted again, so nothing moves. Clicking one does what the
# button does, through macOS (a close as from the red button: Eclipse keeps running if it's set to). In full
# screen, macOS's own show (they're in its menu bar there) and ours don't.
#
# Whether the pointer's near is checked a few times a second while they're showing.

from PyQt6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QCursor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from . import macos

DIM = 0.35   # how they look until you point at them
REACH = 6    # points round the circles that already count as pointing at them
CHECK_MS = 80
# macOS's own colours: (fill, edge, the symbol drawn on it as you point at them)
COLORS = [("#ff5f57", "#e14640", "#4d0000"), ("#febc2e", "#dfa023", "#995700"), ("#28c840", "#1dad2b", "#006500")]
ACTIONS = ["close", "minimize", "full_screen"]
NAMES = ["Close", "Minimize", "Full Screen"]


class LightsOverlay(QWidget):
    """The three circles, over where macOS's hidden buttons are."""

    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.lit = False       # (the pointer's near)
        self.hover = False     # (the pointer's on them: their symbols show)
        self.pressed = None    # (the one held down)
        self.centres: list[QPointF] = []
        self.diameter = 14.0
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("Window controls")
        self.hide()

    def place(self, frames):
        """frames: macOS's buttons, (x, y, w, h) in the window."""
        if not frames:
            return
        area = QRectF()
        for x, y, w, h in frames:
            area = area.united(QRectF(x, y, w, h))
        self.setGeometry(area.toAlignedRect())
        origin = QPointF(self.x(), self.y())
        self.centres = [QPointF(x + w / 2, y + h / 2) - origin for x, y, w, h in frames]
        self.diameter = min(frames[0][2], frames[0][3])
        self.update()

    def which(self, pos) -> int | None:
        for i, centre in enumerate(self.centres):
            if (QPointF(pos) - centre).manhattanLength() <= self.diameter * 0.75:
                return i
        return None

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.window_.isActiveWindow()
        coloured = self.hover or (active and self.lit)
        radius = self.diameter / 2 - 0.5
        if not coloured and not active:  # (a window in the background: grey, like macOS's)
            grey = QColor(self.window_.theme.text if hasattr(self.window_, "theme") else QColor("#888"))
            grey.setAlphaF(0.22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grey)
            for centre in self.centres:
                painter.drawEllipse(centre, radius, radius)
            return
        painter.setOpacity(1.0 if coloured else DIM)
        for i, centre in enumerate(self.centres):
            fill, edge, mark = COLORS[i]
            colour = QColor(fill).darker(115) if self.pressed == i else QColor(fill)
            painter.setPen(QPen(QColor(edge), 0.6))
            painter.setBrush(colour)
            painter.drawEllipse(centre, radius, radius)
            if self.hover:
                self._symbol(painter, i, centre, radius, QColor(mark))

    @staticmethod
    def _symbol(painter, i, centre, radius, colour):
        size = radius * 0.4
        painter.setPen(QPen(colour, 1.0, cap=Qt.PenCapStyle.RoundCap))
        if i == 0:  # ×
            painter.drawLine(centre + QPointF(-size, -size), centre + QPointF(size, size))
            painter.drawLine(centre + QPointF(-size, size), centre + QPointF(size, -size))
        elif i == 1:  # −
            painter.drawLine(centre + QPointF(-size * 1.4, 0), centre + QPointF(size * 1.4, 0))
        else:  # full screen: two little triangles pointing apart
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            s = size * 1.15
            for sign in (1, -1):
                tip = centre + QPointF(-s * sign, -s * sign)
                painter.drawPolygon(tip, tip + QPointF(s * 1.2 * sign, 0), tip + QPointF(0, s * 1.2 * sign))

    def enterEvent(self, _event):
        self.hover = True
        self.update()

    def leaveEvent(self, _event):
        self.hover, self.pressed = False, None
        self.update()

    def mousePressEvent(self, event):
        self.pressed = self.which(event.position())
        self.update()

    def mouseReleaseEvent(self, event):
        pressed, self.pressed = self.pressed, None
        self.update()
        if pressed is not None and self.which(event.position()) == pressed:
            action = ACTIONS[pressed]
            if action == "full_screen" and event.modifiers() & Qt.KeyboardModifier.AltModifier:
                action = "zoom"  # (⌥-click: zoom, as macOS's own green button does)
            macos.window_button_action(self.window_, action)

    def mouseMoveEvent(self, event):
        index = self.which(event.position())
        self.setToolTip(NAMES[index] if index is not None else "")


class TrafficLights(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.showing = True
        self.offset = (0, 0)
        self._lit = None
        self.overlay = LightsOverlay(window)
        self.timer = QTimer(self, interval=CHECK_MS, timeout=self._check)

    def _native(self) -> bool:
        """Full screen: macOS's own buttons, in its menu bar."""
        return self.window.isFullScreen() or getattr(self.window, "_fullscreen", False)

    def place(self, showing: bool, dx: float = 0, dy: float = 0):
        """Put them `dx` right and `dy` down from where macOS puts them, showing or not."""
        self.showing, self.offset = showing, (dx, dy)
        self._lit = self.near()
        native = self._native()
        # macOS's: hidden unless in full screen (and never dimmed: that's what had them jump), then moved
        macos.style_traffic_lights(self.window, not (showing and native))
        macos.shift_traffic_lights(self.window, dx, dy)
        QTimer.singleShot(0, lambda: self.offset == (dx, dy) and (macos.shift_traffic_lights(self.window, dx, dy),
                                                                   self._sync()))
        self._sync()
        self.activity()

    def _sync(self):
        """Ours, exactly over macOS's, showing when they should be."""
        overlay = self.overlay
        frames = macos.traffic_light_frames(self.window)
        overlay.place(frames)
        overlay.lit = bool(self._lit)
        shown = bool(frames) and self.showing and not self._native()
        if shown:
            overlay.show()
            overlay.raise_()  # (over the sidebar, as macOS's are)
        else:
            overlay.hide()
        overlay.update()

    def activity(self):
        """Watch for the pointer only while they're showing and the window is the one you're using."""
        if self.showing and self.window.isActiveWindow():
            self.timer.start()
        else:
            self.timer.stop()
            if self.showing and self._lit:  # (dim again, the pointer having gone elsewhere)
                self._lit = False
        self.overlay.lit = bool(self._lit)
        self.overlay.update()  # (grey in a window in the background, like macOS's)

    def keep(self):
        """Put them back where they belong if macOS has laid them out again (it does when the window's
        title changes, say): a no-op when they're where they should be."""
        if self.offset != (0, 0):
            macos.shift_traffic_lights(self.window, *self.offset)
        if self.overlay.isVisible():
            self.overlay.place(macos.traffic_light_frames(self.window))

    def zone(self) -> QRect | None:
        """Where the circles are in the window, give or take REACH."""
        frames = macos.traffic_light_frames(self.window)
        if not frames:
            return None
        area = QRectF()
        for x, y, w, h in frames:
            area = area.united(QRectF(x, y, w, h))
        return area.toAlignedRect().adjusted(-REACH, -REACH, REACH, REACH)

    def near(self) -> bool:
        zone = self.zone()
        return zone is not None and self.window.isActiveWindow() and \
            zone.contains(self.window.mapFromGlobal(QCursor.pos()))

    def _check(self):
        self.keep()
        lit = self.near()
        if lit != self._lit and self.showing:
            self._lit = lit
            self.overlay.lit = lit
            self.overlay.update()
