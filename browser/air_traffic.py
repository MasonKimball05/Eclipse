# Arc's Air Traffic Control: rules that send links from other apps to a particular Space.
#
#   "zoom.us"        → Work      (a domain: that site and its subdomains)
#   "docs.google"    → School    (anything else: links containing that text)
#
# A link that matches opens as a Today tab in that Space; anything else opens the usual way
# (a Little window, or a new tab, per Settings). Rules are checked top to bottom.

from urllib.parse import urlsplit

_DOMAIN_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789.-")


def _is_domain(text: str) -> bool:
    return "." in text and set(text) <= _DOMAIN_CHARS and not text.startswith(".")


def rule_matches(pattern: str, url: str) -> bool:
    pattern = pattern.strip().lower()
    if not pattern:
        return False
    if _is_domain(pattern):
        # Whole labels of the host, anywhere in it: "zoom.us" covers us02web.zoom.us (not notzoom.us),
        # and "docs.google" covers docs.google.com
        host_labels = (urlsplit(url).hostname or "").lower().split(".")
        want = pattern.split(".")
        return any(host_labels[i:i + len(want)] == want for i in range(len(host_labels) - len(want) + 1))
    return pattern in url.lower()


def route(rules: list[dict], url: str) -> str | None:
    """The Space id the first matching rule sends this link to, or None."""
    for rule in rules:
        if rule.get("space") and rule_matches(rule.get("match", ""), url):
            return rule["space"]
    return None
