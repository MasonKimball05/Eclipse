# Ask About This Page (⇧⌘A): a panel beside the page for a conversation about it, the follow-up questions
# Ask on Page (⌘F) can't take. Each tab has its own conversation; every question reads the page again, so
# answers follow what's on it now. Asked like every other Claude feature (ai.py): the page is untrusted
# text, and the CLI that answers has no tools, so the worst a page can do is make an answer wrong.
#
# Beside the page, drag its inner edge to make it wider or narrower. Or pop it out (the ⧉ button) into a
# small window of its own, which stays on top of other windows while its pin is in (click the pin to let
# it go behind them), and still follows the tab you're on. The ⧉ in it puts it back beside the page.

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QKeySequence, QPainter, QShortcut
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

import qtawesome as qta

from .ai import page_block
from .features import Feature
from .models import display_host

WIDTH = 320               # beside the page, until you drag its edge
MIN_WIDTH, MAX_WIDTH = 260, 600
EDGE = 7                  # points of its inner edge that take hold to resize it
WIDTH_SETTING = "page_chat_width"
POPPED_SETTING = "page_chat_popped"
ON_TOP_SETTING = "page_chat_on_top"
GEOMETRY_SETTING = "page_chat_window"
STARTERS = [("Summarize", "Summarize this page in a few bullet points."),
            ("Key points", "What are the most important points or facts on this page?"),
            ("Explain simply", "Explain what this page is about in plain language, as if I'm new to the topic.")]
HISTORY_TURNS = 6  # earlier questions and answers sent along with a new one (older ones drop off)


def prompt_for(title, url, text, turns, question) -> str:
    """What Claude is asked: the page, the conversation so far, and the new question."""
    earlier = "".join(f"\nQuestion: {q}\nAnswer: {a}\n" for q, a in turns[-HISTORY_TURNS:] if a)
    return (page_block(title, url, text) +
            ("\n\nThe conversation about this page so far:\n" + earlier if earlier else "") +
            "\n\nUsing the page above, answer the question below in at most 180 words, using Markdown where it "
            "helps. If the page doesn't cover it, say so briefly, then answer from general knowledge if you "
            "can.\n\nQuestion: " + question)


def transcript(turns) -> str:
    """The conversation as Markdown: each question in bold, then its answer."""
    parts = []
    for question, answer in turns:
        parts.append(f"**{question}**\n\n{answer or '*Reading the page…*'}")
    return "\n\n\u00a0\n\n".join(parts)  # (a little space between them: a rule drew a hard line across)


class ChatEdge(QWidget):
    """The panel's edge on the page's side: drag to resize it, double-click for its usual width."""

    def __init__(self, chat):
        super().__init__(chat)
        self.chat = chat
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip("Drag to resize · double-click to reset")
        self._drag = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().x(), self.chat.width())

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            start, width = self._drag
            moved = event.globalPosition().x() - start
            # (beside a page on its left, it grows leftwards; with the sidebar on the right it's the other way)
            self.chat.set_width(width + moved if self.chat.page_on_right() else width - moved, save=False)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.chat.set_width(self.chat.width())

    def mouseDoubleClickEvent(self, event):
        self.chat.set_width(WIDTH)


class ChatWindow(QWidget):
    """The panel popped out: a small window of its own, on the Space's colours."""

    def __init__(self, chat):
        super().__init__(chat.browser, Qt.WindowType.Window)  # (it closes with its browser window)
        self.chat = chat
        self.setWindowTitle("Ask About This Page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 14)
        settings = chat.browser.store.settings
        if not self.restoreGeometry(QByteArray.fromBase64(settings.get(GEOMETRY_SETTING, "").encode())):
            self.resize(380, 560)
        self.setMinimumSize(280, 320)
        shortcut = QShortcut(QKeySequence("Ctrl+Shift+A"), self)  # (⇧⌘A works from here too)
        shortcut.activated.connect(chat.toggle)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), self.chat.browser.theme.gradient(self.width(), self.height()))
        p.end()

    def remember(self):
        self.chat.browser.store.settings[GEOMETRY_SETTING] = bytes(self.saveGeometry().toBase64()).decode()
        self.chat.browser.store.schedule_save()

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible():
            self.remember()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self.remember()

    def closeEvent(self, event):
        """Its red button: the panel's put away (popped out it stays, for next time)."""
        self.chat.cancel()
        event.accept()


class PageChat(QFrame):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("PageChat")
        self.setFixedWidth(self._saved_width())
        self.window_ = None  # (its own window, once it's been popped out)
        self.tab = None
        self.chats: dict[str, list[list[str]]] = {}  # tab id → [[question, answer], …]
        self.request = None
        self._asking_tab = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 0)  # (it's on the window's colours: the gap round the page is its margin)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.title = QLabel("Ask About This Page")
        self.title.setObjectName("PageChatTitle")
        self.site = QLabel()
        self.site.setObjectName("PageChatSite")
        words = QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(self.title)
        words.addWidget(self.site)
        top.addLayout(words, 1)
        self.new_btn = self._icon_button("ph.arrow-counter-clockwise", "New conversation", self.clear)
        self.pin_btn = self._icon_button("ph.push-pin-fill", "", self.toggle_on_top)
        self.pop_btn = self._icon_button("ph.arrow-square-out", "Pop out into a window of its own", self.toggle_popped)
        self.close_btn = self._icon_button("ph.x", "Close (⇧⌘A)", self.toggle)
        for button in (self.new_btn, self.pin_btn, self.pop_btn, self.close_btn):
            top.addWidget(button)
        layout.addLayout(top)

        self.view = QTextBrowser()
        self.view.setObjectName("Answer")
        self.view.setOpenLinks(False)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.document().setDocumentMargin(2)
        self.view.document().setIndentWidth(16)  # (lists sit in close: it's a narrow column)
        # Only web links open: an answer is the model's words, which a page can steer, so a data:, file: or app
        # link in one goes nowhere
        self.view.anchorClicked.connect(lambda url: url.scheme() in ("http", "https") and browser.open_url(url.toString()))
        layout.addWidget(self.view, 1)

        self.starters = QVBoxLayout()
        self.starters.setSpacing(6)
        self.starter_buttons = []
        for label, question in STARTERS:
            button = QPushButton(label)
            button.setObjectName("PageChatStarter")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, q=question, l=label: self.ask(q, shown=l))
            self.starters.addWidget(button)
            self.starter_buttons.append(button)
        layout.addLayout(self.starters)

        self.input = QLineEdit()
        self.input.setObjectName("PageChatInput")
        self.input.setPlaceholderText("Ask about this page…")
        self.input.returnPressed.connect(lambda: self.ask(self.input.text()))
        layout.addWidget(self.input)
        self.edge = ChatEdge(self)
        self.hide()
        self.restyle()

    def restyle(self):
        """The Space's colours changed (or it moved): the buttons' icons and its edge follow."""
        on_top = self.browser.store.settings.get(ON_TOP_SETTING, True)
        icons = ((self.new_btn, "ph.arrow-counter-clockwise"), (self.close_btn, "ph.x"),
                 (self.pop_btn, "ph.arrow-square-in" if self.popped else "ph.arrow-square-out"),
                 (self.pin_btn, "ph.push-pin-fill" if on_top else "ph.push-pin"))
        for button, icon in icons:
            button.setIcon(qta.icon(icon, color=self.browser.theme.text))
        self.pop_btn.setToolTip("Put back beside the page" if self.popped else "Pop out into a window of its own")
        self.pin_btn.setToolTip("Kept on top of other windows: click to let it go behind them" if on_top else
                                "Keep on top of other windows")
        self.pin_btn.setVisible(self.popped)
        self.edge.setVisible(not self.popped)
        # (beside the page, room on its page side for the edge you drag; in its window, the window's margins)
        side = 0 if self.popped else EDGE + 1
        left, right = (2, side) if self.page_on_right() else (side, 2)
        self.layout().setContentsMargins(0 if self.popped else left, 4, 0 if self.popped else right, 0)
        if self.window_ is not None:
            self.window_.update()

    # --- Where it is: beside the page (resizable), or in a window of its own --------------------------------

    @property
    def popped(self) -> bool:
        return self.window_ is not None and self.parentWidget() is self.window_

    def page_on_right(self) -> bool:
        """Beside the page, which side of it the page is on (the panel's on the far side from the sidebar)."""
        return self.browser.sidebar_on_right

    def _saved_width(self) -> int:
        return min(max(int(self.browser.store.settings.get(WIDTH_SETTING, WIDTH)), MIN_WIDTH), MAX_WIDTH)

    def set_width(self, width, save=True):
        width = round(min(max(width, MIN_WIDTH), MAX_WIDTH))
        if not self.popped:
            self.setFixedWidth(width)
        if save:
            self.browser.store.settings[WIDTH_SETTING] = width
            self.browser.store.schedule_save()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        x = self.width() - EDGE if self.page_on_right() else 0
        self.edge.setGeometry(x, 0, EDGE, self.height())
        self.edge.raise_()

    def is_open(self) -> bool:
        return self.window_.isVisible() if self.popped else self.isVisible()

    def toggle(self):
        """⇧⌘A: open it where it was last (beside the page or in its window), or put it away."""
        if self.is_open():
            self.cancel()
            if self.popped:
                self.window_.hide()
            else:
                self.hide()
            view = self.browser._current_view()
            if view is not None:
                self.browser.activateWindow()
                view.setFocus()
            return
        self.follow(self.browser.current_tab)
        if self.browser.store.settings.get(POPPED_SETTING, False):
            self.pop_out()
        else:
            self.dock()
        self.input.setFocus()

    def toggle_popped(self):
        if self.popped:
            self.window_.hide()
            self.dock()
        else:
            self.hide()
            self.pop_out()
        self.input.setFocus()

    def dock(self):
        """Beside the page, on the far side from the sidebar."""
        layout = self.browser.content_layout
        if self.popped:
            self.window_.layout().removeWidget(self)
        layout.removeWidget(self)
        self.setParent(layout.parentWidget())
        layout.insertWidget(0 if self.browser.sidebar_on_right else 1, self)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.setFixedWidth(self._saved_width())
        self.browser.store.settings[POPPED_SETTING] = False
        self.restyle()
        self.show()

    def pop_out(self):
        if self.window_ is None:
            self.window_ = ChatWindow(self)
        self.browser.content_layout.removeWidget(self)
        self.window_.layout().addWidget(self)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        self.browser.store.settings[POPPED_SETTING] = True
        self._apply_on_top()
        self.restyle()
        self.show()
        self.window_.show()
        self.window_.raise_()
        self.window_.activateWindow()

    def toggle_on_top(self):
        settings = self.browser.store.settings
        settings[ON_TOP_SETTING] = not settings.get(ON_TOP_SETTING, True)
        self.browser.store.schedule_save()
        self._apply_on_top()
        self.restyle()

    def _apply_on_top(self):
        if self.window_ is None:
            return
        on_top = self.browser.store.settings.get(ON_TOP_SETTING, True)
        if bool(self.window_.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != on_top:
            shown = self.window_.isVisible()
            self.window_.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
            if shown:
                self.window_.show()  # (a window shows again after its flags change)

    def _icon_button(self, icon, tip, slot):
        button = QPushButton()
        button.setObjectName("PageChatButton")
        button.setIcon(qta.icon(icon, color=self.browser.theme.text))
        button.setToolTip(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    # --- Following the tab you're on ---------------------------------------------------------------------

    def follow(self, tab):
        """The tab on screen changed: show its conversation (or say there's nothing to ask about)."""
        if self.tab is not None and not sip.isdeleted(self.tab):
            try:
                self.tab.changed.disconnect(self._tab_changed)
            except TypeError:
                pass
        self.tab = tab
        if tab is not None:
            tab.changed.connect(self._tab_changed)  # (its page moves on: the site's name follows)
        self.refresh()

    def _tab_changed(self):
        if self.isVisible():
            self.refresh()

    def _askable(self) -> bool:
        tab = self.tab
        return (tab is not None and tab.view is not None and hasattr(tab.view, "page")
                and tab.url.startswith(("http://", "https://", "file:")))

    def refresh(self):
        tab = self.tab
        turns = self.chats.get(tab.id, []) if tab is not None else []
        askable = self._askable()
        ai = self.browser.assistant()
        self.site.setText(display_host(tab.url) if askable else "")
        if not askable:
            self.view.setMarkdown("*Open a web page to ask about it.*")
        elif ai is None:
            self.view.setMarkdown("*Turn on Claude features (View → Claude Features) to ask about pages.*")
        else:
            self.view.setMarkdown(transcript(turns) or f"*Ask {self.browser.assistant_name(ai)} anything about "
                                                       "this page, then follow up.*")
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
        for button in self.starter_buttons:
            button.setVisible(askable and ai is not None and not turns)
        self.input.setEnabled(askable and ai is not None)
        self.new_btn.setVisible(bool(turns))

    # --- Asking --------------------------------------------------------------------------------------------

    def ask(self, question, shown=None):
        question = question.strip()
        ai = self.browser.assistant()
        if not question or not self._askable() or ai is None:
            return
        self.cancel()
        tab = self.tab
        turns = self.chats.setdefault(tab.id, [])
        earlier = [list(t) for t in turns]
        turns.append([shown or question, ""])
        self.input.clear()
        self._asking_tab = tab
        self.refresh()

        def got_text(text):
            if tab.view is None or sip.isdeleted(self) or self._asking_tab is not tab:
                return  # (closed, or asked something else meanwhile)
            request = ai.ask(prompt_for(tab.label, tab.url, text, earlier, question))
            self.request = request
            request.partial.connect(lambda answer: self._answer(tab, answer))
            request.finished.connect(lambda answer: self._answer(tab, answer, done=True))
            request.failed.connect(lambda message: self._answer(tab, f"**Couldn't ask:** {message}", done=True))
        tab.view.page().toPlainText(got_text)

    def _answer(self, tab, answer, done=False):
        turns = self.chats.get(tab.id)
        if not turns or sip.isdeleted(self):
            return
        turns[-1][1] = answer
        if done:
            self.request = None
            self._asking_tab = None
        if tab is self.tab:
            self.refresh()

    def cancel(self):
        if self.request is not None:
            self.request.cancel()
            self.request = None
        if self._asking_tab is not None:
            turns = self.chats.get(self._asking_tab.id)
            if turns and not turns[-1][1]:
                turns.pop()  # (asked and never answered: it goes)
            self._asking_tab = None

    def clear(self):
        """A new conversation about this tab."""
        self.cancel()
        if self.tab is not None:
            self.chats.pop(self.tab.id, None)
        self.refresh()
        self.input.setFocus()

    def forget(self, tab):
        """A tab closed: its conversation goes with it."""
        self.chats.pop(tab.id, None)


class PageChatFeature(Feature):
    """Ask About This Page (⇧⌘A): beside the page on the window's own colours, like a second sidebar (on the
    far side from the first), or popped out into a window of its own."""
    name = "page_chat"
    chat = None

    def ready(self):
        self.chat = PageChat(self.window, self.window.content_wrap)

    def toggle(self):
        self.chat.toggle()

    def tab_shown(self, tab):
        if self.chat is not None:
            self.chat.follow(tab)

    def tab_closed(self, tab):
        if self.chat is not None:
            self.chat.forget(tab)

    def theme_changed(self):
        if self.chat is not None:
            self.chat.restyle()

    def commands(self):
        return [("Ask About This Page", "ph.chats-circle", self.toggle)]

    def menus(self, add, menus, bar):
        add(menus["View"], "Ask About This Page", "Ctrl+Shift+A", self.toggle)


FEATURE = PageChatFeature
