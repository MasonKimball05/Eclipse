# The sidebar's resize edge where it looks like it is: on the page's edge. The splitter between them
# only takes hold in the gap beside the page, so the edge you can see (the page's rounded side) wasn't
# grabbable and the spot that was sat just to one side of it. This strip straddles that edge, over the
# gap and a few points onto the page, as in Arc: drag it to resize, double-click to reset. (Only with the
# sidebar on the left: on the right, the page's edge is its scrollbar, which keeps every point it has.)

from PyQt6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

ONTO_PAGE = 5  # points of the page's own edge that also take hold
MIN_WIDTH, MAX_WIDTH = 190, 420


class SidebarEdge(QWidget):
    def __init__(self, window):
        super().__init__(window.centralWidget())
        self.window_ = window
        self.setObjectName("SidebarEdge")
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setToolTip("Drag to resize the sidebar · double-click to reset")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._drag = None  # (where the pointer started, the sidebar's width then) while dragging
        self.hide()
        window.content.installEventFilter(self)  # (it follows the page's edge wherever that goes)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide):
            QTimer.singleShot(0, self.place)  # (once the layout's settled)
        return False

    def place(self):
        w = self.window_
        sidebar, page = w.sidebar, w.content
        docked = (not sidebar.isHidden() and sidebar.property("floating") is not True
                  and not getattr(w, "_fullscreen", False) and sidebar.parentWidget() is w.splitter)
        if not docked or not w.isVisible():
            self.hide()
            return
        root = w.centralWidget()
        handle = w.splitter.handle(1)
        gap = handle.mapTo(root, QPoint(0, 0))
        top = page.mapTo(root, QPoint(0, 0)).y()
        if w.sidebar_on_right:
            left, right = gap.x(), gap.x() + handle.width()
        else:
            left, right = gap.x(), gap.x() + handle.width() + ONTO_PAGE
        self.setGeometry(left, top, right - left, page.height())
        self.show()
        self.raise_()
        for overlay in (w.peek, w.command_bar, w.tab_switcher):  # (never over something that's opened over it all)
            if overlay.isVisible():
                overlay.raise_()

    # --- Dragging ----------------------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = (event.globalPosition().x(), self.window_.sidebar.width())
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        start, width = self._drag
        moved = event.globalPosition().x() - start
        wanted = width - moved if self.window_.sidebar_on_right else width + moved
        self.window_._set_sidebar_size(round(min(max(wanted, MIN_WIDTH), MAX_WIDTH)))
        self.place()
        if self.window_.sidebar_on_right:
            self.window_.place_traffic_lights()  # (they ride on the sidebar's left edge, which is moving)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.window_._on_splitter_moved()  # (remembered, and everything that follows the edge moves)
            self.update()

    def mouseDoubleClickEvent(self, event):
        self.window_.reset_sidebar_width()
        self.place()

    # --- Looks -------------------------------------------------------------------------------------

    def event(self, event):
        if event.type() in (event.Type.HoverEnter, event.Type.HoverLeave):
            self.update()
        return super().event(event)

    def paintEvent(self, event):
        """While pointed at or dragged, a slim bar down the middle of the gap (as the splitter's own did)."""
        if self._drag is None and not self.underMouse():
            return
        handle = self.window_.splitter.handle(1)
        middle = handle.mapTo(self, QPoint(handle.width() // 2, 0)).x()
        color = QColor(self.window_.theme.text)
        color.setAlphaF(0.10 if self._drag is None else 0.16)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(middle - 2, 12, 4, max(self.height() - 24, 0)), 2, 2)
        p.end()
