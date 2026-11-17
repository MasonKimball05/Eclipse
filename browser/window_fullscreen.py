# Full screen: a page's video filling the screen, and macOS full screen (⌃⌘F).
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWebEngineCore import QWebEnginePage

from .models import display_host


class FullScreen:
    def _on_fullscreen(self, request, view=None):
        if request.toggleOn() and view is not None and not view.isVisible():
            request.reject()  # (only the page you're looking at: one in the background can't take over the screen)
            return
        floated = self.mini_player.tab
        if request.toggleOn() and view is not None and floated is not None and view is floated.view:
            # (the mini player's video, double-clicked: accepted, this window went full screen, showing another tab,
            # its sidebar gone, and the video stayed small in its corner)
            request.reject()
            return
        request.accept()
        self._fullscreen_view = view if request.toggleOn() else None
        self._set_fullscreen(request.toggleOn())
        if request.toggleOn():
            # Said every time, so a page can't pass off its own drawing of a browser (or a password box) as real
            host = display_host(request.origin().toString()) or "This page"
            self.toast(f"{host} is full screen. Press Esc to leave.")

    def _leave_page_fullscreen(self):
        view = getattr(self, "_fullscreen_view", None)
        if view is not None and not sip.isdeleted(view):
            view.page().triggerAction(QWebEnginePage.WebAction.ExitFullScreen)
        else:
            self._set_fullscreen(False)

    def _set_fullscreen(self, on):
        was = self._fullscreen
        self._fullscreen = on
        if self._fullscreen:
            if not was:
                # Whether the sidebar is meant to be there (the hidden one, slid out over the page, isn't)
                self._sidebar_before_fullscreen = self.store.settings.get("sidebar_visible", True)
                # How the window was (zoomed, or already macOS full screen), to go back to it, not just to normal
                self._state_before_fullscreen = self.windowState()
                self._geometry_before_fullscreen = self.saveGeometry()  # (what's saved if you quit meanwhile)
            self.spread_sidebar.fold(animate=False)
            if self.floating_sidebar.active:
                self.sidebar.hide()
                self._dock_sidebar()
            self.sidebar.hide()
            if not was:
                self._solo_fullscreen_view(True)
            self.showFullScreen()
        else:
            self.sidebar.setVisible(getattr(self, "_sidebar_before_fullscreen", True))
            self._solo_fullscreen_view(False)
            state = getattr(self, "_state_before_fullscreen", Qt.WindowState.WindowNoState)
            if state & Qt.WindowState.WindowFullScreen:
                pass  # (macOS full screen before the video: still)
            elif state & Qt.WindowState.WindowMaximized:
                self.showMaximized()
            else:
                self.showNormal()
        self.content.set_rounded(not self._fullscreen)
        self._update_content_margins()

    def geometry_to_save(self):
        """The window's size and place, to open with next time: with a video filling the screen, as it was before
        (else the next launch opened in full screen, with nothing to take it out of it but the green button)."""
        return self._geometry_before_fullscreen if self._fullscreen else self.saveGeometry()

    def _solo_fullscreen_view(self, on):
        """The page filling the screen, alone: what's beside it (Ask About This Page, the web panel, Split View's
        other panes) hides while it does, and comes back after."""
        if on:
            view = getattr(self, "_fullscreen_view", None)
            beside = [self.content_layout.itemAt(i).widget() for i in range(self.content_layout.count())]
            split = self.content.split_view
            if self.content.splitting and view is not None:
                mine = split.pane_containing(view)
                beside += [p for p in split.panes if p is not mine]
            self._hidden_for_fullscreen = [w for w in beside if w is not None and w is not self.content
                                           and not w.isHidden()]
            for widget in self._hidden_for_fullscreen:
                widget.hide()
        else:
            for widget in getattr(self, "_hidden_for_fullscreen", []):
                if not sip.isdeleted(widget):
                    widget.show()
            self._hidden_for_fullscreen = []

    def _toggle_full_screen(self):
        window = self._front_window()
        if window is self and self._fullscreen:
            # (a video that went full screen: out of it, and out of macOS full screen too if it was in that before)
            self._state_before_fullscreen = self._state_before_fullscreen & ~Qt.WindowState.WindowFullScreen
            self._leave_page_fullscreen()
        elif window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()
