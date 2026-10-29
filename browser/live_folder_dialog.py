# The New Live Folder dialog: what a folder follows, from a feed or GitHub to your own apps'
# APIs and pages.

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QRadioButton, QStackedWidget, QVBoxLayout, QWidget)

from . import keychain
from .features import Feature
from .live_folders import (DEFAULT_ROOT, GITHUB, LiveFolders, allow_here, allowed_here, feed_name, github_repo,
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
        if kind == "feed":
            self.feed_url.setText(preset.get("url", ""))
        elif kind == "json":
            self.json_url.setText(preset.get("url", ""))
            self.json_items.setText(preset.get("items", ""))
            self.json_title.setText(preset.get("title", "title"))
            self.json_link.setText(preset.get("link", "url"))
            self.json_auth.setCurrentIndex(1 if preset.get("auth") == "token" else 0)
            self.json_header.setText(preset.get("header", "Authorization"))
        elif kind == "page":
            self.page_url.setText(preset.get("url", ""))
            self.page_selector.setText(preset.get("selector", ""))
        elif kind == "calendar":
            self.calendar_url.setText(preset.get("url", ""))
        elif kind == "repos":
            self.repos_root.setText(preset.get("root", "") or "~/projects/GitHub")

    def result_live(self) -> tuple[dict, str, str | None] | None:
        """(live settings, folder name, token to keep in the Keychain), or None if something's missing."""
        kind = KINDS[self.kind_group.checkedId()][0]
        name = self.name.text().strip()
        if kind == "feed":
            url = self.feed_url.text().strip()
            if not url:
                return None
            return {"kind": "feed", "url": url if "://" in url else "https://" + url}, name, None
        if kind == "github":
            query = self.github_query.currentData()
            return {"kind": "github", "query": query, "named": True}, name or GITHUB[query][0], None
        if kind == "json":
            url = self.json_url.text().strip()
            if not url.startswith(("http://", "https://")):
                return None
            live = {"kind": "json", "url": url, "items": self.json_items.text().strip(),
                    "title": self.json_title.text().strip() or "title", "link": self.json_link.text().strip() or "url",
                    "auth": self.json_auth.currentData()}
            token = None
            if live["auth"] == "token":
                live["header"] = self.json_header.text().strip() or "Authorization"
                token = self.json_token.text().strip() or None
                if token is None:
                    return None
            return live, name, token
        if kind == "calendar":
            url = self.calendar_url.text().strip().replace("webcal://", "https://")
            return ({"kind": "calendar", "url": url}, name, None) if url.startswith(("http://", "https://")) else None
        if kind == "repos":
            return {"kind": "repos", "root": self.repos_root.text().strip()}, name or "Repo Radar", None
        url, selector = self.page_url.text().strip(), self.page_selector.text().strip()
        if not url.startswith(("http://", "https://")) or not selector:
            return None
        return {"kind": "page", "url": url, "selector": selector}, name, None


class LiveFoldersFeature(Feature):
    """Live Folders: pinned folders that fill themselves from a feed or GitHub, refreshed every half hour, new
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
        dialog = LiveFolderDialog(window, preset)
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            result = dialog.result_live()
            if result is not None:
                break
            window.toast("Fill in the address (and a token, if you chose one)")
        live, name, token = result
        if name:
            live["named"] = True
        self.create_live_folder(live, name or (feed_name(live["url"]) if live.get("url") else "Live Folder"), token)

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
            button.clicked.connect(lambda _=False, k=kind: self.new_live_folder({"kind": k}))
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
        token, ok = QInputDialog.getText(window, "Set Token", f"Token for “{folder.name}”, sent to {site} "
                                         "(kept in the Keychain):", QLineEdit.EchoMode.Password)
        if not ok or not token.strip():
            return
        if self.set_token(folder, token.strip()):
            allow_here(folder)
            self._refresh(folder)
        else:
            window.toast("Couldn't keep the token in the Keychain")

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
        if QMessageBox.question(self.window, f"Use “{folder.name}” on This Computer", question) == \
                QMessageBox.StandardButton.Yes:
            allow_here(folder)
            self._refresh(folder)


FEATURE = LiveFoldersFeature
