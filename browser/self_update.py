# Keeping the browser itself up to date.
#
# The app runs straight from its git checkout (the .app is a launcher pointing at it), so an
# update is a fast-forward to the branch it tracks on GitHub: once a day it fetches, and if there are
# new commits it offers Update & Restart, which pulls them, installs any new requirements, and
# relaunches.
#
# It never touches your own work: a checkout with uncommitted changes, local commits that aren't
# pushed, or no upstream branch is left alone (a check from Settings says why).

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import brand

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


def inspect(fetch=True) -> dict:
    """Where this checkout stands: {"state": …, "behind": n, "subjects": […], "reason": …}.
    state: "current", "available", "blocked" (an update exists but can't be applied) or "unavailable"."""
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
    return True, "Updated"


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

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._busy = False
        self._manual = False
        self.checked.connect(self._checked)
        self.applied.connect(self._applied)
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
        subjects = "\n".join(f"• {s}" for s in result.get("subjects", []))
        more = result["behind"] - len(result.get("subjects", []))
        if more > 0:
            subjects += f"\n…and {more} more"
        box = QMessageBox(self.browser)
        box.setWindowTitle("Update Available")
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
        threading.Thread(target=lambda: self.applied.emit(*apply_update()), daemon=True).start()  # pip can be slow

    def _applied(self, ok, message):
        if not ok:
            QMessageBox.warning(self.browser, "Couldn't Update", message)
            return
        if message != "Updated":
            QMessageBox.information(self.browser, "Updated", message)
        self.browser.store.save()
        command = relaunch_command()
        # Wait for this process to be gone (it holds the single-instance socket), then start again
        script = 'while kill -0 "$0" 2>/dev/null; do sleep 0.2; done; exec "$@"'
        QProcess.startDetached("/bin/sh", ["-c", script, str(os.getpid()), *command])
        QApplication.instance().quit()
