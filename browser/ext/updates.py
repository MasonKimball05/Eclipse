# Keeping extensions and userscripts up to date, once a day (and on demand from Settings).
#
#   - Extensions with a Chrome Web Store id (including everything imported from Arc) are checked
#     against the Web Store's update service, which answers with the latest version and its .crx.
#   - Userscripts installed from a link are re-downloaded from it, and replaced if the @version
#     is newer.
#
# An update that asks for nothing new installs quietly (with a toast); one that wants more
# permissions, sites or GM_* grants asks first, like Chrome does.

import re
import time
import xml.etree.ElementTree as ElementTree

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QMessageBox

from . import chrome, userscripts
from ..theme import plain_tip
from .ui import fetch

CHECK_EVERY_SECONDS = 24 * 3600
FIRST_CHECK_MS = 60_000  # a minute after launch, out of the way of startup
UPDATE_CHECK_URL = ("https://clients2.google.com/service/update2/crx?response=updatecheck&acceptformat=crx3"
                    "&prodversion=140.0&x=id%3D{id}%26v%3D{version}%26uc")


def version_key(version: str) -> tuple:
    """"1.10.2" > "1.9", compared number by number ("1.2" equals "1.2.0"); a pre-release ("2.0-beta.3")
    comes before its release."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)(.*)", version or "")
    if not m:
        return ((0,), 1, ())
    numbers = tuple(int(n) for n in m.group(1).split("."))
    numbers += (0,) * (4 - len(numbers))
    rest = m.group(2)
    pre = bool(re.match(r"\s*[-_.+~]?\s*(alpha|beta|preview|pre|dev|rc|a|b)(?![a-z])", rest, re.IGNORECASE))
    return (numbers, 0 if pre else 1, tuple(int(n) for n in re.findall(r"\d+", rest)))


def is_newer(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def parse_update_check(xml: bytes) -> tuple[str, str] | None:
    """(version, .crx url) from the Web Store's update answer, or None if there's no update."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    for element in root.iter():
        if element.tag.endswith("updatecheck") and element.get("status") == "ok" and element.get("codebase"):
            return element.get("version", ""), element.get("codebase")
    return None


def _extension_asks(manifest: dict) -> set[str]:
    perms = set(p for p in manifest.get("permissions", []) if isinstance(p, str))
    perms |= set(manifest.get("host_permissions", []))
    perms |= {m for cs in manifest.get("content_scripts", []) for m in cs.get("matches", [])}
    return perms


def new_permissions(old: dict, new: dict) -> set[str]:
    return _extension_asks(new) - _extension_asks(old)


class Updater(QObject):
    def __init__(self, browser, host, installer):
        super().__init__(browser)
        self.browser = browser
        self.host = host
        self.installer = installer
        self.store = browser.store
        self._timer = QTimer(self, interval=3600 * 1000, timeout=self._maybe_check)  # hourly: is a day up?
        self._timer.start()
        QTimer.singleShot(FIRST_CHECK_MS, self._maybe_check)
        self._outstanding = 0
        self._report: list[str] = []
        self._manual = False

    def _maybe_check(self):
        if time.time() - self.store.settings.get("extensions_checked_at", 0) >= CHECK_EVERY_SECONDS:
            self.check()

    def check(self, manual=False):
        """Look for updates to everything installed. manual: say so when nothing was found."""
        self.store.settings["extensions_checked_at"] = time.time()
        self.store.schedule_save()
        self._manual, self._report = manual, []
        targets = [e for e in self.host.extensions.values() if re.fullmatch(r"[a-p]{32}", e.id)]
        scripts = [s for s in self.host.scripts.values() if s.origin.startswith(("http://", "https://"))]
        self._outstanding = len(targets) + len(scripts)
        if not self._outstanding:
            self._finished()
        for ext in targets:
            url = UPDATE_CHECK_URL.format(id=ext.id, version=ext.version or "0")
            fetch(self.host.network, url, lambda data, _u, e=ext: self._extension_answer(e, data),
                  lambda _message: self._done_one())
        for script in scripts:
            fetch(self.host.network, script.origin,
                  lambda data, _u, s=script: self._script_answer(s, data.decode("utf-8", errors="replace")),
                  lambda _message: self._done_one())

    def _done_one(self):
        self._outstanding -= 1
        if self._outstanding <= 0:
            self._finished()

    def _finished(self):
        if self._report:
            self.browser.toast("Updated " + ", ".join(self._report))
        elif self._manual:
            self.browser.toast("Everything is up to date")

    # Extensions -------------------------------------------------------------------------------

    def _extension_answer(self, ext, xml):
        found = parse_update_check(xml)
        if found is None or not is_newer(found[0], ext.version):
            self._done_one()
            return
        fetch(self.host.network, found[1], lambda data, _u: self._install_extension(ext, data),
              lambda _message: self._done_one())

    def _install_extension(self, ext, data):
        try:
            preview = chrome.archive_preview(data)
            added = new_permissions(ext.manifest, preview.manifest)
            if added and not self._ask(f"“{ext.name}” {preview.version} wants new permissions",
                                       "It will also be able to use: " + ", ".join(sorted(added))):
                return
            self.host.add_extension(chrome.install_archive(data, ext.id), enabled=ext.enabled)
            self._report.append(f"{preview.name} to {preview.version}")
        except Exception:
            pass  # a broken download is skipped; tomorrow's check tries again
        finally:
            self._done_one()

    # Userscripts --------------------------------------------------------------------------------

    def _script_answer(self, script, source):
        try:
            fresh = userscripts.from_source(source, script.origin)
            if "==UserScript==" not in source or not is_newer(fresh.version, script.version):
                return
            more = sorted((set(fresh.grants) - set(script.grants)) | (set(fresh.matches) - set(script.matches)))
            # Losing its @match lines means every page; losing an @exclude means more pages
            if script.matches and not fresh.matches:
                more.append("every website")
            more += [f"no longer excluded from {e}" for e in sorted(set(script.excludes) - set(fresh.excludes))]
            if script.noframes and not fresh.noframes:
                more.append("running inside embedded frames")
            if more and not self._ask(f"“{script.name}” {fresh.version} wants to do more",
                                      "It now also asks for: " + ", ".join(more)):
                return
            # From plain http, anyone on the network could have changed it on the way: always ask
            if not more and script.origin.startswith("http://") and not self._ask(
                    f"“{script.name}” {fresh.version} is available",
                    "It comes from an insecure (http) address, so it may have been changed on the way here."):
                return
            fresh.id, fresh.enabled = script.id, script.enabled  # same script: keep its stored values
            self.installer.fetch_requires_and_install(fresh, quiet=True)
            self._report.append(f"{fresh.name} to {fresh.version}")
        finally:
            self._done_one()

    def _ask(self, title, detail) -> bool:
        # (as text, never HTML: the name and version come from the script's own update file)
        answer = QMessageBox.question(self.browser, "Update Available", plain_tip(f"{title}.\n\n{detail}\n\nUpdate it?"))
        return answer == QMessageBox.StandardButton.Yes
