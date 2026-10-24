# ⌃Tab, Arc style: hold Control and tap Tab to go back through the tabs you used most recently,
# shown as cards with a thumbnail of each page; let go of Control to switch. A quick ⌃Tab just
# flips to the tab you were on before, without the cards ever appearing.
#
# It stays within the current Space (plus Favorites), like the sidebar does.

import sys
import time

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QWidget

from .models import display_host
from .sidebar import tab_icon

CARD_W, CARD_H, THUMB_H = 200, 156, 118
GAP = 14
REVEAL_MS = 150     # cards appear only if Control is still held after this long
POLL_MS = 30
REPEAT_MS = 90      # a held Tab key steps at most this often
CONTROL = Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin" else Qt.KeyboardModifier.ControlModifier


def recent_tabs(visible, mru_ids, limit=8):
    """The tabs to offer, most recently used first: those in the MRU list, then the rest by when
    they were last active. Only tabs in `visible` (the current Space and Favorites)."""
    by_id = {t.id: t for t in visible}
    ordered = [by_id[i] for i in mru_ids if i in by_id]
    seen = {id(t) for t in ordered}
    ordered += sorted((t for t in visible if id(t) not in seen), key=lambda t: t.last_active, reverse=True)
    return ordered[:limit]


class TabSwitcher(QWidget):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.tabs = []
        self.index = 0
        self.active = False
        self._last_step = 0.0
        self.hide()
        self._reveal = QTimer(self, singleShot=True, interval=REVEAL_MS, timeout=self._show_cards)
        self._poll = QTimer(self, interval=POLL_MS, timeout=self._check_keys)

    def step(self, direction):
        now = time.monotonic()
        if self.active and now - self._last_step < REPEAT_MS / 1000:
            return
        self._last_step = now
        if not self.active:
            store = self.browser.store
            self.tabs = recent_tabs([t for t in store.visible_tabs() if t.url], self.browser._mru)
            if len(self.tabs) < 2:
                return
            self.active = True
            self.index = 0
            self._reveal.start()
            self._poll.start()
            # The page keeps keyboard focus, so watch the whole app for Esc (and for switching away)
            QApplication.instance().installEventFilter(self)
        self.index = (self.index + direction) % len(self.tabs)
        self.update()

    def _check_keys(self):
        if not QGuiApplication.queryKeyboardModifiers() & CONTROL:
            self.commit()

    def _show_cards(self):
        if not self.active:
            return
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def commit(self):
        if not self.active:
            return
        tab = self.tabs[self.index]
        self._finish()
        if self.browser.store.locate(tab) is not None:
            self.browser.activate_tab(tab)

    def cancel(self):
        self._finish()

    def _finish(self):
        self.active = False
        QApplication.instance().removeEventFilter(self)
        for timer in (self._reveal, self._poll):
            timer.stop()
        self.hide()

    def eventFilter(self, obj, event):
        if self.active:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.cancel()
                return True
            if event.type() == QEvent.Type.ApplicationDeactivate:  # ⌘Tab'd away with Control held
                self.cancel()
        return False

    def mousePressEvent(self, event):
        hit = self._card_at(event.position())
        if hit is None:
            self.cancel()
        else:
            self.index = hit
            self.commit()

    def _layout(self):
        count = len(self.tabs)
        total = count * CARD_W + (count - 1) * GAP
        scale = min(1.0, (self.width() - 80) / total) if total else 1.0
        w, h, gap = CARD_W * scale, CARD_H * scale, GAP * scale
        x0 = (self.width() - (count * w + (count - 1) * gap)) / 2
        y = (self.height() - h) / 2
        return [QRectF(x0 + i * (w + gap), y, w, h) for i in range(count)]

    def _card_at(self, pos):
        return next((i for i, r in enumerate(self._layout()) if r.contains(pos)), None)

    def paintEvent(self, event):
        if not self.tabs:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        theme = self.browser.theme
        p.fillRect(self.rect(), QColor(0, 0, 0, 70))
        rects = self._layout()
        panel = QRectF(rects[0].left() - 18, rects[0].top() - 18,
                       rects[-1].right() - rects[0].left() + 36, rects[0].height() + 36)
        path = QPainterPath()
        path.addRoundedRect(panel, 18, 18)
        p.fillPath(path, theme.panel)
        for i, (tab, rect) in enumerate(zip(self.tabs, rects)):
            card = QPainterPath()
            card.addRoundedRect(rect, 10, 10)
            p.fillPath(card, QColor(theme.panel).lighter(115) if theme.dark else QColor(theme.panel).darker(103))
            thumb_rect = QRectF(rect.left() + 6, rect.top() + 6, rect.width() - 12, rect.height() * THUMB_H / CARD_H - 6)
            thumb = self.browser.thumbnails.get(tab.id)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(thumb_rect, 6, 6)
            p.setClipPath(clip)
            if thumb is not None:
                # Fill the card's width with the top of the page, at the screen's pixel density
                ratio = self.devicePixelRatioF()
                scaled = thumb.scaledToWidth(int(thumb_rect.width() * ratio), Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(ratio)
                p.drawPixmap(thumb_rect.topLeft(), scaled,
                             QRectF(0, 0, scaled.width(), min(scaled.height(), thumb_rect.height() * ratio)))
            else:
                p.fillRect(thumb_rect, QColor(theme.accent).lighter(170))
                icon = tab_icon(tab, theme).pixmap(64, 64)
                p.drawPixmap(QRectF(thumb_rect.center().x() - 16, thumb_rect.center().y() - 16, 32, 32), icon,
                             QRectF(icon.rect()))
            p.restore()
            p.setPen(theme.panel_text)
            font = QFont(p.font())
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            text_rect = QRectF(rect.left() + 10, thumb_rect.bottom() + 4, rect.width() - 20, 18)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(tab.label, Qt.TextElideMode.ElideRight, int(text_rect.width())))
            font.setWeight(QFont.Weight.Normal)
            font.setPixelSize(10)
            p.setFont(font)
            host_rect = QRectF(text_rect.left(), text_rect.bottom(), text_rect.width(), 14)
            p.drawText(host_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(display_host(tab.url), Qt.TextElideMode.ElideRight, int(host_rect.width())))
            if i == self.index:
                p.setPen(QColor(theme.accent))
                pen = p.pen()
                pen.setWidthF(2.5)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 12, 12)
        p.end()
