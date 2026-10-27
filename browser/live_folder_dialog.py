# The New Live Folder dialog: what a folder follows, from a feed or GitHub to your own apps'
# APIs and pages.

from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                             QRadioButton, QStackedWidget, QVBoxLayout, QWidget)

from .live_folders import GITHUB
from .theme import FittedLabel, stylesheet

KINDS = [
    ("feed", "A feed", "RSS or Atom, or any site that has one. A GitHub repo: its releases."),
    ("github", "GitHub", "Pull requests and issues, with the GitHub CLI you're signed in to."),
    ("json", "An app's API", "Any JSON list: your own apps, a dashboard's API."),
    ("page", "Links on a page", "The links on a page that match a CSS selector, read with your logins."),
    ("calendar", "A calendar", "Upcoming events from an .ics feed: deadlines, chapter events."),
    ("repos", "Repo Radar", "Your local git repos with uncommitted or unpushed work."),
]


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
