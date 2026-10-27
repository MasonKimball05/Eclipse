# GitHub, through the GitHub CLI (`gh`, with the account you're signed in to):
#
# A pull request's status, on its own page: a chip in the URL pill says whether its checks pass, fail or
# are still running, and what review has decided (or that it's merged, closed or a draft). Click it for
# each check, and open one. It's read with the GitHub CLI (`gh pr view`), so private repos work with the
# account you're signed in to, and kept a minute; while checks are running it looks again every 30
# seconds, as long as you're on that page.
#
# Your repos in ⌘T: type a few letters of one's name and it's offered, to open on GitHub. The list is
# kept in the data folder and read again, in the background, when ⌘T opens and it's over an hour old.

import json
import time

from PyQt6 import sip
from PyQt6.QtCore import QObject, QProcess, QTimer, QUrl, pyqtSignal

from . import storage
from .downloads import find_tool

FIELDS = "state,isDraft,reviewDecision,mergeable,statusCheckRollup"
FRESH_S = 60          # an answer is good for this long (and gh failing isn't asked again for as long)
PENDING_POLL_MS = 30_000
MAX_POLLS = 60        # half an hour of looking at one PR's running checks, then it waits for you to come back
GH_TIMEOUT_MS = 30_000

REPOS_FILE = "github_repos.json"
REPOS_FRESH_S = 3600
REPOS_RETRY_S = 600
REPO_FIELDS = "name,nameWithOwner,url,description,isPrivate,pushedAt"

PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}


def github_pull(url: str) -> tuple[str, str, int] | None:
    """(owner, repo, number) for any page of a GitHub pull request (its files, its checks…)."""
    q = QUrl(url)
    parts = [p for p in q.path().split("/") if p]
    if q.host().lower() in ("github.com", "www.github.com") and len(parts) >= 4 and parts[2] == "pull" \
            and parts[3].isdigit():
        return parts[0], parts[1], int(parts[3])
    return None


def check_state(check: dict) -> str:
    """"pass", "fail" or "running" for one entry of statusCheckRollup (a check run or a commit status)."""
    if check.get("__typename") == "StatusContext":
        state = (check.get("state") or "").upper()
        return "pass" if state == "SUCCESS" else "fail" if state in FAILED else "running"
    if (check.get("status") or "").upper() != "COMPLETED":
        return "running"
    conclusion = (check.get("conclusion") or "").upper()
    return "pass" if conclusion in PASSED else "fail" if conclusion in FAILED else "pass"


def checks(data: dict) -> list[tuple[str, str, str]]:
    """(name, state, link) for each check, failing ones first, then running, then passed."""
    out = []
    for c in data.get("statusCheckRollup") or []:
        name = c.get("name") or c.get("context") or "Check"
        if c.get("workflowName") and c.get("workflowName") != name:
            name = f"{c['workflowName']} · {name}"
        out.append((name, check_state(c), c.get("detailsUrl") or c.get("targetUrl") or ""))
    order = {"fail": 0, "running": 1, "pass": 2}
    return sorted(out, key=lambda c: order[c[1]])


def summarize(data: dict) -> tuple[str, str, str]:
    """(chip text, its colour: pass/fail/running/merged/closed/draft/none, a tooltip) for a PR."""
    state = (data.get("state") or "").upper()
    if state == "MERGED":
        return "Merged", "merged", "This pull request has been merged"
    if state == "CLOSED":
        return "Closed", "closed", "This pull request was closed without merging"
    found = checks(data)
    failing = sum(1 for c in found if c[1] == "fail")
    running = sum(1 for c in found if c[1] == "running")
    review = {"APPROVED": "Approved", "CHANGES_REQUESTED": "Changes requested",
              "REVIEW_REQUIRED": "Review required"}.get((data.get("reviewDecision") or "").upper(), "")
    parts, tips = [], []
    if failing:
        parts.append(f"✕ {failing} failing")
        color = "fail"
    elif running:
        parts.append(f"● {running} running")
        color = "running"
    elif found:
        parts.append(f"✓ {len(found)}")
        color = "pass"
    else:
        color = "none"
    if found:
        tips.append(f"Checks: {len(found) - failing - running} passed, {failing} failing, {running} running")
    if data.get("isDraft"):
        parts.insert(0, "Draft")
        color = "draft" if color in ("none", "pass") else color
    if review:
        parts.append(review)
        tips.append(f"Review: {review.lower()}")
        if color == "none":
            color = "pass" if review == "Approved" else "running"
    if (data.get("mergeable") or "").upper() == "CONFLICTING":
        parts.append("Conflicts")
        tips.append("It has merge conflicts with its base branch")
        color = "fail"
    if not parts:
        return "Open", "none", "No checks or reviews yet"
    return " · ".join(parts), color, "\n".join(tips) + "\nClick for each check"


class PullStatus(QObject):
    """Answers from `gh pr view`, kept a minute. changed(url) when a new one arrives for a page."""
    changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._answers: dict[tuple, tuple[float, dict]] = {}
        self._failed: dict[tuple, float] = {}  # when gh last couldn't say (not signed in, no access…)
        self._asking: set[tuple] = set()
        self._polls = 0
        self._poll = QTimer(self, singleShot=True, interval=PENDING_POLL_MS)
        self._polling_url = ""
        self._poll.timeout.connect(lambda: self.lookup(self._polling_url, force=True))

    def lookup(self, url: str, force=False) -> dict | None:
        """What's known about the PR at `url` now (None until gh answers); asks again if it's stale."""
        pull = github_pull(url)
        if pull is None:
            return None
        known = self._answers.get(pull)
        recently_failed = time.time() - self._failed.get(pull, 0) < FRESH_S
        if (force or known is None or time.time() - known[0] > FRESH_S) and pull not in self._asking \
                and (force or not recently_failed):
            self._ask(pull, url)
        return known[1] if known else None

    def watch(self, url: str, data: dict | None):
        """You're on `url`, whose PR is `data` (lookup's answer): while its checks are running, look again
        now and then (one page at a time; anywhere else, stop)."""
        if data is not None and data.get("state", "OPEN") == "OPEN" \
                and any(state == "running" for _name, state, _link in checks(data)):  # (merged or closed: settled)
            if self._polling_url != url:
                self._polling_url, self._polls = url, 0
            if not self._poll.isActive() and self._polls < MAX_POLLS:
                self._polls += 1
                self._poll.start()
        else:
            self._poll.stop()
            self._polling_url = ""

    def _ask(self, pull, url):
        gh = find_tool("gh")
        if gh is None:
            return
        owner, repo, number = pull
        self._asking.add(pull)
        process = QProcess(self)

        def finished(code, _status):
            if sip.isdeleted(process) or sip.isdeleted(self):
                return  # (the app's closing: Qt ends the process on its way out)
            self._asking.discard(pull)
            output = bytes(process.readAllStandardOutput()).decode(errors="replace")
            process.deleteLater()
            try:
                data = json.loads(output) if code == 0 else None
            except ValueError:
                data = None
            if isinstance(data, dict):
                self._answers[pull] = (time.time(), data)
                self._failed.pop(pull, None)
                self.changed.emit(url)
            else:
                self._failed[pull] = time.time()
                if self._polling_url == url and self._polls < MAX_POLLS:  # (a poll that failed: try again later)
                    self._polls += 1
                    self._poll.start()
        process.finished.connect(finished)
        QTimer(process, singleShot=True, interval=GH_TIMEOUT_MS, timeout=process.kill).start()  # (stuck: it fails)
        process.errorOccurred.connect(lambda error: error == QProcess.ProcessError.FailedToStart and (
            self._asking.discard(pull), self._failed.__setitem__(pull, time.time()), process.deleteLater()))
        process.start(gh, ["pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", FIELDS])


def matching_repos(repos: list[dict], query: str, limit=3) -> list[dict]:
    """Your repos whose name has `query` in it (ignoring case, "-" and "_"): names that start with it
    first, then the most recently pushed."""
    norm = lambda text: text.lower().replace("-", "").replace("_", "").replace(" ", "")
    q = norm(query)
    if len(q) < 2:
        return []
    found = [r for r in repos if q in norm(r.get("name", ""))]
    found.sort(key=lambda r: r.get("pushedAt") or "", reverse=True)
    found.sort(key=lambda r: not norm(r.get("name", "")).startswith(q))  # (stable: pushed order stays)
    return found[:limit]


class GitHubRepos(QObject):
    """Your repos (`gh repo list`), for ⌘T. `repos` is what's known now; changed() when a fresh list arrives."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        saved = storage.read_json(storage.data_dir() / REPOS_FILE) or {}
        self.repos: list[dict] = saved.get("repos") or []
        self.fetched: float = saved.get("fetched") or 0
        self._process = None

    def refresh_if_stale(self):
        if time.time() - self.fetched < REPOS_FRESH_S or self._process is not None:
            return
        gh = find_tool("gh")
        if gh is None:
            return
        process = self._process = QProcess(self)

        def finished(code, _status):
            if sip.isdeleted(process) or sip.isdeleted(self):
                return  # (the app's closing: Qt ends the process on its way out)
            self._process = None
            output = bytes(process.readAllStandardOutput()).decode(errors="replace")
            process.deleteLater()
            try:
                repos = json.loads(output) if code == 0 else None
            except ValueError:
                repos = None
            if not isinstance(repos, list):  # (not signed in, offline…: try again in ten minutes)
                self.fetched = time.time() - REPOS_FRESH_S + REPOS_RETRY_S
            else:
                self.repos, self.fetched = [r for r in repos if isinstance(r, dict) and r.get("url")], time.time()
                storage.write_json(storage.data_dir() / REPOS_FILE, {"fetched": self.fetched, "repos": self.repos})
                self.changed.emit()
        process.finished.connect(finished)
        QTimer(process, singleShot=True, interval=GH_TIMEOUT_MS, timeout=process.kill).start()  # (stuck: it fails)
        process.errorOccurred.connect(lambda error: error == QProcess.ProcessError.FailedToStart and (
            setattr(self, "_process", None), process.deleteLater()))
        process.start(gh, ["repo", "list", "--limit", "500", "--json", REPO_FIELDS])
