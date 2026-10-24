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
from pathlib import Path

import qtawesome as qta
from PyQt6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem,
                             QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
                             QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget)

from . import storage
from .models import new_id

EASEL_SCHEME = "easel:"
COLORS = ["#1f2937", "#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#a855f7"]
STICKY_COLORS = {"#1f2937": "#fde68a", "#ef4444": "#fecaca", "#f59e0b": "#fde68a", "#22c55e": "#bbf7d0",
                 "#3b82f6": "#bfdbfe", "#a855f7": "#e9d5ff"}
TOOLS = [("select", "ph.cursor", "Select & move (V)", "V"), ("text", "ph.text-t", "Text (T)", "T"),
         ("sticky", "ph.note", "Sticky note (S)", "S"), ("pen", "ph.pencil-simple", "Draw (P)", "P"),
         ("rect", "ph.square", "Rectangle (R)", "R"), ("ellipse", "ph.circle", "Ellipse (O)", "O"),
         ("arrow", "ph.arrow-up-right", "Arrow (A)", "A")]
UNDO_LIMIT = 60
KIND = 0     # QGraphicsItem.data() keys
SOURCE = 1   # for images: the page they were captured from
IMAGE = 2    # for images: their file name


def is_easel(url: str) -> bool:
    return url.startswith(EASEL_SCHEME)


def new_easel_url() -> str:
    return EASEL_SCHEME + new_id()


def easel_folder(url: str) -> Path:
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
    tile.fill(QColor("#fafaf9"))
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
        self.text.setTextWidth(rect.width() - 20)
        self.text.setPos(rect.topLeft() + QPointF(10, 10))
        self.text.setDefaultTextColor(QColor("#1f2937"))
        self.text.setFont(QFont("Helvetica Neue", 14))
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)


class EaselScene(QGraphicsScene):
    def __init__(self, easel):
        super().__init__(easel)
        self.easel = easel
        self._start: QPointF | None = None
        self._drawing = None

    def mousePressEvent(self, event):
        tool = self.easel.tool
        pos = event.scenePos()
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

    def mouseMoveEvent(self, event):
        if self._drawing is None:
            return super().mouseMoveEvent(event)
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
        if self._drawing is None:
            return super().mouseReleaseEvent(event)
        self._drawing, self._start = None, None

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.scenePos(), self.views()[0].transform()) if self.views() else None
        if item is not None and item.data(SOURCE):  # a capture: back to the page it came from
            self.easel.open_link.emit(item.data(SOURCE))
            return
        super().mouseDoubleClickEvent(event)


class EaselView(QWidget):
    """The whole easel tab: a floating toolbar over an endless canvas."""
    title_changed = pyqtSignal(str)
    open_link = pyqtSignal(str)

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
                           ("Ctrl+0", self.fit)):
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

    def fit(self):
        bounds = self.scene.itemsBoundingRect()
        if not bounds.isEmpty():
            self.canvas.fitInView(bounds.adjusted(-60, -80, 60, 60), Qt.AspectRatioMode.KeepAspectRatio)
            if self.canvas.transform().m11() > 1:
                self.canvas.resetTransform()
                self.canvas.centerOn(bounds.center())

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

    def delete_selected(self):
        if self._editing_text():
            return
        for item in self.scene.selectedItems():
            self.scene.removeItem(item)

    # --- Saving, loading, undo ----------------------------------------------------------------------

    @property
    def title(self) -> str:
        return self.title_edit.text().strip() or "Untitled Easel"

    def to_dict(self) -> dict:
        items = []
        # Bottom first (by z, then stacking order), so loading adds them back in the same order
        for item in self.scene.items(Qt.SortOrder.AscendingOrder):
            kind = item.data(KIND)
            if kind is None or (item.parentItem() is not None):
                continue
            entry = {"kind": kind, "x": item.pos().x(), "y": item.pos().y(), "z": item.zValue()}
            if kind == "text":
                entry.update(html=item.toHtml(), color=item.defaultTextColor().name(),
                             size=item.font().pointSize(), source=item.data(SOURCE) or "")
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
            items.append(entry)
        return {"title": self.title_edit.text().strip(), "items": items}

    def from_dict(self, data: dict):
        self._loading = True
        self.scene.clear()
        self.title_edit.setText(data.get("title", ""))
        for entry in data.get("items", []):
            item = self._make(entry)
            if item is not None:
                item.setPos(entry.get("x", 0), entry.get("y", 0))
                item.setZValue(entry.get("z", 0))
                self.scene.addItem(item)
        self._loading = False

    def _make(self, e):
        kind, color = e.get("kind"), QColor(e.get("color", COLORS[0]))
        pen = QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        if kind == "text":
            item = _movable(QGraphicsTextItem(), "text")
            item.setHtml(e.get("html", ""))
            item.setDefaultTextColor(color)
            item.setFont(QFont("Helvetica Neue", e.get("size", 18)))
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
        state = json.dumps(self.to_dict())
        if self._history and state == self._history[-1]:
            return
        self._history.append(state)
        del self._history[:-UNDO_LIMIT]
        self._future.clear()
        storage.write_json(self.folder / "easel.json", json.loads(state))

    def undo(self):
        if self._editing_text() or self.scene._drawing is not None:
            return
        self.save()  # anything not yet saved becomes a step first
        if len(self._history) > 1:
            self._future.append(self._history.pop())
            self._restore(self._history[-1])

    def redo(self):
        if self._future and self.scene._drawing is None:
            state = self._future.pop()
            self._history.append(state)
            self._restore(state)

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
