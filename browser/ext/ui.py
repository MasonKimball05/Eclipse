# The extension runtime's windows: installing userscripts and extensions, the toolbar
# popup an extension shows when you click it, and the Extensions page in Settings.

from html import escape
from pathlib import Path

import qtawesome as qta
from PyQt6 import sip
from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import QIcon
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                             QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import brand
from ..features import Feature
from ..theme import stylesheet
from ..webview import WebView
from . import chrome, userscripts
from .patterns import match_pattern, matches

POPUP_MIN = QSize(60, 30)
POPUP_MAX = QSize(800, 600)  # Chrome's limits for a toolbar popup
MEASURE = "[document.documentElement.scrollWidth, document.documentElement.scrollHeight]"


def fetch(network, url, done, fail):
    """GET a URL in the background; done(bytes, final_url) or fail(message)."""
    request = QNetworkRequest(QUrl(url))
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    request.setTransferTimeout(30_000)
    reply = network.get(request)

    def finished():
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0
        if reply.error() != QNetworkReply.NetworkError.NoError or status >= 400:
            fail(reply.errorString())
        else:
            done(bytes(reply.readAll()), reply.url().toString())
        reply.deleteLater()
    reply.finished.connect(finished)


# --- Installing -------------------------------------------------------------------------------------

class UserscriptDialog(QDialog):
    """Arc/Tampermonkey-style "Install this script?" with what it runs on and what it can do."""

    def __init__(self, script: userscripts.Userscript, replacing, theme, parent):
        super().__init__(parent)
        self.setObjectName("UserscriptDialog")
        self.setProperty("panel", True)
        self.setStyleSheet(stylesheet(theme))
        self.setWindowTitle("Install Userscript")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel(f"{script.name}  {script.version}".strip())
        title.setTextFormat(Qt.TextFormat.PlainText)  # the script chose these words, not us
        title.setObjectName("PanelTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        # Everything the script supplies is escaped, so its text can't restyle or hide the warnings
        lines = []
        if script.description:
            lines.append(escape(script.description))
        if replacing is not None:
            lines.append(f"<b>Updates</b> the installed version {escape(replacing.version or '')}".strip())
        sites = [escape(s) for s in script.matches] or ["every website"]
        lines.append("<b>Runs on</b> " + ", ".join(f"<code>{s}</code>" for s in sites[:8]) +
                     (f" and {len(sites) - 8} more" if len(sites) > 8 else ""))
        grants = [escape(g) for g in script.grants if g != "none"]
        lines.append("<b>Can use</b> " + (", ".join(grants) if grants else "nothing beyond the page itself"))
        if script.requires:
            lines.append(f"<b>Loads</b> {len(script.requires)} library file(s) from the web")
        if script.unsupported_grants:
            lines.append("<span style='color:#d97706'><b>Not supported here:</b> " +
                         ", ".join(map(escape, script.unsupported_grants)) + " — parts of the script may not work.</span>")
        if script.origin:
            lines.append(f"<span style='opacity:.6'>From {escape(script.origin)}</span>")
        body = QLabel("<br><br>".join(lines))
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(body)
        warn = QLabel("Only install scripts from sources you trust: a userscript can read and change "
                      "the websites it runs on.")
        warn.setObjectName("Hint")
        warn.setWordWrap(True)
        layout.addWidget(warn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        install = buttons.addButton("Update" if replacing else "Install", QDialogButtonBox.ButtonRole.AcceptRole)
        install.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Installer:
    """Every way into the runtime: links to .user.js files, files on disk, the Chrome Web Store, Arc."""

    def __init__(self, browser, host):
        self.browser = browser
        self.host = host
        self.busy = False  # a Web Store install is downloading or waiting for an answer

    def _error(self, title, message):
        QMessageBox.warning(self.browser, title, message)

    # Userscripts -------------------------------------------------------------------------

    def userscript_from_url(self, url):
        self.browser.toast("Getting the userscript…")
        fetch(self.host.network, url,
              lambda data, final: self.offer_userscript(data.decode("utf-8", errors="replace"), final),
              lambda message: self._error("Couldn't Get the Userscript", message))

    def userscript_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self.browser, "Install Userscript", str(Path.home()),
                                              "Userscripts (*.user.js *.js)")
        if path:
            self.offer_userscript(Path(path).read_text(encoding="utf-8", errors="replace"),
                                  QUrl.fromLocalFile(path).toString())

    def offer_userscript(self, source, origin=""):
        if "==UserScript==" not in source:
            self._error("Not a Userscript", "That file doesn't have a ==UserScript== header.")
            return
        script = userscripts.from_source(source, origin)
        existing = self.host.scripts.get(script.id)
        dialog = UserscriptDialog(script, existing, self.browser.theme, self.browser)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if existing is not None:
            script.enabled = existing.enabled
        self.fetch_requires_and_install(script)

    def fetch_requires_and_install(self, script, quiet=False, remaining=None, loaded=()):
        # @require'd libraries are downloaded once, at install time, and run before the script
        remaining = list(script.requires) if remaining is None else remaining
        if not remaining:
            self.host.install_userscript(script, "\n;\n".join(loaded))
            if not quiet:
                self.browser.toast(f"Installed “{script.name}” — reload a page it runs on to start it")
            return
        url = remaining.pop(0)
        fetch(self.host.network, url,
              lambda data, _final: self.fetch_requires_and_install(
                  script, quiet, remaining, [*loaded, data.decode("utf-8", errors="replace")]),
              lambda message: self._error("Couldn't Install the Userscript",
                                          f"A library it needs didn't download:\n{url}\n\n{message}"))

    def new_userscript(self):
        source = ("// ==UserScript==\n// @name        My Script\n// @match       https://example.com/*\n"
                  "// @grant       none\n// ==/UserScript==\n\nconsole.log('Hello from a userscript');\n")
        self.offer_userscript(source)

    # Chrome extensions -------------------------------------------------------------------

    def confirm_extension(self, ext, replacing=None) -> bool:
        """Chrome's "Add …?" prompt: what the extension will be able to do, and what won't work here."""
        perms = [p for p in ext.manifest.get("permissions", []) if isinstance(p, str)]
        hosts = ext.manifest.get("host_permissions", []) + [p for p in perms if "://" in p or p == "<all_urls>"]
        scripts = {m for cs in ext.manifest.get("content_scripts", []) for m in cs.get("matches", [])}
        # The extension wrote its name and description: escaped, so they can't hide what follows
        lines = [f"<b>{escape(ext.name)}</b> {escape(ext.version)}", escape(ext.description)]
        if replacing is not None:
            lines.append(f"<b>Replaces</b> the installed “{escape(replacing.name)}” {escape(replacing.version)}.")
        sites = sorted(s for s in set(hosts) | scripts if isinstance(s, str))
        if any(s in ("<all_urls>", "*://*/*", "https://*/*", "http://*/*") for s in sites):
            lines.append("It can <b>read and change your data on all websites</b>.")
        elif sites:
            lines.append("It can read and change your data on: " + ", ".join(map(escape, sites[:6])) +
                         (f" and {len(sites) - 6} more" if len(sites) > 6 else ""))
        others = [p for p in perms if "://" not in p and p != "<all_urls>"]
        if others:
            lines.append("Permissions: " + ", ".join(map(escape, others)))
        if ext.unsupported:
            lines.append("<span style='color:#d97706'>May not fully work here: needs " +
                         ", ".join(map(escape, ext.unsupported)) + ".</span>")
        box = QMessageBox(QMessageBox.Icon.Question, "Add Extension", "<br><br>".join(l for l in lines if l),
                          parent=self.browser)
        box.setTextFormat(Qt.TextFormat.RichText)
        add = box.addButton("Add Extension", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(add)
        box.exec()
        return box.clickedButton() is add

    def _install_archive(self, data, ext_id=""):
        try:
            preview = chrome.archive_preview(data)
        except Exception as error:  # bad zip, missing manifest, …
            self._error("Couldn't Add the Extension", str(error))
            return
        replacing = self.host.extensions.get(ext_id or chrome.local_id(preview.name))
        if self.confirm_extension(preview, replacing):
            try:
                self._installed(chrome.install_archive(data, ext_id))
            except Exception as error:
                self._error("Couldn't Add the Extension", str(error))

    def _installed(self, ext_id):
        ext = self.host.add_extension(ext_id)
        note = ""
        if ext.unsupported:
            note = f" (may not fully work: needs {', '.join(ext.unsupported[:3])})"
        self.browser.toast(f"Added “{ext.name}”{note}")
        return ext

    def extension_folder(self):
        path = QFileDialog.getExistingDirectory(self.browser, "Load Unpacked Extension (the folder with "
                                                              "manifest.json)", str(Path.home()))
        if not path:
            return
        if not (Path(path) / "manifest.json").exists():
            self._error("Not an Extension", "That folder doesn't have a manifest.json.")
            return
        try:
            preview = chrome.ChromeExtension.load("", Path(path))
            if self.confirm_extension(preview, self.host.extensions.get(chrome.local_id(preview.name))):
                self._installed(chrome.install_folder(Path(path)))
        except (OSError, ValueError) as error:
            self._error("Couldn't Add the Extension", str(error))

    def extension_file(self):
        path, _ = QFileDialog.getOpenFileName(self.browser, "Install Extension", str(Path.home()),
                                              "Chrome extensions (*.crx *.zip)")
        if path:
            self._install_archive(Path(path).read_bytes())  # a local extension: its file name proves nothing

    def extension_from_store(self, text=None):
        tab = self.browser.current_tab
        if text is None and tab and "chromewebstore.google.com" in tab.url and chrome.web_store_id(tab.url):
            text = tab.url  # already on the extension's store page
        if text is None:
            text, ok = QInputDialog.getText(self.browser, "Install from Chrome Web Store",
                                            "Paste the extension's Chrome Web Store link (or its ID):")
            if not ok:
                return
        ext_id = chrome.web_store_id(text)
        if ext_id is None:
            self._error("No Extension ID", "Couldn't find a 32-letter extension ID in that link.")
            return
        self.browser.toast("Downloading the extension…")
        self.busy = True

        def downloaded(data, _final):
            try:
                self._install_archive(data, ext_id)
            finally:
                self.busy = False

        def failed(message):
            self.busy = False
            self._error("Couldn't Download the Extension", message)
        fetch(self.host.network, chrome.web_store_download_url(ext_id), downloaded, failed)

    def import_from_arc(self):
        found = chrome.arc_extensions()
        if not found:
            self._error("Nothing to Import", "No Arc extensions were found on this Mac.")
            return
        added = []
        for ext_id, folder in found:
            try:
                added.append(self.host.add_extension(chrome.install_folder(folder, ext_id)))
            except (OSError, ValueError):
                continue
        partial = [e.name for e in added if e.unsupported]
        message = f"Imported {len(added)} extension(s) from Arc."
        if partial:
            message += ("\n\nThese use Chrome features this browser doesn't have, so they may not work "
                        "(you can turn them off in Settings → Extensions):\n• " + "\n• ".join(partial))
        QMessageBox.information(self.browser, "Imported from Arc", message)


# --- The toolbar popup ---------------------------------------------------------------------------------

class ExtensionPopup(QFrame):
    """An extension's default_popup, floating under the toolbar button like Chrome's."""

    def __init__(self, browser, ext, anchor: QPoint):
        super().__init__(browser, Qt.WindowType.Popup)
        self.setObjectName("ExtensionPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.anchor = anchor
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = WebView(browser.profile, self)
        browser.feature("extensions").host.attach_page(self.view.page(), extension_page=True)
        layout.addWidget(self.view)
        # Like Chrome, start as small as possible and grow to fit the page; shown once it's sized
        self.resize(POPUP_MIN)
        self.view.loadFinished.connect(lambda ok: self._fit())
        self.view.page().windowCloseRequested.connect(self.close)
        self.view.load(QUrl(ext.url(ext.popup)))
        QTimer.singleShot(1500, self._show)  # a slow or broken popup still appears

    def _fit(self):
        self.view.page().runJavaScript(MEASURE, 0, self._resize)
        QTimer.singleShot(250, self._fit_late)  # late content (a bound method: dropped if we're closed by then)

    def _fit_late(self):
        self.view.page().runJavaScript(MEASURE, 0, self._resize)

    def _resize(self, size):
        if sip.isdeleted(self) or sip.isdeleted(self.view):
            return  # closed while the page was answering
        if size:
            w = max(POPUP_MIN.width(), min(POPUP_MAX.width(), int(size[0])))
            h = max(POPUP_MIN.height(), min(POPUP_MAX.height(), int(size[1])))
            self.resize(w, h)
        self._show()

    def _show(self):
        self._place()
        if not self.isVisible():
            self.show()

    def _place(self):
        screen = self.screen().availableGeometry() if self.screen() else None
        x, y = self.anchor.x(), self.anchor.y()
        if screen is not None:
            x = min(x, screen.right() - self.width() - 8)
            y = min(y, screen.bottom() - self.height() - 8)
        self.move(x, y)


def extensions_menu(browser, menu: QMenu):
    """The puzzle-piece menu: each extension (click for its popup), then management."""
    feature = browser.feature("extensions")
    host = feature.host
    theme = browser.theme
    exts = [e for e in host.extensions.values() if e.enabled]
    for ext in exts:
        icon = QIcon(str(ext.icon_file)) if ext.icon_file else qta.icon("ph.puzzle-piece", color=theme.panel_text)
        badge = host.badge_text(ext, browser.current_tab.url if browser.current_tab else "")
        label = ext.name + (f"   [{badge}]" if badge else "")
        action = menu.addAction(icon, label)
        action.triggered.connect(lambda _=False, e=ext: feature.click_extension(e))
    scripts = [s for s in host.scripts.values() if s.enabled]
    if scripts:
        tab = browser.current_tab
        here = [s for s in scripts if tab and _runs_on(s, tab.url)]
        if exts:
            menu.addSeparator()
        menu.addAction(f"{len(here)} userscript(s) on this page" if here else
                       f"{len(scripts)} userscript(s) on, none here").setEnabled(False)
        for script in here:
            menu.addAction("    " + script.name).setEnabled(False)
    if exts or scripts:
        menu.addSeparator()
    if exts:
        pins = menu.addMenu(qta.icon("ph.push-pin", color=theme.panel_text), "Pin to URL Bar")
        pinned = {e.id for e in feature.pinned_extensions()}
        for ext in exts:
            action = pins.addAction(ext.name)
            action.setCheckable(True)
            action.setChecked(ext.id in pinned)
            action.triggered.connect(lambda _=False, e=ext: feature.toggle_pinned_extension(e))
    menu.addAction(qta.icon("ph.gear-six", color=theme.panel_text), "Manage Extensions…",
                   lambda: browser.open_settings("extensions"))


# --- Context menu items ------------------------------------------------------------------------------

def _contexts(request) -> set[str]:
    found = set()
    if request.selectedText():
        found.add("selection")
    if request.linkUrl().isValid() and not request.linkUrl().isEmpty():
        found.add("link")
    if request.isContentEditable():
        found.add("editable")
    media = {1: "image", 2: "video", 3: "audio"}.get(int(request.mediaType().value))
    if media:
        found.add(media)
    return found | ({"all"} if found else {"all", "page"})  # "page" only when nothing more specific applies


def _item_applies(item, contexts, page_url, request) -> bool:
    if item.get("visible") is False:
        return False
    wanted = set(item.get("contexts") or ["page"])
    if not wanted & contexts:
        return False
    patterns = item.get("documentUrlPatterns")
    if patterns and not matches(page_url, [r for r in map(match_pattern, patterns) if r]):
        return False
    targets = item.get("targetUrlPatterns")
    target = request.linkUrl().toString() or request.mediaUrl().toString()
    if targets and target and not matches(target, [r for r in map(match_pattern, targets) if r]):
        return False
    return True


def add_extension_menu_items(host, view, menu, request):
    page = view.page()
    page_url = page.url().toString()
    contexts = _contexts(request)
    selection = request.selectedText()
    short = (selection[:30] + "…") if len(selection) > 30 else selection
    first = True
    for ext in host.extensions.values():
        items = [i for i in host.menu_items(ext.id) if ext.enabled and _item_applies(i, contexts, page_url, request)]
        if not items:
            continue
        if first:
            menu.addSeparator()
            first = False
        ids = {i["id"] for i in items}
        top = [i for i in items if i.get("parentId") not in ids]
        icon = QIcon(str(ext.icon_file)) if ext.icon_file else QIcon()

        def fill(target, parent_id, level_items):
            for item in level_items:
                if item.get("type") == "separator":
                    target.addSeparator()
                    continue
                title = (item.get("title") or ext.name).replace("%s", short)
                children = [i for i in items if i.get("parentId") == item["id"]]
                if children:
                    fill(target.addMenu(title), item["id"], children)
                    continue
                action = target.addAction(title)
                action.setEnabled(item.get("enabled", True) is not False)
                if item.get("type") in ("checkbox", "radio"):
                    action.setCheckable(True)
                    action.setChecked(bool(item.get("checked")))
                    action.toggled.connect(lambda on, e=ext.id, i=item: host.menu_clicked(e, i, request, page, on))
                else:
                    action.triggered.connect(lambda _=False, e=ext.id, i=item: host.menu_clicked(e, i, request, page))
        # Like Chrome: one item sits in the menu directly, several go in a submenu named for the extension
        if len(top) == 1 and not [i for i in items if i.get("parentId") == top[0]["id"]]:
            fill(menu, None, top)
            menu.actions()[-1].setIcon(icon)
        else:
            sub = menu.addMenu(icon, ext.name)
            fill(sub, None, top)


def _runs_on(script, url) -> bool:
    return matches(url, script.include_regexes, script.exclude_regexes)


# --- Settings → Extensions -------------------------------------------------------------------------------

class ExtensionsPage(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.browser = settings.browser
        self.feature = self.browser.feature("extensions")
        self.host = self.feature.host
        self.installer = self.feature.installer
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        add_script = QPushButton("Add Userscript  ▾")
        script_menu = QMenu(add_script)
        script_menu.addAction("From a Link…", self._script_from_link)
        script_menu.addAction("From a File…", self.installer.userscript_from_file)
        script_menu.addAction("Write a New One…", self.installer.new_userscript)
        add_script.setMenu(script_menu)
        add_ext = QPushButton("Add Extension  ▾")
        ext_menu = QMenu(add_ext)
        ext_menu.addAction("From the Chrome Web Store…", lambda: self.installer.extension_from_store())
        ext_menu.addAction("From a .crx or .zip File…", self.installer.extension_file)
        ext_menu.addAction("Load Unpacked Folder…", self.installer.extension_folder)
        ext_menu.addSeparator()
        ext_menu.addAction("Import from Arc", self.installer.import_from_arc)
        add_ext.setMenu(ext_menu)
        actions.addWidget(add_script)
        actions.addWidget(add_ext)
        updates = QPushButton("Check for Updates")
        updates.clicked.connect(lambda: self.feature.updates.check(manual=True))
        actions.addWidget(updates)
        actions.addStretch(1)
        actions.setContentsMargins(0, 0, 0, 0)
        buttons = QWidget()
        buttons.setLayout(actions)

        self.scripts_title = QLabel("Userscripts")
        self.scripts_title.setObjectName("SettingTitle")
        self.scripts_box = QVBoxLayout()
        self.ext_title = QLabel("Extensions")
        self.ext_title.setObjectName("SettingTitle")
        self.ext_box = QVBoxLayout()
        self.empty = QLabel("Nothing installed yet. Userscripts (from sites like Greasy Fork) install when you "
                            "open a .user.js link. Chrome extensions can be added from the Chrome Web Store or "
                            "imported from Arc.")
        self.empty.setObjectName("Hint")
        self.empty.setWordWrap(True)
        for widget in (self.scripts_title, self.ext_title):
            widget.setContentsMargins(2, 6, 0, 0)
        # A card like the other Settings pages': what this is, the buttons on a line of their own under it
        heading = settings._row(
            "Extensions and userscripts",
            f"Extensions run on {brand.NAME}'s own lightweight runtime, not Chrome's. Content scripts, storage, "
            "messaging, popups, options pages and background scripts work; features that need Chrome's internals "
            "(blocking requests, side panels, native apps…) don't, and extensions that rely on them are marked. "
            "Changes apply to pages loaded afterwards.", None)
        words = heading.layout().itemAt(0).layout()
        words.addSpacing(6)
        words.addWidget(buttons)
        words.addSpacing(4)
        words.addWidget(self.empty)
        layout.addWidget(settings._card([heading]))
        layout.addWidget(self.scripts_title)
        layout.addLayout(self.scripts_box)
        layout.addWidget(self.ext_title)
        layout.addLayout(self.ext_box)
        self.host.changed.connect(self.refresh)

    def _script_from_link(self):
        url, ok = QInputDialog.getText(self, "Add Userscript", "Link to the .user.js file:")
        if ok and url.strip():
            self.installer.userscript_from_url(url.strip())

    @staticmethod
    def _clear(box):
        while box.count():
            item = box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def refresh(self):
        self._clear(self.scripts_box)
        self._clear(self.ext_box)
        scripts = sorted(self.host.scripts.values(), key=lambda s: s.name.lower())
        exts = sorted(self.host.extensions.values(), key=lambda e: e.name.lower())
        if scripts:
            self.scripts_box.addWidget(self.settings._card([self._script_row(s) for s in scripts]))
        if exts:
            self.ext_box.addWidget(self.settings._card([self._ext_row(e) for e in exts]))
        self.scripts_title.setVisible(bool(scripts))
        self.ext_title.setVisible(bool(exts))
        self.empty.setVisible(not scripts and not exts)

    def _toggle(self, on, slot) -> QCheckBox:
        box = QCheckBox()
        box.setChecked(on)
        box.toggled.connect(slot)
        return box

    def _controls(self, *widgets) -> QWidget:
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for w in widgets:
            h.addWidget(w)
        return box

    def _button(self, text, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _script_row(self, script) -> QWidget:
        sites = ", ".join(script.matches[:3]) or "every website"
        detail = f"{script.version}  ·  runs on {sites}".strip(" ·")
        if script.unsupported_grants:
            detail += f"  ·  ⚠︎ unsupported: {', '.join(script.unsupported_grants)}"
        remove = self._button("Remove", lambda: self._remove_script(script))
        toggle = self._toggle(script.enabled, lambda on: self.host.set_userscript_enabled(script.id, on))
        buttons = [remove, toggle]
        if script.origin.startswith("http"):
            buttons.insert(0, self._button("Update", lambda: self.installer.userscript_from_url(script.origin)))
        return self.settings._row(script.name, detail, self._controls(*buttons))

    def _ext_row(self, ext) -> QWidget:
        detail = f"{ext.version}  ·  {ext.description}".strip(" ·")
        if ext.unsupported:
            detail += f"\n⚠︎ May not work fully: needs {', '.join(ext.unsupported)}"
        widgets = []
        if ext.options_page:
            widgets.append(self._button("Options", lambda: self.host.open_options(ext.id)))
        widgets.append(self._button("Remove", lambda: self._remove_ext(ext)))
        widgets.append(self._toggle(ext.enabled, lambda on: self.host.set_extension_enabled(ext.id, on)))
        return self.settings._row(ext.name, detail, self._controls(*widgets))

    def _remove_script(self, script):
        if QMessageBox.question(self, "Remove Userscript", f"Remove “{script.name}” and its saved data?") == \
                QMessageBox.StandardButton.Yes:
            self.host.remove_userscript(script.id)

    def _remove_ext(self, ext):
        if QMessageBox.question(self, "Remove Extension", f"Remove “{ext.name}” and its saved data?") == \
                QMessageBox.StandardButton.Yes:
            self.host.remove_extension(ext.id)


class ExtensionsFeature(Feature):
    """Chrome extensions and userscripts, on our own runtime (Qt's own extension support crashes): the puzzle
    piece and pinned extensions in the address pill, their popups and shortcuts, the Extensions page in
    Settings, installing from the Chrome Web Store, files or Arc, and daily updates."""
    name = "extensions"

    def __init__(self, window):
        super().__init__(window)
        from .host import ExtensionHost
        from .updates import Updater
        self.host = ExtensionHost(window)  # userscripts and Chrome extensions (our own runtime)
        self.installer = Installer(window, self.host)
        self.updates = Updater(window, self.host, self.installer)  # daily update checks
        self._shortcuts = []
        self.host.changed.connect(self.install_shortcuts)
        self.page = None    # (Settings' Extensions page, once it's been made)
        self.chip = None

    def shutdown(self):
        self.host.shutdown()

    def before_backup(self):
        self.host._flush_storage()

    # --- Pages -------------------------------------------------------------------------------------------

    def profile_ready(self, profile):
        self.host.attach_profile(profile)

    def view_created(self, view, tab):
        self.host.attach_page(view.page())

    def page_menu(self, menu, request, view, tab):
        self.host.extend_context_menu(view, menu, request)

    def url_changed(self, tab, url):
        self.host.tab_updated(tab, url=tab.url)

    def load_finished(self, tab, ok):
        self.host.tab_updated(tab, status="complete")

    def tab_shown(self, tab):
        if tab is not None:
            self.host.tab_activated(tab)

    def tab_closed(self, tab):
        self.host.tab_removed(tab)

    def console_message(self, tab, message):
        from .host import STORE_MESSAGE
        if not message.startswith(STORE_MESSAGE):
            return False
        # "Add to Eclipse" on a Chrome Web Store page: only our button knows the secret, and only on the store
        prefix = self.host.store_prefix
        if (message.startswith(prefix) and QUrl(tab.url).host() == "chromewebstore.google.com"
                and not self.installer.busy):
            self.installer.extension_from_store(message[len(prefix):])
        return True

    # --- The puzzle piece and pinned extensions ----------------------------------------------------------

    def pill_buttons(self):
        from PyQt6.QtCore import QPoint, QSize
        from PyQt6.QtWidgets import QHBoxLayout, QToolButton, QWidget
        chip = self.chip = QWidget()
        chip.setProperty("pill_end", True)  # (after the shield, at the end of the pill)
        row = QHBoxLayout(chip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.pinned_row = QHBoxLayout()  # pinned extensions get their own buttons, just left of the puzzle piece
        self.pinned_row.setContentsMargins(0, 0, 0, 0)
        self.pinned_row.setSpacing(0)
        self.pinned_buttons = []
        row.addLayout(self.pinned_row)
        # The puzzle piece, shown once anything is installed; carries a badge like Chrome's
        self.puzzle = QToolButton()
        self.puzzle.setObjectName("ExtButton")
        self.puzzle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.puzzle.setIconSize(QSize(12, 12))
        self.puzzle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.puzzle.setToolTip("Extensions")
        self.puzzle.clicked.connect(lambda: self.show_menu(self.puzzle.mapToGlobal(QPoint(0, self.puzzle.height()))))
        row.addWidget(self.puzzle)
        self.host.badges_changed.connect(lambda: self.update_pill(self.window.current_tab))
        chip.hide()
        return [chip]

    def update_pill(self, tab):
        from PyQt6.QtCore import QSize
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QToolButton
        import qtawesome as qta
        from ..theme import letter_icon
        if self.chip is None:
            return
        window, host = self.window, self.host
        for button in self.pinned_buttons:
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self.pinned_buttons = []
        if not host.extensions and not host.scripts:
            self.chip.hide()
            window.sidebar.fit_pill()
            return
        url = tab.url if tab else ""
        pinned = self.pinned_extensions()
        for ext in pinned:
            button = QToolButton()
            button.setObjectName("ExtButton")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(14, 14))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(QIcon(str(ext.icon_file)) if ext.icon_file else letter_icon(ext.name, window.theme.accent))
            button.setText(host.badge_text(ext, url))
            button.setToolTip(ext.name)
            button.setAccessibleName(ext.name)
            button.clicked.connect(lambda _=False, e=ext, b=button: self.click_extension(e, b))
            self.pinned_row.addWidget(button)
            self.pinned_buttons.append(button)
        # The puzzle piece shows a badge only for extensions that aren't pinned (those show their own)
        badge = next((b for b in (host.badge_text(e, url) for e in host.extensions.values()
                                  if e.enabled and e not in pinned) if b), "")
        self.puzzle.setIcon(qta.icon("ph.puzzle-piece", color=_alpha(window.theme.text, 0.55)))
        self.puzzle.setText(badge)
        self.chip.show()
        window.sidebar.fit_pill()

    def show_menu(self, pos):
        menu = self.window._menu()
        extensions_menu(self.window, menu)
        menu.exec(pos)

    def pinned_extensions(self):
        pinned = self.window.store.settings.get("pinned_extensions", [])
        exts = self.host.extensions
        return [exts[i] for i in pinned if i in exts and exts[i].enabled]

    def toggle_pinned_extension(self, ext):
        store = self.window.store
        pinned = store.settings.setdefault("pinned_extensions", [])
        if ext.id in pinned:
            pinned.remove(ext.id)
        else:
            pinned.append(ext.id)
        store.schedule_save()
        self.update_pill(self.window.current_tab)

    def click_extension(self, ext, button=None):
        from PyQt6.QtCore import QPoint
        window = self.window
        if ext.popup:
            button = button or self.puzzle
            anchor = button.mapToGlobal(QPoint(0, button.height() + 4))
            ExtensionPopup(window, ext, anchor)  # shows itself once sized
        else:  # no popup: the extension handles the click itself (chrome.action.onClicked)
            tab = window.current_tab
            self.host.broadcast("action.clicked:" + ext.id, [self.host.tab_info(tab) if tab else None],
                                only_extension=ext.id)

    # --- Shortcuts, menus, commands, Settings ------------------------------------------------------------

    def install_shortcuts(self):
        """Keyboard shortcuts from extensions' manifests (e.g. ⌘⇧X opens Proton Pass), unless one would take
        over a shortcut the browser already uses."""
        from PyQt6.QtGui import QAction, QKeySequence, QShortcut
        window = self.window
        for shortcut in self._shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._shortcuts = []
        taken = {a.shortcut().toString() for a in window.findChildren(QAction) if not a.shortcut().isEmpty()}
        for ext in self.host.extensions.values():
            if not ext.enabled:
                continue
            for name, keys, _description in ext.commands:
                sequence = QKeySequence(keys)
                if sequence.isEmpty() or sequence.toString() in taken:
                    continue
                taken.add(sequence.toString())
                shortcut = QShortcut(sequence, window)
                shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
                shortcut.activated.connect(lambda e=ext, n=name: self._command(e, n))
                self._shortcuts.append(shortcut)

    def _command(self, ext, name):
        if name in ("_execute_action", "_execute_browser_action", "_execute_page_action"):
            self.click_extension(ext)
            return
        tab = self.window.current_tab
        self.host.broadcast("commands:" + ext.id, [name, self.host.tab_info(tab) if tab else None],
                            only_extension=ext.id)

    def menus(self, add, menus, bar):
        self.install_shortcuts()  # after every menu, so they can't steal a browser shortcut

    def shortcuts_changed(self):
        self.install_shortcuts()  # (one you've given a menu item now is the browser's)

    def commands(self):
        window = self.window
        return [("Extensions & Userscripts", "ph.puzzle-piece", lambda: window.open_settings("extensions")),
                ("Install Extension from Chrome Web Store", "ph.puzzle-piece",
                 lambda: self.installer.extension_from_store()),
                ("Import Extensions from Arc", "ph.arrow-square-in", self.installer.import_from_arc)]

    def settings_pages(self):
        return [("extensions", "ph.puzzle-piece", "Extensions", self._page, "shortcuts")]

    def _page(self, settings):
        self.page = ExtensionsPage(settings)
        return self.page

    def settings_shown(self, settings):
        if self.page is not None:
            self.page.refresh()


def _alpha(color, alpha):
    c = color.__class__(color)
    c.setAlphaF(alpha)
    return c


FEATURE = ExtensionsFeature
