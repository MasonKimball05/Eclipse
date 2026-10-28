# The Python side of the extension runtime: keeps the installed userscripts and Chrome
# extensions, installs their scripts into every browser profile, and answers the calls
# their JavaScript makes (see runtime.js for the other side).
#
# Two kinds of caller, each tied to exactly one extension or userscript:
#   - Extension pages (pbext://<id>/…: popups, options, background) talk over a web channel
#     in their main world. They can only act for the extension in their URL.
#   - Content scripts and userscripts run in an isolated JavaScript world of their own (one
#     per extension or script), invisible to the page and to each other. Qt allows a web
#     channel in only one world per page, so these send their calls as console messages
#     stamped with a secret token for their world, and get answers by Python running code
#     back in that world. A page can't forge calls: it can't see any world's token.
#     With all_frames they also run in subframes (a cross-origin ad inside your bank, say), so
#     each world bridge is pinned to the frame it really runs in: the top frame is the page, and
#     a subframe is found by asking every frame's copy of that world which one holds its name.
#     Senders get that frame's address as the browser sees it, never the top page's.

import base64
import itertools
import json
import secrets
import shutil
import time
import traceback
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QFile, QIODevice, QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PyQt6.QtWidgets import QApplication

from .. import storage
from ..webview import EXTENSION_CALL
from . import chrome, native, userscripts
from .chrome import BACKGROUND_PAGE, ChromeExtension, ExtensionScheme
from .netrules import TYPES, NetRules
from .ui import add_extension_menu_items
from .patterns import match_pattern, matches

SCRIPT_PREFIX = "pbx-"
FIRST_WORLD, LAST_WORLD = 10, 250  # Qt allows world ids up to 256
MAIN = QWebEngineScript.ScriptWorldId.MainWorld
ISOLATED = QWebEngineScript.ScriptWorldId.ApplicationWorld
REPLY_TIMEOUT_MS = 10_000
# chrome.declarativeNetRequest calls, answered by NetRules
DNR_METHODS = {"updateDynamic", "getDynamic", "updateSession", "getSession", "updateEnabledRulesets",
               "getEnabledRulesets", "updateStatic", "getDisabledRuleIds", "setActionOptions", "getMatchedRules",
               "isRegexSupported", "testMatchOutcome"}
# What a content script may do (as in Chrome): storage, messaging and ports, nothing that reaches other tabs
CONTENT_SCRIPT_METHODS = ("storage.", "runtime.sendMessage", "runtime.reply", "runtime.noReply",
                          "runtime.openOptionsPage", "port.")
EXT, USERSCRIPT = "ext", "us"  # the two kinds of owner; they never share a world, a token or an API


def _runtime_source() -> tuple[str, str]:
    # Qt ships qwebchannel.js as a resource; both files are wrapped in one function so
    # nothing they define leaks into the page as a global
    qrc = QFile(":/qtwebchannel/qwebchannel.js")
    qrc.open(QIODevice.OpenModeFlag.ReadOnly)
    channel = bytes(qrc.readAll()).decode()
    runtime = (Path(__file__).with_name("runtime.js")).read_text(encoding="utf-8")
    return channel, runtime


def _origin(url: QUrl) -> str:
    """A URL's origin as a page sees it (location.origin): scheme://host[:port], or "null"."""
    scheme = url.scheme()
    if scheme == "blob":
        return _origin(QUrl(url.path()))
    if scheme == "file":
        return "file://"
    if scheme not in ("http", "https", "ws", "wss", "ftp", "pbext") or not url.host():
        return "null"  # data:, about: and the like have no origin of their own
    host = url.host(QUrl.ComponentFormattingOption.FullyEncoded)
    if ":" in host:
        host = f"[{host}]"  # an IPv6 address
    port = url.port()
    default = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}.get(scheme, -1)
    return f"{scheme}://{host}" + (f":{port}" if port not in (-1, default) else "")


def _subframes(page):
    """Every frame of a page but the top one, each with the frame it sits in."""
    stack = [(child, page.mainFrame()) for child in page.mainFrame().children()]
    while stack:
        frame, parent = stack.pop()
        stack.extend((child, frame) for child in frame.children())
        yield frame, parent


def _script(name, source, world, point=QWebEngineScript.InjectionPoint.DocumentCreation, subframes=True):
    script = QWebEngineScript()
    script.setName(SCRIPT_PREFIX + name)
    script.setSourceCode(source)
    script.setInjectionPoint(point)
    script.setWorldId(world)
    script.setRunsOnSubFrames(subframes)
    return script


class PageBridge(QObject):
    """One page's connection to the runtime."""
    event = pyqtSignal(str, str)  # (kind, JSON payload) → listeners in the page

    def __init__(self, host, page):
        super().__init__(page)
        self.host = host
        self.page = page

    @property
    def extension_id(self) -> str | None:
        url = self.page.url()
        return url.host() if url.scheme() == "pbext" else None

    @property
    def owner(self) -> str | None:
        """The extension or userscript this caller may act for (None: nobody, e.g. a website)."""
        return self.extension_id

    @property
    def kind(self) -> str | None:
        """EXT, USERSCRIPT, or None for a caller that may use no API at all."""
        return EXT if self.extension_id else None

    def send(self, kind, payload):
        if self.owner is None:
            return  # a website's own page: nothing of ours listens there
        # Events named for one extension ("storage:<id>") only go to that extension
        if ":" in kind and not kind.startswith("port") and kind.rsplit(":", 1)[1] != self.owner:
            return
        self.event.emit(kind, json.dumps(payload))

    @pyqtSlot(str, str, result=str)
    def call(self, method, args_json):
        try:
            value = self.host.dispatch(self, method, json.loads(args_json))
            return json.dumps({"value": value})
        except Exception as error:  # reported to the calling script as a rejected promise
            return json.dumps({"error": f"{type(error).__name__}: {error}"})


class WorldBridge(PageBridge):
    """One extension's (or userscript's) isolated world in one frame of a page."""

    def __init__(self, host, page, owner_kind, owner, world, frame):
        super().__init__(host, page)
        self._kind = owner_kind
        self._owner = owner
        self.world = world
        self.frame = frame  # "top", or the random name the runtime gave a subframe
        # Which of the page's frames this is (found by the host; see ExtensionHost._locate), its
        # parent (whose origin an about:blank frame shares), and chrome's frameId for it
        self.qframe = None
        self.parent = None
        self.frame_id = 0 if frame == "top" else None
        self.backlog = []  # a subframe's calls from before it was found

    @property
    def located(self) -> bool:
        return self.frame_id is not None

    @property
    def kind(self):
        return self._kind

    @property
    def extension_id(self):
        return None  # content scripts aren't extension pages (runtime.sendMessage skips them)

    @property
    def owner(self):
        return self._owner

    def _run(self, code):
        if self.frame == "top":
            self.page.runJavaScript(code, self.world)
            return
        if self.qframe is not None:
            self.qframe.runJavaScript(code, self.world)
            return
        # A subframe not found yet: every frame checks the name
        for frame, _parent in _subframes(self.page):
            frame.runJavaScript(code, self.world)

    def send(self, kind, payload):
        # Only what concerns this world: its extension's events, and replies to its own calls
        if not (kind.endswith(":" + self._owner) or kind in ("reply", "xhr") or kind.startswith("port")):
            return
        self._run(f"window.__pbEvent && window.__pbEvent({json.dumps(self.frame)}, {json.dumps(kind)}, "
                  f"{json.dumps(json.dumps(payload))})")

    def answer(self, call_id: int, raw):
        self._run(f"window.__pbReply && window.__pbReply({json.dumps(self.frame)}, {call_id}, {json.dumps(raw)})")


class ExternalBridge(PageBridge):
    """A website listed in an extension's externally_connectable, messaging it from its main world."""

    def __init__(self, host, page):
        super().__init__(host, page)
        self.calls: dict[int, int] = {}  # reply id → the page's call id

    @property
    def extension_id(self):
        return None

    @property
    def owner(self):
        return None  # can't use any chrome.* API, only message the extension

    @property
    def kind(self):
        return None

    def send(self, kind, payload):
        if kind == "reply" and payload["id"] in self.calls:
            self.page.runJavaScript(f"window.__pbExternalReply && window.__pbExternalReply("
                                    f"{self.calls.pop(payload['id'])}, {json.dumps(json.dumps(payload['value']))})", 0)


# The page side of externally_connectable: chrome.runtime.sendMessage(extensionId, message) on sites
# an extension allows. Calls travel like a world's (console messages), with "external" for a token;
# Python checks the page's address against the extension's list before delivering anything.
EXTERNAL_SHIM = """(() => {
  const allowed = Object.entries(%s)
    .filter(([id, patterns]) => patterns.some((p) => { try { return new RegExp(p).test(location.href); } catch (e) { return false; } }))
    .map(([id]) => id);
  if (!allowed.length) return;
  const log = console.debug.bind(console), stringify = JSON.stringify;
  const callbacks = {}, sent = {};
  let next = 0;
  const fixed = (name, fn) => Object.defineProperty(window, name, { value: fn, writable: false, configurable: false });
  fixed('__pbExternalReply', (id, raw) => { const fn = callbacks[id]; delete callbacks[id]; if (fn) fn(JSON.parse(raw)); });
  // Python asks this (top) frame to vouch for each call, so an iframe can't speak for the page
  fixed('__pbExternalVerify', (id, payload) => { const ok = sent[id] === payload; delete sent[id]; return ok; });
  const sendMessage = (extensionId, message, options, callback) => {
    if (typeof options === 'function') callback = options;
    return new Promise((resolve) => {
      const id = ++next;
      callbacks[id] = (value) => { value = value === null ? undefined : value; resolve(value); if (callback) callback(value); };
      const payload = stringify([extensionId, message]);
      sent[id] = payload;
      log('\\u2063pbx:' + stringify(['external', 'top', id, 'external.sendMessage', payload]));
    });
  };
  const runtime = { sendMessage, connect: () => { throw new Error('runtime.connect from websites is not supported'); } };
  window.chrome = window.chrome || {};
  try { window.chrome.runtime = Object.assign(window.chrome.runtime || {}, runtime); }
  catch (e) { Object.defineProperty(window.chrome, 'runtime', { value: runtime, configurable: true }); }
})();"""


# The Chrome Web Store only offers "Add to Chrome" in Chrome, so extension pages there get our own
# button. It lives in an isolated world (the store's scripts can't touch it) and asks the browser
# through a console message; installing always shows a confirmation first.
STORE_MESSAGE = "ECLIPSE_ADD_EXTENSION:"
STORE_BUTTON = """(() => {
  if (location.hostname !== 'chromewebstore.google.com') return;
  const button = document.createElement('button');
  button.textContent = 'Add to Eclipse';
  button.style.cssText = 'position:fixed;right:24px;bottom:24px;z-index:2147483647;padding:12px 20px;' +
    'border:0;border-radius:999px;background:#7c5cff;color:#fff;font:600 14px -apple-system,sans-serif;' +
    'box-shadow:0 8px 28px rgba(0,0,0,.25);cursor:pointer';
  button.onclick = () => console.log('%s' + location.href);  // with this session's secret
  // The store is a single-page app: show the button only on an extension's page
  const update = () => {
    const onDetail = /\\/detail\\/.*[a-p]{32}/.test(location.pathname);
    if (onDetail && !button.isConnected && document.body) document.body.appendChild(button);
    if (!onDetail) button.remove();
  };
  setInterval(update, 800);
  update();
})();"""


class ExtensionHost(QObject):
    badges_changed = pyqtSignal()
    changed = pyqtSignal()  # something was installed, removed, turned on or off

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.store = browser.store
        self.profiles = []
        self.bridges: list[PageBridge] = []
        self.scheme = ExtensionScheme(self)
        self.network = QNetworkAccessManager(self)
        self._channel_js, self._runtime_js = _runtime_source()

        self.scripts: dict[str, userscripts.Userscript] = {}
        self._gm_values: dict[str, dict] = {}
        for record in self.store.settings.get("userscripts", []):
            source_file = self._userscripts_dir() / f"{record['id']}.user.js"
            if source_file.exists():
                self.scripts[record["id"]] = userscripts.Userscript(**record, source=source_file.read_text())

        self.extensions: dict[str, ChromeExtension] = {}
        for record in self.store.settings.get("extensions", []):
            folder = chrome.extensions_dir() / record["id"]
            try:
                self.extensions[record["id"]] = ChromeExtension.load(record["id"], folder, record.get("enabled", True))
            except (OSError, ValueError):
                continue  # deleted or broken; dropped from the list on the next save
        self._ext_storage: dict[str, dict] = {}
        self._dirty_storage: set[str] = set()
        self._save_timer = QTimer(self, singleShot=True, interval=500, timeout=self._flush_storage)
        self._reinstall_timer = QTimer(self, singleShot=True, interval=400, timeout=self._reinstall_all)

        self._ids = itertools.count(1)
        self._pending: dict[int, dict] = {}   # message replies we're waiting for
        self._ports: dict[int, dict] = {}     # open runtime.connect ports
        self.menus: dict[str, list[dict]] = {}  # each extension's context menu items
        self._xhrs: dict[int, QNetworkReply] = {}
        self._tab_ids: dict[str, int] = {}     # our tab ids (strings) ↔ chrome's (numbers)
        self._tab_numbers = itertools.count(1)
        self._frames: dict = {}                # page → [(subframe, chrome's frameId)] in its document
        self._frame_numbers = itertools.count(1)  # never reused, so an old frameId can't reach a new frame
        self._refused_helpers: set[str] = set()
        self.web_request_listeners: dict[int, dict] = {}  # chrome.webRequest listeners (watch-only)
        self._request_ids = itertools.count(1)
        self._worlds: dict[tuple, tuple[int, str]] = {}  # (kind, id) → (world id, secret token)
        self._tokens: dict[str, tuple] = {}              # token → (kind, id)
        # The Web Store button proves it's ours with a secret the store's own scripts can't see
        self.store_prefix = f"{STORE_MESSAGE}{secrets.token_hex(12)}:"
        self.backgrounds: dict[str, QWebEnginePage] = {}
        self._just_installed: dict[str, dict] = {}  # extension → its onInstalled details

        # Extensions' blocking rules run inside the ad blocker's request interceptor
        self.net = NetRules(self)
        self.net.matched.connect(self.badges_changed)
        browser.adblock.extension_rules = self.net.intercept
        self.net.rebuild()

        # Background pages start a moment after the window is up, so they don't slow launch
        QTimer.singleShot(1500, self._start_backgrounds)

    # --- Profiles and pages ---------------------------------------------------------------------

    def attach_profile(self, profile):
        self.profiles.append(profile)
        profile.installUrlSchemeHandler(chrome.SCHEME, self.scheme)
        self._install(profile)

    def attach_page(self, page, extension_page=False):
        # extension_page: a popup or background page, which only ever shows pbext:// pages
        bridge = PageBridge(self, page)
        channel = QWebChannel(page)
        channel.registerObject("pbHost", bridge)
        # On websites the channel is parked in the application world, where none of our scripts run;
        # it answers nothing there anyway (dispatch only serves extension pages and our worlds)
        world = MAIN if extension_page else ISOLATED
        page.setWebChannel(channel, world.value)
        page.setProperty("pbx_world", world.value)
        if not extension_page:
            page.navigation_filter = lambda url, main: self._before_navigation(page, url, main)
            page.extension_messages = lambda message: self.world_call(page, message)
            # A new document starts fresh worlds (their runtimes say hello again)
            page.loadStarted.connect(lambda: self._drop_bridges(page, worlds_only=True))
        self.bridges.append(bridge)
        page.destroyed.connect(lambda: self._drop_bridges(page))
        return bridge

    def world(self, kind, owner) -> tuple[int, str]:
        """The isolated world (and its secret) for an extension's content scripts (kind EXT) or a
        userscript (USERSCRIPT). The kind is part of the key, so ids can't collide across kinds."""
        key = (kind, owner)
        if key not in self._worlds:
            world = FIRST_WORLD + len(self._worlds)
            if world > LAST_WORLD:
                raise RuntimeError("too many extensions and userscripts")
            token = secrets.token_hex(16)
            self._worlds[key] = (world, token)
            self._tokens[token] = key
        return self._worlds[key]

    def world_runtime(self, kind, owner) -> str:
        world, token = self.world(kind, owner)
        return (f"(() => {{\nconst PB_TOKEN = {json.dumps(token)}, PB_OWNER = {json.dumps(owner)};\n"
                f"{self._runtime_js}\n}})();")

    def world_call(self, page, message):
        """A call from a content script or userscript, sent as a console message. Anything a page
        could forge is checked here: this runs inside Qt, where an exception would end the app."""
        try:
            self._world_call(page, message)
        except Exception:  # never let a page's message take the browser down
            traceback.print_exc()

    def _world_call(self, page, message):
        try:
            token, frame, call_id, method, args_json, *rest = json.loads(message[len(EXTENSION_CALL):])
        except (ValueError, TypeError):
            return
        href = rest[0] if rest else None  # where the world's frame says it is (the external shim sends none)
        if not (isinstance(token, str) and isinstance(frame, str) and isinstance(method, str)
                and isinstance(args_json, str) and isinstance(call_id, int) and not isinstance(call_id, bool)):
            return
        if token == "external":
            self._external_call(page, call_id, method, args_json)
            return
        key = self._tokens.get(token)
        if key is None or not isinstance(href, str):
            return  # not from one of our worlds
        world = self._worlds[key][0]
        bridge = next((b for b in self.bridges if isinstance(b, WorldBridge) and b.page is page
                       and b.world == world and b.frame == frame), None)
        if bridge is None:
            bridge = WorldBridge(self, page, key[0], key[1], world, frame)
            self.bridges.append(bridge)
            if frame != "top":
                self._locate(bridge)
        if not bridge.located:
            bridge.backlog.append((call_id, method, args_json, href))  # answered once it's found
            return
        self._serve(bridge, call_id, method, args_json, href)

    def _serve(self, bridge, call_id, method, args_json, href):
        if self._honest(bridge, href):
            raw = bridge.call(method, args_json)
        else:  # its document has gone, or it isn't where it says it is
            raw = json.dumps({"error": "PermissionError: this frame isn't at that address"})
        if method != "hello":  # a runtime saying hello doesn't wait for an answer
            bridge.answer(call_id, raw)

    # Which frame a world is in ---------------------------------------------------------------------

    def _locate(self, bridge):
        """Find the subframe a world bridge lives in: each frame's copy of the world is asked whether
        the name is its own. Only the runtime there knows its name, and it answers from inside the
        frame, so the answer comes from the frame Qt ran it in, whatever the page claims."""
        ask = f"window.__pbFrameIs && window.__pbFrameIs({json.dumps(bridge.frame)})"
        for frame, parent in _subframes(bridge.page):
            frame.runJavaScript(ask, bridge.world, lambda yes, f=frame, p=parent: yes is True and self._found(bridge, f, p))
        QTimer.singleShot(REPLY_TIMEOUT_MS, lambda: bridge.located or bridge.backlog.clear())

    def _found(self, bridge, frame, parent):
        if bridge not in self.bridges or sip.isdeleted(bridge.page):
            return
        if bridge.qframe is not None:
            if not bridge.qframe == frame:  # two frames claim one name: trust neither
                self._forget_bridge(bridge)
            return
        bridge.qframe, bridge.parent = frame, parent
        bridge.frame_id = self._frame_number(bridge.page, frame)
        # An earlier document in the same frame and world has gone; its bridge goes with it
        for old in [b for b in self.bridges if isinstance(b, WorldBridge) and b is not bridge and b.page is bridge.page
                    and b.world == bridge.world and b.qframe is not None and b.qframe == frame]:
            self._forget_bridge(old)
        backlog, bridge.backlog = bridge.backlog, []
        for call in backlog:
            self._serve(bridge, *call)

    def _frame_number(self, page, frame) -> int:
        """chrome's frameId for a subframe: the same for every extension in that frame."""
        known = self._frames.setdefault(page, [])
        for other, number in known:
            if other == frame:
                return number
        number = next(self._frame_numbers)
        known.append((frame, number))
        return number

    def _where(self, bridge) -> tuple[QUrl, str] | None:
        """A world bridge's frame as the browser sees it: (its URL, its origin), or None once it's gone."""
        if bridge.frame == "top":
            return bridge.page.url(), _origin(bridge.page.url())
        frame = bridge.qframe
        if frame is None or not frame.isValid():
            return None
        url = frame.url()
        if url.scheme() == "about" and bridge.parent is not None and bridge.parent.isValid():
            return url, _origin(bridge.parent.url())  # about:blank and srcdoc frames share their parent's
        return url, _origin(url)

    def _honest(self, bridge, href) -> bool:
        """Whether a world's frame is where its runtime says (same origin; the path may have moved on)."""
        where = self._where(bridge)
        if where is None:
            return False
        url, origin = where
        reported = QUrl(href)
        if url.scheme() == "about":
            return reported.scheme() == "about"
        return _origin(reported) == origin

    def _before_navigation(self, page, url: QUrl, main_frame: bool) -> bool:
        if not main_frame:
            return True
        # Extension pages run their code in the main world, so the bridge has to move there
        world = MAIN if url.scheme() == "pbext" else ISOLATED
        channel = page.webChannel()
        if channel is not None and page.property("pbx_world") != world.value:
            page.setWebChannel(channel, world.value)
            page.setProperty("pbx_world", world.value)
        if url.scheme() in ("http", "https", "file") and url.path().endswith(".user.js"):
            QTimer.singleShot(0, lambda: self.browser.feature("extensions").installer.userscript_from_url(url.toString()))
            return False
        return True

    def _external_call(self, page, call_id, method, args_json):
        try:
            ext_id, message = json.loads(args_json)
        except (ValueError, TypeError):
            return
        if not isinstance(ext_id, str) or method != "external.sendMessage":
            return
        # Only the page itself (its top frame, where the shim lives) may speak for its address: an
        # iframe can log the same console message, but it can't make the top frame vouch for it
        verify = f"window.__pbExternalVerify && window.__pbExternalVerify({call_id}, {json.dumps(args_json)})"
        page.runJavaScript(verify, 0, lambda ok: ok is True and self._external_deliver(page, call_id, ext_id, message))

    def _external_deliver(self, page, call_id, ext_id, message):
        if sip.isdeleted(page):
            return
        ext = self.extensions.get(ext_id)
        bridge = next((b for b in self.bridges if isinstance(b, ExternalBridge) and b.page is page), None)
        if bridge is None:
            bridge = ExternalBridge(self, page)
            self.bridges.append(bridge)
        url = bytes(page.url().toEncoded()).decode()
        if ext is None or not ext.enabled or not matches(url, self._external_patterns(ext)):
            reply_id = -next(self._ids)
            bridge.calls[reply_id] = call_id
            bridge.send("reply", {"id": reply_id, "value": None})  # nobody's listening, as far as the page knows
            return
        recipients = [b for b in self.bridges if b.extension_id == ext_id]
        reply_id = self._deliver(bridge, recipients, ext_id, message, kind="messageExternal:")
        bridge.calls[reply_id] = call_id

    @staticmethod
    def _external_patterns(ext) -> list[str]:
        patterns = (ext.manifest.get("externally_connectable") or {}).get("matches") or []
        return [r for r in map(match_pattern, patterns) if r]

    def _drop_bridges(self, page, worlds_only=False):
        self._frames.pop(page, None)  # a new document has new frames
        for bridge in [b for b in self.bridges if b.page is page and (isinstance(b, (WorldBridge, ExternalBridge)) or not worlds_only)]:
            self._forget_bridge(bridge)

    def _forget_bridge(self, bridge):
        if bridge in self.bridges:
            self.bridges.remove(bridge)
        for port_id, port in list(self._ports.items()):  # its ports close with it
            port.get("offered", set()).discard(bridge)
            if port["opener"] is bridge or bridge in port["others"]:
                self._m_port_close(bridge, port["ext"], port_id)

    def _content_bridges(self, page, ext_id, frame_id=None) -> list[WorldBridge]:
        """An extension's content scripts in a page (one per frame they run in), or in just one frame."""
        if frame_id is not None and (not isinstance(frame_id, int) or isinstance(frame_id, bool)):
            raise TypeError("frameId must be a number")
        return [b for b in self.bridges if isinstance(b, WorldBridge) and b.page is page
                and b.kind == EXT and b.owner == ext_id and (frame_id is None or b.frame_id == frame_id)]

    @property
    def active(self) -> bool:
        return any(s.enabled for s in self.scripts.values()) or any(e.enabled for e in self.extensions.values())

    # --- Installing scripts into profiles --------------------------------------------------------

    def _install(self, profile):
        collection = profile.scripts()
        for script in collection.toList():
            if script.name().startswith(SCRIPT_PREFIX):
                collection.remove(script)
        collection.insert(_script("store-button", STORE_BUTTON % self.store_prefix, ISOLATED,
                                  QWebEngineScript.InjectionPoint.DocumentReady, subframes=False))
        if not self.active:
            return
        # Extension pages: the same runtime in their main world, plus window.chrome for that extension
        ext_page = f"""(() => {{
  if (location.protocol !== 'pbext:') return;
  {self._channel_js}
  {self._runtime_js}
  const request = new XMLHttpRequest();
  request.open('GET', '/__pb_meta.json', false);
  request.send();
  const api = window.__pbChrome(location.host, JSON.parse(request.responseText));
  try {{ Object.defineProperty(window, 'chrome', {{ value: api, configurable: true, writable: true }}); }}
  catch (e) {{ window.chrome = api; }}
  window.browser = api;
  // Extensions may fetch other sites without CORS; plain GETs try the page's own fetch first (it has
  // the browser's cookies), anything else, or a GET that CORS blocks, goes through Python
  const nativeFetch = window.fetch.bind(window);
  const viaPython = async (request) => {{
    const body = ['GET', 'HEAD'].includes(request.method) ? null : await request.text();
    const r = await window.__pb.request('net.fetch', location.host, {{
      method: request.method, url: request.url, headers: Object.fromEntries(request.headers), data: body }});
    if (r.type === 'error' || !r.status) throw new TypeError('Failed to fetch');
    const headers = new Headers();
    for (const line of (r.headers || '').split('\\r\\n')) {{
      const i = line.indexOf(':');
      if (i > 0) try {{ headers.append(line.slice(0, i).trim(), line.slice(i + 1).trim()); }} catch (e) {{}}
    }}
    const nullBody = [101, 204, 205, 304].includes(r.status) || request.method === 'HEAD';
    const response = new Response(nullBody ? null : window.__pb.decode(r.body), {{ status: r.status, statusText: r.statusText, headers }});
    Object.defineProperty(response, 'url', {{ value: r.finalUrl }});
    return response;
  }};
  window.fetch = async (input, init) => {{
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (!/^https?:$/.test(url.protocol) || url.origin === location.origin) return nativeFetch(input, init);
    if (request.method === 'GET') {{
      try {{ return await nativeFetch(input, init); }} catch (e) {{ /* blocked by CORS: retry below */ }}
    }}
    return viaPython(request);
  }};
}})();"""
        collection.insert(_script("ext-pages", ext_page, MAIN))
        # Websites an extension accepts messages from (externally_connectable) get chrome.runtime.sendMessage
        external = {e.id: self._external_patterns(e) for e in self.extensions.values() if e.enabled}
        external = {k: v for k, v in external.items() if v}
        if external:
            collection.insert(_script("external", EXTERNAL_SHIM % json.dumps(external), MAIN, subframes=False))
        # Each userscript that uses GM_* APIs, and each extension's content scripts, get a world
        # of their own, with the runtime installed first
        # (The runtime only goes into subframes when a script there could use it.)
        for script in self.scripts.values():
            if script.enabled:
                world = None
                if script.uses_gm:
                    world = self.world(USERSCRIPT, script.id)[0]
                    collection.insert(_script("runtime-us-" + script.id, self.world_runtime(USERSCRIPT, script.id),
                                              world, subframes=not script.noframes))
                collection.insert(userscripts.build_script(script, self._required_code(script),
                                                           self._values(script.id), world))
        for ext in self.extensions.values():
            if ext.enabled and ext.manifest.get("content_scripts"):
                world = self.world(EXT, ext.id)[0]
                all_frames = any(cs.get("all_frames") for cs in ext.manifest["content_scripts"])
                collection.insert(_script("runtime-ext-" + ext.id, self.world_runtime(EXT, ext.id), world,
                                          subframes=all_frames))
                for script in ext.content_scripts(world):
                    collection.insert(script)

    def _reinstall_all(self):
        for profile in self.profiles:
            self._install(profile)

    def reinstall(self):
        self._reinstall_timer.start()

    # --- Calls from pages ------------------------------------------------------------------------

    def dispatch(self, bridge: PageBridge, method: str, args: list):
        owner = args[0] if args else None
        kind = bridge.kind
        if kind is None or bridge.owner is None or owner != bridge.owner:
            raise PermissionError("this page can't act for that extension")
        if method != "hello":
            if kind == USERSCRIPT and not method.startswith("gm."):
                raise PermissionError("userscripts can only use GM_* functions")
            if kind == EXT and method.startswith("gm."):
                raise PermissionError("GM_* functions are for userscripts")
            if isinstance(bridge, WorldBridge) and kind == EXT and not method.startswith(CONTENT_SCRIPT_METHODS):
                raise PermissionError(f"content scripts can't use {method}")
        if method.startswith("dnr.") and method[4:] in DNR_METHODS:
            ext = self.extensions.get(owner)
            if ext is None or not ext.has_permission("declarativeNetRequest", "declarativeNetRequestWithHostAccess"):
                raise PermissionError("needs the declarativeNetRequest permission")
            return getattr(self.net, method[4:])(*args)
        handler = getattr(self, "_m_" + method.replace(".", "_"), None)
        if handler is None:
            raise NotImplementedError(method)
        return handler(bridge, *args)

    # chrome.storage ---------------------------------------------------------------------------

    def _storage_file(self, ext_id) -> Path:
        folder = storage.data_dir() / "extension_storage"
        folder.mkdir(exist_ok=True)
        return folder / f"{ext_id}.json"

    def _area(self, ext_id, area) -> dict:
        if ext_id not in self._ext_storage:
            data = storage.read_json(self._storage_file(ext_id)) or {}
            self._ext_storage[ext_id] = {"local": data.get("local", {}), "sync": data.get("sync", {}),
                                         "session": {}, "managed": {}}
        return self._ext_storage[ext_id][area if area in ("local", "sync", "session", "managed") else "local"]

    def _flush_storage(self):
        for ext_id in self._dirty_storage:
            data = self._ext_storage.get(ext_id, {})
            storage.write_json(self._storage_file(ext_id), {"local": data.get("local", {}),
                                                             "sync": data.get("sync", {})})
        self._dirty_storage.clear()

    def _storage_changed(self, ext_id, area, changes):
        if not changes:
            return
        if area in ("local", "sync"):
            self._dirty_storage.add(ext_id)
            self._save_timer.start()
        self.broadcast(f"storage:{ext_id}", [changes, area], owner=ext_id)
        self.broadcast(f"storage.{area}:{ext_id}", [changes], owner=ext_id)

    def _m_storage_get(self, bridge, ext_id, area, keys):
        data = self._area(ext_id, area)
        if keys is None:
            return dict(data)
        if isinstance(keys, str):
            keys = [keys]
        if isinstance(keys, dict):
            return {k: data.get(k, default) for k, default in keys.items()}
        return {k: data[k] for k in keys if k in data}

    def _m_storage_set(self, bridge, ext_id, area, items):
        if area == "managed":
            raise PermissionError("managed storage is read-only")
        data = self._area(ext_id, area)
        changes = {}
        for key, value in (items or {}).items():
            change = {"newValue": value}
            if key in data:
                change["oldValue"] = data[key]
            data[key] = value
            changes[key] = change
        self._storage_changed(ext_id, area, changes)

    def _m_storage_remove(self, bridge, ext_id, area, keys):
        data = self._area(ext_id, area)
        changes = {k: {"oldValue": data.pop(k)} for k in ([keys] if isinstance(keys, str) else keys) if k in data}
        self._storage_changed(ext_id, area, changes)

    def _m_storage_clear(self, bridge, ext_id, area):
        data = self._area(ext_id, area)
        changes = {k: {"oldValue": v} for k, v in data.items()}
        data.clear()
        self._storage_changed(ext_id, area, changes)

    # chrome.runtime messaging --------------------------------------------------------------------

    def broadcast(self, kind, payload, only_extension=None, owner=None):
        """Send an event to pages. only_extension: just that extension's own pages (not its content
        scripts). owner: everything of that extension's, content scripts included."""
        for bridge in list(self.bridges):
            if only_extension is not None and bridge.extension_id != only_extension:
                continue
            if owner is not None and (bridge.kind != EXT or bridge.owner != owner):
                continue
            bridge.send(kind, payload)

    def _sender(self, bridge, ext_id) -> dict:
        # A content script's own frame (never the top page's address for a subframe); extension
        # pages and websites speak from their top frame
        if isinstance(bridge, WorldBridge):
            url, origin = self._where(bridge) or (QUrl(), "null")
        else:
            url, origin = bridge.page.url(), _origin(bridge.page.url())
        frame_id = bridge.frame_id if isinstance(bridge, WorldBridge) else 0
        sender = {"url": url.toString(), "origin": origin, "frameId": frame_id}
        if not isinstance(bridge, ExternalBridge):
            sender["id"] = ext_id  # websites have no extension id
        tab = self._tab_of(bridge.page)
        if tab is not None:
            sender["tab"] = self.tab_info(tab)
        return sender

    def _deliver(self, origin, recipients, ext_id, message, kind="message:") -> int:
        reply_id = next(self._ids)
        if not recipients:
            QTimer.singleShot(0, lambda: self._finish(reply_id, None, origin))
            return reply_id
        self._pending[reply_id] = {"origin": origin, "waiting": len(recipients), "recipients": set(recipients)}
        sender = self._sender(origin, ext_id)
        for bridge in recipients:
            bridge.send(kind + ext_id, [message, sender, reply_id])
        QTimer.singleShot(REPLY_TIMEOUT_MS, lambda: self._finish(reply_id, None))
        return reply_id

    def _finish(self, reply_id, value, origin=None):
        pending = self._pending.pop(reply_id, None)
        origin = origin or (pending and pending["origin"])
        if origin is not None and origin in self.bridges:
            origin.send("reply", {"id": reply_id, "value": value})

    def _m_runtime_sendMessage(self, bridge, ext_id, message):
        # To the extension's own pages (background, popup, options), never back to the sender
        recipients = [b for b in self.bridges if b.extension_id == ext_id and b is not bridge]
        return self._deliver(bridge, recipients, ext_id, message)

    def _m_tabs_sendMessage(self, bridge, ext_id, tab_id, message, options=None):
        # Every frame's content script, as in Chrome, unless options.frameId picks one
        frame_id = (options or {}).get("frameId") if isinstance(options, dict) else None
        tab = self._tab_by_number(tab_id)
        targets = self._content_bridges(tab.view.page(), ext_id, frame_id) if tab and hasattr(tab.view, "page") else []
        return self._deliver(bridge, targets, ext_id, message)

    def _m_runtime_reply(self, bridge, ext_id, reply_id, value):
        # Only a page the message was delivered to may answer it
        pending = self._pending.get(reply_id)
        if pending is not None and bridge in pending.get("recipients", ()):
            self._finish(reply_id, value)

    def _m_runtime_noReply(self, bridge, ext_id, reply_id):
        pending = self._pending.get(reply_id)
        if pending is not None and bridge in pending.get("recipients", ()):
            pending["recipients"].discard(bridge)
            pending["waiting"] -= 1
            if pending["waiting"] <= 0:
                self._finish(reply_id, None)

    # Ports ----------------------------------------------------------------------------------------

    def _m_port_open(self, bridge, ext_id, name, tab_number, frame_id=None):
        if tab_number is None:
            recipients = [b for b in self.bridges if b.extension_id == ext_id and b is not bridge]
        else:  # tabs.connect: every frame's content script, or just frameId's
            tab = self._tab_by_number(tab_number)
            recipients = (self._content_bridges(tab.view.page(), ext_id, frame_id)
                          if tab and hasattr(tab.view, "page") else [])
        port_id = next(self._ids)
        self._ports[port_id] = {"ext": ext_id, "opener": bridge, "others": [], "waiting": len(recipients),
                                "queue": [], "offered": set(recipients)}
        sender = self._sender(bridge, ext_id)
        for recipient in recipients:
            recipient.send("connect:" + ext_id, [port_id, name, sender])
        if not recipients:
            QTimer.singleShot(0, lambda: self._close_port(port_id, [bridge]))
        return port_id

    def _port(self, port_id, ext_id):
        port = self._ports.get(port_id)
        return port if port is not None and port["ext"] == ext_id else None

    def _m_port_accept(self, bridge, ext_id, port_id):
        port = self._port(port_id, ext_id)
        if port is not None and bridge in port.get("offered", ()):  # only a page it was offered to
            port["offered"].discard(bridge)
            port["others"].append(bridge)
            for message in port["queue"]:  # sent by the opener before anyone had answered
                bridge.send(f"port:{port_id}", [message])
            port["queue"] = []

    def _m_port_reject(self, bridge, ext_id, port_id):
        port = self._port(port_id, ext_id)
        if port is not None and bridge in port.get("offered", ()):
            port["offered"].discard(bridge)
            port["waiting"] -= 1
            if port["waiting"] <= 0 and not port["others"]:
                self._close_port(port_id, [port["opener"]])

    def _m_port_post(self, bridge, ext_id, port_id, message):
        port = self._port(port_id, ext_id)
        if port is None or not (bridge is port["opener"] or bridge in port["others"]):
            return
        if port.get("native") is not None:
            if bridge is port["opener"]:
                port["native"].send(message)
            return
        if bridge is port["opener"]:
            if not port["others"]:
                port["queue"].append(message)
            for other in port["others"]:
                other.send(f"port:{port_id}", [message])
        else:
            port["opener"].send(f"port:{port_id}", [message])

    def _m_port_close(self, bridge, ext_id, port_id):
        port = self._port(port_id, ext_id)
        if port is None:
            return
        if bridge is port["opener"]:
            self._close_port(port_id, port["others"])
        elif bridge in port["others"]:
            port["others"].remove(bridge)
            if not port["others"]:
                self._close_port(port_id, [port["opener"]])

    def _close_port(self, port_id, notify, error=None):
        port = self._ports.pop(port_id, None)
        if port is None:
            return
        if port.get("native") is not None:
            port["native"].stop()
            port["native"].deleteLater()
        for bridge in notify:
            if bridge in self.bridges:
                bridge.send(f"port.disconnect:{port_id}", [error])  # error: becomes runtime.lastError

    # Native messaging (helper programs on this Mac; see native.py) -----------------------------

    def _native_allowed(self, ext_id, name) -> tuple[dict | None, str | None]:
        ext = self.extensions.get(ext_id)
        if ext is None or "nativeMessaging" not in ext.manifest.get("permissions", []):
            return None, native.FORBIDDEN
        return native.check_access(name, ext_id)

    def _m_native_connect(self, bridge, ext_id, name):
        port_id = next(self._ids)
        self._ports[port_id] = {"ext": ext_id, "opener": bridge, "others": [], "waiting": 0, "queue": [],
                                "native": None}
        manifest, error = self._native_allowed(ext_id, name)
        if error:
            QTimer.singleShot(0, lambda: self._close_port(port_id, [bridge], error))
            return port_id
        helper = native.NativePort(manifest, ext_id, self)
        self._ports[port_id]["native"] = helper
        helper.message.connect(lambda message: bridge.send(f"port:{port_id}", [message]))
        helper.closed.connect(lambda reason: self._native_closed(port_id, bridge, ext_id, reason))
        return port_id

    def _native_closed(self, port_id, bridge, ext_id, reason):
        if reason == native.REFUSED_BY_MACOS and ext_id not in self._refused_helpers:
            self._refused_helpers.add(ext_id)  # say it once; extensions often retry
            ext = self.extensions.get(ext_id)
            self.browser.toast(f"macOS won't start the helper app “{ext.name if ext else ext_id}” needs: "
                               "Apple only allows it for browsers that pass its signing rules")
        self._close_port(port_id, [bridge], reason or None)

    def _m_native_send(self, bridge, ext_id, name, message):
        """runtime.sendNativeMessage: start the helper, send one message, take its first answer."""
        reply_id = next(self._ids)
        self._pending[reply_id] = {"origin": bridge, "waiting": 1}
        manifest, error = self._native_allowed(ext_id, name)
        if error:
            QTimer.singleShot(0, lambda: self._finish(reply_id, {"__pbNativeError": error}))
            return reply_id
        helper = native.NativePort(manifest, ext_id, self)

        def answered(value):
            self._finish(reply_id, value)
            helper.stop()
            helper.deleteLater()
        helper.message.connect(answered)
        helper.closed.connect(lambda reason: self._finish(reply_id, {"__pbNativeError": reason or native.EXITED}))
        helper.send(message)
        return reply_id

    def _m_hello(self, bridge, owner):
        pass  # a world's runtime announcing itself, so messages can reach it before it calls anything

    def _m_runtime_openOptionsPage(self, bridge, ext_id):
        self.open_options(ext_id)

    # chrome.tabs ---------------------------------------------------------------------------------

    def _number(self, tab) -> int:
        if tab.id not in self._tab_ids:
            self._tab_ids[tab.id] = next(self._tab_numbers)
        return self._tab_ids[tab.id]

    def _tab_by_number(self, number):
        if number is None:
            return self.browser.current_tab
        tab_id = next((k for k, v in self._tab_ids.items() if v == number), None)
        return self.store.find_tab(tab_id) if tab_id else None

    def _tab_of(self, page):
        return next((t for t in self.store.all_tabs()
                     if t.view is not None and getattr(t.view, "page", None) and t.view.page() is page), None)

    def tab_info(self, tab) -> dict:
        page = tab.view.page() if tab.view is not None and hasattr(tab.view, "page") else None
        discarded = page is not None and page.lifecycleState() == QWebEnginePage.LifecycleState.Discarded
        return {"id": self._number(tab), "index": 0, "windowId": 1, "active": tab is self.browser.current_tab,
                "highlighted": tab is self.browser.current_tab, "pinned": False, "url": tab.url,
                "title": tab.title or tab.url, "favIconUrl": "", "status": "complete", "incognito": False,
                "audible": False, "discarded": discarded or page is None, "autoDiscardable": True,
                "mutedInfo": {"muted": False}, "groupId": -1}

    def _m_tabs_query(self, bridge, ext_id, info):
        tabs = [t for t in self.store.all_tabs() if not t.url.startswith("note:")]
        if info.get("active") or info.get("highlighted"):
            tabs = [t for t in tabs if t is self.browser.current_tab]
        if "url" in info:
            includes = [match_pattern(p) for p in ([info["url"]] if isinstance(info["url"], str) else info["url"])]
            tabs = [t for t in tabs if matches(t.url, [r for r in includes if r])]
        if info.get("title"):
            needle = info["title"].replace("*", "").lower()
            tabs = [t for t in tabs if needle in (t.title or "").lower()]
        return [dict(self.tab_info(t), index=i) for i, t in enumerate(tabs)]

    def _m_tabs_get(self, bridge, ext_id, number):
        tab = self._tab_by_number(number)
        if tab is None:
            raise LookupError(f"No tab with id: {number}")
        return self.tab_info(tab)

    def _m_tabs_current(self, bridge, ext_id):
        tab = self._tab_of(bridge.page)
        return self.tab_info(tab) if tab else None

    def _not_another_extension(self, url: QUrl, ext_id):
        if url.scheme() == "pbext" and url.host() != ext_id:
            raise PermissionError("can't open another extension's pages")
        if url.scheme() == "javascript":  # would run in that page without its host permission
            raise PermissionError("can't open javascript: URLs")

    def _m_tabs_create(self, bridge, ext_id, props):
        url = bridge.page.url().resolved(QUrl(props.get("url") or "")) if props.get("url") else QUrl()
        self._not_another_extension(url, ext_id)
        tab = self.browser.open_url(url.toString() or "about:blank", background=props.get("active") is False)
        return self.tab_info(tab)

    def _m_tabs_update(self, bridge, ext_id, number, props):
        tab = self._tab_by_number(number)
        if tab is None:
            raise LookupError(f"No tab with id: {number}")
        if props.get("url"):
            target = bridge.page.url().resolved(QUrl(props["url"]))
            self._not_another_extension(target, ext_id)
            view = self.browser._ensure_view(tab, load=False)
            view.load(target)
        if props.get("reload") and tab.view is not None:
            tab.view.reload()
        if props.get("active") or props.get("highlighted"):
            self.browser.activate_tab(tab)
        return self.tab_info(tab)

    def _m_tabs_remove(self, bridge, ext_id, numbers):
        for number in numbers:
            tab = self._tab_by_number(number)
            if tab is not None:
                self.browser.close_tab(tab)

    # Called by the window as tabs change
    def tab_updated(self, tab, **changes):
        if self._has_extension_pages():
            self.broadcast("tabs.updated", [self._number(tab), changes, self.tab_info(tab)])

    def tab_activated(self, tab):
        if self._has_extension_pages():
            self.broadcast("tabs.activated", [{"tabId": self._number(tab), "windowId": 1}])

    def tab_removed(self, tab):
        # Closed, archived or deleted: said once, and its number is never handed out again (as in Chrome)
        number = self._tab_ids.pop(tab.id, None)
        if number is not None and self._has_extension_pages():
            self.broadcast("tabs.removed", [number, {"windowId": 1, "isWindowClosing": False}])

    def _has_extension_pages(self) -> bool:
        return any(b.extension_id for b in self.bridges)

    # chrome.webRequest (watch only: Qt can't pause a request while an extension decides) ----------

    WEB_REQUEST_EVENTS = {"onBeforeRequest", "onBeforeSendHeaders", "onSendHeaders"}

    def _m_webRequest_listen(self, bridge, ext_id, event, rule_filter, extra):
        ext = self.extensions.get(ext_id)
        if ext is None or not ext.has_permission("webRequest"):
            raise PermissionError("needs the webRequest permission")
        if event not in self.WEB_REQUEST_EVENTS:
            return None  # the response-side events never fire here
        listener_id = next(self._ids)
        urls = [r for r in map(match_pattern, rule_filter.get("urls") or ["<all_urls>"]) if r]
        self.web_request_listeners[listener_id] = {
            "ext": ext_id, "event": event, "urls": urls, "types": set(rule_filter.get("types") or []),
            "tab": rule_filter.get("tabId"), "headers": "requestHeaders" in (extra or []),
            "body": "requestBody" in (extra or []), "bridge": bridge, "access": ext.host_patterns}
        return listener_id

    def _m_webRequest_unlisten(self, bridge, ext_id, listener_id):
        listener = self.web_request_listeners.get(listener_id)
        if listener and listener["ext"] == ext_id:
            del self.web_request_listeners[listener_id]

    def observe_request(self, info):
        """Called by the request interceptor for every request that wasn't blocked."""
        url = bytes(info.requestUrl().toEncoded()).decode()
        rtype = TYPES.get(info.resourceType(), "other")
        page = info.firstPartyUrl().toString()
        tab = self.browser.current_tab
        if tab is None or tab.url != page:
            tab = next((t for t in self.store.all_tabs() if t.url == page), None)
        tab_number = self._number(tab) if tab is not None else -1
        initiator = info.initiator()
        details = {"requestId": str(next(self._request_ids)), "url": url,
                   "method": bytes(info.requestMethod()).decode(), "type": rtype, "tabId": tab_number,
                   "frameId": 0 if rtype == "main_frame" else -1 if rtype == "sub_frame" else 0, "parentFrameId": -1,
                   "timeStamp": time.time() * 1000,
                   "initiator": initiator.toString() if initiator.isValid() and initiator.host() else None,
                   "documentUrl": page}
        for listener_id, listener in list(self.web_request_listeners.items()):
            if listener["bridge"] not in self.bridges:
                del self.web_request_listeners[listener_id]  # its page has gone
                continue
            if listener["types"] and rtype not in listener["types"]:
                continue
            if listener["tab"] is not None and listener["tab"] != tab_number:
                continue
            if not matches(url, listener["urls"]) or not matches(url, listener["access"]):
                continue  # only sites the extension asked for, and has host access to
            event = dict(details)
            if listener["headers"] and listener["event"] != "onBeforeRequest":
                event["requestHeaders"] = [{"name": bytes(k).decode(), "value": bytes(v).decode()}
                                           for k, v in info.httpHeaders().items()]
            listener["bridge"].send(f"webRequest:{listener['ext']}", [listener_id, event])

    # chrome.action, notifications ------------------------------------------------------------------

    def _m_action_setBadge(self, bridge, ext_id, details):
        ext = self.extensions.get(ext_id)
        text = str(details.get("text") or "")[:4]
        if ext is not None and ext.badge != text:
            ext.badge = text
            self.badges_changed.emit()

    def _m_action_setBadgeColor(self, bridge, ext_id, details):
        pass  # the badge always uses the theme's accent colour

    # chrome.contextMenus -----------------------------------------------------------------------

    def _menu_file(self, ext_id) -> Path:
        return self._storage_file(ext_id).with_suffix(".menus.json")

    def menu_items(self, ext_id) -> list[dict]:
        if ext_id not in self.menus:
            self.menus[ext_id] = (storage.read_json(self._menu_file(ext_id)) or {}).get("items", [])
        return self.menus[ext_id]

    def _save_menus(self, ext_id):
        storage.write_json(self._menu_file(ext_id), {"items": self.menu_items(ext_id)})

    def _m_menus_create(self, bridge, ext_id, props):
        items = [i for i in self.menu_items(ext_id) if i["id"] != props["id"]]  # re-creating replaces
        items.append(props)
        self.menus[ext_id] = items
        self._save_menus(ext_id)

    def _m_menus_update(self, bridge, ext_id, item_id, props):
        for item in self.menu_items(ext_id):
            if item["id"] == item_id:
                item.update(props)
        self._save_menus(ext_id)

    def _m_menus_remove(self, bridge, ext_id, item_id):
        doomed = {item_id}
        items = self.menu_items(ext_id)
        changed = True
        while changed:  # children go with their parent
            changed = False
            for item in items:
                if item.get("parentId") in doomed and item["id"] not in doomed:
                    doomed.add(item["id"])
                    changed = True
        self.menus[ext_id] = [i for i in items if i["id"] not in doomed]
        self._save_menus(ext_id)

    def _m_menus_removeAll(self, bridge, ext_id):
        self.menus[ext_id] = []
        self._save_menus(ext_id)

    def extend_context_menu(self, view, menu, request):
        """Add extensions' items to a page's right-click menu."""
        add_extension_menu_items(self, view, menu, request)

    def menu_clicked(self, ext_id, item, request, page, checked=None):
        url = page.url().toString()
        info = {"menuItemId": item["id"], "pageUrl": url, "frameUrl": url, "editable": request.isContentEditable()}
        if item.get("parentId") is not None:
            info["parentMenuItemId"] = item["parentId"]
        if request.selectedText():
            info["selectionText"] = request.selectedText()
        if request.linkUrl().isValid():
            info["linkUrl"] = request.linkUrl().toString()
        if request.mediaUrl().isValid():
            info["srcUrl"] = request.mediaUrl().toString()
            info["mediaType"] = {1: "image", 2: "video", 3: "audio"}.get(int(request.mediaType().value), "")
        if checked is not None:
            info["wasChecked"], info["checked"] = not checked, checked
            item["checked"] = checked
            self._save_menus(ext_id)
        tab = self._tab_of(page)
        self.broadcast("menus.clicked:" + ext_id, [info, self.tab_info(tab) if tab else None], only_extension=ext_id)

    def badge_text(self, ext, url="") -> str:
        """What the toolbar shows: the extension's own badge, or its block count if it asked for that."""
        if ext.badge or not self.net.state(ext.id)["badge_counts"]:
            return ext.badge
        count = self.net.count_for(ext.id, url)
        return str(count) if count else ""

    def _m_action_getBadge(self, bridge, ext_id):
        ext = self.extensions.get(ext_id)
        return ext.badge if ext else ""

    # chrome.scripting, offscreen, downloads ------------------------------------------------------------

    def _tab_page(self, number):
        tab = self._tab_by_number(number)
        if tab is None:
            raise LookupError(f"No tab with id: {number}")
        if not hasattr(tab.view, "page"):
            raise LookupError(f"Tab {number} isn't loaded (it's asleep, or hasn't been opened yet)")
        return tab.view.page()

    def _scriptable_page(self, ext_id, number):
        """A tab's page, if this extension may run code in it: a site it has host access to (or, with
        activeTab, the tab you're on), never another extension's page."""
        page = self._tab_page(number)
        ext = self.extensions.get(ext_id)
        url = page.url()
        if ext is None or url.scheme() == "pbext" and url.host() != ext_id:
            raise PermissionError("can't run code in another extension's pages")
        if url.scheme() in ("http", "https", "file"):
            spec = bytes(url.toEncoded()).decode()
            on_active_tab = ext.has_permission("activeTab") and self._tab_of(page) is self.browser.current_tab
            if not (ext.has_host_access(spec) or on_active_tab):
                raise PermissionError(f"no host permission for {url.host()}")
        elif url.scheme() != "pbext":
            raise PermissionError(f"can't run code in {url.scheme()}: pages")
        return page

    def _m_scripting_execute(self, bridge, ext_id, number, code, main_world=False, all_frames=False):
        # Runs where the extension's content scripts do (its world, given the runtime first if it
        # has no content scripts of its own), or in the page's own world if asked; the main frame's
        # result comes back as a reply
        page = self._scriptable_page(ext_id, number)
        reply_id = next(self._ids)
        self._pending[reply_id] = {"origin": bridge, "waiting": 1}
        world = 0 if main_world else self.world(EXT, ext_id)[0]
        if not main_world:
            code = f"{self.world_runtime(EXT, ext_id)}\n{code}"
        page.runJavaScript(code, world, lambda result: self._finish(reply_id, result))
        if all_frames:
            ext = self.extensions[ext_id]
            stack = list(page.mainFrame().children())
            while stack:
                frame = stack.pop()
                stack.extend(frame.children())
                spec = bytes(frame.url().toEncoded()).decode()
                if frame.url().scheme() in ("http", "https", "file") and ext.has_host_access(spec):
                    frame.runJavaScript(code, world)
        return reply_id

    def _m_scripting_css(self, bridge, ext_id, number, css, remove):
        source = f"""(() => {{
  const css = {json.dumps(css)}, id = {json.dumps("pbx-css-" + ext_id)};
  const old = [...document.querySelectorAll('style[data-pbx]')].find((s) => s.dataset.pbx === id && s.textContent === css);
  if ({json.dumps(bool(remove))}) {{ if (old) old.remove(); return; }}
  if (old) return;
  const style = document.createElement('style');
  style.dataset.pbx = id;
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);
}})();"""
        self._scriptable_page(ext_id, number).runJavaScript(source, self.world(EXT, ext_id)[0])

    def _m_offscreen_create(self, bridge, ext_id, url):
        key = f"{ext_id}#offscreen"
        if key in self.backgrounds:
            raise RuntimeError("Only a single offscreen document may be created.")
        target = QUrl(f"{chrome.SCHEME.decode()}://{ext_id}/").resolved(QUrl(url))
        if target.scheme() != chrome.SCHEME.decode() or target.host() != ext_id:
            raise PermissionError("an offscreen document must be one of the extension's own pages")
        page = QWebEnginePage(self.profiles[0], self)
        self.attach_page(page, extension_page=True)
        page.load(target)
        self.backgrounds[key] = page

    def _m_offscreen_close(self, bridge, ext_id):
        self.stop_background(f"{ext_id}#offscreen")

    def _m_offscreen_has(self, bridge, ext_id):
        return f"{ext_id}#offscreen" in self.backgrounds

    def _m_downloads_download(self, bridge, ext_id, options):
        # Goes through the browser's normal download handling (and its Downloads window)
        url = bridge.page.url().resolved(QUrl(options.get("url", "")))
        bridge.page.download(url, Path(options.get("filename") or "").name)
        return next(self._ids)

    def _m_notify(self, bridge, ext_id, title, message):
        self.browser.toast(f"{title} — {message}" if title and message else title or message)

    # --- GM_* (userscripts) -----------------------------------------------------------------------

    @staticmethod
    def _userscripts_dir() -> Path:
        folder = storage.data_dir() / "userscripts"
        folder.mkdir(exist_ok=True)
        return folder

    def _values(self, script_id) -> dict:
        if script_id not in self._gm_values:
            self._gm_values[script_id] = storage.read_json(self._userscripts_dir() / f"{script_id}.values.json") or {}
        return self._gm_values[script_id]

    def _save_values(self, script_id):
        storage.write_json(self._userscripts_dir() / f"{script_id}.values.json", self._values(script_id))
        self.reinstall()  # pages loaded from now on start with the new values

    def _required_code(self, script) -> str:
        file = self._userscripts_dir() / f"{script.id}.require.js"
        return file.read_text(encoding="utf-8", errors="replace") if file.exists() else ""

    def _m_gm_setValue(self, bridge, script_id, key, value):
        self._values(script_id)[key] = value
        self._save_values(script_id)

    def _m_gm_deleteValue(self, bridge, script_id, key):
        if self._values(script_id).pop(key, None) is not None:
            self._save_values(script_id)

    def _m_gm_openInTab(self, bridge, script_id, url, background):
        self.browser.open_url(url, background=bool(background))

    def _m_gm_setClipboard(self, bridge, script_id, text):
        QApplication.clipboard().setText(text)

    def _m_gm_notification(self, bridge, script_id, title, text):
        self.browser.toast(f"{title} — {text}" if text else title)

    def _m_gm_xhr(self, bridge, script_id, details):
        # GM_xmlhttpRequest exists to get around the same-origin rule, so it's made from Python. Only to the web:
        # Python could read file:// too, and a script that builds its address from the page would hand your files
        # to whatever page tricked it
        url = QUrl(details["url"])
        if url.scheme() not in ("http", "https"):
            raise PermissionError("only http and https addresses can be fetched")
        request = QNetworkRequest(url)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        for name, value in (details.get("headers") or {}).items():
            request.setRawHeader(name.encode(), str(value).encode())
        if details.get("timeout"):
            request.setTransferTimeout(int(details["timeout"]))
        data = details.get("data")
        reply = self.network.sendCustomRequest(request, details.get("method", "GET").upper().encode(),
                                               data.encode() if data is not None else b"")
        request_id = next(self._ids)
        self._xhrs[request_id] = reply
        reply.finished.connect(lambda: self._xhr_done(bridge, request_id, reply))
        return request_id

    def _m_net_fetch(self, bridge, ext_id, details):
        # Extension pages may fetch any site (Chrome lets extensions skip CORS for their hosts)
        if bridge.extension_id is None:
            raise PermissionError("only extension pages can fetch across sites")
        return self._m_gm_xhr(bridge, ext_id, details)

    def _xhr_done(self, bridge, request_id, reply):
        self._xhrs.pop(request_id, None)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
        failed = reply.error() != QNetworkReply.NetworkError.NoError and not status
        headers = "\r\n".join(f"{bytes(k).decode()}: {bytes(v).decode()}" for k, v in reply.rawHeaderPairs())
        if bridge in self.bridges:
            bridge.send("xhr", {
                "id": request_id, "type": "error" if failed else "load", "status": status,
                "statusText": reply.attribute(QNetworkRequest.Attribute.HttpReasonPhraseAttribute) or "",
                "finalUrl": reply.url().toString(), "headers": headers,
                "body": base64.b64encode(bytes(reply.readAll())).decode(),
            })
        reply.deleteLater()

    # --- Managing userscripts -----------------------------------------------------------------------

    def install_userscript(self, script: userscripts.Userscript, required_code=""):
        folder = self._userscripts_dir()
        (folder / f"{script.id}.user.js").write_text(script.source, encoding="utf-8")
        (folder / f"{script.id}.require.js").write_text(required_code, encoding="utf-8")
        self.scripts[script.id] = script
        self._save_records()

    def set_userscript_enabled(self, script_id, enabled):
        self.scripts[script_id].enabled = enabled
        self._save_records()

    def remove_userscript(self, script_id):
        self.scripts.pop(script_id, None)
        for suffix in (".user.js", ".require.js", ".values.json"):
            (self._userscripts_dir() / f"{script_id}{suffix}").unlink(missing_ok=True)
        self._gm_values.pop(script_id, None)
        self._save_records()

    # --- Managing extensions ------------------------------------------------------------------------

    def add_extension(self, ext_id, enabled=True):
        """Load an extension that's just been copied into the extensions folder (new, or an update)."""
        self.stop_background(ext_id)
        previous = self.extensions.get(ext_id)
        ext = ChromeExtension.load(ext_id, chrome.extensions_dir() / ext_id, enabled)
        self.extensions[ext_id] = ext
        # What chrome.runtime.onInstalled will say once its background page is running
        self._just_installed[ext_id] = ({"reason": "update", "previousVersion": previous.version}
                                        if previous else {"reason": "install"})
        self._save_records()
        self.start_background(ext)
        return ext

    def set_extension_enabled(self, ext_id, enabled):
        ext = self.extensions[ext_id]
        ext.enabled = enabled
        self._save_records()
        if enabled:
            self.start_background(ext)
        else:
            self.stop_background(ext_id)

    def remove_extension(self, ext_id):
        self.stop_background(ext_id)
        ext = self.extensions.pop(ext_id, None)
        self._save_records()
        if ext is not None:
            shutil.rmtree(ext.path, ignore_errors=True)
        self._storage_file(ext_id).unlink(missing_ok=True)
        self._menu_file(ext_id).unlink(missing_ok=True)
        self._ext_storage.pop(ext_id, None)
        self.menus.pop(ext_id, None)
        self.net.forget(ext_id)

    def open_options(self, ext_id):
        ext = self.extensions.get(ext_id)
        if ext is not None and ext.options_page:
            self.browser.open_url(ext.url(ext.options_page))

    def _save_records(self):
        self.store.settings["userscripts"] = [s.to_record() for s in self.scripts.values()]
        self.store.settings["extensions"] = [{"id": e.id, "enabled": e.enabled} for e in self.extensions.values()]
        self.store.schedule_save()
        self._reinstall_all()
        self.net.rebuild()
        self.changed.emit()
        self.badges_changed.emit()

    # --- Background pages ---------------------------------------------------------------------------

    def _start_backgrounds(self):
        for ext in self.extensions.values():
            if ext.enabled:
                self.start_background(ext)

    def start_background(self, ext):
        if not ext.background or ext.id in self.backgrounds or not self.profiles:
            return
        page = QWebEnginePage(self.profiles[0], self)
        self.attach_page(page, extension_page=True)
        if ext.id in self._just_installed:
            details = self._just_installed.pop(ext.id)
            page.loadFinished.connect(lambda ok, e=ext.id: QTimer.singleShot(
                300, lambda: self.broadcast("installed:" + e, [details], only_extension=e)))
        page.load(QUrl(ext.url(BACKGROUND_PAGE)))
        self.backgrounds[ext.id] = page

    def stop_background(self, ext_id):
        for key in (ext_id, f"{ext_id}#offscreen"):
            page = self.backgrounds.pop(key, None)
            if page is not None:
                page.deleteLater()

    def shutdown(self):
        self._flush_storage()
        for port_id in [i for i, p in self._ports.items() if p.get("native") is not None]:
            self._close_port(port_id, [])  # helper programs end with the browser
        for reply in list(self._xhrs.values()):
            reply.abort()
        for ext_id in list(self.backgrounds):
            page = self.backgrounds.pop(ext_id)
            if not sip.isdeleted(page):
                sip.delete(page)  # before the profile goes, or Qt complains
