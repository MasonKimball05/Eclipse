import datetime
import os
import subprocess

import pytest

from browser.integrations import (DEFAULTS, config, daybook_url, job_tracker_url, live_folder_presets, local_clone,
                                  search_shortcut_presets, summarize, without_thinking)
from browser.live_folders import parse_json_items, repo_state, scan_repos, upcoming_events, web_url



@pytest.fixture(autouse=True)
def as_the_maker(monkeypatch):
    """These tests are of the maker's own apps, whoever runs them (the last one is of anyone else's copy)."""
    from browser import member_card
    monkeypatch.setattr(member_card, "_maker", True)


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


def test_config_defaults_and_overrides():
    cfg = config({"my_apps": {"jobtracker": "", "homebase": "http://hb.test:8090/"}})
    assert cfg["jobtracker"] == "" and cfg["parliament"] == DEFAULTS["parliament"]
    names = [p["name"] for p in live_folder_presets(cfg)]
    assert "Job Radar" not in names and "Away Tasks" in names  # a cleared address leaves that app out
    away = next(p for p in live_folder_presets(cfg) if p["name"] == "Away Tasks")
    assert away["url"] == "http://hb.test:8090/api/away"


def test_presets_parse_their_apps_answers():
    cfg = config({})
    by_name = {p["name"]: p for p in live_folder_presets(cfg)}
    radar = by_name["Job Radar"]
    rows = [{"id": 1, "company": "Acme", "title": "Engineer", "url": "https://jobs.example/1"}]
    assert parse_json_items(rows, radar["items"], radar["title"], radar["link"]) == [("Engineer · Acme", "https://jobs.example/1")]
    follow = by_name["Follow Ups"]
    assert parse_json_items([{"id": 9, "company": "Acme", "role": "SWE"}], follow["items"], follow["title"],
                            follow["link"]) == [("Acme · SWE", DEFAULTS["jobtracker"] + "/applications/9")]
    parl = by_name["Parliament"]
    answer = {"notifications": [{"title": "New vote", "link": "/legislation/4/"}], "unread_count": 1}
    assert parse_json_items(answer, parl["items"], parl["title"], parl["link"], "https://am-parliament.org/api/n/") == \
        [("New vote", "https://am-parliament.org/legislation/4/")]
    away = by_name["Away Tasks"]
    tasks = {"mode": "on", "tasks": [{"id": "1023-1", "project": "Parliament-Dev", "text": "Add tests", "status": "done"}]}
    assert parse_json_items(tasks, away["items"], away["title"], away["link"]) == \
        [("done · Parliament-Dev · Add tests", DEFAULTS["homebase"] + "/#away-1023-1")]


def test_the_homebase_preset_lists_its_apps_and_how_each_is_doing():
    hb = {p["id"]: p for p in live_folder_presets(config({}))}["homebase-apps"]
    assert hb["name"] == "Homebase" and hb["url"] == DEFAULTS["homebase"] + "/api/status"
    status = {"host": "arkans-pc1", "apps": [
        {"name": "jobtracker", "title": "Job Tracker", "state": "running", "url": "http://desk:8001/"},
        {"name": "gradtrack", "title": "gradtrack", "state": "crashed", "url": "http://desk:8002/"},
        {"name": "worker", "title": "Worker", "state": "running"}]}  # (no page: nothing to open)
    assert parse_json_items(status, hb["items"], hb["title"], hb["link"]) == [
        ("Job Tracker · running", "http://desk:8001/"), ("gradtrack · crashed", "http://desk:8002/")]


def test_search_shortcut_presets():
    assert {e["keyword"] for e in search_shortcut_presets(config({}))} == {"parl", "mygh"}


def test_homebase_summary():
    status = {"apps": [{"state": "running", "health": {"ok": True}}, {"state": "running", "health": {"ok": False}},
                       {"state": "external"}, {"state": "crashed"}]}
    assert summarize(status)[:2] == (2, 4)


def test_qwen_thinking_is_hidden():
    assert without_thinking("<think>hmm, the user wants…</think>\n\nA mutex is a lock.") == "A mutex is a lock."
    assert without_thinking("<think>still going") == ""


def test_send_to_links():
    assert job_tracker_url(config({}), "https://jobs.example/p?id=1&x=2") == \
        DEFAULTS["jobtracker"] + "/new?url=https%3A%2F%2Fjobs.example%2Fp%3Fid%3D1%26x%3D2"
    assert daybook_url("Read this", "https://a.example/").toString().startswith("daybook://add-task?text=Read")


def test_repo_radar(tmp_path):
    clean, dirty = tmp_path / "clean", tmp_path / "Dirty"
    for repo in (clean, dirty):
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        (repo / "a.txt").write_text("1")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "one")
        _git(repo, "remote", "add", "origin", f"git@github.com:me/{repo.name}.git")
    (dirty / "a.txt").write_text("2")
    (dirty / "new.txt").write_text("x")
    assert repo_state(str(clean)) == (0, 0) and repo_state(str(dirty)) == (2, 0)
    assert scan_repos(str(tmp_path)) == [("Dirty · 2 changed", "https://github.com/me/Dirty")]
    assert os.path.samefile(local_clone("https://github.com/someone/dirty/pull/3", str(tmp_path)), dirty)  # any case
    assert local_clone("https://gitlab.com/me/Dirty", str(tmp_path)) is None


def test_web_url():
    assert web_url("git@github.com:me/repo.git") == "https://github.com/me/repo"
    assert web_url("https://user:tok@github.com/me/repo.git") == "https://github.com/me/repo"  # never the token


def test_calendar_feed():
    ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART;VALUE=DATE:20261101\r\nSUMMARY:Thesis due\r\n"
           "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:b\r\nDTSTART:20200101T100000Z\r\nSUMMARY:Long ago\r\nEND:VEVENT\r\n"
           "END:VCALENDAR\r\n")
    items = upcoming_events(ics, "https://grad.example/calendar.ics", now=datetime.datetime(2026, 10, 24))
    assert items == [("Nov 1 · Thesis due", "https://grad.example/calendar.ics#event-a")]


def test_homebase_alerts_only_for_real_changes():
    from browser.integrations import transitions
    up = {"apps": [{"name": "jt", "title": "Job Tracker", "state": "running", "health": {"ok": True}},
                   {"name": "sh", "title": "Shelf", "state": "running"}]}
    down = {"apps": [{"name": "jt", "title": "Job Tracker", "state": "crashed"},
                     {"name": "sh", "title": "Shelf", "state": "stopped", "last_exit": {"requested": True}}]}
    assert transitions(up, down) == [("Job Tracker", False)]  # Shelf was stopped on purpose
    assert ("Job Tracker", True) in transitions(down, up)
    assert transitions(None, up) == [] and transitions(up, None) == []  # unreachable isn't an app going down


def test_up_next():
    from browser.upnext import next_event, when_text
    now = datetime.datetime(2026, 10, 23, 14, 0, tzinfo=datetime.timezone.utc)
    feed = {"today": [
        {"title": "Done", "start": "2026-10-23T12:00:00Z", "end": "2026-10-23T13:00:00Z", "allDay": False},
        {"title": "Holiday", "start": "2026-10-23T00:00:00Z", "end": "2026-10-24T00:00:00Z", "allDay": True},
        {"title": "Officers", "start": "2026-10-23T14:20:00Z", "end": "2026-10-23T15:00:00Z", "allDay": False,
         "place": "Room 2 · https://us02web.zoom.us/j/123?pwd=x."}]}
    event = next_event(feed, now)
    assert event["title"] == "Officers" and event["link"] == "https://us02web.zoom.us/j/123?pwd=x"
    assert when_text(event, now).endswith("in 20 min")
    live = next_event(feed, datetime.datetime(2026, 10, 23, 14, 30, tzinfo=datetime.timezone.utc))
    assert live["live"] and when_text(live, datetime.datetime(2026, 10, 23, 14, 30, tzinfo=datetime.timezone.utc)) \
        == "Now · 30 min left"
    assert next_event(feed, datetime.datetime(2026, 10, 23, 16, 0, tzinfo=datetime.timezone.utc)) is None


def test_job_postings_are_recognised():
    from browser.integrations import is_job_posting
    assert is_job_posting("https://www.linkedin.com/jobs/view/4012345678/")
    assert is_job_posting("https://boards.greenhouse.io/acme/jobs/1234567")
    assert is_job_posting("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/Engineer_R123")
    assert not is_job_posting("https://www.linkedin.com/feed/")
    assert not is_job_posting("https://github.com/jobs")


def test_issue_to_away_queue():
    from browser.integrations import away_task_text, github_issue, matching_projects
    assert github_issue("https://github.com/MasonKimball05/Parliament/issues/12#issuecomment-1") == \
        ("MasonKimball05", "Parliament", 12)
    assert github_issue("https://github.com/a/b/pull/3") is None
    assert matching_projects("Parliament", ["homebase", "Loom", "Parliament-Dev"]) == ["Parliament-Dev"]
    assert matching_projects("loom", ["homebase", "Loom", "Loom-old"]) == ["Loom"]  # an exact name wins
    text = away_task_text("me", "Loom", 7, "Crash on save", "Steps:\n1. Save", "https://github.com/me/Loom/issues/7")
    assert text.startswith("GitHub issue #7 in me/Loom: Crash on save") and text.endswith("/issues/7")


def test_site_changes():
    from browser.integrations import site_changes
    ok = [{"name": "parliament", "url": "https://am-parliament.org", "status": "ok", "results": []}]
    bad = [{"name": "parliament", "url": "https://am-parliament.org", "status": "fail",
            "results": [{"check": "status", "status": "fail", "detail": "502 in 30ms"}]}]
    assert site_changes(ok, bad) == ["am-parliament.org: 502 in 30ms"]
    assert site_changes(bad, ok) == ["am-parliament.org is fine again"]
    assert site_changes(None, bad) == [] and site_changes(ok, ok) == []



def test_anyone_elses_copy_has_only_the_general_parts(monkeypatch):
    from browser import member_card
    from browser.integrations import GENERAL, ollama_url
    monkeypatch.setattr(member_card, "_maker", False)
    cfg = config({})
    assert cfg == GENERAL and not any(cfg[k] for k in ("homebase", "parliament", "jobtracker", "daybook", "editor"))
    assert [p["id"] for p in live_folder_presets(cfg)] == ["repo-radar"]
    assert search_shortcut_presets(cfg) == []
    assert ollama_url(cfg) == "http://localhost:11434"
    mine = config({"my_apps": {"github_user": "someone", "ollama": "http://gpu.local:11434/"}})
    assert {e["keyword"] for e in search_shortcut_presets(mine)} == {"mygh"} and ollama_url(mine) == "http://gpu.local:11434"
    # The maker's copy goes through Homebase's proxy
    monkeypatch.setattr(member_card, "_maker", True)
    assert ollama_url(config({})) == DEFAULTS["homebase"] + "/ollama"
