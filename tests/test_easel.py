import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPen
from PyQt6.QtWidgets import QGraphicsRectItem, QGraphicsTextItem

from browser.easel import (ROTATE, EaselView, StickyNote, _movable, apply_geometry, arrow_path, geometry, new_easel_url,
                           outline, resized, rotated, text_size, turn)


def _close(a: QPointF, b: QPointF, within=0.01) -> bool:
    return math.hypot(a.x() - b.x(), a.y() - b.y()) < within


class _Mouse:
    """Stands in for a QGraphicsSceneMouseEvent (PyQt can't make one) on a handle, where the scene
    handles it all itself."""

    def __init__(self, pos, buttons, modifiers):
        self._pos, self._buttons, self._modifiers = pos, buttons, modifiers

    def scenePos(self): return self._pos
    def button(self): return Qt.MouseButton.LeftButton
    def buttons(self): return self._buttons
    def modifiers(self): return self._modifiers
    def accept(self): pass


def _drag(scene, start: QPointF, end: QPointF, modifiers=Qt.KeyboardModifier.NoModifier):
    """A mouse drag on a handle, as the canvas hands it to the scene."""
    scene.mousePressEvent(_Mouse(start, Qt.MouseButton.LeftButton, modifiers))
    scene.mouseMoveEvent(_Mouse(end, Qt.MouseButton.LeftButton, modifiers))
    scene.mouseReleaseEvent(_Mouse(end, Qt.MouseButton.NoButton, modifiers))


def test_easel_saves_and_loads(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    easel.title_edit.setText("Plans")
    rect = _movable(QGraphicsRectItem(QRectF(0, 0, 50, 30)), "rect")
    easel.scene.addItem(rect)
    sticky = _movable(StickyNote(QRectF(0, 0, 200, 160), "#fde68a", "Remember"), "sticky")
    sticky.setPos(100, 100)
    easel.scene.addItem(sticky)
    image = QImage(400, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor("teal"))
    easel.add_image(image, source="https://example.com/page", caption="Example", pixel_ratio=2)
    easel.save()

    again = EaselView(url)
    kinds = sorted(i["kind"] for i in again.to_dict()["items"])
    assert kinds == ["image", "rect", "sticky", "text"] and again.title == "Plans"
    img = next(i for i in again.to_dict()["items"] if i["kind"] == "image")
    assert img["source"] == "https://example.com/page" and img["scale"] == 0.5  # Retina capture at its real size
    note = next(i for i in again.scene.items() if i.data(0) == "sticky")
    assert note.text.toPlainText() == "Remember" and note.pos() == QPointF(100, 100)


def test_undo_and_redo(data_dir):
    easel = EaselView(new_easel_url())
    easel.scene.addItem(_movable(QGraphicsRectItem(QRectF(0, 0, 10, 10)), "rect"))
    easel.save()
    easel.scene.addItem(_movable(QGraphicsRectItem(QRectF(20, 0, 10, 10)), "rect"))
    easel.save()
    easel.undo()
    assert len(easel.to_dict()["items"]) == 1
    easel.redo()
    assert len(easel.to_dict()["items"]) == 2


def test_arrow_has_a_head():
    path = arrow_path(QPointF(0, 0), QPointF(100, 0))
    assert path.elementCount() == 6  # the line, plus two strokes for the head


def _settle():
    # Let Qt deliver queued scene-change signals and run timers, like the real app would
    import time
    from PyQt6.QtWidgets import QApplication
    end = time.time() + 1.0
    while time.time() < end:
        QApplication.processEvents()


def test_stacking_order_survives_reloads(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    for i, color in enumerate(("red", "green", "blue")):  # blue ends up on top
        rect = _movable(QGraphicsRectItem(QRectF(0, 0, 50, 50)), "rect")
        rect.setPen(QColor(color))
        rect.setPos(i * 10, 0)
        easel.scene.addItem(rect)
    easel.save()
    for _ in range(3):  # reload a few times: the order must not flip
        easel = EaselView(url)
    top = easel.scene.items(QPointF(25, 25))[0]
    assert top.pen().color().name() == QColor("blue").name()


def test_several_undos_then_redo(data_dir):
    easel = EaselView(new_easel_url())
    items = []
    for i in range(3):
        item = _movable(QGraphicsRectItem(QRectF(i * 30, 0, 20, 20)), "rect")
        easel.scene.addItem(item)
        items.append(item)
    easel.save()
    for item in items[:2]:  # two separate deletions
        easel.scene.removeItem(item)
        _settle()
        easel.save()
    easel.undo()
    _settle()
    easel.undo()
    _settle()
    assert len(easel.to_dict()["items"]) == 3
    easel.redo()
    _settle()
    assert len(easel.to_dict()["items"]) == 2


# --- Resizing and exporting -------------------------------------------------------------------------


def test_resized_keeps_the_opposite_corner():
    start = QRectF(10, 10, 100, 50)
    # The bottom-right corner (2) dragged out: the top left stays
    assert resized(start, 2, QPointF(210, 110)) == QRectF(10, 10, 200, 100)
    # The top-left corner (0) dragged in: the bottom right stays
    assert resized(start, 0, QPointF(60, 40)) == QRectF(60, 40, 50, 20)
    # Dragged past the other corner: never smaller than the minimum, still anchored
    small = resized(start, 2, QPointF(0, 0))
    assert small.topLeft() == QPointF(10, 10) and small.width() == 12 and small.height() == 12


def test_resized_can_keep_the_shape():
    start = QRectF(0, 0, 100, 50)
    out = resized(start, 2, QPointF(300, 60), keep_shape=True)
    assert out == QRectF(0, 0, 300, 150)


def test_resizing_bakes_into_each_kind_and_saves(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    image = QImage(200, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("teal"))
    picture = easel.add_image(image, at=QPointF(0, 0))
    sticky = _movable(StickyNote(QRectF(0, 0, 200, 160), "#fde68a", "Note"), "sticky")
    sticky.setPos(300, 0)
    easel.scene.addItem(sticky)
    arrow = _movable(easel.scene.addPath(arrow_path(QPointF(0, 300), QPointF(100, 300))), "arrow")

    for item, target in ((picture, QRectF(0, 0, 400, 200)), (sticky, QRectF(300, 0, 300, 100)),
                         (arrow, QRectF(0, 300, 200, 0))):
        start = geometry(item)
        shape = item.path() if item.data(0) == "arrow" else item.scale()
        apply_geometry(item, start, shape, target)
    assert geometry(picture) == QRectF(0, 0, 400, 200) and picture.scale() == 2
    assert geometry(sticky) == QRectF(300, 0, 300, 100) and sticky.text.textWidth() == 280
    line = arrow.path()
    assert (line.elementAt(0).x, line.elementAt(1).x) == (0, 200)  # the line, with a fresh head

    easel.save()
    again = EaselView(url)
    items = {i["kind"]: i for i in again.to_dict()["items"]}
    assert items["image"]["scale"] == 2 and (items["sticky"]["w"], items["sticky"]["h"]) == (300, 100)


def test_handles_follow_one_selected_item(data_dir):
    easel = EaselView(new_easel_url())
    a = _movable(QGraphicsRectItem(QRectF(0, 0, 50, 50)), "rect")
    b = _movable(QGraphicsRectItem(QRectF(100, 0, 50, 50)), "rect")
    easel.scene.addItem(a)
    easel.scene.addItem(b)
    a.setSelected(True)
    assert easel.scene.handles.target is a
    assert easel.scene.handles.handle_at(QPointF(50, 50)) == 2 and easel.scene.handles.handle_at(QPointF(25, 25)) is None
    b.setSelected(True)  # two things: no handles
    assert easel.scene.handles.target is None
    easel.save()
    assert len(easel.to_dict()["items"]) == 2  # the handles are never saved
    easel.scene.clearSelection()
    a.setSelected(True)
    easel.undo()  # (reloads the scene: the handles come back)
    assert easel.scene.handles.scene() is easel.scene


def test_render_image_and_pdf(data_dir, tmp_path):
    easel = EaselView(new_easel_url())
    assert easel.is_empty() and easel.render_image() is None and not easel.render_pdf(str(tmp_path / "e.pdf"))
    rect = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    rect.setBrush(QColor("#ff0000"))
    rect.setPen(QPen(Qt.PenStyle.NoPen))
    easel.scene.addItem(rect)
    rect.setSelected(True)
    image = easel.render_image(scale=2)
    # The box plus a 40-point margin all round, at twice the size
    assert (image.width(), image.height()) == (360, 260) and image.devicePixelRatio() == 2
    assert image.pixelColor(180, 130).name() == "#ff0000"  # the middle of the box
    assert image.pixelColor(10, 10).name() == "#fafaf9"    # the paper, no dots
    assert not easel.scene.selectedItems()  # (no selection outline in the picture)
    pdf = tmp_path / "e.pdf"
    assert easel.render_pdf(str(pdf)) and pdf.read_bytes().startswith(b"%PDF")


def test_a_flat_stroke_stays_put_when_resized(data_dir):
    from PyQt6.QtGui import QPainterPath
    easel = EaselView(new_easel_url())
    path = QPainterPath(QPointF(0, 100))
    path.lineTo(100, 100)
    line = _movable(easel.scene.addPath(path), "pen")
    start = geometry(line)
    apply_geometry(line, start, line.path(), resized(start, 0, QPointF(-20, 60)))  # its top-left handle
    out = line.path()
    assert (out.elementAt(0).x, out.elementAt(0).y, out.elementAt(1).x, out.elementAt(1).y) == (-20, 100, 100, 100)


def test_export_includes_a_notes_overflowing_text(data_dir):
    easel = EaselView(new_easel_url())
    note = _movable(StickyNote(QRectF(0, 0, 200, 40), "#fde68a", "\n".join(f"line {i}" for i in range(30))), "sticky")
    easel.scene.addItem(note)
    note.text.setFocus()
    image = easel.render_image(scale=1)
    assert image.height() > 400  # the whole text, not just the 40-point note
    assert not note.text.hasFocus()  # (a shown easel's caret: in the live test)


def test_a_huge_easel_fits_a_pdf_page(data_dir, tmp_path):
    import re
    easel = EaselView(new_easel_url())
    easel.scene.addItem(_movable(QGraphicsRectItem(QRectF(0, 0, 20_000, 300)), "rect"))
    pdf = tmp_path / "wide.pdf"
    assert easel.render_pdf(str(pdf))
    box = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", pdf.read_bytes())
    assert box and max(float(box.group(1)), float(box.group(2))) <= 14_400


def test_the_canvas_scrolls_without_scrollbars(data_dir, qapp):
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QWheelEvent
    from PyQt6.QtWidgets import QApplication
    easel = EaselView(new_easel_url())
    easel.resize(600, 400)
    easel.show()
    qapp.processEvents()
    bar = easel.canvas.verticalScrollBar()
    assert not bar.isVisible() and not easel.canvas.horizontalScrollBar().isVisible()
    before = bar.value()
    at = QPointF(300, 200)
    QApplication.sendEvent(easel.canvas.viewport(), QWheelEvent(at, at, QPoint(0, -40), QPoint(0, -120),
                                                                 Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                                                 Qt.ScrollPhase.NoScrollPhase, False))
    assert bar.value() > before  # (the trackpad or wheel still moves about)
    easel.close()


def test_turning_keeps_the_middle_and_saves(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    box = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    box.setPos(200, 100)
    easel.scene.addItem(box)
    turn(box, 90)
    assert box.rotation() == 90 and _close(box.mapToScene(QPointF(50, 25)), QPointF(250, 125))
    assert geometry(box) == QRectF(200, 100, 100, 50)  # (its box as if it weren't turned)
    corners_before = outline(box)
    easel.save()
    again = EaselView(url)
    item = next(i for i in again.scene.items() if i.data(0) == "rect")
    assert item.rotation() == 90 and all(_close(a, b) for a, b in zip(outline(item), corners_before))


def test_the_knob_turns_the_selected_item(data_dir):
    easel = EaselView(new_easel_url())
    box = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    easel.scene.addItem(box)
    box.setSelected(True)
    handles = easel.scene.handles
    knob = handles._knob(outline(box))[1]
    assert handles.handle_at(knob) == ROTATE and knob.x() == 50 and knob.y() < 0  # above the top edge's middle
    middle = QPointF(50, 25)
    # A quarter turn clockwise, a little off: it settles on square
    _drag(easel.scene, knob, middle + QPointF(60, 2))
    assert box.rotation() == 90 and _close(box.mapToScene(QPointF(50, 25)), middle)
    # With ⇧ it goes in steps of 15°
    _drag(easel.scene, handles._knob(outline(box))[1], middle + rotated(QPointF(60, 0), 20),
          Qt.KeyboardModifier.ShiftModifier)
    assert box.rotation() == 105
    assert len(easel._history) >= 3  # each turn is an undo step
    easel.undo()
    assert next(i for i in easel.scene.items() if i.data(0) == "rect").rotation() == 90


def test_resizing_a_turned_item_holds_the_opposite_corner(data_dir):
    easel = EaselView(new_easel_url())
    box = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    box.setPos(100, 100)
    easel.scene.addItem(box)
    turn(box, 30)
    box.setSelected(True)
    held = outline(box)[0]
    corner = outline(box)[2]
    assert easel.scene.handles.handle_at(corner) == 2
    # The bottom-right corner pulled out along the box's own sides, by 50 and 25
    _drag(easel.scene, corner, corner + rotated(QPointF(50, 25), 30))
    assert box.rotation() == 30 and abs(box.rect().width() - 150) < 0.01 and abs(box.rect().height() - 75) < 0.01
    assert _close(outline(box)[0], held)


def test_text_resizes_by_its_size_and_keeps_it(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    text = _movable(QGraphicsTextItem("Hello"), "text")
    text.setFont(QFont("Helvetica Neue", 18))
    text.setPos(10, 10)
    easel.scene.addItem(text)
    start = geometry(text)
    apply_geometry(text, start, text_size(text), resized(start, 2, QPointF(10, 10) + QPointF(start.width(), start.height()) * 2,
                                                         keep_shape=True))
    assert text_size(text) == 36 and text.pos() == QPointF(10, 10)  # (the top left held still)
    # Dragged from the top left instead, the bottom right stays put
    start = geometry(text)
    apply_geometry(text, start, text_size(text), resized(start, 0, start.center(), keep_shape=True))
    assert text_size(text) == 18 and abs(geometry(text).right() - start.right()) < 0.01
    text.setSelected(True)
    assert easel.scene.handles.target is text  # (text has handles too)
    easel.save()
    again = EaselView(url)
    item = next(i for i in again.scene.items() if i.data(0) == "text")
    assert text_size(item) == 18 and item.toPlainText() == "Hello"
    apply_geometry(item, geometry(item), 18, QRectF(0, 0, 1, 1))
    assert text_size(item) == 6  # (never unreadably small)


def test_a_drag_let_go_elsewhere_ends_and_undo_works_again(data_dir):
    easel = EaselView(new_easel_url())
    box = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    easel.scene.addItem(box)
    easel.save()
    box.setSelected(True)
    knob = easel.scene.handles._knob(outline(box))[1]
    easel.scene.mousePressEvent(_Mouse(knob, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    easel.scene.mouseMoveEvent(_Mouse(QPointF(150, 25), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    turned = box.rotation()
    # The release went somewhere else (you switched tabs mid-drag): the next move, with no button, ends it
    easel.scene.mouseMoveEvent(_Mouse(QPointF(50, 200), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert box.rotation() == turned and not easel._dragging()
    easel.undo()
    assert next(i for i in easel.scene.items() if i.data(0) == "rect").rotation() == 0


def test_text_can_be_bold_italic_and_underlined_and_it_saves(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    text = _movable(QGraphicsTextItem("Big idea"), "text")
    easel.scene.addItem(text)
    text.setSelected(True)
    easel.style_text("bold")
    easel.style_text("italic")
    from PyQt6.QtGui import QTextCursor
    cursor = QTextCursor(text.document())
    cursor.setPosition(2)
    assert cursor.charFormat().fontWeight() >= 700 and cursor.charFormat().fontItalic()
    easel.style_text("bold")  # (again: off)
    cursor = QTextCursor(text.document()); cursor.setPosition(2)
    assert cursor.charFormat().fontWeight() < 700
    easel.save()
    again = EaselView(url)
    item = next(i for i in again.scene.items() if i.data(0) == "text")
    cursor = QTextCursor(item.document()); cursor.setPosition(2)
    assert cursor.charFormat().fontItalic() and item.toPlainText() == "Big idea"


def test_duplicate_and_stacking_order(data_dir):
    url = new_easel_url()
    easel = EaselView(url)
    a = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    b = _movable(QGraphicsRectItem(QRectF(0, 0, 100, 50)), "rect")
    b.setZValue(1)
    easel.scene.addItem(a)
    easel.scene.addItem(b)
    turn(a, 30)
    a.setSelected(True)
    easel.bring_to_front()
    assert a.zValue() > b.zValue()
    easel.send_to_back()
    assert a.zValue() < b.zValue()
    easel.duplicate()
    copies = easel.scene.selectedItems()
    assert len(copies) == 1 and copies[0] is not a
    copy = copies[0]
    assert copy.rotation() == 30 and copy.pos() == a.pos() + QPointF(24, 24) and copy.zValue() > b.zValue()
    assert copy.rect() == a.rect()
    again = EaselView(url)
    assert sum(1 for i in again.to_dict()["items"] if i["kind"] == "rect") == 3
