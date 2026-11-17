# Linear and Jira Live Folders: reading their answers, the form, and what's sent (never to a real account).

import json
from types import SimpleNamespace

import pytest

from browser import live_folders
from browser.live_folder_dialog import live_from


def test_linear_issues_and_its_errors():
    answer = {"data": {"viewer": {"assignedIssues": {"nodes": [
        {"identifier": "ENG-12", "title": "Fix the sync", "url": "https://linear.app/team/issue/ENG-12/fix-the-sync"},
        {"identifier": "ENG-13", "title": "Not a link", "url": "javascript:alert(1)"}]}}}}
    assert live_folders.parse_linear(json.dumps(answer).encode()) == [
        ("ENG-12 · Fix the sync", "https://linear.app/team/issue/ENG-12/fix-the-sync")]
    with pytest.raises(ValueError, match="Authentication required"):
        live_folders.parse_linear(json.dumps({"errors": [{"message": "Authentication required"}]}).encode())
    with pytest.raises(ValueError):
        live_folders.parse_linear(b"<html>")


def test_jira_issues_its_site_and_its_errors():
    site = live_folders.jira_site("Team.atlassian.net/")
    assert site == "https://team.atlassian.net" and live_folders.jira_site("http://team.atlassian.net") == ""
    answer = {"issues": [{"key": "APP-7", "fields": {"summary": "Crash on launch"}},
                         {"key": "../../evil", "fields": {"summary": "a path, not a key"}}]}
    assert live_folders.parse_jira(json.dumps(answer).encode(), site) == [
        ("APP-7 · Crash on launch", "https://team.atlassian.net/browse/APP-7")]
    with pytest.raises(ValueError, match="Jira: The value 'XYZ' does not exist"):
        live_folders.parse_jira(json.dumps({"errorMessages": ["The value 'XYZ' does not exist"]}).encode(), site)


def test_the_form_makes_linear_and_jira_folders_with_their_tokens():
    live, name, token = live_from({"kind": "linear", "linear_token": "lin_api_test"})
    assert (live["kind"], live["auth"], name, token) == ("linear", "token", "Linear", "lin_api_test")
    live, name, token = live_from({"kind": "jira", "jira_site": "team.atlassian.net", "jira_email": "a@example.com",
                                   "jira_token": "t0ken", "jira_jql": "project = APP"})
    assert live["site"] == "https://team.atlassian.net" and live["jql"] == "project = APP" and token == "t0ken"
    assert live_from({"kind": "jira", "jira_site": "team.atlassian.net", "jira_email": "", "jira_token": "t"}) is None
    assert live_folders.reaches({"kind": "jira", "site": "https://team.atlassian.net"}) == \
        "token:https://team.atlassian.net"
    assert live_folders.reaches({"kind": "linear"}) == "token:" + live_folders.LINEAR_API


def test_what_jira_and_linear_are_sent():
    sent = []
    fake = SimpleNamespace(_token=lambda folder, done: "t0ken",
                           _get=lambda url, done, fail, headers=None, secret=False: sent.append((url, headers, secret)),
                           _post=lambda url, body, done, fail, headers=None: sent.append((url, body, headers)))
    folder = SimpleNamespace(id="f", live={"kind": "jira", "site": "https://team.atlassian.net", "email": "a@example.com"})
    live_folders.LiveFolders._jira(fake, folder, lambda *a, **k: None)
    url, headers, secret = sent[-1]
    assert url.startswith("https://team.atlassian.net/rest/api/3/search/jql?jql=assignee%20%3D%20currentUser()")
    import base64
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"a@example.com:t0ken").decode() and secret
    live_folders.LiveFolders._linear(fake, SimpleNamespace(id="g", live={"kind": "linear"}), lambda *a, **k: None)
    url, body, headers = sent[-1]
    assert url == "https://api.linear.app/graphql" and headers["Authorization"] == "t0ken"
    assert "assignedIssues" in json.loads(body)["query"]


def test_a_new_linear_or_jira_folder_needs_its_token():
    from browser.live_folder_dialog import LiveFoldersFeature
    made = []
    me = SimpleNamespace(create_live_folder=lambda live, name, token: made.append(name))
    assert "token" in LiveFoldersFeature._made(me, ({"kind": "linear", "auth": "token", "url": "https://linear.app"},
                                                     "Linear", None))
    assert LiveFoldersFeature._made(me, ({"kind": "linear", "auth": "token", "url": "https://linear.app"},
                                         "Linear", "lin_api_test")) is None and made == ["Linear"]
