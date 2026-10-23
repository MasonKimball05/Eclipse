# Arc's Peek: links opened from a pinned tab or a favorite (Slack, Gmail, Notion…)
# float over the page in a card instead of piling up as new tabs. Read it, then
# close it (Esc or click outside) or keep it with "Open as Tab".

import qtawesome as qta
from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QToolButton, QVBoxLayout,
                             QWidget)

from .models import display_host


class PeekOverlay(QWidget):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.view = None
        self.setObjectName("PeekBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
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
        self.close_btn = self._button("ph.x", "Close (Esc)", self.close_peek)
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
        self.close_peek()
        for btn in (self.open_btn, self.close_btn):
            btn.setIcon(qta.icon(btn.property("icon_name"), color=self.browser.theme.panel_text))
        view = self.view = self.browser.create_view(profile)
        view.urlChanged.connect(lambda u: self.host.setText("Peek  ·  " + (display_host(u.toString()) or u.toString())))
        view.page().windowCloseRequested.connect(self.close_peek)
        view.page().newWindowRequested.connect(lambda req: self.browser.handle_new_window(None, req, profile))
        self.card_layout.addWidget(view, 1)
        if request is not None:
            request.openIn(view.page())
            self.host.setText("Peek  ·  " + display_host(request.requestedUrl().toString()))
        else:
            view.load(QUrl(url))
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        view.setFocus()

    def close_peek(self):
        self.hide()
        if self.view is not None:
            self.view.deleteLater()
            self.view = None
            self.browser.focus_current()

    def promote(self):
        view, self.view = self.view, None
        self.hide()
        if view is not None:
            self.card_layout.removeWidget(view)
            self.browser.adopt_view(view)

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        mx, my = max(int(w * 0.08), 40), max(int(h * 0.06), 30)
        self.card.setGeometry(mx, my, w - 2 * mx, h - 2 * my)

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.position().toPoint()):
            self.close_peek()
