from browser.ext.updates import is_newer, new_permissions, parse_update_check

ANSWER = b"""<?xml version="1.0" encoding="UTF-8"?>
<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0" server="prod">
  <app appid="eimadpbcbfnmbkopoojfekhnkhdbieeh" status="ok">
    <updatecheck codebase="https://clients2.googleusercontent.com/crx/blobs/x/extension_4_9_133.crx"
                 hash_sha256="00" status="ok" version="4.9.133"/>
  </app>
</gupdate>"""


def test_versions():
    assert is_newer("1.10.0", "1.9.9")
    assert is_newer("2", "1.99")
    assert not is_newer("1.2", "1.2.0")
    assert not is_newer("", "1.0")


def test_update_check_answer():
    assert parse_update_check(ANSWER) == ("4.9.133", "https://clients2.googleusercontent.com/crx/blobs/x/extension_4_9_133.crx")
    assert parse_update_check(ANSWER.replace(b'status="ok" version', b'status="noupdate" version')) is None
    assert parse_update_check(b"not xml") is None


def test_new_permissions():
    old = {"permissions": ["storage"], "content_scripts": [{"matches": ["https://a.com/*"]}]}
    same = {"permissions": ["storage"], "content_scripts": [{"matches": ["https://a.com/*"]}]}
    more = {"permissions": ["storage", "tabs"], "host_permissions": ["<all_urls>"]}
    assert new_permissions(old, same) == set()
    assert new_permissions(old, more) == {"tabs", "<all_urls>"}


def test_words_that_merely_start_like_prereleases():
    assert not is_newer("1.0", "1.0-build5")  # "build" isn't "b"(eta)
    assert is_newer("1.0", "1.0b2")
    assert is_newer("1.0", "1.0-beta.3")
