# Colours, per-Space theming and the app-wide stylesheet.
#
# Like Arc, every Space has an accent colour; the whole window chrome is tinted
# with a soft gradient of it. Light/dark follows the macOS appearance setting.

import random
from dataclasses import dataclass
from functools import lru_cache

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QIcon, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QWidget

PALETTE = [
    ("Lavender", "#8b7cf6"),
    ("Ocean", "#3b82f6"),
    ("Mint", "#14b8a6"),
    ("Rose", "#f43f5e"),
    ("Peach", "#f97316"),
    ("Sunflower", "#eab308"),
    ("Grape", "#a855f7"),
    ("Slate", "#64748b"),
]


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(a.redF() * (1 - t) + b.redF() * t,
                           a.greenF() * (1 - t) + b.greenF() * t,
                           a.blueF() * (1 - t) + b.blueF() * t)


def rgba(c: QColor, alpha: float | None = None) -> str:
    a = c.alphaF() if alpha is None else alpha
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(a * 255)})"


def system_is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


DEFAULT_SPACE_THEME = {"colors": [], "intensity": 0.5, "mode": "auto", "grain": False}


@dataclass(frozen=True)
class Theme:
    accent: QColor
    stops: tuple  # gradient colours, top-left to bottom-right
    text: QColor
    dark: bool
    grain: bool = False

    @property
    def top(self) -> QColor:
        return self.stops[0]

    @property
    def bottom(self) -> QColor:
        return self.stops[-1]

    @property
    def panel(self) -> QColor:
        # Floating surfaces: command bar, find bar, toasts
        return QColor("#242428") if self.dark else QColor("#ffffff")

    @property
    def panel_text(self) -> QColor:
        return QColor("#f2f2f4") if self.dark else QColor("#1d1d22")

    def gradient(self, width: float, height: float, offset_x: float = 0, offset_y: float = 0) -> QLinearGradient:
        # The window background (a diagonal gradient), in a child widget's coordinates
        g = QLinearGradient(-offset_x, -offset_y, width * 0.6 - offset_x, height - offset_y)
        for i, color in enumerate(self.stops):
            g.setColorAt(i / max(len(self.stops) - 1, 1), color)
        return g

    def qss_gradient(self) -> str:
        stops = ", ".join(f"stop:{i / max(len(self.stops) - 1, 1):.2f} {rgba(c)}" for i, c in enumerate(self.stops))
        return f"qlineargradient(x1:0, y1:0, x2:0.6, y2:1, {stops})"


def make_theme(color: str, dark: bool | None = None, colors=None, intensity=0.5, mode="auto",
               grain=False) -> Theme:
    """Arc-style Space theme: 1-3 colours, how strongly they tint the window, and light/dark."""
    if dark is None:
        dark = system_is_dark() if mode == "auto" else mode == "dark"
    colors = [QColor(c) for c in (colors or [color])]
    if dark:
        amount = 0.95 - 0.5 * intensity  # how much of the near-black base shows through
        bases = (QColor("#151517"), QColor("#0d0d0f"))
    else:
        amount = 0.9 - 0.65 * intensity  # how much white shows through
        bases = (QColor("#ffffff"), QColor("#ffffff"))
    if len(colors) == 1:
        # One colour: a gentle two-tone of it, lighter at the top
        lift = 0.12
        stops = (mix(colors[0], bases[0], min(amount + lift, 1)), mix(colors[0], bases[1], max(amount - lift, 0)))
    else:
        stops = tuple(mix(c, bases[0], amount) for c in colors)
    text = QColor("#f2f2f4") if dark else QColor("#1d1d22")
    return Theme(colors[0], stops, text, dark, grain)


def theme_for_space(space) -> Theme:
    t = {**DEFAULT_SPACE_THEME, **space.theme}
    return make_theme(space.color, colors=t["colors"] or [space.color], intensity=t["intensity"],
                      mode=t["mode"], grain=t["grain"])


_noise = None


def noise_pixmap() -> QPixmap:
    # A small tile of random speckles, tiled over the window for Arc's "grain" look
    global _noise
    if _noise is None:
        size = 128
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        rng = random.Random(7)
        for y in range(size):
            for x in range(size):
                v = rng.randint(0, 255)
                image.setPixelColor(x, y, QColor(v, v, v, rng.randint(6, 22)))
        _noise = QPixmap.fromImage(image)
    return _noise


class PlainMenu(QMenu):
    """A popup menu that shows "&" as itself: titles and site names often have one ("Ads & Trackers",
    "Q&A"), and Qt would otherwise eat it as a keyboard-accelerator marker."""

    @staticmethod
    def _plain(args):
        return [a.replace("&", "&&") if isinstance(a, str) else a for a in args]

    def addAction(self, *args, **kwargs):
        return super().addAction(*self._plain(args), **kwargs)

    def addMenu(self, *args, **kwargs):
        return super().addMenu(*self._plain(args), **kwargs)

    def addSection(self, *args, **kwargs):
        return super().addSection(*self._plain(args), **kwargs)

    def setTitle(self, title):
        super().setTitle(title.replace("&", "&&"))


class ThemedBackground(QWidget):
    """The window's root widget: paints the Space gradient (and grain) behind everything."""

    def __init__(self, browser):
        super().__init__()
        self.browser = browser

    def paintEvent(self, event):
        theme = self.browser.theme
        p = QPainter(self)
        p.fillRect(self.rect(), theme.gradient(self.width(), self.height()))
        if theme.grain:
            p.drawTiledPixmap(self.rect(), noise_pixmap())


def quiet_scrollbars(area):
    """Hide a scroll area's scrollbars until it scrolls (or the pointer is on them), then fade them
    away again a moment later."""
    for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
        bar.setProperty("quiet", True)
        timer = QTimer(bar, singleShot=True, interval=900)

        def show(_value=None, b=bar, t=timer):
            if b.property("active") is not True:
                b.setProperty("active", True)
                b.style().unpolish(b)
                b.style().polish(b)
            t.start()

        def hide(b=bar):
            b.setProperty("active", False)
            b.style().unpolish(b)
            b.style().polish(b)
        bar.valueChanged.connect(show)
        timer.timeout.connect(hide)
    return area


_legible: dict = {}


def legible(icon: QIcon, theme: "Theme") -> QIcon:
    """A site icon that shows up against this theme. Some sites serve an all-white icon in dark mode
    (GitHub, Notion…) that vanishes on a light sidebar, or an all-black one that vanishes on a dark
    one: those get a small contrasting tile behind them."""
    if icon is None or icon.isNull():
        return icon
    key = (icon.cacheKey(), theme.dark)
    if key in _legible:
        return _legible[key]
    image = icon.pixmap(32, 32).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    total = weight = 0.0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            c = image.pixelColor(x, y)
            if c.alpha() > 40:
                a = c.alphaF()
                total += a * (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF())
                weight += a
    result = icon
    if weight > 4:
        lum = total / weight
        if (not theme.dark and lum > 0.86) or (theme.dark and lum < 0.12):
            size = 64
            out = QPixmap(size, size)
            out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#2c2c31") if not theme.dark else QColor("#e9e9ec"))
            p.drawRoundedRect(0, 0, size, size, 14, 14)
            inner = icon.pixmap(44, 44)
            p.drawPixmap((size - 44) // 2, (size - 44) // 2, 44, 44, inner)
            p.end()
            result = QIcon(out)
    _legible[key] = result
    return result


def letter_icon(text: str, color: QColor, size: int = 32) -> QIcon:
    # Fallback favicon: the first letter of the site on a rounded square
    return _letter_icon((text[:1] or "?").upper(), QColor(color).rgba(), size)


@lru_cache(maxsize=512)
def _letter_icon(letter: str, rgba: int, size: int) -> QIcon:
    color = QColor.fromRgba(rgba)
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.56))
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    return QIcon(pm)


@lru_cache(maxsize=256)
def dot_icon(color: str, size: int = 14) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, size - 2, size - 2))
    p.end()
    return QIcon(pm)


def switch_images(accent: QColor, dark: bool) -> tuple[str, str]:
    """macOS-style switch pictures (off, on) for every checkbox, written once per colour into the temp
    folder with @2x copies for Retina. Paths use forward slashes, as stylesheets want."""
    import tempfile
    from pathlib import Path
    folder = Path(tempfile.gettempdir()) / "pythonbrowser-ui"
    folder.mkdir(exist_ok=True)
    names = []
    for on in (False, True):
        name = f"switch-{'on' if on else 'off'}-{accent.name()[1:]}-{'d' if dark else 'l'}"
        for scale, suffix in ((1, ""), (2, "@2x")):
            path = folder / f"{name}{suffix}.png"
            if not path.exists():
                w, h = 30 * scale, 18 * scale
                image = QImage(w, h, QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent)
                p = QPainter(image)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(Qt.PenStyle.NoPen)
                track = QColor(accent) if on else (QColor(255, 255, 255, 46) if dark else QColor(0, 0, 0, 38))
                p.setBrush(track)
                p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
                knob = h - 4 * scale
                x = w - knob - 2 * scale if on else 2 * scale
                p.setBrush(QColor(0, 0, 0, 40))
                p.drawEllipse(QRectF(x, 2.5 * scale, knob, knob))  # a hint of shadow under the knob
                p.setBrush(QColor("white"))
                p.drawEllipse(QRectF(x, 2 * scale, knob, knob))
                p.end()
                image.save(str(path))
        names.append((folder / f"{name}.png").as_posix())
    return names[0], names[1]


def hover_color(t: Theme) -> QColor:
    """The sidebar's hover highlight (rows paint it themselves, so it can fade)."""
    color = QColor("white")
    color.setAlphaF(0.07 if t.dark else 0.32)
    return color


def stylesheet(t: Theme) -> str:
    # A grey Space would make "on" look like "off": those switches (and sliders) use the system blue
    control = t.accent if t.accent.hsvSaturationF() > 0.25 else QColor("#0a84ff")
    switch_off, switch_on = switch_images(control, t.dark)
    text = rgba(t.text)
    dim = rgba(t.text, 0.55)
    faint = rgba(t.text, 0.10)
    white = QColor("white")
    hover = rgba(white, 0.07) if t.dark else rgba(white, 0.32)
    selected = rgba(white, 0.14) if t.dark else rgba(white, 0.82)
    tile = rgba(white, 0.05) if t.dark else rgba(white, 0.28)
    content_bg = "#1b1b1e" if t.dark else "#ffffff"
    panel = rgba(t.panel)
    panel_text = rgba(t.panel_text)
    panel_dim = rgba(t.panel_text, 0.5)
    panel_line = rgba(t.panel_text, 0.10)
    return f"""
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 30px; height: 18px; image: url({switch_off}); }}
    QCheckBox::indicator:checked {{ image: url({switch_on}); }}
    QSlider::groove:horizontal {{ height: 4px; background: {rgba(t.panel_text, 0.15)}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {rgba(control)}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 18px; height: 18px; margin: -7px 0; border-radius: 9px; background: white;
        border: 1px solid rgba(0, 0, 0, 0.18);
    }}
    #Sidebar QLabel {{ color: {text}; background: transparent; }}
    #Sidebar QLabel[dim="true"] {{ color: {dim}; font-size: 11px; }}
    #Sidebar QScrollArea, #SidebarScrollBody {{ background: transparent; border: none; }}
    /* Scrollbars that stay out of sight until you scroll or point at them, like macOS's own */
    QScrollBar[quiet="true"]:vertical {{ width: 8px; background: transparent; margin: 2px 1px; }}
    QScrollBar[quiet="true"]:horizontal {{ height: 8px; background: transparent; margin: 1px 2px; }}
    QScrollBar[quiet="true"]::handle {{ background: transparent; border-radius: 3px; min-height: 28px; min-width: 28px; }}
    QScrollBar[quiet="true"][active="true"]::handle, QScrollBar[quiet="true"]:hover::handle {{
        background: {rgba(t.text, 0.22)};
    }}
    *[panel="true"] QScrollBar[quiet="true"][active="true"]::handle,
    *[panel="true"] QScrollBar[quiet="true"]:hover::handle {{ background: {rgba(t.panel_text, 0.22)}; }}
    QScrollBar[quiet="true"]::add-line, QScrollBar[quiet="true"]::sub-line,
    QScrollBar[quiet="true"]::add-page, QScrollBar[quiet="true"]::sub-page {{
        width: 0; height: 0; background: none;
    }}

    QToolButton#IconButton {{ background: transparent; border: none; border-radius: 6px; padding: 3px; }}
    QToolButton#IconButton:hover {{ background: {hover}; }}
    QToolButton#IconButton:pressed {{ background: {faint}; }}
    QToolButton#IconButton:disabled {{ background: transparent; }}

    QPushButton#UrlPill {{
        background: {tile}; color: {text}; border: none; border-radius: 10px;
        padding: 8px 10px; text-align: left; font-size: 13px;
    }}
    QPushButton#UrlPill:hover {{ background: {hover}; }}

    QFrame#TabRow {{ background: transparent; border-radius: 9px; }}
    QFrame#TabRow[selected="true"] {{ background: {selected}; }}
    QFrame#TabRow QLabel {{ font-size: 13px; }}
    QFrame#TabRow QLabel[unloaded="true"] {{ color: {rgba(t.text, 0.72)}; }}
    QFrame#TabRow QLabel[fresh="true"] {{ font-weight: 700; }}

    QToolButton#FavoriteTile {{ background: {tile}; border: none; border-radius: 10px; }}
    QToolButton#FavoriteTile:hover {{ background: {hover}; }}
    QToolButton#FavoriteTile[selected="true"] {{ background: {selected}; }}

    QToolButton#SpaceDot {{
        background: transparent; border: none; border-radius: 6px; padding: 3px; font-size: 14px;
    }}
    QToolButton#SpaceDot:hover {{ background: {hover}; }}
    QToolButton#SpaceDot[selected="true"] {{ background: {selected}; }}

    QToolButton#IconChoice {{
        background: transparent; border: 2px solid transparent; border-radius: 14px; padding: 2px;
    }}
    QToolButton#IconChoice:hover {{ background: {hover}; }}
    QToolButton#IconChoice:checked {{ border-color: {rgba(t.accent)}; }}

    QFrame#DropLine {{ background: {text}; border-radius: 1px; }}
    QFrame#DropBox {{ background: transparent; border: 2px solid {text}; border-radius: 9px; }}
    QFrame#Divider {{ background: {faint}; }}
    QPushButton#ClearButton {{
        background: transparent; border: none; color: {dim}; font-size: 11px;
        padding: 2px 6px; border-radius: 5px;
    }}
    QPushButton#ClearButton:hover {{ background: {hover}; color: {text}; }}
    QPushButton#SpaceTitle {{
        background: transparent; border: none; border-radius: 7px; color: {text};
        font-size: 13px; font-weight: 600; padding: 4px 8px 4px 6px; text-align: left;
    }}
    QPushButton#SpaceTitle:hover {{ background: {hover}; }}
    QLabel#ProfileBadge {{
        background: {faint}; color: {dim}; border-radius: 6px; padding: 1px 7px; font-size: 11px; font-weight: 600;
    }}

    QSplitter::handle {{ background: transparent; }}
    QSplitter::handle:hover, QSplitter::handle:pressed {{
        background: {faint}; border-radius: 3px; margin: 12px 2px;
    }}
    #ContentFrame {{ background: {content_bg}; border-radius: 10px; }}
    #EmptyState QLabel {{ color: {rgba(QColor("#ffffff") if t.dark else QColor("#000000"), 0.45)}; font-size: 13px; }}
    #EmptyState QLabel#EmptyTitle {{ font-size: 22px; font-weight: 600; }}
    #EmptyState QLabel#EmptyKey {{
        background: {rgba(QColor("#ffffff") if t.dark else QColor("#000000"), 0.06)}; border-radius: 6px;
        padding: 3px 0; font-size: 13px; font-weight: 600;
    }}
    QLabel#PanelEmptyTitle {{ color: {panel_text}; font-size: 15px; font-weight: 600; }}
    #ProgressBar {{ background: {rgba(t.accent)}; border-radius: 1px; }}

    #LinkLabel, #Toast {{
        background: {panel}; color: {panel_text}; border: 1px solid {panel_line};
        border-radius: 7px; padding: 4px 9px; font-size: 12px;
    }}
    #Toast {{ padding: 0; border-radius: 10px; }}
    #ToastText {{ color: {panel_text}; font-size: 13px; background: transparent; border: none; padding: 1px 0; }}
    QPushButton#ToastAction {{
        background: {rgba(t.accent, 0.18)}; color: {panel_text}; border: none; border-radius: 6px;
        padding: 3px 10px; font-size: 12px; font-weight: 600;
    }}
    QPushButton#ToastAction:hover {{ background: {rgba(t.accent, 0.32)}; }}

    #FindBar {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 10px; }}
    #NotificationBanner {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 12px; }}
    #BannerTitle {{ color: {panel_text}; font-weight: 600; font-size: 13px; }}
    #BannerBody {{ color: {panel_text}; font-size: 12px; }}
    #BannerSite {{ color: {rgba(t.accent)}; font-size: 11px; }}
    QToolButton#BannerClose {{ background: transparent; border: none; padding: 2px; }}
    QLabel#UnreadBadge {{
        background: {rgba(t.accent)}; color: white; border-radius: 8px; padding: 0 5px;
        font-size: 10px; font-weight: 700; min-width: 6px;
    }}
    #PermissionPrompt {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 12px; }}
    #PermissionPrompt QLabel {{ color: {panel_text}; font-size: 13px; }}
    #PermissionPrompt QPushButton {{
        padding: 5px 12px; border-radius: 7px; border: 1px solid {panel_line}; background: transparent;
        color: {panel_text};
    }}
    #PermissionPrompt QPushButton:hover {{ background: {rgba(t.accent, 0.12)}; }}
    QPushButton#PermissionAllow {{ background: {rgba(t.accent)}; color: white; border: none; border-radius: 7px; }}
    QPushButton#PermissionClose {{ background: transparent; border: none; padding: 4px; }}
    #FindBar QLineEdit {{
        background: transparent; border: none; color: {panel_text}; font-size: 13px; padding: 4px;
    }}
    #FindBar QLabel {{ color: {panel_dim}; font-size: 12px; }}

    QTextBrowser#Answer {{
        background: transparent; border: none; color: {panel_text}; font-size: 13px;
        selection-background-color: {rgba(t.accent, 0.45)};
    }}
    #CommandPanel QTextBrowser#Answer {{ font-size: 14px; }}

    #PreviewCard {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 12px; }}
    #PreviewHost {{ color: {rgba(t.accent)}; font-size: 11px; font-weight: 600; }}
    #PreviewTitle {{ color: {panel_text}; font-size: 14px; font-weight: 600; }}
    #PreviewSummary {{ color: {panel_dim}; font-size: 13px; }}

    #CommandPanel {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 14px; }}
    #CommandInput {{
        background: transparent; border: none; color: {panel_text}; font-size: 18px;
        padding: 14px 16px; selection-background-color: {rgba(t.accent, 0.45)};
    }}
    #SitePill {{
        background: {rgba(t.accent, 0.9)}; color: white; font-size: 14px; font-weight: 600;
        border-radius: 8px; padding: 3px 9px;
    }}
    #CommandDivider {{ background: {panel_line}; }}
    #CommandList {{ background: transparent; border: none; outline: 0; padding: 6px; }}

    QToolButton#ShieldButton, QToolButton#ExtButton {{
        background: transparent; border: none; border-radius: 6px; padding: 1px 5px;
        color: {dim}; font-size: 11px;
    }}
    QToolButton#ShieldButton:hover, QToolButton#ExtButton:hover {{ background: {hover}; color: {text}; }}
    QToolButton#DevChip {{
        background: {rgba(t.accent, 0.22)}; color: {text}; border: none; border-radius: 6px;
        padding: 1px 6px; font-size: 10px; font-weight: 700;
    }}
    QToolButton#DevChip[errors="true"] {{ background: rgba(229, 72, 77, 0.85); color: white; }}
    QToolButton#DevChip[open="true"] {{ border: 1px solid {rgba(t.accent, 0.9)}; }}
    #CrashedCard {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 14px; }}
    #CrashedTitle {{ color: {panel_text}; font-size: 15px; font-weight: 600; }}
    #CrashedDetail {{ color: {panel_text}; font-size: 12px; }}
    QPushButton#CrashReload {{
        background: {rgba(t.accent)}; color: white; border: none; border-radius: 8px;
        padding: 6px 22px; font-size: 13px; font-weight: 600; margin-top: 6px;
    }}
    #Reader {{ background: {panel}; }}
    #ReaderBar {{ background: {panel}; border-bottom: 1px solid {panel_line}; }}
    #ReaderLabel {{ color: {panel_text}; font-size: 12px; font-weight: 600; }}
    QToolButton#ReaderTool {{ background: transparent; border: none; border-radius: 6px; padding: 3px 5px; }}
    QToolButton#ReaderTool:hover {{ background: {rgba(t.accent, 0.15)}; }}
    QPushButton#ReaderButton {{
        background: transparent; color: {panel_text}; border: 1px solid {panel_line}; border-radius: 6px;
        padding: 2px 9px; font-size: 11px;
    }}
    QPushButton#ReaderButton:hover {{ background: {rgba(t.accent, 0.15)}; }}
    #NowPlaying {{ background: {hover}; border-radius: 10px; }}
    #NowPlaying:hover {{ background: {rgba(t.accent, 0.22)}; }}
    #NowPlayingTitle {{ color: {text}; font-size: 12px; font-weight: 600; }}
    #NowPlayingSub {{ color: {dim}; font-size: 11px; }}
    QPushButton#UpNextJoin {{
        background: {rgba(t.accent)}; color: white; border: none; border-radius: 7px; padding: 3px 10px;
        font-size: 11px; font-weight: 600;
    }}
    QPushButton#UpNextJoin:hover {{ background: {rgba(t.accent.lighter(115))}; }}
    QToolButton#NowPlayingButton {{ background: transparent; border: none; border-radius: 6px; padding: 3px; }}
    QToolButton#NowPlayingButton:hover {{ background: {hover}; }}
    #DevConsole {{ background: {panel}; border-top: 1px solid {panel_line}; }}
    #DevTitle {{ color: {panel_text}; font-weight: 600; font-size: 12px; }}
    QPushButton#DevButton {{
        background: transparent; color: {panel_text}; border: 1px solid {panel_line}; border-radius: 6px;
        padding: 2px 8px; font-size: 11px;
    }}
    QPushButton#DevButton:hover {{ background: {rgba(t.accent, 0.15)}; }}
    QTextEdit#DevLog {{
        background: transparent; border: none; color: {panel_text};
        font-family: Menlo, monospace; font-size: 11px;
    }}
    QLineEdit#DevInput {{
        background: {rgba(t.accent, 0.08)}; border: 1px solid {panel_line}; border-radius: 6px; color: {panel_text};
        font-family: Menlo, monospace; font-size: 11px; padding: 4px 6px;
    }}

    *[panel="true"] {{ background: {panel}; }}
    *[panel="true"] QLabel, *[panel="true"] QCheckBox {{
        color: {panel_text}; font-size: 13px;
    }}
    *[panel="true"] QLabel#PanelTitle {{ font-size: 15px; font-weight: 600; }}
    *[panel="true"] QLabel#InfoTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#Hint, QLabel#DownloadStatus {{ color: {panel_dim}; font-size: 12px; }}
    QLabel#DownloadTitle {{ font-weight: 500; }}
    *[panel="true"] QLineEdit, *[panel="true"] QComboBox {{
        background: {rgba(t.panel_text, 0.06)}; color: {panel_text}; border: 1px solid {panel_line};
        border-radius: 7px; padding: 6px 9px; font-size: 13px; selection-background-color: {rgba(t.accent, 0.45)};
    }}
    *[panel="true"] QLineEdit:focus {{ border: 1px solid {rgba(t.accent)}; }}
    *[panel="true"] QPushButton {{
        background: {rgba(t.panel_text, 0.08)}; color: {panel_text}; border: none; border-radius: 7px;
        padding: 7px 14px; font-size: 13px;
    }}
    *[panel="true"] QPushButton:hover {{ background: {rgba(t.panel_text, 0.14)}; }}
    *[panel="true"] QPushButton:disabled {{ color: {panel_dim}; }}
    *[panel="true"] QPushButton#Primary {{
        background: {rgba(t.accent)}; color: white; font-weight: 600; padding: 8px 20px;
    }}
    *[panel="true"] QPushButton#Primary:hover {{ background: {rgba(t.accent, 0.85)}; }}
    *[panel="true"] QPushButton#Segment:checked {{
        background: {rgba(t.accent)}; color: white;
    }}
    *[panel="true"] QPushButton#Link {{ background: transparent; color: {panel_dim}; padding: 2px 6px; }}
    *[panel="true"] QPushButton#FolderButton {{ text-align: left; }}
    *[panel="true"] QFrame#OptionsCard {{ background: {rgba(t.panel_text, 0.04)}; border: 1px solid {panel_line}; border-radius: 12px; }}
    *[panel="true"] QFrame#DownloadRow {{ background: {rgba(t.panel_text, 0.04)}; border-radius: 10px; }}
    *[panel="true"] QScrollArea, #DownloadList {{ background: transparent; }}
    *[panel="true"] QProgressBar {{ background: {panel_line}; border: none; border-radius: 2px; }}
    *[panel="true"] QProgressBar::chunk {{ background: {rgba(t.accent)}; border-radius: 2px; }}
    *[panel="true"] QToolButton#IconButton:hover {{ background: {rgba(t.panel_text, 0.08)}; }}

    QLineEdit#SettingsSearch {{
        background: {rgba(t.panel_text, 0.06)}; border: 1px solid {panel_line}; border-radius: 8px;
        color: {panel_text}; padding: 6px 9px; font-size: 13px; margin-right: 10px;
    }}
    *[panel="true"] *[match="true"] {{ background: {rgba(t.accent, 0.16)}; border-radius: 8px; }}
    QFrame#SettingsSide {{ background: {rgba(t.panel_text, 0.035)}; border-right: 1px solid {panel_line}; }}
    QListWidget#SettingsNav {{
        background: transparent; border: none; padding: 4px 10px 10px 0; outline: 0; font-size: 13px;
    }}
    QListWidget#SettingsNav::item {{ color: {panel_text}; padding: 7px 8px; border-radius: 7px; margin-bottom: 2px; }}
    QListWidget#SettingsNav::item:selected {{ background: {rgba(t.accent)}; color: white; }}
    QListWidget#SettingsNav::item:hover:!selected {{ background: {rgba(t.panel_text, 0.07)}; }}
    #SettingsBody {{ background: {panel}; }}
    *[panel="true"] QLabel#SettingTitle {{ font-weight: 600; }}
    *[panel="true"] QLabel#EditorSection {{ color: {panel_dim}; font-size: 10px; font-weight: 700; }}
    *[panel="true"] QFrame#SettingsLine {{ background: {panel_line}; }}
    QListWidget#SettingsList {{
        background: {rgba(t.panel_text, 0.04)}; border: 1px solid {panel_line}; border-radius: 8px;
        color: {panel_text}; font-size: 13px; outline: 0; padding: 4px;
    }}
    QListWidget#SettingsList::item {{ padding: 5px 6px; border-radius: 5px; }}
    QListWidget#SettingsList::item:selected {{ background: {rgba(t.accent)}; color: white; }}
    QListWidget#LibraryList {{ background: transparent; border: none; outline: 0; }}
    QLabel#KeyCap {{
        background: {rgba(t.panel_text, 0.08)}; color: {panel_text}; border: 1px solid {panel_line};
        border-bottom-width: 2px; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 600;
    }}

    #ContentFrame[split="true"] {{ background: transparent; }}
    QSplitter#SplitView::handle {{ background: transparent; }}
    QFrame#Pane {{ background: {content_bg}; border-radius: 10px; }}
    QFrame#PaneFocus {{ background: transparent; border: 2px solid {rgba(t.accent, 0.9)}; border-radius: 10px; }}
    QToolButton#PaneClose {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 12px; }}
    QFrame#TabRow[insplit="true"] {{ background: {hover}; }}
    QFrame#TabRow[marked="true"] {{ background: {selected}; }}

    #PeekBackdrop {{ background: rgba(0, 0, 0, {70 if t.dark else 40}); }}
    QFrame#PeekCard {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 14px; }}
    QLabel#PeekHost {{ color: {panel_dim}; font-size: 12px; font-weight: 600; }}

    #EaselToolbar {{ background: white; border: 1px solid #e7e5e4; border-radius: 12px; }}
    #EaselTitle {{ background: transparent; border: none; color: #1f2937; font-weight: 600; font-size: 13px; }}
    QToolButton#EaselTool {{ background: transparent; border: none; border-radius: 7px; padding: 5px; }}
    QToolButton#EaselTool:hover {{ background: #f5f5f4; }}
    QToolButton#EaselTool:checked {{ background: {rgba(t.accent)}; }}
    QToolButton#EaselSwatch {{ border-radius: 9px; border: 2px solid white; }}
    QToolButton#EaselSwatch:checked {{ border: 2px solid {rgba(t.accent)}; }}
    QTextEdit#NoteEditor {{
        background: {content_bg}; color: {rgba(QColor("#f2f2f4") if t.dark else QColor("#1d1d22"))}; border: none;
        font-size: 15px; selection-background-color: {rgba(t.accent, 0.35)};
    }}

    #Sidebar[floating="true"] {{
        background: {t.qss_gradient()};
        border: 1px solid {faint}; border-radius: 12px;
    }}

    QMenu {{
        background: {panel}; border: 1px solid {panel_line}; border-radius: 10px; padding: 5px;
    }}
    QMenu::item {{ color: {panel_text}; padding: 6px 22px 6px 8px; border-radius: 6px; font-size: 13px; }}
    QMenu::item:selected {{ background: {rgba(t.accent)}; color: white; }}
    QMenu::item:disabled {{ color: {panel_dim}; background: transparent; }}
    QMenu::separator {{ height: 1px; background: {panel_line}; margin: 4px 6px; }}
    QMenu::icon {{ padding-left: 8px; }}
    QMenu::right-arrow {{ width: 8px; height: 8px; }}

    QFrame#SpaceEditor {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 14px; }}
    #SpaceEditor QLabel {{ color: {panel_text}; }}
    #SpaceEditor QLabel#EditorSection {{ color: {panel_dim}; font-size: 10px; font-weight: 700; }}
    #SpaceEditor QLabel#SpacePreview {{ background: {rgba(t.panel_text, 0.07)}; border-radius: 8px; }}
    #SpaceEditor QLineEdit#SpaceName, #SpaceEditor QComboBox#SpaceCombo {{
        background: {rgba(t.panel_text, 0.06)}; color: {panel_text}; border: 1px solid {panel_line};
        border-radius: 7px; padding: 5px 8px; font-size: 13px; selection-background-color: {rgba(t.accent, 0.45)};
    }}
    #SpaceEditor QLineEdit#SpaceName:focus {{ border: 1px solid {rgba(t.accent)}; }}
    #SpaceEditor QToolButton#IconChoice {{ background: transparent; border: none; border-radius: 7px; }}
    #SpaceEditor QToolButton#IconChoice:hover {{ background: {rgba(t.panel_text, 0.09)}; }}
    #SpaceEditor QToolButton#IconChoice[selected="true"] {{ background: {rgba(t.accent, 0.35)}; }}
    #SpaceEditor QToolButton#ColorDot {{ border: 2px solid transparent; border-radius: 11px; }}
    #SpaceEditor QToolButton#ColorDot[selected="true"] {{ border: 2px solid {panel_text}; }}
    #SpaceEditor QPushButton#MiniTab {{
        background: transparent; color: {panel_dim}; border: none; border-radius: 6px; padding: 2px 8px; font-size: 12px;
    }}
    #SpaceEditor QPushButton#MiniTab:checked {{ background: {rgba(t.panel_text, 0.1)}; color: {panel_text}; }}
    #SpaceEditor QPushButton#LinkButton {{
        background: transparent; color: {rgba(t.accent)}; border: none; font-size: 12px; font-weight: 600;
    }}
    #SpaceEditor QPushButton#ActionRow, #SpaceEditor QPushButton#DangerRow {{
        background: transparent; color: {panel_text}; border: none; border-radius: 7px;
        padding: 6px 8px; font-size: 13px; text-align: left;
    }}
    #SpaceEditor QPushButton#DangerRow {{ color: #ef4444; }}
    #SpaceEditor QPushButton#ActionRow:hover, #SpaceEditor QPushButton#DangerRow:hover {{
        background: {rgba(t.panel_text, 0.07)};
    }}
    #SpaceEditor QFrame#EditorLine {{ background: {panel_line}; }}

    QFrame#LittleCard {{ background: {content_bg}; border-radius: 10px; }}
    QPushButton#LittleHost {{
        background: transparent; border: none; border-radius: 6px; color: {text};
        font-size: 13px; font-weight: 600; text-align: left; padding: 4px 6px;
    }}
    QPushButton#LittleHost:hover {{ background: {hover}; }}
    QLineEdit#LittleUrl {{
        background: {tile}; color: {text}; border: none; border-radius: 7px; padding: 5px 9px; font-size: 13px;
        selection-background-color: {rgba(t.accent, 0.45)};
    }}
    QPushButton#LittleOpen {{
        background: {selected}; color: {text}; border: none; border-radius: 8px;
        padding: 5px 11px; font-size: 12px; font-weight: 600;
    }}
    QPushButton#LittleOpen:hover {{ background: {rgba(QColor("white"), 0.22) if t.dark else rgba(QColor("white"), 0.95)}; }}
    """
