# Web panels (Vivaldi's, and Zen's): sites kept open in a slim panel beside the page, whatever tab you're on: a chat,
# music, a to-do list. Right-click a tab → Open in Web Panel adds its site; the strip of site icons down the panel's
# outer edge flips between them (each stays loaded once you've been to it), + adds the current tab's site, and
# right-click an icon to remove it. (A strip down the side rather than a header along the top, so its page lines up
# with the page beside it.) ⌥⌘B (or ⌘T → Toggle Web Panel) shows or hides it. One panel, on the far side of the page
# from the sidebar, where Ask About This Page goes too. It keeps its sites (wherever you've gone in each), which one
# was showing, and whether it was open, across restarts. They aren't tabs: never archived or put to sleep, and the
# header's arrow moves the one showing into a tab.

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QSize, Qt, QUrl
from PyQt6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QMenu, QStackedLayout, QToolButton, QVBoxLayout

from . import motion
from .content import RADIUS, Corner
from .features import Feature
from .models import display_host
from .sidebar import IconButton, repolish
from .storage import favicon
from .theme import legible, letter_icon

SETTING = "web_panel"  # {"sites": what each is showing, "current": which is out, "open": whether the panel is}
WIDTH = 380  # (its page, with the strip beside it)
MIN_WIDTH = 240  # (however narrow the window)
STRIP = 30
MAX_SITES = 8


class Site:
    """One of the panel's sites: where it is, and its page once it's been shown (made the first time)."""

    def __init__(self, url):
        self.url = url
        self.view = None
        self.button = None

    @property
    def where(self) -> str:
        return self.view.url().toString() if self.view is not None else self.url


class WebPanel(QFrame):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("WebPanel")
        self.setFixedWidth(WIDTH + STRIP + 6)
        browser.content_wrap.installEventFilter(self)  # (narrower in a narrow window: fit)
        self.sites: list[Site] = []
        self.current: Site | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.pages = QStackedLayout()
        layout.addLayout(self.pages, 1)
        strip = QVBoxLayout()  # (on the window's edge side: see _dock)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(4)
        self.site_column = QVBoxLayout()
        self.site_column.setSpacing(4)
        strip.addLayout(self.site_column)
        self.add_btn = IconButton("ph.plus", "Add This Tab's Site", size=STRIP - 4, icon_size=14)
        self.add_btn.clicked.connect(self.add_current_tab)
        strip.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        strip.addStretch(1)
        self.as_tab_btn = IconButton("ph.arrow-square-out", "Move to a Tab", size=STRIP - 4, icon_size=14)
        self.as_tab_btn.clicked.connect(self.open_as_tab)
        self.close_btn = IconButton("ph.x", "Close Web Panel (⌥⌘B)", size=STRIP - 4, icon_size=14)
        self.close_btn.clicked.connect(self.close_panel)
        strip.addWidget(self.as_tab_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        strip.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(strip)
        self.strip = strip
        # Rounded like the page beside it (a page can't be clipped: the window's colours over its corners)
        self.corners = [Corner(self.browser, which, self) for which in ("tl", "tr", "bl", "br")]
        for corner in self.corners:
            corner.hide()
        self.restyle()
        self.hide()

    @property
    def url(self) -> str:
        return self.current.where if self.current is not None else ""

    def load(self, saved):
        """The sites kept from last time (not loaded till they're shown)."""
        urls = saved.get("sites") or ([saved["url"]] if saved.get("url") else [])  # (before there were several)
        for url in urls[:MAX_SITES]:
            self._add(Site(url))
        if self.sites:
            self.current = self.sites[min(max(saved.get("current", 0), 0), len(self.sites) - 1)]

    def open(self, url: str):
        """Show `url` in the panel: its site, if the panel has it already (and the panel, if it's put away)."""
        site = self._site_for(url) if url else self.current
        if site is None:
            if len(self.sites) >= MAX_SITES:
                self.browser.toast(f"The web panel holds {MAX_SITES} sites: right-click one to remove it first")
                return
            site = self._add(Site(url))
        self.show_site(site)
        self._dock()

    def _site_for(self, url):
        host = display_host(url)
        return next((s for s in self.sites if display_host(s.where) == host), None)

    def _add(self, site):
        self.sites.append(site)
        button = QToolButton()
        button.setObjectName("WebPanelSite")
        button.setFixedSize(STRIP, STRIP)
        button.setIconSize(QSize(16, 16))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self.show_site(site))
        button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda at: self._site_menu(site, button.mapToGlobal(at)))
        site.button = button
        self.site_column.addWidget(button)
        self._dress(site)
        return site

    def show_site(self, site):
        first = site.view is None
        if first:
            site.view = self.browser.create_view()
            site.view.titleChanged.connect(lambda _title: self._dress(site))
            site.view.urlChanged.connect(lambda _url: (self._named(site), self._save()))
            site.view.iconChanged.connect(lambda _icon: self._dress(site))
            site.view.installEventFilter(self)  # (its corners go where it's laid out)
            self.pages.addWidget(site.view)
            site.view.load(QUrl(site.url))

        def change():
            self.current = site
            self.pages.setCurrentWidget(site.view)
            for each in self.sites:
                repolish(each.button, selected=each is site)
            self._named(site)
        if site is not self.current and self.isVisible():
            motion.crossfade(self.browser.centralWidget(), self, change)  # (flipping between sites, or to a new one)
        else:
            change()
        self._place_corners()
        self._save()

    def _dock(self):
        """Beside the page, on the far side from the sidebar (after anything else there, Ask About This Page): the
        page area crossfades as it makes room, as it does for Split View."""
        if self.isVisible():
            return

        def change():
            self.place()
            self.show()
        motion.crossfade(self.browser.centralWidget(), self.browser.content_wrap, change)
        self._save()

    def place(self):
        """On the far side of the page from the sidebar, its strip on the window's edge and its page next to the
        page."""
        layout = self.browser.content_layout
        layout.removeWidget(self)
        left = self.browser.sidebar_on_right
        layout.insertWidget(0 if left else layout.count(), self)
        self.layout().setDirection(QBoxLayout.Direction.RightToLeft if left else QBoxLayout.Direction.LeftToRight)
        self.fit()

    def fit(self):
        """Its width: WIDTH, or less in a narrow window, so the page beside it keeps the larger share."""
        room = self.browser.content_wrap.width() - STRIP - 6 - 16
        self.setFixedWidth(max(MIN_WIDTH, min(WIDTH, round(room * 0.4))) + STRIP + 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_corners()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move) and self.current is not None and obj is self.current.view:
            self._place_corners()
        elif event.type() == QEvent.Type.Resize and obj is self.browser.content_wrap:
            self.fit()
        return False

    def _place_corners(self):
        if self.current is None or self.current.view is None:
            return
        area = self.current.view.geometry()
        for corner in self.corners:
            x = area.left() if corner.which[1] == "l" else area.right() - RADIUS + 1
            y = area.top() if corner.which[0] == "t" else area.bottom() - RADIUS + 1
            corner.move(x, y)
            corner.show()
            corner.raise_()

    def toggle(self):
        if self.isVisible():
            self.close_panel()
        elif self.current is not None:
            self.open("")
        elif self.browser.current_tab and self.browser.current_tab.url.startswith(("http://", "https://")):
            self.open(self.browser.current_tab.url)
        else:
            self.browser.toast("Right-click a tab → Open in Web Panel to keep its site here")

    def add_current_tab(self):
        tab = self.browser.current_tab
        if tab is None or not tab.url.startswith(("http://", "https://")):
            self.browser.toast("Open a site in a tab to add it here")
            return
        self.open(tab.url)

    def close_panel(self):
        if self.isVisible():
            motion.crossfade(self.browser.centralWidget(), self.browser.content_wrap, self.hide)
        self._save()

    def open_as_tab(self):
        """The site showing, moved into a tab of its own (and out of the panel)."""
        site = self.current
        if site is None:
            return
        self.browser.open_url(site.where)
        self.remove(site)

    def remove(self, site):
        index = self.sites.index(site)
        if site is not self.current and self.isVisible():  # (its icon fading out of the strip, not vanishing)
            motion.crossfade(self.browser.centralWidget(), self, lambda: self._remove(site, index))
        else:
            self._remove(site, index)

    def _remove(self, site, index):
        self.sites.remove(site)
        site.button.deleteLater()
        if site.view is not None:
            self.pages.removeWidget(site.view)
            site.view.deleteLater()
        if site is self.current:
            self.current = None
            if self.sites:
                self.show_site(self.sites[min(index, len(self.sites) - 1)])
            else:
                for corner in self.corners:
                    corner.hide()
                self.close_panel()
        self._save()

    def _site_menu(self, site, at):
        menu = QMenu(self)
        self.browser._act(menu, "ph.arrow-square-out", "Move to a Tab",
                          lambda: (self.show_site(site), self.open_as_tab()))
        self.browser._act(menu, "ph.arrow-clockwise", "Reload", lambda: site.view and site.view.reload())
        menu.addSeparator()
        self.browser._act(menu, "ph.x", "Remove from Web Panel", lambda: self.remove(site))
        menu.exec(at)

    def _named(self, site):
        title = site.view.title() if site.view is not None else ""
        host = display_host(site.where)
        name = title if title and title != site.where else host
        site.button.setToolTip(name)
        site.button.setAccessibleName(name)

    def _dress(self, site):
        """Its button's icon: the site's own, as tabs show it, or its first letter."""
        theme = self.browser.theme
        icon = site.view.icon() if site.view is not None else None
        if icon is None or icon.isNull():
            icon = favicon(site.where)
        host = display_host(site.where)
        # (on an address, 127.0.0.1 or localhost, its title's first letter says more, as for tabs)
        if not host or host[0].isdigit() or host.startswith(("localhost", "[", ":")):
            host = site.view.title() if site.view is not None and site.view.title() else host
        site.button.setIcon(legible(icon, theme) if icon is not None and not icon.isNull()
                            else letter_icon(host, theme.accent))
        self._named(site)

    def _save(self):
        self.browser.store.settings[SETTING] = {
            "sites": [s.where for s in self.sites],
            "current": self.sites.index(self.current) if self.current in self.sites else 0,
            "open": not self.isHidden()}  # (as it's meant to be: not isVisible(), false while the window's away)
        self.browser.store.schedule_save()

    def restyle(self):
        text = self.browser.theme.text
        for button in (self.add_btn, self.as_tab_btn, self.close_btn):
            button.set_color(text)
        for site in self.sites:
            self._dress(site)
        for corner in self.corners:
            corner.update()


class WebPanelFeature(Feature):
    name = "web_panel"
    panel = None

    def ready(self):
        self.panel = WebPanel(self.window, self.window.content_wrap)
        saved = self.window.store.settings.get(SETTING) or {}
        self.panel.load(saved)
        if saved.get("open") and self.panel.current is not None:
            self.panel.open("")  # (as you left it)

    def toggle(self):
        self.panel.toggle()

    def theme_changed(self):
        if self.panel is not None:
            self.panel.restyle()

    def sidebar_side_changed(self):
        if self.panel is not None and self.panel.isVisible():
            self.panel.place()

    def shutdown(self):
        # Its pages go before their profile does, as the tabs' do (MainWindow.shutdown)
        for site in self.panel.sites if self.panel is not None else []:
            if site.view is not None and not sip.isdeleted(site.view):
                sip.delete(site.view)
            site.view = None

    def tab_menu(self, menu, tab, kind):
        if tab.url.startswith(("http://", "https://")):
            self.window._act(menu, "ph.columns", "Open in Web Panel", lambda: self.panel.open(tab.url))

    def commands(self):
        return [("Toggle Web Panel", "ph.columns", self.toggle)]

    def menus(self, add, menus, bar):
        add(menus["View"], "Toggle Web Panel", "Ctrl+Alt+B", self.toggle)


FEATURE = WebPanelFeature
