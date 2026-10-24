# Arc's mini player: switch away from a tab that's playing a video and the video keeps playing
# in a small floating window, with play/pause, "back to tab" and close on hover.
#
# Qt WebEngine has no picture-in-picture, so this moves the tab's live web view into the floating
# window and has the page show only the video (a style that makes it fill the viewport). Going back
# to the tab, or closing the player, puts everything back.

from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

import qtawesome as qta

from .theme import stylesheet

DEFAULT_SIZE = QSize(400, 225)
MARGIN = 20
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
  v.paused ? v.play() : v.pause();
  return v.paused;
})()"""
PAUSE = "document.querySelector('video[data-pb-mini]')?.pause()"


class MiniPlayerWindow(QFrame):
    def __init__(self, player):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.player = player
        self.setObjectName("MiniPlayer")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
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
        row.addWidget(self.title, 1)
        self.play_btn = self._button("ph.pause-fill", "Play / Pause", player.toggle_play)
        row.addWidget(self.play_btn)
        row.addWidget(self._button("ph.arrow-square-out", "Back to Tab", player.back_to_tab))
        row.addWidget(self._button("ph.x", "Close", player.close))
        self.controls.hide()

    def _button(self, icon, tip, slot):
        button = QToolButton()
        button.setObjectName("MiniButton")
        button.setIcon(qta.icon(icon, color="white"))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def set_playing(self, playing):
        self.play_btn.setIcon(qta.icon("ph.pause-fill" if playing else "ph.play-fill", color="white"))

    def resizeEvent(self, event):
        self.controls.setGeometry(0, 0, self.width(), 40)
        super().resizeEvent(event)

    def enterEvent(self, event):
        self.controls.show()
        self.controls.raise_()

    def leaveEvent(self, event):
        self.controls.hide()

    # Drag anywhere on the control strip to move the player
    def mousePressEvent(self, event):
        self._drag_from = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)

    def mouseReleaseEvent(self, event):
        self._drag_from = None


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
        if tab is self.tab and self.window is not None:
            self.window.set_playing(self.browser.tab_audio(tab)[0])

    # The player itself ----------------------------------------------------------------------------

    def _float(self, tab):
        # Still away from it? (The user may have come straight back, or put it in Split View.)
        if self._on_screen(tab) or tab.view is None or self.tab is not None:
            return
        if self.window is None:
            self.window = MiniPlayerWindow(self)
            self.window.setStyleSheet(stylesheet(self.browser.theme) + MINI_STYLE)
            corner = self.browser.geometry().bottomRight() - QPoint(DEFAULT_SIZE.width() + MARGIN,
                                                                    DEFAULT_SIZE.height() + MARGIN)
            self.window.move(corner)
        self.tab = tab
        view = tab.view
        view.page().runJavaScript(VIDEO_ONLY, WORLD)
        self.window.view_layout.addWidget(view)  # takes it out of the main window's page stack
        view.show()
        self.window.title.setText(tab.label)
        self.window.set_playing(True)
        self.window.show()
        self.window.controls.raise_()

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

    def back_to_tab(self):
        if self.tab is not None:
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
#MiniPlayer { background: #000; border-radius: 12px; }
#MiniControls { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(0,0,0,170), stop:1 rgba(0,0,0,0)); }
#MiniTitle { color: white; font-weight: 600; }
QToolButton#MiniButton { background: transparent; border: none; border-radius: 6px; padding: 4px; }
QToolButton#MiniButton:hover { background: rgba(255,255,255,40); }
"""
