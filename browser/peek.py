# Arc's Peek: links opened from a pinned tab or a favorite (Slack, Gmail, Notion…)
# float over the page in a card instead of piling up as new tabs. Read it, then
# close it (Esc or click outside) or keep it with "Open as Tab".

import qtawesome as qta
from PyQt6.QtCore import QObject, QSize, Qt, QUrl
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QToolButton, QVBoxLayout,
                             QWidget)

from . import motion
from .models import display_host

RISE = 24  # points the card rises as it opens (and sinks as it closes)


class PeekOverlay(QWidget):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.view = None
        self.setObjectName("PeekBackdrop")
        self.dim = 0.0  # the backdrop, fading in as the card rises
        self.rise = 0.0  # how far below its place the card is (0 → 1: RISE points)
        self.closing = False
        self.hide()

        self.card = QFrame(self)
        self.card.setObjectName("PeekCard")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(60)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.card.setGraphicsEffect(shadow)
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(6, 0, 6, 6)
        self.card_layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(8, 6, 2, 6)
        self.host = QLabel()
        self.host.setObjectName("PeekHost")
        self.open_btn = self._button("ph.arrows-out", "Open as Tab", self.promote)
        self.close_btn = self._button("ph.x", "Close (Esc)", lambda: self.close_peek())
        header.addWidget(self.host, 1)
        header.addWidget(self.open_btn)
        header.addWidget(self.close_btn)
        self.card_layout.addLayout(header)

    def _button(self, icon, tip, slot):
        btn = QToolButton()
        btn.setObjectName("IconButton")
        btn.setProperty("icon_name", icon)
        btn.setToolTip(tip)
        btn.setIconSize(QSize(15, 15))
        btn.setFixedSize(26, 26)
        btn.clicked.connect(slot)
        return btn

    def open(self, profile, request=None, url=None):
        self.close_peek(animate=False)
        for btn in (self.open_btn, self.close_btn):
            btn.setIcon(qta.icon(btn.property("icon_name"), color=self.browser.theme.panel_text))
        view = self.view = self.browser.create_view(profile)
        self._links = [  # cut when the page is promoted to a tab (see promote)
            view.urlChanged.connect(lambda u: self.host.setText("Peek  ·  " + (display_host(u.toString())
                                                                                or u.toString()))),
            view.page().windowCloseRequested.connect(self.close_peek),
            view.page().newWindowRequested.connect(lambda req: self.browser.handle_new_window(None, req, profile)),
        ]
        self.card_layout.addWidget(view, 1)
        if request is not None:
            request.openIn(view.page())
            self.host.setText("Peek  ·  " + display_host(request.requestedUrl().toString()))
        else:
            view.load(QUrl(url))
        self.setGeometry(self.parentWidget().rect())
        self.closing = False
        self._ease(0.0, 1.0)
        self.show()
        self.raise_()
        view.setFocus()

    def _ease(self, start, end, done=None):
        """0: closed (no backdrop, card down), 1: open. Only the card's position changes, never its
        size: resizing a web page every frame would make it re-lay itself out (and stutter)."""
        def step(t):
            self.dim, self.rise = t, 1 - t
            self._place_card()
            self.update()
        motion.animate(self, start, end, step, motion.NORMAL if end else motion.FAST, done)

    def close_peek(self, animate=True):
        if not self.isVisible() or self.view is None:
            self._closed()
            return
        if not animate or self.closing:
            self._closed()
            return
        self.closing = True
        self._ease(self.dim, 0.0, self._closed)

    def _closed(self):
        animation = getattr(self, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        self.closing = False
        self.hide()
        if self.view is not None:
            self.view.deleteLater()
            self.view = None
            self.browser.focus_current()

    def promote(self):
        view, self.view = self.view, None
        animation = getattr(self, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        self.closing = False
        self.hide()
        if view is not None:
            for link in self._links:
                QObject.disconnect(link)
            self.card_layout.removeWidget(view)
            self.browser.adopt_view(view)

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        mx, my = max(int(w * 0.08), 40), max(int(h * 0.06), 30)
        self.card.resize(w - 2 * mx, h - 2 * my)
        self._place_card()

    def _place_card(self):
        mx, my = max(int(self.width() * 0.08), 40), max(int(self.height() * 0.06), 30)
        self.card.move(mx, my + round(RISE * self.rise))

    def paintEvent(self, event):
        dark = self.browser.theme.dark
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, round((70 if dark else 40) * self.dim)))
        p.end()

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.position().toPoint()):
            self.close_peek()
