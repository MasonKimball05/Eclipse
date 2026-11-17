# Focus sessions: "Focus for 50 minutes in Work". Start one from ⌘T (Start Focus Session…) or a Space's menu (Focus…):
# pick how long, and which sites to keep away (the Space's own blocked sites from Space Rules, or the usual
# distractions). Until the time's up, those sites show a quiet "you're focusing" page in every Space (Open Anyway lets
# one through for 10 minutes, as Space Rules' does), and a small card at the bottom of the sidebar counts down, with
# End to stop early. When it's over, a toast says so and everything's back as it was. A session outlasts a restart.

import html
import time

import qtawesome as qta
from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QInputDialog, QLabel, QPushButton, QVBoxLayout

from . import forms
from .features import Feature
from .webview import WebPage

SETTING = "focus_session"  # ({"until": time, "space": id, "minutes": n, "blocked": [sites]} while one's on)
LAST_SITES = "focus_sites"  # (the sites a session last kept away: offered again next time)
LENGTHS = [25, 50, 90, 120]  # minutes
DISTRACTIONS = ["youtube.com", "x.com", "twitter.com", "reddit.com", "instagram.com", "facebook.com", "tiktok.com",
                "news.ycombinator.com", "netflix.com", "twitch.tv"]
ALLOW_FOR = 10 * 60  # seconds "Open Anyway" lets a blocked site through
TICK_MS = 15 * 1000


def blocked(sites: list[str], host: str) -> bool:
    host = host.lower().removeprefix("www.")
    return bool(host) and any(host == site or host.endswith("." + site) for site in sites)


def left_text(seconds: float) -> str:
    minutes = max(0, round(seconds / 60))
    if minutes >= 60:
        return f"{minutes // 60} h {minutes % 60:02d} min left" if minutes % 60 else f"{minutes // 60} h left"
    return f"{minutes} min left" if minutes >= 1 else "less than a minute left"


class FocusCard(QFrame):
    """At the bottom of the sidebar while a session's on: where, how long's left, and End."""

    def __init__(self, feature, parent=None):
        super().__init__(parent)
        self.feature = feature
        self.setObjectName("NowPlaying")  # (the same quiet card as Now Playing and Up Next)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 6, 7)
        row.setSpacing(9)
        self.icon = QLabel()
        self.icon.setFixedSize(QSize(18, 18))
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("NowPlayingTitle")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.left = QLabel()
        self.left.setObjectName("NowPlayingSub")
        self.left.setTextFormat(Qt.TextFormat.PlainText)
        text.addWidget(self.title)
        text.addWidget(self.left)
        self.end = QPushButton("End")
        self.end.setObjectName("UpNextJoin")
        self.end.setCursor(Qt.CursorShape.PointingHandCursor)
        self.end.clicked.connect(lambda: feature.end(early=True))
        row.addWidget(self.icon)
        row.addLayout(text, 1)
        row.addWidget(self.end)
        self.hide()

    def show_session(self, where: str, seconds_left: float, theme):
        self.icon.setPixmap(qta.icon("ph.timer", color=theme.accent).pixmap(18, 18))
        self.title.setText(f"Focusing in {where}" if where else "Focusing")
        self.left.setText(left_text(seconds_left))
        self.setToolTip("Distracting sites are kept away until then. End stops early.")
        self.show()


class FocusFeature(Feature):
    name = "focus"

    def __init__(self, window):
        super().__init__(window)
        self.card = None
        self.allowed = {}  # site -> until when "Open Anyway" lets it through
        self.ticker = QTimer(window, interval=TICK_MS, timeout=self.tick)
        WebPage.blockers.append(self._allow_navigation)

    @property
    def session(self) -> dict | None:
        found = self.window.store.settings.get(SETTING)
        return found if isinstance(found, dict) and found.get("until", 0) > time.time() else None

    def ready(self):
        if self.window.store.settings.get(SETTING) and self.session is None:
            self.window.store.settings.pop(SETTING, None)  # (ran out while Eclipse was closed)
        self.tick()

    def shutdown(self):
        self.ticker.stop()
        if self._allow_navigation in WebPage.blockers:
            WebPage.blockers.remove(self._allow_navigation)

    # --- Starting and ending ---------------------------------------------------------------------------------

    def default_sites(self, space) -> list[str]:
        rules = self.window.feature("space_rules")
        own = rules.rule(space).get("blocked", []) if rules is not None and space is not None else []
        return own or self.window.store.settings.get(LAST_SITES) or list(DISTRACTIONS)

    def ask(self, space=None):
        """The form: how long, and which sites to keep away; Start begins it."""
        from .space_rules import clean_sites
        space = space or self.window.store.active_space
        sites = self.default_sites(space)

        def answered(button, values):
            if button != "start":
                return
            try:
                minutes = int(values.get("minutes") or 50)
            except ValueError:
                minutes = 50
            self.start(minutes, clean_sites(str(values.get("sites", ""))), space)

        form = forms.open_form(
            self.window, "Focus", [forms.section(
                {"id": "minutes", "kind": "menu", "label": "Focus for", "value": "50", "style": "segmented",
                 "options": [{"value": str(m), "label": f"{m} min" if m < 60 else f"{m // 60} h"
                              if m % 60 == 0 else f"{m} min"} for m in LENGTHS]},
                {"id": "sites", "kind": "lines", "label": "Keep away", "value": "\n".join(sites), "height": 150},
                header=f"In {space.name}" if space else "",
                footer="One site a line. They show a quiet page instead, in every Space, until the time's up.")],
            [{"id": "cancel", "label": "Cancel", "role": "cancel"}, {"id": "start", "label": "Start", "role": "default"}],
            answered, width=440, height=420)
        if form is not None:
            return form
        labels = [f"{m} minutes" for m in LENGTHS]
        chosen, ok = QInputDialog.getItem(self.window, "Focus", "Focus for:", labels, 1, False)
        if ok:
            self.start(LENGTHS[labels.index(chosen)], sites, space)
        return None

    def start(self, minutes: int, sites: list[str], space=None):
        settings = self.window.store.settings
        space = space or self.window.store.active_space
        minutes = max(1, int(minutes))
        settings[SETTING] = {"until": time.time() + minutes * 60, "space": space.id if space else None,
                             "minutes": minutes, "blocked": list(sites)}
        if sites:
            settings[LAST_SITES] = list(sites)
        self.allowed.clear()
        self.window.store.schedule_save()
        if space is not None and space is not self.window.store.active_space:
            self.window.switch_space(space)
        # (a distracting page already open in front of you goes too)
        tab = self.window.current_tab
        view = getattr(tab, "view", None)
        if view is not None and hasattr(view, "page") and blocked(sites, view.url().host()):
            self._show_blocked(view.page(), view.url())
        self.tick()
        self.window.toast(f"Focusing for {minutes} minutes" + (f" in {space.name}" if space else ""),
                          lambda: self.end(early=True), "End")

    def end(self, early=False):
        session = self.window.store.settings.pop(SETTING, None)
        # Pages kept away come back (each still showed its quiet page, and reloading reloaded that)
        for tab in self.window.store.all_tabs():
            view = tab.view
            page = view.page() if view is not None and hasattr(view, "page") else None
            kept = getattr(page, "eclipse_blocked", None) if page is not None else None
            if kept and session and blocked(session.get("blocked", []), QUrl(kept).host()):
                page.load(QUrl(kept))
        self.window.store.schedule_save()
        self.allowed.clear()
        self.tick()
        if session:
            minutes = session.get("minutes", 0)
            self.window.toast("Focus session ended" if early else f"Focus session done · {minutes} minutes")
            if not early:
                from PyQt6.QtWidgets import QApplication
                QApplication.alert(self.window)  # (the Dock icon bounces once, if you're elsewhere)

    def tick(self):
        """Once in a while: the card's countdown, and the end when the time's up."""
        stored = self.window.store.settings.get(SETTING)
        session = self.session
        if stored and session is None:
            self.end()
            return
        if session is None:
            self.ticker.stop()
            if self.card is not None:
                self.card.hide()
            return
        if not self.ticker.isActive():
            self.ticker.start()
        left = session["until"] - time.time()
        if left < TICK_MS / 1000:  # (the end, on time, not up to one tick late)
            QTimer.singleShot(max(0, int(left * 1000)) + 50, self.tick)
        if self.card is not None:
            space = self.window.store.space(session.get("space")) if session.get("space") else None
            self.card.show_session(space.name if space else "", left, self.window.theme)

    # --- Keeping sites away ---------------------------------------------------------------------------------

    def _allow_navigation(self, page, url: QUrl) -> bool:
        if sip.isdeleted(self.window):
            return True  # (a window that's gone: nothing of its to keep a page from)
        session = self.session
        if session is None or url.scheme() not in ("http", "https") or not blocked(session["blocked"], url.host()):
            return True
        site = url.host().lower().removeprefix("www.")
        if self.allowed.get(site, 0) > time.time():
            return True
        QTimer.singleShot(0, lambda: self._show_blocked(page, url))
        return False

    def _show_blocked(self, page, url):
        session = self.session
        if session is None:
            return
        site = url.host().lower().removeprefix("www.")
        page.eclipse_blocked = url.toString()  # (the tab keeps this address, not the quiet page's: window._on_url)
        e = html.escape
        page.setHtml(
            "<html><body style='font: 15px -apple-system, sans-serif; display: flex; align-items: center; "
            "justify-content: center; height: 90vh; color: #888; text-align: center'><div>"
            f"<div style='font-size: 22px; color: #555; margin-bottom: 8px'>You're focusing</div>"
            f"{e(site)} is kept away for {e(left_text(session['until'] - time.time()))}.</div></body></html>")
        self.window.toast(f"{site} is kept away while you focus", lambda: self.open_anyway(page, url), "Open Anyway")

    def open_anyway(self, page, url):
        self.allowed[url.host().lower().removeprefix("www.")] = time.time() + ALLOW_FOR
        page.load(url)

    # --- Where it's started -----------------------------------------------------------------------------------

    def sidebar_cards(self):
        self.card = FocusCard(self)
        return [self.card]

    def theme_changed(self):
        self.tick()

    def commands(self):
        found = [("Start Focus Session…", "ph.timer", lambda: self.ask())]
        if self.session is not None:
            found.append(("End Focus Session", "ph.timer", lambda: self.end(early=True)))
        return found

    def space_actions(self, space) -> list:
        return [("ph.timer", "Focus…", lambda: self.ask(space))]


FEATURE = FocusFeature
