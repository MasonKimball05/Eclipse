# Dark websites: pages that only come in light are darkened by Chromium itself (its "force dark":
# it recolours the page, keeping images as they are). Pages with a dark look of their own are left as
# they are. Off by default; on in Settings → General, and any site can be left light from the shield
# in the address bar.

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineSettings

SETTING = "dark_websites"
SKIP = "dark_websites_skip"  # sites left light


def site(url) -> str:
    host = QUrl(url).host() if not isinstance(url, QUrl) else url.host()
    return host.lower().removeprefix("www.")


def wanted(settings, url) -> bool:
    """Should this page be darkened?"""
    if not settings.get(SETTING, False):
        return False
    scheme = (url if isinstance(url, QUrl) else QUrl(url)).scheme()
    if scheme not in ("http", "https", "file"):
        return False  # (the browser's own pages, extensions: they have their own colours)
    host = site(url)
    return not any(host == s or host.endswith("." + s) for s in settings.get(SKIP, []))


def apply(page, settings, url=None):
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode,
                                 wanted(settings, url if url is not None else page.url()))


def set_skipped(settings, host, skipped):
    skip = settings.setdefault(SKIP, [])
    host = host.lower().removeprefix("www.")
    if skipped and host not in skip:
        skip.append(host)
    elif not skipped and host in skip:
        skip.remove(host)
