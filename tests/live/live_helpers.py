# Helpers for the live tests: waiting on Qt without blocking it.

import time

from PyQt6.QtWidgets import QApplication


def wait_until(condition, timeout=10.0, step=0.02):
    """Let Qt run until condition() is true; returns its last value (falsy on timeout)."""
    end = time.monotonic() + timeout
    while True:
        QApplication.processEvents()
        value = condition()
        if value or time.monotonic() > end:
            return value
        time.sleep(step)


def pause(seconds):
    wait_until(lambda: False, seconds)


def run_js(view, source, world=1, timeout=10.0):
    """The result of `source` in the page (in the isolated world by default)."""
    box = {}
    view.page().runJavaScript(source, world, lambda result: box.setdefault("result", result))
    wait_until(lambda: "result" in box, timeout)
    return box.get("result")


def loaded(view, timeout=15.0):
    """Wait for the page in `view` to finish loading."""
    state = {"done": False}
    view.loadFinished.connect(lambda _ok: state.update(done=True))
    return wait_until(lambda: state["done"] or (not view.page().isLoading() and view.url().isValid()
                                                 and run_js(view, "document.readyState", 1, 2) == "complete"),
                      timeout)


def open_page(window, url, timeout=15.0, wait=True):
    tab = window.open_url(url)
    assert not wait or loaded(tab.view, timeout), f"{url} didn't load"
    return tab
