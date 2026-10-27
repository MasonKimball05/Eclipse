# Site icons for tabs that haven't loaded yet. An icon is otherwise only saved when its page loads and
# Chromium hands it over, so a pinned tab or Favorite you haven't opened since showed a letter instead.
# This reads the site's home page (no cookies, nothing else), takes the best icon it names (a touch
# icon, an SVG or the largest PNG, and /favicon.ico as the last resort), and keeps it where page-loaded
# icons go. Each site is tried once a session, a few at a time.

import ipaddress
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkCookieJar, QNetworkReply, QNetworkRequest

from . import storage

SIZE = 64             # kept at this size (sidebar rows show 16pt, Favorites 22pt: sharp on Retina)
MAX_SIDE = 1024       # a bigger image isn't decoded at all (a site could make us unpack hundreds of MB)
AT_ONCE = 4
TIMEOUT_MS = 10_000   # nothing arriving for this long ends a fetch…
TOTAL_MS = 20_000     # …and so does taking this long altogether (a byte every few seconds)
MAX_CANDIDATES = 4    # icons tried per site, /favicon.ico included (a page can name thousands)
MAX_PAGE = 512 * 1024  # an icon link is in the <head>: no need for more than this of a page


class _IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str, str, str]] = []  # (rel, href, sizes, type)

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            a = {k.lower(): (v or "") for k, v in attrs}
            if "icon" in a.get("rel", "").lower() and a.get("href"):
                self.links.append((a["rel"].lower(), a["href"], a.get("sizes", ""), a.get("type", "")))


def is_private_host(host: str) -> bool:
    """This Mac, or something on your network (a router, a NAS): never fetched for another site's page."""
    host = host.lower().strip("[]").rstrip(".")
    if host in ("localhost", "") or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host  # (a bare name: something on the local network)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified


def redirect_ok(start: str, to: str) -> bool:
    """Whether a fetch for `start` may follow a redirect to `to`."""
    begin, end = urlsplit(start), urlsplit(to)
    if end.scheme not in ("http", "https") or (begin.scheme == "https" and end.scheme != "https"):
        return False
    return is_private_host(begin.hostname or "") or not is_private_host(end.hostname or "")


def icon_candidates(html: str, base: str) -> list[str]:
    """The icons a page names, best first: big enough to look sharp, and not a one-colour mask icon
    (Safari's pinned-tab outline), then the site's /favicon.ico. A few at most, and nothing on your own
    network unless the site itself is there."""
    local_ok = is_private_host(urlsplit(base).hostname or "")
    parser = _IconLinks()
    try:
        parser.feed(html)
    except Exception:  # (a page HTMLParser can't follow: whatever it found before then)
        pass

    def score(link):
        rel, href, sizes, kind = link
        if "mask-icon" in rel:
            return -1
        if href.lower().split("?")[0].endswith(".svg") or "svg" in kind:
            return 200  # (sharp at any size)
        best = 0
        for size in sizes.lower().split():
            if "x" in size and size.split("x")[0].isdigit():
                best = max(best, int(size.split("x")[0]))
        if not best:
            best = 180 if "apple-touch-icon" in rel else 32
        return best if best <= 256 else 256 - best / 100  # (huge ones are slow to fetch, for no gain)
    ranked = sorted((l for l in parser.links if score(l) >= 0), key=score, reverse=True)
    out = []
    for _rel, href, _sizes, _kind in ranked:
        try:
            url = urljoin(base, href.strip())
        except ValueError:  # (a malformed address, "http://[oops/x.png": skip it)
            continue
        if not url.startswith(("http://", "https://")) or url in out:
            continue
        if not local_ok and is_private_host(urlsplit(url).hostname or ""):
            continue  # (a page naming "http://192.168.1.1/reboot" as its icon)
        out.append(url)
    fallback = urljoin(base, "/favicon.ico")
    out = [url for url in out if url != fallback][:MAX_CANDIDATES - 1]
    return out + [fallback]


def decode_icon(data: bytes) -> QImage | None:
    """The largest image in `data` (an .ico holds several), at SIZE; None if it isn't an image."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    best = None
    for i in range(max(reader.imageCount(), 1)):
        if i and not reader.jumpToImage(i):
            break
        svg = reader.format() == b"svg"
        if not svg and max(reader.size().width(), reader.size().height()) > MAX_SIDE:
            continue
        if svg or max(reader.size().width(), reader.size().height()) > SIZE:
            # (an SVG drawn at the size we keep, so it's sharp; never stretched out of shape)
            size = reader.size() if reader.size().isValid() and not reader.size().isEmpty() else QSize(SIZE, SIZE)
            reader.setScaledSize(size.scaled(SIZE, SIZE, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if not image.isNull() and (best is None or image.width() > best.width()):
            best = image
    if best is None or best.width() < 8:
        return None
    return best


class _NoCookies(QNetworkCookieJar):
    def setCookiesFromUrl(self, cookies, url):
        return False

    def cookiesForUrl(self, url):
        return []


def same_site(a: str, b: str) -> bool:
    host = lambda url: QUrl(url).host().lower().removeprefix("www.")
    return host(a) == host(b)


class Favicons(QObject):
    """Fetches missing icons for the tabs it's given. fetched(host url) when one arrives."""
    fetched = pyqtSignal(str)

    def __init__(self, parent=None, user_agent=""):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.network.setCookieJar(_NoCookies(self.network))  # no sign-ins go along, and nothing's kept
        self.user_agent = user_agent
        self._tried: set[str] = set()
        self._queue: list[str] = []
        self._running = 0

    def want(self, urls):
        """Look for icons for these pages' sites, where there's none yet."""
        for url in urls:
            q = QUrl(url)
            if q.scheme() not in ("http", "https") or not q.host() or storage.favicon(url) is not None:
                continue
            home = f"{q.scheme()}://{q.host()}" + (f":{q.port()}" if q.port() != -1 else "") + "/"
            if home not in self._tried:
                self._tried.add(home)
                self._queue.append(home)
        self._next()

    def _next(self):
        while self._running < AT_ONCE and self._queue:
            home = self._queue.pop(0)
            self._running += 1
            self._get(home, MAX_PAGE, lambda data, final, h=home: self._page(h, data, final),
                      lambda h=home: self._try(h, [h.rstrip("/") + "/favicon.ico"]), enough_is_fine=True)

    def _get(self, url, limit, done, fail, enough_is_fine=False):
        """`enough_is_fine`: stopping at `limit` still counts (the start of a page); otherwise a download
        cut short, by the limit or the clock, is a failure (half a picture isn't an icon)."""
        request = QNetworkRequest(QUrl(url))
        # Each redirect is checked like the first address: never onto your own network for a site that isn't
        # there, and never from https down to http
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.UserVerifiedRedirectPolicy)
        request.setTransferTimeout(TIMEOUT_MS)
        if self.user_agent:  # (some sites turn away anything that doesn't look like a browser)
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, self.user_agent)
        reply = self.network.get(request)
        QTimer(reply, singleShot=True, interval=TOTAL_MS, timeout=reply.abort).start()
        reply.redirected.connect(lambda to: reply.redirectAllowed.emit() if redirect_ok(url, to.toString())
                                 else reply.abort())
        chunks = []
        enough = []

        def read():
            chunks.append(bytes(reply.readAll()))
            if sum(map(len, chunks)) >= limit:
                enough.append(True)
                reply.abort()  # (enough: "finished" follows)

        def finished():
            data = b"".join(chunks) + (bytes(reply.readAll()) if reply.isOpen() else b"")  # (not once cut short)
            error = reply.error()
            ok = data and (error == QNetworkReply.NetworkError.NoError
                           or (enough_is_fine and enough and error == QNetworkReply.NetworkError.OperationCanceledError))
            final = reply.url().toString()
            reply.deleteLater()
            done(data, final) if ok else fail()
        reply.readyRead.connect(read)
        reply.finished.connect(finished)

    def _page(self, home, data, final):
        if final and not same_site(final, home):
            # Sent somewhere else (a sign-in page, say): its icons are that site's, not this one's
            self._try(home, [home.rstrip("/") + "/favicon.ico"])
            return
        try:
            candidates = icon_candidates(data[:MAX_PAGE].decode("utf-8", errors="replace"), final or home)
        except Exception:  # (whatever a page holds, the queue must keep moving)
            candidates = [home.rstrip("/") + "/favicon.ico"]
        self._try(home, candidates)

    def _try(self, home, candidates):
        """The first of `candidates` that's really an image is the site's icon."""
        if not candidates:
            self._finish()
            return
        first, rest = candidates[0], candidates[1:]

        def got(data, final):
            try:
                image = decode_icon(data) if same_site(final or first, first) else None
            except Exception:
                image = None
            if image is None:
                self._try(home, rest)
                return
            if storage.favicon(home) is not None:  # (the page has given its own while this was on its way)
                self._finish()
                return
            storage.save_favicon(home, QIcon(QPixmap.fromImage(image)))
            self.fetched.emit(home)
            self._finish()
        self._get(first, 2 * 1024 * 1024, got, lambda: self._try(home, rest))

    def _finish(self):
        self._running -= 1
        QTimer.singleShot(0, self._next)
