# The browser's name and icon.
#
# NAME is what you see: the Dock, the menu bar, window titles, messages. INTERNAL_NAME names the data folder
# (and the app to Qt). It was "PythonBrowser" until 27 Oct 2026; what had the old name (the data folder, the
# Keychain items, the bundle ID) is moved over the first time it's used, by storage.py and secure_store.py.

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QConicalGradient, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

NAME = "Eclipse"
INTERNAL_NAME = "Eclipse"  # the data folder's name
OLD_INTERNAL_NAME = "PythonBrowser"  # (before the rename: moved over when found)
ORGANIZATION = "MasonKimball05"  # (part of the data folder's path too)
SETTING = "app_icon"  # which look you picked in Settings → General


# The icon: a total eclipse. Each look is a few settings for icon_image (the variants sheet was drawn
# with these); LOOK picks the one the app uses.
LOOKS = {
    "original": dict(sky=("#1e1b4b", "#020617"), corona="#ffd27a"),
    "diamond": dict(sky=("#1e1b4b", "#020617"), corona="#ffd27a", diamond=48, offset=(-10, 8)),
    "crescent": dict(sky=("#1e1b4b", "#020617"), corona="#ffd27a", offset=(-20, 16), rim_px=6, rim="#fff3d6"),
    "white": dict(sky=("#18181b", "#000000"), corona="#ffffff", glow=0.85),
    "violet": dict(sky=("#1a1033", "#05030c"), corona="#a78bfa", rim="#efe9ff"),
    "cyan": dict(sky=("#082f49", "#020617"), corona="#5eead4", rim="#e6fffb"),
    "sunset": dict(sky=("#4c0519", "#14020a"), corona="#fb923c", rim="#ffedd5", moon="#12020a"),
    "aurora": dict(sky=("#111827", "#030712"), glow=0.95,
                   conic=(("#f472b6", 0), ("#a78bfa", 0.25), ("#38bdf8", 0.5), ("#34d399", 0.75), ("#f472b6", 1))),
    "daylight": dict(sky=("#bfdbfe", "#60a5fa"), corona="#ffffff", moon="#0f172a"),
    "rays": dict(sky=("#1e1b4b", "#020617"), corona="#ffd27a", rays=16, spread=1.5),
    "small": dict(sky=("#1e1b4b", "#020617"), corona="#ffd27a", radius=175, spread=2.0),
    "earthshine": dict(sky=("#1e1b4b", "#020617"), corona="#ffd27a", moon="#0b1026", earthshine="#3b4a7a",
                       offset=(-14, 10)),
}
LOOK = "original"  # the default
LABELS = {
    "original": "Gold", "diamond": "Diamond Ring", "crescent": "Crescent", "white": "White", "violet": "Violet",
    "cyan": "Cyan", "sunset": "Sunset", "aurora": "Aurora", "daylight": "Daylight", "rays": "Rays",
    "small": "Small Sun", "earthshine": "Earthshine",
}


def chosen_look(settings) -> str:
    look = settings.get(SETTING, LOOK)
    return look if look in LOOKS else LOOK


def saved_look() -> str:
    """The look picked in Settings, read from the session file (for building the app, which runs
    outside the browser)."""
    from PyQt6.QtCore import QCoreApplication

    from . import storage
    if not QCoreApplication.applicationName() or QCoreApplication.organizationName() != ORGANIZATION:
        QCoreApplication.setApplicationName(INTERNAL_NAME)  # (where storage finds the data folder)
        QCoreApplication.setOrganizationName(ORGANIZATION)
    session = storage.read_json(storage.session_path()) or {}
    return chosen_look(session.get("settings", {}))


def apply_icon(app, settings):
    """The Dock icon, while the browser is running (the built app's own icon changes when it's rebuilt)."""
    from PyQt6.QtGui import QIcon, QPixmap
    app.setWindowIcon(QIcon(QPixmap.fromImage(icon_image(512, chosen_look(settings)))))


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


def _faded(color, alpha):
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def icon_image(size: int = 1024, look: str | None = None, tile: bool = True) -> QImage:
    """The app icon: the moon in front of the sun, the corona glowing round it, on a night-sky tile.
    tile=False: just the eclipse, on nothing (for drawing it on a page)."""
    o = LOOKS[look or LOOK]
    corona, radius, spread = o.get("corona", "#ffffff"), o.get("radius", 230), o.get("spread", 1.75)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.scale(size / 1024, size / 1024)  # drawn on Apple's 1024 grid

    # An 824px rounded square centred in the canvas, as macOS icons are
    card = QRectF(100, 100, 824, 824)
    shape = QPainterPath()
    shape.addRoundedRect(card, 185, 185)
    sky = QLinearGradient(card.topLeft(), card.bottomRight())
    sky.setColorAt(0, QColor(o["sky"][0]))
    sky.setColorAt(1, QColor(o["sky"][1]))
    if tile:
        p.fillPath(shape, sky)
        p.setClipPath(shape)
    small = size <= 64  # (a Dock badge or a menu: a touch bigger, with a bolder rim, so it still reads)
    r = radius * (1.08 if small else 1.0)
    sun = QPointF(512, 512)
    moon = QPointF(512 + o.get("offset", (0, 0))[0], 512 + o.get("offset", (0, 0))[1])

    def glow(q):
        if "conic" in o:
            ring = QConicalGradient(sun, 90)
            for color, at in o["conic"]:
                ring.setColorAt(at, QColor(color))
            q.setPen(QPen(ring, r * 0.55))
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawEllipse(sun, r * 1.12, r * 1.12)
            return
        light = QRadialGradient(sun, r * spread)
        light.setColorAt(0.92 / spread, QColor(corona))
        light.setColorAt(1, _faded(corona, 0))
        q.setBrush(light)
        q.setPen(Qt.PenStyle.NoPen)
        q.drawEllipse(sun, r * spread, r * spread)

    p.resetTransform()
    p.setOpacity(o.get("glow", 1.0))
    p.drawImage(0, 0, _blurred(glow, size, 14 if small else 20))
    p.setOpacity(1)
    if o.get("rays"):
        def rays(q):
            q.translate(sun)
            for i in range(o["rays"]):
                q.save()
                q.rotate(i * 360 / o["rays"] + (9 if i % 2 else 0))
                length = r * (2.05 if i % 3 == 0 else 1.6)
                fade = QLinearGradient(QPointF(r, 0), QPointF(length, 0))
                fade.setColorAt(0, _faded(corona, 230))
                fade.setColorAt(1, _faded(corona, 0))
                ray = QPainterPath()
                ray.moveTo(r * 0.95, -r * 0.11)
                ray.lineTo(length, 0)
                ray.lineTo(r * 0.95, r * 0.11)
                ray.closeSubpath()
                q.fillPath(ray, fade)
                q.restore()
        p.drawImage(0, 0, _blurred(rays, size, 4 if small else 6))
    p.scale(size / 1024, size / 1024)

    # The sun's bright edge where the moon doesn't quite cover it, then the moon
    rim = o.get("rim_px", 12) * (2 if small else 1)
    edge, cover = QPainterPath(), QPainterPath()
    edge.addEllipse(sun, r + rim, r + rim)
    cover.addEllipse(moon, r, r)
    p.fillPath(edge.subtracted(cover), QColor(o.get("rim", "#ffffff")))
    if "earthshine" in o:
        face = QRadialGradient(QPointF(moon.x() - r * 0.35, moon.y() - r * 0.4), r * 1.4)
        face.setColorAt(0, QColor(o["earthshine"]))
        face.setColorAt(1, QColor(o.get("moon", "#05060f")))
        p.setBrush(face)
    else:
        p.setBrush(QColor(o.get("moon", "#05060f")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(moon, r, r)

    if "diamond" in o:  # the "diamond ring": the last bead of sunlight flaring at the edge
        angle = math.radians(o["diamond"])
        bead = QPointF(sun.x() + math.cos(angle) * (r + 4), sun.y() - math.sin(angle) * (r + 4))

        def flare(q):
            light = QRadialGradient(bead, 130)
            light.setColorAt(0, QColor("white"))
            light.setColorAt(0.25, QColor(255, 250, 230, 200))
            light.setColorAt(1, QColor(255, 255, 255, 0))
            q.setBrush(light)
            q.setPen(Qt.PenStyle.NoPen)
            q.drawEllipse(bead, 130, 130)
        p.resetTransform()
        p.drawImage(0, 0, _blurred(flare, size, 10))
        p.scale(size / 1024, size / 1024)
        dot = 44 if small else 26
        p.setBrush(QColor("white"))
        p.drawEllipse(bead, dot, dot)
        p.setPen(QPen(QColor(255, 255, 255, 220), 10 if small else 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(bead.x() - 110, bead.y()), QPointF(bead.x() + 110, bead.y()))
        p.drawLine(QPointF(bead.x(), bead.y() - 110), QPointF(bead.x(), bead.y() + 110))
    p.end()
    return image
