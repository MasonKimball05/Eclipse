# Tab previews: rest the pointer on a tab in the sidebar and a card beside it shows the page as it
# looked when you last left it, with its title and address. Once one is showing, moving to the next
# tab shows its preview straight away, the way tooltips do. The pictures are the small ones the
# window keeps for the ⌃Tab switcher, so previews cost nothing until you look at one. Under the title: the
# site, whether the tab's asleep or kept awake, and how much memory its page is using.

import html

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer
from PyQt6.QtGui import QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout

from . import memory, motion
from .models import display_host

SETTING = "tab_previews"
DELAY_MS = 550  # the pointer resting on a tab before its preview shows
WARM_MS = 400   # after one goes, how long the next tab's still shows at once
WIDTH = 260
GAP = 16        # between the sidebar and the card (clear of the page's own edge, too)
ASPECT = 0.625  # the picture: the top of the page, 16:10


def rounded(picture: QPixmap, width: int, ratio: float, radius=7, shape=ASPECT) -> QPixmap:
    """The top of the picture, `width` points wide (at the screen's `ratio`) and no taller than `shape`
    allows, with rounded corners."""
    scaled = picture.scaledToWidth(round(width * ratio), Qt.TransformationMode.SmoothTransformation)
    scaled = scaled.copy(0, 0, scaled.width(), min(scaled.height(), round(scaled.width() * shape)))
    scaled.setDevicePixelRatio(ratio)
    out = QPixmap(scaled.size())
    out.setDevicePixelRatio(ratio)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, scaled.width() / ratio, scaled.height() / ratio), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, scaled)
    p.end()
    return out


# Live previews, as Arc's: resting on a mail or calendar tab shows what's in it (your newest unread mail, what's
# coming up), read from the page that's open, instead of its picture. Read in the page's isolated world; nothing
# found (the site changed, signed out), and it's the picture as ever.
LIVE_ITEMS = 4
LIVE = {
    "mail": ("Unread", r"""
        [...document.querySelectorAll('tr.zA.zE')].slice(0, %d).map(row => {
            const who = row.querySelector('.yX [name], .yX [email], .zF, .yP');
            const subject = row.querySelector('.bog, .bqe, .y6');
            return [who && (who.getAttribute('name') || who.textContent), subject && subject.textContent]
                .filter(Boolean).map(s => s.trim()).join(' — ');
        }).filter(Boolean)"""),
    "calendar": ("Coming up", r"""
        [...document.querySelectorAll('[data-eventid]')].map(chip => {
            const text = (chip.querySelector('.XuJrye') || chip).textContent || chip.getAttribute('aria-label') || '';
            return text.split(',').slice(0, 2).map(s => s.trim()).filter(Boolean).join(' · ');
        }).filter((text, i, all) => text && all.indexOf(text) === i).slice(0, %d)"""),
}


def live_kind(url: str) -> str | None:
    """"mail" or "calendar" for the pages with a live preview, else None."""
    host = display_host(url)
    return {"mail.google.com": "mail", "calendar.google.com": "calendar"}.get(host)


def memory_text(browser, tab) -> str:
    """How much memory a tab's page is using ("180 MB"), as Chrome's previews say, so Memory Saver isn't a mystery;
    "" when it isn't open (or that can't be told). Pages of one site can share a process: then it says so."""
    view = tab.view
    page = view.page() if hasattr(view, "page") else None
    pid = page.renderProcessPid() if page is not None else 0
    used = memory.process_mb(pid)
    if not used:
        return ""
    sharing = sum(1 for other in browser.store.all_tabs() if other is not tab and hasattr(other.view, "page")
                  and other.view.page().renderProcessPid() == pid)
    amount = f"{used / 1024:.1f} GB" if used >= 1024 else f"{used:.0f} MB"
    return f"{amount}, shared with {sharing} other tab{'s' if sharing != 1 else ''}" if sharing else amount


class TabPreview(QFrame):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.setObjectName("TabPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # never in the pointer's way
        self.setFixedWidth(WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 10)
        layout.setSpacing(3)
        self.picture = QLabel()
        self.title = QLabel()
        self.title.setObjectName("TabPreviewTitle")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.host = QLabel()
        self.host.setObjectName("TabPreviewHost")
        self.live = QLabel()  # (a mail or calendar tab's newest: in the picture's place)
        self.live.setObjectName("TabPreviewLive")
        self.live.setTextFormat(Qt.TextFormat.RichText)  # (its heading small and dim, as the app's section labels)
        self.live.setContentsMargins(8, 6, 8, 7)
        self.live.hide()
        layout.addWidget(self.picture)
        layout.addWidget(self.live)
        layout.addSpacing(3)
        for label in (self.title, self.host):
            label.setContentsMargins(4, 0, 4, 0)
            layout.addWidget(label)
        self.row = None
        self._warm = False
        self._live: dict[str, list] = {}  # tab id → what was read from it (newest mail, what's coming up)
        self._live_for = None  # (the row those were read for)
        self._timer = QTimer(self, singleShot=True, interval=DELAY_MS, timeout=self._show)
        self._cool = QTimer(self, singleShot=True, interval=WARM_MS, timeout=self._cool_down)
        self.hide()

    @property
    def enabled(self) -> bool:
        return self.browser.store.settings.get(SETTING, True)

    def hover(self, row):
        if not self.enabled:
            return
        self.row = row
        self._cool.stop()
        if self.isVisible() or self._warm:
            self._show()
        else:
            self._read_live(row)  # (while it waits: ready by the time it shows, so it doesn't change size then)
            self._timer.start()

    def leave(self, row):
        if row is not self.row:
            return
        self.row = None
        self._timer.stop()
        if self.isVisible():
            self._warm = True
            self._cool.start()
            motion.fade(self, False, 90)

    def dismiss(self):
        """A click, a drag, a Space switch: gone now, and the next one waits again."""
        self.row = None
        self._warm = False
        self._timer.stop()
        self._cool.stop()
        self.hide()

    def _cool_down(self):
        self._warm = False

    def _show(self):
        row = self.row
        if row is None or sip.isdeleted(row) or not row.isVisible():
            return
        tab = row.tab
        if tab is self.browser.current_tab:  # (it's right there on screen)
            self.hide()
            return
        thumbnail = self.browser.thumbnails.get(tab.id)
        inner = WIDTH - 16
        if thumbnail is not None and not thumbnail.isNull():
            self.picture.setPixmap(rounded(thumbnail, inner, self.devicePixelRatioF()))
            self.picture.show()
        else:
            self.picture.hide()
        items = self._live.get(tab.id) if self._live_for is row else None
        self._set_live(row, items)
        if items is None:
            self._read_live(row)  # (not read yet: shown when it comes)
        metrics = QFontMetrics(self.title.font())
        self.title.setText(metrics.elidedText(tab.label, Qt.TextElideMode.ElideRight, (inner - 8) * 2 - 24))
        sleeping = self.browser.is_sleeping(tab)
        state = ["Sleeping" if sleeping else "Kept awake" if tab.awake else ""]
        if not sleeping:
            state.append(memory_text(self.browser, tab))
        host = display_host(tab.url) or tab.url
        self.host.setText("  ·  ".join([host] + [s for s in state if s]))
        self.layout().activate()
        self.setFixedHeight(self.layout().sizeHint().height())
        self._place(row)
        self.raise_()
        if not self.isVisible() or getattr(self, "_pb_fade_target", True) is False:
            motion.fade(self, True, 120)

    def _read_live(self, row):
        """A mail or calendar tab: its newest, read from its page, shown in the picture's place when they come."""
        tab = row.tab
        kind = live_kind(tab.url)
        page = tab.view.page() if kind and hasattr(tab.view, "page") and not self.browser.is_sleeping(tab) else None
        if page is None:
            return
        heading, script = LIVE[kind]

        def got(items):
            if self.row is not row or sip.isdeleted(row):
                return  # (gone on to another tab)
            self._live_for = row
            self._live[tab.id] = [str(i) for i in items[:LIVE_ITEMS]] if isinstance(items, list) and items else []
            if self.isVisible():  # (shown before it came: in now, with a quick fade so it doesn't jump)
                self._set_live(row, self._live[tab.id], fade=True)
        page.runJavaScript(script % LIVE_ITEMS, 1, got)  # (1: the isolated world, apart from the page's own)

    def _set_live(self, row, items, fade=False):
        """The newest from a mail or calendar tab in the picture's place; none (or nothing found): the picture."""
        if not items:
            self.live.hide()
            return
        heading = LIVE[live_kind(row.tab.url)][0]
        metrics = QFontMetrics(self.live.font())
        dim = self.browser.theme.panel_text
        lines = "".join(f"<div style='margin-top: 3px'>{html.escape(metrics.elidedText(item, Qt.TextElideMode.ElideRight, WIDTH - 40))}</div>"
                        for item in items)
        self.live.setText(f"<div style='font-size: 10px; font-weight: 600; color: rgba({dim.red()},{dim.green()},"
                          f"{dim.blue()},140)'>{html.escape(heading.upper())}</div>{lines}")
        if fade:
            motion.crossfade(self, self, lambda: self._swap_in_live(row), ms=120)
        else:
            self.picture.hide()
            self.live.show()

    def _swap_in_live(self, row):
        self.picture.hide()
        self.live.show()
        self.layout().activate()
        self.setFixedHeight(self.layout().sizeHint().height())
        self._place(row)

    def _place(self, row):
        window = self.browser
        sidebar = window.sidebar
        edge = sidebar.mapTo(window, QPoint(0, 0)).x()
        on_right = getattr(window, "sidebar_on_right", False)
        x = edge - GAP - self.width() if on_right else edge + sidebar.width() + GAP
        middle = row.mapTo(window, QPoint(0, row.height() // 2)).y()
        y = max(8, min(middle - self.height() // 2, window.height() - self.height() - 8))
        self.move(x, y)
