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
