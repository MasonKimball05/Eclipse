from browser import clean_links
from browser.clean_links import Cleaner, clean


def test_tracking_tags_come_off_and_nothing_else_changes():
    assert clean("https://a.com/x?utm_source=tw&id=3&fbclid=abc#top") == "https://a.com/x?id=3#top"
    assert clean("https://a.com/?q=a%20b+c&utm_medium=x") == "https://a.com/?q=a%20b+c"  # spelling kept
    assert clean("https://a.com/?utm_x=1&gclid=2") == "https://a.com/"
    assert clean("https://a.com/?Q=1&q=2&&") == "https://a.com/?Q=1&q=2&&"  # untouched if nothing to remove
    assert clean("https://a.com/path") == "https://a.com/path"
    assert clean("pbext://x/?utm_source=1") == "pbext://x/?utm_source=1"


def test_share_tags_only_on_the_sites_that_use_them():
    assert clean("https://youtu.be/abc?si=xyz") == "https://youtu.be/abc"
    assert clean("https://www.youtube.com/watch?v=abc&si=1&t=30") == "https://www.youtube.com/watch?v=abc&t=30"
    assert clean("https://example.com/?si=keep") == "https://example.com/?si=keep"
    assert clean("https://www.amazon.com/dp/B01?psc=1&pd_rd_w=x") == "https://www.amazon.com/dp/B01?psc=1"


def test_copying_follows_the_setting():
    url = "https://a.com/?utm_source=x&id=1"
    assert clean_links.for_copy(url, {}) == "https://a.com/?id=1"
    assert clean_links.for_copy(url, {clean_links.SETTING: False}) == url


def test_a_site_that_puts_the_tags_back_is_let_through():
    cleaner = Cleaner()
    url = "https://a.com/?utm_source=x"
    assert cleaner.redirect_for(url) == "https://a.com/"
    assert cleaner.redirect_for(url) is None  # it came straight back: don't bounce forever
    assert cleaner.redirect_for("https://a.com/") is None


def test_certificate_reasons_are_plain_and_mention_dev_servers():
    from PyQt6.QtCore import QUrl
    from browser.cert_errors import T, reason_text
    assert "expired" in reason_text(T.CertificateDateInvalid, "", QUrl("https://a.com/"))
    assert "development server" in reason_text(T.CertificateAuthorityInvalid, "", QUrl("https://localhost:8443/"))
    assert "development server" not in reason_text(T.CertificateAuthorityInvalid, "", QUrl("https://a.com/"))
    assert reason_text(T.CertificateNonUniqueName, "Odd name", QUrl("https://a.com/")) == "Odd name."


def test_more_trackers_go_and_a_sites_own_filters_stay():
    """Google Ads' gad_source (on nearly every ad click now), Analytics' _gl and others went through; and "pk_"
    taken as Matomo's took a site's own ?pk__in=1,2 filter, so a link opened unfiltered."""
    from browser.clean_links import clean
    assert clean("https://shop.example/p?id=4&gad_source=1&gad_campaignid=9&_gl=1*abc") == "https://shop.example/p?id=4"
    assert clean("https://news.example/a?mibextid=Zx&li_fat_id=7&pk_campaign=x") == "https://news.example/a"
    kept = "https://admin.example/items/?pk__in=1,2,3&pk=5"
    assert clean(kept) == kept
    back = "https://login.example/signin?ref_url=https://app.example/home"
    assert clean(back) == back
