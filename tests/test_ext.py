import io
import json
import struct
import zipfile
from pathlib import Path

from PyQt6.QtWebEngineCore import QWebEngineScript

from browser.ext import chrome, userscripts

FIXTURES = Path(__file__).parent / "fixtures"


def test_userscript_metadata():
    script = userscripts.from_source((FIXTURES / "demo.user.js").read_text(), "https://example.com/demo.user.js")
    assert script.name == "Demo Userscript"
    assert script.version == "1.2"
    assert script.matches == ["*://*/*", "file:///*"]
    assert script.uses_gm and not script.unsupported_grants
    assert script.run_at == "document-end"
    assert script.origin == "https://example.com/demo.user.js"


def test_userscript_unsupported_grant_and_defaults():
    script = userscripts.from_source("// ==UserScript==\n// @name X\n// @grant GM_cookie\n// ==/UserScript==\n")
    assert script.unsupported_grants == ["GM_cookie"]
    plain = userscripts.from_source("// ==UserScript==\n// @name Y\n// ==/UserScript==\n")
    assert plain.grants == ["none"] and not plain.uses_gm
    assert plain.include_regexes == [r"^(https?|file)://.*$"]


def test_userscript_build_worlds():
    gm = userscripts.from_source((FIXTURES / "demo.user.js").read_text())
    built = userscripts.build_script(gm, "", {"runs": 3})
    assert built.worldId() == QWebEngineScript.ScriptWorldId.ApplicationWorld
    assert '"runs": 3' in built.sourceCode()
    assert "==UserScript==" not in built.sourceCode()  # Qt must not apply its own @match reading
    assert userscripts.build_script(gm, "", {}, 42).worldId() == 42  # its own world, given by the host
    plain = userscripts.from_source("// ==UserScript==\n// @name Y\n// @grant none\n// ==/UserScript==\nx()")
    assert userscripts.build_script(plain, "", {}).worldId() == QWebEngineScript.ScriptWorldId.MainWorld


def _install_demo():
    ext_id = chrome.install_folder(FIXTURES / "demo_extension")
    return chrome.ChromeExtension.load(ext_id, chrome.extensions_dir() / ext_id)


def test_extension_manifest(data_dir):
    ext = _install_demo()
    assert ext.id == "local-demo-extension"  # installed from a folder: a local id from the (localized) name
    assert ext.name == "Demo Extension"
    assert ext.popup == "popup.html" and ext.options_page == "options.html"
    assert ext.unsupported == []
    assert ext.url("/popup.html") == "pbext://local-demo-extension/popup.html"


def test_extension_content_scripts(data_dir):
    ext = _install_demo()
    [script] = ext.content_scripts()
    assert script.worldId() == QWebEngineScript.ScriptWorldId.ApplicationWorld
    assert script.injectionPoint() == QWebEngineScript.InjectionPoint.DocumentReady
    assert "demoExtension" in script.sourceCode() and "outline" in script.sourceCode()
    assert '__pbChrome("local-demo-extension"' in script.sourceCode()


def test_extension_reads_stay_inside_folder(data_dir):
    ext = _install_demo()
    assert ext._read("../../../etc/passwd") == ""


def test_background_page_for_service_worker(data_dir):
    html = _install_demo().background_html()
    assert '<script src="/background.js"></script>' in html
    assert "importScripts" in html


def test_unsupported_permissions_flagged(tmp_path, data_dir):
    folder = tmp_path / "blocker"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps(
        {"name": "Blocker", "version": "1", "manifest_version": 3,
         "permissions": ["storage", "declarativeNetRequest", "webRequest", "nativeMessaging", "debugger"]}))
    ext = chrome.ChromeExtension.load(chrome.install_folder(folder), chrome.extensions_dir() / "local-blocker")
    assert ext.unsupported == ["debugger"]  # blocking rules, (watching) webRequest and native messaging work


def _zip(files: dict, prefix="") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(prefix + name, text)
    return buffer.getvalue()


def test_crx3_install_and_web_store_id(data_dir):
    payload = _zip({"manifest.json": json.dumps({"name": "Packed", "version": "2"}), "a.js": "1"})
    header = b"\x00" * 10
    crx = b"Cr24" + struct.pack("<II", 3, len(header)) + header + payload
    ext_id = "a" * 32
    assert chrome.web_store_id(f"https://chromewebstore.google.com/detail/packed/{ext_id}") == ext_id
    assert chrome.install_archive(crx, ext_id) == ext_id
    assert (chrome.extensions_dir() / ext_id / "a.js").read_text() == "1"


def test_zip_with_folder_and_traversal(data_dir):
    payload = _zip({"manifest.json": json.dumps({"name": "Nested", "version": "1"}), "../../evil.js": "x"},
                   prefix="nested/")
    ext_id = chrome.install_archive(payload)
    assert (chrome.extensions_dir() / ext_id / "manifest.json").exists()
    assert not (chrome.extensions_dir() / "evil.js").exists()
    assert not (data_dir / "evil.js").exists()
