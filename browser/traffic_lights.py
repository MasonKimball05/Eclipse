# The traffic lights, Arc's way: they belong to the sidebar. They're there while it is (on the right
# too: they move over into it), gone while it's hidden, and come out with it when it slides out over
# the page. They stay dim until the pointer comes near them, then light up.
#
# Whether the pointer's near is checked a few times a second while they're showing: the native buttons
# take the mouse over themselves, so Qt doesn't see every move there.

from PyQt6.QtCore import QObject, QPoint, QRect, QTimer
from PyQt6.QtGui import QCursor

from . import macos

DIM = 0.35   # how they look until you point at them
REACH = 14   # points around them that already count as pointing at them
CHECK_MS = 80


class TrafficLights(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.showing = True
        self.offset = (0, 0)
        self._lit = None
        self.timer = QTimer(self, interval=CHECK_MS, timeout=self._check)

    def place(self, showing: bool, dx: float = 0, dy: float = 0):
        """Put them `dx` right and `dy` down from where macOS puts them, showing or not."""
        self.showing, self.offset = showing, (dx, dy)
        self._lit = self.near()
        # Shown first, then moved: showing them has macOS lay them out again, where it likes them
        macos.style_traffic_lights(self.window, not showing, 1.0 if self._lit else DIM)
        macos.shift_traffic_lights(self.window, dx, dy)
        QTimer.singleShot(0, lambda: self.offset == (dx, dy) and macos.shift_traffic_lights(self.window, dx, dy))
        self.activity()

    def activity(self):
        """Watch for the pointer only while they're showing and the window is the one you're using."""
        if self.showing and self.window.isActiveWindow():
            self.timer.start()
        else:
            self.timer.stop()
            if self.showing and self._lit:  # (dim again, the pointer having gone elsewhere)
                self._lit = False
                macos.style_traffic_lights(self.window, False, DIM, animate=True)

    def keep(self):
        """Put them back where they belong if macOS has laid them out again (it does when the window's
        title changes, say): a no-op when they're where they should be."""
        if self.offset != (0, 0):
            macos.shift_traffic_lights(self.window, *self.offset)

    def zone(self) -> QRect | None:
        """Where they are in the window, give or take REACH."""
        lights = macos.traffic_lights(self.window)
        if lights is None:
            return None
        right, centre = lights
        top_left = QPoint(round(self.offset[0]), 0)
        return QRect(top_left, QPoint(round(right), round(centre * 2))).adjusted(-REACH, -REACH, REACH, REACH)

    def near(self) -> bool:
        zone = self.zone()
        return zone is not None and self.window.isActiveWindow() and \
            zone.contains(self.window.mapFromGlobal(QCursor.pos()))

    def _check(self):
        self.keep()
        lit = self.near()
        if lit != self._lit and self.showing:
            self._lit = lit
            macos.style_traffic_lights(self.window, False, 1.0 if lit else DIM, animate=True)
