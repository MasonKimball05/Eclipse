# Arc's Notes: a page you write in, living in the sidebar like any other tab.
#
# A note is a Tab whose URL is "note:<id>"; instead of a web page it gets a rich-text
# editor. The text is saved as Markdown in the data folder (notes/<id>.md) as you type.
#
# Typed shorthand, as in Arc and Apple Notes: "* " or "- " starts a list, "[] " a checklist (click a box,
# or ⇧⌘L, to tick it), and "# ", "## " or "### " a heading. Return on an empty list item ends the list.

import re

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QFont, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat,
                         QTextListFormat)
from PyQt6.QtWidgets import QTextEdit

from . import storage
from .features import Feature
from .models import new_id
from .shortcuts import for_here

NOTE_SCHEME = "note:"
CHECKLIST = ("[]", "[ ]", "-[]", "- [ ]")  # typed at the start of a line, then a space
Marker = QTextBlockFormat.MarkerType


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # (what new_id() makes: never a path, so "note:../x" isn't a note)


def is_note(url: str) -> bool:
    return url.startswith(NOTE_SCHEME) and bool(_ID.fullmatch(url[len(NOTE_SCHEME):]))


def new_note_url() -> str:
    return NOTE_SCHEME + new_id()


def _path(url):
    if not is_note(url):  # (a page can make a tab of any address: only real note ids name a file)
        raise ValueError(f"not a note: {url[:80]}")
    folder = storage.data_dir() / "notes"
    folder.mkdir(exist_ok=True)
    return folder / (url[len(NOTE_SCHEME):] + ".md")


class NoteEditor(QTextEdit):
    title_changed = pyqtSignal(str)

    def __init__(self, url):
        super().__init__()
        self.path = _path(url)
        self.setObjectName("NoteEditor")
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.document().setDocumentMargin(48)
        self.viewport().setMouseTracking(True)  # (a hand over a checklist's boxes)
        self.setPlaceholderText(for_here("Untitled Note\n\nStart writing…  (⌘B bold, ⌘I italic, ⌘U underline, \"* \" for a "
                                         "list, \"[] \" for a checklist, \"# \" for a heading)"))
        if self.path.exists():
            self.setMarkdown(self.path.read_text())
        self.document().setModified(False)  # setup (margins, loading the file) isn't an edit
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.save)
        self.textChanged.connect(self._edited)

        for keys, toggle in (("Ctrl+B", self._bold), ("Ctrl+I", self._italic), ("Ctrl+U", self._underline),
                             ("Ctrl+Shift+L", self.toggle_checklist)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(toggle)

    @property
    def title(self) -> str:
        for line in self.toPlainText().splitlines():
            if line.strip():
                return line.strip().lstrip("#•*- ").strip()[:80]
        return "Untitled Note"

    def _edited(self):
        self._save_timer.start()
        self.title_changed.emit(self.title)

    def save(self):
        # Only an edited editor writes, so a second, stale editor on the same note can't
        # overwrite newer text when it closes
        self._save_timer.stop()
        if self.document().isModified():
            self.path.write_text(self.toMarkdown())
            self.document().setModified(False)

    def _merge(self, fmt):
        cursor = self.textCursor()
        cursor.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)

    def _bold(self):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Weight.Normal if self.fontWeight() > QFont.Weight.Normal.value else QFont.Weight.Bold)
        self._merge(fmt)

    def _italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.fontItalic())
        self._merge(fmt)

    def _underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.fontUnderline())
        self._merge(fmt)

    # --- Checklists and headings -----------------------------------------------------------------------

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        block = cursor.block()
        if event.key() == Qt.Key.Key_Space and not cursor.hasSelection():
            typed = block.text()[:cursor.positionInBlock()]
            if typed in CHECKLIST:
                self._take(cursor, len(typed))
                self.make_checklist(cursor)
                return
            if typed in ("*", "-") and block.textList() is None:  # (Qt's own auto-bullet left a stray space)
                self._take(cursor, len(typed))
                cursor.createList(QTextListFormat.Style.ListDisc)
                return
            if typed in ("#", "##", "###") and block.textList() is None:
                self._take(cursor, len(typed))
                self.set_heading(cursor, len(typed))
                return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            heading = block.blockFormat().headingLevel()
            ticked = block.textList() is not None and block.blockFormat().marker() != Marker.NoMarker
            if ticked and not block.text().strip():  # an empty item: the checklist ends here
                block.textList().remove(block)
                fmt = cursor.blockFormat()  # (fresh: the old one still names the list)
                fmt.setMarker(Marker.NoMarker)
                fmt.setIndent(0)
                cursor.setBlockFormat(fmt)
                return
            super().keyPressEvent(event)
            if heading:  # the line after a heading is ordinary text again
                self.set_heading(self.textCursor(), 0)
            elif ticked:  # a new item starts unticked, whatever the last one was
                fmt = self.textCursor().blockFormat()
                fmt.setMarker(Marker.Unchecked)
                self.textCursor().setBlockFormat(fmt)
            return
        super().keyPressEvent(event)

    def _take(self, cursor, count):
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter, QTextCursor.MoveMode.KeepAnchor, count)
        cursor.removeSelectedText()

    def make_checklist(self, cursor=None):
        cursor = cursor or self.textCursor()
        if cursor.block().textList() is None:
            cursor.createList(QTextListFormat.Style.ListDisc)
        fmt = cursor.blockFormat()
        fmt.setMarker(Marker.Unchecked)
        cursor.setBlockFormat(fmt)

    def toggle_checklist(self):
        """⇧⌘L: the line becomes a checklist item; on one already, its box is ticked or unticked."""
        cursor = self.textCursor()
        if cursor.block().blockFormat().marker() == Marker.NoMarker:
            self.make_checklist(cursor)
        else:
            self.toggle_box(cursor.block())

    def toggle_box(self, block):
        fmt = block.blockFormat()
        fmt.setMarker(Marker.Unchecked if fmt.marker() == Marker.Checked else Marker.Checked)
        QTextCursor(block).setBlockFormat(fmt)

    def set_heading(self, cursor, level):
        """`level` 1–3 for a heading (as Markdown's # to ###), 0 for ordinary text."""
        fmt = cursor.blockFormat()
        fmt.setHeadingLevel(level)
        cursor.setBlockFormat(fmt)
        char = QTextCharFormat()
        char.setProperty(QTextFormat.Property.FontSizeAdjustment, 4 - level if level else 0)  # (as Qt's Markdown does)
        char.setFontWeight(QFont.Weight.Bold if level else QFont.Weight.Normal)
        line = QTextCursor(cursor.block())
        line.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        line.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        line.mergeCharFormat(char)
        self.mergeCurrentCharFormat(char)  # (what's typed next)

    def box_at(self, pos):
        """The checklist item whose box is at `pos` (in the viewport), if any."""
        cursor = self.cursorForPosition(pos)
        block = cursor.block()
        if block.textList() is None or block.blockFormat().marker() == Marker.NoMarker:
            return None
        start = self.cursorRect(QTextCursor(block))  # where its text starts: the box is just before
        if start.left() - 26 <= pos.x() < start.left() and start.top() - 2 <= pos.y() <= start.bottom() + 2:
            return block
        return None

    def mousePressEvent(self, event):
        block = self.box_at(event.position().toPoint()) if event.button() == Qt.MouseButton.LeftButton else None
        if block is not None:
            self.toggle_box(block)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if not event.buttons():  # (a hand over a box: it can be clicked)
            over = self.box_at(event.position().toPoint()) is not None
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.IBeamCursor)


class NotesFeature(Feature):
    """Notes: tabs you write in (note:<id>), made with ⇧⌘N or ⌘T."""
    name = "notes"

    def view_for(self, tab):
        return NoteEditor(tab.url) if is_note(tab.url) else None

    def new_note(self):
        from .models import TODAY, Tab
        window = self.window
        tab = Tab(new_note_url(), "Untitled Note")
        window.store.add_tab(tab, TODAY, window.store.active_space, 0)
        window.activate_tab(tab)

    def commands(self):
        return [("New Note", "ph.note-pencil", self.new_note)]

    def menus(self, add, menus, bar):
        add(menus["File"], "New Note", "Ctrl+Shift+N", self.new_note)


FEATURE = NotesFeature
