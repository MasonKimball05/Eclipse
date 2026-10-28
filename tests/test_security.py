# The checks that keep web pages (and what they put in titles, file names and answers) from reaching further
# than they should. The live tests drive the rest in the real window.

from browser.downloads import risky
from browser.ext.chrome import BACKGROUND_PAGE, META_FILE, ChromeExtension
from browser.password_store import related, same_site
from browser.passkey_store import rp_id_allowed
from browser.theme import plain_tip
from browser.tidy_downloads import clean_name


def test_files_that_can_run_something_ask_first():
    for name in ("Setup.pkg", "run.command", "Tool.app.zip", "invoice.pdf.app.zip", "x.sh.tar.gz", "a.terminal",
                 "Link.webloc", "profile.mobileconfig", "setup.exe"):
        assert risky(name), name
    for name in ("report.pdf", "Eclipse.dmg", "photos.zip", "notes.sh.txt", "archive.tar.gz", "README"):
        assert not risky(name), name


def test_a_suggested_name_cant_reverse_how_it_reads():
    assert clean_name("Invoice‮fdp") == "Invoicefdp"
    assert clean_name("Acme​ Invoice⁦ March") == "Acme Invoice March"


def test_page_text_in_a_tooltip_is_only_ever_text():
    tip = plain_tip("<b>accounts.google.com</b><!-- wants your camera")
    assert "<b>" not in tip and "&lt;b&gt;accounts.google.com&lt;/b&gt;&lt;!--" in tip


def test_a_shared_hosts_customers_are_separate_sites():
    assert not related("neocities.org", "evil.neocities.org")
    assert not same_site("shop.myshopify.com", "evil.myshopify.com")
    assert not rp_id_allowed("myshopify.com", "evil.myshopify.com")
    assert related("google.com", "accounts.google.com") and same_site("shop.example.com", "accounts.example.com")


def test_an_extensions_own_files_are_never_exposed_to_websites():
    ext = ChromeExtension("abc", None, {"name": "x", "version": "1", "manifest_version": 3, "web_accessible_resources": [
        {"resources": ["*"], "matches": ["https://allowed.example/*"]},
        {"resources": ["public/*"], "matches": ["<all_urls>"]}]})
    assert ext.web_accessible("img/logo.png", "https://allowed.example")
    assert not ext.web_accessible("img/logo.png", "https://evil.example")  # (only for the sites it names)
    assert ext.web_accessible("public/a.css", "https://evil.example")
    for own in (BACKGROUND_PAGE, META_FILE):
        assert not ext.web_accessible(own, "https://allowed.example")
    old = ChromeExtension("def", None, {"name": "y", "version": "1", "web_accessible_resources": ["*.png"]})
    assert old.web_accessible("a.png", "https://anywhere.example") and not old.web_accessible("a.js")
