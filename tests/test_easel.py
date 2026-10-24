from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QGraphicsRectItem

from browser.easel import EaselView, StickyNote, _movable, arrow_path, new_easel_url


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
