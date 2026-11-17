# The site you're on: Site Settings, the shield's menu, ad blocking and dark pages.
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView

from . import dark_pages, native
from .models import display_host
from .permissions import KINDS, origin_key


class SiteControls:
    def apply_dark_pages(self):
        """Dark websites turned on or off (or a site changed): every open page, at once (no reload)."""
        views = [t.view for t in self.store.all_tabs()]
        for window in self.private.windows:  # (private windows' tabs, and Little windows' pages, too)
            views += list(getattr(window, "views", []))
        views += [getattr(window, "view", None) for window in self._extra_windows]
        # (and the web panel's sites and a Peek: left out, they kept the old look till they next loaded a page)
        panel = getattr(self.feature("web_panel"), "panel", None) if self.feature("web_panel") else None
        views += [site.view for site in getattr(panel, "sites", [])]
        views.append(getattr(self.peek, "view", None))
        for view in views:
            if isinstance(view, QWebEngineView) and not sip.isdeleted(view):
                dark_pages.apply(view.page(), self.store.settings)

    def _set_dark_here(self, host, dark):
        dark_pages.set_skipped(self.store.settings, host, not dark)
        self.store.schedule_save()
        self.apply_dark_pages()

    def show_site_settings(self, pos=None, tab=None):
        """Site Settings for the site you're on (or `tab`'s): the native popover beside the address pill, or the
        shield's menu where native pages aren't built, or for a page that isn't a website."""
        from . import native, site_settings
        tab = tab or self.current_tab
        if tab is not None and tab is not self.current_tab:
            self.activate_tab(tab)  # (beside its page, the one it's about)
        if native.available() and site_settings.open_for(self, tab) is not None:
            return
        if pos is None:
            shield = self.sidebar.shield_btn
            pos = shield.mapToGlobal(QPoint(0, shield.height()))
        self.show_adblock_menu(pos)

    def show_adblock_menu(self, pos):
        tab = self.current_tab
        host = QUrl(tab.url).host() if tab else ""
        blocker = self.adblock
        menu = self._menu()
        if host and self.store.settings.get(dark_pages.SETTING, False):
            dark = menu.addAction(f"Dark Pages on {display_host(tab.url)}")
            dark.setCheckable(True)
            dark.setChecked(dark_pages.wanted(self.store.settings, tab.url))
            dark.triggered.connect(lambda on: self._set_dark_here(host, on))
        if host:
            self._site_permissions_menu(menu, tab)
            count = blocker.count_for(tab.url)
            site = display_host(tab.url)
            header = menu.addAction(f"{count:,} ad{'s' if count != 1 else ''} & tracker{'s' if count != 1 else ''} "
                                    f"blocked on {site}" if count else f"Nothing blocked on {site} yet")
            header.setEnabled(False)
            here = menu.addAction(f"Block on {display_host(tab.url)}")
            here.setCheckable(True)
            here.setChecked(not blocker.is_allowed(host))
            here.setEnabled(blocker.enabled)
            here.triggered.connect(lambda on: self._allow_site(host, not on))
            self.emit("site_menu", menu, tab)  # (the Privacy Report…)
            menu.addSeparator()
        everywhere = menu.addAction("Block Ads & Trackers")
        everywhere.setCheckable(True)
        everywhere.setChecked(blocker.enabled)
        everywhere.triggered.connect(lambda _=False: self.toggle_adblock())
        if host and self.page_index.enabled:
            self._act(menu, "ph.eye-slash", f"Keep {display_host(tab.url)} Out of Page Search",
                      lambda: (self.page_index.skip_site(host),
                               self.toast(f"Pages on {display_host(tab.url)} won't be searchable by their text")))
        banners = menu.addAction("Reject Cookie Banners")
        banners.setCheckable(True)
        banners.setChecked(blocker.reject_cookies)
        banners.triggered.connect(lambda on: (blocker.set_reject_cookies(on),
                                              self.toast("Cookie banners: rejected for you" if on else
                                                         "Cookie banners: left for you to answer")))
        info = menu.addAction(f"{len(blocker.domains):,} domains on the block lists")
        info.setEnabled(False)
        self._act(menu, "ph.arrows-clockwise", "Update Block Lists Now", lambda: (blocker.update_lists(force=True),
                                                          self.toast("Updating block lists…")))
        menu.exec(pos)

    def _site_permissions_menu(self, menu, tab):
        """Site Controls: what this site may use, changeable here."""
        view = tab.view if isinstance(tab.view, QWebEngineView) else None
        origin = origin_key(QUrl(tab.url))
        if view is None or not origin:
            return
        profile = view.page().profile()
        rules = self.permissions.rules_for(profile, origin)
        header = menu.addAction(f"Site Controls · {display_host(tab.url)}")
        header.setEnabled(False)
        if not rules:
            none = menu.addAction("This site hasn't asked for any permissions")
            none.setEnabled(False)
        for label, kind, value in rules:
            sub = self._submenu(menu, KINDS[kind][1], f"{label[0].upper()}{label[1:]}: "
                                f"{'Allowed' if value == 'allow' else 'Blocked'}")
            for text, choice in (("Allow", "allow"), ("Block", "block"), ("Ask Next Time", None)):
                action = sub.addAction(text, lambda c=choice, k=kind: self.permissions.set_rule(profile, origin, k, c))
                action.setCheckable(True)
                action.setChecked(choice == value)
        if rules:
            self._act(menu, "ph.arrow-counter-clockwise", "Reset Permissions for This Site",
                      lambda: (self.permissions.reset_site(profile, origin), self.toast("Permissions reset")))
        menu.addSeparator()

    def _allow_site(self, host, allowed):
        self.adblock.set_allowed(host, allowed)
        self.sidebar.update_shield()
        self.reload()  # the page has already loaded without (or with) its ads
        self.toast(f"Ads allowed on {host.removeprefix('www.')}" if allowed else f"Blocking ads on {host.removeprefix('www.')}")

    def toggle_adblock(self):
        enabled = not self.adblock.enabled
        self.adblock.set_enabled(enabled)
        self.adblock_action.setChecked(enabled)
        self.sidebar.update_shield()
        self.toast("Ad blocking on" if enabled else "Ad blocking off")

    def allow_ads_here(self):
        if self.current_tab and QUrl(self.current_tab.url).host():
            host = QUrl(self.current_tab.url).host()
            self._allow_site(host, not self.adblock.is_allowed(host))
