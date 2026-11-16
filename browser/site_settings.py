# Site Settings: everything Eclipse keeps about the site you're on, in one place. A popover beside the address pill
# (click its shield, ⌘T → Site Settings, or right-click a tab), changes applying as they're made: what the site may
# use (camera, microphone, location, notifications…), whether its ads are blocked, its zoom, dark pages, the apps it
# may open without asking, whether its pages are kept for page search, and clearing its cookies and data. Before, these
# were spread over the shield's menu, Settings' pages and the cards that first asked. Where native pages aren't built
# (Windows), the shield's menu stays.

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePermission

from . import dark_pages, external_links, page_index
from .models import display_host
from .permissions import KINDS, origin_key
from .zoom import ZOOM_STEPS, remember_zoom, zoom_for, zoom_key

T = QWebEnginePermission.PermissionType
# The ones every site is shown (what's asked about most), short names for the rows; others show once decided
SHOWN = [(T.MediaVideoCapture, "Camera"), (T.MediaAudioCapture, "Microphone"), (T.Geolocation, "Location"),
         (T.Notifications, "Notifications")]
SHORT = {T.MediaAudioVideoCapture: "Camera and microphone", T.DesktopVideoCapture: "Screen sharing",
         T.DesktopAudioVideoCapture: "Screen and audio sharing", T.ClipboardReadWrite: "Clipboard",
         T.LocalFontsAccess: "Fonts on this Mac", **dict(SHOWN)}
ANSWERS = [{"value": "ask", "label": "Ask"}, {"value": "allow", "label": "Allow"}, {"value": "block", "label": "Block"}]
APP_PREFIX = "app:"  # (an app link's toggle: "app:zoommtg")
COOKIE_SWEEP_MS = 300  # (a cookie the page has only just set: told to the ledger a moment after)


def site_of(tab) -> tuple[str, str]:
    """(host, origin) of a tab's page, or ("", "") for one that isn't a website."""
    url = QUrl(tab.url) if tab is not None and tab.url else QUrl()
    if url.scheme() not in ("http", "https") or not url.host():
        return "", ""
    return url.host(), origin_key(url)


def permission_rows(browser, profile, origin) -> list[tuple[object, str, str]]:
    """(permission type, its row's name, "ask" / "allow" / "block"): the usual four, then any other decided."""
    decided = {kind: value for _label, kind, value in browser.permissions.rules_for(profile, origin)}
    rows = [(kind, name, decided.get(kind, "ask")) for kind, name in SHOWN]
    rows += [(kind, SHORT.get(kind, KINDS[kind][0]), value) for kind, value in decided.items()
             if kind not in dict(SHOWN)]
    return rows


def describe(browser, tab) -> tuple[list[dict], dict]:
    """The popover's sections for `tab`'s site, and the values they start with (to tell what's changed)."""
    from . import forms
    host, origin = site_of(tab)
    view = tab.view
    profile = view.page().profile()
    settings = browser.store.settings
    private = profile.isOffTheRecord()
    values = {}
    permissions = []
    for kind, name, value in permission_rows(browser, profile, origin):
        key = f"perm:{kind.name}"
        values[key] = value
        permissions.append({"id": key, "kind": "menu", "label": name, "value": value, "options": ANSWERS})
    footer = "In a Private window, these last until it closes." if private else ""
    blocker = browser.adblock
    count = blocker.count_for(tab.url)
    values["ads"] = not blocker.is_allowed(host)
    content = [{"id": "ads", "kind": "toggle", "label": "Block ads & trackers", "value": values["ads"]}]
    if settings.get(dark_pages.SETTING, False):
        values["dark"] = dark_pages.wanted(settings, tab.url)
        content.append({"id": "dark", "kind": "toggle", "label": "Dark page", "value": values["dark"]})
    if zoom_key(tab.url) and not private:
        current = zoom_for(settings, tab.url)
        values["zoom"] = _zoom_value(current)
        content.append({"id": "zoom", "kind": "menu", "label": "Zoom", "value": values["zoom"],
                        "options": [{"value": _zoom_value(z), "label": f"{round(z * 100)}%"} for z in ZOOM_STEPS]})
    if browser.page_index.enabled:
        values["search"] = page_index.indexable(tab.url, page_index.skipped_sites(settings))
        content.append({"id": "search", "kind": "toggle", "label": "Keep pages for page search",
                        "value": values["search"]})
    blocked = (f"{count:,} ad{'s' if count != 1 else ''} and tracker{'s' if count != 1 else ''} blocked here"
               if count else "Nothing blocked here yet")
    apps = []
    for scheme in sorted(s for o, s in external_links.all_rules(settings) if o == origin):
        key = APP_PREFIX + scheme
        values[key] = True
        apps.append({"id": key, "kind": "toggle", "label": f"Open {scheme}: links", "value": True})
    data = [{"id": "clear", "kind": "action", "label": "", "title": "Clear Cookies & Site Data…"},
            {"id": "reset", "kind": "action", "label": "", "title": "Reset Permissions"},
            {"id": "passwords", "kind": "action", "label": "", "title": "Saved Passwords…"}]
    if browser.feature("privacy_report") is not None:
        data.append({"id": "report", "kind": "action", "label": "", "title": "Privacy Report…"})
    values["everywhere"] = blocker.enabled
    values["banners"] = blocker.reject_cookies
    everywhere = [{"id": "everywhere", "kind": "toggle", "label": "Block ads & trackers", "value": blocker.enabled},
                  {"id": "banners", "kind": "toggle", "label": "Reject cookie banners", "value": blocker.reject_cookies},
                  {"id": "lists", "kind": "action", "label": "", "title": "Update Block Lists Now"}]
    sections = [forms.section(*permissions, header="Permissions", footer=footer),
                forms.section(*content, footer=blocked)]
    if apps:
        sections.append(forms.section(*apps, header="Apps it may open without asking",
                                      footer="Turned off, it asks again next time."))
    sections += [forms.section(*data, header="Data"), forms.section(*everywhere, header="Every site")]
    return sections, values


def _zoom_value(factor: float) -> str:
    return f"{round(factor * 100)}"


def open_for(browser, tab=None, beside: QRect | None = None):
    """The popover for `tab`'s site (the current one), beside `beside` (global: the address pill, by default).
    Returns the form, or None where it can't be shown (no site, or no native pages: the shield's menu then)."""
    from . import forms
    tab = tab or browser.current_tab
    host, origin = site_of(tab)
    if not host or tab.view is None or not hasattr(tab.view, "page"):
        return None
    sections, values = describe(browser, tab)
    profile = tab.view.page().profile()
    started = dict(values)

    def changed(now):
        apply_changes(browser, tab, host, origin, profile, started, now)

    def act(form, field, _values):
        if field == "clear":
            form.close()
            confirm_clear(browser, tab, host, profile)
        elif field == "reset":
            browser.permissions.reset_site(profile, origin)
            browser.toast(f"Permissions reset for {display_host(tab.url)}")
            form.close()
        elif field == "passwords":
            form.close()
            browser.open_settings("passwords")
        elif field == "report":
            form.close()
            report = browser.feature("privacy_report")
            from .privacy_report import _site
            report.show(_site(host))
        elif field == "lists":
            browser.adblock.update_lists(force=True)
            browser.toast("Updating block lists…")

    if beside is None:
        pill = browser.sidebar.url_pill
        beside = QRect(pill.mapToGlobal(QPoint(0, 0)), pill.size()) if pill.isVisible() else None
    return forms.open_form(browser, f"Site Settings for {display_host(tab.url)}", sections, [], lambda *_: None,
                           heading=display_host(tab.url), width=360, height=620, on_change=changed, on_field=act,
                           popover_at=browser.mapToGlobal(QPoint(40, 40)) if beside is None else None,
                           popover_beside=beside)


def apply_changes(browser, tab, host, origin, profile, started: dict, now: dict):
    """Each value that's changed since the popover opened (or since the last change), applied as it's made."""
    settings = browser.store.settings
    reload = False
    for key, value in now.items():
        if started.get(key) == value or key not in started:
            continue
        started[key] = value
        if key.startswith("perm:"):
            kind = getattr(T, key.removeprefix("perm:"), None)
            if kind is not None:
                browser.permissions.set_rule(profile, origin, kind, None if value == "ask" else value)
        elif key == "ads":
            browser.adblock.set_allowed(host, not value)
            browser.sidebar.update_shield()
            reload = True  # (the page has loaded with, or without, its ads already)
        elif key == "dark":
            browser._set_dark_here(host, bool(value))
        elif key == "zoom":
            try:
                factor = int(value) / 100
            except (TypeError, ValueError):
                continue
            remember_zoom(settings, tab.url, factor)
            if tab.view is not None and not sip.isdeleted(tab.view):
                tab.view.setZoomFactor(factor)
            browser.sidebar.update_zoom()
        elif key == "search":
            if value:
                for entry in list(page_index.skipped_sites(settings)):
                    if page_index.matches_site(host, entry):
                        page_index.unskip(settings, entry)
            else:
                browser.page_index.skip_site(host)
        elif key.startswith(APP_PREFIX):
            external_links.set_allowed(settings, origin, key.removeprefix(APP_PREFIX), bool(value))
        elif key == "everywhere":
            if bool(value) != browser.adblock.enabled:
                browser.toggle_adblock()
        elif key == "banners":
            browser.adblock.set_reject_cookies(bool(value))
    browser.store.schedule_save()
    if reload and tab is browser.current_tab:
        browser.reload()


def confirm_clear(browser, tab, host, profile):
    from . import prompts
    site = display_host(tab.url)
    prompts.confirm(browser, "Clear Cookies & Site Data",
                    f"Sign out of {site} and clear what it keeps in this browser (cookies, stored data, its cache "
                    f"of offline pages)?", lambda: clear_site_data(browser, tab, host, profile),
                    action="Clear", destructive=True)


def same_site(cookie_domain: str, host: str) -> bool:
    """A cookie belongs to the site you're on: for its host, or a domain it's under ("…example.com" for
    www.example.com), or one of its subdomains (shop.example.com's, clearing example.com)."""
    domain = cookie_domain.lower().lstrip(".")
    host = host.lower()
    site = host.removeprefix("www.")
    return bool(domain) and (host == domain or host.endswith("." + domain) or domain == site
                             or domain.endswith("." + site))


# What a page keeps for itself, in its own storage: cleared from the page (Qt has no way to clear one site's)
CLEAR_STORAGE = """(async () => {
  try { localStorage.clear(); } catch (e) {}
  try { sessionStorage.clear(); } catch (e) {}
  try { for (const db of (await indexedDB.databases())) if (db.name) indexedDB.deleteDatabase(db.name); } catch (e) {}
  try { for (const key of await caches.keys()) await caches.delete(key); } catch (e) {}
  try { for (const worker of await navigator.serviceWorker.getRegistrations()) await worker.unregister(); } catch (e) {}
})();"""


def clear_site_data(browser, tab, host, profile, done=None):
    """This site's cookies (from the cookie store: every one for it or a domain it's under), then what its pages
    keep in their own storage, from the page you're on, which reloads signed out."""
    from PyQt6.QtWebEngineCore import QWebEngineScript
    from .webview import cookie_ledger
    store = profile.cookieStore()
    ledger = cookie_ledger(profile)

    def sweep():
        gone = [c for c in list(ledger.cookies.values()) if same_site(c.domain(), host)]
        for cookie in gone:
            store.deleteCookie(cookie)
        view = tab.view
        if view is not None and not sip.isdeleted(view) and QUrl(view.url().toString()).host() == host:
            view.page().runJavaScript(CLEAR_STORAGE, QWebEngineScript.ScriptWorldId.ApplicationWorld)
            QTimer.singleShot(400, view.reload)
        browser.toast(f"Cleared {display_host(tab.url)}'s cookies and data")
        if done is not None:
            done(len(gone))

    QTimer.singleShot(COOKIE_SWEEP_MS, sweep)
