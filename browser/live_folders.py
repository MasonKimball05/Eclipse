# Arc's Live Folders: a pinned folder that fills itself, and keeps itself up to date.
#
#   Feed     any RSS or Atom feed (or a site's address: its feed is found for you). A GitHub repo's
#            address follows its releases (its tags or a branch's commits from those pages), a
#            person's or organisation's their public activity: GitHub has feeds for these it doesn't link to
#   GitHub   pull requests waiting for your review, your open pull requests, or issues assigned
#            to you, read with the GitHub CLI (`gh`), so it uses the account you're signed in to
#   JSON     any JSON API (your own apps): where the list is, and which fields are the title and link.
#            Signed in with the browser's own logins, or with a token kept in the Keychain
#   Page     the links on a page that match a CSS selector, read with the browser's logins, for
#            sites without a feed or an API (a dashboard's "recent" list, say)
#   Calendar the upcoming events in an .ics calendar feed (deadlines, chapter events)
#   Repos    your local git repos with uncommitted or unpushed work, like Repo Radar
#
# Folders refresh when created, every half hour, and from their right-click menu. Items that
# appeared since the last look get a dot until opened. Tabs you already have open stay open.

import datetime
import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from html.parser import HTMLParser
from urllib.parse import urljoin

from PyQt6 import sip
from PyQt6.QtCore import QObject, QProcess, QThread, QTimer, QUrl, QUrlQuery, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from . import keychain, macos
from .downloads import find_tool
from .models import TODAY, Folder, Tab, walk_folders, walk_tabs
from .timefmt import portable

REFRESH_MS = 30 * 60 * 1000
GH_TIMEOUT_MS = 30_000  # gh stuck on the network that long is given up on
MAX_ITEMS = 25
GITHUB = {
    "overview": ("GitHub", None),  # all of the below and your repos' open issues, in sub-folders
    "review": ("PRs to Review", ["search", "prs", "--review-requested=@me", "--state=open"]),
    "mine": ("My Open PRs", ["search", "prs", "--author=@me", "--state=open"]),
    "assigned": ("My Issues", ["search", "issues", "--assignee=@me", "--state=open"]),
}
_FIELDS = ["--json", "title,url,repository,number,isPullRequest"]
OVERVIEW = {
    "review": GITHUB["review"][1] + _FIELDS + ["--limit", str(MAX_ITEMS)],
    "mine": GITHUB["mine"][1] + _FIELDS + ["--limit", str(MAX_ITEMS)],
    "assigned": GITHUB["assigned"][1] + _FIELDS + ["--limit", str(MAX_ITEMS)],
    "yours": ["search", "issues", "--owner=@me", "--state=open", "--include-prs", "--sort", "updated"] + _FIELDS
             + ["--limit", "100"],
    "notifications": ["api", "notifications?per_page=50"],  # (unread ones)
}
SECTIONS = [("review", "Review Requested"), ("mine", "My Pull Requests"), ("assigned", "Assigned to Me"),
            ("notifications", "Notifications")]
OPTIONAL = {"notifications"}  # (a sign-in that can't read them still gets the rest)


# --- JSON APIs -------------------------------------------------------------------------------

def dig(value, path: str):
    """value["a"]["b"][0] for "a.b.0"; None if it isn't there. An empty path is the value itself."""
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def parse_json_items(data, items_path: str, title_field: str, link: str, base_url: str = "") -> list[tuple[str, str]]:
    """[(title, url)] from a JSON answer. `link` is a field name ("url", "links.html") or a template
    with fields in braces ("https://app.example/jobs/{id}"); relative links resolve against base_url."""
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except ValueError as error:
            raise ValueError("not JSON") from error
    rows = dig(data, items_path)
    if isinstance(rows, dict):  # {"id": {...}, ...}
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError(f"no list at “{items_path or '(top level)'}”")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "{" in link:
            url = re.sub(r"\{([\w.]+)\}", lambda m: QUrl.toPercentEncoding(str(dig(row, m.group(1)) or ""), b"/").data()
                         .decode(), link)
        else:
            url = str(dig(row, link) or "")
        url = urljoin(base_url, url) if base_url and url else url
        if not url.lower().startswith(("http://", "https://")):
            continue
        if "{" in title_field:  # "{title} · {company}"
            title = re.sub(r"\{([\w.]+)\}", lambda m: str(dig(row, m.group(1)) or ""), title_field)
        else:
            title = str(dig(row, title_field) or "")
        title = " ".join(title.split()).strip(" ·—-") or url
        items.append((title[:200], url))
    return items[:MAX_ITEMS]


# Runs in a page you're signed in to: the links that match the folder's selector
PAGE_LINKS = r"""
((selector) => {
  let found = [];
  try { found = [...document.querySelectorAll(selector)]; } catch (e) { return {error: 'Not a valid CSS selector'}; }
  return {items: found.map((el) => {
    const a = el.closest('a[href]') || el.querySelector('a[href]');
    return a ? [(el.innerText || a.innerText || a.title || '').trim().split('\n')[0], a.href] : null;
  }).filter(Boolean), title: document.title};
})(%s)
"""


# --- Feeds -----------------------------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(data: bytes, base_url: str = "") -> tuple[str, list[tuple[str, str]]]:
    """(feed title, [(item title, link), …]) from RSS 2.0, RSS 1.0 or Atom; raises ValueError.
    Relative links are resolved against base_url (the feed's own address)."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError("not a feed") from error
    if _local(root.tag) not in ("rss", "RDF", "feed"):
        raise ValueError("not a feed")
    title, items = "", []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "title" and not title:
            title = (element.text or "").strip()
        if tag not in ("item", "entry"):
            continue
        item_title, link = "", ""
        for child in element:
            ctag = _local(child.tag)
            if ctag == "title":
                item_title = " ".join((child.text or "").split())
            elif ctag == "link":
                href = child.get("href")
                if href and child.get("rel", "alternate") == "alternate":
                    link = link or href
                elif child.text and not href:
                    link = child.text.strip()
        if link:
            link = urljoin(base_url, link) if base_url else link
            if link.lower().startswith(("http://", "https://")):  # not javascript:, file:, note:…
                items.append((item_title or link, link))
    return title, items[:MAX_ITEMS]


class _FeedLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in (a.get("rel") or "").lower() and \
                (a.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml") and a.get("href"):
            self.found.append(a["href"])


def find_feed_link(html: str, base: str) -> str | None:
    """A page's advertised feed (<link rel="alternate" type="application/rss+xml">), as an absolute URL."""
    parser = _FeedLinks()
    parser.feed(html)
    return urljoin(base, parser.found[0]) if parser.found else None


# --- Updating a folder ---------------------------------------------------------------------------

# --- GitHub's own feeds -----------------------------------------------------------------------------

# github.com/<name> paths that aren't a person or an organisation
_GITHUB_PAGES = {"about", "account", "apps", "codespaces", "collections", "contact", "copilot", "customer-stories",
                 "dashboard", "enterprise", "enterprises", "events", "explore", "features", "github-copilot",
                 "home", "issues", "join", "login", "logout", "marketplace", "new", "notifications",
                 "organizations", "orgs", "pricing", "pulls", "readme", "resources", "search", "security",
                 "sessions", "settings", "site", "solutions", "sponsors", "stars", "team", "topics", "trending",
                 "users", "watching"}


def _github_parts(url: str) -> list[str] | None:
    q = QUrl(url if "://" in url else "https://" + url)
    if q.host().lower() not in ("github.com", "www.github.com"):
        return None
    return [p for p in q.path().split("/") if p]


def github_repo(url: str) -> tuple[str, str] | None:
    """(owner, repo) for any page of a GitHub repo."""
    parts = _github_parts(url)
    if not parts or len(parts) < 2 or parts[0].lower() in _GITHUB_PAGES:
        return None
    return parts[0], parts[1].removesuffix(".git")


def github_feed(url: str) -> str | None:
    """The Atom feed GitHub keeps for a page it doesn't link one from: a repo's releases (or, from its
    tags or commits pages, those), or a person's public activity. None for anything else."""
    parts = _github_parts(url)
    if not parts or parts[0].lower() in _GITHUB_PAGES or parts[-1].endswith(".atom"):
        return None
    if len(parts) == 1:
        return f"https://github.com/{parts[0]}.atom"
    owner, repo = parts[0], parts[1].removesuffix(".git")
    page = parts[2] if len(parts) > 2 else ""
    if page == "tags":
        return f"https://github.com/{owner}/{repo}/tags.atom"
    if page == "commits":  # (a branch's: the history of one file in it has no feed)
        branch = parts[3] if len(parts) > 3 else ""
        return f"https://github.com/{owner}/{repo}/commits{'/' + branch if branch else ''}.atom"
    return f"https://github.com/{owner}/{repo}/releases.atom"


def known_feed(url: str) -> str | None:
    """The feed a well-known site keeps for a page, often without linking it from the page: GitHub's (above), a
    Bluesky or Mastodon account, a subreddit or Reddit user, a YouTube channel or playlist, a GitLab project's
    releases, a PyPI package's releases, a Stack Overflow (or Stack Exchange) tag, Hacker News. None otherwise."""
    if found := github_feed(url):
        return found
    q = QUrl(url if "://" in url else "https://" + url)
    host = q.host().lower().removeprefix("www.").removeprefix("old.")
    parts = [p for p in q.path().split("/") if p]
    query = QUrlQuery(q)
    if host == "bsky.app" and len(parts) >= 2 and parts[0] == "profile":
        return f"https://bsky.app/profile/{parts[1]}/rss"
    if host == "medium.com" and parts[:1] and parts[0].startswith("@"):
        return f"https://medium.com/feed/{parts[0]}"
    if len(parts) == 1 and parts[0].startswith("@") and len(parts[0]) > 1 and host not in (
            "youtube.com", "m.youtube.com", "tiktok.com", "threads.net", "x.com", "twitter.com"):
        return f"https://{q.host()}/{parts[0]}.rss"  # (Mastodon, and the fediverse servers that work like it)
    if host == "reddit.com" and len(parts) >= 2 and parts[0] in ("r", "user", "u"):
        return f"https://www.reddit.com/{'r' if parts[0] == 'r' else 'user'}/{parts[1]}/.rss"
    if host in ("youtube.com", "m.youtube.com"):
        if len(parts) >= 2 and parts[0] == "channel":
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={parts[1]}"
        if parts[:1] == ["playlist"] and query.queryItemValue("list"):
            return f"https://www.youtube.com/feeds/videos.xml?playlist_id={query.queryItemValue('list')}"
        return None  # (a @handle's page links its feed: found by looking)
    if host == "gitlab.com" and len(parts) >= 2 and parts[0] not in ("explore", "dashboard", "users", "help"):
        project = parts[:parts.index("-")] if "-" in parts else parts
        if len(project) >= 2:
            return f"https://gitlab.com/{'/'.join(project)}/-/releases.atom"
    if host == "pypi.org" and len(parts) >= 2 and parts[0] == "project":
        return f"https://pypi.org/rss/project/{parts[1].lower()}/releases.xml"
    if (host == "stackoverflow.com" or host.endswith(".stackexchange.com")) and parts[:2] == ["questions", "tagged"] \
            and len(parts) >= 3:
        return f"https://{host}/feeds/tag/{parts[2]}"
    if host == "news.ycombinator.com" and q.path() in ("", "/", "/news"):
        return "https://news.ycombinator.com/rss"
    return None


def item_key(tab: Tab) -> str:
    """A Live Folder tab is its feed item's address, even after you've clicked around in it."""
    return tab.home_url or tab.url


def apply_items(folder: Folder, items: list) -> list[Tab]:
    """Make the folder's tabs match the items, newest first; returns the tabs that were dropped.

    An item is (title, url), or (name, [items…]) for a sub-folder of its own (the GitHub folder's
    sections, and a folder per repo). Existing tabs (and their open pages) are kept, wherever they move
    to, and sub-folders keep whether they're open. New ones are marked fresh. Anything else in a folder
    of plain items (it shouldn't be, but a folder from an older version might hold some) stays, after
    them; with sub-folders, the items are the whole of it."""
    live = folder.live
    first_look = "refreshed" not in live
    seen = set(live.get("seen", []))
    # (the GitHub folder always is: with nothing to show at all, its old sub-folders must still go)
    nested = folder.live.get("query") == "overview" or any(isinstance(entry[1], list) for entry in items)
    existing, dropped = {}, []
    for t in walk_tabs(folder.children) if nested else [n for n in folder.children if isinstance(n, Tab)]:
        if item_key(t) in existing:
            dropped.append(t)  # the same item twice: keep one
        else:
            existing[item_key(t)] = t
    listed = set()

    def build(entries, old_children):
        old_folders = {f.name: f for f in old_children if isinstance(f, Folder)}
        children = []
        for title, target in entries:
            if isinstance(target, list):
                sub = old_folders.pop(title, None) or Folder(title, expanded=False)
                sub.live = None
                sub.children = build(target, sub.children)
                if sub.children:  # (an empty section isn't shown)
                    children.append(sub)
                continue
            if target in listed:
                continue  # listed twice (a feed that repeats itself, an issue in two sections): the first
            listed.add(target)
            tab = existing.pop(target, None) or Tab(target, title, home_url=target)
            tab.title, tab.home_url = title, target
            if first_look:
                seen.add(target)
            tab.fresh = target not in seen
            children.append(tab)
        return children

    children = build(items, folder.children)
    others = [] if nested else [n for n in folder.children if not isinstance(n, Tab)]
    folder.children = children + others
    if children:  # forget what's gone (but not everything at once: an empty answer, or inbox zero, is passing)
        live["seen"] = [u for u in seen if any(item_key(t) == u for t in walk_tabs(children))]
    else:
        live["seen"] = sorted(seen)
    live["refreshed"] = time.time()
    return dropped + list(existing.values())


def mark_seen(folder: Folder, tab: Tab):
    tab.fresh = False
    seen = folder.live.setdefault("seen", [])
    if item_key(tab) not in seen:
        seen.append(item_key(tab))


class LiveFolders(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.network = QNetworkAccessManager(self)
        self._busy: set[str] = set()
        self._timer = QTimer(self, interval=REFRESH_MS, timeout=self.refresh_all)
        self._timer.start()
        QTimer.singleShot(5000, self.refresh_all)  # catch up after launch, once the window is up

    def folders(self) -> list[Folder]:
        out = []
        for space in self.browser.store.spaces:
            out += [f for f in walk_folders(space.pinned) if f.live]
        return out

    def refresh_all(self):
        for folder in self.folders():
            self.refresh(folder)

    def refresh(self, folder: Folder, on_done=None):
        if folder.id in self._busy:
            return
        self._busy.add(folder.id)
        done = lambda items, title=None, error=None: self._finish(folder, items, title, error, on_done)
        kind = folder.live.get("kind")
        if kind == "github":
            self._github(folder.live.get("query", "review"), done, folder)
        elif kind == "json":
            self._json(folder, done)
        elif kind == "page":
            self._page_links(folder, done)
        elif kind == "calendar":
            self._get(folder.live.get("url", ""), lambda data, final: self._calendar(data, final, done),
                      lambda message: done([], error="that calendar address isn't there (does the app serve it "
                                                       "yet?)" if "Not Found" in message else message))
        elif kind == "repos":
            self._repos(folder.live.get("root", ""), done)
        else:
            self._feed(folder.live.get("url", ""), done)

    def _finish(self, folder, items, title, error, on_done):
        self._busy.discard(folder.id)
        if error is None:
            if title and not folder.live.get("named"):
                folder.name = title[:40]
            browser = self.browser
            current = browser.current_tab
            on_screen = [current, *(browser.store.split_of(current) or [])] if current is not None else []
            located = browser.store.locate(folder)
            dropped = apply_items(folder, items)
            for tab in dropped:
                if located and any(tab is t for t in on_screen):
                    # Gone from the source while you're reading it (a notification you've just read, say):
                    # it stays, as a Today tab of its own
                    tab.fresh = False
                    browser.store.add_tab(tab, TODAY, located[1], 0)
                    tab.home_url = None
                    continue
                browser.store.remove_from_split(tab)  # gone from the source: close its page too
                browser._remove_view(tab)
            browser.store._changed()
            if current is not None and not browser.store.locate(current):
                browser._activate_fallback()  # (it went, with no Space to keep it in)
            elif dropped:
                browser._relayout_split()
        if on_done is not None:
            on_done(error)

    # Feeds -------------------------------------------------------------------------------------

    def _get(self, url, done, fail, headers=None, secret=False):
        request = QNetworkRequest(QUrl(url))
        for name, value in (headers or {}).items():
            request.setRawHeader(name.encode(), value.encode())
        # Qt copies headers onto a redirect: with a token in them, only follow redirects on the same site
        policy = (QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy if secret
                  else QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, policy)
        request.setTransferTimeout(20_000)
        reply = self.network.get(request)

        def finished():
            if reply.error() != QNetworkReply.NetworkError.NoError:
                fail(reply.errorString())
            else:
                done(bytes(reply.readAll()), reply.url().toString())
            reply.deleteLater()
        reply.finished.connect(finished)

    def _feed(self, url, done, discovered=False, guessed=False):
        known = None if discovered or guessed else known_feed(url)

        def not_there(message):
            if known:  # (the site's usual feed wasn't one this time: look at the page itself)
                self._feed(url, done, guessed=True)
            else:
                done([], error=message)

        def got(data, final_url):
            try:
                title, items = parse_feed(data, final_url)
                if title.startswith("Release notes from "):  # (GitHub's name for a releases feed)
                    title = title.removeprefix("Release notes from ") + " Releases"
                done(items, title)
            except ValueError:
                feed = None if discovered or known else find_feed_link(data.decode("utf-8", errors="replace"),
                                                                        final_url)
                if feed:
                    self._feed(feed, done, discovered=True)
                else:
                    not_there("No feed found at that address.")
        self._get(known or url, got, not_there)

    # Your own apps --------------------------------------------------------------------------------

    def _profile_for(self, folder):
        for space in self.browser.store.spaces:
            if folder in walk_folders(space.pinned):
                return self.browser.profile_named(space.profile)
        return self.browser.profile

    def _json(self, folder, done):
        live = folder.live
        url = live.get("url", "")

        def got(data, final_url):
            try:
                done(parse_json_items(data, live.get("items", ""), live.get("title", "title"),
                                      live.get("link", "url"), final_url))
            except ValueError as error:
                text = data.decode("utf-8", errors="replace").lstrip()[:2000].lower() if isinstance(data, bytes) else ""
                if str(error) == "not JSON" and (text.startswith("<") or "sign in" in text or "log in" in text
                                                 or "password" in text):
                    host = QUrl(url).host()
                    done([], error=f"{host} answered with a page, not data: sign in to it in this browser first")
                else:
                    done([], error=str(error))
        if live.get("auth") == "none":  # a public API (GitHub's, say)
            self._get(url, got, lambda message: done([], error=message), {"Accept": "application/json"})
        elif live.get("auth") == "token":
            # A token from the Keychain, sent as e.g. "Authorization: Bearer …" or "X-API-Key: …"
            token = keychain.load(token_account(folder))
            if not token:
                done([], error="Its token isn't in the Keychain any more: set it again from the folder's menu.")
                return
            header = live.get("header") or "Authorization"
            value = (live.get("prefix", "Bearer ") if header.lower() == "authorization" else "") + token
            self._get(url, got, lambda message: done([], error=message), {header: value}, secret=True)
        else:
            # The browser's own login for that site (its cookies), so nothing to set up
            self.read_in_page(self._profile_for(folder), url, "document.body ? document.body.innerText : ''",
                          lambda text, final: got(text.encode() if isinstance(text, str) else b"", final),
                          lambda message: done([], error=message), settle_ms=200)

    def _page_links(self, folder, done):
        def got(result, final_url):
            if not isinstance(result, dict) or result.get("error"):
                done([], error=(result or {}).get("error") if isinstance(result, dict) else "The page didn't answer")
                return
            items, seen = [], set()
            for title, url in result.get("items") or []:
                if url.startswith(("http://", "https://")) and url not in seen:
                    seen.add(url)
                    items.append((" ".join(title.split())[:200] or url, url))
            done(items[:MAX_ITEMS], result.get("title"))
        self.read_in_page(self._profile_for(folder), folder.live.get("url", ""),
                          PAGE_LINKS % json.dumps(folder.live.get("selector", "a")),
                      got, lambda message: done([], error=message))

    def read_in_page(self, profile, url, script, done, fail, settle_ms=1500):
        """Load `url` in an unseen page with `profile` (so its logins), wait for it to settle (apps
        render after loading), run `script` in its isolated world, then throw the page away."""
        page = QWebEnginePage(profile, self)
        state = {"over": False}
        macos.busy(True)  # loading in the background: no App Nap until it's read

        def finish(result=None, error=None):
            if state["over"]:
                return
            state["over"] = True
            macos.busy(False)
            final = page.url().toString()
            page.deleteLater()
            if error:
                fail(error)
            else:
                done(result, final)

        def loaded(ok):
            if ok:
                state["ok"] = True
                QTimer.singleShot(settle_ms, lambda: state["over"] or page.runJavaScript(
                    script, QWebEngineScript.ScriptWorldId.ApplicationWorld, lambda result: finish(result)))
                return
            # A sign-in redirect or an app's router often abandons the first load on its way to the real
            # one: only give up if nothing else starts loading soon after
            state["restarted"] = False
            QTimer.singleShot(4000, lambda: state["over"] or state.get("ok") or state["restarted"] or finish(
                error="Couldn't load it (are you signed in, and is it running?)"))
        page.loadStarted.connect(lambda: state.update(restarted=True))
        page.loadFinished.connect(loaded)
        QTimer.singleShot(30_000, lambda: finish(error="It took too long to answer"))
        page.load(QUrl(url))

    def _calendar(self, data, final_url, done):
        try:
            done(upcoming_events(data.decode("utf-8", errors="replace"), final_url))
        except ValueError as error:
            done([], error=str(error))

    def _repos(self, root, done):
        # git, once per repo: in a thread, then back on the main thread through a queued signal
        root = os.path.expanduser(root or "~/projects/GitHub")
        job = _RepoScan(root, self)  # owned by this object until it's done, then deletes itself
        job.scanned.connect(lambda items, error: done(items, None, error) if error else done(items))
        job.start()

    def shutdown(self):
        """Quitting: let a repo scan that's running finish (git, a few seconds at most), or Qt aborts
        the app as a running thread is destroyed."""
        for job in self.findChildren(_RepoScan):
            job.scanned.disconnect()
            job.wait(20_000)

    # GitHub ------------------------------------------------------------------------------------

    def _github(self, query, done, folder=None):
        if find_tool("gh") is None:
            done([], error="The GitHub CLI isn't installed (brew install gh, then gh auth login).")
            return
        if query == "overview":
            self._github_overview(done, folder)
            return
        args = GITHUB.get(query, GITHUB["review"])[1] + ["--json", "title,url,repository", "--limit", str(MAX_ITEMS)]
        self._gh(args, lambda rows: done([(_titled(r), r["url"]) for r in rows if r.get("url")]),
                 lambda error: done([], error=error))

    def _github_overview(self, done, folder=None):
        """The GitHub folder: every search at once, then sorted into its sub-folders. A section that
        couldn't be read this time (notifications) keeps what it had, rather than everything in it
        going, and coming back as new next time."""
        results, failed = {}, []
        names = dict(SECTIONS)

        def kept(key):
            previous = next((f for f in (folder.children if folder else []) if isinstance(f, Folder)
                             and f.name == names.get(key)), None)
            return [{"title": t.title, "url": item_key(t)} for t in walk_tabs(previous.children)] if previous else []

        def got(key, rows):
            results[key] = rows
            if len(results) == len(OVERVIEW):
                done(github_sections(results))

        def fail(error):
            if not failed:  # (one message, however many of them fail)
                failed.append(error)
                done([], error=error)
        for key, args in OVERVIEW.items():
            self._gh(args, lambda rows, k=key: not failed and got(k, rows),
                     (lambda _error, k=key: not failed and got(k, kept(k))) if key in OPTIONAL else fail)

    def _gh(self, args, done, fail):
        """Run `gh` with `args`, which asks for JSON: done(the rows) or fail(what went wrong)."""
        process = QProcess(self)

        def finished(code, _status):
            if sip.isdeleted(process) or sip.isdeleted(self):
                return  # (the app's closing: Qt ends the process on its way out)
            output = bytes(process.readAllStandardOutput()).decode(errors="replace")
            error_text = bytes(process.readAllStandardError()).decode(errors="replace").strip()
            process.deleteLater()
            if code != 0:
                fail(error_text or "gh failed")
                return
            try:
                rows = json.loads(output or "[]")
            except ValueError:
                fail("Couldn't read gh's answer.")
                return
            done(rows if isinstance(rows, list) else [])
        process.finished.connect(finished)
        QTimer(process, singleShot=True, interval=GH_TIMEOUT_MS, timeout=process.kill).start()  # (stuck: it fails)
        # (it couldn't start at all: no "finished" follows)
        process.errorOccurred.connect(lambda error: error == QProcess.ProcessError.FailedToStart and (
            process.deleteLater(), fail("Couldn't run gh.")))
        process.start(find_tool("gh"), args)


def _titled(row) -> str:
    """"Title · repo" for an item from a search across repos."""
    return f"{row.get('title', '')} · {(row.get('repository') or {}).get('name', '')}".rstrip(" ·")


def notification_rows(rows) -> list[dict]:
    """GitHub's notifications as search rows: the web page of what each is about (the API gives its API
    address), or the repo's page for things without one (a release, a discussion)."""
    out = []
    for n in rows:
        if "url" in n and "subject" not in n:  # (kept from last time: already a web page)
            out.append(n)
            continue
        subject, repo = n.get("subject") or {}, n.get("repository") or {}
        api = subject.get("url") or ""
        match = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/(issues|pulls|commits)/(\w+)$", api)
        if match:
            kind = {"pulls": "pull", "issues": "issues", "commits": "commit"}[match.group(2)]
            url = f"https://github.com/{match.group(1)}/{kind}/{match.group(3)}"
        else:  # (each its own item, though they open the same page)
            url = f"{repo['html_url']}#notification-{n.get('id', '')}" if repo.get("html_url") else ""
        if url and subject.get("title"):
            out.append({"title": subject["title"], "url": url, "repository": {"name": repo.get("name", "")}})
    return out


def github_sections(results: dict) -> list:
    """The GitHub folder's contents from OVERVIEW's searches: a sub-folder each for reviews asked of you,
    your open pull requests, issues assigned to you and unread notifications, then one for each of your repos with open
    issues or pull requests (most recently active first), its pull requests on top. Something already
    in a section above isn't repeated in its repo's folder."""
    results = {**results, "notifications": notification_rows(results.get("notifications") or [])}
    rows = lambda key: [r for r in results.get(key) or [] if r.get("url")]
    sections, shown = [], set()
    for key, name in SECTIONS:  # (something in two of them shows in the first)
        items = [(_titled(r), r["url"]) for r in rows(key) if r["url"] not in shown]
        shown.update(url for _title, url in items)
        sections.append((name, items))
    repos: dict[str, list] = {}
    full_names = {}
    for r in rows("yours"):  # (newest activity first, so the repos come in that order too)
        if r["url"] in shown:
            continue
        repo = (r.get("repository") or {}).get("name") or "Other"
        full_names[repo] = (r.get("repository") or {}).get("nameWithOwner") or repo
        number = f"#{r['number']} " if r.get("number") else ""
        repos.setdefault(repo, []).append((r.get("isPullRequest", False), f"{'PR ' if r.get('isPullRequest') else ''}"
                                           f"{number}{r.get('title', '')}", r["url"]))
    taken = {name for _key, name in SECTIONS}
    for repo, entries in repos.items():
        entries.sort(key=lambda e: not e[0])  # pull requests first (sorted() keeps the order otherwise)
        # (a repo called "Notifications", say, goes by its full name: sub-folders are kept by name)
        name = full_names.get(repo, f"me/{repo}") if repo in taken else repo
        sections.append((name, [(title, url) for _pr, title, url in entries]))
    return [(name, items) for name, items in sections if items]


# --- Calendars --------------------------------------------------------------------------------

def _ics_lines(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]  # a folded line continues the one before
        else:
            lines.append(raw)
    return lines


def _ics_moment(value: str, tzid: str = ""):
    """An .ics date or time as (the clock time it says, its zone): the zone UTC ("Z"), its TZID's, or None (a date,
    or a "floating" time: this Mac's). (None, None) when it isn't one."""
    value = value.strip()
    try:
        if len(value) == 8:
            return datetime.datetime.strptime(value, "%Y%m%d"), None
        moment = datetime.datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None, None
    if value.endswith("Z"):
        return moment, datetime.timezone.utc
    if tzid:
        try:
            return moment, ZoneInfo(tzid.strip('"'))
        except (ValueError, KeyError, ZoneInfoNotFoundError):
            pass  # (a Windows zone name, say: taken as local time)
    return moment, None


def _in_local_time(moment, zone):
    """A clock time in `zone` as this Mac's (naive) local time."""
    return moment if zone is None else moment.replace(tzinfo=zone).astimezone().replace(tzinfo=None)


def _ics_date(value: str, tzid: str = ""):
    """An .ics date or time as a naive local time: in UTC ("Z") or in its TZID's zone, converted."""
    moment, zone = _ics_moment(value, tzid)
    return None if moment is None else _in_local_time(moment, zone)


_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def next_occurrence(start, rule: str, after, zone=None):
    """The first time a repeating event (a simple daily or weekly RRULE) happens at or after `after`,
    or None once it's over. Anything fancier (monthly by weekday…) is taken from its first date.

    `start` and `after` are clock times in the event's own `zone` (None: local), and so is the answer: a 9:00
    meeting in New York stays at 9:00 there through daylight saving, however that falls where you are."""
    parts = dict(p.split("=", 1) for p in rule.upper().split(";") if "=" in p)
    freq, interval = parts.get("FREQ"), max(int(parts.get("INTERVAL", "1") or 1), 1)
    until = _ics_date(parts["UNTIL"]) if "UNTIL" in parts else None
    if until is not None and zone is not None:
        until = until.astimezone(zone).replace(tzinfo=None)  # (local, as the event's clock has it)
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    if freq not in ("DAILY", "WEEKLY") or start >= after:
        return start if start >= after else None
    if freq == "DAILY":
        step = datetime.timedelta(days=interval)
        n = max(0, -(-(after - start) // step))  # (whole steps to reach it, rounded up)
        moment = start + n * step
        if count is not None and n >= count:
            return None
    else:
        days = sorted(_WEEKDAYS[d[-2:]] for d in parts.get("BYDAY", "").split(",") if d[-2:] in _WEEKDAYS) \
            or [start.weekday()]
        week0 = start - datetime.timedelta(days=start.weekday())  # the Monday of its first week
        moment, seen = None, 0
        for week in range(0, 520 * interval, interval):  # (ten years of weeks is plenty)
            monday = week0 + datetime.timedelta(weeks=week)
            for day in days:
                candidate = monday + datetime.timedelta(days=day)
                if candidate < start:
                    continue
                seen += 1
                if count is not None and seen > count:
                    return None
                if candidate >= after:
                    moment = candidate
                    break
            if moment is not None:
                break
    if moment is None or (until is not None and moment > until):
        return None
    return moment


def upcoming_events(text: str, base_url: str = "", now=None, days=60) -> list[tuple[str, str]]:
    """[("Mar 3 · Thesis draft due", link)] for the events in the next `days`, soonest first. An event
    with no URL of its own links to the calendar's address, told apart by its UID."""
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("not a calendar (.ics)")
    now = now or datetime.datetime.now()
    events, current = [], None
    for line in _ics_lines(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            name, _, params = key.partition(";")
            current[name.upper()] = value
            tzid = re.search(r"(?:^|;)TZID=([^;]+)", params, re.IGNORECASE)
            if tzid:
                current[name.upper() + ";TZID"] = tzid.group(1)
    items = []
    for event in events:
        start, zone = _ics_moment(event.get("DTSTART", ""), event.get("DTSTART;TZID", ""))
        if start is not None and event.get("RRULE"):  # (a weekly meeting that began months ago is still on)
            # Repeated by its own clock (daylight saving where it is, not where you are), then made local
            after = now - datetime.timedelta(hours=12)
            if zone is not None:
                after = after.astimezone(zone).replace(tzinfo=None)
            start = next_occurrence(start, event["RRULE"], after, zone)
        start = None if start is None else _in_local_time(start, zone)
        if start is None or not (now - datetime.timedelta(hours=12) <= start <= now + datetime.timedelta(days=days)):
            continue
        summary = event.get("SUMMARY", "Event").replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
        when = start.strftime(portable("%b %-d")) + ("" if (start.hour, start.minute) == (0, 0) else
                                                start.strftime(portable(" %-I:%M %p")))
        link = event.get("URL", "").strip()
        if not link.lower().startswith(("http://", "https://")):
            uid = QUrl.toPercentEncoding(event.get("UID", summary + when)).data().decode()
            link = (base_url.split("#")[0] or "https://calendar.invalid/") + "#event-" + uid
        items.append((start, f"{when} · {summary}", link))
    items.sort(key=lambda i: i[0])
    return [(title, link) for _start, title, link in items][:MAX_ITEMS]


# --- Repos (Repo Radar, built in) -------------------------------------------------------------------

def _git(repo, *args) -> str:
    try:
        done = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=15,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def web_url(remote: str) -> str | None:
    """https://github.com/me/repo for git@github.com:me/repo.git (or an https remote)."""
    remote = remote.strip()
    m = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", remote)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"(https?)://(?:[^@/]+@)?(.+?)(?:\.git)?/?$", remote)
    return f"https://{m.group(2)}" if m else None


def repo_state(repo: str) -> tuple[int, int] | None:
    """(changed files, commits not pushed) for a repo, or None if it isn't one."""
    status = _git(repo, "status", "--porcelain=v1", "--branch", "--untracked-files=normal")
    if not status:
        return None
    lines = status.splitlines()
    head = lines[0] if lines and lines[0].startswith("##") else ""
    ahead = int(m.group(1)) if (m := re.search(r"ahead (\d+)", head)) else 0
    return len([l for l in lines[1:] if l.strip()]), ahead


def scan_repos(root: str) -> list[tuple[str, str]]:
    items = []
    try:
        names = sorted(os.listdir(root), key=str.lower)
    except OSError as error:
        raise ValueError(f"Can't read {root}") from error
    for name in names:
        repo = os.path.join(root, name)
        if not os.path.isdir(os.path.join(repo, ".git")):
            continue
        state = repo_state(repo)
        if state is None or state == (0, 0):
            continue
        changed, ahead = state
        parts = ([f"{changed} changed"] if changed else []) + ([f"{ahead} unpushed"] if ahead else [])
        url = web_url(_git(repo, "remote", "get-url", "origin")) or f"file://{repo}"
        items.append((f"{name} · {', '.join(parts)}", url))
    return items[:MAX_ITEMS]


class _RepoScan(QThread):
    scanned = pyqtSignal(list, object)  # (items, error)

    def __init__(self, root, parent):
        super().__init__(parent)
        self.root = root
        self.finished.connect(self.deleteLater)  # QThread's own finished: gone once run() has returned

    def run(self):
        try:
            self.scanned.emit(scan_repos(self.root), None)
        except ValueError as error:
            self.scanned.emit([], str(error))


def token_account(folder) -> str:
    return f"live-folder:{folder.id}"


def feed_name(url: str) -> str:
    """A placeholder name until the feed's own title arrives."""
    repo = github_repo(url)
    if repo:
        return repo[1]
    host = QUrl(url if "://" in url else "https://" + url).host().removeprefix("www.")
    return re.sub(r"\.(com|org|net|io|dev|co)$", "", host).capitalize() or "Feed"
