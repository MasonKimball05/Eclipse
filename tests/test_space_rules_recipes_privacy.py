# The rules behind Space rules, tab recipes and the Privacy Report. The window side of each is in
# tests/live/test_live_features.py.

import datetime

from browser import space_rules
from browser.privacy_report import PrivacyLog, owner, report_html
from browser.recipes import web_urls


def test_a_schedule_counts_its_days_and_hours_even_past_midnight():
    work = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}
    monday = datetime.datetime(2026, 10, 26, 9, 0)
    assert space_rules.on_schedule(work, monday)
    assert not space_rules.on_schedule(work, monday.replace(hour=8, minute=59))
    assert not space_rules.on_schedule(work, monday.replace(hour=17))  # (the end's when it's over)
    assert not space_rules.on_schedule(work, datetime.datetime(2026, 10, 31, 10))  # Saturday
    late = {"days": [4], "start": "22:00", "end": "02:00"}  # Friday night
    assert space_rules.on_schedule(late, datetime.datetime(2026, 10, 30, 23))
    assert space_rules.on_schedule(late, datetime.datetime(2026, 10, 31, 1))  # (Saturday, early: still Friday's)
    assert not space_rules.on_schedule(late, datetime.datetime(2026, 10, 31, 23))
    assert not space_rules.on_schedule({}, monday) and not space_rules.on_schedule({"blocked": ["x.com"]}, monday)


def test_blocked_sites_take_their_subdomains_and_any_way_of_typing_them():
    assert space_rules.clean_sites("https://www.Reddit.com/r/all\nnews.example.com, x.com\n\nnot a site\nx.com") == \
        ["reddit.com", "news.example.com", "x.com"]
    rule = {"blocked": ["reddit.com"]}
    assert space_rules.blocks(rule, "old.reddit.com") and space_rules.blocks(rule, "www.reddit.com")
    assert not space_rules.blocks(rule, "notreddit.com") and not space_rules.blocks(rule, "")


def test_a_recipe_keeps_only_web_pages():
    assert web_urls("example.com\nhttps://a.example/x\njavascript:alert(1)\nfile:///etc/passwd\n\nexample.com") == \
        ["https://example.com", "https://a.example/x"]
    assert len(web_urls([f"https://e.example/{i}" for i in range(40)])) == 12


def test_the_privacy_log_counts_by_site_and_company_and_forgets_after_30_days(tmp_path):
    log = PrivacyLog(tmp_path / "privacy_report.json")
    for tracker in ("doubleclick.net", "doubleclick.net", "google-analytics.com", "tracker.example"):
        log.tracker("news.example", tracker)
    log.tracker("shop.example", "facebook.net")
    log.cleaned("news.example")
    old = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
    log.days[old] = {"news.example": {"trackers": {"ancient.example": 9}, "cleaned": 3}}
    log.prune()
    log.save()
    again = PrivacyLog(tmp_path / "privacy_report.json")
    here = again.report("news.example")
    assert here["trackers"] == {"doubleclick.net": 2, "google-analytics.com": 1, "tracker.example": 1}
    assert here["cleaned"] == 1 and here["days"] == 1
    everywhere = again.report()
    assert everywhere["sites"] == {"news.example": 4, "shop.example": 1}
    assert owner("doubleclick.net") == owner("google-analytics.com") == "Google" and owner("x.example") == "x.example"
    page = report_html(everywhere, None)
    assert "<b style='font-size:22px'>5</b> trackers" in page and "from <b>3</b> companies" in page
    assert page.index("Google") < page.index("Meta")  # (most first)
    assert "Sites that tried hardest" in page and "1</b> link had tracking" in page
    assert "Nothing blocked yet" in report_html(again.report("quiet.example"), "quiet.example")
    assert "&lt;b&gt;" in report_html({"trackers": {"<b>": 1}, "cleaned": 0, "sites": {}, "days": 1}, "x")
    again.clear()
    assert PrivacyLog(tmp_path / "privacy_report.json").report()["trackers"] == {}


def test_private_windows_blocker_blocks_but_never_reports(qapp, data_dir):
    from PyQt6.QtCore import QObject, QUrl
    from browser import storage
    from browser.adblock import AdBlocker
    from browser.models import Store

    class Request:
        def __init__(self, url, first):
            self.url, self.first, self.blocked = QUrl(url), QUrl(first), False
        requestUrl = lambda self: self.url
        firstPartyUrl = lambda self: self.first
        resourceType = lambda self: 1  # (a script)
        requestMethod = lambda self: b"GET"
        setHttpHeader = redirect = lambda self, *_: None
        block = lambda self, on: setattr(self, "blocked", on)
    owner_ = QObject()
    main = AdBlocker(Store(storage.session_path()), owner_)
    from browser.adblock import OffTheRecord
    private = OffTheRecord(main)
    reported = []
    main.tracker_blocked.connect(lambda *a: reported.append(a))
    private.tracker_blocked.connect(lambda *a: reported.append(a))
    there, here = Request("https://doubleclick.net/ad.js", "https://news.example/"), \
        Request("https://doubleclick.net/ad.js", "https://secret.example/")
    main.interceptRequest(there)
    private.interceptRequest(here)
    qapp.processEvents()
    assert there.blocked and here.blocked
    assert reported == [("news.example", "doubleclick.net")]  # (nothing of secret.example)
    assert main.count_for("https://secret.example/") == 1  # (the shield still counts it, while Eclipse runs)
