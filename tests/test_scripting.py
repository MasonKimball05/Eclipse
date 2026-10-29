# Eclipse's AppleScript dictionary and its link scheme, as the apps carry them. The commands themselves run in the
# real window: tests/live/test_live_features.py.

import shutil
import subprocess
import sys
from xml.etree import ElementTree

import pytest

from browser.external_links import OWN_SCHEME, is_external
from browser.scripting import COMMANDS, IN_SPACE, SUITE, TAB_RECORD, ScriptingFeature, sdef
from test_brand import _build_app_module


def test_the_dictionary_names_every_command_the_app_answers():
    root = ElementTree.fromstring(sdef().split("\n", 2)[2])  # (after the XML and DOCTYPE lines)
    suite = root.find("suite")
    assert suite.get("code") == SUITE
    codes = {c.get("name"): c.get("code") for c in suite.findall("command")}
    assert codes == {words: SUITE + code for words, code, *_ in COMMANDS}
    assert len({code for _, code, *_ in COMMANDS}) == len(COMMANDS)  # (no two alike)
    record = suite.find("record-type")
    assert {p.get("name"): p.get("code") for p in record.findall("property")} == TAB_RECORD
    assert all(p.get("code") == IN_SPACE for p in suite.iter("parameter"))


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("xmllint"), reason="Apple's sdef rules")
def test_the_dictionary_is_one_macos_accepts(tmp_path):
    path = tmp_path / "Eclipse.sdef"
    path.write_text(sdef())
    subprocess.run(["xmllint", "--noout", "--dtdvalid", "/System/Library/DTDs/sdef.dtd", str(path)], check=True)
    if shutil.which("sdp"):  # (what turns it into AppleScript's words)
        subprocess.run(["sdp", "-fh", "--basename", "Eclipse", "-o", str(tmp_path), str(path)], check=True)


def test_both_apps_carry_the_dictionary_and_the_link_scheme(tmp_path):
    build_app = _build_app_module()
    for dev in (False, True):
        plist = build_app.info_plist(dev=dev)
        assert plist["OSAScriptingDefinition"] == build_app.SDEF == "Eclipse.sdef"
        schemes = [s for t in plist["CFBundleURLTypes"] for s in t["CFBundleURLSchemes"]]
        assert schemes == ["http", "https", OWN_SCHEME]
    build_app.write_scripting(tmp_path)
    assert (tmp_path / "Eclipse.sdef").read_text() == sdef()


def test_eclipses_own_links_count_as_another_apps_to_a_page():
    from PyQt6.QtCore import QUrl
    assert is_external(QUrl(f"{OWN_SCHEME}://open?url=https://example.com/"))  # (webview.py refuses them first)

