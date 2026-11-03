# Downloads, drawn by SwiftUI (native/Sources/Downloads.swift) on a Mac where the native pages are built.
# DownloadsWindow (downloads.py) is still the list: pages' downloads are added to it, it counts them on the Dock
# and runs yt-dlp. It's never shown here; this sends what it holds (the link, what yt-dlp found, the downloads
# and their progress) and does what's asked in the native window to it.

import json
import os
import sys

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication

from . import native
from .downloads import DownloadRow, _file_mime, quick_look, reveal_in_finder

POLL_MS = 250  # (while it's open: progress moves smoothly)
SYMBOLS = {"ph.music-note": "music.note", "ph.film-strip": "film", "ph.download-simple": "arrow.down.circle"}


class NativeDownloads(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.page = None
        self._sent = ""
        self._looking = None
        self._poll = QTimer(self, interval=POLL_MS, timeout=self.refresh)

    @property
    def qt(self):
        return self.browser.downloads_window  # (the list itself: made once, kept, never shown here)

    def open(self, url=""):
        qt = self.qt
        if url and url.startswith(("http://", "https://")):
            qt.url.setText(url)
            qt.fetch()
        if self.page is not None and self.page.is_open():
            self.refresh()
            self.page.front()
            return
        state = self.describe()
        self._sent = json.dumps(state)
        self.page = native.NativePage({"page": "downloads", "title": "Downloads", "width": 560, "height": 600,
                                       "min_width": 460, "min_height": 380, "show_title": True, **state,
                                       **native.theme_state(self.browser.theme)}, self)
        self.page.action.connect(self._act)
        self.page.closed.connect(self._closed)
        self._poll.start()

    def _closed(self):
        self._poll.stop()
        self.page = None

    def isVisible(self) -> bool:
        return self.page is not None and self.page.is_open()

    def close(self):
        if self.page is not None:
            self.page.close()

    def apply_theme(self):
        if self.page is not None:
            self.page.update(native.theme_state(self.browser.theme))

    # --- What it shows -------------------------------------------------------------------------------------

    def describe(self) -> dict:
        qt = self.qt
        options = None
        if qt.info is not None and not qt.options.isHidden():
            options = {"title": qt.info_title.text(), "meta": qt.info_meta.text().replace("  ·  ", " · "),
                       "video_enabled": qt.video_btn.isEnabled(), "video": qt.video_btn.isChecked(),
                       "video_formats": [qt.video_format.itemText(i) for i in range(qt.video_format.count())],
                       "video_format": qt.video_format.currentIndex(),
                       "qualities": [qt.quality.itemText(i) for i in range(qt.quality.count())],
                       "quality": qt.quality.currentIndex(),
                       "audio_formats": [qt.audio_format.itemText(i) for i in range(qt.audio_format.count())],
                       "audio_format": qt.audio_format.currentIndex(), "subs": qt.subs.text(),
                       "embed": qt.embed_subs.isChecked(), "folder": qt.folder.folder,
                       "folder_name": qt.folder.folder.replace(os.path.expanduser("~"), "~", 1),
                       "download": qt.download_btn.text()}
        items = []
        for item in qt.items:
            path = item.path if item.state == "done" and item.path and os.path.exists(item.path) else ""
            items.append({"id": id(item), "title": item.title, "status": item.status, "state": item.state,
                          "fraction": item.fraction, "can_pause": item.can_pause, "paused": bool(item.paused),
                          "path": path, "symbol": SYMBOLS.get(item.icon, "arrow.down.circle")})
        return {"url": qt.url.text(), "message": qt.message.text(), "can_fetch": qt.fetch_btn.isEnabled(),
                "options": options, "items": items, "finder": "Show in Finder" if sys.platform == "darwin"
                else "Show in Folder"}

    def refresh(self):
        if self.page is None or sip.isdeleted(self.qt):
            return
        state = self.describe()
        text = json.dumps(state)
        if text != self._sent:
            self._sent = text
            self.page.update(state)

    # --- What's done in the native window ------------------------------------------------------------------

    def _act(self, name, event):
        qt, value = self.qt, event.get("value")
        if name in ("settings", "folder", "fetch") and (frame := self.page.frame()) is not None:
            qt.setGeometry(frame)  # (a dialog it opens, over the native window)
        if name == "url" and isinstance(value, str):
            qt.url.setText(value)
            if event.get("submit"):
                qt.fetch()
        elif name == "fetch":
            qt.fetch()
        elif name == "settings":
            qt.open_settings(on_page=self.page)  # (a sheet on this window)
        elif qt.info is not None and name in ("kind", "video_format", "quality", "audio_format", "subs", "embed",
                                               "folder", "download"):
            self._option(name, value)
        elif name == "clear":
            qt.clear_finished()
        elif name in ("open", "reveal", "look", "copy", "pause", "cancel", "remove"):
            item = next((i for i in qt.items if id(i) == event.get("id")), None)
            if item is not None:
                self._item(item, name)
        elif name != "focus":
            return
        self.refresh()

    def _option(self, name, value):
        qt = self.qt
        combos = {"video_format": qt.video_format, "quality": qt.quality, "audio_format": qt.audio_format}
        if name == "kind":
            (qt.video_btn if value == "video" and qt.video_btn.isEnabled() else qt.audio_btn).setChecked(True)
        elif name in combos and isinstance(value, int) and 0 <= value < combos[name].count():
            combos[name].setCurrentIndex(value)
        elif name == "subs" and isinstance(value, str):
            qt.subs.setText(value)
        elif name == "embed":
            qt.embed_subs.setChecked(bool(value))
        elif name == "folder":
            qt.folder.click()  # (its folder picker)
        elif name == "download":
            qt.start_download()

    def _item(self, item, name):
        path = item.path if item.state == "done" and item.path and os.path.exists(item.path) else None
        if name == "open" and path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif name == "reveal" and path:
            reveal_in_finder(path)
        elif name == "look" and path:
            self._looking = quick_look(path, self._looking)
        elif name == "copy" and path:
            QApplication.clipboard().setMimeData(_file_mime(path))
        elif name == "pause" and item.state == "running":
            item.toggle_pause()
        elif name == "cancel":
            item.cancel()
        elif name == "remove" and item.state != "running":
            row = next((r for r in self.qt.findChildren(DownloadRow) if r.item is item), None)
            if row is not None:
                self.qt._remove(row)


def chooser(browser):
    """Downloads, natively where that's built (None: Qt's window)."""
    return NativeDownloads(browser) if native.available() else None
