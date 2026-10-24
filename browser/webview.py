import re
import traceback

from PyQt6.QtCore import QStandardPaths, Qt, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from . import storage


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
    return profile


# Content scripts and userscripts call the extension runtime with console messages starting with
# this (see ext/host.py); an invisible character, so it can't collide with a site's own logging
EXTENSION_CALL = "\u2063pbx:"


class WebPage(QWebEnginePage):
    # Pages report back to the browser through console messages (used by Boosts' Zap tool)
    console_message = pyqtSignal(str)
    # Every console message, for Developer Mode: (level, message, line, source)
    console_entry = pyqtSignal(int, str, int, str)
    # Set by the extension runtime: (url, is_main_frame) -> allow? (catches .user.js installs)
    navigation_filter = None
    # Set by the extension runtime: receives EXTENSION_CALL messages
    extension_messages = None

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if self.navigation_filter is not None and not self.navigation_filter(url, is_main_frame):
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def javaScriptConsoleMessage(self, level, message, line, source):
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
