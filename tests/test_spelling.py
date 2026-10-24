import functools
import http.server
import threading
import time

from PyQt6.QtCore import QCoreApplication

from browser import spelling


def _serve(folder):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wait(download):
    result = []
    download.finished.connect(lambda ok, message: result.append((ok, message)))
    deadline = time.monotonic() + 10
    while not result and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return result[0] if result else None


def test_languages_map_to_chromes_dictionaries():
    assert spelling.language("en_US") == "en-US"
    assert spelling.language("en_NZ") == "en-US"  # the nearest one there is
    assert spelling.language("pt_PT") == "pt-BR"
    assert spelling.language("ja_JP") is None


def test_a_dictionary_downloads_once_and_turns_checking_on(qapp, data_dir, tmp_path):
    (tmp_path / "en-us-10-1.bdic").write_bytes(spelling.MAGIC + b"\0" * 20_000)
    server = _serve(tmp_path)
    try:
        download = spelling.DictionaryDownload("en-US", source=f"http://127.0.0.1:{server.server_port}/")
        ok, _message = _wait(download)
    finally:
        server.shutdown()
    assert ok and spelling.installed("en-US")

    class Profile:
        languages, enabled = None, None
        def setSpellCheckLanguages(self, langs): self.languages = langs
        def setSpellCheckEnabled(self, on): self.enabled = on
    profile = Profile()
    assert spelling.apply([profile], {spelling.SETTING: True, "spell_check_language": "en_US"})
    assert profile.enabled and profile.languages == ["en-US"]
    assert not spelling.apply([profile], {spelling.SETTING: False}) and profile.enabled is False


def test_something_that_isnt_a_dictionary_is_refused(qapp, data_dir, tmp_path):
    (tmp_path / "fr-fr-3-0.bdic").write_bytes(b"<html>Sign in to Wi-Fi</html>" * 1000)  # a captive portal, say
    server = _serve(tmp_path)
    try:
        ok, message = _wait(spelling.DictionaryDownload("fr-FR", source=f"http://127.0.0.1:{server.server_port}/"))
    finally:
        server.shutdown()
    assert not ok and "wasn't a dictionary" in message
    assert not spelling.installed("fr-FR")
