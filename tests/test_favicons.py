import functools
import http.server
import threading
import time

import pytest

from browser.favicons import Favicons, decode_icon, icon_candidates, is_private_host, redirect_ok

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#e11"/></svg>'


def test_candidates_best_first():
    html = """<html><head>
      <link rel="mask-icon" href="/mask.svg" color="#000">
      <link rel="icon" href="/small.png" sizes="16x16">
      <link rel="icon" href="/big.png" sizes="192x192">
      <link rel="apple-touch-icon" href="/touch.png">
      <link rel="shortcut icon" href="https://cdn.example/icon.svg">
    </head></html>"""
    assert icon_candidates(html, "https://example.com/start") == [
        "https://cdn.example/icon.svg", "https://example.com/big.png", "https://example.com/touch.png",
        "https://example.com/favicon.ico"]  # (a few at most: the small one isn't worth a fetch)
    assert icon_candidates("not html <<", "https://x.org/") == ["https://x.org/favicon.ico"]


def test_decode_takes_the_largest_and_draws_svg_at_size(qapp):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QImage
    image = decode_icon(SVG)
    assert image is not None and image.width() == 64 and image.pixelColor(32, 32).name() == "#ee1111"
    png = QImage(32, 32, QImage.Format.Format_ARGB32)
    png.fill(QColor("blue"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    png.save(buffer, "PNG")
    assert decode_icon(bytes(data)).width() == 32
    assert decode_icon(b"<html>not an image</html>") is None


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "with").mkdir()
    (tmp_path / "with" / "index.html").write_text('<link rel="icon" href="/with/logo.svg">')
    (tmp_path / "with" / "logo.svg").write_bytes(SVG)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield tmp_path, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _wait(qapp, condition, seconds=5):
    end = time.time() + seconds
    while time.time() < end and not condition():
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def test_fetches_a_sites_icon_once(qapp, data_dir, site):
    from browser import storage
    root, base = site
    fetcher = Favicons()
    got = []
    fetcher.fetched.connect(got.append)
    # (127.0.0.1's home page is the folder listing: no icon named, and no /favicon.ico either)
    fetcher.want([f"{base}/anything"])
    assert _wait(qapp, lambda: fetcher._running == 0 and not fetcher._queue)
    assert got == [] and storage.favicon(base) is None
    # A site whose home page names one
    (root / "index.html").write_text('<link rel="icon" href="/with/logo.svg">')
    fetcher._tried.clear()
    fetcher.want([f"{base}/page", f"{base}/other"])  # (one site: fetched once)
    assert _wait(qapp, lambda: got) and got == [f"{base}/"]
    icon = storage.favicon(base)
    assert icon is not None and icon.pixmap(64, 64).toImage().pixelColor(32, 32).name() == "#ee1111"
    fetcher.want([f"{base}/again"])  # (has one now: not fetched again)
    assert not fetcher._queue and fetcher._running == 0


def test_falls_back_to_favicon_ico(qapp, data_dir, site):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QImage
    from browser import storage
    root, base = site
    (root / "index.html").write_text("<p>no icon here</p>")
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor("green"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "ICO")
    (root / "favicon.ico").write_bytes(bytes(data))
    fetcher = Favicons()
    got = []
    fetcher.fetched.connect(got.append)
    fetcher.want([base + "/x"])
    assert _wait(qapp, lambda: got)
    assert storage.favicon(base) is not None


def test_a_malformed_link_is_skipped():
    assert icon_candidates('<link rel="icon" href="http://[oops/x.png"><link rel="icon" href="/ok.png">',
                           "https://a.org/") == ["https://a.org/ok.png", "https://a.org/favicon.ico"]


def test_huge_images_arent_decoded(qapp):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QImage
    big = QImage(2000, 2000, QImage.Format.Format_RGB32)
    big.fill(0)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    big.save(buffer, "PNG")
    assert decode_icon(bytes(data)) is None


def test_no_cookies_kept_or_sent(qapp):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtNetwork import QNetworkCookie
    fetcher = Favicons()
    jar = fetcher.network.cookieJar()
    url = QUrl("https://example.com/")
    assert jar.setCookiesFromUrl([QNetworkCookie(b"session", b"x")], url) is False
    assert jar.cookiesForUrl(url) == []


def test_a_redirect_to_another_site_doesnt_lend_its_icon(qapp, data_dir, site):
    from browser import storage
    root, base = site
    other = base.replace("127.0.0.1", "localhost")  # (the same server under another name: another site)
    (root / "away.html").write_text('<link rel="icon" href="/with/logo.svg">')
    # The home page redirects to the other site, whose page names an icon: that's not ours
    import http.server

    class Redirect(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"{other}/away.html")
            self.end_headers()

        def log_message(self, *a):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        home = f"http://127.0.0.1:{server.server_port}"
        fetcher = Favicons()
        fetcher.want([home + "/x"])
        assert _wait(qapp, lambda: fetcher._running == 0 and not fetcher._queue)
        assert storage.favicon(home) is None
    finally:
        server.shutdown()


def test_a_page_cant_send_us_everywhere():
    many = "".join(f'<link rel="icon" href="/i{n}.png">' for n in range(1000))
    assert len(icon_candidates(many, "https://example.com/")) == 4
    lan = """<link rel="icon" href="http://192.168.1.1/reboot" sizes="512x512">
             <link rel="icon" href="http://localhost:8080/x.png" sizes="256x256">
             <link rel="icon" href="http://nas.local/x.png" sizes="256x256">
             <link rel="icon" href="/ok.png">"""
    assert icon_candidates(lan, "https://example.com/") == ["https://example.com/ok.png", "https://example.com/favicon.ico"]
    # A site on your own network can name icons there
    assert "http://192.168.1.1/reboot" in icon_candidates(lan, "http://192.168.1.1/")
    assert is_private_host("10.0.0.2") and is_private_host("router") and is_private_host("[::1]")
    assert not is_private_host("github.com") and not is_private_host("8.8.8.8")


def test_a_wide_icon_keeps_its_shape(qapp):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QColor, QImage
    wide = QImage(200, 100, QImage.Format.Format_ARGB32)
    wide.fill(QColor("green"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    wide.save(buffer, "PNG")
    image = decode_icon(bytes(data))
    assert (image.width(), image.height()) == (64, 32)
    tall = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20"><rect width="10" height="20"/></svg>'
    image = decode_icon(tall)
    assert (image.width(), image.height()) == (32, 64)


def test_redirects_are_checked_like_the_first_address():
    assert redirect_ok("https://example.com/", "https://www.example.com/")
    assert not redirect_ok("https://example.com/", "http://example.com/")  # (never less safe)
    assert not redirect_ok("https://example.com/", "http://192.168.1.1/reboot")
    assert not redirect_ok("http://example.com/", "http://localhost:8080/x")
    assert redirect_ok("http://127.0.0.1:5000/", "http://127.0.0.1:5000/favicon.png")  # (a site that is local)
    assert not redirect_ok("https://example.com/", "file:///etc/passwd")


def test_a_section_with_its_own_icon_doesnt_change_the_sites(qapp, data_dir):
    from PyQt6.QtGui import QColor, QIcon, QPixmap
    from browser import storage

    def solid(color):
        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(color))
        return QIcon(pixmap)

    def colour(icon):
        return icon.pixmap(32, 32).toImage().pixelColor(16, 16).name()
    storage.save_favicon("https://www.google.com/", solid("#4285f4"))            # Google's G
    storage.save_favicon("https://www.google.com/maps/place/x", solid("#34a853"))  # Maps' pin
    assert colour(storage.favicon("https://www.google.com/")) == "#4285f4"       # (your Favorite keeps its G)
    assert colour(storage.favicon("https://www.google.com/maps/@1,2")) == "#34a853"
    assert colour(storage.favicon("https://www.google.com/search?q=x")) == "#4285f4"
    # Pages whose icon is the site's own don't each get a copy
    storage.save_favicon("https://www.google.com/search?q=y", solid("#4285f4"))
    assert not (data_dir / "favicons" / "google.com_search.png").exists()
    # The first page seen of a site stands in for it until its home page says otherwise
    storage.save_favicon("https://github.com/anthropics/claude-code", solid("#000000"))
    assert colour(storage.favicon("https://github.com/")) == "#000000"
    storage._icons.clear()  # (and it's all still there next launch)
    assert colour(storage.favicon("https://www.google.com/maps")) == "#34a853"


def test_each_subreddit_has_its_own_section():
    from browser.storage import _section_key
    assert _section_key("https://www.reddit.com/r/Python/comments/1") == "reddit.com/r/python"
    assert _section_key("https://www.google.com/maps/@1,2") == "google.com/maps"
    assert _section_key("https://example.com/") == "" and _section_key("https://example.com/?q=1") == ""
