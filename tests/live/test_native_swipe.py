# Swiping between Spaces on the native sidebar (native/Sources/SidebarBody.swift), with real trackpad scroll events:
# made with Core Graphics and handed straight to the sidebar's scroll view, each the app's current event while it's
# handled, as a delivered one is (nothing posted to the system, so the pointer and focus stay where they are).
# Run with:  ./run.sh --live-tests

import ctypes
import ctypes.util
import time

import pytest

from tests.live.test_spaces_benchmark import _no_app_nap, _spaces, wait_until

PIXEL, CONTINUOUS, POINT_DELTA_X, FIXED_DELTA_X, PHASE, MOMENTUM = 0, 88, 97, 94, 99, 123
BEGAN, CHANGED, ENDED = 1, 2, 4
MOMENTUM_BEGIN, MOMENTUM_CONTINUE, MOMENTUM_END = 1, 2, 3


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


def _quartz():
    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
    cg.CGEventCreateScrollWheelEvent2.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32,
                                                  ctypes.c_int32, ctypes.c_int32]
    cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
    cg.CGEventSetDoubleValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_double]
    cg.CGEventSetLocation.argtypes = [ctypes.c_void_p, _Point]
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    return cg, cf


def _scroll_view(view):
    """The Spaces' sideways NSScrollView in `view`: the one whose content is wider than it is (each Space's own list
    has a scroll view too, inside it; the first found could be one of those, and the swipes went nowhere)."""
    from browser import macos

    class Rect(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]
    found = []

    def walk(v):
        if macos._send(v, "isKindOfClass:", macos._objc.objc_getClass(b"NSScrollView"), restype=ctypes.c_bool,
                       argtypes=(ctypes.c_void_p,)):
            content = macos._send(macos._send(v, "documentView"), "frame", restype=Rect)
            frame = macos._send(v, "frame", restype=Rect)
            found.append((content.w / max(frame.w, 1), v))
        subviews = macos._send(v, "subviews")
        for i in range(macos._send(subviews, "count", restype=ctypes.c_ulong)):
            walk(macos._send(subviews, "objectAtIndex:", i, argtypes=(ctypes.c_ulong,)))
    walk(view)
    wide = [v for ratio, v in found if ratio > 1.5]
    return wide[0] if wide else (found[0][1] if found else None)


def _swipe(target, at, dx, gaps, then=900, coast_ms=240, frame=None):
    """Two fingers across, let go, and `coast_ms` of momentum after (as a trackpad sends it), a frame every 8 ms;
    `gaps` gets the main thread's waits meanwhile; `then`: ms waited after; `frame()` is called every frame."""
    from PyQt6.QtCore import QCoreApplication
    from browser import macos
    cg, cf = _quartz()

    def send(delta, phase=0, momentum=0):
        event = cg.CGEventCreateScrollWheelEvent2(None, PIXEL, 2, 0, int(delta), 0)
        for field, value in ((CONTINUOUS, 1), (POINT_DELTA_X, int(delta)), (PHASE, phase), (MOMENTUM, momentum)):
            cg.CGEventSetIntegerValueField(event, field, value)
        cg.CGEventSetDoubleValueField(event, FIXED_DELTA_X, float(delta))
        cg.CGEventSetLocation(event, _Point(*at))
        ns = macos._send(macos._objc.objc_getClass(b"NSEvent"), "eventWithCGEvent:", event,
                         argtypes=(ctypes.c_void_p,))
        app = macos._send(macos._objc.objc_getClass(b"NSApplication"), "sharedApplication")
        macos._send(app, "_setCurrentEvent:", ns, restype=None, argtypes=(ctypes.c_void_p,))
        macos._send(target, "scrollWheel:", ns, restype=None, argtypes=(ctypes.c_void_p,))
        macos._send(app, "_setCurrentEvent:", None, restype=None, argtypes=(ctypes.c_void_p,))
        cf.CFRelease(event)

    def run(ms):
        last, end = time.perf_counter(), time.perf_counter() + ms / 1000
        while time.perf_counter() < end:
            QCoreApplication.processEvents()
            now = time.perf_counter()
            gaps.append((now - last) * 1000)
            last = now
            if frame is not None:
                frame()
    for phase in [BEGAN] + [CHANGED] * 8:
        send(dx, phase=phase)
        run(8)
    send(0, phase=ENDED)
    run(8)
    frames = max(2, coast_ms // 8)
    coast = [dx * (0.85 ** i if coast_ms <= 240 else max(0.97 ** i, 0.04)) for i in range(frames)]
    for i, delta in enumerate(coast):
        send(delta, momentum=MOMENTUM_BEGIN if i == 0 else MOMENTUM_END if i == len(coast) - 1 else MOMENTUM_CONTINUE)
        run(8)
    run(then)


def test_a_swipe_goes_one_space_over_and_says_so_once_its_at_rest(window, monkeypatch):
    from browser import sidebar_native
    monkeypatch.setenv("ECLIPSE_NATIVE", "1")
    if not sidebar_native.available():
        pytest.skip("the native sidebar needs macOS 14 and the native pages built")
    _no_app_nap()
    spaces = _spaces(window)
    window.resize(1100, 760)
    window.show()
    window.sidebar.use_native(True)
    body = window.sidebar.native
    wait_until(lambda: False, 0.8)
    target = _scroll_view(ctypes.c_void_p(body.page.view()))
    assert target, "no scroll view in the native sidebar"
    middle = body.container.mapToGlobal(body.container.rect().center())
    at = (middle.x(), middle.y())
    told = []
    body.page.action.connect(lambda name, event: told.append((name, body.page.state()["current"]))
                             if name in ("swiped", "hover", "heading") else None)
    for flick in (-12, -45, 45, 12):  # (a gentle swipe and a hard flick, each way)
        before = spaces.index(window.store.active_space)
        told.clear()
        _swipe(target, at, flick, [])
        after = spaces.index(window.store.active_space)
        # One Space over, however hard (the system's paging let a flick's momentum carry it two)
        assert after == before + (1 if flick < 0 else -1), (flick, before, after)
        # Said once, when it's settled (said as soon as it was halfway over, the window's change of Space stopped
        # the scroll), and nothing pointed at on the way
        names = [name for name, _ in told]
        assert names[-1] == "swiped" and names.count("swiped") == 1 and "hover" not in names, names
        assert names[0] == "heading"  # (where it's going said on the way, for its page to start loading)
    # Several quick swipes in a row: the window changes Space once, at the end (after each, it stalled the next)
    window.switch_space(spaces[0])
    wait_until(lambda: False, 0.8)
    told.clear()
    gaps = []
    for _ in range(3):
        _swipe(target, at, -30, gaps, then=60)
    assert wait_until(lambda: window.store.active_space is spaces[3], 2)
    assert [t for t in told if t[0] == "swiped"] == [("swiped", spaces[3].id)]  # (none for the Spaces passed)
    assert max(gaps) < 60, max(gaps)  # (no Space change in the middle of it)
    # A nudge that hardly goes anywhere stays put
    before = window.store.active_space
    _swipe(target, at, -2, [])
    assert window.store.active_space is before


def test_a_swipe_lands_straight_away_with_pages_open_and_after_quick_switches(window, site, monkeypatch):
    """The scroll position's id wasn't always told of a swipe, so the window changed Space only when something
    redrew the sidebar (a page in a tab: seconds late), and switches from Python interrupting each other could leave
    every swipe after taken for theirs."""
    from browser import sidebar_native
    from tests.live.live_helpers import open_page
    monkeypatch.setenv("ECLIPSE_NATIVE", "1")
    if not sidebar_native.available():
        pytest.skip("the native sidebar needs macOS 14 and the native pages built")
    _no_app_nap()
    spaces = _spaces(window)
    window.resize(1100, 760)
    window.show()
    window.sidebar.use_native(True)
    for space in spaces[:3]:  # (switched to quickly, each animation cut short by the next)
        window.switch_space(space)
        assert wait_until(lambda: window.store.active_space is space, 2)
        open_page(window, f"{site}/article.html")
    window.switch_space(spaces[0])
    wait_until(lambda: False, 1.5)
    body = window.sidebar.native
    target = _scroll_view(ctypes.c_void_p(body.page.view()))
    middle = body.container.mapToGlobal(body.container.rect().center())
    for flick, expected in ((-30, 1), (-30, 2), (30, 1)):
        started = time.perf_counter()
        _swipe(target, (middle.x(), middle.y()), flick, [], then=0)
        assert wait_until(lambda: window.store.active_space is spaces[expected], 1.5), \
            (flick, spaces.index(window.store.active_space))
        assert time.perf_counter() - started < 1.2  # (a swipe, its momentum and a moment after: not seconds)


def test_a_flicks_long_momentum_doesnt_hold_the_window_back(window, monkeypatch):
    """A trackpad's momentum runs on a second or more after a flick, long after the Space has snapped into place:
    waited out, the window changed Space seconds after the sidebar."""
    from browser import sidebar_native
    monkeypatch.setenv("ECLIPSE_NATIVE", "1")
    if not sidebar_native.available():
        pytest.skip("the native sidebar needs macOS 14 and the native pages built")
    _no_app_nap()
    spaces = _spaces(window)
    window.resize(1100, 760)
    window.show()
    window.sidebar.use_native(True)
    wait_until(lambda: False, 0.8)
    body = window.sidebar.native
    target = _scroll_view(ctypes.c_void_p(body.page.view()))
    middle = body.container.mapToGlobal(body.container.rect().center())
    started, changed = time.perf_counter(), []

    def frame():
        if not changed and window.store.active_space is spaces[1]:
            changed.append(time.perf_counter() - started)
    _swipe(target, (middle.x(), middle.y()), -30, [], then=300, coast_ms=1600, frame=frame)
    assert changed, "the window never changed Space"
    assert changed[0] < 0.9, changed[0]  # (the momentum runs till about 1.7 s)


def test_the_spaces_either_side_are_ready_before_you_get_there(window, site, monkeypatch):
    """A tab's page is only made the first time it's shown, so the first switch to a Space after opening Eclipse
    showed its page a second or two late. The Spaces beside the one you're on have theirs made in the background,
    and a swipe heading for a Space starts its page on the way."""
    from browser.models import TODAY, Tab
    monkeypatch.setattr(type(window), "PREPARE_AFTER_MS", 50)
    window._preparing = None
    store = window.store
    while len(store.spaces) < 4:
        store.add_space(f"Space {len(store.spaces) + 1}", "#3b82f6")
    tabs = []
    for n, space in enumerate(store.spaces[:4]):
        tab = Tab(f"{site}/article.html#{n}", f"Page {n}")
        store.add_tab(tab, TODAY, space)
        space.active_tab_id = tab.id
        tabs.append(tab)
    window.activate_tab(tabs[1])
    assert wait_until(lambda: tabs[0].view is not None and tabs[2].view is not None, 3)
    assert tabs[3].view is None  # (two over: not yet)
    assert wait_until(lambda: tabs[2].view.url().toString().endswith("#2"), 5)  # (loading, hidden, before you go)
    # In Low Power Mode, nothing's made ahead
    window.saving_minutes = 5
    window.prepare_space(store.spaces[3])
    assert tabs[3].view is None
    window.saving_minutes = None
    # A swipe heading for a Space: its page starts on the way (the native sidebar says where it's heading)
    from browser import sidebar_native
    monkeypatch.setenv("ECLIPSE_NATIVE", "1")
    if sidebar_native.available():
        window.sidebar.use_native(True)
        window.sidebar.native._act("heading", {"id": store.spaces[3].id})
        assert tabs[3].view is not None
