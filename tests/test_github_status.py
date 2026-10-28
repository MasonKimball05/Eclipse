import pytest
from browser.github_status import check_state, checks, github_pull, summarize


def _run(name, status="COMPLETED", conclusion="SUCCESS", workflow="CI"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion,
            "detailsUrl": f"https://github.com/o/r/actions/runs/1/job/{name}", "workflowName": workflow}


def test_github_pull_finds_any_page_of_a_pull_request():
    assert github_pull("https://github.com/o/r/pull/9") == ("o", "r", 9)
    assert github_pull("https://github.com/o/r/pull/9/files#diff-1") == ("o", "r", 9)
    for url in ("https://github.com/o/r/issues/9", "https://github.com/o/r/pull/new", "https://gitlab.com/o/r/pull/9",
                "https://github.com/o/r"):
        assert github_pull(url) is None, url


def test_check_states_for_runs_and_commit_statuses():
    assert check_state(_run("a")) == "pass" and check_state(_run("a", conclusion="SKIPPED")) == "pass"
    assert check_state(_run("a", conclusion="FAILURE")) == "fail"
    assert check_state(_run("a", status="IN_PROGRESS", conclusion="")) == "running"
    status = {"__typename": "StatusContext", "context": "ci/circle", "state": "PENDING"}
    assert check_state(status) == "running" and check_state({**status, "state": "ERROR"}) == "fail"


def test_checks_list_failing_first_with_their_links():
    found = checks({"statusCheckRollup": [_run("lint"), _run("test", conclusion="FAILURE"),
                                          _run("build", status="QUEUED", conclusion="")]})
    assert [(n, s) for n, s, _ in found] == [("CI · test", "fail"), ("CI · build", "running"), ("CI · lint", "pass")]
    assert found[0][2].endswith("/job/test")


def test_summaries():
    assert summarize({"state": "MERGED"})[:2] == ("Merged", "merged")
    assert summarize({"state": "CLOSED"})[:2] == ("Closed", "closed")
    assert summarize({"state": "OPEN", "statusCheckRollup": [_run("a"), _run("b")]})[:2] == ("✓ 2", "pass")
    failing = summarize({"state": "OPEN", "reviewDecision": "APPROVED",
                         "statusCheckRollup": [_run("a"), _run("b", conclusion="FAILURE")]})
    assert failing[:2] == ("✕ 1 failing · Approved", "fail")
    running = summarize({"state": "OPEN", "statusCheckRollup": [_run("a", status="IN_PROGRESS", conclusion="")]})
    assert running[:2] == ("● 1 running", "running")
    assert summarize({"state": "OPEN", "isDraft": True})[:2] == ("Draft", "draft")
    assert summarize({"state": "OPEN", "mergeable": "CONFLICTING", "statusCheckRollup": [_run("a")]})[:2] == \
        ("✓ 1 · Conflicts", "fail")
    assert summarize({"state": "OPEN", "reviewDecision": "CHANGES_REQUESTED"})[:2] == ("Changes requested", "running")
    assert summarize({"state": "OPEN"})[:2] == ("Open", "none")


def test_matching_repos_by_name():
    from browser.github_status import matching_repos
    repos = [{"name": "Parliament-New", "pushedAt": "2026-10-26"}, {"name": "parliament-snapshot", "pushedAt": "2026-09-01"},
             {"name": "Old-Parliament", "pushedAt": "2026-10-27"}, {"name": "gradtrack", "pushedAt": "2026-10-20"}]
    # Starting with it first (most recently pushed first among them), then containing it
    assert [r["name"] for r in matching_repos(repos, "parl")] == ["Parliament-New", "parliament-snapshot",
                                                                  "Old-Parliament"]
    assert [r["name"] for r in matching_repos(repos, "parliament new")] == ["Parliament-New"]
    assert [r["name"] for r in matching_repos(repos, "grad_track")] == ["gradtrack"]
    assert matching_repos(repos, "p") == [] and matching_repos(repos, "zzz") == []


def test_repos_are_kept_in_the_data_folder(data_dir):
    from browser import storage
    from browser.github_status import REPOS_FILE, GitHubRepos
    storage.write_json(storage.data_dir() / REPOS_FILE, {"fetched": 5, "repos": [{"name": "x", "url": "https://github.com/me/x"}]})
    repos = GitHubRepos()
    assert repos.repos[0]["name"] == "x" and repos.fetched == 5


@pytest.mark.mac
def test_a_failing_gh_isnt_asked_again_at_once_and_polling_stops(qapp, monkeypatch):
    import browser.github_status as gs
    status = gs.PullStatus()
    asked = []
    monkeypatch.setattr(status, "_ask", lambda pull, url: asked.append(pull))
    url = "https://github.com/o/r/pull/1"
    status.lookup(url)
    status._failed[("o", "r", 1)] = __import__("time").time()  # (as _ask records it)
    status.lookup(url)
    assert asked == [("o", "r", 1)]
    status.lookup(url, force=True)  # Check Again still asks
    assert len(asked) == 2
    running = {"state": "OPEN", "statusCheckRollup": [{"name": "t", "status": "QUEUED", "conclusion": ""}]}
    for _ in range(gs.MAX_POLLS + 5):  # a check that never finishes
        status.watch(url, running)
        status._poll.stop()  # (as if it fired)
    assert status._polls == gs.MAX_POLLS
    status.watch(url, running)
    assert not status._poll.isActive()  # it's given up until you come back


def _fake_gh(tmp_path, monkeypatch, answer: str, code=0, delay=0.0):
    """A `gh` that prints `answer` (after `delay` seconds) and exits with `code`, wherever gh is looked for."""
    import browser.github_status as gs
    import browser.live_folders as lf
    gh = tmp_path / "gh"
    (tmp_path / "answer.json").write_text(answer)
    gh.write_text(f"#!/bin/sh\nsleep {delay}\ncat '{tmp_path / 'answer.json'}'\nexit {code}\n")
    gh.chmod(0o755)
    for module in (gs, lf):
        monkeypatch.setattr(module, "find_tool", lambda name: str(gh))
    return gh


def _wait(qapp, condition, seconds=5):
    import time
    end = time.time() + seconds
    while time.time() < end and not condition():
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.mark.mac
def test_pull_status_through_a_real_process(qapp, tmp_path, monkeypatch):
    import json
    from browser.github_status import PullStatus
    _fake_gh(tmp_path, monkeypatch, json.dumps({"state": "MERGED"}))
    status = PullStatus()
    got = []
    status.changed.connect(got.append)
    url = "https://github.com/o/r/pull/3"
    assert status.lookup(url) is None
    assert _wait(qapp, lambda: got) and status.lookup(url) == {"state": "MERGED"}


@pytest.mark.mac
def test_pull_status_failing_and_stuck(qapp, tmp_path, monkeypatch):
    import browser.github_status as gs
    _fake_gh(tmp_path, monkeypatch, "not signed in", code=1)
    status = gs.PullStatus()
    url = "https://github.com/o/r/pull/3"
    status.lookup(url)
    assert _wait(qapp, lambda: ("o", "r", 3) in status._failed and not status._asking)
    # One that never answers is stopped after the time limit, and can be asked again
    _fake_gh(tmp_path, monkeypatch, "{}", delay=30)
    monkeypatch.setattr(gs, "GH_TIMEOUT_MS", 300)
    status.lookup(url, force=True)
    assert ("o", "r", 3) in status._asking
    assert _wait(qapp, lambda: not status._asking, 5)


@pytest.mark.mac
def test_repo_list_through_a_real_process(qapp, tmp_path, monkeypatch, data_dir):
    import json
    from browser.github_status import GitHubRepos
    _fake_gh(tmp_path, monkeypatch, json.dumps([{"name": "x", "url": "https://github.com/me/x"}]))
    repos = GitHubRepos()
    repos.refresh_if_stale()
    assert _wait(qapp, lambda: repos.repos) and repos.repos[0]["name"] == "x"
    again = GitHubRepos()  # (kept in the data folder)
    assert again.repos == repos.repos


@pytest.mark.mac  # (a /bin/sh stand-in for gh)
def test_live_folders_run_gh_for_real(qapp, tmp_path, monkeypatch):
    import json
    import browser.live_folders as lf
    from PyQt6.QtCore import QObject
    owner = QObject()
    _fake_gh(tmp_path, monkeypatch, json.dumps([{"title": "t", "url": "u"}]))
    rows, errors = [], []
    lf.LiveFolders._gh(owner, ["search", "prs"], rows.extend, errors.append)
    assert _wait(qapp, lambda: rows or errors) and rows == [{"title": "t", "url": "u"}]
    _fake_gh(tmp_path, monkeypatch, "{}", delay=30)
    monkeypatch.setattr(lf, "GH_TIMEOUT_MS", 300)
    lf.LiveFolders._gh(owner, ["search", "prs"], rows.extend, errors.append)
    assert _wait(qapp, lambda: errors, 5)  # (stuck: given up on, as a failure)


def test_a_merged_pr_isnt_polled_for_its_leftover_checks(qapp):
    import browser.github_status as gs
    status = gs.PullStatus()
    leftover = {"state": "MERGED", "statusCheckRollup": [{"name": "t", "status": "IN_PROGRESS", "conclusion": ""}]}
    status.watch("https://github.com/o/r/pull/1", leftover)
    assert not status._poll.isActive()


def test_a_gh_that_cant_start_isnt_started_again_at_once(qapp, tmp_path, monkeypatch):
    import browser.github_status as gs
    broken = tmp_path / "gh"
    broken.write_text("not a program")  # (not executable: it can't start)
    monkeypatch.setattr(gs, "find_tool", lambda name: str(broken))
    status = gs.PullStatus()
    status.lookup("https://github.com/o/r/pull/7")
    assert _wait(qapp, lambda: ("o", "r", 7) in status._failed)
    assert ("o", "r", 7) not in status._asking
