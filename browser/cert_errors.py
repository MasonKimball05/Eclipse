# "This connection isn't private": what a page with a bad security certificate shows, instead of
# Chromium's own "This site can't be reached… might be temporarily down" (which reads like an outage,
# not a warning). Go Back is the big button. Visiting anyway is behind Details, and only offered when
# the site allows it (sites that insist on a secure connection don't).
#
# Only the page itself gets the card: a bad certificate on something inside a page (an image, a
# script from another site) is simply refused.

import qtawesome as qta
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QPainter
from PyQt6.QtWebEngineCore import QWebEngineCertificateError
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from .devmode import is_dev_url

T = QWebEngineCertificateError.Type
REASONS = {
    T.CertificateDateInvalid: "Its security certificate has expired, or isn't valid yet. If your Mac's date and "
                              "time are right, the site needs to renew it.",
    T.CertificateCommonNameInvalid: "Its certificate belongs to a different website. The site may be set up "
                                    "wrong, or something may be pretending to be it.",
    T.CertificateAuthorityInvalid: "Its certificate isn't from an authority your Mac trusts.",
    T.CertificateRevoked: "Its certificate has been revoked by whoever issued it.",
    T.CertificateWeakSignatureAlgorithm: "Its certificate uses outdated, weak security.",
    T.CertificateWeakKey: "Its certificate uses outdated, weak security.",
    T.SslObsoleteVersion: "It uses an outdated version of the security protocol.",
    T.CertificateKnownInterceptionBlocked: "Something on your network is intercepting secure connections.",
}


def reason_text(error_type, description: str, url: QUrl) -> str:
    text = REASONS.get(error_type) or (description.rstrip(".") + ".")
    if error_type == T.CertificateAuthorityInvalid and is_dev_url(url):
        text += " That's normal for a development server on this Mac."
    return text


class CertificateWarning(QWidget):
    """Covers the page until you go back or choose to visit anyway."""

    def __init__(self, view, error, theme, go_back, visit_anyway):
        super().__init__(view)
        self.error = error
        self.theme = theme
        url = error.url()
        host = url.host() or url.toString()
        card = QFrame(self)
        card.setObjectName("CrashedCard")
        card.setFixedWidth(420)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(qta.icon("ph.shield-warning", color="#e5484d").pixmap(40, 40))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("This connection isn't private")
        title.setObjectName("CrashedTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel(f"Someone could be reading or changing what you send to {host}, like passwords or "
                        "card numbers.")
        detail.setObjectName("CrashedDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)
        back = QPushButton("Go Back")
        back.setObjectName("CrashReload")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(go_back)
        self.back_btn = back
        self.details_btn = QPushButton("Details")
        self.details_btn.setObjectName("ReaderButton")
        self.details_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.details_btn.clicked.connect(self._show_details)

        self.details = QWidget()
        d = QVBoxLayout(self.details)
        d.setContentsMargins(0, 8, 0, 0)
        d.setSpacing(8)
        why = QLabel(reason_text(error.type(), error.description(), url))
        why.setObjectName("CrashedDetail")
        why.setWordWrap(True)
        why.setAlignment(Qt.AlignmentFlag.AlignCenter)
        d.addWidget(why)
        self.anyway_btn = None
        if error.isOverridable():
            self.anyway_btn = QPushButton(f"Visit {host} Anyway")
            self.anyway_btn.setObjectName("ReaderButton")
            self.anyway_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.anyway_btn.clicked.connect(visit_anyway)
            d.addWidget(self.anyway_btn, 0, Qt.AlignmentFlag.AlignCenter)
        else:
            strict = QLabel(f"{host} only allows secure connections, so it can't be visited until it's fixed.")
            strict.setObjectName("CrashedDetail")
            strict.setWordWrap(True)
            strict.setAlignment(Qt.AlignmentFlag.AlignCenter)
            d.addWidget(strict)
        self.details.hide()

        for widget in (icon, title, detail):
            layout.addWidget(widget)
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.details_btn, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.details)
        self.card = card
        view.installEventFilter(self)
        self._place()
        self.show()
        self.raise_()

    def _show_details(self):
        self.details_btn.hide()
        self.details.show()
        self._place()

    def done(self):
        self.hide()
        self.deleteLater()

    def _place(self):
        host = self.parentWidget()
        self.setGeometry(0, 0, host.width(), host.height())
        self.card.adjustSize()
        self.card.move((self.width() - self.card.width()) // 2, max((self.height() - self.card.height()) // 3, 20))

    def paintEvent(self, event):
        # Covers whatever was showing before, so the old page doesn't look like the new site
        QPainter(self).fillRect(self.rect(), self.theme.top)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == event.Type.Resize:
            self._place()
        return False


class CertificateGuard:
    """Gives every page's certificate errors the warning card."""

    def __init__(self, browser):
        self.browser = browser
        self._cards = {}  # id(view) → CertificateWarning

    def attach(self, view):
        view.page().certificateError.connect(lambda error, v=view: self._error(v, error))
        view.loadStarted.connect(lambda v=view: self._clear(v, reject=True))  # went somewhere else instead
        view.destroyed.connect(lambda _=None, key=id(view): self._cards.pop(key, None))

    def _error(self, view, error):
        if not error.isMainFrame():
            error.rejectCertificate()
            return
        error.defer()
        # The signal's error object only lives as long as this call: keep a copy (they share one decision)
        error = QWebEngineCertificateError(error)
        self._clear(view, reject=True)
        previous = view.url()  # (still the page you were on: the new one hasn't arrived)
        self._cards[id(view)] = card = CertificateWarning(view, error, self.browser.theme,
                                                   lambda _=False, v=view: self.go_back(v),
                                                   lambda _=False, v=view: self.visit_anyway(v))
        card.previous = previous if previous != error.url() else QUrl()

    def warning(self, view):
        return self._cards.get(id(view))

    def visit_anyway(self, view):
        card = self._cards.pop(id(view), None)
        if card is not None:
            card.error.acceptCertificate()  # for this site, until the browser quits
            card.done()

    def go_back(self, view):
        card = self._cards.get(id(view))
        if card is None:
            return
        previous = card.previous
        self._clear(view, reject=True)
        if previous.isValid() and not previous.isEmpty():
            # Refusing it shows Chromium's error page in its place; once that's in, step back over it
            def back(_ok=False):
                view.loadFinished.disconnect(back)
                if view.history().canGoBack():
                    view.back()
                else:
                    view.setUrl(previous)
            view.loadFinished.connect(back)
            return
        tab = next((t for t in self.browser.store.all_tabs() if t.view is view), None)
        if tab is not None:
            self.browser.close_tab(tab)  # it was opened just for this site
        else:
            view.setUrl(QUrl("about:blank"))

    def _clear(self, view, reject=False):
        card = self._cards.pop(id(view), None)
        if card is not None:
            if reject:
                card.error.rejectCertificate()
            card.done()
