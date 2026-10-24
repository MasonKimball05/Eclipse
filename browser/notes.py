# Arc's Notes: a page you write in, living in the sidebar like any other tab.
#
# A note is a Tab whose URL is "note:<id>"; instead of a web page it gets a rich-text
# editor. The text is saved as Markdown in the data folder (notes/<id>.md) as you type.

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QKeySequence, QShortcut, QTextCharFormat
from PyQt6.QtWidgets import QTextEdit

from . import storage
from .models import new_id

NOTE_SCHEME = "note:"


def is_note(url: str) -> bool:
    return url.startswith(NOTE_SCHEME)


def new_note_url() -> str:
    return NOTE_SCHEME + new_id()


def _path(url):
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
        self.setAutoFormatting(QTextEdit.AutoFormattingFlag.AutoBulletList)  # "* " starts a list
        self.document().setDocumentMargin(48)
        self.setPlaceholderText("Untitled Note\n\nStart writing…  (⌘B bold, ⌘I italic, ⌘U underline, \"* \" for a list)")
        if self.path.exists():
            self.setMarkdown(self.path.read_text())
        self.document().setModified(False)  # setup (margins, loading the file) isn't an edit
        self._save_timer = QTimer(self, singleShot=True, interval=600, timeout=self.save)
        self.textChanged.connect(self._edited)

        for keys, toggle in (("Ctrl+B", self._bold), ("Ctrl+I", self._italic), ("Ctrl+U", self._underline)):
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
