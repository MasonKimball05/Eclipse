# The New Live Folder dialog: what a folder follows, from a feed or GitHub to your own apps'
# APIs and pages.

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                             QRadioButton, QStackedWidget, QVBoxLayout, QWidget)

from . import forms, keychain, prompts
from .features import Feature
from .live_folders import (DEFAULT_ROOT, GITHUB, LIMIT_CHOICES, MAX_ITEMS, REFRESH_CHOICES, LiveFolders, item_limit,
                           refresh_minutes, allow_here, allowed_here, feed_name, github_repo,
                           mark_seen, token_account)
from .models import walk_tabs
from .theme import FittedLabel, stylesheet

KINDS = [
    ("feed", "A feed", "RSS or Atom, or any site that has one. Or a page from a site that keeps one: a GitHub or "
                       "GitLab repo (its releases), a subreddit, a YouTube channel, a Bluesky, Mastodon or Medium "
                       "account, a PyPI package, a Stack Overflow tag."),
    ("github", "GitHub", "Pull requests and issues, with the GitHub CLI you're signed in to."),
    ("json", "An app's API", "Any JSON list: your own apps, a dashboard's API."),
    ("page", "Links on a page", "The links on a page that match a CSS selector, read with your logins."),
    ("calendar", "A calendar", "Upcoming events from an .ics feed: deadlines, chapter events."),
    ("repos", "Repo Radar", "Your local git repos with uncommitted or unpushed work."),
]


# Settings → Live Folders: every kind, what it's for and how to set one up, each with a button that starts one
# (the New Live Folder dialog, that kind chosen). (kind, how to set it up, examples)
GUIDE = {
    "feed": ("Paste a feed's address, or just a site's: Eclipse finds its feed for you.",
             "github.com/MasonKimball05/Eclipse · reddit.com/r/macapps · youtube.com/@verge · "
             "bsky.app/profile/someone.bsky.social · pypi.org/project/requests · stackoverflow.com/questions/tagged/pyqt"),
    "github": ("Needs the GitHub CLI, signed in once: install it ({install}), run “gh auth login”, then pick what "
               "to follow.", "PRs waiting for your review · your open PRs · issues assigned to you · all of them, "
                             "with each repo's open issues, in sub-folders"),
    "json": ("The address of an API that answers with a list, where the list is in its answer (blank if it's the "
             "answer itself), and which fields are each item's title and link (a link can be a pattern: "
             "https://app.example/items/{{id}}/). It signs in with your logins in Eclipse, or a token kept {vault}.",
             "your own app's /api/announcements/ · a dashboard's recent items"),
    "page": ("A page's address and a CSS selector that picks out its links (“.recent a”, say), for sites with no "
             "feed or API. It reads the page with your logins. To find a selector, right-click a link on the page "
             "and choose Inspect (Developer Mode).", "a team dashboard's “Recently updated” list · a forum's "
                                                      "newest threads"),
    "calendar": ("The calendar's .ics address. Google Calendar: Settings → your calendar → “Secret address in iCal "
                 "format”. Outlook: Settings → Calendar → Shared calendars → Publish a calendar.",
                 "deadlines · a club's or chapter's events · a class schedule"),
    "repos": ("The folder your git repos are in (~/projects/GitHub, unless you choose another). It lists the ones "
              "with changes you haven't committed or commits you haven't pushed. It only runs on this computer.",
              "the side project you forgot to push"),
}


def _hint(text):
    label = FittedLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


class LiveFolderDialog(QDialog):
    def __init__(self, browser, preset=None):
        super().__init__(browser)
        self.setWindowTitle("New Live Folder")
        self.setStyleSheet(stylesheet(browser.theme))
        self.setProperty("panel", True)
        self.resize(520, 0)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.kind_group = QButtonGroup(self)
        for i, (key, title, detail) in enumerate(KINDS):
            button = QRadioButton(f"{title} — {detail}")
            button.setProperty("kind", key)
            self.kind_group.addButton(button, i)
            layout.addWidget(button)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)

        # Feed
        feed = QWidget()
        f = QFormLayout(feed)
        self.feed_url = QLineEdit()
        self.feed_url.setPlaceholderText("https://example.com/feed.xml, example.com, or a GitHub repo")
        f.addRow("Address", self.feed_url)
        self.pages.addWidget(feed)

        # GitHub
        github = QWidget()
        g = QFormLayout(github)
        self.github_query = QComboBox()
        for key, (name, _args) in GITHUB.items():
            self.github_query.addItem("Everything, in sub-folders: reviews, your PRs, your issues, each repo's "
                                      "open issues" if key == "overview" else name, key)
        g.addRow("Show", self.github_query)
        self.pages.addWidget(github)

        # JSON
        api = QWidget()
        a = QFormLayout(api)
        self.json_url = QLineEdit()
        self.json_url.setPlaceholderText("http://localhost:8000/api/announcements/")
        self.json_items = QLineEdit()
        self.json_items.setPlaceholderText("results   (blank if the answer is the list itself)")
        self.json_title = QLineEdit("title")
        self.json_link = QLineEdit("url")
        self.json_auth = QComboBox()
        self.json_auth.addItem("My login in this browser (cookies)", "browser")
        self.json_auth.addItem("A token (kept in the Keychain)", "token")
        self.json_auth.addItem("Nothing (a public address)", "none")
        self.json_header = QLineEdit("Authorization")
        self.json_token = QLineEdit()
        self.json_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.json_token.setPlaceholderText("Paste it here: it goes straight to the Keychain")
        a.addRow("API address", self.json_url)
        a.addRow("List is at", self.json_items)
        a.addRow("Title field", self.json_title)
        a.addRow("Link", self.json_link)
        a.addRow("", _hint("A field name (url, links.html), or an address with fields in braces: "
                           "https://app.example/items/{id}/"))
        a.addRow("Sign in with", self.json_auth)
        a.addRow("Header", self.json_header)
        a.addRow("Token", self.json_token)
        self.json_auth.currentIndexChanged.connect(self._auth_changed)
        self.pages.addWidget(api)

        # Page links
        page = QWidget()
        pl = QFormLayout(page)
        self.page_url = QLineEdit()
        self.page_url.setPlaceholderText("https://app.example/dashboard")
        self.page_selector = QLineEdit()
        self.page_selector.setPlaceholderText("main a.item-title   (right-click → Inspect to find one)")
        pl.addRow("Page", self.page_url)
        pl.addRow("Links matching", self.page_selector)
        pl.addRow("", _hint("Read with the logins of the Space it's in, a few seconds after the page loads."))
        self.pages.addWidget(page)

        # Calendar
        calendar = QWidget()
        c = QFormLayout(calendar)
        self.calendar_url = QLineEdit()
        self.calendar_url.setPlaceholderText("https://app.example/calendar.ics")
        c.addRow("Calendar feed", self.calendar_url)
        c.addRow("", _hint("The next 60 days, soonest first."))
        self.pages.addWidget(calendar)

        # Repos
        repos = QWidget()
        r = QFormLayout(repos)
        self.repos_root = QLineEdit("~/projects/GitHub")
        r.addRow("Repos folder", self.repos_root)
        r.addRow("", _hint("Each item opens the repo on GitHub."))
        self.pages.addWidget(repos)
        for form in (f, g, a, pl, c, r):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        settings = QFormLayout()
        self.refresh = QComboBox()
        for minutes in REFRESH_CHOICES:
            self.refresh.addItem(every(minutes), minutes)
        self.refresh.setCurrentIndex(self.refresh.findData(refresh_minutes({})))
        self.limit = QComboBox()
        for n in LIMIT_CHOICES:
            self.limit.addItem(f"{n} items", n)
        self.limit.setCurrentIndex(self.limit.findData(MAX_ITEMS))
        settings.addRow("Refresh", self.refresh)
        settings.addRow("Show at most", self.limit)
        layout.addLayout(settings)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Folder name (optional)")
        layout.addWidget(self.name)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create = buttons.addButton("Create", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.kind_group.idToggled.connect(lambda i, on: on and self.pages.setCurrentIndex(i))
        self.kind_group.button(0).setChecked(True)
        self._auth_changed()
        if preset:
            self.apply(preset)

    def _auth_changed(self):
        token = self.json_auth.currentData() == "token"
        for widget in (self.json_header, self.json_token):
            widget.setEnabled(token)

    def apply(self, preset: dict):
        """Fill the form in (from an app's suggested folders)."""
        kind = preset.get("kind", "feed")
        index = next((i for i, k in enumerate(KINDS) if k[0] == kind), 0)
        self.kind_group.button(index).setChecked(True)
        self.name.setText(preset.get("name", ""))
        self.refresh.setCurrentIndex(self.refresh.findData(refresh_minutes(preset)))
        self.limit.setCurrentIndex(self.limit.findData(item_limit(preset)))
        if kind == "github" and preset.get("query") in GITHUB:
            self.github_query.setCurrentIndex(max(0, self.github_query.findData(preset["query"])))
        if kind == "feed":
            self.feed_url.setText(preset.get("url", ""))
        elif kind == "json":
            self.json_url.setText(preset.get("url", ""))
            self.json_items.setText(preset.get("items", ""))
            self.json_title.setText(preset.get("title", "title"))
            self.json_link.setText(preset.get("link", "url"))
            self.json_auth.setCurrentIndex(max(0, self.json_auth.findData(preset.get("auth", "browser"))))
            self.json_header.setText(preset.get("header", "Authorization"))
        elif kind == "page":
            self.page_url.setText(preset.get("url", ""))
            self.page_selector.setText(preset.get("selector", ""))
        elif kind == "calendar":
            self.calendar_url.setText(preset.get("url", ""))
        elif kind == "repos":
            self.repos_root.setText(preset.get("root", "") or "~/projects/GitHub")

    def values(self) -> dict:
        """What's filled in, as the native form sends it (live_from reads either)."""
        return {"kind": KINDS[self.kind_group.checkedId()][0], "name": self.name.text(),
                "feed_url": self.feed_url.text(), "github_query": self.github_query.currentData(),
                "json_url": self.json_url.text(), "json_items": self.json_items.text(),
                "json_title": self.json_title.text(), "json_link": self.json_link.text(),
                "json_auth": self.json_auth.currentData(), "json_header": self.json_header.text(),
                "json_token": self.json_token.text(), "page_url": self.page_url.text(),
                "page_selector": self.page_selector.text(), "calendar_url": self.calendar_url.text(),
                "repos_root": self.repos_root.text(), "refresh": self.refresh.currentData(),
                "limit": self.limit.currentData()}

    def result_live(self) -> tuple[dict, str, str | None] | None:
        return live_from(self.values())


# What a source's settings are when they're not set (a folder from an older version, or an app's suggestion, can
# leave them out): comparing with these, an edit that leaves them as they were doesn't count as a new source
SOURCE_DEFAULTS = {"items": "", "title": "title", "link": "url", "auth": "browser", "selector": "", "root": "",
                   "header": "Authorization"}


def source_value(live: dict, key: str):
    value = live.get(key)
    if key == "header" and live.get("auth") != "token":
        return None  # (a header's only used with a token)
    return SOURCE_DEFAULTS.get(key) if value in (None, "") and key in SOURCE_DEFAULTS else value


def every(minutes: int) -> str:
    return f"Every {minutes} minutes" if minutes < 60 else "Every hour" if minutes == 60 else \
        f"Every {minutes // 60} hours"


def _choice(value, choices, default) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value in choices else default


def live_from(v: dict) -> tuple[dict, str, str | None] | None:
    """(live settings, folder name, token to keep in the Keychain) from the form's values, or None if something's
    missing."""
    result = _source_from(v)
    if result is None:
        return None
    live, name, token = result
    refresh = _choice(v.get("refresh"), REFRESH_CHOICES, refresh_minutes({}))
    limit = _choice(v.get("limit"), LIMIT_CHOICES, MAX_ITEMS)
    if refresh != refresh_minutes({}):  # (kept only when it isn't the default)
        live["refresh"] = refresh
    if limit != MAX_ITEMS:
        live["limit"] = limit
    return live, name, token


def _source_from(v: dict) -> tuple[dict, str, str | None] | None:
    text = lambda key: str(v.get(key) or "").strip()  # noqa: E731
    kind = v.get("kind") if v.get("kind") in [k for k, _t, _d in KINDS] else "feed"
    name = text("name")
    if kind == "feed":
        url = text("feed_url")
        if not url:
            return None
        return {"kind": "feed", "url": url if "://" in url else "https://" + url}, name, None
    if kind == "github":
        query = v.get("github_query") if v.get("github_query") in GITHUB else next(iter(GITHUB))
        return {"kind": "github", "query": query, "named": True}, name or GITHUB[query][0], None
    if kind == "json":
        url = text("json_url")
        if not url.startswith(("http://", "https://")):
            return None
        live = {"kind": "json", "url": url, "items": text("json_items"), "title": text("json_title") or "title",
                "link": text("json_link") or "url",
                "auth": v.get("json_auth") if v.get("json_auth") in ("token", "none") else "browser"}
        token = None
        if live["auth"] == "token":
            live["header"] = text("json_header") or "Authorization"
            token = text("json_token") or None
            if token is None:
                return None
        return live, name, token
    if kind == "calendar":
        url = text("calendar_url").replace("webcal://", "https://")
        return ({"kind": "calendar", "url": url}, name, None) if url.startswith(("http://", "https://")) else None
    if kind == "repos":
        return {"kind": "repos", "root": text("repos_root")}, name or "Repo Radar", None
    url, selector = text("page_url"), text("page_selector")
    if not url.startswith(("http://", "https://")) or not selector:
        return None
    return {"kind": "page", "url": url, "selector": selector}, name, None


def live_sections(preset: dict | None = None) -> list[dict]:
    """The New Live Folder form (forms.py): as LiveFolderDialog has it, natively. Each kind's fields show while
    it's chosen."""
    p = preset or {}
    kind = p.get("kind") if p.get("kind") in [k for k, _t, _d in KINDS] else "feed"
    when = lambda *kinds: {"id": "kind", "is": list(kinds)}  # noqa: E731
    given = lambda k, key, default="": p.get(key, default) if kind == k else default  # noqa: E731
    auth = given("json", "auth") if given("json", "auth") in ("token", "none") else "browser"
    return [
        forms.section(
            {"id": "kind", "kind": "menu", "label": "Follow", "value": kind,
             "options": [{"value": k, "label": t} for k, t, _d in KINDS]},
            *[{"kind": "hint", "text": d, "shown_when": when(k)} for k, _t, d in KINDS]),
        forms.section(
            {"id": "feed_url", "kind": "text", "label": "Address", "value": given("feed", "url"),
             "placeholder": "https://example.com/feed.xml, example.com, or a GitHub repo", "shown_when": when("feed")},
            {"id": "github_query", "kind": "menu", "label": "Show", "shown_when": when("github"),
             "value": given("github", "query") if given("github", "query") in GITHUB else next(iter(GITHUB)),
             "options": [{"value": key, "label": "Everything, in sub-folders" if key == "overview" else name}
                         for key, (name, _args) in GITHUB.items()]},
            {"id": "json_url", "kind": "text", "label": "API address", "value": given("json", "url"),
             "placeholder": "http://localhost:8000/api/announcements/", "shown_when": when("json")},
            {"id": "json_items", "kind": "text", "label": "List is at", "value": given("json", "items"),
             "placeholder": "results (blank if the answer is the list)", "shown_when": when("json")},
            {"id": "json_title", "kind": "text", "label": "Title field", "value": given("json", "title", "title"),
             "shown_when": when("json")},
            {"id": "json_link", "kind": "text", "label": "Link", "value": given("json", "link", "url"),
             "shown_when": when("json")},
            {"kind": "hint", "shown_when": when("json"), "text": "A field name (url, links.html), or an address with "
             "fields in braces: https://app.example/items/{id}/"},
            {"id": "json_auth", "kind": "menu", "label": "Sign in with", "value": auth, "shown_when": when("json"),
             "options": [{"value": "browser", "label": "My login in this browser (cookies)"},
                         {"value": "token", "label": "A token (kept in the Keychain)"},
                         {"value": "none", "label": "Nothing (a public address)"}]},
            {"id": "json_header", "kind": "text", "label": "Header", "value": given("json", "header", "Authorization"),
             "shown_when": when("json"), "enabled_when": {"id": "json_auth", "is": ["token"]}},
            {"id": "json_token", "kind": "secret", "label": "Token", "shown_when": when("json"),
             "placeholder": "Goes straight to the Keychain", "enabled_when": {"id": "json_auth", "is": ["token"]}},
            {"id": "page_url", "kind": "text", "label": "Page", "value": given("page", "url"),
             "placeholder": "https://app.example/dashboard", "shown_when": when("page")},
            {"id": "page_selector", "kind": "text", "label": "Links matching", "value": given("page", "selector"),
             "placeholder": "main a.item-title", "shown_when": when("page")},
            {"kind": "hint", "shown_when": when("page"),
             "text": "Read with the logins of the Space it's in, a few seconds after the page loads. To find a "
                     "selector, right-click a link and choose Inspect."},
            {"id": "calendar_url", "kind": "text", "label": "Calendar feed", "value": given("calendar", "url"),
             "placeholder": "https://app.example/calendar.ics", "shown_when": when("calendar")},
            {"kind": "hint", "text": "The next 60 days, soonest first.", "shown_when": when("calendar")},
            {"id": "repos_root", "kind": "text", "label": "Repos folder",
             "value": given("repos", "root") or "~/projects/GitHub", "shown_when": when("repos")},
            {"kind": "hint", "text": "Each item opens the repo on GitHub.", "shown_when": when("repos")}),
        forms.section(
            {"id": "refresh", "kind": "menu", "label": "Refresh", "value": str(refresh_minutes(p)),
             "options": [{"value": str(m), "label": every(m)} for m in REFRESH_CHOICES]},
            {"id": "limit", "kind": "menu", "label": "Show at most", "value": str(item_limit(p)),
             "options": [{"value": str(n), "label": f"{n} items"} for n in LIMIT_CHOICES]},
            footer="For a folder of sub-folders, at most that many in each."),
        forms.section({"id": "name", "kind": "text", "label": "Folder name", "value": p.get("name", ""),
                       "placeholder": "Optional"}),
    ]


class LiveFoldersFeature(Feature):
    """Live Folders: pinned folders that fill themselves from a feed or GitHub, refreshed every half hour (or as often as each is set to), new
    items bold with a count on the folder until you open them."""
    name = "live_folders"

    def __init__(self, window):
        super().__init__(window)
        self.folders = LiveFolders(window)

    def shutdown(self):
        self.folders.shutdown()

    def _refresh(self, folder):
        window = self.window
        self.folders.refresh(folder, lambda error: window.toast(
            f"Couldn't update “{folder.name}”: {error}" if error else f"“{folder.name}” is up to date"))

    # --- Making them -------------------------------------------------------------------------------------

    def new_live_folder(self, preset=None):
        window = self.window
        form = forms.open_form(window, "New Live Folder", live_sections(preset),
                               [{"id": "cancel", "label": "Cancel", "role": "cancel"},
                                {"id": "create", "label": "Create", "role": "default"}],
                               lambda _, values: self._made(live_from(values)), width=540, height=620)
        if form is not None:
            return form
        dialog = LiveFolderDialog(window, preset)
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            result = dialog.result_live()
            if result is not None:
                break
            window.toast("Fill in the address (and a token, if you chose one)")
        self._made(result)

    def _made(self, result):
        if result is None:
            return "Fill in the address (and a token, if you chose one)."  # (the form says so, and stays open)
        live, name, token = result
        if name:
            live["named"] = True
        name = name or (feed_name(live["url"]) if live.get("url") else "Live Folder")
        self.create_live_folder(live, name[:40], token)  # (as long as Edit keeps it: else cut there, later)
        return None

    # What a Live Folder follows: changed, its items are some other feed's, and go (the rest it keeps: what you've
    # seen, when it last looked)
    SOURCE_KEYS = ("kind", "url", "query", "items", "title", "link", "selector", "root", "auth", "header")

    def edit_live_folder(self, folder):
        """The folder's settings, in the New Live Folder form, filled in as they are; saved, it follows what's
        there now. (A token folder's token is kept unless a new one's typed.)"""
        window = self.window
        preset = {**folder.live, "name": folder.name if folder.live.get("named") else ""}
        had_token = folder.live.get("auth") == "token"
        form = forms.open_form(window, f"Edit “{folder.name}”", live_sections(preset),
                               [{"id": "cancel", "label": "Cancel", "role": "cancel"},
                                {"id": "save", "label": "Save", "role": "default"}],
                               lambda _, values: self._edited(folder, values, had_token), width=540, height=620)
        if form is not None:
            return form
        dialog = LiveFolderDialog(window, preset)
        dialog.setWindowTitle(f"Edit “{folder.name}”")
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            problem = self._edited(folder, dialog.values(), had_token)
            if problem is None:
                return None
            window.toast(problem)

    def _edited(self, folder, values, had_token=False):
        """The edit form's values put into `folder`: None, or what's missing (the form says so, and stays open)."""
        window = self.window
        if window.store.locate(folder) is None:
            return None  # (deleted while the form was open)
        values = dict(values)
        keep_token = had_token and values.get("json_auth") == "token" and not str(values.get("json_token") or "").strip()
        if keep_token:
            values["json_token"] = "kept"  # (the one in the Keychain stays: live_from only wants to see there's one)
        result = live_from(values)
        if result is None:
            return "Fill in the address (and a token, if you chose one)."
        live, name, token = result
        if keep_token:
            token = None
        old = folder.live
        same_source = all(source_value(old, k) == source_value(live, k) for k in self.SOURCE_KEYS)
        kept = {k: v for k, v in old.items() if k not in self.SOURCE_KEYS and k not in ("named", "refresh", "limit")} if same_source else {}
        folder.live = {**kept, **live}
        if name:
            folder.name = name[:40]
            folder.live["named"] = True
        if not same_source:
            # (another feed's items, not this one's: the old ones go, and the refresh fills it again)
            current = window.current_tab
            on_screen = [current, *(window.store.split_of(current) or [])] if current is not None else []
            gone = list(walk_tabs(folder.children))
            for tab in gone:
                window.store.remove_from_split(tab)
                window._remove_view(tab)
            folder.children = []
            if current is not None and any(current is t for t in gone):
                window._activate_fallback()  # (you were reading one of them: not a blank page on a tab that's gone)
            elif any(t is s for t in gone for s in on_screen):
                window._relayout_split()
        self.folders.changed(folder)  # (a refresh still out answers for the old settings)
        if old.get("auth") == "token" and live.get("auth") != "token":
            keychain.delete(token_account(folder))  # (signing in with cookies now: the token goes from the Keychain)
        if token and not self.set_token(folder, token):
            window.toast("Couldn't keep the token in the Keychain: set it from the folder's menu")
        allow_here(folder)  # (set up here, now)
        window.store._changed()
        self._refresh(folder)
        return None

    def create_live_folder(self, live, name, token=None):
        window = self.window
        folder = window.store.add_folder(window.store.active_space, name)
        folder.live = live
        allow_here(folder)  # (set up here: it may run git in its folder, or send its token to its site)
        if token and not self.set_token(folder, token):  # into the Keychain, never the session file
            window.toast("Couldn't keep the token in the Keychain: set it from the folder's menu")
        window.store._changed()
        window.toast(f"Filling “{name}”…")
        self._refresh(folder)

    def add_github_folder(self):
        """One Live Folder for all your GitHub: reviews asked of you, your PRs, your issues, and a sub-folder
        for each of your repos with open issues or pull requests (read with the gh CLI)."""
        existing = next((f for f in self.folders.folders() if f.live.get("kind") == "github"
                         and f.live.get("query") == "overview"), None)
        if existing is not None:
            self.window.toast(f"You have one already: “{existing.name}”")
            return
        self.create_live_folder({"kind": "github", "query": "overview", "named": True}, "GitHub")

    def follow_releases(self, owner, repo):
        """A Live Folder of a GitHub repo's releases (from ⌘T on any page of the repo)."""
        self.create_live_folder({"kind": "feed", "url": f"https://github.com/{owner}/{repo}", "named": True},
                                f"{repo} Releases")

    def settings_pages(self):
        return [("live", "ph.lightning", "Live Folders", self.guide_page, "air")]

    def guide_page(self, settings):
        """Settings → Live Folders: what each kind follows and how to set one up."""
        import sys
        from PyQt6.QtWidgets import QPushButton
        intro = settings._row(
            "Folders that fill themselves", "A Live Folder sits with your pinned tabs and keeps itself up to date: it "
            "refreshes when it's made and every half hour (right-click it → Refresh to do it now), new items get a "
            "dot until you open them, and tabs you've opened from it stay open. It's made in the Space you're in.",
            None)
        rows = [intro]
        filled = {"install": "winget install GitHub.cli" if sys.platform == "win32" else "brew install gh",
                  "vault": "in Credential Manager" if sys.platform == "win32" else "in the Keychain"}
        for kind, title, says in KINDS:
            how, examples = GUIDE[kind]
            button = QPushButton("New…")
            # (on the browser window, in front: the new folder shows in its sidebar)
            button.clicked.connect(lambda _=False, k=kind: (self.window.bring_to_front(),
                                                            self.new_live_folder({"kind": k})))
            rows.append(settings._row(title, f"{says}\n\nHow: {how.format(**filled)}\n\nFor example: {examples}",
                                      button))
        note = settings._row(
            "From another computer", "Live Folders come along with Sync. Repo Radar folders, and ones that sign in "
            "with a token, wait until you choose “Use on This Computer…” from their menu, so another computer's "
            "settings can't run git in a folder here or send your token somewhere new.", None)
        return settings._stack(settings._card(rows), settings._card([note]))

    def commands(self):
        cmds = [("New Live Folder (Feed or GitHub)", "ph.rss", self.new_live_folder),
                ("Add GitHub Folder (PRs, Issues, Repos)", "ph.github-logo", self.add_github_folder)]
        tab = self.window.current_tab
        repo = github_repo(tab.url) if tab is not None else None
        if repo:
            cmds.append((f"Follow Releases of {repo[0]}/{repo[1]}", "ph.rss", lambda: self.follow_releases(*repo)))
        return cmds

    # --- Tokens (kept in the Keychain, never the session file) ---------------------------------------------

    def set_token(self, folder, token) -> bool:
        return bool(token) and keychain.store(token_account(folder), token)

    def _ask_token(self, folder):
        window = self.window
        site = QUrl(folder.live.get("url", "")).host() or "its site"
        def given(token):
            if not token.strip():
                return
            if self.set_token(folder, token.strip()):
                allow_here(folder)
                self._refresh(folder)
            else:
                window.toast("Couldn't keep the token in the Keychain")
        prompts.ask_text(window, "Set Token", f"Token for “{folder.name}”, sent to {site} (kept in the Keychain):",
                         given, action="Save", secret=True)

    def folder_deleted(self, folder):
        if folder.live and folder.live.get("auth") == "token":
            keychain.delete(token_account(folder))  # nothing left behind in the Keychain

    # --- Seen and unseen ---------------------------------------------------------------------------------

    def tab_shown(self, tab):
        if tab is None or not tab.fresh:
            return
        window = self.window
        folder = window.store.live_folder_of(tab)  # opening a new item clears its dot
        if folder is not None:
            mark_seen(folder, tab)
            tab.changed.emit()
            window.sidebar.refresh_folder(folder)
            holder = window.store.folder_of(tab)
            if holder is not folder:  # (and the count on the sub-folder it's in)
                window.sidebar.refresh_folder(holder)
            window.store.schedule_save()

    def _mark_seen(self, live, folder):
        for tab in walk_tabs(folder.children):
            if tab.fresh:
                mark_seen(live, tab)
                tab.changed.emit()
        self.window.store._changed()

    # --- Their menus -------------------------------------------------------------------------------------

    def own_folder_menu(self, folder, pos):
        """One of a Live Folder's sub-folders: it's the Live Folder's to change."""
        window = self.window
        parent = window.store.live_folder_of(folder)
        if parent is None:
            return None
        menu = window._menu()
        window._act(menu, "ph.folder" if folder.expanded else "ph.folder-open",
                    "Collapse" if folder.expanded else "Expand", lambda: window.toggle_folder(folder))
        window._act(menu, "ph.list-bullets", "Copy Links", lambda: window.copy_links(walk_tabs(folder.children)))
        window._act(menu, "ph.checks", "Mark All as Seen", lambda: self._mark_seen(parent, folder))
        window._act(menu, "ph.arrows-clockwise", f"Refresh “{parent.name}”", lambda: self._refresh(parent))
        menu.exec(pos)
        return True

    def folder_menu(self, menu, folder):
        if not folder.live:
            return
        window = self.window
        window._act(menu, "ph.pencil-simple", "Edit Live Folder…", lambda: self.edit_live_folder(folder))
        window._act(menu, "ph.arrows-clockwise", "Refresh Now", lambda: self._refresh(folder))
        window._act(menu, "ph.checks", "Mark All as Seen", lambda: self._mark_seen(folder, folder))
        if folder.live.get("auth") == "token":
            window._act(menu, "ph.key", "Set Token…", lambda: self._ask_token(folder))
        if not allowed_here(folder):
            window._act(menu, "ph.check-circle", "Use on This Computer…", lambda: self._use_here(folder))

    def _use_here(self, folder):
        """A folder set up on another computer (through Sync) that runs git or sends a token: only once you say."""
        live = folder.live
        if live.get("kind") == "repos":
            question = (f"Look for changes in the git repos in {live.get('root') or DEFAULT_ROOT}?\n\nA repo's own "
                        "settings can make git run programs on this computer, so only say yes to a folder you trust.")
        else:
            question = (f"Send this folder's token from the Keychain to {QUrl(live.get('url', '')).host()}?\n\n"
                        "It was set up, or changed, on another computer.")
        def yes():
            allow_here(folder)
            self._refresh(folder)
        prompts.confirm(self.window, f"Use “{folder.name}” on This Computer", question, yes, action="Use Here")


FEATURE = LiveFoldersFeature
