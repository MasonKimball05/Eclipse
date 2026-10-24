# chrome.declarativeNetRequest: an extension's blocking/redirect rules, applied by the
# browser's request interceptor (adblock.py) rather than by the extension's own code.
#
# Ad blockers ship hundreds of thousands of rules, far too many to test one by one on every
# request. Like uBlock and adblock-rust, rules are indexed:
#   - by domain, for "||example.com^" filters and requestDomains (most rules), and
#   - by a distinctive word of the filter ("consent" in "-consent-banner-"),
# so each request only checks the few rules that could possibly match it.
#
# As in Chrome, priorities only compete within one extension: each extension's best rule is its
# answer, and then across extensions a block beats a redirect (newest extension first), which
# beats an allow. URLs are matched in their encoded form, with hosts in punycode.
#
# Supported actions: block, allow, allowAllRequests (for the page it matches), redirect (url,
# extensionPath, transform, regexSubstitution), upgradeScheme, and modifyHeaders on request
# headers (Qt can't change response headers or remove request headers).

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

# When rules from the same extension tie on priority, Chrome prefers these in this order
ACTION_RANK = {"allow": 0, "allowAllRequests": 1, "block": 2, "upgradeScheme": 3, "redirect": 4}
ALL_TYPES = {"main_frame", "sub_frame", "stylesheet", "script", "image", "font", "object", "xmlhttprequest",
             "ping", "csp_report", "media", "websocket", "webtransport", "webbundle", "other"}
_WORD = re.compile(r"[a-z0-9]{2,}")
_SEPARATOR = r"(?:[^a-zA-Z0-9_.%-]|$)"  # what "^" means in a urlFilter
_DOMAIN_LISTS = ("requestDomains", "excludedRequestDomains", "initiatorDomains", "excludedInitiatorDomains")
_STRING_LISTS = _DOMAIN_LISTS + ("resourceTypes", "excludedResourceTypes", "requestMethods", "excludedRequestMethods")


def site_of(host: str) -> str:
    """Rough registrable domain (ads.example.co.uk → example.co.uk), for firstParty/thirdParty."""
    labels = host.lower().split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _suffixes(host: str):
    labels = host.split(".")
    return (".".join(labels[i:]) for i in range(len(labels)))


def _in_domains(host: str, domains) -> bool:
    return any(s in domains for s in _suffixes(host))


def url_filter_regex(pattern: str) -> str:
    """Translate a DNR urlFilter ("||ads.example.com^*/banner|") into a regex source."""
    out = ""
    if pattern.startswith("||"):
        out, pattern = r"^[a-z][a-z0-9+.-]*://(?:[^/?#]*\.)?", pattern[2:]
    elif pattern.startswith("|"):
        out, pattern = "^", pattern[1:]
    end = ""
    if pattern.endswith("|"):
        pattern, end = pattern[:-1], "$"
    for ch in pattern:
        out += ".*" if ch == "*" else _SEPARATOR if ch == "^" else re.escape(ch)
    return out + end


# Chrome still takes the condition names from before Chrome 101
_OLD_NAMES = {"domains": "initiatorDomains", "excludedDomains": "excludedInitiatorDomains"}


def modernize(raw):
    """The rule with any old condition names (domains, excludedDomains) renamed."""
    condition = raw.get("condition") if isinstance(raw, dict) else None
    if not isinstance(condition, dict) or not any(k in condition for k in _OLD_NAMES):
        return raw
    condition = dict(condition)
    for old, new in _OLD_NAMES.items():
        if old in condition:
            value = condition.pop(old)
            condition.setdefault(new, value)
    return {**raw, "condition": condition}


def validate_rule(raw) -> str | None:
    """Why a rule can't be used (None if it's fine). Checked when rules come in, so nothing
    malformed ever reaches the request interceptor."""
    if not isinstance(raw, dict):
        return "a rule must be an object"
    if not isinstance(raw.get("id", 0), int) or isinstance(raw.get("id", 0), bool):
        return "a rule's id must be an integer"
    if not isinstance(raw.get("priority", 1), int):
        return "a rule's priority must be an integer"
    action, condition = raw.get("action"), raw.get("condition", {})
    if not isinstance(action, dict) or action.get("type", "block") not in (*ACTION_RANK, "modifyHeaders"):
        return "a rule needs a valid action"
    if not isinstance(condition, dict):
        return "a rule's condition must be an object"
    for key in _STRING_LISTS:
        value = condition.get(key)
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            return f"condition.{key} must be a list of strings"
    for key in ("urlFilter", "regexFilter", "domainType"):
        if key in condition and not isinstance(condition[key], str):
            return f"condition.{key} must be a string"
    if "regexFilter" in condition:
        try:
            groups = re.compile(condition["regexFilter"]).groups
        except re.error:
            return "condition.regexFilter isn't a valid regular expression"
        substitution = (action.get("redirect") or {}).get("regexSubstitution") if isinstance(action.get("redirect"), dict) else None
        if isinstance(substitution, str) and any(int(n) > groups for n in re.findall(r"(?<!\\)\\(\d)", substitution)):
            return "redirect.regexSubstitution refers to a group the regexFilter doesn't have"
    if action.get("type") == "redirect":
        redirect = action.get("redirect")
        if not isinstance(redirect, dict) or not any(k in redirect for k in
                                                     ("url", "extensionPath", "transform", "regexSubstitution")):
            return "a redirect rule needs action.redirect"
        for key in ("url", "extensionPath", "regexSubstitution"):
            if key in redirect and not isinstance(redirect[key], str):
                return f"redirect.{key} must be a string"
        if "transform" in redirect and not isinstance(redirect["transform"], dict):
            return "redirect.transform must be an object"
        if "extensionPath" in redirect and not redirect["extensionPath"].startswith("/"):
            return "redirect.extensionPath must start with /"
        if redirect.get("url", "").strip().lower().startswith(("javascript:", "data:", "file:")):
            return "redirect.url must be a web address"
    if action.get("type") == "modifyHeaders" and not isinstance(action.get("requestHeaders", []), list):
        return "action.requestHeaders must be a list"
    return None


@dataclass(eq=False)
class Rule:
    ext_id: str
    id: int
    priority: int
    action: dict
    condition: dict
    _regex: object = None
    _types: set | None = None
    _sets: dict | None = None

    @property
    def kind(self) -> str:
        return self.action.get("type", "block")

    def types(self) -> set:
        if self._types is None:
            c = self.condition
            if "resourceTypes" in c:
                self._types = set(c["resourceTypes"])
            elif "excludedResourceTypes" in c:  # everything not excluded, main_frame included
                self._types = ALL_TYPES - set(c["excludedResourceTypes"])
            else:  # neither given: main_frame only counts when listed explicitly
                self._types = ALL_TYPES - {"main_frame"}
        return self._types

    def domains(self, key) -> set:
        if self._sets is None:  # turned into sets once, so each check is a lookup, not a scan
            self._sets = {k: {d.lower() for d in self.condition[k]} for k in _DOMAIN_LISTS if k in self.condition}
        return self._sets.get(key, set())

    def regex(self):
        if self._regex is None:
            c = self.condition
            flags = 0 if c.get("isUrlFilterCaseSensitive") else re.IGNORECASE
            if "regexFilter" in c:
                source = c["regexFilter"]
            elif "urlFilter" in c:
                source = url_filter_regex(c["urlFilter"])
            else:
                source = ""
            try:
                self._regex = re.compile(source, flags)
            except (re.error, TypeError):
                self._regex = re.compile(r"(?!)")  # an invalid rule never matches
        return self._regex

    def matches(self, request: "Request") -> bool:
        c = self.condition
        if request.type not in self.types():
            return False
        if "requestDomains" in c and not _in_domains(request.host, self.domains("requestDomains")):
            return False
        if "excludedRequestDomains" in c and _in_domains(request.host, self.domains("excludedRequestDomains")):
            return False
        if "initiatorDomains" in c and not _in_domains(request.initiator, self.domains("initiatorDomains")):
            return False
        if "excludedInitiatorDomains" in c and _in_domains(request.initiator, self.domains("excludedInitiatorDomains")):
            return False
        if "domainType" in c:
            third = site_of(request.host) != site_of(request.initiator) if request.initiator else False
            if (c["domainType"] == "thirdParty") != third:
                return False
        if "requestMethods" in c and request.method not in c["requestMethods"]:
            return False
        if "excludedRequestMethods" in c and request.method in c["excludedRequestMethods"]:
            return False
        if "tabIds" in c or "responseHeaders" in c or "excludedResponseHeaders" in c:
            return False  # can't be checked from a request interceptor
        if "urlFilter" in c or "regexFilter" in c:
            match = self.regex().search(request.url)
            if match is None:
                return False
            request.last_match = match
        return True


@dataclass
class Request:
    url: str              # encoded, as Chrome matches it
    host: str             # punycode
    initiator: str = ""   # the requesting frame's host
    type: str = "other"
    method: str = "get"
    initiator_url: str = ""  # the requesting frame's origin, for host-permission checks
    last_match: object = None
    _words: set | None = field(default=None, repr=False)

    def words(self) -> set:
        if self._words is None:
            self._words = set(_WORD.findall(self.url.lower()))
        return self._words


_PLAIN = re.compile(r"\|\|([a-z0-9.-]+\.[a-z0-9-]+)\^")  # "^" required: "||example.co" also matches example.com


def _plain_domain(condition: dict) -> str | None:
    """The host of a bare "||host^" rule with no other conditions."""
    if condition.keys() - {"urlFilter", "isUrlFilterCaseSensitive"}:
        return None
    m = _PLAIN.fullmatch(condition.get("urlFilter", "").lower())
    return m.group(1) if m else None


def _index_word(pattern: str) -> str | None:
    """The longest whole word a urlFilter forces the URL to contain, or None."""
    anchored = pattern.startswith("|")
    body = pattern.lstrip("|")
    best = None
    for m in re.finditer(r"[a-zA-Z0-9]+", body):
        start, end = m.span()
        # The filter has to bound the word on both sides (separator, anchor or punctuation),
        # or it could be part of a longer word in the URL
        if (start == 0 and not anchored) or end == len(body):
            continue
        if (start and body[start - 1] == "*") or body[end] == "*":
            continue
        word = m.group().lower()
        if len(word) >= 3 and (best is None or len(word) > len(best)):
            best = word
    return best


class RuleIndex:
    """Every enabled rule from every extension, indexed for fast lookup."""

    def __init__(self):
        self.by_domain: dict[str, Rule | list[Rule]] = {}
        self.by_word: dict[str, list[Rule]] = {}
        self.generic: list[Rule] = []
        self.count = 0
        self.order: dict[str, int] = {}  # extension → install order (later wins ties between redirects)
        self.page_cache: dict[str, frozenset] = {}  # allowAllRequests per page, valid for this index only

    def add(self, ext_id: str, rules: list):
        self.order.setdefault(ext_id, len(self.order))
        shared: dict[tuple, Rule] = {}
        for raw in rules:
            raw = modernize(raw)
            if validate_rule(raw) is not None:
                continue
            try:
                priority, action, condition = raw.get("priority", 1), raw["action"], raw.get("condition", {})
                host = _plain_domain(condition)
                if host:
                    # "||host^" and nothing else: most of a block list. One shared rule object per
                    # (action, priority) keeps 200,000 of them to a few MB
                    key = (priority, action.get("type", "block") if len(action) == 1 else json.dumps(action, sort_keys=True))
                    if key not in shared:
                        shared[key] = Rule(ext_id, 0, priority, action, {})
                    self._add_domain(host, shared[key])
                else:
                    self._place(Rule(ext_id, raw.get("id", 0), priority, action, condition))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            self.count += 1

    def _place(self, rule: Rule):
        c = rule.condition
        url_filter = c.get("urlFilter", "")
        if url_filter.startswith("||") and "regexFilter" not in c:
            m = re.match(r"([^\^/*|?:]*)(.?)", url_filter[2:])
            host, after = m.group(1).lower(), m.group(2)
            # Only a complete host can be looked up by suffix ("||ads." or "||example.com*" can't)
            # …and only when it ends there: "||example.com" alone also matches example.community
            if host and "." in host and not host.endswith((".", "-")) and after in ("^", "/", "|"):
                self._add_domain(host, rule)
                return
        if c.get("requestDomains"):
            for domain in c["requestDomains"]:
                self._add_domain(domain.lower(), rule)
            return
        word = _index_word(url_filter) if url_filter and "regexFilter" not in c else None
        if word:
            self.by_word.setdefault(word, []).append(rule)
        else:
            self.generic.append(rule)

    def _add_domain(self, host, rule):
        # A host usually has one rule, stored bare; a list only when there are several
        existing = self.by_domain.get(host)
        if existing is None:
            self.by_domain[host] = rule
        elif isinstance(existing, list):
            existing.append(rule)
        else:
            self.by_domain[host] = [existing, rule]

    def candidates(self, request: Request):
        for suffix in _suffixes(request.host):
            found = self.by_domain.get(suffix)
            if isinstance(found, Rule):
                yield found
            elif found:
                yield from found
        for word in request.words():
            yield from self.by_word.get(word, ())
        yield from self.generic

    def match_each(self, request: Request, kinds=None, permit=None, skip=frozenset()) -> dict:
        """Each extension's own answer: {extension: (rule, regex match)}, by priority and then
        Chrome's action order within that extension. permit(rule, request) can rule a rule out
        (a redirect where the extension has no host access, say); extensions in skip are ignored."""
        best: dict[str, tuple] = {}
        for rule in self.candidates(request):
            kind = rule.kind
            if kind not in ACTION_RANK or (kinds and kind not in kinds) or rule.ext_id in skip:
                continue
            key = (-rule.priority, ACTION_RANK[kind])
            current = best.get(rule.ext_id)
            if current is not None and key >= current[0]:
                continue
            if rule.matches(request) and (permit is None or permit(rule, request)):
                best[rule.ext_id] = (key, rule, request.last_match)
        return {ext: (rule, match) for ext, (_key, rule, match) in best.items()}

    def match(self, request: Request, kinds=None, permit=None, skip=frozenset()) -> tuple[Rule, object] | None:
        """The rule that decides this request: any extension's block, else the newest extension's
        redirect or upgrade, else an allow."""
        answers = self.match_each(request, kinds, permit, skip)
        if not answers:
            return None
        ranked = sorted(answers.values(), key=lambda a: (
            {"block": 0, "redirect": 1, "upgradeScheme": 1}.get(a[0].kind, 2), -self.order.get(a[0].ext_id, 0)))
        return ranked[0]

    def header_rules(self, request: Request, permit=None, skip=frozenset()) -> list[Rule]:
        rules = [r for r in self.candidates(request) if r.kind == "modifyHeaders" and r.ext_id not in skip
                 and r.matches(request) and (permit is None or permit(r, request))]
        return sorted(rules, key=lambda r: -r.priority)  # the highest priority sets a header last


def _substitute(template: str, match) -> str:
    """Chrome's regexSubstitution: \\0-\\9 are groups (unset ones are empty), \\\\ is a backslash."""
    out, i = [], 0
    while i < len(template):
        ch = template[i]
        if ch == "\\" and i + 1 < len(template):
            nxt = template[i + 1]
            if nxt.isdigit():
                n = int(nxt)
                out.append((match.group(n) or "") if n <= (match.re.groups) else "")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _transform_query(query: str, qt: dict) -> str:
    """queryTransform on the raw query: only the named parameters change; the rest stay exactly as
    they were (same bytes, same order)."""
    segments = query.split("&") if query else []
    key_of = lambda segment: unquote_plus(segment.split("=", 1)[0])
    remove = set(qt.get("removeParams", []))
    out = [s for s in segments if key_of(s) not in remove]
    for item in qt.get("addOrReplaceParams", []):
        encoded = f"{quote(item['key'], safe='')}={quote(str(item.get('value', '')), safe='')}"
        at = next((i for i, s in enumerate(out) if key_of(s) == item["key"]), None)
        if at is not None:
            out[at] = encoded
        elif not item.get("replaceOnly"):
            out.append(encoded)
    return "&".join(out)


def redirect_target(rule: Rule, request: Request, match, extension_url) -> str | None:
    """Where a redirect (or upgradeScheme) rule sends the request; None if it can't be worked out."""
    if rule.kind == "upgradeScheme":
        return "https" + request.url[4:] if request.url.startswith("http:") else None
    spec = rule.action.get("redirect") or {}
    if "url" in spec:
        return spec["url"]
    if "extensionPath" in spec:
        return extension_url(rule.ext_id, spec["extensionPath"])
    if "regexSubstitution" in spec and match is not None:
        # The first match within the URL is replaced; what's around it stays
        url = request.url
        return url[:match.start()] + _substitute(spec["regexSubstitution"], match) + url[match.end():]
    if "transform" in spec:
        t = spec["transform"]
        parts = urlsplit(request.url)
        host = t.get("host", parts.hostname or "")
        netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host  # IPv6
        port = t.get("port", parts.port)
        if port not in (None, ""):
            netloc += f":{port}"
        query = parts.query
        if "query" in t:
            query = t["query"].lstrip("?")
        elif "queryTransform" in t:
            query = _transform_query(query, t["queryTransform"])
        fragment = t["fragment"].lstrip("#") if "fragment" in t else parts.fragment
        return urlunsplit((t.get("scheme", parts.scheme), netloc, t.get("path", parts.path), query, fragment))
    return None


def load_ruleset(path) -> list:
    try:
        rules = json.loads(path.read_text(encoding="utf-8-sig"))
        return rules if isinstance(rules, list) else []
    except (OSError, ValueError):
        return []
