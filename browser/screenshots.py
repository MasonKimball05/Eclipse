# Screenshots of a page: what's on screen (copied), or the whole page top to bottom (saved as a PNG).
#
# Chromium in Qt can only show us what's on screen, so the whole page is taken the way screenshot
# extensions do it: scroll a screenful at a time and stitch the pictures together. Headers and other
# fixed or sticky bits are hidden after the first screenful, or they'd repeat down the picture, and
# everything is put back (scroll position included) when it's done. The scripts run in the
# application world, where the page can't see or interfere with them.

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPointF, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtWebEngineCore import QWebEngineScript

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
MAX_PIXELS = 50_000_000  # about 200 MB of picture: a very long page is cut off there
SETTLE_MS = 60           # after the page says it scrolled, for the new part to be painted
SCROLL_TIMEOUT_MS = 500  # a page that doesn't report scrolling (it was already there)

PREPARE = r"""
(() => {
  const style = document.createElement('style');
  style.id = '__ecShotStyle';
  style.textContent = 'html{scrollbar-width:none!important;scroll-behavior:auto!important}' +
                      '::-webkit-scrollbar{display:none!important}';
  (document.head || document.documentElement).appendChild(style);
  const d = document.documentElement, b = document.body;
  return {height: Math.max(d.scrollHeight, b ? b.scrollHeight : 0), view: innerHeight, x: scrollX, y: scrollY};
})()
"""

SCROLL = "window.scrollTo({left: 0, top: %d, behavior: 'instant'}); scrollY"

HIDE_FIXED = r"""
(() => {
  const hidden = [];
  for (const e of document.querySelectorAll('body *')) {
    const position = getComputedStyle(e).position;
    if (position === 'fixed' || position === 'sticky') {
      hidden.push([e, e.style.getPropertyValue('visibility'), e.style.getPropertyPriority('visibility')]);
      e.style.setProperty('visibility', 'hidden', 'important');
    }
  }
  window.__ecShotHidden = hidden;
  return hidden.length;
})()
"""

RESTORE = r"""
((x, y) => {
  for (const [e, value, priority] of window.__ecShotHidden || []) e.style.setProperty('visibility', value, priority);
  delete window.__ecShotHidden;
  document.getElementById('__ecShotStyle')?.remove();
  window.scrollTo({left: x, top: y, behavior: 'instant'});
})(%d, %d)
"""


def visible(view) -> QImage:
    """The page as it is on screen, at the screen's resolution."""
    return view.grab().toImage()


def frame_rows(height, view_height):
    """Where each screenful starts, top to bottom: the last one ends at the bottom of the page."""
    if height <= view_height:
        return [0]
    rows = list(range(0, height - view_height, view_height))
    return rows + [height - view_height]


class PageShot(QObject):
    """The whole page, stitched together. Emits done(image, cut_off) or failed(message) once."""
    done = pyqtSignal(QImage, bool)
    failed = pyqtSignal(str)

    def __init__(self, view):
        super().__init__(view)  # (gone with the page: a closed tab just never finishes)
        self.view = view
        self.image = None
        self.painter = None
        self._waiting = None
        view.page().runJavaScript(PREPARE, WORLD, self._prepared)

    def _alive(self):
        return not sip.isdeleted(self) and not sip.isdeleted(self.view)

    def _prepared(self, info):
        if not self._alive():
            return
        if not info or not info.get("view"):
            self.failed.emit("This page can't be captured")
            return
        self.start = (int(info["x"]), int(info["y"]))
        first = self.view.grab()
        self.ratio = first.devicePixelRatio()
        self.scale = self.view.zoomFactor() * self.ratio  # page pixels → picture pixels
        view_height = int(info["view"])
        width = first.width()
        height = int(info["height"])
        self.cut_off = height * self.scale * width > MAX_PIXELS
        if self.cut_off:
            height = max(view_height, int(MAX_PIXELS / width / self.scale))
        self.image = QImage(width, round(height * self.scale), QImage.Format.Format_ARGB32_Premultiplied)
        self.image.fill(0)
        self.painter = QPainter(self.image)
        self.rows = frame_rows(height, view_height)
        self.index = 0
        self._scroll()

    def _scroll(self):
        target = self.rows[self.index]
        page = self.view.page()
        page.scrollPositionChanged.connect(self._scrolled)
        self._waiting = QTimer(self, singleShot=True, interval=SCROLL_TIMEOUT_MS)
        self._waiting.timeout.connect(self._scrolled)
        self._waiting.start()
        page.runJavaScript(SCROLL % target, WORLD, self._note_y)

    def _note_y(self, y):
        if self._alive() and isinstance(y, (int, float)):
            self.rows[self.index] = int(y)  # where it really got to (a page can't scroll past its end)

    def _scrolled(self, *_):
        if not self._alive() or self._waiting is None:
            return
        self._waiting.stop()
        self._waiting = None
        try:
            self.view.page().scrollPositionChanged.disconnect(self._scrolled)
        except TypeError:
            pass
        QTimer.singleShot(SETTLE_MS, self._take)

    def _take(self):
        if not self._alive():
            return
        shot = self.view.grab()
        shot.setDevicePixelRatio(1)
        self.painter.drawPixmap(QPointF(0, round(self.rows[self.index] * self.scale)), shot)
        self.index += 1
        if self.index == 1 and len(self.rows) > 1:
            # After the first screenful: a header that stays put would show up on every one
            self.view.page().runJavaScript(HIDE_FIXED, WORLD, lambda _n: self._alive() and self._next())
        else:
            self._next()

    def _next(self):
        if self.index < len(self.rows):
            self._scroll()
            return
        self.painter.end()
        self.view.page().runJavaScript(RESTORE % self.start, WORLD)
        self.image.setDevicePixelRatio(self.ratio)
        self.done.emit(self.image, self.cut_off)
        self.deleteLater()
