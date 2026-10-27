# Helpers for the live tests: waiting on Qt without blocking it.

import time

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
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


def steady(read, timeout=3.0, settle=0.15):
    """Let Qt run until read() stops changing (animations done), and return it."""
    last, since = read(), time.monotonic()
    end = since + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.02)
        now = read()
        if now != last:
            last, since = now, time.monotonic()
        elif time.monotonic() - since >= settle:
            break
    return last


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


# A trackpad swipe sideways over a page: scroll phases, as macOS sends them (end=False: fingers still down)
def swipe(view, dx, at=QPoint(600, 450), end=True, during=None):
    target = view.focusProxy()
    phases = [(0, Qt.ScrollPhase.ScrollBegin)] + [(dx / 12, Qt.ScrollPhase.ScrollUpdate)] * 12
    for i, (step, phase) in enumerate(phases):
        QApplication.sendEvent(target, QWheelEvent(QPointF(at), QPointF(target.mapToGlobal(at)), QPoint(int(step), 0),
                                                   QPoint(int(step) * 4, 0), Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier, phase, False))
        pause(0.016)
        if during and i == 8:
            during()
    pause(0.3)
    if not end:
        return
    lift(view, at)


def lift(view, at=QPoint(600, 450)):
    target = view.focusProxy()
    QApplication.sendEvent(target, QWheelEvent(QPointF(at), QPointF(target.mapToGlobal(at)), QPoint(0, 0),
                                               QPoint(0, 0), Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollEnd, False))
