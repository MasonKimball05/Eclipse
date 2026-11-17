# What you can do with a page: print it, save it (or as a PDF), copy its links, share it, screenshots.
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QFileDialog
from pathlib import Path

from . import clean_links, macos, screenshots
from .downloads import reveal_in_finder, ytdlp_settings
from .models import walk_tabs
from .page_actions import markdown_link, safe_filename


class PageActions:
    def print_page(self, view=None):
        view = view if isinstance(view, QWebEngineView) else self._current_view()
        if view is None or self._printing is not None:
            return
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter  # only loaded when someone prints
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec() != QPrintDialog.DialogCode.Accepted:
            return
        self._printing = (view, printer)  # Qt needs the printer until it's done

        def finished(ok):
            view.printFinished.disconnect(finished)
            view.destroyed.disconnect(closed)
            self._printing = None
            self.toast("Sent to the printer" if ok else "Couldn't print this page")

        def closed(_=None):  # the tab closed mid-print: printFinished will never come
            self._printing = None
        view.printFinished.connect(finished)
        view.destroyed.connect(closed)
        view.print(printer)

    def _save_dialog(self, title, suffix, kind):
        tab = self.current_tab
        folder = Path(ytdlp_settings(self.store)["folder"])
        name = safe_filename(tab.label if tab else "") + suffix
        path, _ = QFileDialog.getSaveFileName(self, title, str(folder / name), kind)
        return path

    def save_pdf(self):
        view = self._current_view()
        if view is None:
            return
        path = self._save_dialog("Save as PDF", ".pdf", "PDF (*.pdf)")
        if not path:
            return
        from PyQt6.QtCore import QLocale, QMarginsF
        from PyQt6.QtGui import QPageLayout, QPageSize
        metric = QLocale().measurementSystem() == QLocale.MeasurementSystem.MetricSystem
        size = QPageSize(QPageSize.PageSizeId.A4 if metric else QPageSize.PageSizeId.Letter)
        layout = QPageLayout(size, QPageLayout.Orientation.Portrait, QMarginsF(12, 12, 12, 12),
                             QPageLayout.Unit.Millimeter)

        def finished(file, ok):
            view.pdfPrintingFinished.disconnect(finished)
            self.toast(f"Saved {Path(file).name}" if ok else "Couldn't save the PDF")
        view.pdfPrintingFinished.connect(finished)
        view.printToPdf(path, layout)

    def save_page(self):
        view = self._current_view()
        if view is None:
            return
        path = self._save_dialog("Save Page As", ".mhtml", "Web archive (*.mhtml)")
        if path:
            view.page().save(path, QWebEngineDownloadRequest.SavePageFormat.MimeHtmlSaveFormat)

    def copy_markdown_link(self, tab=None):
        tab = tab or self.current_tab
        if tab and tab.url:
            QApplication.clipboard().setText(markdown_link(tab.label, clean_links.for_copy(tab.url, self.store.settings)))
            self.toast("Copied as a Markdown link")

    def copy_links(self, tabs):
        """A Markdown list of links, one per tab: a folder's, Today's, a Space's."""
        tabs = [t for t in tabs if t.url.startswith(("http://", "https://", "file:"))]
        if not tabs:
            self.toast("There are no links to copy")
            return
        settings = self.store.settings
        QApplication.clipboard().setText("\n".join(
            f"- {markdown_link(t.label, clean_links.for_copy(t.url, settings))}" for t in tabs))
        self.toast(f"Copied {len(tabs)} link{'s' if len(tabs) != 1 else ''}")

    def copy_today_links(self):
        self.copy_links(walk_tabs(self.store.active_space.today))

    def copy_space_links(self):
        space = self.store.active_space
        self.copy_links([*walk_tabs(space.pinned), *walk_tabs(space.today)])

    def share_page(self, tab=None):
        """macOS's share menu for the page: AirDrop it to your phone, or send it to Messages, Mail, Notes…"""
        tab = tab or self.current_tab
        if tab is None or not tab.url.startswith(("http://", "https://")):
            self.toast("Open a web page to share it")
            return
        pill = self.sidebar.url_pill
        if pill.isVisible() and pill.window() is self:
            anchor = QRect(pill.mapTo(self, QPoint(0, 0)), pill.size())
        else:  # (the sidebar's hidden: from the top of the page)
            anchor = QRect(self.content.mapTo(self, QPoint(self.content.width() // 2, 0)), QSize(1, 1))
        if not macos.share(self, clean_links.for_copy(tab.url, self.store.settings), anchor):
            self.toast("Sharing isn't available here")

    def copy_screenshot(self):
        view = self._current_view()
        if view is None:
            self.toast("Screenshots work on web pages")
            return
        QApplication.clipboard().setImage(screenshots.visible(view))
        self.toast("Copied a screenshot of what's on screen")

    def save_page_screenshot(self):
        """The whole page, top to bottom, as a PNG in your downloads folder."""
        view, tab = self._current_view(), self.current_tab
        if view is None:
            self.toast("Screenshots work on web pages")
            return
        if self._page_shot is not None and not sip.isdeleted(self._page_shot):
            self.toast("Still taking the last screenshot")
            return
        shot = self._page_shot = screenshots.PageShot(view)
        name = safe_filename(tab.label if tab else "")
        shot.done.connect(lambda image, cut_off: self._save_screenshot(image, cut_off, name))
        shot.failed.connect(self._screenshot_failed)
        self.toast("Taking a screenshot of the whole page…")

    def _screenshot_failed(self, message):
        self._page_shot = None
        self.toast(message)

    def _new_file(self, name, extension) -> Path:
        """A name not taken yet in your downloads folder: "Name.png", else "Name 2.png", and so on."""
        folder = Path(ytdlp_settings(self.store)["folder"]).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # (an unplugged drive, say: saving then fails, and says so)
        path, n = folder / f"{name}.{extension}", 2
        while path.exists():
            path, n = folder / f"{name} {n}.{extension}", n + 1
        return path

    def _save_screenshot(self, image, cut_off, name):
        self._page_shot = None
        path = self._new_file(name, "png")
        if not image.save(str(path), "PNG"):
            self.toast("Couldn't save the screenshot")
            return
        note = " (the top of it: the page is very long)" if cut_off else ""
        self.toast(f"Saved {path.name}{note}", lambda: reveal_in_finder(str(path)), "Show")
