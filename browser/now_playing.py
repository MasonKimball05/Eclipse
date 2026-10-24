# Now Playing: when a tab you've left is playing music or a video, a small card at the bottom of
# the sidebar shows what it is (from the page's media session: title and artist), with play/pause,
# next track where the site has one, and × to stop. Click the card to go back to the tab.
#
# Videos you switch away from go to the mini player instead; the card is for everything else
# (Spotify, SoundCloud, a podcast, YouTube Music), and for videos when the mini player is off.

from PyQt6.QtCore import QObject, QSize, Qt, QTimer
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

import qtawesome as qta

from .models import display_host

WORLD = 1  # the application world: the page's DOM, not its JavaScript
POLL_MS = 1500

# Remembers the last thing that played. Audio made with new Audio() never joins the document, so
# the application world can't find it: a small hook in the page itself keeps hold of that one and
# answers commands sent as a DOM event, reporting its state in an attribute on <html>.
TRACK = """
(() => {
  if (window.__pbNowPlayingWatch) return;
  window.__pbNowPlayingWatch = true;
  document.addEventListener('play', (e) => { window.__pbNowPlaying = e.target; }, true);
})();
"""
TRACK_DETACHED = """
(() => {
  const play = HTMLMediaElement.prototype.play;
  let last = null;
  // (looked up each time: at document creation there's no <html> yet)
  const report = () => last && document.documentElement && document.documentElement.setAttribute('data-pb-media', last.paused ? 'paused' : 'playing');
  HTMLMediaElement.prototype.play = function () {
    if (!this.isConnected && last !== this) {
      last = this;
      this.addEventListener('play', report);
      this.addEventListener('pause', report);
    }
    const result = play.apply(this, arguments);
    report();
    return result;
  };
  document.addEventListener('pb-media', () => {
    if (!last) return;
    const command = document.documentElement.getAttribute('data-pb-media-command');
    if (command === 'toggle') last.paused ? last.play() : last.pause();
    if (command === 'pause') last.pause();
    report();
  });
})();
"""
FIND = """
  const media = [...document.querySelectorAll('video, audio')];
  const el = media.find((m) => !m.paused && !m.ended) || window.__pbNowPlaying ||
             media.find((m) => m.currentTime > 0);
  const root = document.documentElement;
  const detached = !el && root.getAttribute('data-pb-media');
  const send = (command) => {
    root.setAttribute('data-pb-media-command', command);
    document.dispatchEvent(new Event('pb-media'));
    return root.getAttribute('data-pb-media') === 'paused';
  };
  const next = ['.ytp-next-button', '[data-testid="control-button-skip-forward"]', '.skipControl__next',
                'button[aria-label="Next"]', 'button[aria-label="Next track"]', 'ytmusic-player-bar .next-button']
               .map((s) => document.querySelector(s)).find((b) => b && b.offsetParent !== null);
"""
INFO = "(() => {" + FIND + """
  const meta = navigator.mediaSession && navigator.mediaSession.metadata;
  return {title: (meta && meta.title) || '', artist: (meta && (meta.artist || meta.album)) || '',
          paused: el ? el.paused : detached ? detached === 'paused' : null, next: !!next};
})()"""
TOGGLE = "(() => {" + FIND + """
  if (detached) return send('toggle');
  if (!el) return null;
  el.paused ? el.play() : el.pause();
  return el.paused;
})()"""
NEXT = "(() => {" + FIND + " if (next) next.click(); return !!next; })()"
PAUSE = "(() => {" + FIND + " if (detached) send('pause'); else if (el) el.pause(); })()"


def track_scripts():
    scripts = []
    for name, source, world in (("pb-now-playing", TRACK, WORLD),
                                ("pb-now-playing-page", TRACK_DETACHED, QWebEngineScript.ScriptWorldId.MainWorld)):
        script = QWebEngineScript()
        script.setName(name)
        script.setSourceCode(source)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(world)
        script.setRunsOnSubFrames(False)
        scripts.append(script)
    return scripts


def describe(info: dict, tab_label: str, url: str) -> tuple[str, str]:
    """(first line, second line) for the card: the track and artist, or the tab and its site."""
    title = (info or {}).get("title") or tab_label or display_host(url)
    artist = (info or {}).get("artist") or ""
    second = artist if artist and artist != title else display_host(url)
    return title, second


class NowPlayingCard(QFrame):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("NowPlaying")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 4, 6)
        row.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(QSize(22, 22))
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("NowPlayingTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)  # sites write these
        self.subtitle = QLabel()
        self.subtitle.setObjectName("NowPlayingSub")
        self.subtitle.setTextFormat(Qt.TextFormat.PlainText)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        self.play_btn = self._button("Play / Pause", controller.toggle)
        self.next_btn = self._button("Next", controller.next_track)
        self.close_btn = self._button("Stop", controller.stop)
        row.addWidget(self.icon)
        row.addLayout(text, 1)
        for b in (self.play_btn, self.next_btn, self.close_btn):
            row.addWidget(b)
        self.hide()

    def _button(self, tip, slot):
        b = QToolButton()
        b.setObjectName("NowPlayingButton")
        b.setToolTip(tip)
        b.setIconSize(QSize(14, 14))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def set_state(self, title, subtitle, icon, paused, has_next, color):
        self._full = (title, subtitle)
        self.icon.setPixmap(icon.pixmap(22, 22))
        self.play_btn.setIcon(qta.icon("ph.play-fill" if paused else "ph.pause-fill", color=color))
        self.next_btn.setIcon(qta.icon("ph.skip-forward-fill", color=color))
        self.close_btn.setIcon(qta.icon("ph.x", color=color))
        self.next_btn.setVisible(has_next)
        self.setToolTip(f"{title} — {subtitle}\nClick to go to the tab")
        self._elide()

    def _elide(self):
        title, subtitle = getattr(self, "_full", ("", ""))
        room = max(self.title.width(), 40)
        self.title.setText(self.title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, room))
        self.subtitle.setText(self.subtitle.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, room))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.controller.go_to_tab()


class NowPlaying(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.tab = None
        self.card: NowPlayingCard | None = None
        self._info = {}
        self._poll = QTimer(self, interval=POLL_MS, timeout=self.refresh)

    def attach_profile(self, profile):
        if not any(s.name() == "pb-now-playing" for s in profile.scripts().toList()):
            for script in track_scripts():
                profile.scripts().insert(script)

    def _on_screen(self, tab) -> bool:
        return self.browser.mini_player._on_screen(tab)

    def _eligible(self, tab) -> bool:
        return (tab is not None and tab.view is not None and hasattr(tab.view, "page")
                and not self._on_screen(tab) and tab is not self.browser.mini_player.tab
                and self.browser.store.locate(tab) is not None)

    # --- Called by the window ----------------------------------------------------------------------

    def audible_changed(self, tab):
        if self.browser.tab_audio(tab)[0] and self._eligible(tab):
            self._follow(tab)
        elif tab is self.tab:
            self.refresh()

    def left_tab(self, tab):
        """You switched away from `tab`: if it's still playing (and the mini player didn't take it), show it."""
        if tab is not None and self.browser.tab_audio(tab)[0]:
            QTimer.singleShot(400, lambda: self._eligible(tab) and self._follow(tab))  # after the mini player decides

    def tab_shown(self, tab):
        if tab is self.tab or (self.tab is not None and self._on_screen(self.tab)):
            self._hide()

    def forget(self, tab):
        if tab is self.tab:
            self.tab = None
            self._hide()

    # --- The card ----------------------------------------------------------------------------------

    def _follow(self, tab):
        self.tab = tab
        self.refresh()
        self._poll.start()

    def _hide(self):
        self._poll.stop()
        self.tab = None
        if self.card is not None:
            self.card.hide()

    def refresh(self):
        tab = self.tab
        if tab is None or not self._eligible(tab):
            self._hide()
            return
        tab.view.page().runJavaScript(INFO, WORLD, lambda info, t=tab: self._show(t, info))

    def _show(self, tab, info):
        if tab is not self.tab or self.card is None:
            return
        info = info if isinstance(info, dict) else {}
        if info.get("paused") is None and not self.browser.tab_audio(tab)[0]:
            self._hide()  # nothing to control any more (the page navigated away)
            return
        from .sidebar import tab_icon  # (the sidebar holds the card, so it imports this module)
        theme = self.browser.theme
        title, subtitle = describe(info, tab.label, tab.url)
        paused = info.get("paused") is True or (info.get("paused") is None and not self.browser.tab_audio(tab)[0])
        self.card.set_state(title, subtitle, tab_icon(tab, theme), paused, bool(info.get("next")), theme.text)
        self.card.show()

    def toggle(self):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(TOGGLE, WORLD, lambda _paused: QTimer.singleShot(150, self.refresh))

    def next_track(self):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(NEXT, WORLD, lambda _ok: QTimer.singleShot(800, self.refresh))

    def stop(self):
        if self.tab is not None:
            self.tab.view.page().runJavaScript(PAUSE, WORLD)
        self._hide()

    def go_to_tab(self):
        tab = self.tab
        if tab is not None:
            self.browser.activate_tab(tab)
