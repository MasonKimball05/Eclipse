# Applies extensions' declarativeNetRequest rules (dnr.py) to real requests, and keeps each
# extension's rule state: which static rulesets are on, its dynamic rules (saved) and its
# session rules (forgotten on quit).
#
# The rule index is rebuilt on a background thread whenever anything changes (an ad blocker's
# 200,000 rules take a fraction of a second, which shouldn't freeze the window), then swapped in.

import re
import threading
import traceback
from collections import defaultdict

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInfo

from .. import storage
from . import dnr
from .patterns import matches

RT = QWebEngineUrlRequestInfo.ResourceType
TYPES = {
    RT.ResourceTypeMainFrame: "main_frame", RT.ResourceTypeNavigationPreloadMainFrame: "main_frame",
    RT.ResourceTypeSubFrame: "sub_frame", RT.ResourceTypeNavigationPreloadSubFrame: "sub_frame",
    RT.ResourceTypeStylesheet: "stylesheet", RT.ResourceTypeScript: "script", RT.ResourceTypeImage: "image",
    RT.ResourceTypeFavicon: "image", RT.ResourceTypeFontResource: "font", RT.ResourceTypeObject: "object",
    RT.ResourceTypePluginResource: "object", RT.ResourceTypeMedia: "media", RT.ResourceTypeXhr: "xmlhttprequest",
    RT.ResourceTypeJson: "xmlhttprequest", RT.ResourceTypePing: "ping", RT.ResourceTypeCspReport: "csp_report",
    RT.ResourceTypeWebSocket: "websocket",
}
NETWORK_SCHEMES = ("http", "https", "ws", "wss")
ENCODED = QUrl.ComponentFormattingOption.FullyEncoded


def encoded(url: QUrl) -> str:
    """The URL as Chrome matches rules against it: percent-encoded, with the host in punycode."""
    return bytes(url.toEncoded()).decode()


def ace_host(url: QUrl) -> str:
    return url.host(ENCODED).lower()
MAX_DYNAMIC_RULES = 30_000
MAX_SESSION_RULES = 5_000


class NetRules(QObject):
    matched = pyqtSignal()  # a rule acted on a request (for count badges)

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.index = dnr.RuleIndex()
        self._session: dict[str, list[dict]] = defaultdict(list)
        self._state: dict[str, dict] = {}
        self._access: dict[str, tuple] = {}  # ext → (host access needed for every action?, its host patterns)
        self._static_cache: dict = {}  # ruleset file → (mtime, rules), so a dynamic rule change doesn't re-read them
        self.counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))  # ext → site → blocked
        self.recent: dict[str, list[dict]] = defaultdict(list)  # ext → matched rules (getMatchedRules)
        self._generation = 0
        self._rebuild_timer = QTimer(self, singleShot=True, interval=150, timeout=self._rebuild)
        self._badge_timer = QTimer(self, singleShot=True, interval=500, timeout=self.matched.emit)

    # --- State --------------------------------------------------------------------------------

    def _file(self, ext_id):
        folder = storage.data_dir() / "extension_storage"
        folder.mkdir(exist_ok=True)
        return folder / f"{ext_id}.rules.json"

    def state(self, ext_id) -> dict:
        """{"dynamic": [rules], "enabled": [ruleset ids] or None (manifest default), "disabled": {ruleset: [ids]},
        "badge_counts": bool}"""
        if ext_id not in self._state:
            self._state[ext_id] = {"dynamic": [], "enabled": None, "disabled": {}, "badge_counts": False,
                                   **(storage.read_json(self._file(ext_id)) or {})}
        return self._state[ext_id]

    def _save(self, ext_id):
        storage.write_json(self._file(ext_id), self.state(ext_id))
        self.rebuild()

    def forget(self, ext_id):
        self._state.pop(ext_id, None)
        self._session.pop(ext_id, None)
        self._file(ext_id).unlink(missing_ok=True)
        self.rebuild()

    def rulesets(self, ext) -> list[dict]:
        return (ext.manifest.get("declarative_net_request") or {}).get("rule_resources") or []

    def enabled_rulesets(self, ext) -> list[str]:
        chosen = self.state(ext.id)["enabled"]
        if chosen is not None:
            return list(chosen)
        return [r["id"] for r in self.rulesets(ext) if r.get("enabled")]

    # --- Building the index ---------------------------------------------------------------------

    def rebuild(self):
        self._rebuild_timer.start()

    def _rebuild(self):
        # Gather what to load on this thread; parse and index on another
        jobs, access = [], {}
        for ext in self.host.extensions.values():
            # Only extensions that asked for declarativeNetRequest get their rules applied
            if not ext.enabled or not ext.has_permission("declarativeNetRequest", "declarativeNetRequestWithHostAccess"):
                continue
            access[ext.id] = (not ext.has_permission("declarativeNetRequest"), ext.host_patterns)
            state = self.state(ext.id)
            files = [(ext.path / r["path"].lstrip("/"), set(state["disabled"].get(r["id"], [])))
                     for r in self.rulesets(ext) if r["id"] in self.enabled_rulesets(ext)]
            jobs.append((ext.id, files, list(state["dynamic"]) + list(self._session.get(ext.id, []))))
        self._generation += 1
        generation = self._generation
        cache = self._static_cache

        def build():
            index = dnr.RuleIndex()
            for ext_id, files, extra in jobs:
                for path, disabled in files:
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if cache.get(path, (None,))[0] != mtime:
                        cache[path] = (mtime, dnr.load_ruleset(path))
                    rules = cache[path][1]
                    if disabled:
                        rules = [r for r in rules if not isinstance(r, dict) or r.get("id") not in disabled]
                    index.add(ext_id, rules)
                index.add(ext_id, extra)
            if generation == self._generation:  # a newer rebuild supersedes this one
                self._access = access
                self.index = index
        threading.Thread(target=build, name="dnr-index", daemon=True).start()

    # --- Deciding requests ----------------------------------------------------------------------

    def intercept(self, info) -> bool:
        """Apply the extensions' rules to a request, then tell webRequest listeners about it (unless
        it was blocked). True if a rule decided it (blocked, redirected or explicitly allowed), so
        the built-in blocker should leave it alone."""
        try:
            url = info.requestUrl()
            if url.scheme() not in NETWORK_SCHEMES:
                return False
            decided, blocked = self._apply(info, url)
            if not blocked and self.host.web_request_listeners:
                self.host.observe_request(info)
            return decided
        except Exception:  # one extension's odd rule must never take the browser down
            traceback.print_exc()
            return False

    def _permit(self, rule, request) -> bool:
        """Redirects and header changes (every action, for declarativeNetRequestWithHostAccess) only
        apply where the extension has host access to both the URL and whoever asked for it."""
        all_actions, patterns = self._access.get(rule.ext_id, (False, []))
        if not all_actions and rule.kind not in ("redirect", "modifyHeaders"):
            return True
        # Host permissions cover a site's WebSockets too, as Chrome does
        url = re.sub(r"^ws(s?)://", r"http\1://", request.url)
        return matches(url, patterns) and (not request.initiator_url or matches(request.initiator_url, patterns))

    def _apply(self, info, url) -> tuple[bool, bool]:
        """(decided, blocked)"""
        index = self.index
        if not index.count:
            return False, False
        page = info.firstPartyUrl()
        # The frame that asked for it (an iframe's site, say), falling back to the page
        origin = info.initiator() if info.initiator().host() else page
        request = dnr.Request(encoded(url), ace_host(url), ace_host(origin), TYPES.get(info.resourceType(), "other"),
                              bytes(info.requestMethod()).decode().lower(), initiator_url=encoded(origin))
        # Extensions whose allowAllRequests matched this page stay out of everything on it
        allowed_here = self._page_allowed(index, page) if request.type not in ("main_frame", "sub_frame") else frozenset()
        for rule in index.header_rules(request, self._permit, skip=allowed_here):
            for header in rule.action.get("requestHeaders", []):
                if isinstance(header, dict) and header.get("operation") in ("set", "append") and header.get("header"):
                    info.setHttpHeader(str(header["header"]).encode(), str(header.get("value", "")).encode())
        hit = index.match(request, permit=self._permit, skip=allowed_here)
        if hit is None:
            return bool(allowed_here), False
        rule, match = hit
        self._record(rule, request)
        kind = rule.kind
        if kind in ("allow", "allowAllRequests"):
            return True, False
        if kind == "block":
            info.block(True)
            return True, True
        target = dnr.redirect_target(rule, request, match, lambda ext_id, path: f"pbext://{ext_id}/{path.lstrip('/')}")
        # Compare encoded forms, so a rewrite that changes nothing never redirects to itself
        if target and QUrl(target).toEncoded() != url.toEncoded():
            info.redirect(QUrl(target))
            return True, False
        return False, False

    def _page_allowed(self, index, page: QUrl) -> frozenset:
        """allowAllRequests: the extensions whose rule matched the page itself, and so leave
        everything on it alone. Cached on the index, so a rebuild can never serve a stale answer."""
        key = encoded(page)
        cache = index.page_cache
        if key not in cache:
            if len(cache) > 500:
                cache.clear()
            request = dnr.Request(key, ace_host(page), "", "main_frame")
            cache[key] = frozenset(index.match_each(request, kinds={"allowAllRequests"}))
        return cache[key]

    def _record(self, rule, request):
        recent = self.recent[rule.ext_id]
        recent.append({"rule": {"ruleId": rule.id, "rulesetId": "_dynamic"}, "url": request.url,
                       "site": dnr.site_of(request.initiator) if request.initiator else ""})
        del recent[:-500]
        if rule.kind == "block" and request.initiator:
            self.counts[rule.ext_id][dnr.site_of(request.initiator)] += 1
            if self.state(rule.ext_id)["badge_counts"]:
                self._badge_timer.start()

    def count_for(self, ext_id, url) -> int:
        host = ace_host(QUrl(url))
        return self.counts[ext_id].get(dnr.site_of(host), 0) if host else 0

    # --- chrome.declarativeNetRequest calls (dispatched from host.py) ---------------------------

    @staticmethod
    def _update(rules, options, limit):
        remove = set(options.get("removeRuleIds") or [])
        rules = [r for r in rules if r.get("id") not in remove]
        add = options.get("addRules") or []
        if not isinstance(add, list):
            raise ValueError("addRules must be a list")
        taken = {r.get("id") for r in rules}
        for rule in add:
            problem = dnr.validate_rule(rule)  # rejected here, with a reason, so it never reaches a request
            if problem:
                raise ValueError(f"Rule with id {rule.get('id') if isinstance(rule, dict) else '?'}: {problem}.")
            if rule.get("id") in taken:
                raise ValueError(f"Rule with id {rule.get('id')} does not have a unique ID.")
            taken.add(rule.get("id"))
        rules += add
        if len(rules) > limit:
            raise ValueError("Rule count exceeds the limit.")
        return rules

    @staticmethod
    def _pick(rules, options):
        ids = (options or {}).get("ruleIds")
        return [r for r in rules if r.get("id") in ids] if ids else rules

    def updateDynamic(self, ext_id, options):
        state = self.state(ext_id)
        state["dynamic"] = self._update(state["dynamic"], options or {}, MAX_DYNAMIC_RULES)
        self._save(ext_id)

    def getDynamic(self, ext_id, options):
        return self._pick(self.state(ext_id)["dynamic"], options)

    def updateSession(self, ext_id, options):
        self._session[ext_id] = self._update(self._session[ext_id], options or {}, MAX_SESSION_RULES)
        self.rebuild()

    def getSession(self, ext_id, options):
        return self._pick(self._session[ext_id], options)

    def updateEnabledRulesets(self, ext_id, options):
        ext = self.host.extensions[ext_id]
        known = {r["id"] for r in self.rulesets(ext)}
        enabled = set(self.enabled_rulesets(ext))
        for ruleset in (options.get("disableRulesetIds") or []) + (options.get("enableRulesetIds") or []):
            if ruleset not in known:
                raise ValueError(f"Invalid ruleset id: {ruleset}.")
        enabled -= set(options.get("disableRulesetIds") or [])
        enabled |= set(options.get("enableRulesetIds") or [])
        self.state(ext_id)["enabled"] = sorted(enabled)
        self._save(ext_id)

    def getEnabledRulesets(self, ext_id):
        return self.enabled_rulesets(self.host.extensions[ext_id])

    def updateStatic(self, ext_id, options):
        disabled = self.state(ext_id)["disabled"]
        ruleset = options.get("rulesetId")
        ids = set(disabled.get(ruleset, [])) | set(options.get("disableRuleIds") or [])
        ids -= set(options.get("enableRuleIds") or [])
        disabled[ruleset] = sorted(ids)
        self._save(ext_id)

    def getDisabledRuleIds(self, ext_id, options):
        return self.state(ext_id)["disabled"].get((options or {}).get("rulesetId"), [])

    def setActionOptions(self, ext_id, options):
        if "displayActionCountAsBadgeText" in (options or {}):
            self.state(ext_id)["badge_counts"] = bool(options["displayActionCountAsBadgeText"])
            self._save(ext_id)
            self.matched.emit()

    def getMatchedRules(self, ext_id, options):
        url = None
        tab_id = (options or {}).get("tabId")
        if tab_id is not None:
            tab = self.host._tab_by_number(tab_id)
            url = tab.url if tab else ""
        site = dnr.site_of(ace_host(QUrl(url))) if url else None
        rules = [{"rule": m["rule"], "tabId": tab_id if tab_id is not None else -1, "timeStamp": 0}
                 for m in self.recent[ext_id] if site is None or m["site"] == site]
        return {"rulesMatchedInfo": rules}

    def isRegexSupported(self, ext_id, options):
        try:
            re.compile((options or {}).get("regex", ""))
            return {"isSupported": True}
        except re.error:
            return {"isSupported": False, "reason": "syntaxError"}

    def testMatchOutcome(self, ext_id, request):
        url, initiator = QUrl(request.get("url", "")), QUrl(request.get("initiator", ""))
        req = dnr.Request(encoded(url), ace_host(url), ace_host(initiator), request.get("type", "other"),
                          request.get("method", "get"), initiator_url=encoded(initiator))
        answer = self.index.match_each(req, permit=self._permit).get(ext_id)  # this extension's own answer
        return {"matchedRules": [{"ruleId": answer[0].id, "rulesetId": "_dynamic"}] if answer else []}
