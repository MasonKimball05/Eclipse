# Site permissions: what websites may use (camera, microphone, screen sharing, location,
# notifications, the clipboard, local fonts), asked the way Arc asks: a card at the top of the page,
# "meet.google.com wants to use your camera and microphone", with Don't Allow and Allow.
#
# Two kinds of answer, stored in two places:
#   - Camera and microphone: Qt never remembers these, so we do, per site, in the settings
#     (store.settings["site_permissions"]), which also puts them in backups. Screen sharing is asked
#     for every time, and then you pick which screen or window to share.
#   - Notifications, location, clipboard and fonts: Qt keeps these itself, per profile, on disk.
#
# A request from a tab you're not looking at waits until you switch to it. Everything you've
# allowed or blocked can be changed in Site Controls (the shield in the URL pill) or in
# Settings → Privacy.

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePermission, QWebEngineProfile
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton,
                             QTabWidget, QVBoxLayout)

import qtawesome as qta

from . import motion
from .theme import stylesheet

T = QWebEnginePermission.PermissionType
# What each permission is called in a prompt, and its icon
KINDS = {
    T.MediaAudioCapture: ("use your microphone", "ph.microphone"),
    T.MediaVideoCapture: ("use your camera", "ph.video-camera"),
    T.MediaAudioVideoCapture: ("use your camera and microphone", "ph.video-camera"),
    T.DesktopVideoCapture: ("share your screen", "ph.monitor"),
    T.DesktopAudioVideoCapture: ("share your screen and its audio", "ph.monitor"),
    T.Geolocation: ("know your location", "ph.map-pin"),
    T.Notifications: ("show notifications", "ph.bell"),
    T.ClipboardReadWrite: ("see what you copy", "ph.clipboard-text"),
    T.LocalFontsAccess: ("use the fonts installed on this Mac", "ph.text-aa"),
}
# Answers we remember ourselves (Qt forgets them); screen sharing deliberately isn't one of them
REMEMBERED = {T.MediaAudioCapture, T.MediaVideoCapture, T.MediaAudioVideoCapture}
SCREEN = {T.DesktopVideoCapture, T.DesktopAudioVideoCapture}
SETTING = "site_permissions"


def origin_key(url: QUrl) -> str:
    """"https://meet.google.com" — the scheme, host and (non-default) port, which is what a rule is for.
    "" for anything that isn't a website (file:, data:…), which share no origin and get no rules."""
    if url.scheme() not in ("http", "https") or not url.host():
        return ""
    return url.adjusted(QUrl.UrlFormattingOption.RemovePath | QUrl.UrlFormattingOption.RemoveQuery |
                        QUrl.UrlFormattingOption.RemoveFragment | QUrl.UrlFormattingOption.RemoveUserInfo
                        ).toString(QUrl.ComponentFormattingOption.FullyEncoded).rstrip("/")


def capture_rule(settings: dict, origin: str, type_name: str) -> str | None:
    """"allow", "block" or None (ask) for a camera/microphone permission on a site."""
    return settings.get(SETTING, {}).get(origin, {}).get(type_name)


def set_capture_rule(settings: dict, origin: str, type_name: str, value: str | None):
    rules = settings.setdefault(SETTING, {})
    site = rules.setdefault(origin, {})
    if value is None:
        site.pop(type_name, None)
    else:
        site[type_name] = value
    if not site:
        rules.pop(origin, None)


def describe(types) -> str:
    """One phrase for several requests at once: "use your camera and microphone"."""
    types = set(types)
    if {T.MediaAudioCapture, T.MediaVideoCapture} <= types:
        types = (types - {T.MediaAudioCapture, T.MediaVideoCapture}) | {T.MediaAudioVideoCapture}
    phrases = [KINDS[t][0] for t in sorted(types, key=lambda t: t.value) if t in KINDS]
    return " and ".join(phrases) if len(phrases) < 3 else ", ".join(phrases[:-1]) + " and " + phrases[-1]


class PermissionPrompt(QFrame):
    """The card at the top of the page."""

    def __init__(self, manager, view, host, origin):
        super().__init__(host)
        self.manager, self.view, self.host_widget, self.origin = manager, view, host, origin
        self.setObjectName("PermissionPrompt")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(10)
        self.icon = QLabel()
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setWordWrap(True)
        deny = QPushButton("Don't Allow")
        allow = QPushButton("Allow")
        allow.setObjectName("PermissionAllow")
        allow.setDefault(True)
        close = QPushButton()
        close.setObjectName("PermissionClose")
        close.setIcon(qta.icon("ph.x", color=manager.browser.theme.panel_text))
        close.setToolTip("Decide later")
        deny.clicked.connect(lambda: manager.answer(self, False))
        allow.clicked.connect(lambda: manager.answer(self, True))
        close.clicked.connect(lambda: manager.dismiss(self))
        row.addWidget(self.icon)
        row.addWidget(self.text, 1)
        row.addWidget(deny)
        row.addWidget(allow)
        row.addWidget(close)
        host.installEventFilter(self)

    def update_text(self, types):
        host = QUrl(self.origin).host().removeprefix("www.") or "This page"
        self.text.setText(f"<b>{host}</b> wants to {describe(types)}")
        icon = KINDS.get(min(types, key=lambda t: t.value), ("", "ph.shield-check"))[1]
        self.icon.setPixmap(qta.icon(icon, color=self.manager.browser.theme.accent).pixmap(20, 20))
        self.place()

    def place(self):
        width = min(560, self.host_widget.width() - 40)
        self.setFixedWidth(max(width, 280))
        self.adjustSize()
        self.move((self.host_widget.width() - self.width()) // 2, 12)
        # The page area is shared by every tab: only show while the asking tab is the one on screen
        if self.view.isVisible() and not self.isVisible() and not getattr(self, "_shown_once", False):
            self._shown_once = True
            motion.fade(self, True, motion.NORMAL)  # the first time, it eases in
        else:
            self.setVisible(self.view.isVisible())
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self.host_widget and event.type() == QEvent.Type.Resize:
            self.place()
        return False


class ScreenPicker(QDialog):
    """Which screen or window to share (after the site was allowed to ask)."""

    def __init__(self, request, origin, theme, parent):
        super().__init__(parent)
        self.request = request
        self.setWindowTitle("Share Your Screen")
        self.setStyleSheet(stylesheet(theme))
        self.setProperty("panel", True)
        self.resize(420, 360)
        layout = QVBoxLayout(self)
        intro = QLabel(f"Choose what <b>{QUrl(origin).host()}</b> can see:")
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)
        self.tabs = QTabWidget()
        self.lists = []
        for label, model in (("Screens", request.screensModel()), ("Windows", request.windowsModel())):
            listing = QListWidget()
            for row in range(model.rowCount()):
                listing.addItem(str(model.data(model.index(row, 0)) or f"{label[:-1]} {row + 1}"))
            if listing.count():
                listing.setCurrentRow(0)
            listing.itemDoubleClicked.connect(lambda _item: self.accept())
            self.tabs.addTab(listing, label)
            self.lists.append((listing, model))
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        share = buttons.addButton("Share", QDialogButtonBox.ButtonRole.AcceptRole)
        share.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self):
        listing, model = self.lists[self.tabs.currentIndex()]
        row = listing.currentRow()
        return (self.tabs.currentIndex(), model.index(row, 0)) if row >= 0 else None


class PermissionManager(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._waiting: dict[int, list] = {}      # id(view) → [(view, permission), …] not answered yet
        self._prompts: dict[int, PermissionPrompt] = {}
        self._capturing: set[int] = set()       # id(view) of pages given the camera, microphone or screen

    @property
    def settings(self):
        return self.browser.store.settings

    def attach_profile(self, profile):
        # Notifications, location and the like: Qt remembers the answers, on disk, per profile
        profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.StoreOnDisk)

    def attach(self, view):
        page = view.page()
        page.permissionRequested.connect(lambda permission, v=view: self._requested(v, permission))
        page.desktopMediaRequested.connect(lambda request, v=view: self._pick_screen(v, request))
        view.destroyed.connect(lambda _=None, key=id(view): self._forget(key))

    def is_capturing(self, view) -> bool:
        """Whether the page was given the camera, microphone or a screen (so it shouldn't be put to sleep)."""
        return id(view) in self._capturing

    def _grant(self, view, permission):
        if permission.permissionType() in REMEMBERED:
            self._capturing.add(id(view))
        permission.grant()

    # --- Requests --------------------------------------------------------------------------------

    def _requested(self, view, permission):
        # Qt passes a temporary that's gone once this returns; keep our own copy to answer later
        permission = QWebEnginePermission(permission)
        kind = permission.permissionType()
        if kind == T.MouseLock:
            permission.grant()  # games and 3D viewers; Esc always gets out
            return
        if kind not in KINDS:
            permission.deny()
            return
        origin = origin_key(permission.origin())
        if kind in REMEMBERED and origin:
            rule = capture_rule(self.settings, origin, kind.name)
            if rule == "allow":
                self._grant(view, permission)
                return
            if rule == "block":
                permission.deny()
                return
        waiting = self._waiting.setdefault(id(view), [])
        if any(origin_key(p.origin()) == origin and p.permissionType() == kind for _v, p in waiting):
            return  # already asking
        waiting.append((view, permission))
        if view.isVisible():
            self._show(view)
        else:
            tab = next((t for t in self.browser.store.all_tabs() if t.view is view), None)
            if tab is not None:
                self.browser.toast(f"{tab.label} wants to {describe([kind])} — switch to it to answer")

    def flush(self, view):
        """A view just came on screen: ask about anything it requested while hidden, and hide the
        prompts of the tabs that went off screen."""
        for prompt in self._prompts.values():
            prompt.setVisible(prompt.view.isVisible())
        if view is not None and self._waiting.get(id(view)):
            self._show(view)

    def _show(self, view):
        waiting = [(v, p) for v, p in self._waiting.get(id(view), []) if p.isValid()]
        self._waiting[id(view)] = waiting
        if not waiting:
            self._close_prompt(id(view))
            return
        origin = origin_key(waiting[0][1].origin())
        same_site = [p.permissionType() for _v, p in waiting if origin_key(p.origin()) == origin]
        prompt = self._prompts.get(id(view))
        if prompt is None or prompt.origin != origin:
            self._close_prompt(id(view))
            prompt = self._prompts[id(view)] = PermissionPrompt(self, view, self._host_for(view), origin)
        prompt.update_text(same_site)

    def _host_for(self, view):
        """Where the prompt goes: over the main window's page area, or a Little window's or Peek's card."""
        widget = view
        while widget is not None:
            if widget is self.browser.content:
                return widget
            widget = widget.parentWidget()
        return view.parentWidget() or view

    def answer(self, prompt, allow):
        key = id(prompt.view)
        answered = [(v, p) for v, p in self._waiting.get(key, []) if origin_key(p.origin()) == prompt.origin]
        for view, permission in answered:
            kind = permission.permissionType()
            if permission.isValid():
                self._grant(view, permission) if allow else permission.deny()
            # A private window's answers last as long as its page: remembering them would be a record
            if kind in REMEMBERED and prompt.origin and not view.page().profile().isOffTheRecord():
                set_capture_rule(self.settings, prompt.origin, kind.name, "allow" if allow else "block")
        if any(p.permissionType() in REMEMBERED for _v, p in answered):
            self.browser.store.schedule_save()
        self._waiting[key] = [w for w in self._waiting.get(key, []) if w not in answered]
        self._close_prompt(key)
        self._show(prompt.view)  # another site's request may be waiting behind it

    def dismiss(self, prompt):
        """× : decide later. The page hears "no" for now (it would otherwise wait forever, and can't ask
        again), but nothing is remembered, so it can ask next time."""
        key = id(prompt.view)
        dismissed = [(v, p) for v, p in self._waiting.get(key, []) if origin_key(p.origin()) == prompt.origin]
        for view, permission in dismissed:
            if not permission.isValid():
                continue
            permission.deny()
            if QWebEnginePermission.isPersistent(permission.permissionType()):
                view.page().profile().queryPermission(permission.origin(), permission.permissionType()).reset()
        self._waiting[key] = [w for w in self._waiting.get(key, []) if w not in dismissed]
        self._close_prompt(key)
        self._show(prompt.view)

    def _close_prompt(self, key):
        prompt = self._prompts.pop(key, None)
        if prompt is not None:
            prompt.host_widget.removeEventFilter(prompt)
            prompt.deleteLater()

    def _forget(self, key):
        self._waiting.pop(key, None)
        self._capturing.discard(key)
        self._close_prompt(key)

    def _pick_screen(self, view, request):
        origin = origin_key(view.page().url())
        picker = ScreenPicker(request, origin, self.browser.theme, self.browser)
        if picker.exec() == QDialog.DialogCode.Accepted and picker.choice() is not None:
            which, index = picker.choice()
            (request.selectScreen if which == 0 else request.selectWindow)(index)
            self._capturing.add(id(view))
        else:
            request.cancel()

    # --- Site Controls / Settings ------------------------------------------------------------------

    def rules_for(self, profile, origin) -> list[tuple[str, object, str]]:
        """(label, permission type, "allow"/"block") for everything decided about a site."""
        out = []
        for type_name, value in self.settings.get(SETTING, {}).get(origin, {}).items():
            kind = getattr(T, type_name, None)
            if kind in KINDS:
                out.append((KINDS[kind][0], kind, value))
        for permission in profile.listPermissionsForOrigin(QUrl(origin)):
            kind = permission.permissionType()
            state = permission.state()
            if kind in KINDS and state != QWebEnginePermission.State.Ask:
                out.append((KINDS[kind][0], kind, "allow" if state == QWebEnginePermission.State.Granted else "block"))
        return out

    def set_rule(self, profile, origin, kind, value):
        """value: "allow", "block" or None (ask next time)."""
        if kind in REMEMBERED:
            set_capture_rule(self.settings, origin, kind.name, value)
            self.browser.store.schedule_save()
            return
        permission = profile.queryPermission(QUrl(origin), kind)
        if value is None:
            permission.reset()
        elif value == "allow":
            permission.grant()
        else:
            permission.deny()

    def reset_site(self, profile, origin):
        self.settings.get(SETTING, {}).pop(origin, None)
        for permission in profile.listPermissionsForOrigin(QUrl(origin)):
            permission.reset()
        self.browser.store.schedule_save()

    def all_rules(self) -> list[tuple[str, str, object, str, object]]:
        """(origin, label, type, value, profile or None) across settings and every profile, for Settings."""
        out = []
        for origin, rules in self.settings.get(SETTING, {}).items():
            for type_name, value in rules.items():
                kind = getattr(T, type_name, None)
                if kind in KINDS:
                    out.append((origin, KINDS[kind][0], kind, value, None))
        for name in self.browser.profile_names():
            profile = self.browser._profiles.get(name)
            if profile is None:
                continue
            for permission in profile.listAllPermissions():
                kind, state = permission.permissionType(), permission.state()
                if kind in KINDS and state != QWebEnginePermission.State.Ask:
                    out.append((origin_key(permission.origin()), KINDS[kind][0], kind,
                                "allow" if state == QWebEnginePermission.State.Granted else "block", profile))
        return sorted(out, key=lambda r: (r[0], r[1]))
