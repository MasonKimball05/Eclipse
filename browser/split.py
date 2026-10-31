# Arc's Split View: up to four tabs side by side, each in its own rounded pane.
#
# The split itself is data (Store.splits, a list of tab-id groups) so it survives
# restarts; this module is only the widget that lays the panes out. Clicking into a
# pane makes that tab the current one; the sidebar highlights every tab in the split.

import qtawesome as qta
from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QFrame, QSplitter, QToolButton, QVBoxLayout

from . import motion
from .content import RADIUS, Corner

MAX_PANES = 4


class Pane(QFrame):
    def __init__(self, browser, tab):
        super().__init__()
        self.browser = browser
        self.tab = tab
        self.view = tab.view
        self.setObjectName("Pane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.view.show()

        self.corners = [Corner(browser, w, self) for w in ("tl", "tr", "bl", "br")]
        self.focus_ring = QFrame(self)
        self.focus_ring.setObjectName("PaneFocus")
        self.focus_ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.close_btn = QToolButton(self)
        self.close_btn.setObjectName("PaneClose")
        self.close_btn.setIcon(qta.icon("ph.x", color=browser.theme.panel_text))
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setToolTip("Remove from Split View")
        self.close_btn.clicked.connect(lambda: browser.remove_from_split(tab))
        self.close_btn.hide()

    def set_focused(self, focused):
        self.focus_ring.setVisible(focused)

    def take_view(self):
        view, self.view = self.view, None
        if view is not None and not sip.isdeleted(view):
            self.layout().removeWidget(view)
            return view
        return None

    def enterEvent(self, event):
        self.close_btn.raise_()
        motion.fade(self.close_btn, True)  # (fading, as the other hover controls do)

    def leaveEvent(self, event):
        motion.fade(self.close_btn, False)

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        spots = {"tl": (0, 0), "tr": (w - RADIUS, 0), "bl": (0, h - RADIUS), "br": (w - RADIUS, h - RADIUS)}
        for corner in self.corners:
            corner.move(*spots[corner.which])
            corner.raise_()
        self.focus_ring.setGeometry(self.rect())
        self.focus_ring.raise_()
        self.close_btn.move(w - self.close_btn.width() - 8, 8)


class SplitView(QSplitter):
    def __init__(self, browser):
        super().__init__(Qt.Orientation.Horizontal)
        self.browser = browser
        self.setObjectName("SplitView")
        self.setHandleWidth(8)
        self.setChildrenCollapsible(False)
        self.panes: list[Pane] = []

    def show_tabs(self, tabs, focused, stack):
        # The same tabs, each page still in its pane (not taken by the mini player or the page stack)
        same = [p.tab for p in self.panes] == tabs and all(
            p.view is t.view and p.view is not None and p.view.parentWidget() is p for p, t in zip(self.panes, tabs))
        if not same:
            self.release(stack)
            self.panes = [Pane(self.browser, t) for t in tabs]
            for pane in self.panes:
                self.addWidget(pane)
            self.setSizes([10_000] * len(self.panes))  # equal widths
        for pane in self.panes:
            pane.set_focused(pane.tab is focused)

    def release(self, stack):
        """Hand every pane's page back to the normal page stack and drop the panes."""
        for pane in self.panes:
            view = pane.take_view()
            if view is not None:
                stack.addWidget(view)
            pane.setParent(None)
            pane.deleteLater()
        self.panes = []

    def forget(self, view):
        # The view is being destroyed (tab closed or unloaded)
        for pane in self.panes:
            if pane.view is view:
                pane.take_view()

    def pane_containing(self, widget) -> Pane | None:
        while widget is not None:
            if isinstance(widget, Pane) and widget in self.panes:
                return widget
            widget = widget.parentWidget()
        return None
