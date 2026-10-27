import re
import traceback

from PyQt6.QtCore import QStandardPaths, Qt, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from . import storage
from .external_links import is_external


DEFAULT_PROFILE = "default"


def build_profile(parent, name=DEFAULT_PROFILE) -> QWebEngineProfile:
    # A named profile keeps cookies, logins and cache on disk between launches.
    # Spaces can each use their own profile, so they're signed in to different accounts.
    folder = storage.profile_dir(name)
    profile = QWebEngineProfile(name, parent)
    profile.setPersistentStoragePath(str(folder))
    profile.setCachePath(str(folder / "cache"))
    profile.setDownloadPath(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))
    # Some sites serve a degraded page when they see "QtWebEngine" in the user agent
    profile.setHttpUserAgent(re.sub(r"QtWebEngine/\S+\s*", "", profile.httpUserAgent()))

    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
    harden(profile)
    return profile


def harden(profile):
    """What a page may never do, in every profile (private ones too). A local .html file (a download,
    say) can't read other files on the Mac: Qt lets it fetch any file:// address by default, and send
    what it reads anywhere."""
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
    # Nor open another app (zoommtg:, vscode:…) by itself: the browser's visible pages ask first (WebPage)
    profile.settings().setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)


# Content scripts and userscripts call the extension runtime with console messages starting with
# this (see ext/host.py); an invisible character, so it can't collide with a site's own logging
EXTENSION_CALL = "\u2063pbx:"
PASSWORD_CALL = "\u2063pbp:"  # (the password script's messages: they can hold a password, so never logged)
ADDRESS_CALL = "\u2063pba:"  # (the address script's: never logged either, they're about you)


class WebPage(QWebEnginePage):
    # Pages report back to the browser through console messages (used by Boosts' Zap tool)
    console_message = pyqtSignal(str)
    # Every console message, for Developer Mode: (level, message, line, source)
    console_entry = pyqtSignal(int, str, int, str)
    # Set by the extension runtime: (url, is_main_frame) -> allow? (catches .user.js installs)
    navigation_filter = None
    # Set by the extension runtime: receives EXTENSION_CALL messages
    extension_messages = None
    # Set by passwords.py: receives PASSWORD_CALL messages (never shown in the console)
    password_messages = None
    # Set by addresses.py: receives ADDRESS_CALL messages (never shown in the console)
    address_messages = None
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
        if is_external(url):
            # Another app's link: never opened from here; the window asks you (external_links.py)
            if WebPage.external_link is not None:
                WebPage.external_link(self, url, is_main_frame,
                                      nav_type == QWebEnginePage.NavigationType.NavigationTypeRedirect)
            return False
        if self.navigation_filter is not None and not self.navigation_filter(url, is_main_frame):
            return False
        if is_main_frame and self.dark_rule is not None:
            self.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, self.dark_rule(url))
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source):
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
        menu.popup(event.globalPos())
