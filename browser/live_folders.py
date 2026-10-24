# Arc's Live Folders: a pinned folder that fills itself, and keeps itself up to date.
#
#   Feed     any RSS or Atom feed (or a site's address: its feed is found for you)
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
from html.parser import HTMLParser
from urllib.parse import urljoin

from PyQt6.QtCore import QObject, QProcess, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from . import keychain
from .downloads import find_tool
from .models import Folder, Tab, walk_folders

REFRESH_MS = 30 * 60 * 1000
MAX_ITEMS = 25
GITHUB = {
    "review": ("PRs to Review", ["search", "prs", "--review-requested=@me", "--state=open"]),
    "mine": ("My Open PRs", ["search", "prs", "--author=@me", "--state=open"]),
    "assigned": ("My Issues", ["search", "issues", "--assignee=@me", "--state=open"]),
}


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

def item_key(tab: Tab) -> str:
    """A Live Folder tab is its feed item's address, even after you've clicked around in it."""
    return tab.home_url or tab.url


def apply_items(folder: Folder, items: list[tuple[str, str]]) -> list[Tab]:
    """Make the folder's tabs match the items, newest first; returns the tabs that were dropped.
    Existing tabs (and their open pages) are kept; new ones are marked fresh. Anything else in the
    folder (it shouldn't be, but a folder from an older version might hold some) stays, after them."""
    live = folder.live
    first_look = "refreshed" not in live
    seen = set(live.get("seen", []))
    existing, dropped = {}, []
    for t in folder.children:
        if isinstance(t, Tab):
            if item_key(t) in existing:
                dropped.append(t)  # the same item twice: keep one
            else:
                existing[item_key(t)] = t
    others = [n for n in folder.children if not isinstance(n, Tab)]
    children, listed = [], set()
    for title, url in items:
        if url in listed:
            continue  # a feed that lists an item twice
        listed.add(url)
        tab = existing.pop(url, None) or Tab(url, title, home_url=url)
        tab.title, tab.home_url = title, url
        if first_look:
            seen.add(url)
        tab.fresh = url not in seen
        children.append(tab)
    folder.children = children + others
    live["seen"] = [u for u in seen if any(item_key(t) == u for t in children)]  # forget what's gone
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
            self._github(folder.live.get("query", "review"), done)
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
            dropped = apply_items(folder, items)
            for tab in dropped:  # gone from the source: close its page too
                browser.store.remove_from_split(tab)
                browser._destroy_view(tab)
            browser.store._changed()
            if any(tab is current for tab in dropped):
                browser._activate_fallback()  # you were looking at it: go somewhere sensible
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

    def _feed(self, url, done, discovered=False):
        def got(data, final_url):
            try:
                title, items = parse_feed(data, final_url)
                done(items, title)
            except ValueError:
                feed = None if discovered else find_feed_link(data.decode("utf-8", errors="replace"), final_url)
                if feed:
                    self._feed(feed, done, discovered=True)
                else:
                    done([], error="No feed found at that address.")
        self._get(url, got, lambda message: done([], error=message))

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

        def finish(result=None, error=None):
            if state["over"]:
                return
            state["over"] = True
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
        root = os.path.expanduser(root or "~/Documents/GitHub")
        job = _RepoScan(root, self)  # owned by this object until it's done, then deletes itself
        job.scanned.connect(lambda items, error: done(items, None, error) if error else done(items))
        job.start()

    # GitHub ------------------------------------------------------------------------------------

    def _github(self, query, done):
        gh = find_tool("gh")
        if gh is None:
            done([], error="The GitHub CLI isn't installed (brew install gh, then gh auth login).")
            return
        process = QProcess(self)
        args = GITHUB.get(query, GITHUB["review"])[1] + ["--json", "title,url,repository", "--limit", str(MAX_ITEMS)]

        def finished(code, _status):
            output = bytes(process.readAllStandardOutput()).decode(errors="replace")
            error_text = bytes(process.readAllStandardError()).decode(errors="replace").strip()
            process.deleteLater()
            if code != 0:
                done([], error=error_text or "gh failed")
                return
            try:
                rows = json.loads(output or "[]")
            except ValueError:
                done([], error="Couldn't read gh's answer.")
                return
            items = [(f"{r['title']} · {(r.get('repository') or {}).get('name', '')}".rstrip(" ·"), r["url"])
                     for r in rows if r.get("url")]
            done(items)
        process.finished.connect(finished)
        process.start(gh, args)


# --- Calendars --------------------------------------------------------------------------------

def _ics_lines(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]  # a folded line continues the one before
        else:
            lines.append(raw)
    return lines


def _ics_date(value: str):
    value = value.strip()
    try:
        if len(value) == 8:
            return datetime.datetime.strptime(value, "%Y%m%d")
        moment = datetime.datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
        if value.endswith("Z"):
            moment = moment.replace(tzinfo=datetime.timezone.utc).astimezone().replace(tzinfo=None)
        return moment
    except ValueError:
        return None


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
            current[key.split(";", 1)[0].upper()] = value
    items = []
    for event in events:
        start = _ics_date(event.get("DTSTART", ""))
        if start is None or not (now - datetime.timedelta(hours=12) <= start <= now + datetime.timedelta(days=days)):
            continue
        summary = event.get("SUMMARY", "Event").replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
        when = start.strftime("%b %-d") + ("" if (start.hour, start.minute) == (0, 0) else start.strftime(" %-I:%M %p"))
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
    host = QUrl(url if "://" in url else "https://" + url).host().removeprefix("www.")
    return re.sub(r"\.(com|org|net|io|dev|co)$", "", host).capitalize() or "Feed"
