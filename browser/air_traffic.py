# Arc's Air Traffic Control: rules that send links from other apps to a particular Space.
#
#   "zoom.us"        → Work      (a domain: that site and its subdomains)
#   "docs.google"    → School    (anything else: links containing that text)
#
# A link that matches opens as a Today tab in that Space; anything else opens the usual way
# (a Little window, or a new tab, per Settings). Rules are checked top to bottom.

from urllib.parse import urlsplit

_DOMAIN_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789.-")
# A pattern ending in one of these is a whole site ("google.com"): only that, and its subdomains, never with more
# after it ("accounts.google.com.evil.io", a page made to look like it, sent into a Space that's signed in there)
_TOP_LEVEL = {"com", "org", "net", "edu", "gov", "io", "co", "us", "uk", "ca", "au", "de", "fr", "jp", "nl", "es",
              "it", "se", "no", "ch", "in", "br", "me", "app", "dev", "ai", "so", "gg", "tv", "fm", "ly", "to", "xyz"}


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
        for i in range(len(host_labels) - len(want) + 1):
            if host_labels[i:i + len(want)] != want:
                continue
            after = host_labels[i + len(want):]
            if not after:
                return True  # (the site itself, or one of its subdomains)
            # ("docs.google" in docs.google.com or .co.uk: a short ending after it, never another site's name)
            if want[-1] not in _TOP_LEVEL and len(after) <= 2 and all(len(label) <= 3 for label in after):
                return True
        return False
    return pattern in url.lower()


def route(rules: list[dict], url: str, spaces=None) -> str | None:
    """The Space id the first matching rule sends this link to, or None. `spaces`: the ids there are (a rule for one
    deleted since is passed over: taken, the link went to no Space, and no rule after it was tried)."""
    for rule in rules:
        if rule.get("space") and (spaces is None or rule["space"] in spaces) and \
                rule_matches(rule.get("match", ""), url):
            return rule["space"]
    return None
