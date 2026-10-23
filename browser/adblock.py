# Built-in ad & tracker blocking (Qt 6.11 can't run extensions like uBlock yet).
#
# Three layers:
#   1. Network: third-party requests to domains on well-known block lists are cancelled
#      before they leave the browser. Requests to the site you're on are never blocked,
#      which is what keeps sites from breaking.
#   2. Cosmetic: a few conservative CSS rules hide the empty boxes ads leave behind.
#   3. YouTube: its ads come from the same servers as the videos, so no domain list can
#      stop them; a small script skips them instead (mutes, jumps to the end, clicks Skip).
#
# Block lists are downloaded in the background and refreshed weekly; a short built-in
# list covers the biggest ad networks until the first download finishes.

import time

from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor

from . import storage

BLOCK_LISTS = {
    # Peter Lowe's ad & tracking server list: small and careful about false positives
    "peter-lowe.txt": "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=nohtml&showintro=0&mimetype=plaintext",
    # StevenBlack's unified hosts (ads + malware), the list Pi-hole setups commonly use
    "stevenblack-hosts.txt": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
}
REFRESH_SECONDS = 7 * 24 * 3600

BUILTIN_DOMAINS = {
    "doubleclick.net", "googlesyndication.com", "googleadservices.com", "google-analytics.com",
    "googletagservices.com", "googletagmanager.com", "adservice.google.com", "amazon-adsystem.com",
    "adnxs.com", "criteo.com", "criteo.net", "taboola.com", "outbrain.com", "scorecardresearch.com",
    "quantserve.com", "moatads.com", "rubiconproject.com", "pubmatic.com", "openx.net",
    "casalemedia.com", "adsrvr.org", "advertising.com", "yieldmo.com", "sharethrough.com", "teads.tv",
    "smartadserver.com", "33across.com", "hotjar.com", "media.net", "bidswitch.net", "indexww.com",
    "lijit.com", "contextweb.com", "adform.net", "zemanta.com", "revcontent.com", "mgid.com",
}
_NOT_DOMAINS = {"localhost", "localhost.localdomain", "local", "broadcasthost", "0.0.0.0", "ip6-localhost"}

_SKIPPED_TYPES = {
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame,
    QWebEngineUrlRequestInfo.ResourceType.ResourceTypeNavigationPreloadMainFrame,
}

COSMETIC_CSS = (
    "ins.adsbygoogle, .adsbygoogle, amp-ad, amp-embed[type='taboola'], iframe[id^='google_ads_iframe'], "
    "div[id^='div-gpt-ad'], div[id^='taboola-'], .OUTBRAIN, [data-ad-slot], [data-google-query-id]"
    " { display: none !important; }"
)

YOUTUBE_SKIPPER = """
setInterval(() => {
  const player = document.querySelector('.html5-video-player');
  if (!player || !player.classList.contains('ad-showing')) return;
  const skip = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern');
  if (skip) { skip.click(); return; }
  const video = player.querySelector('video');
  if (video && isFinite(video.duration)) { video.muted = true; video.currentTime = video.duration; }
}, 300);
"""


def _site(host: str) -> str:
    """Rough "registrable domain": news.bbc.co.uk -> bbc.co.uk, cdn.example.com -> example.com."""
    labels = host.lower().removeprefix("www.").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3:  # co.uk, com.au, ...
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def should_block(host: str, first_party_host: str, domains: set[str], allowed_sites=()) -> bool:
    """Block third-party requests to listed domains (or their subdomains), unless the site is allowed."""
    host = host.lower()
    first_party = _site(first_party_host)
    if not host or _site(host) == first_party or first_party in allowed_sites:
        return False
    labels = host.split(".")
    return any(".".join(labels[i:]) in domains for i in range(len(labels) - 1))


def parse_list(text: str) -> set[str]:
    """Reads both plain domain lists and hosts files ("0.0.0.0 ads.example.com")."""
    domains = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        domain = parts[1] if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1", "::1", "::") else parts[0]
        domain = domain.lower().strip(".")
        if "." in domain and domain not in _NOT_DOMAINS:
            domains.add(domain)
    return domains


class AdBlocker(QWebEngineUrlRequestInterceptor):
    # A request was blocked on this site (the first-party host, without "www.")
    blocked = pyqtSignal(str)

    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.profiles = []
        self.domains: set[str] = set(BUILTIN_DOMAINS)
        self.counts: dict[str, int] = {}
        self._network = QNetworkAccessManager(self)
        self._load_cached_lists()
        QTimer.singleShot(5000, self.update_lists)

    def attach(self, profile):
        """Start blocking in a profile (every Space profile gets the same blocker)."""
        self.profiles.append(profile)
        profile.setUrlRequestInterceptor(self)
        self._install_scripts(profile)

    def detach_all(self):
        for profile in self.profiles:
            profile.setUrlRequestInterceptor(None)

    # --- Settings ---------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.store.settings.get("adblock_enabled", True)

    @property
    def allowed_sites(self) -> list[str]:
        return self.store.settings.setdefault("adblock_allowed_sites", [])

    def set_enabled(self, enabled):
        self.store.settings["adblock_enabled"] = enabled
        self.store.schedule_save()
        self.install_scripts()

    def is_allowed(self, host) -> bool:
        return _site(host) in self.allowed_sites

    def set_allowed(self, host, allowed):
        site = _site(host)
        sites = self.allowed_sites
        if allowed and site not in sites:
            sites.append(site)
        elif not allowed and site in sites:
            sites.remove(site)
        self.store.schedule_save()
        self.install_scripts()

    def count_for(self, url: str) -> int:
        return self.counts.get(_site(QUrl(url).host()), 0)

    # --- Blocking ---------------------------------------------------------------------

    def interceptRequest(self, info):
        if not self.enabled or info.resourceType() in _SKIPPED_TYPES:
            return
        first_party_host = info.firstPartyUrl().host()
        if should_block(info.requestUrl().host(), first_party_host, self.domains, self.allowed_sites):
            info.block(True)
            first_party = _site(first_party_host)
            self.counts[first_party] = self.counts.get(first_party, 0) + 1
            self.blocked.emit(first_party)

    def install_scripts(self):
        for profile in self.profiles:
            self._install_scripts(profile)

    def _install_scripts(self, profile):
        """(Re)install the page scripts, with the current on/off state and allowed sites baked in."""
        scripts = profile.scripts()
        for old in scripts.find("pybrowser-adblock"):
            scripts.remove(old)
        if not self.enabled:
            return
        allowed = repr(self.allowed_sites)
        source = f"""
        (() => {{
          const site = location.hostname.replace(/^www\\./, '');
          if ({allowed}.some(s => site === s || site.endsWith('.' + s))) return;
          const style = document.createElement('style');
          style.textContent = {COSMETIC_CSS!r};
          const add = () => (document.head || document.documentElement).appendChild(style);
          document.documentElement ? add() : document.addEventListener('DOMContentLoaded', add);
          if (/(^|\\.)youtube\\.com$/.test(location.hostname)) {{ {YOUTUBE_SKIPPER} }}
        }})();
        """
        script = QWebEngineScript()
        script.setName("pybrowser-adblock")
        script.setSourceCode(source)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)  # isolated from page scripts
        script.setRunsOnSubFrames(False)
        scripts.insert(script)

    # --- Block lists ------------------------------------------------------------------

    def _list_dir(self):
        folder = storage.data_dir() / "blocklists"
        folder.mkdir(exist_ok=True)
        return folder

    def _load_cached_lists(self):
        domains = set(BUILTIN_DOMAINS)
        for name in BLOCK_LISTS:
            path = self._list_dir() / name
            if path.exists():
                domains |= parse_list(path.read_text(errors="replace"))
        self.domains = domains

    def update_lists(self, force=False):
        for name, url in BLOCK_LISTS.items():
            path = self._list_dir() / name
            if force or not path.exists() or time.time() - path.stat().st_mtime > REFRESH_SECONDS:
                reply = self._network.get(QNetworkRequest(QUrl(url)))
                reply.finished.connect(lambda reply=reply, path=path: self._list_downloaded(reply, path))

    def _list_downloaded(self, reply, path):
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            return  # keep the cached copy; we'll try again next launch
        text = bytes(reply.readAll()).decode(errors="replace")
        if len(parse_list(text)) < 500:
            return  # an error page, not a block list
        path.write_text(text)
        self._load_cached_lists()
