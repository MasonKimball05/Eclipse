# Arc's Command Bar (⌘T / ⌘L): one floating box for URLs, searches, open tabs,
# history, archived tabs and app commands. There is no "new tab page" — a tab
# only exists once you pick something here.

import re
from dataclasses import dataclass
from typing import Callable

import qtawesome as qta
from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QLineEdit, QListWidget, QListWidgetItem,
                             QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .ai import looks_like_question
from .content import answer_browser, fit_answer
from .models import display_host
from .storage import favicon
from .sidebar import tab_icon
from .theme import letter_icon

SEARCH_URL = "https://www.google.com/search?q={}"
ROW_HEIGHT = 42
MAX_ROWS = 8

_DOMAIN = re.compile(r"^([\w-]+\.)+[a-z]{2,}(:\d+)?([/?#].*)?$", re.IGNORECASE)
_LOCAL = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?([/?#].*)?$", re.IGNORECASE)
_SCHEME = re.compile(r"^(https?|file|about|chrome|view-source|data):", re.IGNORECASE)


def to_url(text: str) -> str | None:
    """Return a URL if the text looks like one, otherwise None (meaning: search for it)."""
    text = text.strip()
    if not text or " " in text:
        return None
    if _SCHEME.match(text):
        return text
    if _LOCAL.match(text):
        return "http://" + text
    if _DOMAIN.match(text):
        return "https://" + text
    return None


def search_url(text: str) -> str:
    return SEARCH_URL.format(QUrl.toPercentEncoding(text.strip()).data().decode())


@dataclass
class Result:
    title: str
    subtitle: str
    badge: str
    icon: object
    run: Callable[[], None]
    keep_open: bool = False  # e.g. "Ask Claude": the answer appears inside the bar


class ResultDelegate(QStyledItemDelegate):
    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        result: Result = index.data(Qt.ItemDataRole.UserRole)
        theme = self.bar.browser.theme
        text = theme.panel_text
        rect = QRectF(option.rect).adjusted(0, 1, 0, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if option.state & QStyle.StateFlag.State_Selected:
            path = QPainterPath()
            path.addRoundedRect(rect, 9, 9)
            highlight = QColor(theme.accent)
            highlight.setAlphaF(0.85 if not theme.dark else 0.65)
            painter.fillPath(path, highlight)
            text = QColor("white")

        icon_rect = QRectF(rect.left() + 12, rect.center().y() - 9, 18, 18)
        result.icon.paint(painter, icon_rect.toRect())

        left = icon_rect.right() + 12
        right = rect.right() - 12
        font = QFont(painter.font())
        font.setPixelSize(12)
        painter.setFont(font)
        badge_w = painter.fontMetrics().horizontalAdvance(result.badge) if result.badge else 0
        if result.badge:
            dim = QColor(text)
            dim.setAlphaF(0.6)
            painter.setPen(dim)
            painter.drawText(QRectF(right - badge_w, rect.top(), badge_w, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, result.badge)
            right -= badge_w + 14

        font.setPixelSize(14)
        painter.setFont(font)
        fm = painter.fontMetrics()
        title = fm.elidedText(result.title, Qt.TextElideMode.ElideRight, int((right - left) * 0.7))
        painter.setPen(text)
        painter.drawText(QRectF(left, rect.top(), right - left, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

        if result.subtitle:
            sub_left = left + fm.horizontalAdvance(title) + 10
            font.setPixelSize(12)
            painter.setFont(font)
            dim = QColor(text)
            dim.setAlphaF(0.5)
            painter.setPen(dim)
            sub = painter.fontMetrics().elidedText("— " + result.subtitle, Qt.TextElideMode.ElideRight,
                                                   int(max(right - sub_left, 0)))
            painter.drawText(QRectF(sub_left, rect.top(), right - sub_left, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub)
        painter.restore()


class CommandBar(QWidget):
    """Full-window overlay: a dimmed backdrop with the command panel floating on top."""

    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.mode = "new"
        self.request = None
        self.setObjectName("CommandBackdrop")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.hide()

        self.panel = QFrame(self)
        self.panel.setObjectName("CommandPanel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.panel.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.input = QLineEdit()
        self.input.setObjectName("CommandInput")
        self.input.setPlaceholderText("Search or Enter URL…")
        self.input.textChanged.connect(self.update_results)
        self.input.installEventFilter(self)
        self.divider = QFrame()
        self.divider.setObjectName("CommandDivider")
        self.divider.setFixedHeight(1)
        self.list = QListWidget()
        self.list.setObjectName("CommandList")
        self.list.setItemDelegate(ResultDelegate(self))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemClicked.connect(self._run_item)
        layout.addWidget(self.input)
        layout.addWidget(self.divider)
        layout.addWidget(self.list)
        self.answer = answer_browser(browser, margin=16)
        layout.addWidget(self.answer)

    # --- Showing / hiding --------------------------------------------------------

    def open(self, mode="new", text=""):
        """mode "new" opens results in a new tab; "current" navigates the current tab."""
        self.mode = mode
        self.input.setPlaceholderText("Open a page next to this one…" if mode == "split" else "Search or Enter URL…")
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.input.setText(text)
        self.input.selectAll()
        self.input.setFocus()
        self.update_results()

    def close_bar(self):
        self._cancel_answer()
        self.hide()
        self.input.clear()
        if self.browser.current_tab and self.browser.current_tab.view:
            self.browser.current_tab.view.setFocus()

    def resizeEvent(self, event):
        self._place_panel()

    def mousePressEvent(self, event):
        # Clicking the backdrop dismisses, like Arc
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close_bar()

    def _place_panel(self):
        width = min(660, self.width() - 80)
        answering = self.answer.isVisible()
        rows = 0 if answering else min(self.list.count(), MAX_ROWS)
        list_h = rows * ROW_HEIGHT + 12 if rows else 0
        self.list.setFixedHeight(list_h)
        self.list.setVisible(bool(rows))
        self.divider.setVisible(bool(rows) or answering)
        body_h = self.answer.height() if answering else list_h
        height = self.input.sizeHint().height() + (body_h + 1 if body_h else 0)
        self.panel.setGeometry((self.width() - width) // 2, int(self.height() * 0.17), width, height)

    # --- Keyboard ----------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                if self.list.count():
                    self.list.setCurrentRow((self.list.currentRow() + step) % self.list.count())
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item:
                    self._run_item(item)
                return True
        return super().eventFilter(obj, event)

    def _run_item(self, item: QListWidgetItem):
        result: Result = item.data(Qt.ItemDataRole.UserRole)
        if result.keep_open:
            result.run()
            return
        self.close_bar()
        result.run()

    # --- Building results ---------------------------------------------------------------

    def _icon(self, name):
        return qta.icon(name, color=self.browser.theme.panel_text)

    def _open(self, url):
        if self.mode == "split":
            return lambda: self.browser.open_in_split(url)
        if self.mode == "current" and self.browser.current_tab:
            return lambda: self.browser.navigate_current(url)
        return lambda: self.browser.open_url(url)

    # --- Ask Claude --------------------------------------------------------------------

    def ask(self, question):
        self._cancel_answer()
        self._show_answer("*Thinking…*")
        self.request = self.browser.claude.ask(
            "Answer this question from someone using their web browser. Be direct and concise "
            "(under 200 words unless they ask for more); use Markdown where it helps.\n\n" + question)
        self.request.partial.connect(self._show_answer)
        self.request.finished.connect(self._show_answer)
        self.request.failed.connect(lambda msg: self._show_answer(f"**Couldn't ask Claude:** {msg}"))

    def _show_answer(self, text):
        fit_answer(self.answer, self.panel.width(), text, max_height=int(self.height() * 0.6))
        self._place_panel()

    def _cancel_answer(self):
        if self.request is not None:
            self.request.cancel()
            self.request = None
        self.answer.hide()

    def update_results(self):
        self._cancel_answer()
        query = self.input.text().strip()
        q = query.lower()
        results: list[Result] = []
        store = self.browser.store
        theme = self.browser.theme

        if query:
            url = to_url(query)
            if url:
                results.append(Result(url, "", "Open", self._icon("ph.globe-simple"), self._open(url)))
            results.append(Result(query, "Search Google", "Search" if url else "",
                                  self._icon("ph.magnifying-glass"), self._open(search_url(query))))
            if self.browser.claude.available and not url and len(query.split()) >= 2:
                ask = Result(query, "Ask Claude", "Claude", qta.icon("ph.sparkle", color=theme.accent),
                             lambda: self.ask(query), keep_open=True)
                # A question goes straight to Claude on Return; anything else still searches first
                results.insert(0 if looks_like_question(query) else 1, ask)

        # Commands are only offered once the query clearly names one
        if len(q) >= 2:
            for name, icon, run in self.browser.commands():
                if q in name.lower():
                    results.append(Result(name, "", "Command", self._icon(icon), run))

        seen = set()
        current = self.browser.current_tab
        for tab in store.all_tabs():
            if tab is current or not tab.url:
                continue
            if not q or q in tab.label.lower() or q in tab.url.lower():
                if not q and tab not in store.visible_tabs():
                    continue
                seen.add(tab.url)
                if self.mode == "split":
                    badge, run = "Split With", (lambda t=tab: self.browser.add_to_split(t))
                else:
                    badge, run = "Switch to Tab", (lambda t=tab: self.browser.activate_tab(t))
                results.append(Result(tab.label, display_host(tab.url), badge, tab_icon(tab, theme), run))
            if len(results) >= MAX_ROWS:
                break

        limit = MAX_ROWS + 4
        for entry in store.history:
            if len(results) >= limit:
                break
            if entry["url"] in seen:
                continue
            if not q or q in entry["title"].lower() or q in entry["url"].lower():
                seen.add(entry["url"])
                results.append(self._url_result(entry, ""))

        if q:
            for entry in store.archive:
                if len(results) >= limit:
                    break
                if entry["url"] not in seen and (q in entry["title"].lower() or q in entry["url"].lower()):
                    seen.add(entry["url"])
                    results.append(self._url_result(entry, "Archived"))

        self.list.clear()
        for result in results[:limit]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._place_panel()

    def _url_result(self, entry, badge):
        url = entry["url"]
        icon = favicon(url) or letter_icon(display_host(url), self.browser.theme.accent)
        return Result(entry.get("title") or url, display_host(url), badge, icon, self._open(url))
