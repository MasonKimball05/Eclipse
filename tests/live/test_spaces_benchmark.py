# Switching Spaces, Qt's way (sidebar.py, space_slide.py) against a trial in SwiftUI (native/Sources/SpacesLab.swift),
# on the same Spaces: how long a switch takes to start moving, how evenly its frames come, and how long it takes all
# told. Only run when asked (it shows both windows in front for a few seconds, without taking focus, since macOS
# stops counting a hidden window's frames):
#   ECLIPSE_BENCH=results.json pytest tests/live/test_spaces_benchmark.py --live

import json
import os
import statistics
import time

import pytest


SWITCHES = 10


def _spaces(window):
    from browser.models import Folder, Space, Tab
    store = window.store
    colors = [("#3b82f6", "#8b5cf6"), ("#f59e0b", "#ef4444"), ("#10b981", "#06b6d4"), ("#ec4899", "#f43f5e")]
    while len(store.spaces) < 4:
        store.add_space(f"Space {len(store.spaces) + 1}", colors[len(store.spaces)][0])
    for n, space in enumerate(store.spaces[:4]):
        space.theme = {"colors": list(colors[n]), "intensity": 0.6}
        space.pinned = [Folder(f"Folder {f}", [Tab(f"about:blank#{n}-{f}-{i}", f"Pinned {i} in {f}")
                                               for i in range(4)]) for f in range(2)]
        space.pinned += [Tab(f"about:blank#{n}-p{i}", f"Pinned tab {i}") for i in range(4)]
        space.today = [Tab(f"about:blank#{n}-t{i}", f"Today tab number {i} in {space.name}") for i in range(30)]
    store._changed()
    return store.spaces[:4]


def _lab_json(spaces, favorites):
    from browser.models import Folder

    def node(n):
        if isinstance(n, Folder):
            return {"id": n.id, "title": n.name, "children": [node(c) for c in n.children]}
        return {"id": n.id, "title": n.title, "host": "example.com"}
    return {"spaces": [{"id": s.id, "name": s.name, "colors": s.theme.get("colors", [s.color]),
                        "pinned": [node(c) for c in s.pinned], "today": [node(t) for t in s.today]} for s in spaces],
            "favorites": [{"id": f"f{i}", "title": "Fav", "host": h} for i, h in enumerate(favorites)]}


def wait_until(condition, timeout=3.0):
    """Qt's own event loop till condition() holds (not live_helpers': it sleeps between looks, and a main thread
    asleep draws nothing, Qt's frames or SwiftUI's)."""
    from PyQt6.QtCore import QEventLoop, QTimer
    loop, end = QEventLoop(), time.monotonic() + timeout
    check = QTimer(interval=5)
    check.timeout.connect(lambda: loop.quit() if condition() or time.monotonic() > end else None)
    check.start()
    if not condition():
        loop.exec()
    check.stop()
    return condition()


def _front(widget=None, number=0):
    """A window in front of everything (a Qt one, or a native page's by its number), without taking focus."""
    import ctypes
    from browser import macos
    if widget is not None:
        window = macos._send(ctypes.c_void_p(int(widget.winId())), "window")
    else:
        app = macos._send(macos._objc.objc_getClass(b"NSApplication"), "sharedApplication")
        window = macos._send(app, "windowWithWindowNumber:", number, argtypes=(ctypes.c_long,))
    macos._send(window, "orderFrontRegardless", restype=None, argtypes=())


def _no_app_nap():
    """The test app is a background one (no Dock icon, so it never takes focus), and macOS slows a background app's
    timers and display link down (App Nap): held off while measuring, as a foreground app's would be."""
    import ctypes
    from browser import macos
    info = macos._send(macos._objc.objc_getClass(b"NSProcessInfo"), "processInfo")
    reason = macos._send(macos._objc.objc_getClass(b"NSString"), "stringWithUTF8String:", b"Benchmark",
                         argtypes=(ctypes.c_char_p,))
    activity = macos._send(info, "beginActivityWithOptions:reason:", 0x00FFFFFF | 0xFF00000000, reason,
                           argtypes=(ctypes.c_uint64, ctypes.c_void_p))
    macos._send(activity, "retain")
    return activity


def _summary(runs):
    gaps = [g for r in runs for g in r["frames_ms"]]
    return {"first_frame_ms": round(statistics.median(r["first_ms"] for r in runs), 1),
            "switch_call_ms": round(statistics.median(r.get("call_ms", 0) for r in runs), 1),
            "total_ms": round(statistics.median(r["took_ms"] for r in runs), 1),
            "frame_ms_median": round(statistics.median(gaps), 2) if gaps else None,
            "frame_ms_p95": round(sorted(gaps)[int(len(gaps) * 0.95)], 2) if gaps else None,
            "frame_ms_worst": round(max(gaps), 1) if gaps else None,
            "frames_over_20ms": sum(g > 20 for g in gaps), "frames": len(gaps)}


def test_switching_spaces_qt_against_swiftui(window, monkeypatch):
    out = os.environ.get("ECLIPSE_BENCH")
    if not out:
        pytest.skip("a benchmark: ECLIPSE_BENCH=results.json to run it")
    from browser import native, space_slide
    monkeypatch.setenv("ECLIPSE_NATIVE", "1")
    monkeypatch.setattr(native, "KEEP_BEHIND", False)
    _no_app_nap()
    spaces = _spaces(window)
    window.move(40, 60)
    _front(window)
    wait_until(lambda: False, 1.0)

    # Qt: each paint of the slide's cover, from the switch till it's lifted
    paints = []
    painted = space_slide.SpaceSlide.paintEvent
    def counted(self, event):
        paints.append(time.perf_counter())
        painted(self, event)
    monkeypatch.setattr(space_slide.SpaceSlide, "paintEvent", counted)
    qt_runs, change_ms = [], []
    set_active = window._set_active_space

    def timed_set_active(space):
        start = time.perf_counter()
        set_active(space)
        change_ms.append((time.perf_counter() - start) * 1000)
    monkeypatch.setattr(window, "_set_active_space", timed_set_active)
    for i in range(SWITCHES):
        target = spaces[(spaces.index(window.store.active_space) + 1) % len(spaces)]
        paints.clear()
        start = time.perf_counter()
        window.switch_space(target)
        call = (time.perf_counter() - start) * 1000
        slides = lambda: [w for w in window.findChildren(space_slide.SpaceSlide) if w.isVisible()]  # noqa: E731
        wait_until(lambda: not slides(), 3)
        end = paints[-1] if paints else time.perf_counter()
        qt_runs.append({"first_ms": ((paints[0] if paints else end) - start) * 1000, "call_ms": call,
                        "took_ms": (end - start) * 1000,
                        "frames_ms": [(b - a) * 1000 for a, b in zip(paints, paints[1:])]})
        wait_until(lambda: False, 0.4)
    rebuild = []
    for _ in range(5):
        start = time.perf_counter()
        window.sidebar.rebuild()
        rebuild.append((time.perf_counter() - start) * 1000)

    # The native sidebar in this window: the same switches, the window's colors and page changing too, the window
    # taking them at once or once the scroll's done
    from browser import sidebar_native
    window.sidebar.use_native(True)
    body = window.sidebar.native
    assert body is not None, "the native sidebar needs macOS 14 and the native library"
    wait_until(lambda: False, 0.8)
    real = {}
    for wait in (0, 320):
        monkeypatch.setattr(sidebar_native.NativeSidebarBody, "CHANGE_AFTER_MS", wait)
        got = real[wait] = []
        catch = lambda name, event: got.append(event) if name == "bench" else None  # noqa: E731
        body.page.action.connect(catch)
        measured = body.go

        def go(space, animate=True, measure=None):
            measured(space, animate, measure=0.6)
        monkeypatch.setattr(body, "go", go)
        for i in range(SWITCHES):
            target = spaces[(spaces.index(window.store.active_space) + 1) % len(spaces)]
            window.switch_space(target)
            assert wait_until(lambda: len(got) == i + 1, 3)
            wait_until(lambda: False, 0.4)
        body.page.action.disconnect(catch)
        monkeypatch.setattr(body, "go", measured)
    window.sidebar.use_native(False)

    # SwiftUI: the same Spaces in the trial; each switch measured by its display link
    lab = native.NativePage({"page": "spaces_lab", "title": "Spaces (SwiftUI trial)", "width": 300, "height": 760,
                             **_lab_json(spaces, ["apple.com", "x.com", "news.ycombinator.com", "bbc.co.uk"]),
                             **native.theme_state(window.theme)})
    assert lab.id, "the trial needs macOS 14 and the native library"
    _front(number=lab.window_number())
    wait_until(lambda: False, 1.0)
    swift_runs = []
    lab.action.connect(lambda name, event: swift_runs.append(event) if name == "bench" else None)
    ids = [s.id for s in spaces]
    for i in range(SWITCHES):
        lab.update({"bench": {"n": i + 1, "to": ids[(i + 1) % len(ids)], "seconds": 0.6}})
        assert wait_until(lambda: len(swift_runs) == i + 1, 3)
        wait_until(lambda: False, 0.4)
    lab.close()

    results = {"qt": {**_summary(qt_runs), "color_change_ms": round(statistics.median(change_ms), 1),
                      "sidebar_rebuild_ms": round(statistics.median(rebuild), 1)},
               "native_sidebar_change_at_once": _summary(real[0]),
               "native_sidebar_change_after_scroll": _summary(real[320]),
               "swiftui_trial": _summary(swift_runs), "switches": SWITCHES,
               "raw": {"qt": qt_runs, "native_sidebar": real, "swiftui_trial": swift_runs}}
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
    print(json.dumps({k: v for k, v in results.items() if k != "raw"}, indent=1))
