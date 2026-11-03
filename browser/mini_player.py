# Arc's mini player: switch away from a tab that's playing a video and the video keeps playing
# in a small floating window. On hover: its title, "back to tab" and close along the top, and back 10 seconds,
# play/pause, ahead 10 seconds and next video (where the site has one) along the bottom. Drag its top to move it,
# or an edge or corner to resize it (keeping the video's shape); let go and it glides to the nearest corner of the
# screen, like Arc's. Its size and corner are remembered.
#
# Qt WebEngine has no picture-in-picture, so this moves the tab's live web view into the floating
# window and has the page show only the video (a style that makes it fill the viewport). Going back
# to the tab, or closing the player, puts everything back.

import sys

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

import qtawesome as qta

from . import macos, motion
from .theme import stylesheet

DEFAULT_SIZE = QSize(400, 225)
MIN_SIZE, MAX_SIZE = QSize(240, 135), QSize(800, 450)  # (resized from its edges: always the video's shape)
ASPECT = DEFAULT_SIZE.width() / DEFAULT_SIZE.height()  # (16:9, as most videos)
MARGIN = 20  # (from the screen's edges, in its corner)
GRIP = 8  # how far in from its edges they resize it (corners: twice that)
SIZE_SETTING, CORNER_SETTING = "mini_player_size", "mini_player_corner"  # ([w, h]; "tl", "tr", "bl" or "br")
RADIUS = 12  # (its rounded corners, like Arc's)
BORDER = 0.22  # (a light edge just inside them, so it stands out on a dark screen as on a light one)
SKIP = 10  # seconds back or ahead
# The page script runs in the application world: it shares the page's DOM but not its JavaScript
WORLD = 1
FIND_PLAYING = """(() => [...document.querySelectorAll('video')]
  .some((v) => !v.paused && !v.ended && v.readyState > 2 && v.videoWidth > 0))()"""
VIDEO_ONLY = """(() => {
  const videos = [...document.querySelectorAll('video')].filter((v) => !v.ended && v.videoWidth > 0);
  const video = videos.find((v) => !v.paused) || videos[0];
  if (!video) return false;
  video.setAttribute('data-pb-mini', '');
  const style = document.createElement('style');
  style.id = 'pb-mini-style';
  style.textContent = `
    html, body { overflow: hidden !important; }
    video[data-pb-mini] { position: fixed !important; inset: 0 !important; width: 100vw !important;
      height: 100vh !important; max-width: none !important; max-height: none !important; margin: 0 !important;
      transform: none !important; z-index: 2147483647 !important; background: #000 !important;
      object-fit: contain !important; }`;
  document.documentElement.appendChild(style);
  return true;
})()"""
RESTORE = """(() => {
  document.getElementById('pb-mini-style')?.remove();
  document.querySelectorAll('[data-pb-mini]').forEach((v) => v.removeAttribute('data-pb-mini'));
})()"""
TOGGLE_PLAY = """(() => {
  const v = document.querySelector('video[data-pb-mini]');
  if (!v) return null;
  if (!v.paused) { v.pause(); return true; }
  return v.play().then(() => false, () => true);  // (refused, say by "videos wait for a click": still paused)
})()"""
IS_PAUSED = "document.querySelector('video[data-pb-mini]')?.paused ?? null"
PAUSE = "document.querySelector('video[data-pb-mini]')?.pause()"
# The site's own next button (YouTube, Spotify, SoundCloud, YouTube Music…), shown: Now Playing's next track too
NEXT_BUTTON = """['.ytp-next-button', '[data-testid="control-button-skip-forward"]', '.skipControl__next',
   'button[aria-label="Next"]', 'button[aria-label="Next track"]', 'ytmusic-player-bar .next-button']
  .map((s) => document.querySelector(s)).find((b) => b && b.offsetParent !== null)"""
HAS_NEXT = f"!!({NEXT_BUTTON})"
NEXT_VIDEO = f"(() => {{ const next = {NEXT_BUTTON}; if (next) next.click(); return !!next; }})()"


def seek(seconds):
    return f"""(() => {{
  const v = document.querySelector('video[data-pb-mini]');
  if (!v) return null;
  v.currentTime = Math.max(0, v.currentTime + ({seconds}));
  return v.currentTime;
}})()"""


class Grip(QWidget):
    """An edge or corner of the player: drag it to resize the player."""

    CURSORS = {"l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
               "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
               "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
               "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor}

    def __init__(self, window, edges):
        super().__init__(window)
        self.window_, self.edges = window, edges
        self.setCursor(self.CURSORS[edges])
        self._from = None

    def mousePressEvent(self, event):
        motion.stop(self.window_)  # (still gliding to its corner: the resize starts from where it is)
        self._from = (event.globalPosition().toPoint(), self.window_.geometry())

    def mouseMoveEvent(self, event):
        if self._from is not None:
            start, geometry = self._from
            self.window_.resize_from(self.edges, geometry, event.globalPosition().toPoint() - start)

    def mouseReleaseEvent(self, event):
        self._from = None
        self.window_.player.settle()


class MiniPlayerWindow(QFrame):
    def __init__(self, player):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.player = player
        self.setObjectName("MiniPlayer")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        if sys.platform == "darwin":  # (see-through outside its rounded corners: macos.round_window)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(DEFAULT_SIZE)
        self._drag_from: QPoint | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view_layout = layout

        # Controls float over the video and show on hover
        self.controls = QWidget(self)
        self.controls.setObjectName("MiniControls")
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(10, 6, 6, 6)
        self.title = QLabel()
        self.title.setObjectName("MiniTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.title, 1)
        row.addWidget(self._button("ph.arrow-square-out", "Back to Tab", player.back_to_tab))
        row.addWidget(self._button("ph.x", "Close", player.close))
        self.controls.hide()

        # …and the player's own along the bottom
        self.transport = QWidget(self)
        self.transport.setObjectName("MiniTransport")
        row = QHBoxLayout(self.transport)
        row.setContentsMargins(6, 4, 6, 8)
        row.setSpacing(6)
        row.addStretch(1)
        self.back_btn = self._button("ph.rewind-fill", f"Back {SKIP} Seconds", lambda: player.skip(-SKIP))
        self.play_btn = self._button("ph.pause-fill", "Play / Pause", player.toggle_play, 22)
        self.ahead_btn = self._button("ph.fast-forward-fill", f"Ahead {SKIP} Seconds", lambda: player.skip(SKIP))
        self.next_btn = self._button("ph.skip-forward-fill", "Next Video", player.next_video)
        for button in (self.back_btn, self.play_btn, self.ahead_btn, self.next_btn):
            row.addWidget(button)
        row.addStretch(1)
        self.next_btn.hide()  # (only where the site has a next one)
        self.transport.hide()
        self.grips = [Grip(self, edges) for edges in ("l", "r", "t", "b", "tl", "tr", "bl", "br")]

    def _button(self, icon, tip, slot, size=16):
        button = QToolButton()
        button.setObjectName("MiniButton")
        button.setIcon(qta.icon(icon, color="white"))
        button.setIconSize(QSize(size, size))
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def set_playing(self, playing):
        self.play_btn.setIcon(qta.icon("ph.pause-fill" if playing else "ph.play-fill", color="white"))

    def resizeEvent(self, event):
        self.controls.setGeometry(0, 0, self.width(), 40)
        self.transport.setGeometry(0, self.height() - 52, self.width(), 52)
        w, h, c = self.width(), self.height(), GRIP * 2
        places = {"l": (0, c, GRIP, h - 2 * c), "r": (w - GRIP, c, GRIP, h - 2 * c),
                  "t": (c, 0, w - 2 * c, GRIP), "b": (c, h - GRIP, w - 2 * c, GRIP),
                  "tl": (0, 0, c, c), "tr": (w - c, 0, c, c), "bl": (0, h - c, c, c), "br": (w - c, h - c, c, c)}
        for grip in self.grips:
            grip.setGeometry(*places[grip.edges])
        super().resizeEvent(event)

    def raise_grips(self):
        for grip in self.grips:  # (over the page and the controls: the edges always resize)
            grip.raise_()

    def resize_from(self, edges, start: QRect, delta: QPoint):
        """Dragged by `edges` (from `start`, `delta` so far): a new size in the video's shape, the opposite edges
        staying put."""
        aspect = ASPECT  # (fixed, not measured each time: rounding would drift it a point at a time)
        if "l" in edges or "r" in edges:
            width = start.width() + (delta.x() if "r" in edges else -delta.x())
        else:
            width = (start.height() + (delta.y() if "b" in edges else -delta.y())) * aspect
        width = max(MIN_SIZE.width(), min(MAX_SIZE.width(), round(width)))
        height = round(width / aspect)
        x = start.right() + 1 - width if "l" in edges else start.x()
        y = start.bottom() + 1 - height if "t" in edges else start.y()
        self.setGeometry(x, y, width, height)

    def enterEvent(self, event):
        self.player.refresh()  # (whether there's a next video now, and its title)
        for bar in (self.controls, self.transport):
            bar.raise_()
            motion.fade(bar, True)  # (fading, as the other hover controls do)

    def leaveEvent(self, event):
        for bar in (self.controls, self.transport):
            motion.fade(bar, False)

    # Drag anywhere on the control strip to move the player
    def mousePressEvent(self, event):
        motion.stop(self)  # (grabbed again mid-glide: the drag has it, not the glide)
        self._drag_from = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event):
        if self._drag_from is not None:
            self._drag_from = None
            self.player.settle()


class MiniPlayer:
    def __init__(self, browser):
        self.browser = browser
        self.tab = None
        self.window: MiniPlayerWindow | None = None

    @property
    def enabled(self) -> bool:
        return self.browser.store.settings.get("mini_player_enabled", True)

    # Called by the window ----------------------------------------------------------------------

    def _on_screen(self, tab) -> bool:
        """The current tab, or a pane next to it in Split View: still visible, so not floated."""
        current = self.browser.current_tab
        return tab is current or (current is not None and any(tab is t for t in self.browser.store.split_of(current) or []))

    def left_tab(self, tab):
        """The user switched away from `tab`: if it's playing a video, float it."""
        if (not self.enabled or tab is None or tab is self.tab or self._on_screen(tab)
                or not self.browser.tab_audio(tab)[0]):
            return
        view = tab.view
        view.page().runJavaScript(FIND_PLAYING, WORLD, lambda playing: playing and self._float(tab))

    def release(self, tab):
        """`tab` is about to be shown in the main window again (or closed): take it back."""
        if tab is not None and tab is self.tab:
            self._restore(return_view=False)

    def audible_changed(self, tab):
        # Asked of the video: a quiet stretch isn't a pause, and sound starting or stopping may be the site's own controls
        if tab is self.tab and self.window is not None:
            tab.view.page().runJavaScript(IS_PAUSED, WORLD, lambda paused: paused is not None and tab is self.tab
                                          and self.window is not None and self.window.set_playing(not paused))

    # The player itself ----------------------------------------------------------------------------

    def _float(self, tab):
        # Still away from it? (The user may have come straight back, or put it in Split View.)
        if self._on_screen(tab) or tab.view is None or self.tab is not None:
            return
        view = tab.view
        # Only with a video to show (the page may have swapped its player since): never the whole page in it
        view.page().runJavaScript(VIDEO_ONLY, WORLD, lambda found: found and self._show_player(tab))

    def _show_player(self, tab):
        if self._on_screen(tab) or tab.view is None or self.tab is not None:
            tab.view is not None and tab.view.page().runJavaScript(RESTORE, WORLD)
            return
        if self.window is None:
            self.window = MiniPlayerWindow(self)
        # Each time, on the screen the browser is on now, in its corner, in the Space's look
        self.window.setStyleSheet(stylesheet(self.browser.theme) + MINI_STYLE)
        settings = self.browser.store.settings
        w = (settings.get(SIZE_SETTING) or [DEFAULT_SIZE.width()])[0]
        w = max(MIN_SIZE.width(), min(MAX_SIZE.width(), w))
        self.window.resize(w, round(w / ASPECT))
        self.window.move(self._corner_spot(settings.get(CORNER_SETTING, "br"), self._screen_area()))
        self.tab = tab
        view = tab.view
        self.window.view_layout.addWidget(view)  # takes it out of the main window's page stack
        view.show()
        self.window.title.setText(tab.label)
        self.window.set_playing(True)
        self.window.show()
        macos.round_window(self.window, RADIUS, BORDER)  # (the page in it too, which its style can't round)
        self.window.controls.raise_()
        self.window.transport.raise_()
        self.window.raise_grips()
        self.refresh()

    def _restore(self, return_view=True):
        tab, self.tab = self.tab, None
        if self.window is not None:
            self.window.hide()
        if tab is None or tab.view is None:
            return
        tab.view.page().runJavaScript(RESTORE, WORLD)
        if return_view:  # back into the main window's stack, behind the current page
            self.browser.content.stack.addWidget(tab.view)

    def toggle_play(self):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(TOGGLE_PLAY, WORLD,
                                               lambda paused: paused is not None and self.window.set_playing(not paused))

    def _screen_area(self) -> QRect:
        """The screen it's on (or the browser's, before it's anywhere), less the menu bar and Dock."""
        where = self.window.geometry().center() if self.window is not None and self.window.isVisible() \
            else self.browser.geometry().center()
        screen = QGuiApplication.screenAt(where) or self.browser.screen()
        return screen.availableGeometry()

    def _corner_spot(self, corner: str, area: QRect) -> QPoint:
        size = self.window.size()
        x = area.left() + MARGIN if corner[1] == "l" else area.right() + 1 - MARGIN - size.width()
        y = area.top() + MARGIN if corner[0] == "t" else area.bottom() + 1 - MARGIN - size.height()
        return QPoint(x, y)

    def settle(self):
        """Let go after a move or resize: it glides to the nearest corner of the screen, and its size and corner are
        kept for next time."""
        window = self.window
        if window is None:
            return
        area, center = self._screen_area(), window.geometry().center()
        corner = ("t" if center.y() < area.center().y() else "b") + ("l" if center.x() < area.center().x() else "r")
        settings = self.browser.store.settings
        settings[CORNER_SETTING] = corner
        settings[SIZE_SETTING] = [window.width(), window.height()]
        self.browser.store.schedule_save()
        start, end = window.pos(), self._corner_spot(corner, area)
        motion.animate(window, 0.0, 1.0, lambda t: window.move(round(start.x() + (end.x() - start.x()) * t),
                                                                round(start.y() + (end.y() - start.y()) * t)),
                       motion.NORMAL)

    def skip(self, seconds):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(seek(seconds), WORLD)

    def next_video(self):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(NEXT_VIDEO, WORLD)

    def refresh(self):
        """Its title, and whether the site has a next video to skip to."""
        tab = self.tab
        if tab is None or self.window is None:
            return
        self.window.title.setText(tab.label)
        tab.view.page().runJavaScript(HAS_NEXT, WORLD, lambda has: tab is self.tab and self.window is not None
                                      and self.window.next_btn.setVisible(bool(has)))

    def back_to_tab(self):
        if self.tab is not None:
            if self.browser.store.locate(self.tab) is None:
                self.close()  # (archived or moved away meanwhile: nothing to go back to)
                return
            self.browser.activate_tab(self.tab)  # which calls release()
            self.browser.raise_()
            self.browser.activateWindow()

    def close(self):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(PAUSE, WORLD)
        self._restore()

    def shutdown(self):
        if self.tab is not None:
            self._restore()
        if self.window is not None:
            self.window.deleteLater()
            self.window = None


MINI_STYLE = """
#MiniPlayer { background: #000; border-radius: 12px; }  /* (RADIUS) */
#MiniControls { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(0,0,0,170), stop:1 rgba(0,0,0,0)); }
#MiniTransport { background: qlineargradient(x1:0, y1:1, x2:0, y2:0, stop:0 rgba(0,0,0,170), stop:1 rgba(0,0,0,0)); }
#MiniTitle { color: white; font-weight: 600; }
QToolButton#MiniButton { background: transparent; border: none; border-radius: 6px; padding: 4px; }
QToolButton#MiniButton:hover { background: rgba(255,255,255,40); }
"""
