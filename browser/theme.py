# Colours, per-Space theming and the app-wide stylesheet.
#
# Like Arc, every Space has an accent colour; the whole window chrome is tinted
# with a soft gradient of it. Light/dark follows the macOS appearance setting.

import random
from dataclasses import dataclass

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QIcon, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

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


def letter_icon(text: str, color: QColor, size: int = 32) -> QIcon:
    # Fallback favicon: the first letter of the site on a rounded square
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
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, (text[:1] or "?").upper())
    p.end()
    return QIcon(pm)


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


def stylesheet(t: Theme) -> str:
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
    #Sidebar QLabel {{ color: {text}; background: transparent; }}
    #Sidebar QLabel[dim="true"] {{ color: {dim}; font-size: 11px; }}
    #Sidebar QScrollArea, #SidebarScrollBody {{ background: transparent; border: none; }}
    #Sidebar QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}
    #Sidebar QScrollBar::handle:vertical {{ background: {faint}; border-radius: 3px; min-height: 24px; }}
    #Sidebar QScrollBar::add-line, #Sidebar QScrollBar::sub-line,
    #Sidebar QScrollBar::add-page, #Sidebar QScrollBar::sub-page {{ height: 0; background: none; }}

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
    QFrame#TabRow:hover {{ background: {hover}; }}
    QFrame#TabRow[selected="true"] {{ background: {selected}; }}
    QFrame#TabRow QLabel {{ font-size: 13px; }}
    QFrame#TabRow QLabel[unloaded="true"] {{ color: {dim}; }}

    QToolButton#FavoriteTile {{ background: {tile}; border: none; border-radius: 10px; }}
    QToolButton#FavoriteTile:hover {{ background: {hover}; }}
    QToolButton#FavoriteTile[selected="true"] {{ background: {selected}; }}

    QToolButton#SpaceDot {{
        background: transparent; border: none; border-radius: 6px; padding: 3px; font-size: 14px;
    }}
    QToolButton#SpaceDot:hover {{ background: {hover}; }}
    QToolButton#SpaceDot[selected="true"] {{ background: {selected}; }}

    QFrame#DropLine {{ background: {text}; border-radius: 1px; }}
    QFrame#DropBox {{ background: transparent; border: 2px solid {text}; border-radius: 9px; }}
    QFrame#Divider {{ background: {faint}; }}
    QPushButton#ClearButton {{
        background: transparent; border: none; color: {dim}; font-size: 11px;
        padding: 2px 6px; border-radius: 5px;
    }}
    QPushButton#ClearButton:hover {{ background: {hover}; color: {text}; }}
    QLabel#SpaceTitle {{ font-size: 13px; font-weight: 600; padding-left: 4px; }}

    QSplitter::handle {{ background: transparent; }}
    #ContentFrame {{ background: {content_bg}; border-radius: 10px; }}
    #EmptyState QLabel {{ color: {rgba(QColor("#ffffff") if t.dark else QColor("#000000"), 0.35)}; font-size: 14px; }}
    #EmptyState QLabel#EmptyTitle {{ font-size: 22px; font-weight: 600; }}
    #ProgressBar {{ background: {rgba(t.accent)}; border-radius: 1px; }}

    #LinkLabel, #Toast {{
        background: {panel}; color: {panel_text}; border: 1px solid {panel_line};
        border-radius: 7px; padding: 4px 9px; font-size: 12px;
    }}
    #Toast {{ font-size: 13px; padding: 7px 14px; border-radius: 10px; }}

    #FindBar {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 10px; }}
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

    #CommandBackdrop {{ background: rgba(0, 0, 0, {60 if t.dark else 28}); }}
    #CommandPanel {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 14px; }}
    #CommandInput {{
        background: transparent; border: none; color: {panel_text}; font-size: 18px;
        padding: 14px 16px; selection-background-color: {rgba(t.accent, 0.45)};
    }}
    #CommandDivider {{ background: {panel_line}; }}
    #CommandList {{ background: transparent; border: none; outline: 0; padding: 6px; }}

    QToolButton#ShieldButton {{
        background: transparent; border: none; border-radius: 6px; padding: 1px 5px;
        color: {dim}; font-size: 11px;
    }}
    QToolButton#ShieldButton:hover {{ background: {hover}; color: {text}; }}

    #DownloadsWindow, #DownloadSettings {{ background: {panel}; }}
    #DownloadsWindow QLabel, #DownloadSettings QLabel, #DownloadsWindow QCheckBox, #DownloadSettings QCheckBox {{
        color: {panel_text}; font-size: 13px;
    }}
    #DownloadsWindow QLabel#PanelTitle {{ font-size: 15px; font-weight: 600; }}
    #DownloadsWindow QLabel#InfoTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#Hint, QLabel#DownloadStatus {{ color: {panel_dim}; font-size: 12px; }}
    QLabel#DownloadTitle {{ font-weight: 500; }}
    #DownloadsWindow QLineEdit, #DownloadSettings QLineEdit, #DownloadsWindow QComboBox, #DownloadSettings QComboBox {{
        background: {rgba(t.panel_text, 0.06)}; color: {panel_text}; border: 1px solid {panel_line};
        border-radius: 7px; padding: 6px 9px; font-size: 13px; selection-background-color: {rgba(t.accent, 0.45)};
    }}
    #DownloadsWindow QLineEdit:focus, #DownloadSettings QLineEdit:focus {{ border: 1px solid {rgba(t.accent)}; }}
    #DownloadsWindow QPushButton, #DownloadSettings QPushButton {{
        background: {rgba(t.panel_text, 0.08)}; color: {panel_text}; border: none; border-radius: 7px;
        padding: 7px 14px; font-size: 13px;
    }}
    #DownloadsWindow QPushButton:hover, #DownloadSettings QPushButton:hover {{ background: {rgba(t.panel_text, 0.14)}; }}
    #DownloadsWindow QPushButton:disabled {{ color: {panel_dim}; }}
    #DownloadsWindow QPushButton#Primary, #DownloadSettings QPushButton#Primary {{
        background: {rgba(t.accent)}; color: white; font-weight: 600; padding: 8px 20px;
    }}
    #DownloadsWindow QPushButton#Primary:hover, #DownloadSettings QPushButton#Primary:hover {{ background: {rgba(t.accent, 0.85)}; }}
    #DownloadsWindow QPushButton#Segment:checked, #DownloadSettings QPushButton#Segment:checked {{
        background: {rgba(t.accent)}; color: white;
    }}
    #DownloadsWindow QPushButton#Link {{ background: transparent; color: {panel_dim}; padding: 2px 6px; }}
    #DownloadsWindow QPushButton#FolderButton, #DownloadSettings QPushButton#FolderButton {{ text-align: left; }}
    #DownloadsWindow QFrame#OptionsCard {{ background: {rgba(t.panel_text, 0.04)}; border: 1px solid {panel_line}; border-radius: 12px; }}
    #DownloadsWindow QFrame#DownloadRow {{ background: {rgba(t.panel_text, 0.04)}; border-radius: 10px; }}
    #DownloadsWindow QScrollArea, #DownloadList {{ background: transparent; }}
    #DownloadsWindow QProgressBar {{ background: {panel_line}; border: none; border-radius: 2px; }}
    #DownloadsWindow QProgressBar::chunk {{ background: {rgba(t.accent)}; border-radius: 2px; }}
    #DownloadsWindow QToolButton#IconButton:hover {{ background: {rgba(t.panel_text, 0.08)}; }}

    #ContentFrame[split="true"] {{ background: transparent; }}
    QSplitter#SplitView::handle {{ background: transparent; }}
    QFrame#Pane {{ background: {content_bg}; border-radius: 10px; }}
    QFrame#PaneFocus {{ background: transparent; border: 2px solid {rgba(t.accent, 0.9)}; border-radius: 10px; }}
    QToolButton#PaneClose {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 12px; }}
    QFrame#TabRow[insplit="true"] {{ background: {hover}; }}

    #PeekBackdrop {{ background: rgba(0, 0, 0, {70 if t.dark else 40}); }}
    QFrame#PeekCard {{ background: {panel}; border: 1px solid {panel_line}; border-radius: 14px; }}
    QLabel#PeekHost {{ color: {panel_dim}; font-size: 12px; font-weight: 600; }}

    QTextEdit#NoteEditor {{
        background: {content_bg}; color: {rgba(QColor("#f2f2f4") if t.dark else QColor("#1d1d22"))}; border: none;
        font-size: 15px; selection-background-color: {rgba(t.accent, 0.35)};
    }}

    #Sidebar[floating="true"] {{
        background: {t.qss_gradient()};
        border: 1px solid {faint}; border-radius: 12px;
    }}

    #LittleHeader {{ background: {rgba(t.top)}; }}
    #LittleHeader QLabel {{ color: {text}; font-size: 12px; }}
    #LittleHeader QLineEdit {{
        background: {tile}; color: {text}; border: none; border-radius: 7px; padding: 4px 8px;
    }}
    """
