# Extensions in the real window: content scripts in subframes speak for their own frame.
# Run with:  python -m pytest tests/live --live -k extension

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from live_helpers import open_page, pause, wait_until

FIXTURES = Path(__file__).parent.parent / "fixtures"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def other_origin():
    """The test pages from a second port: another origin, as an ad inside a bank's page would be."""
    handler = functools.partial(_Quiet, directory=str(Path(__file__).parent / "site"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _js(target, source, timeout=10.0):
    """The result of `source` in a page or frame's main world."""
    box = {}
    target.runJavaScript(source, 0, lambda result: box.setdefault("result", result))
    wait_until(lambda: "result" in box, timeout)
    return box.get("result")


def test_extension_messages_say_which_frame_they_come_from(window, site, other_origin):
    from browser.ext import chrome
    host = window.extensions
    ext_id = chrome.install_folder(FIXTURES / "frames_extension")
    host.add_extension(ext_id)
    assert wait_until(lambda: ext_id in host.backgrounds and not host.backgrounds[ext_id].isLoading(), 10)
    background = host.backgrounds[ext_id]

    inner = f"{other_origin}/frame.html"
    tab = open_page(window, f"{site}/frames.html?inner={inner}")
    seen = lambda: json.loads(_js(background, "JSON.stringify(self.seen)") or "{}")  # noqa: E731
    assert wait_until(lambda: len(seen()) == 2, 10), seen()
    frames = seen()
    top, sub = frames[f"{site}/frames.html?inner={inner}"], frames[inner]
    assert top["url"] == f"{site}/frames.html?inner={inner}" and top["frameId"] == 0 and top["origin"] == site
    # The subframe is reported as itself: its own address and origin, and a frameId of its own
    assert sub["url"] == inner and sub["origin"] == other_origin and sub["frameId"] > 0
    assert top["tabId"] == sub["tabId"]

    # tabs.sendMessage with a frameId reaches only that frame; without one, every frame (as in Chrome)
    send = "chrome.tabs.sendMessage({tab}, {{ type: 'mark', token: '{token}' }}{options}); true"
    _js(background, send.format(tab=sub["tabId"], token="A", options=f", {{ frameId: {sub['frameId']} }}"))
    _js(background, send.format(tab=sub["tabId"], token="T", options=", { frameId: 0 }"))
    _js(background, send.format(tab=sub["tabId"], token="B", options=""))
    page = tab.view.page()
    subframe = page.mainFrame().children()[0]
    marked = lambda target: _js(target, "document.documentElement.dataset.marked || ''")  # noqa: E731
    assert wait_until(lambda: marked(subframe) == "AB" and marked(page.mainFrame()) == "TB", 10), \
        (marked(subframe), marked(page.mainFrame()))
    pause(0.3)
    assert marked(subframe) == "AB" and marked(page.mainFrame()) == "TB"
