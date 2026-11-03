# The native forms (browser/forms.py, drawn by native/Sources/Form.swift): what Space Rules, a recipe and a Boost
# describe, and what each makes of the values that come back. Opening them for real is in
# tests/live/test_live_features.py.

from types import SimpleNamespace

from browser import forms
from browser.boosts import Boost, apply_values, boost_sections
from browser.recipes import recipe_from, recipe_sections
from browser.space_rules import rule_from, rule_sections


def _fields(sections):
    return {f["id"]: f for s in sections for f in s["fields"] if f.get("id")}


def test_space_rules_read_back_as_the_qt_dialog_made_them():
    work = SimpleNamespace(name="Work")
    rule = {"days": [0, 2], "start": "08:30", "end": "16:00", "display": "Studio Display", "blocked": ["x.com"]}
    fields = _fields(rule_sections(work, rule, ["Built-in Retina Display"]))
    assert fields["scheduled"]["value"] and fields["days"]["value"] == [0, 2] and fields["start"]["value"] == "08:30"
    assert [o["value"] for o in fields["display"]["options"]] == ["Built-in Retina Display", "Studio Display"]
    assert fields["display"]["value"] == "Studio Display" and fields["blocked"]["value"] == "x.com"
    assert fields["days"]["enabled_by"] == "scheduled" and fields["display"]["enabled_by"] == "on_display"
    back = {"scheduled": True, "days": [4, 0, 9, "x"], "start": "08:30", "end": "16:00", "on_display": True,
            "display": "Studio Display", "blocked": "https://www.Reddit.com/r/all\nx.com"}
    assert rule_from(back) == {"days": [0, 4], "start": "08:30", "end": "16:00", "display": "Studio Display",
                               "blocked": ["reddit.com", "x.com"]}
    # Turned off (or no days): no schedule, no display
    assert rule_from({**back, "scheduled": False, "on_display": False}) == {"blocked": ["reddit.com", "x.com"]}
    assert "days" not in rule_from({**back, "days": []})
    # No displays known and none in the rule: no menu to pick from
    assert "display" not in _fields(rule_sections(work, {}, []))


def test_a_recipe_reads_back_with_only_web_pages_and_a_name():
    spaces = [SimpleNamespace(id="a", name="Home"), SimpleNamespace(id="b", name="Work")]
    fields = _fields(recipe_sections({"name": "Morning", "space": "gone", "urls": ["https://a.example"]}, spaces))
    assert fields["space"]["value"] == "a"  # (its Space deleted: the first)
    assert fields["urls"]["value"] == "https://a.example"
    assert recipe_from({"name": "  ", "space": "b", "urls": "https://a.example\njavascript:alert(1)\nb.example",
                        "split": True, "ask": " what's new? "}) == \
        {"name": "Recipe", "space": "b", "urls": ["https://a.example", "https://b.example"], "split": True,
         "ask": "what's new?"}


def test_a_boost_reads_back_into_itself():
    boost = Boost("example.com", css="a{}", zapped=["#ad", ".banner"])
    fields = _fields(boost_sections(boost))
    assert fields["css"]["kind"] == "code" and fields["zapped"]["value"] == ["#ad", ".banner"]
    apply_values(boost, {"name": " Mine ", "site": "News.Example.org/path", "enabled": False, "css": "b{}",
                         "js": "1", "zapped": [".banner", 3]})
    assert (boost.name, boost.site, boost.enabled, boost.css, boost.js, boost.zapped) == \
        ("Mine", "news.example.org", False, "b{}", "1", [".banner"])
    apply_values(boost, {"site": "not a site at all"})  # (a website that isn't one: the one it had)
    assert boost.site == "news.example.org"


def test_save_buttons_put_a_destructive_one_first_when_asked():
    assert [b["id"] for b in forms.save_buttons()] == ["cancel", "save"]
    delete = forms.save_buttons("Delete Recipe", "Delete it?")
    assert delete[0] == {"id": "delete", "label": "Delete Recipe", "role": "destructive", "confirm": "Delete it?"}
    assert forms.section(None, {"id": "x"}, header="H")["fields"] == [{"id": "x"}]


def test_a_live_folder_reads_back_as_the_qt_dialog_made_it():
    from browser.live_folder_dialog import live_from, live_sections
    fields = _fields(live_sections({"kind": "json", "url": "https://app.example/api/", "auth": "token"}))
    assert fields["kind"]["value"] == "json" and fields["json_url"]["value"] == "https://app.example/api/"
    assert fields["json_auth"]["value"] == "token" and fields["json_token"]["kind"] == "secret"
    assert fields["feed_url"]["value"] == "" and fields["feed_url"]["shown_when"] == {"id": "kind", "is": ["feed"]}
    assert fields["json_token"]["enabled_when"] == {"id": "json_auth", "is": ["token"]}
    assert live_from({"kind": "feed", "feed_url": " example.com ", "name": ""}) == \
        ({"kind": "feed", "url": "https://example.com"}, "", None)
    assert live_from({"kind": "feed", "feed_url": ""}) is None
    assert live_from({"kind": "json", "json_url": "https://a.example/", "json_auth": "token", "json_token": ""}) is None
    live, name, token = live_from({"kind": "json", "json_url": "https://a.example/", "json_auth": "token",
                                   "json_token": " t0k ", "json_header": "", "name": "Mine"})
    assert live["header"] == "Authorization" and token == "t0k" and name == "Mine" and live["title"] == "title"
    assert live_from({"kind": "calendar", "calendar_url": "webcal://cal.example/x.ics"})[0]["url"] == \
        "https://cal.example/x.ics"
    assert live_from({"kind": "page", "page_url": "https://a.example/", "page_selector": ""}) is None
    assert live_from({"kind": "nonsense", "feed_url": "x.example"})[0]["kind"] == "feed"  # (an unknown kind: a feed)


def test_download_settings_read_back_with_only_known_choices():
    from browser.downloads import DEFAULT_SETTINGS, settings_from, settings_sections
    store = SimpleNamespace(settings={"ytdlp": {"max_height": 1080, "cookies_from": "safari"}})
    fields = _fields(settings_sections(store))
    assert fields["max_height"]["value"] == "1080" and fields["folder"]["kind"] == "folder"
    assert {"value": "0", "label": "Best available"} in fields["max_height"]["options"]
    back = settings_from({"folder": "/tmp/x", "template": " ", "kind": "audio", "max_height": "720",
                          "audio_format": "nope", "cookies_from": "", "embed_subs": True, "rate_limit": " 5M "}, store)
    assert back["max_height"] == 720 and back["kind"] == "audio" and back["cookies_from"] == ""
    assert back["audio_format"] == DEFAULT_SETTINGS["audio_format"]  # (not one of its choices: as it was)
    assert back["template"] == DEFAULT_SETTINGS["template"] and back["rate_limit"] == "5M" and back["embed_subs"]


def test_screen_choices_list_screens_then_windows():
    from browser.permissions import screen_choices

    class Model:
        def __init__(self, names):
            self.names = names

        def rowCount(self):
            return len(self.names)

        def index(self, row, _col):
            return row

        def data(self, row):
            return self.names[row]

    request = SimpleNamespace(screensModel=lambda: Model(["Built-in Display"]),
                              windowsModel=lambda: Model(["Notes", None]))
    assert screen_choices(request) == [("screen:0", "Screen: Built-in Display"), ("window:0", "Window: Notes"),
                                       ("window:1", "Window: Window 2")]
