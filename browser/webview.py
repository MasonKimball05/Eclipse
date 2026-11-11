import re
import traceback

from PyQt6.QtCore import QEvent, QObject, QStandardPaths, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QContextMenuEvent, QMouseEvent
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

from . import storage
from .external_links import OWN_SCHEME, is_external
from .theme import held_open




DEFAULT_PROFILE = "default"


def build_profile(parent, name=DEFAULT_PROFILE) -> QWebEngineProfile:
    # A named profile keeps cookies, logins and cache on disk between launches.
    # Spaces can each use their own profile, so they're signed in to different accounts.
    folder = storage.profile_dir(name)
    storage.clear_site_data_if_asked(folder)  # (Clear Cookies & Site Data: what Chromium had open last time)
    profile = QWebEngineProfile(name, parent)
    profile.setPersistentStoragePath(str(folder))
    profile.setCachePath(str(folder / "cache"))
    profile.setDownloadPath(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))
    look_like_chrome(profile)

    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
    harden(profile)
    return profile


def look_like_chrome(profile):
    """The User-Agent without "QtWebEngine" (some sites serve a degraded page when they see it); the engine's
    own, untouched, is kept for Google's sign-in (site_quirks.py)."""
    from . import site_quirks
    site_quirks.remember_engine_agent(profile.httpUserAgent())
    profile.setHttpUserAgent(re.sub(r"QtWebEngine/\S+\s*", "", profile.httpUserAgent()))


def harden(profile):
    """What a page may never do, in every profile (private ones too). A local .html file (a download,
    say) can't read other files on the Mac: Qt lets it fetch any file:// address by default, and send
    what it reads anywhere."""
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
    # Nor open a pop-up window by itself (Qt allows it by default): window.open() still works after a click,
    # for sign-in pop-ups, but a page can't bury you in windows from a timer
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
    # Nor open another app (zoommtg:, vscode:…) by itself: the browser's visible pages ask first (WebPage)
    profile.settings().setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)
    if not profile.scripts().find("eclipse-quirks"):
        from . import site_quirks
        quirks = QWebEngineScript()  # (Google's sign-in sees the engine as it is: site_quirks.py)
        quirks.setName("eclipse-quirks")
        quirks.setSourceCode(site_quirks.script_source())
        quirks.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        quirks.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        quirks.setRunsOnSubFrames(True)
        profile.scripts().insert(quirks)
    if not profile.scripts().find("eclipse-painted"):
        painted = QWebEngineScript()  # (each page says when it's about to draw its first frame)
        painted.setName("eclipse-painted")
        painted.setSourceCode(f"requestAnimationFrame(() => console.debug({PAINTED_CALL!r}));")
        painted.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        painted.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        painted.setRunsOnSubFrames(False)
        profile.scripts().insert(painted)
    if not profile.scripts().find("eclipse-link-menu"):
        link_menu = QWebEngineScript()  # (a right-clicked link isn't selected: LINK_MENU_SCRIPT)
        link_menu.setName("eclipse-link-menu")
        link_menu.setSourceCode(LINK_MENU_SCRIPT)
        link_menu.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        link_menu.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        link_menu.setRunsOnSubFrames(True)
        profile.scripts().insert(link_menu)


# A Mac's editing ways select what's right-clicked, before the menu: on a link (a search result, say) that's all its
# text, left highlighted. On a link, the selection goes back to what it was before the click (none, mostly), and the
# menu offers the link's own things; on plain text the word's still picked, as everywhere on a Mac
LINK_MENU_SCRIPT = """
(() => {
  let before = [];
  addEventListener("mousedown", e => {
    if (e.button !== 2 && !(e.button === 0 && e.ctrlKey)) return;
    const s = getSelection();
    before = Array.from({length: s.rangeCount}, (_, i) => s.getRangeAt(i).cloneRange());
  }, true);
  addEventListener("contextmenu", e => {
    const link = e.target instanceof Element ? e.target.closest("a[href]") : null;
    if (!link) return;
    const kept = before;
    setTimeout(() => {  // (the click selects as the event's done)
      const s = getSelection();
      if (s.isCollapsed || !link.contains(s.anchorNode)) return;  // (nothing selected there by the click)
      s.removeAllRanges();
      kept.forEach(r => s.addRange(r));
    });
  }, true);
})();
"""


# Content scripts and userscripts call the extension runtime with console messages starting with
# this (see ext/host.py); an invisible character, so it can't collide with a site's own logging
EXTENSION_CALL = "\u2063pbx:"
PASSWORD_CALL = "\u2063pbp:"
PAINTED_CALL = "\u2063pb-painted"  # (a page's about to draw its first frame: see MainWindow._dark_until_painted)  # (the password script's messages: they can hold a password, so never logged)
ADDRESS_CALL = "\u2063pba:"  # (the address script's: never logged either, they're about you)


class WebPage(QWebEnginePage):
    # Pages report back to the browser through console messages (used by Boosts' Zap tool)
    console_message = pyqtSignal(str)
    # Every console message, for Developer Mode: (level, message, line, source)
    console_entry = pyqtSignal(int, str, int, str)
    # Set by the extension runtime: (url, is_main_frame) -> allow? (catches .user.js installs)
    navigation_filter = None
    # Features' say over where any page goes, as it goes (Space rules' blocked sites): (page, url) -> allow?
    blockers = []
    # Set by the extension runtime: receives EXTENSION_CALL messages
    extension_messages = None
    # Set by passwords.py: receives PASSWORD_CALL messages (never shown in the console)
    password_messages = None
    # Set by addresses.py: receives ADDRESS_CALL messages (never shown in the console)
    address_messages = None
    # Set by the window: called when the page is about to draw its first frame (PAINTED_CALL)
    first_paint = None
    # Set by the window: url -> darken this page? (dark_pages.py), decided as each page starts loading
    dark_rule = None
    # Set by the window, for every page: (page, url, main_frame, redirect) when a page tries to open
    # another app's link
    external_link = None

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        # The profile refuses other apps' links outright; this page lets them through to
        # acceptNavigationRequest, which refuses them too, and asks you (only pages you see do this)
        self.settings().setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.AllowAllUnknownUrlSchemes)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme().lower() == OWN_SCHEME:
            return False  # (Eclipse's own links are for other apps, never a page: external_links.py)
        if is_external(url):
            # Another app's link: never opened from here; the window asks you (external_links.py)
            if WebPage.external_link is not None:
                WebPage.external_link(self, url, is_main_frame,
                                      nav_type == QWebEnginePage.NavigationType.NavigationTypeRedirect)
            return False
        if self.navigation_filter is not None and not self.navigation_filter(url, is_main_frame):
            return False
        if is_main_frame and not all(allow(self, url) for allow in list(WebPage.blockers)):
            return False
        if is_main_frame and self.dark_rule is not None:
            self.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, self.dark_rule(url))
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source):
        if message == PAINTED_CALL:
            if self.first_paint is not None:
                self.first_paint()
            return
        if message.startswith(PASSWORD_CALL):
            if self.password_messages is not None:
                try:
                    self.password_messages(message)
                except Exception:  # a page's message must never take the browser down
                    traceback.print_exc()
            return
        if message.startswith(ADDRESS_CALL):
            if self.address_messages is not None:
                try:
                    self.address_messages(message)
                except Exception:  # a page's message must never take the browser down
                    traceback.print_exc()
            return
        if message.startswith(EXTENSION_CALL):
            if self.extension_messages is not None:
                try:
                    self.extension_messages(message)
                except Exception:  # a page's message must never take the browser down
                    traceback.print_exc()
            return
        self.console_message.emit(message)
        self.console_entry.emit(level.value, message, line, source)


class WebView(QWebEngineView):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setPage(WebPage(profile, self))
        # Set by the window: (view, menu, request) adds extensions' items to the right-click menu
        self.context_menu_hook = None

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if self.context_menu_hook is not None:
            self.context_menu_hook(self, menu, self.lastContextMenuRequest())
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        at = event.globalPos()
        if event.reason() == QContextMenuEvent.Reason.Mouse and QApplication.mouseButtons() & Qt.MouseButton.RightButton:
            # A Mac asks for the menu as the button goes down. Shown then, the menu takes the mouse and the page never
            # sees the button come up: its drawing (Qt Quick) took the press as held and closed the menu at its
            # press-and-hold time (800 ms). Telling the page the button came up closed it straight away. So it opens
            # as the button comes up, the page having seen it
            self._menu_on_release = _OnRelease(menu, at)  # (kept: Python would let it go, and its watch with it)
        else:
            menu.popup(at)


class _OnRelease(QObject):
    """Shows `menu` at `at` once the right button comes up (or after WAIT_S, if that's missed)."""

    WAIT_S = 2.0

    def __init__(self, menu, at):
        super().__init__(menu)
        self.menu, self.at, self.done, self.released = menu, at, False, False
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self, singleShot=True, interval=int(self.WAIT_S * 1000))
        self.timer.timeout.connect(lambda: self.show(late=not self.released))
        self.timer.start()

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.RightButton):
            self.released = True
            self.timer.start(0)  # (once the page has had the release too)
        return False

    def show(self, late=False):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
        if late:
            held_open(self.menu)  # (the button may still be down: letting it go picks nothing)
        self.menu.popup(self.at)
