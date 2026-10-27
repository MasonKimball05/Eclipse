# Keeping the browser itself up to date.
#
# The app runs straight from its git checkout (the .app is a launcher pointing at it), so an
# update is a fast-forward to the branch it tracks on GitHub: once a day it fetches, and if there are
# new commits it offers Update & Restart, which pulls them, installs any new requirements, and
# relaunches.
#
# It never touches your own work: a checkout with uncommitted changes, local commits that aren't
# pushed, or no upstream branch is left alone (a check from Settings says why).
#
# The release app (everything inside it, signed) has no checkout: it updates from GitHub's releases
# instead, through release_update.py, with the same offer.

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import brand, release_update, verified
from .features import Feature

REPO = Path(__file__).resolve().parents[1]
VENV = Path.home() / "Library" / "Application Support" / brand.INTERNAL_NAME / "venv"  # same as run.sh
CHECK_EVERY_SECONDS = 24 * 3600
FIRST_CHECK_MS = 120_000
GIT_TIMEOUT = 60


def _git(*args, timeout=GIT_TIMEOUT) -> tuple[int, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    try:
        done = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout,
                              env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return done.returncode, (done.stdout or done.stderr).strip()


def release_bundle() -> Path | None:
    """The release app this runs from (no checkout around it), or None."""
    return None if (REPO / ".git").exists() else verified.app_bundle(REPO)


def inspect(fetch=True) -> dict:
    """Where this checkout stands: {"state": …, "behind": n, "subjects": […], "reason": …}.
    state: "current", "available", "blocked" (an update exists but can't be applied) or "unavailable"."""
    if (bundle := release_bundle()) is not None:
        return release_update.inspect(bundle) if fetch else {"state": "current", "behind": 0}
    if not (REPO / ".git").exists():
        return {"state": "unavailable", "reason": f"{brand.NAME} isn't running from a git checkout."}
    code, upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code != 0:
        code, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        return {"state": "unavailable", "reason": f"The branch “{branch}” doesn't track one on GitHub."}
    if fetch:
        code, out = _git("fetch", "--quiet", upstream.split("/", 1)[0])
        if code != 0:
            return {"state": "unavailable", "reason": "Couldn't reach GitHub" + (": " + out.splitlines()[-1] if out else "")}
    code, count = _git("rev-list", "--count", "HEAD..@{u}")
    behind = int(count) if code == 0 and count.isdigit() else 0
    if not behind:
        return {"state": "current", "behind": 0}
    _code, log = _git("log", "--format=%s", "-n", "8", "HEAD..@{u}")
    result = {"behind": behind, "subjects": log.splitlines(), "upstream": upstream}
    _code, dirty = _git("status", "--porcelain", "--untracked-files=no")
    ahead_code, _ = _git("merge-base", "--is-ancestor", "HEAD", "@{u}")
    if dirty:
        return {**result, "state": "blocked", "reason": "This checkout has changes that aren't committed."}
    if ahead_code != 0:
        return {**result, "state": "blocked", "reason": "This checkout has commits that aren't on GitHub."}
    return {**result, "state": "available"}


def apply_update() -> tuple[bool, str]:
    """Fast-forward to the upstream branch; reinstall requirements if they changed."""
    _code, before = _git("rev-parse", "HEAD")
    code, out = _git("merge", "--ff-only", "@{u}")
    if code != 0:
        return False, out
    code, changed = _git("diff", "--name-only", before, "HEAD")
    if "requirements.txt" in changed.splitlines():
        pip = VENV / "bin" / "pip"
        if not pip.exists():
            return True, "Updated. New requirements: run ./run.sh once to install them."
        done = subprocess.run([str(pip), "install", "--quiet", "--disable-pip-version-check", "-r",
                               str(REPO / "requirements.txt")], capture_output=True, text=True)
        if done.returncode != 0:
            return True, "Updated, but installing new requirements failed: run ./run.sh to retry."
        # The hash run.sh compares, so it doesn't install them all over again
        (VENV / ".requirements-hash").write_text(
            hashlib.sha1((REPO / "requirements.txt").read_bytes()).hexdigest() + "\n")
    precompile()
    return True, "Updated"


def precompile():
    """Compile any changed code now, so the next launch doesn't spend a couple of seconds doing it
    (Python compiles what's changed the first time it runs it). Quick when nothing has changed."""
    import compileall
    if not (REPO / ".git").exists():
        return  # (a release app: compiled ahead, and sealed by its signature, so nothing's written into it)
    try:
        compileall.compile_dir(str(REPO / "browser"), quiet=2)
    except Exception:  # (never worth failing an update or a quit over)
        pass


def relaunch_command() -> list[str]:
    """How to start this app again: the .app bundle if we're in one, else main.py."""
    exe = Path(sys.executable).resolve()
    bundle = next((p for p in exe.parents if p.suffix == ".app"), None)
    if bundle is not None:
        return ["/usr/bin/open", "-n", str(bundle)]
    return [sys.executable, str(REPO / "main.py")]


class SelfUpdater(QObject):
    checked = pyqtSignal(dict)
    applied = pyqtSignal(bool, str)
    progressed = pyqtSignal(int)  # (a release's download, in percent)

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._busy = False
        self._manual = False
        self._found = {}
        self._staged = None  # (a release: the checked new copy, swapped in after quitting)
        self.checked.connect(self._checked)
        self.applied.connect(self._applied)
        self.progressed.connect(lambda percent: self.browser.toast(f"Downloading the update… {percent}%"))
        self._timer = QTimer(self, interval=3600 * 1000, timeout=self._maybe_check)
        self._timer.start()
        QTimer.singleShot(FIRST_CHECK_MS, self._maybe_check)

    @property
    def settings(self):
        return self.browser.store.settings

    def _maybe_check(self):
        if not self.settings.get("app_updates", True) or os.environ.get("PYBROWSER_DATA_DIR"):
            return  # a throwaway profile (tests, --fresh) never updates the checkout
        if time.time() - self.settings.get("app_update_checked_at", 0) >= CHECK_EVERY_SECONDS:
            self.check()

    def check(self, manual=False):
        if self._busy:
            return
        self._busy, self._manual = True, manual
        self.settings["app_update_checked_at"] = time.time()
        self.browser.store.schedule_save()
        if manual:
            self.browser.toast("Checking for updates…")
        threading.Thread(target=lambda: self.checked.emit(inspect()), daemon=True).start()

    def _checked(self, result):
        self._busy = False
        state = result.get("state")
        if state == "available":
            self._offer(result)
        elif self._manual:
            if state == "current":
                self.browser.toast(f"{brand.NAME} is up to date")
            elif state == "blocked":
                QMessageBox.information(self.browser, "Update Available",
                                        f"There {'is' if result['behind'] == 1 else 'are'} {result['behind']} new "
                                        f"commit{'s' if result['behind'] != 1 else ''} on {result['upstream']}, "
                                        f"but it can't update itself: {result['reason']}\n\nPull it yourself "
                                        f"(git pull) when you're ready.")
            else:
                self.browser.toast(result.get("reason") or "Couldn't check for updates")

    def _offer(self, result):
        self._found = result
        subjects = "\n".join(f"• {s}" for s in result.get("subjects", []))
        more = result["behind"] - len(result.get("subjects", []))
        if more > 0:
            subjects += f"\n…and {more} more"
        box = QMessageBox(self.browser)
        box.setWindowTitle("Update Available")
        if "build" in result:  # (a release)
            size = f", {result['size'] // 1_000_000} MB" if result.get("size") else ""
            box.setText(f"{result['version']} is ready{size}.")
        else:
            box.setText(f"A new version of {brand.NAME} is ready ({result['behind']} "
                        f"change{'s' if result['behind'] != 1 else ''}).")
        box.setInformativeText(subjects + "\n\nYour tabs come back after the restart.")
        update = box.addButton("Update && Restart", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is update:
            self.update_and_restart()

    def update_and_restart(self):
        self.browser.toast(f"Updating {brand.NAME}…")
        if (bundle := release_bundle()) is not None and "build" in self._found:
            threading.Thread(target=lambda: self._prepare(bundle), daemon=True).start()  # (a few hundred MB)
            return
        threading.Thread(target=lambda: self.applied.emit(*apply_update()), daemon=True).start()  # pip can be slow

    def _prepare(self, bundle):
        shown = [-1]

        def progress(fraction):
            percent = int(fraction * 100) // 10 * 10
            if percent != shown[0]:
                shown[0] = percent
                self.progressed.emit(percent)
        self._staged, why = release_update.prepare(self._found, bundle, progress)
        self.applied.emit(self._staged is not None, why or "Updated")

    def _applied(self, ok, message):
        if not ok:
            QMessageBox.warning(self.browser, "Couldn't Update", message)
            return
        if message != "Updated":
            QMessageBox.information(self.browser, "Updated", message)
        self.browser.store.save()
        if (bundle := release_bundle()) is not None and self._staged is not None:
            swap = release_update.swap_command(bundle, self._staged, os.getpid())
            QProcess.startDetached(swap[0], swap[1:])
            QApplication.instance().quit()
            return
        command = relaunch_command()
        # Wait for this process to be gone (it holds the single-instance socket), then start again
        script = 'while kill -0 "$0" 2>/dev/null; do sleep 0.2; done; exec "$@"'
        QProcess.startDetached("/bin/sh", ["-c", script, str(os.getpid()), *command])
        QApplication.instance().quit()


class UpdatesFeature(Feature):
    """Updates from GitHub: the daily look, Check Now in Settings, and the ⌘T command."""
    name = "self_update"

    def __init__(self, window):
        super().__init__(window)
        self.updater = SelfUpdater(window)
        if release_bundle() is not None and not os.environ.get("PYBROWSER_DATA_DIR"):
            threading.Thread(target=release_update.tidy, daemon=True).start()  # (what the last update left)

    def check(self):
        self.updater.check(manual=True)

    def commands(self):
        return [(f"Check for {brand.NAME} Updates", "ph.arrows-clockwise", self.check)]


FEATURE = UpdatesFeature
