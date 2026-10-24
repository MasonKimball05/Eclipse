from browser.ext import dnr


def _index(*rules, ext="ext"):
    index = dnr.RuleIndex()
    index.add(ext, [dict(id=i + 1, **r) for i, r in enumerate(rules)])
    return index


def _req(url, initiator="news.example", type_="script", method="get"):
    return dnr.Request(url, url.split("/")[2].split(":")[0], initiator, type_, method)


def _kind(index, request):
    hit = index.match(request)
    return hit and hit[0].kind


def test_url_filter_syntax():
    assert dnr.url_filter_regex("||ads.com^") == r"^[a-z][a-z0-9+.-]*://(?:[^/?#]*\.)?ads\.com(?:[^a-zA-Z0-9_.%-]|$)"
    rule = dnr.Rule("e", 1, 1, {"type": "block"}, {"urlFilter": "|https://x.com/*/ad.js|"})
    assert rule.matches(_req("https://x.com/a/b/ad.js"))
    assert not rule.matches(_req("https://x.com/a/b/ad.json"))


def test_domain_rules_cover_subdomains_only():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "||tracker.com^"}})
    assert _kind(index, _req("https://tracker.com/p.gif")) == "block"
    assert _kind(index, _req("https://cdn.tracker.com/p.gif")) == "block"
    assert _kind(index, _req("https://nottracker.com/p.gif")) is None
    # main_frame is only matched when a rule lists it
    assert _kind(index, _req("https://tracker.com/", "", "main_frame")) is None


def test_word_index_and_case():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "-consent-banner."}})
    assert _kind(index, _req("https://cdn.site.com/js/cookie-consent-banner.js")) == "block"
    assert _kind(index, _req("https://cdn.site.com/js/COOKIE-CONSENT-BANNER.JS")) == "block"
    assert _kind(index, _req("https://cdn.site.com/js/consentbanner.js")) is None


def test_priority_and_allow_beats_block():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "||ads.com^"}},
                   {"action": {"type": "allow"}, "condition": {"urlFilter": "||ads.com/ok^"}},
                   {"action": {"type": "block"}, "priority": 5, "condition": {"urlFilter": "||ads.com/ok/bad"}})
    assert _kind(index, _req("https://ads.com/x.js")) == "block"
    assert _kind(index, _req("https://ads.com/ok/x.js")) == "allow"
    assert _kind(index, _req("https://ads.com/ok/bad.js")) == "block"  # higher priority wins


def test_conditions():
    index = _index({"action": {"type": "block"}, "condition": {
        "requestDomains": ["cdn.net"], "initiatorDomains": ["shop.com"], "resourceTypes": ["image"],
        "domainType": "thirdParty"}})
    assert _kind(index, _req("https://a.cdn.net/p.png", "www.shop.com", "image")) == "block"
    assert _kind(index, _req("https://a.cdn.net/p.png", "www.shop.com", "script")) is None
    assert _kind(index, _req("https://a.cdn.net/p.png", "blog.org", "image")) is None
    methods = _index({"action": {"type": "block"}, "condition": {"urlFilter": "/collect", "requestMethods": ["post"]}})
    assert _kind(methods, _req("https://x.com/collect?a", method="post")) == "block"
    assert _kind(methods, _req("https://x.com/collect?a", method="get")) is None


def test_redirects():
    rules = _index(
        {"action": {"type": "redirect", "redirect": {"extensionPath": "/noop.js"}},
         "condition": {"urlFilter": "||ga.com/analytics.js"}},
        {"action": {"type": "redirect", "redirect": {"regexSubstitution": "https://\\1.org/\\2"}},
         "condition": {"regexFilter": "^https://www\\.(\\w+)\\.com/(.*)$", "resourceTypes": ["main_frame"]}},
        {"action": {"type": "redirect", "redirect": {"transform": {"queryTransform": {"removeParams": ["utm_source"]}}}},
         "condition": {"urlFilter": "utm_source=", "resourceTypes": ["main_frame"]}},
        {"action": {"type": "upgradeScheme"}, "condition": {"urlFilter": "||plain.com^", "resourceTypes": ["image"]}})
    ext_url = lambda ext, path: f"pbext://{ext}{path}"

    def target(request):
        rule, match = rules.match(request)
        return dnr.redirect_target(rule, request, match, ext_url)
    assert target(_req("https://ga.com/analytics.js")) == "pbext://ext/noop.js"
    assert target(_req("https://www.example.com/a/b", "", "main_frame")) == "https://example.org/a/b"
    assert target(_req("https://x.io/p?utm_source=a&id=2", "", "main_frame")) == "https://x.io/p?id=2"
    assert target(_req("http://plain.com/i.png", type_="image")) == "https://plain.com/i.png"


def test_header_rules_and_bad_rules_ignored():
    index = _index({"action": {"type": "modifyHeaders", "requestHeaders": [{"header": "DNT", "operation": "set",
                                                                            "value": "1"}]},
                    "condition": {"urlFilter": "||site.com^"}},
                   {"action": {"type": "block"}, "condition": {"regexFilter": "(unclosed"}},
                   {"condition": {"urlFilter": "no-action"}})
    assert len(index.header_rules(_req("https://site.com/x"))) == 1
    assert index.match(_req("https://site.com/(unclosed")) is None
    assert index.count == 1  # the rule without an action, and the one whose regex won't compile, are skipped


def test_plain_domain_rules_share_objects():
    index = _index(*({"action": {"type": "block"}, "condition": {"urlFilter": f"||d{i}.com^",
                                                                  "isUrlFilterCaseSensitive": False}}
                     for i in range(100)))
    assert len({id(r) for r in index.by_domain.values()}) == 1
    assert _kind(index, _req("https://d42.com/x")) == "block"


def test_regex_substitution_keeps_the_rest_of_the_url():
    # Chrome's own example: only the matched part changes
    index = _index({"action": {"type": "redirect", "redirect": {"regexSubstitution": "https://\\1.xyz.com/"}},
                    "condition": {"regexFilter": "^https://www\\.(abc|def)\\.xyz\\.com/", "resourceTypes": ["main_frame"]}})
    request = _req("https://www.abc.xyz.com/path?q=1", "", "main_frame")
    rule, match = index.match(request)
    assert dnr.redirect_target(rule, request, match, None) == "https://abc.xyz.com/path?q=1"
    assert dnr._substitute("\\\\x\\1", __import__("re").search("(b)", "abc")) == "\\xb"


def test_query_transform_leaves_other_params_alone():
    qt = {"removeParams": ["utm_source"], "addOrReplaceParams": [{"key": "ref", "value": "a b", "replaceOnly": True}]}
    assert dnr._transform_query("q=%E2%9C%93&utm_source=x&b=1+2", qt) == "q=%E2%9C%93&b=1+2"
    assert dnr._transform_query("q=%E2%9C%93", qt) == "q=%E2%9C%93"  # nothing to change: identical
    assert dnr._transform_query("ref=old&x=1", qt) == "ref=a%20b&x=1"


def test_priorities_only_compete_within_an_extension():
    index = dnr.RuleIndex()
    index.add("blocker", [{"id": 1, "action": {"type": "block"}, "condition": {"urlFilter": "||ads.com^"}}])
    index.add("helper", [{"id": 1, "priority": 99, "action": {"type": "allow"}, "condition": {"urlFilter": "||ads.com^"}}])
    rule, _ = index.match(_req("https://ads.com/x.js"))
    assert rule.ext_id == "blocker" and rule.kind == "block"  # another extension's allow can't cancel a block
    assert set(index.match_each(_req("https://ads.com/x.js"))) == {"blocker", "helper"}


def test_permit_can_rule_out_redirects():
    index = _index({"action": {"type": "redirect", "redirect": {"url": "https://elsewhere/"}},
                    "condition": {"urlFilter": "||site.com^"}})
    assert index.match(_req("https://site.com/"), permit=lambda rule, request: False) is None
    assert index.match(_req("https://site.com/"), permit=lambda rule, request: True) is not None


def test_excluded_types_include_main_frame():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "||x.com^", "excludedResourceTypes": ["image"]}})
    assert _kind(index, _req("https://x.com/", "", "main_frame")) == "block"
    assert _kind(index, _req("https://x.com/a.png", type_="image")) is None


def test_partial_host_filters_still_match():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "||ads."}},
                   {"action": {"type": "block"}, "condition": {"urlFilter": "||tracker.com*/pixel"}})
    assert _kind(index, _req("https://ads.example.com/x")) == "block"
    assert _kind(index, _req("https://tracker.com/a/pixel.gif")) == "block"


def test_malformed_rules_are_rejected():
    bad = [{"id": 1, "action": "block"}, {"id": 2, "action": {"type": "nuke"}},
           {"id": 3, "action": {"type": "block"}, "condition": {"requestDomains": "x.com"}},
           {"id": 4, "action": {"type": "block"}, "condition": {"regexFilter": 5}},
           {"id": 5, "action": {"type": "redirect", "redirect": {"regexSubstitution": "\\3"}},
            "condition": {"regexFilter": "(a)"}}, "not a rule"]
    assert all(dnr.validate_rule(r) for r in bad)
    index = dnr.RuleIndex()
    index.add("e", bad)
    assert index.count == 0


def test_old_condition_names_still_work():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "track", "domains": ["news.example"]}})
    assert _kind(index, _req("https://cdn.example/track.js")) == "block"
    assert _kind(index, _req("https://cdn.example/track.js", initiator="other.example")) is None


def test_bad_redirects_are_refused():
    assert dnr.validate_rule({"id": 1, "action": {"type": "redirect", "redirect": {}}, "condition": {}})
    assert dnr.validate_rule({"id": 1, "action": {"type": "redirect", "redirect": {"url": "javascript:alert(1)"}},
                              "condition": {}})
    assert dnr.validate_rule({"id": 1, "action": {"type": "redirect", "redirect": {"extensionPath": 5}},
                              "condition": {}})
    assert dnr.validate_rule({"id": 1, "action": {"type": "redirect", "redirect": {"url": "https://x.example/"}},
                              "condition": {}}) is None


def test_unterminated_host_filter_matches_longer_hosts():
    index = _index({"action": {"type": "block"}, "condition": {"urlFilter": "||example.co"}})
    assert _kind(index, _req("https://example.com/a.js")) == "block"
