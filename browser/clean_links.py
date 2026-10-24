# Clean links: the tracking tags that ride along on shared links (utm_source, fbclid, gclid, …) are
# taken off before a page opens, and links you copy are copied without them. Nothing else in the
# address changes, so the page is the same page.
#
# Only parameters that exist purely for tracking are removed: a site never needs them to show you
# the page. The "si" share tag is removed only on the sites known to use it that way (YouTube,
# Spotify), since elsewhere "si" can mean something real.

import threading
import time
from urllib.parse import unquote_plus, urlsplit, urlunsplit

SETTING = "clean_links"

TRACKING = {
    "fbclid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid", "ttclid",
    "igshid", "igsh", "mc_cid", "mc_eid", "mkt_tok", "_hsenc", "_hsmi", "__hssc", "__hstc", "__hsfp",
    "hsctatracking", "oly_anon_id", "oly_enc_id", "vero_id", "vero_conv", "_openstat", "rb_clickid",
    "s_kwcid", "ef_id", "srsltid", "ref_src", "ref_url", "wt_mc", "wt.mc_id", "spm",
}
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_")
# Names that are tracking tags only on these sites
SITE_TRACKING = {
    "youtube.com": {"si", "pp"}, "youtu.be": {"si"}, "music.youtube.com": {"si"},
    "open.spotify.com": {"si"}, "x.com": {"s", "t"}, "twitter.com": {"s", "t"},
    "instagram.com": {"igsh"}, "linkedin.com": {"trk", "trackingid", "lipi"},
    "amazon.com": {"pd_rd_r", "pd_rd_w", "pd_rd_wg", "pf_rd_p", "pf_rd_r", "pd_rd_i", "content-id"},
    "reddit.com": {"share_id", "rdt"},
}


def _site_params(host: str) -> set:
    host = host.lower().removeprefix("www.").removeprefix("m.")
    for site, names in SITE_TRACKING.items():
        if host == site or host.endswith("." + site):
            return names
    return set()


def is_tracking(name: str, host: str = "") -> bool:
    key = name.lower()
    return key in TRACKING or key.startswith(TRACKING_PREFIXES) or key in _site_params(host)


def clean(url: str) -> str:
    """The same address without its tracking parameters (unchanged if it has none)."""
    if "?" not in url or not url.startswith(("http://", "https://")):
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = parts.hostname or ""
    # Cut the tracking pieces out of the query as written, so everything else keeps its exact spelling
    pieces = parts.query.split("&")
    kept = [piece for piece in pieces if piece and not is_tracking(unquote_plus(piece.split("=", 1)[0]), host)]
    if len(kept) == len([piece for piece in pieces if piece]):
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))


def for_copy(url: str, settings: dict) -> str:
    """A link as it should go on the clipboard."""
    return clean(url) if settings.get(SETTING, True) else url


class Cleaner:
    """For the request interceptor (it runs off the main thread): the cleaned address for a page about
    to open, or None to leave it alone. A site that keeps putting the tags back (a redirect that adds
    them again) is let through rather than bounced back and forth."""

    def __init__(self):
        self._recent: dict[str, float] = {}
        self._lock = threading.Lock()

    def redirect_for(self, url: str) -> str | None:
        cleaned = clean(url)
        if cleaned == url:
            return None
        now = time.monotonic()
        with self._lock:
            if len(self._recent) > 200:
                self._recent = {u: t for u, t in self._recent.items() if now - t < 10}
            if now - self._recent.get(cleaned, -100) < 5:
                return None  # cleaned this one a moment ago and it came straight back
            self._recent[cleaned] = now
        return cleaned

