import datetime

from browser.answers import answer, calculate, color, convert, world_time
from browser.command_bar import parse_site_search, site_for_tab, site_url, use_custom_sites
from browser.models import Tab
from browser.sidebar import is_away


def test_math():
    assert calculate("23*4+7").text == "99"
    assert calculate("2^10").text == "1,024"
    assert calculate("15% of 80").text == "12"
    assert calculate("sqrt(16) + 1").text == "5"
    assert calculate("1/3").text.startswith("0.333")


def test_not_math():
    for q in ("2024", "python 3.12", "pi", "1/0", "9**9**9", "facebook", "covid-19", "__import__('os')"):
        assert calculate(q) is None, q


def test_conversions():
    assert convert("5 km in miles").text == "3.107 mi"
    assert convert("72 f in c").text == "22.22 °C"
    assert convert("1 gb in mib").text == "953.7 MiB"
    assert convert("3 cups to ml").text == "709.8 mL"
    assert convert("5 km in kg") is None  # different kinds of thing


def test_world_time():
    noon_utc = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.timezone.utc)
    assert world_time("time in tokyo", noon_utc).text.startswith("9:00 PM")
    assert world_time("london time", noon_utc).text.startswith("12:00 PM")
    assert world_time("time in atlantis", noon_utc) is None


def test_colors():
    assert color("#7c5cff").detail.startswith("rgb(124, 92, 255)")
    assert color("#fff").text == "#ffffff"
    assert color("facade") is None and color("beef") is None  # words, not colors


def test_answer_picks_one():
    assert answer("10 lb to kg").kind == "unit"
    assert answer("how to cook rice") is None


def test_custom_search_shortcuts():
    try:
        use_custom_sites([{"name": "Parliament", "keyword": "parl", "url": "https://parl.example/search?q=%s"},
                          {"name": "Bad", "keyword": "bad", "url": "javascript:alert(%s)"},
                          {"name": "Mine", "keyword": "yt", "url": "https://tube.example/?q=%s"}])
        site, terms = parse_site_search("parl budget vote")
        assert site_url(site, terms) == "https://parl.example/search?q=budget%20vote"
        assert site_for_tab("bad") is None  # only web addresses
        assert site_url(site_for_tab("yt"), "x") == "https://tube.example/?q=x"  # yours win over a built-in
    finally:
        use_custom_sites([])
    assert site_for_tab("yt") == "youtube"


def test_pinned_tab_away_from_home():
    tab = Tab("https://mail.google.com/mail/u/0/#inbox", "Gmail", home_url="https://mail.google.com/mail/u/0/")
    assert not is_away(tab)  # only the fragment changed
    tab.url = "https://mail.google.com/mail/u/0/?tab=rm"
    assert not is_away(tab)  # a query the page added itself
    tab.url = "https://mail.google.com/mail/u/0/settings"
    assert is_away(tab)
    tab.url = "https://accounts.google.com/"
    assert is_away(tab)
    assert not is_away(Tab("https://x.com/", "X"))  # never pinned at a page


def test_dates_zip_codes_and_tiny_or_complex_results_arent_wrong_answers():
    """10/26/2024 became 0.000…, a ZIP+4 a subtraction, 24/7 a fraction; 1 mg in tonnes rounded to "0 t"; and
    (-8)^(1/3) showed a complex number."""
    from browser.answers import calculate, convert
    for q in ("10/26/2024", "12345-6789", "24/7", "(-8)^(1/3)"):
        assert calculate(q) is None, q
    assert calculate("1/3").text.startswith("0.333")
    tiny = convert("1 mg in t")
    assert tiny is not None and not tiny.text.startswith("0 ") and "e-09" in tiny.text


def test_addresses_dev_servers_ipv6_and_files_open_rather_than_search(tmp_path):
    """app.localhost:5173 and myapp.test went to https (an http dev server failed); [::1]-style addresses and
    myserver:8080 went to the search engine; so did a file's path, with its folders and your name; and a shortcut
    of yours with the words in it twice raised on every key."""
    from browser import command_bar
    from browser.command_bar import SITE_SEARCHES, site_url, to_url
    assert to_url("app.localhost:5173") == "http://app.localhost:5173"
    assert to_url("myapp.test/login") == "http://myapp.test/login"
    assert to_url("[fe80::1]:8080") == "http://[fe80::1]:8080" and to_url("myserver:8080") == "http://myserver:8080"
    assert to_url("example.com") == "https://example.com" and to_url("hello world") is None
    page = tmp_path / "index.html"
    page.write_text("<p>hi</p>")
    assert to_url(str(page)) == page.as_uri()
    SITE_SEARCHES["custom:test"] = ("Two", "https://x.example/?q={}&alt={}", ("xx",))
    try:
        assert site_url("custom:test", "a b") == "https://x.example/?q=a%20b&alt=a%20b"
    finally:
        del SITE_SEARCHES["custom:test"]
    assert command_bar is not None
