import json

from PyQt6.QtCore import QObject, QUrl

from browser.ext.chrome import ChromeExtension
from browser.ext.host import EXT, USERSCRIPT, ExtensionHost, PageBridge, WorldBridge
from browser.webview import EXTENSION_CALL


class FakeFrame:
    """One of a page's frames, with the runtime (answering to `name`) in each world it lists."""

    def __init__(self, url, name=None, children=(), main=False):
        self._url, self.name, self._children, self.main = QUrl(url), name, list(children), main
        self.ran = []

    def url(self):
        return self._url

    def isValid(self):
        return True

    def isMainFrame(self):
        return self.main

    def children(self):
        return self._children

    def runJavaScript(self, code, world=0, callback=None):
        self.ran.append((world, code))
        if callback is not None:  # the host asking "is this you?"
            callback(self.name is not None and f"__pbFrameIs({json.dumps(self.name)})" in code)


class FakePage(QObject):
    def __init__(self, url, frames=()):
        super().__init__()
        self._url = QUrl(url)
        self.ran = []
        self._main = FakeFrame(url, children=frames, main=True)

    def url(self):
        return self._url

    def mainFrame(self):
        return self._main

    def lifecycleState(self):
        from PyQt6.QtWebEngineCore import QWebEnginePage
        return QWebEnginePage.LifecycleState.Active

    def runJavaScript(self, code, world=0, callback=None):
        self.ran.append((world, code))


class FakeBlocker:
    extension_rules = None


class FakeBrowser(QObject):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.adblock = FakeBlocker()
        self.current_tab = None


def _host(store, extensions=()):
    host = ExtensionHost(FakeBrowser(store))
    for ext_id, manifest in extensions:
        host.extensions[ext_id] = ChromeExtension(ext_id, None, {"name": ext_id, "version": "1", **manifest})
    return host


def _message(token, method, args, call_id=1, frame="top", href="https://site.com/"):
    return EXTENSION_CALL + json.dumps([token, frame, call_id, method, json.dumps(args), href])


def _answer(page):
    """The last answer the host ran in a world, as {"value": …} or {"error": …}."""
    code = page.ran[-1][1]
    return json.loads(json.loads(code[code.rindex(", ") + 2:-1]))


def test_world_call_needs_the_right_token(store):
    host = _host(store)
    world, token = host.world(EXT, "demo")
    page = FakePage("https://site.com/")
    host.world_call(page, _message("not-the-token", "storage.set", ["demo", "local", {"a": 1}]))
    assert not page.ran and not [b for b in host.bridges if isinstance(b, WorldBridge)]
    host.world_call(page, _message(token, "storage.set", ["demo", "local", {"a": 1}]))
    assert host._area("demo", "local") == {"a": 1}
    assert page.ran[-1][0] == world  # the answer goes back into that world only


def test_forged_messages_never_raise(store):
    host = _host(store)
    page = FakePage("https://evil.example/")
    for body in ("[[],0,0,0,0]", '["external","top","x","external.sendMessage","[\\"a\\",1]"]',
                 '["external","top",1,"external.sendMessage","[[],1]"]', "[1,2,3]", "{}", "null", "not json",
                 '["tok","top",true,"m","[]"]'):
        host.world_call(page, EXTENSION_CALL + body)  # must simply be ignored
    assert not page.ran


def test_world_cannot_act_for_another_extension(store):
    host = _host(store)
    _, token = host.world(EXT, "demo")
    page = FakePage("https://site.com/")
    host.world_call(page, _message(token, "storage.get", ["other-extension", "local", None]))
    assert "PermissionError" in _answer(page)["error"] and host._ext_storage.get("other-extension") is None


def test_userscripts_and_extensions_never_share_a_world(store):
    host = _host(store)
    assert host.world(EXT, "same-id") != host.world(USERSCRIPT, "same-id")
    page = FakePage("https://site.com/")
    _, us_token = host.world(USERSCRIPT, "same-id")
    host.world_call(page, _message(us_token, "storage.get", ["same-id", "local", None]))
    assert "userscripts can only use GM_*" in _answer(page)["error"]
    _, ext_token = host.world(EXT, "same-id")
    host.world_call(page, _message(ext_token, "gm.setValue", ["same-id", "k", 1], call_id=2))
    assert "GM_* functions are for userscripts" in _answer(page)["error"]


def test_content_scripts_only_get_content_script_apis(store):
    host = _host(store)
    _, token = host.world(EXT, "demo")
    page = FakePage("https://site.com/")
    host.world_call(page, _message(token, "tabs.query", ["demo", {}]))
    assert "content scripts can't use tabs.query" in _answer(page)["error"]


def test_websites_get_nothing_from_the_page_bridge(store):
    host = _host(store)
    bridge = PageBridge(host, FakePage("https://evil.example/"))
    assert "PermissionError" in bridge.call("storage.get", json.dumps(["demo", "local", None]))
    ext_page = PageBridge(host, FakePage("pbext://demo/popup.html"))
    assert json.loads(ext_page.call("storage.get", json.dumps(["demo", "local", None]))) == {"value": {}}
    assert "PermissionError" in ext_page.call("storage.get", json.dumps(["other", "local", None]))


def test_extension_pages_only_hear_their_own_events(store):
    host = _host(store)
    mine, theirs = PageBridge(host, FakePage("pbext://demo/bg.html")), PageBridge(host, FakePage("pbext://spy/bg.html"))
    heard = {"demo": [], "spy": []}
    mine.event.connect(lambda kind, _p: heard["demo"].append(kind))
    theirs.event.connect(lambda kind, _p: heard["spy"].append(kind))
    host.bridges += [mine, theirs]
    host._storage_changed("demo", "local", {"secret": {"newValue": 1}})
    assert heard["demo"] == ["storage:demo", "storage.local:demo"] and heard["spy"] == []


def test_only_recipients_can_answer_a_message(store):
    host = _host(store)
    sender, receiver, outsider = (PageBridge(host, FakePage(f"pbext://{e}/p.html")) for e in ("demo", "demo", "spy"))
    host.bridges += [sender, receiver, outsider]
    reply_id = host._m_runtime_sendMessage(sender, "demo", "hi")
    host._m_runtime_reply(outsider, "spy", reply_id, "forged")
    host._m_runtime_noReply(outsider, "spy", reply_id)
    assert reply_id in host._pending  # still waiting for the real recipient
    host._m_runtime_reply(receiver, "demo", reply_id, "real")
    assert reply_id not in host._pending


def test_ports_only_admit_who_they_were_offered_to(store):
    host = _host(store)
    opener, receiver, outsider = (PageBridge(host, FakePage(f"pbext://{e}/p.html")) for e in ("demo", "demo", "spy"))
    host.bridges += [opener, receiver, outsider]
    got = []
    outsider.event.connect(lambda kind, payload: got.append(kind))
    port_id = host._m_port_open(opener, "demo", "chat", None)
    host._m_port_accept(outsider, "spy", port_id)        # wrong extension
    host._m_port_accept(outsider, "demo", port_id)       # never offered (and not its id anyway)
    host._m_port_post(opener, "demo", port_id, "secret")
    assert got == [] and host._ports[port_id]["others"] == []
    host._m_port_accept(receiver, "demo", port_id)
    assert host._ports[port_id]["others"] == [receiver]


def test_scripting_needs_host_access_and_never_targets_other_extensions(store):
    from browser.models import Tab
    host = _host(store, [("demo", {"host_permissions": ["https://allowed.example/*"]})])

    class View:
        def __init__(self, url):
            self._page = FakePage(url)

        def page(self):
            return self._page

    for url, ok in (("https://allowed.example/x", True), ("https://other.example/", False),
                    ("pbext://another-extension/popup.html", False)):
        tab = Tab(url)
        tab.view = View(url)
        store.add_tab(tab)
        number = host._number(tab)
        caller = PageBridge(host, FakePage("pbext://demo/bg.html"))
        try:
            host._m_scripting_execute(caller, "demo", number, "1")
            allowed = True
        except PermissionError:
            allowed = False
        assert allowed == ok, url


def test_each_owner_gets_its_own_world(store):
    host = _host(store)
    worlds = {host.world(EXT, name)[0] for name in ("a", "b", "c")}
    assert len(worlds) == 3 and min(worlds) >= 10
    assert host.world(EXT, "a") == host.world(EXT, "a")


# --- Frames: a content script in a subframe speaks for that frame, never for the page --------------

def _bank(store):
    """bank.com with a cross-origin ad and a same-site login frame, the demo extension's runtime in
    all three, and its background page listening."""
    from browser.models import Tab
    host = _host(store, [("demo", {})])
    ad = FakeFrame("https://ads.evil.example/slot?x=1", name="adname")
    login = FakeFrame("https://bank.com/login", name="loginname")
    page = FakePage("https://bank.com/account", frames=[ad, login])
    background = PageBridge(host, FakePage("pbext://demo/bg.html"))
    heard = []
    background.event.connect(lambda kind, payload: heard.append((kind, json.loads(payload))))
    host.bridges.append(background)

    class View:
        def page(self):
            return page

    tab = Tab("https://bank.com/account")
    tab.view = View()
    store.add_tab(tab)
    return host, page, ad, login, heard, host._number(tab)


def _say(host, page, frame, href, method, args, call_id=1):
    host.world_call(page, _message(host.world(EXT, "demo")[1], method, args, call_id, frame, href))


def _senders(heard):
    return [payload[1] for kind, payload in heard if kind == "message:demo"]


def test_a_subframe_reports_its_own_address_and_frame(store):
    host, page, ad, login, heard, _ = _bank(store)
    _say(host, page, "adname", "https://ads.evil.example/slot?x=1", "runtime.sendMessage", ["demo", "creds?"])
    _say(host, page, "top", "https://bank.com/account", "runtime.sendMessage", ["demo", "hi"])
    ad_sender, top_sender = _senders(heard)
    assert ad_sender["url"] == "https://ads.evil.example/slot?x=1"
    assert ad_sender["origin"] == "https://ads.evil.example" and ad_sender["frameId"] > 0
    assert top_sender["url"] == "https://bank.com/account" and top_sender["frameId"] == 0
    assert top_sender["origin"] == "https://bank.com"
    # Each frame keeps its number, and another extension's world in the same frame gets the same one
    _say(host, page, "loginname", "https://bank.com/login", "runtime.sendMessage", ["demo", "x"], call_id=2)
    login_sender = _senders(heard)[-1]
    assert login_sender["frameId"] not in (0, ad_sender["frameId"])
    assert host._frame_number(page, ad) == ad_sender["frameId"]


def test_a_subframe_cant_claim_the_pages_address(store):
    host, page, ad, login, heard, _ = _bank(store)
    _say(host, page, "adname", "https://bank.com/account", "runtime.sendMessage", ["demo", "creds?"])
    assert _senders(heard) == []
    assert "isn't at that address" in ad.ran[-1][1]  # refused, and only told in its own frame
    # Nor can it say it's the top frame: that's decided by window.top in the runtime, and Python
    # only ever answers "top" in the page's main frame
    assert not any("creds" in code for _, code in page.ran)


def test_a_subframe_nobody_answers_for_is_never_served(store):
    host, page, ad, login, heard, _ = _bank(store)
    _say(host, page, "made-up-name", "https://ads.evil.example/", "storage.set", ["demo", "local", {"a": 1}])
    assert host._ext_storage.get("demo") is None and _senders(heard) == []


def test_two_frames_with_one_name_are_both_refused(store):
    host, page, ad, login, heard, _ = _bank(store)
    login.name = "adname"
    _say(host, page, "adname", "https://bank.com/login", "runtime.sendMessage", ["demo", "creds?"])
    assert not [b for b in host.bridges if isinstance(b, WorldBridge)]


def test_tabs_send_message_with_a_frame_id_reaches_only_that_frame(store):
    host, page, ad, login, heard, tab_number = _bank(store)
    for frame, href in (("top", "https://bank.com/account"), ("adname", "https://ads.evil.example/slot?x=1"),
                        ("loginname", "https://bank.com/login")):
        _say(host, page, frame, href, "hello", ["demo"])
    login_id = host._frame_number(page, login)
    background = next(b for b in host.bridges if b.extension_id == "demo")

    def reached(secret):
        return {name for name, frame in (("top", page), ("ad", ad), ("login", login))
                if any(secret in code for _, code in frame.ran)}

    host._m_tabs_sendMessage(background, "demo", tab_number, "password-1", {"frameId": login_id})
    assert reached("password-1") == {"login"}
    host._m_tabs_sendMessage(background, "demo", tab_number, "password-2", {"frameId": 0})
    assert reached("password-2") == {"top"}
    host._m_tabs_sendMessage(background, "demo", tab_number, "everyone", {})  # Chrome: every frame
    assert reached("everyone") == {"top", "ad", "login"}
    host._m_port_open(background, "demo", "port-to-login", tab_number, login_id)
    assert reached("port-to-login") == {"login"}


def test_origins_are_what_the_page_sees():
    from browser.ext.host import _origin
    assert _origin(QUrl("https://bank.com:443/a?b#c")) == "https://bank.com"
    assert _origin(QUrl("http://127.0.0.1:8000/x")) == "http://127.0.0.1:8000"
    assert _origin(QUrl("blob:https://bank.com/1234")) == "https://bank.com"
    assert _origin(QUrl("https://user:pw@bücher.example/")) == "https://xn--bcher-kva.example"
    assert _origin(QUrl("data:text/html,hi")) == _origin(QUrl("about:blank")) == "null"


def test_tabs_on_removed_fires_once_per_tab(store):
    from browser.models import Tab
    host = _host(store)
    background = PageBridge(host, FakePage("pbext://demo/bg.html"))
    removed = []
    background.event.connect(lambda kind, payload: kind == "tabs.removed" and removed.append(json.loads(payload)[0]))
    host.bridges.append(background)
    tab = Tab("https://site.com/")
    store.add_tab(tab)
    number = host._number(tab)
    host.tab_removed(tab)
    host.tab_removed(tab)  # (closed, then its folder deleted, say)
    assert removed == [number] and host._number(tab) != number
