# Web notifications and unread badges.
#
#   - Notifications from sites you've allowed (Gmail, Slack, Calendar…) appear as banners in the
#     top-right corner of the window; click one to go to its tab. In the background, the Dock icon
#     bounces. Do Not Disturb (Settings → Tabs, or the command bar) keeps the banners away.
#   - Pinned tabs and Favorites show the unread count a site puts in its title ("(3) Inbox"), Arc
#     style, even after Tidy Tab Titles renamed the tab; the Dock icon shows the total.

import re

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

import qtawesome as qta

from . import macos, motion
from .models import display_host, walk_tabs
from .theme import letter_icon

BANNER_MS = 6000
MAX_BANNERS = 3
_LEADING = re.compile(r"^\s*[(\[](\d{1,4}\+?)[)\]]\s")      # "(3) WhatsApp", "[12] Feed"
_INBOX = re.compile(r"\b(?:Inbox|Unread)\s*\((\d{1,4}\+?)\)")  # "Inbox (12) - you@gmail.com - Gmail"


def unread_count(title: str) -> str | None:
    """The unread count a site shows in its title, as text ("3", "99+", or "•" for Slack's
    "* " / "! " marks), or None."""
    if not title:
        return None
    m = _LEADING.match(title) or _INBOX.search(title)
    if m:
        n = int(m.group(1).rstrip("+"))
        if n == 0 or (m.re is _LEADING and 1900 <= n <= 2099 and not m.group(1).endswith("+")):
            return None  # nothing unread, or "(2026) the year in review"
        return m.group(1)
    if title.startswith(("* ", "! ")):  # Slack: unread messages / mentions
        return "•"
    return None


class Banner(QFrame):
    def __init__(self, center, notification, tab, parent):
        super().__init__(parent)
        self.center, self.notification, self.tab = center, notification, tab
        self.setObjectName("NotificationBanner")
        self.setFixedWidth(340)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 6, 10)
        row.setSpacing(10)
        icon = QLabel()
        image = notification.icon()
        theme = center.browser.theme
        origin = notification.origin().toString()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else letter_icon(display_host(origin), theme.accent).pixmap(32, 32)
        icon.setPixmap(pixmap.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(notification.title() or display_host(origin))
        title.setObjectName("BannerTitle")
        title.setTextFormat(Qt.TextFormat.PlainText)  # sites write these: never rich text
        body = QLabel(notification.message())
        body.setObjectName("BannerBody")
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        site = QLabel(display_host(origin))
        site.setObjectName("BannerSite")
        text.addWidget(title)
        if notification.message():
            text.addWidget(body)
        text.addWidget(site)
        close = QToolButton()
        close.setObjectName("BannerClose")
        close.setIcon(qta.icon("ph.x", color=theme.panel_text))
        close.clicked.connect(lambda: center.dismiss(self, close_it=True))
        row.addWidget(icon)
        row.addLayout(text, 1)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        # Owned by the banner, so it can't fire after the banner's gone
        self._timer = QTimer(self, singleShot=True, interval=BANNER_MS, timeout=lambda: center.dismiss(self))
        self._timer.start()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.center.clicked(self)


class NotificationCenter(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._live = []      # notifications kept alive while they're showing
        self._banners = []

    @property
    def do_not_disturb(self) -> bool:
        return self.browser.store.settings.get("notifications_dnd", False)

    def attach_profile(self, profile):
        profile.setNotificationPresenter(lambda notification, p=profile: self.present(p, notification))

    def present(self, profile, notification):
        self._live.append(notification)
        notification.closed.connect(self._closed)  # not a lambda holding it, which would keep it alive
        notification.show()
        tab = self._tab_for(profile, notification)
        if tab is not None:
            tab.notified = True
            tab.changed.emit()
        if not self.browser.isActiveWindow():
            QApplication.alert(self.browser, 0)  # bounce the Dock icon until you look
        if self.do_not_disturb:
            return
        banner = Banner(self, notification, tab, self.browser.content)
        self._banners.insert(0, banner)
        for old in self._banners[MAX_BANNERS:]:
            self.dismiss(old)
        self._place()

    def _closed(self):
        notification = self.sender()
        self._live = [n for n in self._live if n is not notification]

    def _tab_for(self, profile, notification):
        """The tab the notification came from, looked up now (the tab may have moved since)."""
        host = display_host(notification.origin().toString())
        tabs = [t for t in self.browser.store.all_tabs()
                if hasattr(t.view, "page") and t.view.page().profile() is profile and display_host(t.url) == host]
        current_space = set(map(id, self.browser.store.visible_tabs()))
        tabs.sort(key=lambda t: id(t) not in current_space)
        return tabs[0] if tabs else None

    def _place(self):
        content = self.browser.content
        y = 12
        for banner in self._banners:
            banner.adjustSize()
            banner.move(content.width() - banner.width() - 12, y)
            if not banner.isVisible():
                motion.fade(banner, True, motion.NORMAL)  # new ones fade in; the rest just move down
            banner.raise_()
            y += banner.height() + 8

    def relayout(self):
        if self._banners:
            self._place()

    def clicked(self, banner):
        tab = banner.tab if banner.tab is not None and self.browser.store.locate(banner.tab) else None
        banner.notification.click()
        if tab is not None:
            self.browser.activate_tab(tab)
        self.browser.bring_to_front()
        self.dismiss(banner)

    def dismiss(self, banner, close_it=False):
        if banner not in self._banners:
            return
        self._banners.remove(banner)
        if close_it:
            banner.notification.close()
        banner.hide()
        banner.deleteLater()
        self._place()

    # --- Unread badges ---------------------------------------------------------------------------

    def title_changed(self, tab):
        tab.unread = unread_count(tab.title)
        self.update_dock()

    def update_dock(self):
        total, dot = 0, False
        store = self.browser.store
        for tab in list(store.favorites) + [t for s in store.spaces for t in walk_tabs(s.pinned)]:
            count = getattr(tab, "unread", None) or getattr(tab, "app_unread", None)
            if count == "•":
                dot = True
            elif count:
                total += int(count.rstrip("+"))
        macos.set_dock_badge(str(total) if total else "•" if dot else "")
