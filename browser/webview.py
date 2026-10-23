import re

from PyQt6.QtCore import QStandardPaths, pyqtSignal
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


class WebPage(QWebEnginePage):
    # Pages report back to the browser through console messages (used by Boosts' Zap tool)
    console_message = pyqtSignal(str)

    def javaScriptConsoleMessage(self, level, message, line, source):
        self.console_message.emit(message)


class WebView(QWebEngineView):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setPage(WebPage(profile, self))
