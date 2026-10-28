# The start view: what the page area shows with no tab open. Arc shows nothing there, and this keeps its
# feel (the Space's own background, nothing boxed in), with a little to start from: the Space's name, a
# search pill that opens ⌘T, the sites you visit most, and the tabs you closed last.
#
# Settings → Tabs → Start view turns it off, for the plain emptiness.

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from . import storage
from .icons import space_icon
from .models import display_host, frecency
from .theme import letter_icon

SETTING = "start_view"
MOST_VISITED = 8
RECENTLY_CLOSED = 5


def most_visited(history: list[dict], limit=MOST_VISITED) -> list[dict]:
    """The sites you visit most (by ⌘T's measure: visits, weighted to recent ones), one per site:
    [{"url", "title", "host"}], the site's home page if you've been there, else the page you visit most."""
    sites = {}
    for entry in history:
        host = display_host(entry.get("url", ""))
        if not host or not entry["url"].startswith(("https://", "http://")):
            continue
        site = sites.setdefault(host, {"score": 0.0, "best": None, "home": None})
        site["score"] += frecency(entry)
        if site["best"] is None or frecency(entry) > frecency(site["best"]):
            site["best"] = entry
        path = entry["url"].split(host, 1)[-1]
        if path in ("", "/") and site["home"] is None:
            site["home"] = entry
    ranked = sorted(sites.items(), key=lambda item: item[1]["score"], reverse=True)[:limit]
    return [{"url": (site["home"] or site["best"])["url"], "host": host,
             "title": _site_name((site["home"] or site["best"]).get("title", ""), host)} for host, site in ranked]


def _site_name(title: str, host: str) -> str:
    """A tile's label: the site's name from its page title (usually the last part: "Gmail" from "Inbox (3) - Gmail",
    "Wikipedia" from "Eclipse - Wikipedia"), or from its address when the title doesn't give one ("Python" for
    docs.python.org)."""
    from_host = host.split(".")[-2].capitalize() if host.count(".") else host
    for separator in (" · ", " | ", " - ", " — ", " – "):
        if separator in title:
            title = title.split(separator)[-1]
    title = title.strip()
    if not title or len(title) > 18 or any(c.isdigit() for c in title) or "(" in title:
        return from_host  # (a count, a version, a long headline: the site's own name reads better)
    return title


class StartView(QWidget):
    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.setObjectName("EmptyState")  # (the page frame goes see-through over it: ContentFrame.blank)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addStretch(2)
        self.column = QWidget()
        self.column.setMaximumWidth(560)
        self.body = QVBoxLayout(self.column)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.column, 10)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)

    def refresh(self):
        """Fill it in again (it's shown: what you visit and close changes)."""
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout() is not None:
                QWidget().setLayout(item.layout())
        if not self.browser.store.settings.get(SETTING, True):
            return  # (off: nothing, like Arc)
        theme, space = self.browser.theme, self.browser.store.active_space
        text = theme.text
        faint = QColor(text)
        faint.setAlphaF(0.55)
        self.setStyleSheet(f"""
            #StartTitle {{ color: {text.name()}; font-size: 26px; font-weight: 600; }}
            #StartHeading {{ color: {faint.name(QColor.NameFormat.HexArgb)}; font-size: 11px; font-weight: 600;
                            letter-spacing: 0.6px; margin-top: 10px; }}
            #StartSearch {{ background: rgba({text.red()}, {text.green()}, {text.blue()}, 0.09); border: none;
                           border-radius: 12px; padding: 12px 16px; text-align: left; font-size: 14px;
                           color: {faint.name(QColor.NameFormat.HexArgb)}; }}
            #StartSearchText {{ color: {faint.name(QColor.NameFormat.HexArgb)}; font-size: 14px; background: transparent; }}
            #StartSearch:hover {{ background: rgba({text.red()}, {text.green()}, {text.blue()}, 0.14); }}
            #StartTile {{ background: transparent; border: none; border-radius: 12px; padding: 8px 2px;
                         color: {text.name()}; font-size: 12px; }}
            #StartTile:hover {{ background: rgba({text.red()}, {text.green()}, {text.blue()}, 0.10); }}
            #StartClosed {{ background: transparent; border: none; border-radius: 8px; padding: 6px 8px;
                           text-align: left; color: {text.name()}; font-size: 13px; }}
            #StartClosed:hover {{ background: rgba({text.red()}, {text.green()}, {text.blue()}, 0.10); }}
        """)
        title = QPushButton(f"  {space.name}")
        title.setObjectName("StartTitle")
        title.setFlat(True)
        title.setIcon(space_icon(space, text, 64))
        title.setIconSize(QSize(28, 28))
        title.setStyleSheet("text-align: left; background: transparent; border: none;")
        title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.body.addWidget(title)
        search = QPushButton()
        search.setObjectName("StartSearch")
        search.setCursor(Qt.CursorShape.PointingHandCursor)
        search.setMinimumHeight(44)
        search.clicked.connect(lambda: self.browser.open_command_bar("new"))
        inside = QHBoxLayout(search)
        inside.setContentsMargins(16, 0, 16, 0)
        for words, align in (("Search or enter an address", Qt.AlignmentFlag.AlignLeft),
                             ("⌘T", Qt.AlignmentFlag.AlignRight)):
            label = QLabel(words)
            label.setObjectName("StartSearchText")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            inside.addWidget(label, 1 if align == Qt.AlignmentFlag.AlignLeft else 0, align)
        self.body.addWidget(search)
        sites = most_visited(self.browser.store.history)
        if sites:
            self.body.addWidget(self._heading("MOST VISITED"))
            grid = QGridLayout()
            grid.setSpacing(6)
            for i, site in enumerate(sites):
                tile = QToolButton()
                tile.setObjectName("StartTile")
                tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                tile.setIcon(storage.favicon(site["url"]) or letter_icon(site["title"], theme.accent, 64))
                tile.setIconSize(QSize(32, 32))
                tile.setText(site["title"])
                tile.setToolTip(site["url"])
                tile.setFixedSize(QSize(128, 82))
                tile.setCursor(Qt.CursorShape.PointingHandCursor)
                tile.clicked.connect(lambda _=False, url=site["url"]: self.browser.open_url(url))
                grid.addWidget(tile, i // 4, i % 4)
            self.body.addLayout(grid)
        closed = getattr(self.browser, "_closed", [])
        if closed:
            self.body.addWidget(self._heading("RECENTLY CLOSED"))
            for index in range(len(closed) - 1, max(len(closed) - 1 - RECENTLY_CLOSED, -1), -1):
                data = closed[index][0]
                url = data.get("url", "")
                row = QPushButton("  " + (data.get("title") or display_host(url) or "Untitled"))
                row.setObjectName("StartClosed")
                row.setIcon(storage.favicon(url) or QIcon(letter_icon(display_host(url) or "?", theme.accent, 32)))
                row.setIconSize(QSize(16, 16))
                row.setToolTip(url)
                row.setCursor(Qt.CursorShape.PointingHandCursor)
                row.clicked.connect(lambda _=False, i=index: self.browser.reopen_closed_tab(i))
                self.body.addWidget(row)
        if not sites and not closed:
            hint = QLabel("The sites you visit most will be here.")
            hint.setObjectName("StartHeading")
            self.body.addWidget(hint)

    def _heading(self, text):
        label = QLabel(text)
        label.setObjectName("StartHeading")
        return label
