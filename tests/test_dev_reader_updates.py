import os
from pathlib import Path

import pytest

from browser import crash, storage
from browser.devmode import dev_label, is_dev_url, origin_of, project_files
from browser.reader import reader_document


# --- Developer Mode ---------------------------------------------------------------------------

def test_dev_urls():
    for url in ("http://localhost:5173/", "http://127.0.0.1:8000/admin", "https://myapp.test/", "http://api.localhost/",
                "file:///Users/me/site/index.html", "http://0.0.0.0:3000"):
        assert is_dev_url(url), url
    for url in ("https://github.com", "https://localhost.example.com", "about:blank", "note:abc", ""):
        assert not is_dev_url(url), url


def test_dev_label_shows_port_and_path():
    assert dev_label("http://localhost:5173/settings?tab=2") == "localhost:5173/settings?tab=2"
    assert dev_label("http://localhost:3000/") == "localhost:3000"
    assert dev_label("file:///Users/me/site/index.html") == "index.html"
    assert origin_of("http://localhost:5173/a") == origin_of("http://localhost:5173/b") != origin_of("http://localhost:3000/")


def test_project_files_skip_dependencies(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("x")
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "index.js").write_text("x")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x")
    (tmp_path / "index.html.swp").write_text("x")
    assert [Path(os.path.relpath(p, tmp_path)).as_posix() for p in project_files(str(tmp_path))] == ["src/app.js"]


# --- Translate ----------------------------------------------------------------------------------


# --- Reader -------------------------------------------------------------------------------------

def test_reader_document_escapes_what_it_adds():
    page = reader_document({"title": "<script>alert(1)</script>", "site": "a&b", "byline": "<b>x</b>",
                            "html": "<p>ok</p>", "words": 460, "lang": '"><script>'}, 17, "sepia")
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert '"><script>' not in page
    assert "2 min read" in page and "<p>ok</p>" in page
    assert "default-src 'none'" in page  # no scripts, frames or connections, even if something slipped through


# --- Now Playing ----------------------------------------------------------------------------------


# --- Crash recovery -------------------------------------------------------------------------------

@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ECLIPSE_DATA_DIR", str(tmp_path))
    return tmp_path


def test_a_run_that_didnt_quit_is_noticed(data):
    assert crash.start() is None  # first launch
    crash.clean_exit()
    assert crash.start() is None  # quit properly last time
    crash.clean_exit()
    (data / crash.MARKER).write_text("999999 2026-10-23 21:15")  # a run whose process is gone
    found = crash.start()
    crash.clean_exit()
    assert found and found["when"] == "2026-10-23 21:15"


def test_errors_are_logged(data):
    try:
        raise ValueError("boom")
    except ValueError as error:
        crash.log_exception(type(error), error, error.__traceback__)
    assert "ValueError: boom" in (data / "logs" / "errors.log").read_text()


def test_session_falls_back_to_the_copy_before(data):
    path = data / "session.json"
    storage.write_json(path, {"n": 1}, keep_previous=True)
    storage.write_json(path, {"n": 2}, keep_previous=True)
    assert storage.read_json(storage.previous_copy(path)) == {"n": 1}
    path.write_text("{ truncated")
    assert storage.read_json(path) is None
    assert storage.read_json(storage.previous_copy(path)) == {"n": 1}
