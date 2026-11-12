from browser.air_traffic import route, rule_matches


def test_domains_cover_subdomains_only():
    assert rule_matches("zoom.us", "https://us02web.zoom.us/j/123")
    assert rule_matches("zoom.us", "https://zoom.us/")
    assert not rule_matches("zoom.us", "https://notzoom.us/")
    assert not rule_matches("zoom.us", "https://example.com/?next=zoom.us")


def test_text_rules_and_order():
    rules = [{"match": "docs.google.com/spreadsheets", "space": "work"}, {"match": "google.com", "space": "home"},
             {"match": "", "space": "never"}, {"match": "figma", "space": ""}]
    assert route(rules, "https://docs.google.com/spreadsheets/d/1") == "work"
    assert route(rules, "https://docs.google.com/document/d/1") == "home"
    assert route(rules, "https://www.figma.com/file/x") is None  # a rule with no Space is ignored
    assert route([], "https://zoom.us") is None


def test_a_whole_site_never_matches_another_site_named_after_it():
    """"google.com" matched anywhere in a host: accounts.google.com.evil.io, a page made to look like Google's, went
    into the Space signed in to Google."""
    assert rule_matches("google.com", "https://accounts.google.com/x")
    assert not rule_matches("google.com", "https://accounts.google.com.evil.io/x")
    assert not rule_matches("google.com", "https://google.com.evil/x")
    assert rule_matches("docs.google", "https://docs.google.com/d/1")  # (a part of a name: its ending may follow)
    assert rule_matches("docs.google", "https://docs.google.co.uk/d/1")
    assert not rule_matches("docs.google", "https://docs.google.evil.io/d/1")


def test_a_rule_for_a_deleted_space_lets_the_next_rule_decide():
    rules = [{"match": "google.com", "space": "gone"}, {"match": "docs.google.com", "space": "school"}]
    assert route(rules, "https://docs.google.com/d/1", {"school", "work"}) == "school"
