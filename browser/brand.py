# The browser's name and icon.
#
# NAME is what you see: the Dock, the menu bar, window titles, messages. The app's internal name
# ("PythonBrowser") stays as it was underneath — the data folder, the Keychain entries and the bundle
# ID — so a rename never strands your tabs, sign-ins, saved tokens, or macOS's memory of it as your
# default browser and its camera/microphone permissions.

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

NAME = "Halo"
INTERNAL_NAME = "PythonBrowser"  # data folder, Keychain service, bundle; don't change


def _ring_path(center, rx, ry, thickness, lift):
    """A halo seen from slightly above: the band between two ellipses, the inner one raised, so the
    near side of the ring comes out thicker than the far side."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addEllipse(center, rx, ry)
    path.addEllipse(QPointF(center.x(), center.y() - lift), rx - thickness, ry - thickness * 0.72)
    return path


def _blurred(draw, size, amount):
    """`draw` painted small and scaled back up: a soft glow without a blur filter."""
    small = max(8, size // amount)
    layer = QImage(small, small, QImage.Format.Format_ARGB32_Premultiplied)
    layer.fill(Qt.GlobalColor.transparent)
    p = QPainter(layer)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(small / 1024, small / 1024)
    draw(p)
    p.end()
    for step in (small * 2, small * 4):  # in stages, so it stays smooth
        if step < size:
            layer = layer.scaled(step, step, transformMode=Qt.TransformationMode.SmoothTransformation)
    return layer.scaled(size, size, transformMode=Qt.TransformationMode.SmoothTransformation)


def icon_image(size: int = 1024) -> QImage:
    """The app icon: a glowing halo floating on a dusk gradient tile."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # An 824px rounded square centred in the canvas, as macOS icons are (drawn on Apple's 1024 grid)
    p.save()
    p.scale(size / 1024, size / 1024)
    tile = QRectF(100, 100, 824, 824)
    shape = QPainterPath()
    shape.addRoundedRect(tile, 185, 185)
    base = QLinearGradient(tile.topLeft(), tile.bottomRight())
    base.setColorAt(0, QColor("#a78bfa"))
    base.setColorAt(0.45, QColor("#6d28d9"))
    base.setColorAt(1, QColor("#172554"))
    p.fillPath(shape, base)
    p.restore()

    p.save()
    p.scale(size / 1024, size / 1024)
    p.setClipPath(shape)
    center = QPointF(512, 440)
    small = size <= 64  # (a Dock badge or a menu: bolder and rounder, so it still reads as a ring)
    ring = _ring_path(center, 300, 150, 78, 18) if small else _ring_path(center, 286, 116, 46, 14)
    p.resetTransform()
    p.drawImage(0, 0, _blurred(lambda q: q.fillRect(QRectF(0, 0, 1024, 1024), _pool(center)), size, 16))
    p.setOpacity(0.75)
    p.drawImage(0, 0, _blurred(lambda q: q.fillPath(ring, QColor("white")), size, 28))  # the glow
    p.setOpacity(1)
    p.scale(size / 1024, size / 1024)
    shine = QLinearGradient(QPointF(0, center.y() - 116), QPointF(0, center.y() + 116))
    shine.setColorAt(0, QColor("#e9e3ff"))  # the far side a touch lavender, the near side pure white
    shine.setColorAt(0.6, QColor("white"))
    p.fillPath(ring, shine)
    p.restore()
    p.end()
    return image


def _pool(center):
    """The light the halo casts on the tile below it."""
    light = QRadialGradient(QPointF(center.x(), center.y() + 150), 380)
    light.setColorAt(0, QColor(255, 255, 255, 60))
    light.setColorAt(1, QColor(255, 255, 255, 0))
    return light
