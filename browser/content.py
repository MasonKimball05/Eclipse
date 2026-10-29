# The right-hand side of the window: the rounded "card" that holds the web
# pages, plus the small floating overlays Arc draws on top of it.

import qtawesome as qta
from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QUrl, QVariantAnimation
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget, QTextBrowser,
                             QToolButton, QVBoxLayout, QWidget)

from . import motion, shortcuts
from .models import display_host

RADIUS = 10
ANSWER_MAX_HEIGHT = 340


class Corner(QWidget):
    """Paints the window background over one corner of the page so it looks rounded.

    Web content can't be clipped with a stylesheet border-radius, so instead each
    corner gets a tiny overlay painted with the same gradient as the window.
    """

    def __init__(self, browser, which, parent):
        super().__init__(parent)
        self.browser = browser
        self.which = which  # "tl", "tr", "bl", "br"
        self.setFixedSize(RADIUS, RADIUS)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        window = self.window()
        offset = self.mapTo(window, QPointF(0, 0))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(0, 0, RADIUS, RADIUS))
        cx = 0 if self.which[1] == "r" else RADIUS
        cy = 0 if self.which[0] == "b" else RADIUS
        circle = QPainterPath()
        circle.addEllipse(QPointF(cx, cy), RADIUS, RADIUS)
        p.fillPath(path.subtracted(circle),
                   self.browser.theme.gradient(window.width(), window.height(), offset.x(), offset.y()))


def answer_browser(browser, margin=12) -> QTextBrowser:
    # Markdown view for Claude's answers; links open as new tabs
    view = QTextBrowser()
    view.document().setDocumentMargin(margin)
    view.setObjectName("Answer")
    view.setOpenLinks(False)
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.anchorClicked.connect(lambda url: browser.open_url(url.toString()))
    view.hide()
    return view


def fit_answer(view, width, text, max_height=ANSWER_MAX_HEIGHT):
    # `width` is the view's full width: it has no frame or padding, only the document margin
    view.setMarkdown(text)
    view.document().setTextWidth(width)
    view.setFixedHeight(min(int(view.document().size().height()) + 4, max_height))
    view.show()


class FindBar(QFrame):
    """⌘F. Plain text finds on the page; a question + Return asks Claude about the page (Arc's "Ask on Page")."""

    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.request = None
        self._ask_id = 0
        self.setObjectName("FindBar")
        self.setFixedWidth(340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(2)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Find, or ask a question…")
        self.input.textChanged.connect(self._on_text)
        self.input.returnPressed.connect(self._on_return)
        self.count = QLabel("")
        self.ask_hint = QLabel("↩ Ask Claude")
        self.ask_hint.hide()
        self.prev_btn = self._button("ph.caret-up", lambda: self.find(backward=True))
        self.next_btn = self._button("ph.caret-down", lambda: self.find())
        self.close_btn = self._button("ph.x", self.close_bar)
        row.addWidget(self.input, 1)
        row.addWidget(self.ask_hint)
        row.addWidget(self.count)
        for btn in (self.prev_btn, self.next_btn, self.close_btn):
            row.addWidget(btn)
        outer.addLayout(row)
        self.answer = answer_browser(browser)
        outer.addWidget(self.answer)
        self.hide()

    def _button(self, icon, slot):
        btn = QToolButton()
        btn.setObjectName("IconButton")
        btn.setFixedSize(24, 24)
        btn.setProperty("icon_name", icon)
        btn.clicked.connect(slot)
        return btn

    TOP = 12  # where it sits, below the page's top edge

    def open(self):
        for btn in (self.prev_btn, self.next_btn, self.close_btn):
            btn.setIcon(qta.icon(btn.property("icon_name"), color=self.browser.theme.panel_text))
        if not self.isVisible() or getattr(self, "_closing", False):
            # It slides down into place from just above the page
            self._closing = False
            if not self.isVisible():
                self.move(self.x(), -self.height())
            motion.animate(self, self.y(), self.TOP, lambda y: self.move(self.x(), round(y)), motion.NORMAL + 40,
                           easing=motion.spring())
        self.show()
        self.raise_()
        self.input.setFocus()
        self.input.selectAll()
        self.find()

    def close_bar(self):
        self._cancel()
        self.browser.find_in_page("")
        self._closing = True

        def gone():
            if self._closing:
                self._closing = False
                self.answer.hide()
                self.hide()
        motion.animate(self, self.y(), -self.height() - 4, lambda y: self.move(self.x(), round(y)), motion.FAST, gone)
        self.count.setText("")

    def find(self, backward=False):
        self.browser.find_in_page(self.input.text(), backward)

    def show_result(self, active, total):
        self.count.setText(f"{active}/{total}" if self.input.text() and not self.ask_hint.isVisible() else "")

    def _is_question(self):
        claude = self.browser.feature("claude")  # (Ask on Page: a question asks Claude about the page)
        return claude is not None and claude.is_question(self.input.text())

    def _on_text(self):
        self.ask_hint.setVisible(self._is_question())
        if self.ask_hint.isVisible():
            self.count.setText("")
        self.find()

    def _on_return(self):
        if self._is_question():
            self.ask()
        else:
            self.find()

    # --- Ask on Page ---------------------------------------------------------------

    def ask(self):
        self._cancel()
        self._ask_id += 1
        ask_id = self._ask_id
        self._show_answer("*Reading the page…*")
        self.browser.feature("claude").ask_page(self.input.text().strip(), lambda req: self._attach(req, ask_id))

    def _attach(self, request, ask_id):
        if ask_id != self._ask_id or not self.isVisible():
            request.cancel()
            return
        self.request = request
        request.partial.connect(self._show_answer)
        request.finished.connect(self._show_answer)
        request.failed.connect(lambda msg: self._show_answer(f"**Couldn't ask Claude:** {msg}"))

    def _cancel(self):
        self._ask_id += 1
        if self.request is not None:
            self.request.cancel()
            self.request = None

    def _show_answer(self, text):
        fit_answer(self.answer, self.width(), text)
        self.adjustSize()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_bar()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.find(backward=True)
        else:
            super().keyPressEvent(event)


SLOW_LOAD_MS = 2500  # (a page still loading after this long gets the sweeping highlight)
SWEEP_MS = 1300
PREVIEW_IMAGE_BYTES = 3_000_000  # (a page's preview picture: anything bigger isn't one)
PREVIEW_IMAGE_HEIGHT = 170


class PreviewCard(QFrame):
    """Hover a link and tap Shift: a floating summary of the linked page (Arc's "5-second previews"), with the
    picture the page offers for link previews at the top, if it has one (as Arc's hover cards show)."""

    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.url = ""
        self.setObjectName("PreviewCard")
        self.setFixedWidth(380)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to open in a new tab")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.host = QLabel()
        self.host.setObjectName("PreviewHost")
        self.title = QLabel()
        self.title.setObjectName("PreviewTitle")
        self.summary = QLabel()
        self.summary.setObjectName("PreviewSummary")
        for label in (self.title, self.summary):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.image = QLabel()
        self.image.setObjectName("PreviewImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.hide()
        layout.addWidget(self.image)
        layout.addWidget(self.host)
        layout.addWidget(self.title)
        layout.addWidget(self.summary)
        self._network = None
        self._image_reply = None
        self._hide_timer = QTimer(self, singleShot=True, interval=400, timeout=self._maybe_hide)
        self.hide()

    def show_for(self, url):
        self.url = url
        self._hide_timer.stop()
        self.host.setText(f"✦  Preview · {display_host(url)}")
        self.set_image("")
        self.set_content("Loading…", "")
        self.show()
        self.raise_()

    def set_content(self, title=None, summary=None):
        if title is not None:
            self.title.setText(title)
        if summary is not None:
            self.summary.setText(summary)
            self.summary.setVisible(bool(summary))
        self.reposition()

    def set_image(self, src: str):
        """The page's preview picture (an http(s) address), fetched and shown at the top; "" for none."""
        if self._image_reply is not None:
            self._image_reply.abort()
            self._image_reply = None
        self.image.clear()
        self.image.hide()
        self.reposition() if self.isVisible() else None
        if not src.startswith(("https://", "http://")):
            return
        if self._network is None:
            self._network = QNetworkAccessManager(self)
        reply = self._image_reply = self._network.get(QNetworkRequest(QUrl(src)))
        url = self.url
        reply.downloadProgress.connect(lambda got, _total: got > PREVIEW_IMAGE_BYTES and reply.abort())
        reply.finished.connect(lambda: self._got_image(reply, url))

    def _got_image(self, reply, url):
        reply.deleteLater()
        if reply is self._image_reply:
            self._image_reply = None
        if reply.error() != QNetworkReply.NetworkError.NoError or url != self.url:
            return
        image = QImage.fromData(bytes(reply.readAll()))
        if image.isNull() or image.width() < 64 or image.height() < 32:
            return  # (not a picture, or a tiny icon: the card's fine without it)
        width = self.width() - self.layout().contentsMargins().left() - self.layout().contentsMargins().right()
        pixmap = QPixmap.fromImage(image).scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        if pixmap.height() > PREVIEW_IMAGE_HEIGHT:
            pixmap = pixmap.copy(0, (pixmap.height() - PREVIEW_IMAGE_HEIGHT) // 2, width, PREVIEW_IMAGE_HEIGHT)
        self.image.setPixmap(pixmap)
        self.image.show()
        self.reposition()

    def reposition(self):
        # Word-wrapped labels need heightForWidth; adjustSize() alone under-measures them
        margins = self.layout().contentsMargins()
        inner = self.width() - margins.left() - margins.right()
        labels = [l for l in (self.image, self.host, self.title, self.summary) if not l.isHidden()]
        height = (margins.top() + margins.bottom() + self.layout().spacing() * (len(labels) - 1)
                  + sum(l.heightForWidth(inner) if l.wordWrap() else l.sizeHint().height() for l in labels))
        self.setFixedHeight(height)
        self.move(12, self.parentWidget().height() - self.height() - 44)

    def keep(self):
        self._hide_timer.stop()

    def schedule_hide(self):
        if self.isVisible():
            self._hide_timer.start()

    def _maybe_hide(self):
        if not self.underMouse():
            self.dismiss()

    def dismiss(self):
        self.hide()
        self.url = ""
        self.set_image("")

    def leaveEvent(self, event):
        self.schedule_hide()

    def mousePressEvent(self, event):
        url = self.url
        self.dismiss()
        if url:
            self.browser.open_url(url)


class ContentFrame(QFrame):
    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("ContentFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

        self.stack = QStackedWidget(self)
        from .start_view import StartView
        self.empty = StartView(browser)  # (with no tab open: start_view.py)
        self.stack.addWidget(self.empty)

        self.progress = QFrame(self)
        self.progress.setObjectName("ProgressBar")
        self.progress.hide()
        # A page still loading after a while: a highlight sweeps along the bar, so it's plainly still working
        # (Zen's idea), until it's done
        self.shimmer = QFrame(self)
        self.shimmer.setObjectName("ProgressShimmer")
        self.shimmer.hide()
        self._slow = QTimer(self, singleShot=True, interval=SLOW_LOAD_MS, timeout=self._start_shimmer)
        self._sweep = None

        self.link_label = QLabel(self)
        self.link_label.setObjectName("LinkLabel")
        self.link_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.link_label.hide()

        self.toast = QFrame(self)
        self.toast.setObjectName("Toast")
        toast_layout = QHBoxLayout(self.toast)
        toast_layout.setContentsMargins(14, 6, 8, 6)
        toast_layout.setSpacing(12)
        self.toast_text = QLabel()
        self.toast_text.setObjectName("ToastText")
        self.toast_text.setTextFormat(Qt.TextFormat.PlainText)  # (it often says a page's title: never as HTML)
        self.toast_button = QPushButton()  # an action to go with it, like Undo
        self.toast_button.setObjectName("ToastAction")
        self.toast_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toast_button.clicked.connect(self._toast_action)
        toast_layout.addWidget(self.toast_text)
        toast_layout.addWidget(self.toast_button)
        self._toast_do = None
        self.toast.hide()
        self._toast_timer = QTimer(self, singleShot=True, interval=1800, timeout=self._hide_toast)

        self.find_bar = FindBar(browser, self)
        self.preview_card = PreviewCard(browser, self)
        self.split_drop = SplitDropZone(browser, self)
        self.corners = [Corner(browser, w, self) for w in ("tl", "tr", "bl", "br")]
        self._rounded = True
        self.drawer = None  # Developer Mode's console, under the page when open

        from .split import SplitView  # split.py builds on this module's Corner
        self.split_view = SplitView(browser)
        self.stack.addWidget(self.split_view)
        self._update_chrome()  # (it starts empty: no card)

    @property
    def splitting(self) -> bool:
        return self.stack.currentWidget() is self.split_view

    def set_rounded(self, rounded):
        # Fullscreen video is edge-to-edge
        self._rounded = rounded
        self._update_chrome()

    @property
    def blank(self) -> bool:
        """Nothing open: like Arc, just the Space's background, no page card."""
        return self.stack.currentWidget() is self.empty

    def _update_chrome(self):
        # In Split View each pane is its own rounded card, so the frame itself disappears; with nothing
        # open it's a faint, rounded shade of the Space's colours
        for c in self.corners:
            c.setVisible(self._rounded and not self.splitting)
        if (self.property("split") is True) != self.splitting or (self.property("blank") is True) != self.blank:
            self.setProperty("split", self.splitting)
            self.setProperty("blank", self.blank)
            self.style().unpolish(self)
            self.style().polish(self)

    def show_view(self, view):
        if self.splitting:
            self.split_view.release(self.stack)
        if view is None:
            self.empty.refresh()
            self.stack.setCurrentWidget(self.empty)
        else:
            if self.stack.indexOf(view) < 0:
                self.stack.addWidget(view)
            self.stack.setCurrentWidget(view)
        self._after_switch()

    def show_split(self, tabs, focused):
        self.split_view.show_tabs(tabs, focused, self.stack)
        self.stack.setCurrentWidget(self.split_view)
        self._after_switch()

    def _after_switch(self):
        self.set_progress(100)
        self.link_label.hide()
        self.preview_card.dismiss()
        self._update_chrome()
        self._raise_overlays()

    def remove_view(self, view):
        self.split_view.forget(view)
        self.stack.removeWidget(view)

    def set_progress(self, value):
        if value >= 100:
            self.progress.hide()
            self._stop_shimmer()
            return
        if self.progress.isHidden():  # (a load just started)
            self._slow.start()
        self.progress.setGeometry(0, 0, max(int(self.width() * value / 100), 8), 2)
        self.progress.show()
        self.progress.raise_()
        if self.shimmer.isVisible():
            self.shimmer.raise_()

    def _start_shimmer(self):
        if self.progress.isHidden() or motion.reduced():
            return
        width = max(self.width() // 4, 60)
        self.shimmer.setGeometry(-width, 0, width, 2)
        self.shimmer.show()
        self.shimmer.raise_()
        self._sweep = QVariantAnimation(self, startValue=-width, endValue=self.width(), duration=SWEEP_MS,
                                        loopCount=-1)
        self._sweep.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._sweep.valueChanged.connect(lambda x: self.shimmer.move(int(x), 0))
        self._sweep.start()

    def _stop_shimmer(self):
        self._slow.stop()
        if self._sweep is not None:
            self._sweep.stop()
            self._sweep.deleteLater()
            self._sweep = None
        self.shimmer.hide()

    def show_link(self, url):
        if not url:
            self.link_label.hide()
            return
        text = self.link_label.fontMetrics().elidedText(url, Qt.TextElideMode.ElideMiddle, self.width() // 2)
        self.link_label.setText(text)
        self.link_label.adjustSize()
        self.link_label.move(8, self.height() - self.link_label.height() - 8)
        self.link_label.show()
        self.link_label.raise_()

    def show_toast(self, text, action=None, label="Undo"):
        """A short message at the bottom of the page. With `action`, it has a button (Undo, say) and
        stays a little longer to give you time for it."""
        self.toast_text.setText(shortcuts.for_here(text))  # (Windows' keys and names, off a Mac)
        self._toast_do = action
        self.toast_button.setText(label)
        self.toast_button.setVisible(action is not None)
        # (clicks go through a plain message, to whatever's under it)
        self.toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, action is None)
        self._toast_timer.setInterval(5000 if action is not None else 1800)
        self.toast.layout().setContentsMargins(14, 6, 8 if action is not None else 14, 6)
        self.toast.adjustSize()
        self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 20)
        self.toast.raise_()
        motion.fade(self.toast, True)
        self._toast_timer.start()

    def _toast_action(self):
        action, self._toast_do = self._toast_do, None
        self._hide_toast()
        if action is not None:
            action()

    def _hide_toast(self):
        self._toast_do = None
        self._toast_timer.stop()
        motion.fade(self.toast, False, motion.NORMAL)

    def set_drawer(self, drawer):
        if drawer is self.drawer:
            return
        if self.drawer is not None:
            self.drawer.hide()
        self.drawer = drawer
        if drawer is not None:
            drawer.show()
            drawer.raise_()
        self.resizeEvent(None)

    def resizeEvent(self, event):
        drawer_h = self.drawer.height() if self.drawer is not None else 0
        self.stack.setGeometry(0, 0, self.width(), self.height() - drawer_h)
        if self.drawer is not None:
            self.drawer.setGeometry(0, self.height() - drawer_h, self.width(), drawer_h)
        w, h = self.width(), self.height()
        self.find_bar.move(w - self.find_bar.width() - 12, self.find_bar.y() if self.find_bar.isVisible() else FindBar.TOP)
        if self.preview_card.isVisible():
            self.preview_card.reposition()
        positions = {"tl": (0, 0), "tr": (w - RADIUS, 0), "bl": (0, h - RADIUS), "br": (w - RADIUS, h - RADIUS)}
        for c in self.corners:
            c.move(*positions[c.which])
        self._raise_overlays()

    def _raise_overlays(self):
        for widget in (self.progress, self.link_label, self.find_bar, self.preview_card, self.toast, *self.corners):
            widget.raise_()


class SplitDropZone(QWidget):
    """While a tab is being dragged from the sidebar, the page area accepts it: drop to split."""

    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.setAcceptDrops(True)
        self._hot = False
        self.hide()

    def mousePressEvent(self, event):
        # A click can't come while a tab's being dragged (the button's held for the drag), so one here means the
        # zone was left up: out of the way at once, never between you and the page
        self.activate(False)
        self.browser.focus_current()
        event.ignore()

    def activate(self, on):
        if on and self.browser.current_tab is not None:
            self.setGeometry(self.parentWidget().rect())
            self.show()
            self.raise_()
        else:
            self._hot = False
            self.hide()

    def _tab(self, event):
        from .sidebar import TAB_MIME
        if not event.mimeData().hasFormat(TAB_MIME):
            return None
        tab = self.browser.store.find_tab(bytes(event.mimeData().data(TAB_MIME)).decode())
        return tab if tab is not None and tab is not self.browser.current_tab else None

    def dragEnterEvent(self, event):
        if self._tab(event):
            self._hot = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hot = False
        self.update()

    def dropEvent(self, event):
        from .sidebar import dragged_tabs
        from .split import MAX_PANES  # (split.py imports this module)
        tab = self._tab(event)
        self.activate(False)
        if tab is None:
            return
        event.acceptProposedAction()
        current = self.browser.current_tab
        together = [t for t in dragged_tabs(self.browser, event) if t is not current]
        if together and current is not None:  # several picked tabs: beside the one you're on
            QTimer.singleShot(0, lambda: self.browser.split_tabs([current, *together][:MAX_PANES]))
        else:
            QTimer.singleShot(0, lambda: self.browser.add_to_split(tab))

    def paintEvent(self, event):
        if not self._hot:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(self.browser.theme.accent)
        accent.setAlphaF(0.22)
        half = QRectF(self.width() / 2 + 4, 8, self.width() / 2 - 12, self.height() - 16)
        path = QPainterPath()
        path.addRoundedRect(half, RADIUS, RADIUS)
        p.fillPath(path, accent)
        p.setPen(self.browser.theme.panel_text)
        p.drawText(half, Qt.AlignmentFlag.AlignCenter, "Drop to open in Split View")
