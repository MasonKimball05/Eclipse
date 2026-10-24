# The Library (⌘Y): everything you've been to or saved, searchable in one window:
# history, archived tabs, notes and Browse for Me answers.

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import qtawesome as qta
from PyQt6.QtCore import QRectF, QSize, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import storage
from .models import display_host
from .theme import letter_icon, quiet_scrollbars, stylesheet

SECTIONS = [
    ("history", "ph.clock-counter-clockwise", "History"),
    ("archive", "ph.archive", "Archived Tabs"),
    ("notes", "ph.note-pencil", "Notes"),
    ("answers", "ph.compass", "Browse for Me"),
]
SHOW_AT_MOST = 600
ENTRY, HEADER = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 1


def when(ts: float) -> str:
    moment = datetime.fromtimestamp(ts)
    clock = moment.strftime("%-I:%M %p")
    today = datetime.now().date()
    if moment.date() == today:
        return clock
    if moment.date() == today - timedelta(days=1):
        return "Yesterday " + clock
    return moment.strftime("%b %-d, %Y" if moment.year != today.year else "%b %-d")


def day_label(ts: float) -> str:
    day = datetime.fromtimestamp(ts).date()
    today = datetime.now().date()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    if (today - day).days < 7:
        return day.strftime("%A")
    return day.strftime("%A, %B %-d" + (", %Y" if day.year != today.year else ""))


class EntryDelegate(QStyledItemDelegate):
    """Rows: favicon · title · site … time. Day headers are small caps."""

    def __init__(self, library):
        super().__init__(library)
        self.library = library

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 30 if index.data(HEADER) else 40)

    def paint(self, painter: QPainter, option, index):
        theme = self.library.browser.theme
        ink = QColor(theme.panel_text)
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        header = index.data(HEADER)
        font = QFont(painter.font())
        if header:
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            dim = QColor(ink)
            dim.setAlphaF(0.5)
            painter.setPen(dim)
            painter.drawText(rect.adjusted(10, 8, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             header.upper())
            painter.restore()
            return

        entry = index.data(ENTRY)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.accent)
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 8, 8)
            ink = QColor("white")
        elif option.state & QStyle.StateFlag.State_MouseOver:
            hover = QColor(ink)
            hover.setAlphaF(0.06)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover)
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 8, 8)

        entry["icon"].paint(painter, QRectF(rect.left() + 12, rect.center().y() - 8, 16, 16).toRect())
        left, right = rect.left() + 40, rect.right() - 12
        font.setPixelSize(12)
        painter.setFont(font)
        time_w = painter.fontMetrics().horizontalAdvance(entry["when"])
        dim = QColor(ink)
        dim.setAlphaF(0.55)
        painter.setPen(dim)
        painter.drawText(QRectF(right - time_w, rect.top(), time_w, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, entry["when"])
        right -= time_w + 16

        font.setPixelSize(13)
        painter.setFont(font)
        fm = painter.fontMetrics()
        title = fm.elidedText(entry["title"], Qt.TextElideMode.ElideRight, int((right - left) * 0.68))
        painter.setPen(ink)
        painter.drawText(QRectF(left, rect.top(), right - left, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        sub_left = left + fm.horizontalAdvance(title) + 12
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(dim)
        sub = painter.fontMetrics().elidedText(entry["subtitle"], Qt.TextElideMode.ElideRight, int(max(right - sub_left, 0)))
        painter.drawText(QRectF(sub_left, rect.top(), right - sub_left, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub)
        painter.restore()


class LibraryWindow(QWidget):
    def __init__(self, browser):
        super().__init__(None, Qt.WindowType.Window)
        self.browser = browser
        self.store = browser.store
        self.section = "history"
        self.setObjectName("LibraryWindow")
        self.setProperty("panel", True)
        self.setWindowTitle("Library")
        self.resize(940, 680)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("SettingsNav")
        self.nav.setFixedWidth(210)
        self.nav.setIconSize(QSize(16, 16))
        for _, _, title in SECTIONS:
            self.nav.addItem(title)
        self.nav.currentRowChanged.connect(lambda row: self.show_section(SECTIONS[row][0]))
        layout.addWidget(self.nav)

        right = QWidget()
        right.setObjectName("SettingsBody")
        v = QVBoxLayout(right)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(12)
        top = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("PanelTitle")
        self.count = QLabel()
        self.count.setObjectName("Hint")
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self._clear_all)
        top.addWidget(self.title)
        top.addSpacing(8)
        top.addWidget(self.count, 1)
        top.addWidget(self.clear_btn)
        v.addLayout(top)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self._open_current)
        v.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("LibraryList")
        quiet_scrollbars(self.list)
        self.list.setItemDelegate(EntryDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(False)
        self.list.itemActivated.connect(self._open_item)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.list, 1)
        self.empty = QLabel()
        self.empty.setObjectName("Hint")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty)
        layout.addWidget(right, 1)
        self.apply_theme()

    def apply_theme(self):
        theme = self.browser.theme
        self.setStyleSheet(stylesheet(theme))
        for i, (_, icon, _) in enumerate(SECTIONS):
            self.nav.item(i).setIcon(qta.icon(icon, color=theme.panel_text, color_active="white"))
        self.list.viewport().update()

    def open(self, section="history"):
        self.show_section(section)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        self.search.selectAll()

    def show_section(self, section):
        self.section = section
        row = [s for s, _, _ in SECTIONS].index(section)
        if self.nav.currentRow() != row:
            self.nav.setCurrentRow(row)
        self.title.setText(SECTIONS[row][2])
        self.clear_btn.setText({"history": "Clear History…", "archive": "Empty Archive…"}.get(section, ""))
        self.clear_btn.setVisible(section in ("history", "archive"))
        self.refresh()

    # --- Entries ---------------------------------------------------------------------------

    def _icon(self, url):
        if url.startswith("note:"):
            return qta.icon("ph.note-pencil", color=self.browser.theme.accent)
        if "/browse/" in url and url.startswith("file:"):
            return qta.icon("ph.compass", color=self.browser.theme.accent)
        return storage.favicon(url) or letter_icon(display_host(url) or "?", self.browser.theme.accent)

    def _entries(self) -> list[dict]:
        store = self.store
        if self.section == "history":
            return [{"url": h["url"], "title": h.get("title") or h["url"], "ts": h["ts"],
                     "subtitle": display_host(h["url"]), "source": h} for h in store.history]
        if self.section == "archive":
            names = {s.id: s.name for s in store.spaces}
            return [{"url": a["url"], "title": a.get("title") or a["url"], "ts": a.get("archived_at", 0),
                     "subtitle": " · ".join(filter(None, [display_host(a["url"]), names.get(a.get("space_id"))])),
                     "source": a} for a in store.archive]
        folder = storage.data_dir() / ("notes" if self.section == "notes" else "browse")
        entries = []
        for path in sorted(folder.glob("*.md" if self.section == "notes" else "*.html"),
                           key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []:
            text = path.read_text(errors="replace")
            if self.section == "notes":
                first = next((l.strip().lstrip("#*-• ").strip() for l in text.splitlines() if l.strip()), "")
                title, url, sub = first or "Untitled Note", "note:" + path.stem, f"{len(text.split())} words"
            else:
                match = re.search(r"<title>(.*?)</title>", text, re.S)
                title = (match.group(1) if match else path.stem).replace("✦", "").strip()
                url, sub = QUrl.fromLocalFile(str(path)).toString(), f"{text.count('class=\"source\"')} sources"
            entries.append({"url": url, "title": title, "ts": path.stat().st_mtime, "subtitle": sub, "path": path})
        return entries

    def refresh(self, *_):
        query = self.search.text().strip().lower()
        entries = [e for e in self._entries()
                   if not query or query in e["title"].lower() or query in e["url"].lower()]
        self.count.setText(f"{len(entries):,}" + (" matching" if query else ""))
        self.list.clear()
        last_day = None
        for entry in entries[:SHOW_AT_MOST]:
            day = day_label(entry["ts"]) if entry["ts"] else "Earlier"
            if self.section in ("history", "archive") and day != last_day:
                header = QListWidgetItem()
                header.setData(HEADER, day)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(header)
                last_day = day
            entry["icon"] = self._icon(entry["url"])
            entry["when"] = when(entry["ts"]) if entry["ts"] else ""
            item = QListWidgetItem()
            item.setData(ENTRY, entry)
            item.setToolTip(entry["url"] if not entry["url"].startswith("file:") else entry["title"])
            self.list.addItem(item)
        empty = {"history": "No history yet.", "archive": "Nothing archived yet. Today tabs you stop using end up here.",
                 "notes": "No notes yet. ⇧⌘N starts one.", "answers": "No Browse for Me answers yet. Ask a question "
                                                                    "in ⌘T and press ⌘↩."}[self.section]
        self.empty.setText("Nothing matches." if query else empty)
        self.empty.setVisible(not entries)
        self.list.setVisible(bool(entries))
        first = next((i for i in range(self.list.count()) if self.list.item(i).data(ENTRY)), None)
        if first is not None:
            self.list.setCurrentRow(first)

    # --- Actions ---------------------------------------------------------------------------

    def _open_current(self):
        item = self.list.currentItem()
        if item is not None and item.data(ENTRY):
            self._open_item(item)

    def _open_item(self, item, little=False):
        entry = item.data(ENTRY)
        if not entry:
            return
        url = entry["url"]
        if little and not url.startswith("note:"):
            self.browser.new_little_window(url)
            return
        existing = next((t for t in self.store.all_tabs() if t.url == url), None)
        if existing is not None and (url.startswith("note:") or url.startswith("file:")):
            self.browser.activate_tab(existing)  # notes and answers already open: just go there
        else:
            self.browser.open_url(url)
        if self.section == "archive":
            # Compared by value: Qt hands list items back as copies, so identity never matches
            self.store.archive = [a for a in self.store.archive if a != entry["source"]]  # restored
            self.store.schedule_save()
            self.refresh()
        self.browser.bring_to_front()

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        entry = item.data(ENTRY) if item else None
        if not entry:
            return
        b = self.browser
        menu = b._menu(self)
        b._act(menu, "ph.arrow-square-out", "Open", lambda: self._open_item(item))
        if not entry["url"].startswith("note:"):
            b._act(menu, "ph.app-window", "Open in Little Window", lambda: self._open_item(item, little=True))
        if entry["url"].startswith("http"):
            b._act(menu, "ph.link", "Copy Link", lambda: QGuiApplication.clipboard().setText(entry["url"]))
        menu.addSeparator()
        b._act(menu, "ph.trash", "Delete" if "path" in entry else "Remove", lambda: self._remove(entry))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _remove(self, entry):
        if self.section == "history":
            # By URL: the entry may have been replaced since this list was drawn (you revisited it)
            self.store.history = [h for h in self.store.history if h["url"] != entry["url"]]
            self.browser.page_index.forget_url(entry["url"])  # and what that page said
        elif self.section == "archive":
            self.store.archive = [a for a in self.store.archive if a != entry["source"]]
        else:
            kind = "note" if self.section == "notes" else "answer"
            answer = QMessageBox.question(self, f"Delete {kind.capitalize()}", f"Delete “{entry['title']}”? "
                                          "This can't be undone.")
            if answer != QMessageBox.StandardButton.Yes:
                return
            for tab in [t for t in self.store.all_tabs() if t.url == entry["url"]]:
                self.browser.delete_tab(tab)  # close it first, so an open note doesn't save itself back
            entry["path"].unlink(missing_ok=True)
        self.store.schedule_save()
        self.refresh()

    def _clear_all(self):
        if self.section == "history":
            answer = QMessageBox.question(self, "Clear History", f"Delete all {len(self.store.history):,} history "
                                                                 "entries?")
            if answer == QMessageBox.StandardButton.Yes:
                self.store.history = []
                self.browser.page_index.clear()  # what you read goes with where you went
        elif self.section == "archive":
            answer = QMessageBox.question(self, "Empty Archive", f"Delete all {len(self.store.archive):,} archived "
                                                                 "tabs?")
            if answer == QMessageBox.StandardButton.Yes:
                self.store.archive = []
        self.store.schedule_save()
        self.refresh()
