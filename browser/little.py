# "Little Arc": a small, quick window for a one-off page (⌥⌘N, or a page's
# popup). If it turns out you want to keep it, "Open in Main Window" moves the
# live page into the current Space as a Today tab without reloading it.

import qtawesome as qta
from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget

from .command_bar import search_url, to_url
from .theme import stylesheet


class LittleWindow(QWidget):
    def __init__(self, browser, url=None, request=None):
        super().__init__(None, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.browser = browser
        self.setWindowTitle("Little Window")
        self.resize(560, 700)
        self.setStyleSheet(stylesheet(browser.theme))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("LittleHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 6, 8, 6)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Search or Enter URL…")
        self.url_edit.returnPressed.connect(self._go)
        open_btn = QToolButton()
        open_btn.setObjectName("IconButton")
        open_btn.setIcon(qta.icon("ph.arrow-square-out", color=browser.theme.text))
        open_btn.setIconSize(QSize(16, 16))
        open_btn.setToolTip("Open in Main Window")
        open_btn.clicked.connect(self.promote)
        header_layout.addWidget(self.url_edit, 1)
        header_layout.addWidget(open_btn)
        layout.addWidget(header)

        self.view = browser.create_view()
        self.view.titleChanged.connect(self.setWindowTitle)
        self.view.urlChanged.connect(self._on_url)
        self.view.page().windowCloseRequested.connect(self.close)
        self.view.page().newWindowRequested.connect(lambda req: browser.handle_new_window(None, req))
        layout.addWidget(self.view, 1)

        if request is not None:
            request.openIn(self.view.page())
        elif url:
            self.view.load(QUrl(url))
        else:
            self.url_edit.setFocus()

    def _go(self):
        text = self.url_edit.text().strip()
        if text:
            self.view.load(QUrl(to_url(text) or search_url(text)))
            self.view.setFocus()

    def _on_url(self, url):
        if not self.url_edit.hasFocus():
            self.url_edit.setText(url.toString())
            self.url_edit.setCursorPosition(0)

    def promote(self):
        view, self.view = self.view, None
        if view is not None and not view.url().isEmpty():
            self.browser.adopt_view(view)
        self.close()
