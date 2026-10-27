# Tidy tab titles, like Arc Max's: the sidebar leaves out what its icon already says. "Pull requests ·
# anthropics/claude-code · GitHub" shows as "Pull requests · anthropics/claude-code", "Q3 plan - Google Docs"
# as "Q3 plan". Only how the sidebar shows it: the page's
# own title is kept (and keeps updating), a tab you've renamed keeps your name, and a tab preview or pointing
# at the row shows the whole title. Off in Settings → Tabs.

import re
from urllib.parse import urlsplit

SETTING = "tidy_titles"
# A site's name is set off by spaced punctuation: "Title - Site", "Site | Title", "Title · Site"
_SEPARATOR = re.compile(r"(\s+(?:[-–—|·•»/]|::)\s+)")
# What's left being one of these, the site's name says more ("Home - YouTube" → "YouTube")
_GENERIC = {"home", "homepage", "welcome", "dashboard", "start", "feed", "untitled"}
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}  # (bbc.co.uk is "bbc")
# Words a site's name is often made of besides its own ("BBC News", "Stripe Documentation")
_NAME_WORDS = {"news", "docs", "documentation", "help", "blog", "online", "app", "apps", "web", "official", "site",
               "search", "maps", "images", "translate"}
_ALIASES = {"gmail": "google"}  # (a product's name: Gmail is mail.google.com)
_EMAIL = re.compile(r"\S+@\S+\.\w+")  # which account it is: "Inbox (3) - you@gmail.com - Gmail"


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _host_names(host: str) -> list[str]:
    """The names in a host that could be the site's: "mail.google.com" → ["mail", "google"]."""
    labels = host.lower().removeprefix("www.").split(".")
    if len(labels) > 1:
        labels = labels[:-1]
        if len(labels) > 1 and labels[-1] in _SECOND_LEVEL:
            labels = labels[:-1]
    return [label for label in labels if label]


def is_site_name(part: str, host: str) -> bool:
    """Whether a piece of a title is just the site's name ("GitHub" for github.com, "Gmail" for
    mail.google.com, "Google Docs" for docs.google.com)."""
    squashed, names = _squash(part), _host_names(host)
    words = [w for w in re.split(r"[^a-z0-9]+", part.lower()) if w]
    if not squashed or not names or len(words) > 3:
        return False
    if squashed in names or squashed == "".join(names) or squashed == "".join(reversed(names)):
        return True
    # Whole words only, each one the host's or a usual part of a name ("Google Docs", "BBC News"), so a
    # title's own words ("GitHub Actions", "Mail merge", "Team standup") are never taken for the site's
    own = [w in names or _ALIASES.get(w) in names for w in words]
    return any(own) and all(o or w in _NAME_WORDS for o, w in zip(own, words))


def tidy(title: str, url: str) -> str:
    text = title.strip()
    host = urlsplit(url).hostname or ""
    pieces = _SEPARATOR.split(text)  # [part, separator, part, …]
    if not host or len(pieces) < 3:
        return text or title
    site = None
    for end in (-1, 0):  # the site's name is at one end: most often the last
        if is_site_name(pieces[end], host):
            site = pieces[end].strip()
            pieces = pieces[:-2] if end == -1 else pieces[2:]
            break
    if site is None:
        return text
    if len(pieces) >= 3 and _EMAIL.fullmatch(pieces[-1].strip()):
        pieces = pieces[:-2]
    rest = "".join(pieces).strip()
    if not rest:
        return text
    return site if _squash(rest) in _GENERIC else rest


def shown_title(tab, settings: dict) -> str:
    """What the sidebar calls a tab."""
    if tab.custom_title or not tab.title or not settings.get(SETTING, True):
        return tab.label
    return tidy(tab.title, tab.url) or tab.label
