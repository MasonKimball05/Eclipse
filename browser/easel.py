# Arc's Easels: a whiteboard tab for collecting and arranging things.
#
# An easel is a Tab whose URL is "easel:<id>". It holds text, sticky notes, freehand drawing,
# rectangles, ellipses, arrows and images, which you can paste or drop in, or capture from any web
# page with ⇧⌘2 (Capture: drag a box over the page). A captured image remembers where it came from;
# double-click it to open that page again.
#
# Saved in the data folder as easels/<id>/easel.json, with images alongside as PNGs. Every change is
# saved shortly after it's made, and ⌘Z / ⇧⌘Z step through them.

import json
import math
import re
from pathlib import Path

import qtawesome as qta
from PyQt6.QtCore import QLineF, QMarginsF, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QKeySequence, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen,
                         QPixmap, QShortcut, QTextCharFormat, QTextCursor)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
                             QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
                             QHBoxLayout, QLineEdit, QMenu, QToolButton, QVBoxLayout, QWidget)

from . import storage
from .features import Feature
from .models import new_id
from .shortcuts import for_here

EASEL_SCHEME = "easel:"
COLORS = ["#1f2937", "#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#a855f7"]
STICKY_COLORS = {"#1f2937": "#fde68a", "#ef4444": "#fecaca", "#f59e0b": "#fde68a", "#22c55e": "#bbf7d0",
                 "#3b82f6": "#bfdbfe", "#a855f7": "#e9d5ff"}
TOOLS = [("select", "ph.cursor", "Select & move (V)", "V"), ("text", "ph.text-t", "Text (T)", "T"),
         ("sticky", "ph.note", "Sticky note (S)", "S"), ("pen", "ph.pencil-simple", "Draw (P)", "P"),
         ("rect", "ph.square", "Rectangle (R)", "R"), ("ellipse", "ph.circle", "Ellipse (O)", "O"),
         ("arrow", "ph.arrow-up-right", "Arrow (A)", "A")]
UNDO_LIMIT = 60
PAPER = "#fafaf9"                # an exported easel's background (the canvas's, without the dots)
EXPORT_MARGIN = 40
EXPORT_MAX_PIXELS = 60_000_000   # a picture of a huge easel is made smaller to stay under about 240 MB
PDF_MAX_SIDE = 14_400            # points: Acrobat's largest page (a bigger easel is scaled down to fit)
KIND = 0     # QGraphicsItem.data() keys
SOURCE = 1   # for images: the page they were captured from
IMAGE = 2    # for images: their file name


_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # (what new_id() makes: never a path, so "easel:../x" isn't one)


def is_easel(url: str) -> bool:
    return url.startswith(EASEL_SCHEME) and bool(_ID.fullmatch(url[len(EASEL_SCHEME):]))


def new_easel_url() -> str:
    return EASEL_SCHEME + new_id()


def easel_folder(url: str) -> Path:
    if not is_easel(url):  # (a page can make a tab of any address: only real easel ids name a folder)
        raise ValueError(f"not an easel: {url[:80]}")
    folder = storage.data_dir() / "easels" / url[len(EASEL_SCHEME):]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def arrow_path(start: QPointF, end: QPointF) -> QPainterPath:
    path = QPainterPath(start)
    path.lineTo(end)
    line = QLineF(end, start)
    if line.length() > 1:
        for angle in (25, -25):
            head = QLineF(end, start)
            head.setLength(min(16.0, line.length() / 2))
            head.setAngle(line.angle() + angle)
            path.moveTo(end)
            path.lineTo(head.p2())
    return path


def _dot_grid() -> QPixmap:
    tile = QPixmap(24, 24)
    tile.fill(QColor(PAPER))
    p = QPainter(tile)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#d6d3d1"))
    p.drawEllipse(QPointF(12, 12), 1.1, 1.1)
    p.end()
    return tile


def _movable(item, kind):
    item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
    item.setData(KIND, kind)
    return item


class StickyNote(QGraphicsRectItem):
    def __init__(self, rect: QRectF, color: str, text=""):
        super().__init__(rect)
        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.text = QGraphicsTextItem(text, self)
        self.text.setDefaultTextColor(QColor("#1f2937"))
        self.text.setFont(QFont("Helvetica Neue", 14))
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self._fit_text()

    def setRect(self, rect):
        super().setRect(rect)
        self._fit_text()

    def _fit_text(self):
        rect = self.rect()
        self.text.setTextWidth(max(rect.width() - 20, 10))
        self.text.setPos(rect.topLeft() + QPointF(10, 10))


# --- Resizing and turning ------------------------------------------------------------------------------
#
# One thing selected gets a handle at each corner: drag one and the opposite corner stays put. Pictures
# and text keep their shape (text by changing its size); boxes, ellipses, notes, drawings and arrows
# stretch. A knob above it turns it about its middle (⇧ in steps of 15°, and it settles on square when
# close). The change is baked into the item itself (a new rect, path, scale, text size or rotation), so
# it saves and undoes like any other edit.

RESIZABLE = ("image", "rect", "ellipse", "sticky", "pen", "arrow", "text")
HANDLE = 8       # points on screen, whatever the zoom
KNOB = 22        # points on screen from an item's top edge to its turning knob
ROTATE = 4       # what handle_at() says for the knob (corners are 0–3)
MIN_SIZE = 12.0  # in the easel
TEXT_SIZES = (6.0, 400.0)
SNAP = 3         # degrees: this close to square, a turn settles on it


def local_box(item) -> QRectF:
    """An item's box in its own coordinates, without the width of its outline."""
    kind = item.data(KIND)
    if kind in ("rect", "ellipse", "sticky"):
        return item.rect()
    if kind in ("pen", "arrow"):
        return item.path().boundingRect()
    if kind == "image":  # (its boundingRect has an extra pixel round it for smooth scaling)
        return QRectF(item.pixmap().rect())
    return item.boundingRect()


def geometry(item) -> QRectF:
    """An item's box in the easel as it would be if it weren't turned (about its middle)."""
    box = local_box(item)
    middle = item.mapToScene(box.center())
    width, height = box.width() * item.scale(), box.height() * item.scale()
    return QRectF(middle.x() - width / 2, middle.y() - height / 2, width, height)


def outline(item) -> list[QPointF]:
    """The corners of an item's box in the easel, turned with it (clockwise from its own top left)."""
    return [item.mapToScene(corner) for corner in corners(local_box(item))]


def rotated(vector: QPointF, degrees: float) -> QPointF:
    """`vector` turned clockwise on screen, as QGraphicsItem.setRotation turns things."""
    a = math.radians(degrees)
    return QPointF(vector.x() * math.cos(a) - vector.y() * math.sin(a), vector.x() * math.sin(a) + vector.y() * math.cos(a))


def turn(item, degrees: float):
    """Turn an item to `degrees`, about its middle (which stays where it is)."""
    middle = item.mapToScene(local_box(item).center())
    item.setRotation(degrees % 360)
    item.setPos(item.pos() + middle - item.mapToScene(local_box(item).center()))


def text_size(item) -> float:
    cursor = QTextCursor(item.document())
    if not item.document().isEmpty():
        cursor.setPosition(1)  # (the first letter's format: saved text carries its size with it)
    return cursor.charFormat().fontPointSize() or item.font().pointSizeF()


def set_text_size(item, size: float):
    size = min(max(size, TEXT_SIZES[0]), TEXT_SIZES[1])
    font = item.font()
    font.setPointSizeF(size)
    item.setFont(font)  # (what's typed next)
    fmt = QTextCharFormat()
    fmt.setFontPointSize(size)
    cursor = QTextCursor(item.document())
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.mergeCharFormat(fmt)
    cursor.mergeBlockCharFormat(fmt)


def corners(rect: QRectF) -> list[QPointF]:
    return [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]


def resized(start: QRectF, corner: int, to: QPointF, keep_shape=False) -> QRectF:
    """`start` with corner number `corner` (clockwise from the top left) dragged to `to`."""
    anchor = corners(start)[(corner + 2) % 4]
    width = max(abs(to.x() - anchor.x()), MIN_SIZE)
    height = max(abs(to.y() - anchor.y()), MIN_SIZE)
    if keep_shape and start.width() > 0 and start.height() > 0:
        factor = max(width / start.width(), height / start.height())
        width, height = start.width() * factor, start.height() * factor
    left = anchor.x() - width if corner in (0, 3) else anchor.x()
    top = anchor.y() - height if corner in (0, 1) else anchor.y()
    return QRectF(left, top, width, height)


def apply_geometry(item, start_geometry: QRectF, start_shape, rect: QRectF):
    """Make `item` (not turned) fill `rect` in the easel; `start_shape` is its path, scale or text size when
    the drag began."""
    kind = item.data(KIND)
    if kind == "text":
        set_text_size(item, start_shape * rect.height() / max(start_geometry.height(), 1))
        box = local_box(item)
        # (text doesn't grow exactly in proportion: it's placed by the corner that's held still)
        right = abs(rect.left() - start_geometry.left()) > abs(rect.right() - start_geometry.right())
        bottom = abs(rect.top() - start_geometry.top()) > abs(rect.bottom() - start_geometry.bottom())
        item.setPos(QPointF(rect.right() - box.width() if right else rect.left(),
                            rect.bottom() - box.height() if bottom else rect.top()) - box.topLeft())
    elif kind == "image":
        item.setScale(rect.width() / item.pixmap().width())
        item.setPos(rect.topLeft())
    elif kind in ("rect", "ellipse", "sticky"):
        item.setPos(rect.topLeft())
        item.setRect(QRectF(0, 0, rect.width(), rect.height()))
    elif kind in ("pen", "arrow"):
        old = start_geometry
        sx = rect.width() / old.width() if old.width() else 1
        sy = rect.height() / old.height() if old.height() else 1

        def moved(x, y):  # a point of the path as it was, to where it goes now (in the item)
            scene = item.mapToScene(QPointF(x, y))
            # (a flat line has no height to stretch: it stays at its own height, and likewise sideways)
            new_x = rect.left() + (scene.x() - old.left()) * sx if old.width() else scene.x()
            new_y = rect.top() + (scene.y() - old.top()) * sy if old.height() else scene.y()
            return item.mapFromScene(QPointF(new_x, new_y))
        if kind == "arrow":  # just the line: the head is drawn again, the right size
            ends = [start_shape.elementAt(0), start_shape.elementAt(1)]
            item.setPath(arrow_path(moved(ends[0].x, ends[0].y), moved(ends[1].x, ends[1].y)))
        else:
            path = QPainterPath()
            for i in range(start_shape.elementCount()):
                e = start_shape.elementAt(i)
                (path.moveTo if e.type == QPainterPath.ElementType.MoveToElement else path.lineTo)(moved(e.x, e.y))
            item.setPath(path)


class ResizeHandles(QGraphicsItem):
    """The four corner handles and the turning knob of the one selected item (not part of the easel:
    never saved)."""

    def __init__(self):
        super().__init__()
        self.target = None
        self.setZValue(1e9)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def follow(self, item):
        self.prepareGeometryChange()
        self.target = item
        self.update()

    def _unit(self) -> float:
        """One point on screen, in the easel."""
        view = self.scene().views()[0] if self.scene() and self.scene().views() else None
        return 1 / (view.transform().m11() if view else 1)

    def _size(self) -> float:
        return HANDLE * self._unit()

    def _corners(self) -> list[QPointF]:
        return outline(self.target) if self.target is not None and self.target.scene() is self.scene() else []

    def _knob(self, points) -> tuple[QPointF, QPointF]:
        """Where the knob's stalk starts (the middle of the top edge) and the knob itself, turned with the item."""
        top, bottom = (points[0] + points[1]) / 2, (points[3] + points[2]) / 2
        up = top - bottom
        length = math.hypot(up.x(), up.y())
        up = up / length if length > 0.01 else rotated(QPointF(0, -1), self.target.rotation())
        return top, top + up * KNOB * self._unit()

    def handle_at(self, pos: QPointF) -> int | None:
        points = self._corners()
        if not points:
            return None
        reach = self._size()
        for i, corner in enumerate(points):
            if abs(pos.x() - corner.x()) <= reach and abs(pos.y() - corner.y()) <= reach:
                return i
        knob = self._knob(points)[1]
        if math.hypot(pos.x() - knob.x(), pos.y() - knob.y()) <= reach:
            return ROTATE
        return None

    def boundingRect(self):
        points = self._corners()
        if not points:
            return QRectF()
        points.append(self._knob(points)[1])
        xs, ys = [p.x() for p in points], [p.y() for p in points]
        pad = self._size()
        return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad)

    def shape(self):
        path = QPainterPath()
        points, size = self._corners(), self._size()
        if points:
            for corner in points:
                path.addRect(QRectF(corner.x() - size / 2, corner.y() - size / 2, size, size))
            path.addEllipse(self._knob(points)[1], size / 2, size / 2)
        return path

    def paint(self, painter, option, widget=None):
        points = self._corners()
        if not points:
            return
        size = self._size()
        painter.setPen(QPen(QColor("#7c5cff"), 1.5 * size / HANDLE))
        stalk, knob = self._knob(points)
        painter.drawLine(stalk, knob)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(knob, size / 2, size / 2)
        for corner in points:
            painter.drawRect(QRectF(corner.x() - size / 2, corner.y() - size / 2, size, size))


class EaselScene(QGraphicsScene):
    def __init__(self, easel):
        super().__init__(easel)
        self.easel = easel
        self._start: QPointF | None = None
        self._drawing = None
        self._resizing = None  # (item, corner, its box, its path/scale/size, its turn, the corner held, its pos) while dragged
        self._turning = None   # (item, its middle, where the pointer started on the dial) while the knob is dragged
        self.handles = None
        self.add_handles()
        self.selectionChanged.connect(self.update_handles)

    def add_handles(self):
        """(Again after clear(), which takes everything away.)"""
        self.handles = ResizeHandles()
        self.addItem(self.handles)

    def update_handles(self):
        if self.handles is None:
            return
        selected = self.selectedItems()
        self.handles.follow(selected[0] if len(selected) == 1 and selected[0].data(KIND) in RESIZABLE else None)

    def mousePressEvent(self, event):
        tool = self.easel.tool
        pos = event.scenePos()
        if tool == "select" and event.button() == Qt.MouseButton.LeftButton:
            corner = self.handles.handle_at(pos)
            if corner == ROTATE:
                item = self.handles.target
                middle = item.mapToScene(local_box(item).center())
                self._turning = (item, middle, _bearing(pos - middle) - item.rotation())
                event.accept()
                return
            if corner is not None:
                item = self.handles.target
                kind = item.data(KIND)
                shape = (item.path() if kind in ("pen", "arrow") else text_size(item) if kind == "text"
                         else item.scale())
                degrees, held = item.rotation(), outline(item)[(corner + 2) % 4]
                turn(item, 0)  # (resizing works on it square, then turns it back)
                unturned = item.pos()
                turn(item, degrees)
                self._resizing = (item, corner, geometry(item), shape, degrees, held, unturned)
                event.accept()
                return
        if tool == "select" or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        color = self.easel.color
        if tool == "text":
            item = _movable(QGraphicsTextItem(""), "text")
            item.setFont(QFont("Helvetica Neue", 18))
            item.setDefaultTextColor(QColor(color))
            item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            item.setPos(pos)
            self.addItem(item)
            item.setFocus()
            self.easel.set_tool("select")
            return
        if tool == "sticky":
            item = _movable(StickyNote(QRectF(0, 0, 200, 160), STICKY_COLORS[color]), "sticky")
            item.setPos(pos - QPointF(100, 80))
            self.addItem(item)
            item.text.setFocus()
            self.easel.set_tool("select")
            return
        self._start = pos
        pen = QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if tool == "pen":
            path = QPainterPath(pos)
            self._drawing = _movable(QGraphicsPathItem(path), "pen")
        elif tool == "rect":
            self._drawing = _movable(QGraphicsRectItem(QRectF(pos, pos)), "rect")
        elif tool == "ellipse":
            self._drawing = _movable(QGraphicsEllipseItem(QRectF(pos, pos)), "ellipse")
        elif tool == "arrow":
            self._drawing = _movable(QGraphicsPathItem(arrow_path(pos, pos)), "arrow")
        self._drawing.setPen(pen)
        self.addItem(self._drawing)

    def finish_drag(self):
        """A resize or turn is over: one undo step for it. (Also when the button went up somewhere this
        never heard about, the easel having been switched away from mid-drag.)"""
        if self._resizing is not None or self._turning is not None:
            self._resizing = self._turning = None
            self.easel.save()

    def mouseMoveEvent(self, event):
        if (self._resizing is not None or self._turning is not None) and \
                not event.buttons() & Qt.MouseButton.LeftButton:
            self.finish_drag()  # (it must have been let go elsewhere: the item stays where it was put)
            return
        if self._turning is not None:
            item, middle, offset = self._turning
            degrees = _bearing(event.scenePos() - middle) - offset
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                degrees = round(degrees / 15) * 15
            elif abs(degrees - round(degrees / 90) * 90) < SNAP:
                degrees = round(degrees / 90) * 90
            turn(item, degrees)
            self.handles.follow(item)
            return
        if self._resizing is not None:
            item, corner, start, shape, degrees, held, unturned = self._resizing
            keep = item.data(KIND) in ("image", "text") or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            # The pointer, seen square on to the item, from the corner that's held still
            anchor = corners(start)[(corner + 2) % 4]
            box = resized(start, corner, anchor + rotated(event.scenePos() - held, -degrees), keep_shape=keep)
            if degrees:  # turned about its new middle, the held corner has to land back where it was
                box.translate(held - (box.center() + rotated(anchor - box.center(), degrees)))
            item.setRotation(0)
            item.setPos(unturned)
            apply_geometry(item, start, shape, box)
            turn(item, degrees)
            self.handles.follow(item)
            return
        if self._drawing is None:
            if self.easel.tool == "select" and not event.buttons():  # the pointer shows a handle can be dragged
                corner = self.handles.handle_at(event.scenePos())
                self.easel.canvas.viewport().setCursor(
                    Qt.CursorShape.ArrowCursor if corner is None else
                    Qt.CursorShape.PointingHandCursor if corner == ROTATE else
                    Qt.CursorShape.SizeFDiagCursor if corner in (0, 2) else Qt.CursorShape.SizeBDiagCursor)
            super().mouseMoveEvent(event)
            if self.handles.target is not None and event.buttons():  # (it's being moved)
                self.handles.follow(self.handles.target)
            return
        pos, kind = event.scenePos(), self._drawing.data(KIND)
        if kind == "pen":
            path = self._drawing.path()
            path.lineTo(pos)
            self._drawing.setPath(path)
        elif kind in ("rect", "ellipse"):
            self._drawing.setRect(QRectF(self._start, pos).normalized())
        elif kind == "arrow":
            self._drawing.setPath(arrow_path(self._start, pos))

    def mouseReleaseEvent(self, event):
        if self._resizing is not None or self._turning is not None:
            self.finish_drag()  # one undo step for the whole drag
            return
        if self._drawing is None:
            return super().mouseReleaseEvent(event)
        self._drawing, self._start = None, None

    def contextMenuEvent(self, event):
        item = self.itemAt(event.scenePos(), self.views()[0].transform()) if self.views() else None
        if item is not None and item is not self.handles and not self.easel._editing_text():
            self.easel.item_menu(item, event.screenPos())
            return
        super().contextMenuEvent(event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        if self.handles is not None and self.handles.target is not None:  # (typing changes a text's size)
            self.handles.follow(self.handles.target)

    def mouseDoubleClickEvent(self, event):
        if self.easel.tool == "select" and self.handles.handle_at(event.scenePos()) is not None:
            return self.mousePressEvent(event)  # (a quick second grab of a handle still resizes)
        item = self.itemAt(event.scenePos(), self.views()[0].transform()) if self.views() else None
        if item is not None and item.data(SOURCE):  # a capture: back to the page it came from
            self.easel.open_link.emit(item.data(SOURCE))
            return
        super().mouseDoubleClickEvent(event)


def _bearing(vector: QPointF) -> float:
    """The direction of `vector` in degrees, clockwise on screen from pointing right."""
    return math.degrees(math.atan2(vector.y(), vector.x()))


class EaselView(QWidget):
    """The whole easel tab: a floating toolbar over an endless canvas."""
    title_changed = pyqtSignal(str)
    open_link = pyqtSignal(str)
    export = pyqtSignal(str)  # "copy", "png" or "pdf": the window does it (it knows where files go)

    def __init__(self, url):
        super().__init__()
        self.setObjectName("Easel")
        self.url = url
        self.folder = easel_folder(url)
        self.tool = "select"
        self.color = COLORS[0]
        self._history: list[str] = []
        self._future: list[str] = []
        self._loading = False

        self.scene = EaselScene(self)
        self.scene.setSceneRect(-5000, -5000, 10000, 10000)
        self.canvas = QGraphicsView(self.scene)
        self.canvas.setObjectName("EaselCanvas")
        self.canvas.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.canvas.setFrameShape(QFrame.Shape.NoFrame)
        # (an endless canvas: no scrollbars, you move about by scrolling or with the trackpad, as on Arc's)
        self.canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.canvas.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.canvas.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.canvas.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.canvas.setAcceptDrops(True)
        self.canvas.viewport().installEventFilter(self)
        self.canvas.setBackgroundBrush(QBrush(_dot_grid()))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self._build_toolbar()

        self._save_timer = QTimer(self, singleShot=True, interval=700, timeout=self.save)
        self.scene.changed.connect(lambda _rects: self._loading or self._save_timer.start())
        for keys, slot in (("Ctrl+Z", self.undo), ("Ctrl+Shift+Z", self.redo), ("Ctrl+V", self.paste),
                           ("Backspace", self.delete_selected), ("Delete", self.delete_selected),
                           ("Ctrl+=", lambda: self.zoom(1.2)), ("Ctrl+-", lambda: self.zoom(1 / 1.2)),
                           ("Ctrl+0", self.fit), ("Ctrl+B", lambda: self.style_text("bold")),
                           ("Ctrl+I", lambda: self.style_text("italic")),
                           ("Ctrl+U", lambda: self.style_text("underline")),
                           ("Ctrl+}", self.bring_to_front), ("Ctrl+{", self.send_to_back)):  # (⇧⌘] / ⇧⌘[)
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        for name, _icon, _tip, key in TOOLS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda n=name: self._editing_text() or self.set_tool(n))
        self.load()

    # --- Toolbar ----------------------------------------------------------------------------------

    def _build_toolbar(self):
        bar = self.toolbar = QFrame(self)
        bar.setObjectName("EaselToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(4)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("EaselTitle")
        self.title_edit.setPlaceholderText("Untitled Easel")
        self.title_edit.setFixedWidth(180)
        self.title_edit.textEdited.connect(lambda _t: (self._save_timer.start(), self.title_changed.emit(self.title)))
        row.addWidget(self.title_edit)
        self.tool_buttons = QButtonGroup(self)
        for name, icon, tip, _key in TOOLS:
            button = self._tool_button(icon, tip)
            button.setCheckable(True)
            button.setChecked(name == self.tool)
            button.clicked.connect(lambda _=False, n=name: self.set_tool(n))
            self.tool_buttons.addButton(button)
            button.setProperty("tool", name)
            row.addWidget(button)
        row.addSpacing(6)
        self.color_buttons = QButtonGroup(self)
        for color in COLORS:
            swatch = QToolButton()
            swatch.setObjectName("EaselSwatch")
            swatch.setCheckable(True)
            swatch.setChecked(color == self.color)
            swatch.setFixedSize(18, 18)
            swatch.setStyleSheet(f"QToolButton#EaselSwatch {{ background: {color}; }}")
            swatch.clicked.connect(lambda _=False, c=color: self.set_color(c))
            self.color_buttons.addButton(swatch)
            row.addWidget(swatch)
        row.addSpacing(6)
        row.addWidget(self._tool_button("ph.arrow-counter-clockwise", "Undo (⌘Z)", self.undo))
        row.addWidget(self._tool_button("ph.arrows-out", "Fit everything (⌘0)", self.fit))
        share = self._tool_button("ph.export", "Copy or save the easel")
        share.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(share)
        menu.addAction("Copy as Image", lambda: self.export.emit("copy"))
        menu.addAction("Save as PNG", lambda: self.export.emit("png"))
        menu.addAction("Save as PDF", lambda: self.export.emit("pdf"))
        share.setMenu(menu)
        share.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        row.addWidget(share)
        bar.adjustSize()

    def _tool_button(self, icon, tip, slot=None):
        button = QToolButton()
        button.setObjectName("EaselTool")
        button.setIcon(qta.icon(icon, color="#374151", color_on="white"))
        button.setIconSize(QSize(16, 16))
        button.setToolTip(tip)
        if slot:
            button.clicked.connect(slot)
        return button

    def hideEvent(self, event):
        self.scene.finish_drag()  # (switched away mid-drag: the button's let go where this can't hear it)
        super().hideEvent(event)

    def resizeEvent(self, event):
        self.toolbar.adjustSize()
        self.toolbar.move(max((self.width() - self.toolbar.width()) // 2, 8), 14)
        self.toolbar.raise_()
        super().resizeEvent(event)

    def set_tool(self, name):
        self.tool = name
        for button in self.tool_buttons.buttons():
            button.setChecked(button.property("tool") == name)
        self.canvas.setDragMode(QGraphicsView.DragMode.RubberBandDrag if name == "select"
                                else QGraphicsView.DragMode.NoDrag)
        self.canvas.viewport().setCursor(Qt.CursorShape.ArrowCursor if name == "select" else
                                         Qt.CursorShape.IBeamCursor if name == "text" else Qt.CursorShape.CrossCursor)

    def set_color(self, color):
        self.color = color
        for item in self.scene.selectedItems():  # recolour what's selected, too
            kind = item.data(KIND)
            if kind == "text":
                item.setDefaultTextColor(QColor(color))
            elif kind == "sticky":
                item.setBrush(QBrush(QColor(STICKY_COLORS[color])))
            elif kind in ("pen", "rect", "ellipse", "arrow"):
                pen = item.pen()
                pen.setColor(QColor(color))
                item.setPen(pen)

    def style_text(self, style):
        """⌘B / ⌘I / ⌘U: on the words selected in the text being written, or on every text and sticky
        note that's selected. Each turns the style on, or off if it's already all on."""
        focus = self.scene.focusItem()
        if self._editing_text() and focus.textCursor().hasSelection():
            cursors = [focus.textCursor()]
        else:
            items = [focus] if self._editing_text() else [
                i.text if i.data(KIND) == "sticky" else i for i in self.scene.selectedItems()
                if i.data(KIND) in ("text", "sticky")]
            cursors = []
            for item in items:
                cursor = QTextCursor(item.document())
                cursor.select(QTextCursor.SelectionType.Document)
                cursors.append(cursor)
        if not cursors:
            return
        def on(cursor):
            fmt = cursor.charFormat()
            return {"bold": fmt.fontWeight() >= QFont.Weight.Bold.value, "italic": fmt.fontItalic(),
                    "underline": fmt.fontUnderline()}[style]
        turn_on = not all(on(c) for c in cursors)
        change = QTextCharFormat()
        if style == "bold":
            change.setFontWeight(QFont.Weight.Bold if turn_on else QFont.Weight.Normal)
        elif style == "italic":
            change.setFontItalic(turn_on)
        else:
            change.setFontUnderline(turn_on)
        for cursor in cursors:
            cursor.mergeCharFormat(change)
        self._save_timer.start()

    def _editing_text(self) -> bool:
        focus = self.scene.focusItem()
        return isinstance(focus, QGraphicsTextItem) and bool(focus.textInteractionFlags())

    # --- Navigating ---------------------------------------------------------------------------------

    def eventFilter(self, obj, event):
        t = event.type()
        if t == event.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:  # ⌘-scroll (or pinch) zooms
                self.zoom(1.0015 ** event.angleDelta().y())
                return True
        elif t == event.Type.NativeGesture and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom(1 + event.value())
            return True
        elif t == event.Type.DragEnter and (event.mimeData().hasImage() or event.mimeData().hasUrls()):
            event.acceptProposedAction()
            return True
        elif t == event.Type.DragMove:
            event.acceptProposedAction()
            return True
        elif t == event.Type.Drop:
            self._drop(event.mimeData(), self.canvas.mapToScene(event.position().toPoint()))
            event.acceptProposedAction()
            return True
        return False

    def zoom(self, factor):
        scale = self.canvas.transform().m11() * factor
        if 0.1 <= scale <= 6:
            self.canvas.scale(factor, factor)
            self.scene.update_handles()  # (they stay the same size on screen)

    def fit(self):
        bounds = self.scene.itemsBoundingRect()
        if not bounds.isEmpty():
            self.canvas.fitInView(bounds.adjusted(-60, -80, 60, 60), Qt.AspectRatioMode.KeepAspectRatio)
            if self.canvas.transform().m11() > 1:
                self.canvas.resetTransform()
                self.canvas.centerOn(bounds.center())
            self.scene.update_handles()

    # --- Images -----------------------------------------------------------------------------------

    def add_image(self, image: QImage, source="", caption="", at: QPointF | None = None,
                  pixel_ratio=1.0) -> QGraphicsPixmapItem:
        """Add an image (pixel_ratio: 2 for a Retina capture, so it shows at its on-screen size)."""
        name = f"{new_id()}.png"
        image.save(str(self.folder / name), "PNG")
        scale = 1 / pixel_ratio
        if image.width() * scale > 900:  # big images start at a comfortable size; zoom in for detail
            scale = 900 / image.width()
        existing = self.scene.itemsBoundingRect()
        item = self._image_item(name, source, scale)
        if at is None:  # below everything that's already here, so nothing gets covered
            at = QPointF(existing.left(), existing.bottom() + 40) if not existing.isEmpty() else QPointF(0, 0)
        item.setPos(at)
        self.scene.addItem(item)
        if caption:
            label = _movable(QGraphicsTextItem(caption), "text")
            label.setFont(QFont("Helvetica Neue", 12))
            label.setDefaultTextColor(QColor("#6b7280"))
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            label.setPos(at + QPointF(0, item.sceneBoundingRect().height() + 6))
            self.scene.addItem(label)
        self.scene.clearSelection()
        item.setSelected(True)
        self.canvas.ensureVisible(item)
        self.save()
        return item

    def _image_item(self, name, source="", scale=1.0) -> QGraphicsPixmapItem:
        item = _movable(QGraphicsPixmapItem(QPixmap(str(self.folder / name))), "image")
        item.setScale(scale)  # full resolution kept, so zooming in stays sharp
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setData(IMAGE, name)
        item.setData(SOURCE, source)
        if source:
            item.setToolTip(f"{source}\nDouble-click to open")
        return item

    def paste(self):
        if self._editing_text():
            return
        self._drop(QApplication.clipboard().mimeData(), None)

    def _drop(self, mime, at):
        if mime.hasImage():
            self.add_image(QImage(mime.imageData()), at=at)
        elif mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile() and QImage(url.toLocalFile()).width() > 0:
                    self.add_image(QImage(url.toLocalFile()), at=at)
                elif not url.isLocalFile():  # a link: as a text card you can double-click
                    text = _movable(QGraphicsTextItem(url.toString()), "text")
                    text.setDefaultTextColor(QColor("#2563eb"))
                    text.setData(SOURCE, url.toString())
                    text.setPos(at or self.canvas.mapToScene(self.canvas.viewport().rect().center()))
                    self.scene.addItem(text)
        elif mime.hasText() and mime.text().strip():
            text = _movable(QGraphicsTextItem(mime.text().strip()), "text")
            text.setFont(QFont("Helvetica Neue", 16))
            text.setDefaultTextColor(QColor(self.color))
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            text.setPos(at or self.canvas.mapToScene(self.canvas.viewport().rect().center()))
            self.scene.addItem(text)

    # --- Arranging (the right-click menu) ---------------------------------------------------------------

    def _chosen(self):
        return [i for i in self.scene.selectedItems() if i.data(KIND) is not None and i.parentItem() is None]

    def duplicate(self):
        """Copies of what's selected, a little down and to the right, on top, and selected instead."""
        chosen = self._chosen()
        if not chosen:
            return
        top = self._z_range()[1]
        copies = []
        for n, item in enumerate(sorted(chosen, key=lambda i: i.zValue()), 1):
            entry = self._entry(item)
            entry.update(x=entry["x"] + 24, y=entry["y"] + 24, z=top + n)
            if item.data(SOURCE) and entry["kind"] not in ("text", "image"):
                entry["source"] = item.data(SOURCE)
            copies.append(self._add_entry(entry))
        self.scene.clearSelection()
        for copy in copies:
            if copy is not None:
                copy.setSelected(True)
        self.save()

    def _z_range(self) -> tuple[float, float]:
        zs = [i.zValue() for i in self.scene.items() if i.data(KIND) is not None and i.parentItem() is None]
        return (min(zs), max(zs)) if zs else (0.0, 0.0)

    def bring_to_front(self):
        top = self._z_range()[1]
        for n, item in enumerate(sorted(self._chosen(), key=lambda i: i.zValue()), 1):
            item.setZValue(top + n)
        self.save()

    def send_to_back(self):
        bottom = self._z_range()[0]
        for n, item in enumerate(sorted(self._chosen(), key=lambda i: -i.zValue()), 1):
            item.setZValue(bottom - n)
        self.save()

    def item_menu(self, item, at):
        """Right-click on something: it's selected (with whatever else is), then what can be done to it."""
        while item.parentItem() is not None and item.data(KIND) is None:  # (a sticky note's text: the note)
            item = item.parentItem()
        if item.data(KIND) is None:
            return
        if not item.isSelected():
            self.scene.clearSelection()
            item.setSelected(True)
        menu = QMenu(self)
        if item.data(SOURCE):
            menu.addAction("Open Page", lambda: self.open_link.emit(item.data(SOURCE)))
            menu.addSeparator()
        menu.addAction("Duplicate", self.duplicate)
        menu.addAction(for_here("Bring to Front\t⇧⌘]"), self.bring_to_front)
        menu.addAction(for_here("Send to Back\t⇧⌘["), self.send_to_back)
        menu.addSeparator()
        menu.addAction("Delete", lambda: (self._delete(self._chosen())))
        menu.exec(at)

    def _delete(self, items):
        for item in items:
            self.scene.removeItem(item)

    def delete_selected(self):
        if self._editing_text():
            return
        for item in self.scene.selectedItems():
            self.scene.removeItem(item)

    # --- Exporting --------------------------------------------------------------------------------

    def _contents(self) -> QRectF:
        """Everything on the easel, with a margin (no selection outlines or handles in the picture)."""
        self.scene.clearSelection()
        if self.scene.focusItem() is not None:  # (no text cursor in the picture either)
            self.scene.focusItem().clearFocus()
        bounds = QRectF()
        for item in self.scene.items():
            if item.data(KIND) is not None and item.parentItem() is None:
                # (a note's text can run past the note: that's part of what's on the easel)
                bounds = bounds.united(item.sceneBoundingRect()).united(item.mapRectToScene(item.childrenBoundingRect()))
        return bounds.adjusted(-EXPORT_MARGIN, -EXPORT_MARGIN, EXPORT_MARGIN, EXPORT_MARGIN) if not bounds.isEmpty() else bounds

    def is_empty(self) -> bool:
        return not any(item.data(KIND) is not None for item in self.scene.items())

    def render_image(self, scale=2.0) -> QImage | None:
        """The whole easel as a picture (at `scale`: 2 is sharp on a Retina screen), or None if it's empty."""
        source = self._contents()
        if source.isEmpty():
            return None
        scale = min(scale, (EXPORT_MAX_PIXELS / (source.width() * source.height())) ** 0.5)
        image = QImage(max(1, round(source.width() * scale)), max(1, round(source.height() * scale)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(PAPER))
        p = QPainter(image)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        self.scene.render(p, QRectF(image.rect()), source)
        p.end()
        image.setDevicePixelRatio(scale)
        return image

    def render_pdf(self, path: str) -> bool:
        """The whole easel as a one-page PDF the size of what's on it (drawings and text stay sharp)."""
        source = self._contents()
        if source.isEmpty():
            return False
        writer = QPdfWriter(path)
        writer.setTitle(self.title)
        writer.setResolution(72)  # one point of the easel is one point on the page...
        writer.setPageMargins(QMarginsF(0, 0, 0, 0))
        shrink = min(1.0, PDF_MAX_SIDE / max(source.width(), source.height()))  # ...up to what PDF readers allow
        writer.setPageSize(QPageSize(source.size() * shrink, QPageSize.Unit.Point, "Easel"))
        p = QPainter(writer)
        if not p.isActive():
            return False
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(0, 0, writer.width(), writer.height())
        p.fillRect(target, QColor(PAPER))
        self.scene.render(p, target, source)
        p.end()
        return True

    # --- Saving, loading, undo ----------------------------------------------------------------------

    @property
    def title(self) -> str:
        return self.title_edit.text().strip() or "Untitled Easel"

    def to_dict(self) -> dict:
        items = []
        # Bottom first (by z, then stacking order), so loading adds them back in the same order
        for item in self.scene.items(Qt.SortOrder.AscendingOrder):
            if item.data(KIND) is not None and item.parentItem() is None:
                items.append(self._entry(item))
        return {"title": self.title_edit.text().strip(), "items": items}

    def _entry(self, item) -> dict:
        """One item as it's saved (and copied, to duplicate it)."""
        kind = item.data(KIND)
        entry = {"kind": kind, "x": item.pos().x(), "y": item.pos().y(), "z": item.zValue()}
        if item.rotation():
            entry["rotation"] = item.rotation()
        if kind == "text":
            entry.update(html=item.toHtml(), color=item.defaultTextColor().name(),
                         size=text_size(item), source=item.data(SOURCE) or "")
        elif kind == "sticky":
            r = item.rect()
            entry.update(w=r.width(), h=r.height(), color=item.brush().color().name(), html=item.text.toHtml())
        elif kind in ("rect", "ellipse"):
            r = item.rect()
            entry.update(rx=r.x(), ry=r.y(), w=r.width(), h=r.height(), color=item.pen().color().name())
        elif kind in ("pen", "arrow"):
            path = item.path()
            entry.update(points=[[path.elementAt(i).x, path.elementAt(i).y, int(path.elementAt(i).type.value)]
                                 for i in range(path.elementCount())], color=item.pen().color().name())
        elif kind == "image":
            entry.update(image=item.data(IMAGE), source=item.data(SOURCE) or "", scale=item.scale())
        return entry

    def from_dict(self, data: dict):
        self._loading = True
        self.scene.handles = None  # (no handles to move while everything's taken away)
        self.scene.clearSelection()
        self.scene.clear()
        self.scene.add_handles()
        self.title_edit.setText(data.get("title", ""))
        for entry in data.get("items", []):
            self._add_entry(entry)
        self._loading = False

    def _add_entry(self, entry):
        item = self._make(entry)
        if item is not None:
            item.setPos(entry.get("x", 0), entry.get("y", 0))
            item.setZValue(entry.get("z", 0))
            item.setRotation(entry.get("rotation", 0))
            self.scene.addItem(item)
        return item

    def _make(self, e):
        kind, color = e.get("kind"), QColor(e.get("color", COLORS[0]))
        pen = QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if kind == "text":
            item = _movable(QGraphicsTextItem(), "text")
            item.setHtml(e.get("html", ""))
            item.setDefaultTextColor(color)
            font = QFont("Helvetica Neue")
            font.setPointSizeF(float(e.get("size", 18)))
            item.setFont(font)
            item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            if e.get("source"):
                item.setData(SOURCE, e["source"])
            return item
        if kind == "sticky":
            item = _movable(StickyNote(QRectF(0, 0, e.get("w", 200), e.get("h", 160)), e.get("color", "#fde68a")), "sticky")
            item.text.setHtml(e.get("html", ""))
            return item
        if kind in ("rect", "ellipse"):
            cls = QGraphicsRectItem if kind == "rect" else QGraphicsEllipseItem
            item = _movable(cls(QRectF(e.get("rx", 0), e.get("ry", 0), e.get("w", 0), e.get("h", 0))), kind)
            item.setPen(pen)
            return item
        if kind in ("pen", "arrow"):
            path = QPainterPath()
            for x, y, element in e.get("points", []):
                (path.moveTo if element == 0 else path.lineTo)(x, y)
            item = _movable(QGraphicsPathItem(path), kind)
            item.setPen(pen)
            return item
        if kind == "image" and (self.folder / e.get("image", "")).is_file():
            return self._image_item(e["image"], e.get("source", ""), e.get("scale", 1.0))
        return None

    def load(self):
        file = self.folder / "easel.json"
        data = storage.read_json(file) or {}
        self.from_dict(data)
        self._history = [json.dumps(self.to_dict())]
        QTimer.singleShot(0, self._save_timer.stop)  # loading isn't an edit
        QTimer.singleShot(0, self.fit)

    def save(self):
        self._save_timer.stop()
        if self.scene._resizing is not None or self.scene._turning is not None:  # (once, when the drag ends)
            return
        state = json.dumps(self.to_dict())
        if self._history and state == self._history[-1]:
            return
        self._history.append(state)
        del self._history[:-UNDO_LIMIT]
        self._future.clear()
        storage.write_json(self.folder / "easel.json", json.loads(state))

    def undo(self):
        if self._editing_text() or self._dragging():
            return
        self.save()  # anything not yet saved becomes a step first
        if len(self._history) > 1:
            self._future.append(self._history.pop())
            self._restore(self._history[-1])

    def redo(self):
        if self._future and not self._dragging():
            state = self._future.pop()
            self._history.append(state)
            self._restore(state)

    def _dragging(self) -> bool:
        return any(x is not None for x in (self.scene._drawing, self.scene._resizing, self.scene._turning))

    def _restore(self, state):
        self.from_dict(json.loads(state))
        # The history holds exactly what's on screen now, so the save the reload triggers (Qt reports
        # scene changes later) finds nothing new and leaves redo alone
        self._history[-1] = json.dumps(self.to_dict())
        QTimer.singleShot(0, self._save_timer.stop)
        storage.write_json(self.folder / "easel.json", json.loads(state))
        self.title_changed.emit(self.title)


class CaptureOverlay(QWidget):
    """⇧⌘2 on a web page: drag a box to capture that part of the page (Esc cancels)."""
    captured = pyqtSignal(QImage, float)  # the image, and its pixel ratio (2 on a Retina screen)

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.shot = view.grab()  # the page as it looks now
        self._start = self._end = None
        self.setGeometry(view.rect())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.show()
        self.raise_()
        self.setFocus()

    def _rect(self):
        return QRectF(self._start, self._end).normalized().toRect() if self._start and self._end else None

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        rect = self._rect()
        if rect:
            ratio = self.shot.devicePixelRatio()
            source = QRectF(rect.x() * ratio, rect.y() * ratio, rect.width() * ratio, rect.height() * ratio)
            p.drawPixmap(QRectF(rect), self.shot, source)
            p.setPen(QPen(QColor("#7c5cff"), 2))
            p.drawRect(rect)
        else:
            p.setPen(QColor("white"))
            p.setFont(QFont("Helvetica Neue", 15, QFont.Weight.DemiBold))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drag to capture part of this page · Esc to cancel")

    def mousePressEvent(self, event):
        self._start = self._end = event.position()
        self.update()

    def mouseMoveEvent(self, event):
        self._end = event.position()
        self.update()

    def mouseReleaseEvent(self, event):
        self._end = event.position()
        rect = self._rect()
        self.close()
        self.deleteLater()
        if rect and rect.width() > 8 and rect.height() > 8:
            ratio = self.shot.devicePixelRatio()
            image = self.shot.copy(int(rect.x() * ratio), int(rect.y() * ratio),
                                   int(rect.width() * ratio), int(rect.height() * ratio)).toImage()
            image.setDevicePixelRatio(1)
            self.captured.emit(image, ratio)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self.deleteLater()


class EaselsFeature(Feature):
    """Easels (easel:<id>): new ones, Capture (⇧⌘2) onto the one you used last, and copying or saving them."""
    name = "easels"

    def __init__(self, window):
        super().__init__(window)
        self.last = None  # where Capture puts things

    def view_for(self, tab):
        if not is_easel(tab.url):
            return None
        view = EaselView(tab.url)
        view.open_link.connect(lambda url: self.window.open_url(url))
        view.export.connect(lambda kind: self.export(view, kind))
        return view

    def tab_shown(self, tab):
        if tab is not None and is_easel(tab.url):
            self.last = tab

    def new_easel(self, activate=True):
        from .models import TODAY, Tab
        window = self.window
        tab = Tab(new_easel_url(), "Untitled Easel")
        window.store.add_tab(tab, TODAY, window.store.active_space, 0)
        if activate:
            window.activate_tab(tab)
        else:
            window._ensure_view(tab)
        return tab

    def export(self, easel, kind):
        """An easel's toolbar: copy it as a picture, or save it as a PNG or PDF in your downloads folder."""
        from .downloads import reveal_in_finder
        from .page_actions import safe_filename
        window = self.window
        if easel.is_empty():
            window.toast("This easel is empty")
            return
        if kind == "copy":
            QApplication.clipboard().setImage(easel.render_image())
            window.toast("Copied the easel as an image")
            return
        path = window._new_file(safe_filename(easel.title, "Easel"), kind)
        ok = easel.render_pdf(str(path)) if kind == "pdf" else easel.render_image().save(str(path), "PNG")
        if not ok:
            window.toast("Couldn't save the easel")
            return
        window.toast(f"Saved {path.name}", lambda: reveal_in_finder(str(path)), "Show")

    def capture(self):
        """Arc's Capture (⇧⌘2): drag a box over the page; that part goes onto your easel."""
        window = self.window
        view, tab = window._current_view(), window.current_tab
        if view is None:
            window.toast("Capture works on web pages")
            return
        overlay = CaptureOverlay(view)
        overlay.captured.connect(lambda image, ratio: self._add_capture(image, ratio, tab.url, tab.label))

    def _add_capture(self, image, ratio, url, title):
        window = self.window
        # The easel you used last, if it's still around; otherwise a new one
        easel = self.last if self.last is not None and window.store.locate(self.last) else None
        if easel is None:
            easel = self.last = self.new_easel(activate=False)
        window._ensure_view(easel).add_image(image, source=url, caption=title, pixel_ratio=ratio)
        window.toast(f"Captured to “{easel.label}”")

    def commands(self):
        return [("New Easel", "ph.chalkboard-simple", self.new_easel),
                ("Capture Part of This Page to an Easel", "ph.selection-plus", self.capture)]

    def menus(self, add, menus, bar):
        add(menus["File"], "New Easel", "", self.new_easel)
        add(menus["File"], "Capture to Easel", "Ctrl+Shift+2", self.capture)


FEATURE = EaselsFeature
