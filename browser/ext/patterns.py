# URL matching for userscripts and extensions, turned into regular expressions that work
# the same in Python (tests, tab queries) and JavaScript (the injected scripts check
# location.href themselves).
#
#   Chrome match patterns   <all_urls>, *://*.example.com/*, https://example.com/path*
#   Greasemonkey globs      http*://example.com/*   (and /regex/ form)

import re

_MATCH = re.compile(r"^(\*|https?|file|ftp|wss?)://([^/]*)(/.*)$")


def _escape(text: str) -> str:
    # Escaping that means the same thing in Python and JavaScript regexes
    return re.sub(r"([.^$+?()\[\]{}|\\/])", r"\\\1", text)


def match_pattern(pattern: str) -> str | None:
    """Chrome match pattern → regex source, or None if it isn't a valid pattern."""
    if pattern == "<all_urls>":
        return r"^(https?|file|ftp)://.*$"
    m = _MATCH.match(pattern)
    if not m:
        return None
    scheme, host, path = m.groups()
    scheme_re = "https?" if scheme == "*" else _escape(scheme)
    if host == "*":
        host_re = r"[^/]*"
    elif host.startswith("*."):
        host_re = r"([^/]*\.)?" + _escape(host[2:])
    else:
        host_re = _escape(host)
    path_re = ".*".join(_escape(part) for part in path.split("*"))
    return f"^{scheme_re}://{host_re}(:\\d+)?{path_re}$"


def glob_pattern(glob: str) -> str:
    """Greasemonkey @include/@exclude → regex source. "/.../" is already a regex."""
    if len(glob) > 2 and glob.startswith("/") and glob.endswith("/"):
        return glob[1:-1]
    glob = glob.replace(".tld", ".*")  # Greasemonkey's "any top-level domain"
    return "^" + ".*".join(_escape(part) for part in glob.split("*")) + "$"


def matches(url: str, includes: list[str], excludes: list[str] = ()) -> bool:
    """Python-side check, using the same regex sources the injected JS uses."""
    def hit(sources):
        for source in sources:
            try:
                if re.search(source, url):
                    return True
            except re.error:
                continue
        return False
    return hit(includes) and not hit(excludes)
