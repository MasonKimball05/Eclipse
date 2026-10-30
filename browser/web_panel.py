# Web panels (Vivaldi's, and early Zen's): a site kept open in a slim panel beside the page, whatever tab you're on: a
# chat, music, a to-do list. Right-click a tab → Open in Web Panel puts its site there; ⌥⌘B (or ⌘T → Toggle Web
# Panel) shows or hides it. One panel, on the far side of the page from the sidebar, where Ask About This Page goes
# too. It keeps its site (wherever you've gone in it) and whether it was open, across restarts. It isn't a tab:
# it's never archived or put to sleep, and its header's arrow opens what it's showing as one.

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from . import motion
from .content import RADIUS, Corner
from .features import Feature
from .models import display_host
from .sidebar import IconButton

SETTING = "web_panel"  # {"url": what it's showing, "open": whether it's out}
WIDTH = 380


class WebPanel(QFrame):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("WebPanel")
        self.setFixedWidth(WIDTH)
        self.view = None  # (made the first time it opens)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 2, 0)
        self.title = QLabel()
        self.title.setObjectName("WebPanelTitle")
        self.as_tab_btn = IconButton("ph.arrow-square-out", "Open as a Tab", size=24, icon_size=14)
        self.as_tab_btn.clicked.connect(self.open_as_tab)
        self.close_btn = IconButton("ph.x", "Close Web Panel (⌥⌘B)", size=24, icon_size=14)
        self.close_btn.clicked.connect(self.close_panel)
        header.addWidget(self.title, 1)
        header.addWidget(self.as_tab_btn)
        header.addWidget(self.close_btn)
        layout.addLayout(header)
        self.body = layout
        self.restyle()
        self.hide()

    @property
    def url(self) -> str:
        return self.view.url().toString() if self.view is not None else ""

    def open(self, url: str):
        """Show `url` in the panel (and the panel, if it's put away)."""
        if self.view is None:
            self.view = self.browser.create_view()
            self.view.titleChanged.connect(lambda _title: self._name())
            self.view.urlChanged.connect(lambda _url: (self._name(), self._save()))
            self.body.addWidget(self.view, 1)
            # Rounded like the page beside it (a page can't be clipped: the window's colours over its corners)
            self.corners = [Corner(self.browser, which, self) for which in ("tl", "tr", "bl", "br")]
        if url and url != self.url:
            self.view.load(QUrl(url))
        self._dock()
        self._name()
        self._save()

    def _dock(self):
        """Beside the page, on the far side from the sidebar (after anything else there, Ask About This Page): the
        page area crossfades as it makes room, as it does for Split View."""
        if self.isVisible():
            return

        def change():
            layout = self.browser.content_layout
            layout.removeWidget(self)
            layout.insertWidget(0 if self.browser.sidebar_on_right else layout.count(), self)
            self.show()
        motion.crossfade(self.browser.centralWidget(), self.browser.content_wrap, change)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.view is not None:
            area = self.view.geometry()
            for corner in self.corners:
                x = area.left() if corner.which[1] == "l" else area.right() - RADIUS + 1
                y = area.top() if corner.which[0] == "t" else area.bottom() - RADIUS + 1
                corner.move(x, y)
                corner.raise_()

    def toggle(self):
        if self.isVisible():
            self.close_panel()
        else:
            saved = self.browser.store.settings.get(SETTING) or {}
            url = saved.get("url") or (self.browser.current_tab.url if self.browser.current_tab else "")
            if not url:
                self.browser.toast("Right-click a tab → Open in Web Panel to keep its site here")
                return
            self.open(url)

    def close_panel(self):
        if self.isVisible():
            motion.crossfade(self.browser.centralWidget(), self.browser.content_wrap, self.hide)
        self._save()

    def open_as_tab(self):
        if self.url:
            self.browser.open_url(self.url)
        self.close_panel()

    def _name(self):
        title = self.view.title() if self.view is not None else ""
        host = display_host(self.url)
        self.title.setText(title if title and title != self.url else host)
        self.title.setToolTip(self.url)

    def _save(self):
        self.browser.store.settings[SETTING] = {"url": self.url, "open": self.isVisible()}
        self.browser.store.schedule_save()

    def restyle(self):
        text = self.browser.theme.text
        for button in (self.as_tab_btn, self.close_btn):
            button.set_color(text)
        for corner in getattr(self, "corners", []):
            corner.update()


class WebPanelFeature(Feature):
    name = "web_panel"
    panel = None

    def ready(self):
        self.panel = WebPanel(self.window, self.window.content_wrap)
        saved = self.window.store.settings.get(SETTING) or {}
        if saved.get("open") and saved.get("url"):
            self.panel.open(saved["url"])  # (as you left it)

    def toggle(self):
        self.panel.toggle()

    def theme_changed(self):
        if self.panel is not None:
            self.panel.restyle()

    def tab_menu(self, menu, tab, kind):
        if tab.url.startswith(("http://", "https://")):
            self.window._act(menu, "ph.columns", "Open in Web Panel", lambda: self.panel.open(tab.url))

    def commands(self):
        return [("Toggle Web Panel", "ph.columns", self.toggle)]

    def menus(self, add, menus, bar):
        add(menus["View"], "Toggle Web Panel", "Ctrl+Alt+B", self.toggle)


FEATURE = WebPanelFeature
