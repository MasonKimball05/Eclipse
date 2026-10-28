# Your cards (Settings → the first page): a little membership card, as Arc gave everyone: your name, a
# made-up title ("STATUESQUE TECHNICIAN"), a shape in a soft gradient, the day you started, and a stamp for
# each year since.
#
# Two of them: your Eclipse card, new, from the day you started using Eclipse, and yours to change; and,
# if you had one, your Arc card, kept just as it was. That one's copied into Eclipse's own settings the
# first time it's found, so it stays after Arc itself is gone.
#
# One title isn't in the shuffle and can't be typed in: ABSOLUTE ECLIPSE, for the person who made Eclipse.
# They're known by the email in this checkout's git settings (only its SHA-256 is written here), and their
# card keeps that title for good.
#
# Arc keeps it in ~/Library/Application Support/Arc/StorableMembershipCard.json, and your name and the day
# your account was made in StorableAuthState.json. Only those two fields are read from that file: never
# its sign-in tokens or your email address.

import hashlib
import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
                             QWidget)

from . import brand, storage
from .features import Feature
from .timefmt import portable

SETTING = "member_card"   # your Eclipse card
ARC_SETTING = "arc_card"  # your Arc card, kept
ARC_FILE = "arc-card.json"  # …and a copy of it on its own in the data folder, in case the settings are ever reset
ARC_DIR = Path.home() / "Library" / "Application Support" / "Arc"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)  # (Arc's dates count seconds from here)
SIZE = QSize(300, 450)  # the card, in points

# Arc numbers its palettes; these follow its order where it matters (3: the teal and sea-green one)
PALETTES = [
    {"name": "Lavender", "paper": "#fbf8ff", "ink": "#6d4aff", "stops": ["#8b6cf6", "#b6a4fb", "#e4dcfe"]},
    {"name": "Apricot", "paper": "#fff8f1", "ink": "#e8590c", "stops": ["#ff922b", "#ffc078", "#ffe8cc"]},
    {"name": "Sky", "paper": "#f4f9ff", "ink": "#1c64f2", "stops": ["#4dabf7", "#91cdfa", "#d0ebff"]},
    {"name": "Sea Glass", "paper": "#fffdf0", "ink": "#22a06b", "stops": ["#58bd96", "#79cbb6", "#a9dcd8"]},
    {"name": "Rose", "paper": "#fff6f8", "ink": "#d6336c", "stops": ["#f06595", "#f7a1bf", "#ffdeeb"]},
    {"name": "Honey", "paper": "#fffbea", "ink": "#c27a00", "stops": ["#fcc419", "#ffd96b", "#fff3bf"]},
    {"name": "Fern", "paper": "#13241d", "ink": "#8ce99a", "stops": ["#2f9e44", "#69db7c", "#1f3b2d"]},
    {"name": "Midnight", "paper": "#16151f", "ink": "#b197fc", "stops": ["#7048e8", "#9775fa", "#2b2547"]},
]
ECLIPSE_LOOK = {"palette": 6, "shape": "Shape3"}  # a new Eclipse card: Fern's deep green, and the arch
MAKER_TITLE = "ABSOLUTE ECLIPSE"
_MAKER = "6b00de7db9df2748062a655f71367dc5de9a7b3269b3f4935bd4e8fd3524dc2c"  # (SHA-256 of the maker's git email)
_maker = None
SHAPES = ["Shape1", "Shape2", "Shape3", "Shape4"]  # (Arc's names: a round one, a leaf, an arch, a petal)
STAMP = QColor("#4f46e5")

ADJECTIVES = ["STATUESQUE", "LUMINOUS", "CURIOUS", "VELVET", "RADIANT", "QUIET", "NIMBLE", "GALLANT", "COSMIC",
              "MERRY", "SERENE", "DAUNTLESS", "WHIMSICAL", "GOLDEN", "STEADFAST", "BREEZY", "ELEGANT", "PLUCKY",
              "SPLENDID", "TIDY", "WANDERING", "MIDNIGHT", "VIVID", "GENTLE", "BOLD", "SUNLIT", "CANDID", "DAPPER"]
NOUNS = ["TECHNICIAN", "CARTOGRAPHER", "ARCHIVIST", "VOYAGER", "GARDENER", "ALCHEMIST", "NAVIGATOR", "TINKERER",
         "LIBRARIAN", "ASTRONOMER", "BOTANIST", "CURATOR", "DRAFTSMAN", "PILOT", "SCRIBE", "SURVEYOR", "WEAVER",
         "COLLECTOR", "EXPLORER", "INVENTOR", "LOOKOUT", "MAPMAKER", "OBSERVER", "PATHFINDER", "SKIPPER"]


def random_title(rng=random) -> str:
    return f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)}"


def _git_email(config: Path) -> str:
    """user.email from a git settings file ("" if it hasn't one)."""
    try:
        text = config.read_text(errors="replace")
    except OSError:
        return ""
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]").split()[0].lower() if line.strip("[]").split() else ""
        elif section == "user" and "=" in line:
            key, _, value = line.partition("=")
            if key.strip().lower() == "email":
                return value.split("#")[0].split(";")[0].strip().strip('"').lower()
    return ""


def is_maker() -> bool:
    """Whether this is Eclipse's maker, by the email git signs this checkout's commits with (asked once)."""
    global _maker
    if _maker is None:
        # The checkout's, or (in a release app, which has no checkout) the Mac's own git settings: read from git's
        # files rather than asking git (twice), which held up the window opening
        email = ""
        home = Path.home()
        for config in (Path(__file__).resolve().parent.parent / ".git" / "config", home / ".gitconfig",
                       home / ".config" / "git" / "config"):
            email = _git_email(config)
            if email:
                break
        _maker = bool(email) and hashlib.sha256(email.encode()).hexdigest() == _MAKER
    return _maker


def allowed_title(title: str) -> bool:
    """A title anyone may have: everything but the maker's own."""
    return " ".join(title.upper().split()) != MAKER_TITLE or is_maker()


def _first_name() -> str:
    """Your first name, from your account's full name (a Mac's, or Linux's; Windows only gives the account's)."""
    full = ""
    try:
        import pwd  # (Unix only)
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (ImportError, KeyError, OSError, AttributeError):
        pass
    account = os.environ.get("USER") or os.environ.get("USERNAME") or "You"  # (USERNAME: Windows's)
    return (full.split() or [account.capitalize()])[0]


def started_with_eclipse() -> str:
    """The day you started using Eclipse: when its data folder was made (today, if that can't be told)."""
    from . import storage
    try:
        made = storage.data_dir().stat().st_birthtime
        return datetime.fromtimestamp(made).date().isoformat()
    except (OSError, AttributeError):
        return date.today().isoformat()


def new_card(rng=random, name=None, since=None) -> dict:
    """A fresh card: a new title, colours and shape."""
    return {"name": name or _first_name(), "title": random_title(rng), "palette": rng.randrange(len(PALETTES)),
            "shape": rng.choice(SHAPES), "since": since or date.today().isoformat(), "origin": "",
            "blob": [rng.randrange(16) for _ in range(15)]}


def new_eclipse_card(name=None, since=None) -> dict:
    """Your Eclipse card as it starts: a new title, in Eclipse's own colours and shape (yours to change)."""
    return {**new_card(name=name, since=since), **ECLIPSE_LOOK, "look": 1}


def arc_card_for(settings: dict) -> dict | None:
    """Your Arc card, as kept here (copied from Arc the first time it's found), or None if you had none."""
    kept = settings.get(ARC_SETTING)
    if not (isinstance(kept, dict) and kept.get("title")):
        kept = storage.read_json(storage.data_dir() / ARC_FILE) or read_arc_card()
        if not (isinstance(kept, dict) and kept.get("title")):
            return None
        settings[ARC_SETTING] = kept
    if not (storage.data_dir() / ARC_FILE).exists():
        storage.write_json(storage.data_dir() / ARC_FILE, kept)
    return {**new_card(random.Random(0)), **kept}


def card_for(settings: dict) -> dict:
    """Your Eclipse card (made, and kept, the first time it's asked for): under the name you had in Arc, if
    you had one there, but otherwise new."""
    card = settings.get(SETTING)
    if not isinstance(card, dict):
        arc = settings.get(ARC_SETTING) if isinstance(settings.get(ARC_SETTING), dict) else read_arc_card()
        card = settings[SETTING] = new_eclipse_card(name=(arc or {}).get("name"), since=started_with_eclipse())
    elif "look" not in card:
        # (made by an early version, which gave it your Arc card's colours and shape: it gets its own)
        arc = settings.get(ARC_SETTING) if isinstance(settings.get(ARC_SETTING), dict) else {}
        if (card.get("palette"), card.get("shape")) == (arc.get("palette"), arc.get("shape")):
            card.update(ECLIPSE_LOOK)
        card["look"] = 1
    if is_maker():  # (the maker's card keeps its title for good)
        card.update(title=MAKER_TITLE, maker=True)
    elif card.get("maker") or not allowed_title(card.get("title", "")):
        card.update(title=random_title(), maker=False)
    return {**new_card(random.Random(0)), **card}


# --- Bringing it over from Arc -----------------------------------------------------------------------------

def read_arc_card(folder: Path | None = None) -> dict | None:
    """Your Arc card, as a card here, or None if there isn't one."""
    folder = folder or ARC_DIR
    try:
        arc = json.loads((folder / "StorableMembershipCard.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(arc, dict) or not arc.get("title"):
        return None
    card = {"title": str(arc["title"])[:60].upper(), "origin": "arc",
            "palette": int(arc.get("paletteNumber", 0)) % len(PALETTES),
            "shape": arc.get("shapeName") if arc.get("shapeName") in SHAPES else SHAPES[1],
            "blob": [int(n) for n in arc.get("backgroundBlobInfo", []) if isinstance(n, (int, float))][:15]}
    try:  # (just the name and the day the account was made: nothing else in that file is looked at)
        user = json.loads((folder / "StorableAuthState.json").read_text())["session"]["user"]
        if user.get("name"):
            card["name"] = str(user["name"])[:40]
        if isinstance(user.get("createdAt"), (int, float)):
            # (the day it was here, as Arc shows it, not in UTC)
            card["since"] = (APPLE_EPOCH + timedelta(seconds=user["createdAt"])).astimezone().date().isoformat()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return card


# --- Drawing it --------------------------------------------------------------------------------------------

def shape_path(name: str, r: QRectF) -> QPainterPath:
    """A card's shape, filling `r`."""
    def at(x, y):
        return QPointF(r.left() + x * r.width(), r.top() + y * r.height())
    path = QPainterPath()
    if name == "Shape1":  # a round blob
        path.addEllipse(r)
    elif name == "Shape3":  # an arch
        path.moveTo(at(0, 1))
        path.lineTo(at(0, 0.5))
        path.cubicTo(at(0, 0.2), at(0.22, 0), at(0.5, 0))
        path.cubicTo(at(0.78, 0), at(1, 0.2), at(1, 0.5))
        path.lineTo(at(1, 1))
        path.closeSubpath()
    elif name == "Shape4":  # a petal: round at the top left and bottom right, pointed at the others
        path.moveTo(at(0, 0.5))
        path.cubicTo(at(0, 0.18), at(0.18, 0), at(0.5, 0))
        path.lineTo(at(1, 0))
        path.lineTo(at(1, 0.5))
        path.cubicTo(at(1, 0.82), at(0.82, 1), at(0.5, 1))
        path.lineTo(at(0, 1))
        path.closeSubpath()
    else:  # Shape2, a leaf: pointed at the top right and bottom left
        path.moveTo(at(1, 0))
        path.lineTo(at(1, 0.4))
        path.cubicTo(at(1, 0.8), at(0.72, 1), at(0.3, 1))
        path.lineTo(at(0, 1))
        path.lineTo(at(0, 0.62))
        path.cubicTo(at(0, 0.24), at(0.28, 0), at(0.68, 0))
        path.closeSubpath()
    return path


def anniversaries(since: str, today: date | None = None) -> int:
    """Whole years since `since` (an ISO date)."""
    try:
        start = date.fromisoformat(since)
    except (TypeError, ValueError):
        return 0
    today = today or date.today()
    return max(today.year - start.year - ((today.month, today.day) < (start.month, start.day)), 0)


def _font(family, size, weight=QFont.Weight.Normal, spacing=0.0, fixed=False) -> QFont:
    font = QFont(family) if family else QFont()
    if fixed:
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def _stamp(p: QPainter, centre: QPointF, year: int, color: QColor, paper: QColor, maker: str):
    """An anniversary stamp: a ring with the year in it and the words round the edge."""
    p.save()
    p.setPen(QPen(color, 1.6))
    fill = QColor(paper)
    fill.setAlphaF(0.88)
    p.setBrush(fill)
    p.drawEllipse(centre, 25, 25)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(centre, 11, 11)
    p.setFont(_font("", 13, QFont.Weight.Bold))
    p.drawText(QRectF(centre.x() - 11, centre.y() - 11, 22, 22), Qt.AlignmentFlag.AlignCenter, str(year))
    text = f"{maker.upper()} · ANNIVERSARY · "
    p.setFont(_font("", 6, QFont.Weight.Bold, 0.4))
    step = 360 / len(text)
    for i, ch in enumerate(text):
        p.save()
        p.translate(centre)
        p.rotate(-90 + i * step)
        p.translate(0, -18)
        p.drawText(QRectF(-4, -4, 8, 8), Qt.AlignmentFlag.AlignCenter, ch)
        p.restore()
    p.restore()


def paint_card(p: QPainter, rect: QRectF, card: dict, today: date | None = None):
    """The whole card, filling `rect` (drawn at SIZE and scaled, so it looks the same at any size)."""
    palette = PALETTES[int(card.get("palette", 0)) % len(PALETTES)]
    paper, ink = QColor(palette["paper"]), QColor(palette["ink"])
    blob = (list(card.get("blob") or []) + [8] * 15)[:15]
    p.save()
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    p.translate(rect.topLeft())
    p.scale(rect.width() / SIZE.width(), rect.height() / SIZE.height())
    w, h = SIZE.width(), SIZE.height()
    outline = QPainterPath()
    outline.addRoundedRect(QRectF(0, 0, w, h), 14, 14)
    p.fillPath(outline, paper)
    p.setClipPath(outline)

    # The shape, in a soft glow of the palette (where its brightest part sits comes from the card's own numbers)
    art = QRectF(22, 22, w - 44, 218)
    centre = QPointF(art.left() + art.width() * (0.3 + blob[0] / 40), art.top() + art.height() * (0.4 + blob[1] / 50))
    glow = QRadialGradient(centre, art.width() * (0.75 + blob[2] / 60))
    stops = [QColor(c) for c in palette["stops"]]
    glow.setColorAt(0.0, stops[0])
    glow.setColorAt(0.5 + blob[3] / 80, stops[1])
    glow.setColorAt(1.0, stops[2])
    p.fillPath(shape_path(card.get("shape", "Shape2"), art), glow)

    # The name and the title
    p.setPen(ink)
    name = str(card.get("name", ""))[:24]
    size = 40
    p.setFont(_font("", size, QFont.Weight.Black))
    while size > 22 and p.fontMetrics().horizontalAdvance(name) > w - 44:  # (a long name gets smaller to fit)
        size -= 2
        p.setFont(_font("", size, QFont.Weight.Black))
    p.drawText(QRectF(22, art.bottom() + 16, w - 44, 48), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
    p.setFont(_font("Menlo", 12, QFont.Weight.Medium, 0.6, fixed=True))
    p.drawText(QRectF(22, art.bottom() + 60, w - 44, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               str(card.get("title", "")).upper()[:32])

    # A stamp for each year, overlapping like ones collected
    maker = "Arc" if card.get("origin") == "arc" else brand.NAME  # (whose card it is)
    years = min(anniversaries(card.get("since", ""), today), 8)
    stamp = QColor(STAMP) if paper.lightness() > 128 else QColor("#a5b4fc")  # (a lighter ink on a dark card)
    for i in range(years):
        color = QColor(stamp)
        color.setAlphaF(0.55 + 0.45 * (i + 1) / years)
        _stamp(p, QPointF(48 + i * 32, h - 82), i + 1, color, paper, maker)

    # Where it's from and when: a little ticket at the bottom
    label = ("ARC" if card.get("origin") == "arc" else  # (a card from Arc says so, as it did there)
             "MAKER" if card.get("maker") else "SINCE")
    try:
        when = date.fromisoformat(card.get("since", "")).strftime(portable("%b %-d, %Y"))
    except (TypeError, ValueError):
        when = ""
    p.setFont(_font("", 10, QFont.Weight.Bold, 0.3))
    label_w = p.fontMetrics().horizontalAdvance(label) + 14
    date_w = p.fontMetrics().horizontalAdvance(when) + 16
    ticket = QRectF(22, h - 40, label_w + 18 + date_w, 20)
    p.setPen(QPen(ink, 1.3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(ticket, 3, 3)
    p.drawText(QRectF(ticket.left(), ticket.top(), label_w, ticket.height()), Qt.AlignmentFlag.AlignCenter, label)
    hatch = QRectF(ticket.left() + label_w, ticket.top(), 18, ticket.height())
    p.drawLine(hatch.topLeft(), hatch.bottomLeft())
    p.drawLine(hatch.topRight(), hatch.bottomRight())
    p.save()
    p.setClipRect(hatch)
    p.setPen(QPen(ink, 0.8))
    for x in range(-20, 20, 4):
        p.drawLine(QPointF(hatch.left() + x, hatch.bottom()), QPointF(hatch.left() + x + 20, hatch.top()))
    p.restore()
    p.drawText(QRectF(hatch.right(), ticket.top(), date_w, ticket.height()), Qt.AlignmentFlag.AlignCenter, when)

    # The browser's name, set like a wordmark
    p.setFont(_font("Georgia", 15, QFont.Weight.DemiBold, 0.2))
    p.drawText(QRectF(w - 130, h - 44, 110, 26), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, maker)
    p.restore()


def card_image(card: dict, scale: float = 3.0) -> QImage:
    """The card as a picture (sharp at `scale`), with see-through corners."""
    image = QImage(round(SIZE.width() * scale), round(SIZE.height() * scale), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    paint_card(p, QRectF(image.rect()), card)
    p.end()
    image.setDevicePixelRatio(scale)
    return image


class CardView(QWidget):
    """The card on screen, with a soft shadow under it."""

    SCALE = 0.62  # (the card on the page: the size of one in your hand, not a poster)

    def __init__(self, card: dict, parent=None):
        super().__init__(parent)
        self.card = card
        self.setFixedSize(round(SIZE.width() * self.SCALE) + 24, round(SIZE.height() * self.SCALE) + 24)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_card(self, card):
        self.card = card
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(12, 10, SIZE.width() * self.SCALE, SIZE.height() * self.SCALE)
        for spread, alpha in ((10, 10), (6, 14), (3, 20)):  # (a soft shadow, a few rings deep)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(body.adjusted(-spread / 2, -spread / 2 + 4, spread / 2, spread / 2 + 4), 16, 16)
        paint_card(p, body, self.card)
        p.end()


def palette_icon(index: int, size: int = 22) -> "QIcon":
    """A palette as a little round swatch of its glow."""
    from PyQt6.QtGui import QIcon, QPixmap
    palette = PALETTES[index % len(PALETTES)]
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(palette["paper"]).darker(115), 2))
    glow = QRadialGradient(QPointF(size * 0.8, size * 0.9), size * 1.2)
    for at, color in zip((0.0, 0.55, 1.0), palette["stops"]):
        glow.setColorAt(at, QColor(color))
    p.setBrush(glow)
    p.drawEllipse(QRectF(2, 2, size * 2 - 4, size * 2 - 4))
    p.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def shape_icon(name: str, color: QColor, size: int = 22) -> "QIcon":
    from PyQt6.QtGui import QIcon, QPixmap
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(shape_path(name, QRectF(4, 4, size * 2 - 8, size * 2 - 8)), color)
    p.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


# --- The Settings page ------------------------------------------------------------------------------------

def name_page(self):
    """Settings lists this page under your card's name, as Arc did."""
    name = card_for(self.store.settings).get("name", "").strip()
    if "card" in self.page_index:
        self.nav.item(self.page_index["card"]).setText(name or "Your Cards")

def build_page(self):
    """The page itself (`self` is the Settings window)."""
    from PyQt6.QtWidgets import QApplication, QFileDialog
    card = card_for(self.store.settings)
    self.store.settings[SETTING] = dict(card)
    self.store.schedule_save()
    view = CardView(card)

    def save(**changes):
        card.update(changes)
        self.store.settings[SETTING] = dict(card)
        self.store.schedule_save()
        view.set_card(card)
        name_page(self)
        show_choices()

    name = QLineEdit(card["name"])
    name.setMaxLength(24)
    name.setFixedWidth(220)
    name.textEdited.connect(lambda text: save(name=text.strip()))
    title = QLineEdit(card["title"])
    title.setMaxLength(32)
    title.setFixedWidth(220)
    def edited_title(text):
        if allowed_title(text):
            save(title=text.upper())
        else:  # (that one's the maker's alone)
            title.setText(card["title"])
            self.browser.toast(f"“{MAKER_TITLE.title()}” belongs to {brand.NAME}'s maker")
    title.textEdited.connect(edited_title)
    shuffle = QPushButton("Shuffle")
    shuffle.setToolTip("A new title, made up")

    def new_title():
        title.setText(random_title())
        save(title=title.text())
    shuffle.clicked.connect(new_title)
    title_box = QWidget()
    tb = QHBoxLayout(title_box)
    tb.setContentsMargins(0, 0, 0, 0)
    tb.setSpacing(8)
    tb.addWidget(title)
    tb.addWidget(shuffle)
    if card.get("maker"):  # (yours alone, and for good)
        title.setReadOnly(True)
        shuffle.hide()

    def choices(items, icon_for, pick):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        buttons = []
        for value, tip in items:
            button = QToolButton()
            button.setObjectName("CardChoice")
            button.setCheckable(True)
            button.setIconSize(QSize(22, 22))
            button.setIcon(icon_for(value))
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: pick(v))
            button.value = value
            row.addWidget(button)
            buttons.append(button)
        return box, buttons
    colours, colour_buttons = choices([(i, p["name"]) for i, p in enumerate(PALETTES)], palette_icon,
                                      lambda i: save(palette=i))
    shape_names = {"Shape1": "Round", "Shape2": "Leaf", "Shape3": "Arch", "Shape4": "Petal"}
    shapes, shape_buttons = choices([(n, shape_names[n]) for n in SHAPES],
                                    lambda n: shape_icon(n, self.browser.theme.panel_text), lambda n: save(shape=n))
    since = QLabel()
    since.setObjectName("Hint")

    def show_choices():
        for button in colour_buttons:
            button.setChecked(button.value == card.get("palette"))
        for button in shape_buttons:
            button.setChecked(button.value == card.get("shape"))
        try:
            from datetime import date
            when = date.fromisoformat(card.get("since", "")).strftime(portable("%B %-d, %Y"))
        except ValueError:
            when = ""
        since.setText(f"Since {when}" if when else "")
    show_choices()

    def card_actions(get_card, filename):
        copy = QPushButton("Copy Image")
        copy.clicked.connect(lambda: (QApplication.clipboard().setImage(card_image(get_card())),
                                      self.browser.toast("Copied the card")))
        keep = QPushButton("Save Image…")

        def save_image():
            path, _ = QFileDialog.getSaveFileName(self, "Save Card", f"{filename()}.png", "PNG (*.png)")
            if path:
                card_image(get_card()).save(path, "PNG")
        keep.clicked.connect(save_image)
        column = QVBoxLayout()  # (beside the card, at its top)
        column.setSpacing(8)
        column.addWidget(copy)
        column.addWidget(keep)
        column.addStretch(1)
        return column

    def section(heading, view, actions, side):
        # The card with its buttons beside it, and its settings underneath, the page's full width: side by
        # side, a window of Settings' usual size had no room for them
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        label = QLabel(heading)
        label.setObjectName("InfoTitle")
        v.addWidget(label)
        h = QHBoxLayout()
        h.setSpacing(16)
        h.addWidget(view, 0, Qt.AlignmentFlag.AlignTop)
        h.addLayout(actions)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(side)
        return box

    rows = [self._row("Name", "The name on your card.", name),
            self._row("Title", f"Yours alone, as {brand.NAME}'s maker: nobody else can have it." if card.get("maker")
                      else "Made up, like Arc's. Shuffle for another, or write your own.", title_box),
            self._row("Colours", None, colours),
            self._row("Shape", None, shapes),
            self._row(f"Started with {brand.NAME}", "A stamp for every year since.", since)]
    page = QWidget()
    v = QVBoxLayout(page)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(28)
    v.addWidget(section(f"Your {brand.NAME} Card", view, card_actions(lambda: card, lambda: card.get("name") or "Card"),
                        self._stack(self._card(rows))))

    # Your Arc card, kept as it was (copied into Eclipse's settings the first time it's found)
    arc = arc_card_for(self.store.settings)
    if arc is not None:
        self.store.schedule_save()
        try:
            from datetime import date
            joined = date.fromisoformat(arc.get("since", "")).strftime(portable("%B %-d, %Y"))
        except ValueError:
            joined = "—"
        kept = [self._row("Kept as it was", f"Your card from Arc: {arc.get('name', '')}, {arc['title'].title()}. "
                                            f"It's saved with {brand.NAME}'s settings, so it stays when Arc is gone.", None),
                self._row("Joined Arc", None, QLabel(joined)),
                self._row("Years with Arc", None, QLabel(str(anniversaries(arc.get("since", "")))))]
        v.addWidget(section("Your Arc Card", CardView(arc),
                            card_actions(lambda: arc, lambda: f"{arc.get('name') or 'My'} Arc Card"),
                            self._stack(self._card(kept))))
    return page


class CardsFeature(Feature):
    """Your cards: the first page of Settings, under your name."""
    name = "member_card"

    def ready(self):
        # Your Arc card, copied out of Arc as soon as Eclipse starts (not only once you look at it): Arc's
        # data is going away
        settings = self.window.store.settings
        had = bool(settings.get(ARC_SETTING))
        if arc_card_for(settings) is not None and not had:
            self.window.store.schedule_save()

    def settings_pages(self):
        name = card_for(self.window.store.settings).get("name", "").strip()
        return [("card", "ph.identification-card", name or "Your Cards", build_page, "general")]


FEATURE = CardsFeature
