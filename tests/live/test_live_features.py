# Eclipse's features, end to end, in the real window: swipes, Now Playing, Browse for Me, Easels, GitHub,
# Boosts and Ask About This Page. Run with:  ./run.sh --live-tests

import time

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QApplication

from live_helpers import lift, loaded, open_page, pause, run_js, swipe, wait_until


def _sidebar_swipe(window, steps, step_dx, gap=0.016, end=True, begin=True, clock=None):
    """Two fingers sideways on the sidebar: `steps` moves of `step_dx` points each. With `clock` (see
    _steady_clock), the swipe's speed is measured as `gap` apart however long the Mac takes per event."""
    target = window.sidebar.scroll.viewport()
    at = QPointF(60, 200)

    def send(phase, dx):
        if clock is not None:
            clock[0] += gap
        QApplication.sendEvent(target, QWheelEvent(at, QPointF(target.mapToGlobal(at.toPoint())), QPoint(int(dx), 0),
                                                   QPoint(int(dx) * 4, 0), Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier, phase, False))
        QApplication.processEvents()  # (one event per pass of the event loop, as from a trackpad)
    if begin:
        send(Qt.ScrollPhase.ScrollBegin, 0)
    for _ in range(steps):
        send(Qt.ScrollPhase.ScrollUpdate, step_dx)
        time.sleep(gap)
    if end:
        send(Qt.ScrollPhase.ScrollEnd, 0)


def _steady_clock(monkeypatch):
    """A clock for the gesture code that only moves as _sidebar_swipe sends events: on a busy Mac a quick
    flick would otherwise arrive as a slow drag (each event late), which rightly isn't a flick."""
    import types
    import browser.gestures
    clock = [1000.0]
    monkeypatch.setattr(browser.gestures, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def _two_spaces(window):
    if len(window.store.spaces) < 2:
        window.store.add_space("Work", "#22c55e")
        window.sidebar.rebuild()
        pause(0.3)
    return window.store.spaces[:2]


GITHUB_RELEASES = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Release notes from qtwebengine</title>
<entry><title>v6.12.0</title><link rel="alternate" type="text/html" href="https://github.com/qt/qtwebengine/releases/tag/v6.12.0"/></entry>
<entry><title>v6.11.2</title><link rel="alternate" type="text/html" href="https://github.com/qt/qtwebengine/releases/tag/v6.11.2"/></entry>
</feed>"""


def test_swipe_goes_back_and_forward_but_not_over_a_scroller(window, site):
    tab = open_page(window, f"{site}/index.html")
    tab.view.load(QUrl(f"{site}/carousel.html"))
    assert loaded(tab.view)
    run_js(tab.view, "document.getElementById('c').scrollLeft = 500")
    swipe(tab.view, 400, QPoint(200, 150))  # over the carousel, which can still scroll left
    pause(1)
    assert tab.view.url().fileName() == "carousel.html"
    swipe(tab.view, 400)  # on the page itself
    assert wait_until(lambda: tab.view.url().fileName() == "index.html", 5)
    swipe(tab.view, -400)
    assert wait_until(lambda: tab.view.url().fileName() == "carousel.html", 5)


def test_now_playing_card_controls_a_background_tab(window, site):
    window.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    radio = open_page(window, f"{site}/music.html")
    assert wait_until(lambda: window.tab_audio(radio)[0], 8), "the page never played"
    open_page(window, f"{site}/index.html")
    card = window.feature("now_playing").card
    assert wait_until(lambda: card.isVisible(), 5)
    assert card.title.text() == "Blue in Green" and card.subtitle.text() == "Miles Davis"
    card.play_btn.click()
    assert wait_until(lambda: run_js(radio.view, "document.documentElement.getAttribute('data-pb-media')") == "paused", 5)


def test_space_swipe_follows_the_fingers_and_springs_back(window, site):
    open_page(window, f"{site}/index.html")
    first, second = _two_spaces(window)
    width = window.sidebar.width()
    _sidebar_swipe(window, 10, -width * 0.04, gap=0.03, end=False)  # 40%, slowly, fingers still down
    slide = window.feature("gestures").space_swipe.slide
    assert slide is not None and slide.after is not None
    assert abs(slide.travel - width * 0.4) < width * 0.05  # it's where the fingers are, not snapped
    pause(0.15)  # resting before letting go: no flick
    _sidebar_swipe(window, 0, 0, begin=False)
    assert wait_until(lambda: window.feature("gestures").space_swipe.slide is None, 2)
    pause(0.1)
    assert window.store.active_space is first  # not far enough: back where it was


def test_space_swipe_past_halfway_or_a_flick_switches(window, site, monkeypatch):
    open_page(window, f"{site}/index.html")
    first, second = _two_spaces(window)
    width = window.sidebar.width()
    clock = _steady_clock(monkeypatch)
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.03, clock=clock)  # 70%, then let go
    assert wait_until(lambda: window.feature("gestures").space_swipe.slide is None, 2)
    assert window.store.active_space is second
    _sidebar_swipe(window, 4, width * 0.05, gap=0.008, clock=clock)  # a short quick flick back
    assert wait_until(lambda: window.feature("gestures").space_swipe.slide is None, 2)
    assert window.store.active_space is first


def test_space_swipe_with_one_space_only_gives_a_little(window, site):
    open_page(window, f"{site}/index.html")
    only = window.store.active_space
    window.store.spaces[:] = [only]
    window.sidebar.rebuild()
    pause(0.2)
    _sidebar_swipe(window, 20, -30, end=False)
    assert window.feature("gestures").space_swipe.slide.travel < 80  # 600 points of swiping, under 80 of movement
    _sidebar_swipe(window, 0, 0, begin=False)
    assert wait_until(lambda: window.feature("gestures").space_swipe.slide is None, 2)
    assert window.store.active_space is only


def test_the_page_follows_a_back_swipe_over_a_picture_of_the_last_one(window, site):
    tab = open_page(window, f"{site}/index.html")
    pause(0.3)
    tab.view.load(QUrl(f"{site}/links.html"))
    assert loaded(tab.view)
    pause(0.3)
    seen = {}

    def look():
        gesture = window.feature("gestures").swipes._gestures.get(id(tab.view))
        seen["slide"] = gesture and gesture.slide
        seen["travel"] = gesture and gesture.slide and gesture.slide.travel
    swipe(tab.view, 240, end=False, during=look)  # 9 of 12 steps in: 180 points
    slide = seen["slide"]
    assert slide is not None and abs(seen["travel"] - (180 - 14)) < 25  # right under the fingers
    shot = window.feature("gestures").swipes.snapshots.get(tab.view, 0, QUrl(f"{site}/index.html"))
    assert shot is not None and slide.other is shot  # the real page underneath, not a placeholder
    lift(tab.view)  # resting at 240 of ~830: not far enough
    assert wait_until(lambda: not slide.isVisible(), 3)
    pause(0.3)
    assert tab.view.url().fileName() == "links.html"
    swipe(tab.view, 420)  # far enough this time
    assert wait_until(lambda: tab.view.url().fileName() == "index.html", 5)
    assert wait_until(lambda: not window.feature("gestures").swipes._landing, 3)  # the picture lifted once it arrived


def test_swiping_straight_back_shows_only_the_two_spaces(window, site):
    from browser.gestures import SpaceSlide
    first, second = _two_spaces(window)
    open_page(window, f"{site}/index.html")
    width = window.sidebar.width()
    swipes = window.feature("gestures").space_swipe
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.01)  # over to the second, let go
    assert wait_until(lambda: swipes.slide is None, 4)
    _sidebar_swipe(window, 6, width * 0.05, gap=0.01, end=False)  # straight back, while it's still fading
    assert wait_until(lambda: swipes.slide is not None, 2)  # (a busy machine can take a moment to start it)
    # The last one's gone, not baked into this one's pictures
    assert wait_until(lambda: [c for c in window.findChildren(SpaceSlide) if c.isVisible()] == [swipes.slide], 2)
    _sidebar_swipe(window, 8, width * 0.05, gap=0.01, begin=False)
    assert wait_until(lambda: swipes.slide is None, 4)
    pause(0.3)
    assert window.store.active_space is first
    assert not [c for c in window.findChildren(SpaceSlide) if c.isVisible()]


def test_a_keyboard_switch_mid_swipe_never_wedges_the_sidebar(window, site):
    first, second = _two_spaces(window)
    open_page(window, f"{site}/index.html")
    width = window.sidebar.width()
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.01)  # let go: it's settling…
    assert window.feature("gestures").space_swipe.state == "settling"
    window.cycle_space(1)  # …and ⌃⌥→ (or a Space dot) comes in now
    pause(0.4)
    assert window.feature("gestures").space_swipe.slide is None and window.feature("gestures").space_swipe.state not in ("mine", "settling")
    # The tab list still scrolls, and Space swipes still work
    target = window.sidebar.scroll.viewport()
    at = QPointF(60, 200)
    for phase, dy in ((Qt.ScrollPhase.ScrollBegin, 0), (Qt.ScrollPhase.ScrollUpdate, 20), (Qt.ScrollPhase.ScrollUpdate, 20)):
        event = QWheelEvent(at, QPointF(target.mapToGlobal(at.toPoint())), QPoint(0, dy), QPoint(0, dy * 4),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, phase, False)
        used = window.feature("gestures").space_swipe.wheel(event)
        QApplication.processEvents()
    assert used is False  # a vertical scroll is the tab list's, not swallowed
    _sidebar_swipe(window, 0, 0, begin=False)
    before = window.store.active_space
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.01)
    assert wait_until(lambda: window.feature("gestures").space_swipe.slide is None, 2)
    assert window.store.active_space is not before


def test_a_second_back_swipe_while_the_first_lands_leaves_no_old_picture(window, site):
    from browser.gestures import PageSlide
    tab = open_page(window, f"{site}/index.html")
    for page in ("links.html", "img.html"):
        tab.view.load(QUrl(f"{site}/{page}"))
        assert loaded(tab.view)
        pause(0.2)
    target = tab.view.focusProxy()
    at = QPoint(600, 450)

    def send(phase, dx):
        QApplication.sendEvent(target, QWheelEvent(QPointF(at), QPointF(target.mapToGlobal(at)), QPoint(int(dx), 0),
                                                   QPoint(int(dx) * 4, 0), Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier, phase, False))
    for _ in range(2):  # two quick flicks back, the second while the first is still landing
        send(Qt.ScrollPhase.ScrollBegin, 0)
        for _ in range(8):
            send(Qt.ScrollPhase.ScrollUpdate, 60)
            pause(0.016)
        send(Qt.ScrollPhase.ScrollMomentum, 30)
        pause(0.05)
    assert wait_until(lambda: not [s for s in tab.view.findChildren(PageSlide) if s.isVisible()], 4)
    assert tab.view.url().fileName() == "index.html"  # back twice


def test_a_slow_page_swipe_doesnt_swallow_the_mouse_wheel(window, site):
    tab = open_page(window, f"{site}/index.html")
    tab.view.load(QUrl(f"{site}/links.html"))
    assert loaded(tab.view)
    swipe(tab.view, 120)  # a short, slow swipe: it springs back, the fingers lift without a glide
    pause(0.4)
    assert id(tab.view) not in window.feature("gestures").swipes._gliding


def test_closing_a_browse_for_me_tab_stops_its_search(window, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal
    from browser import window as window_module

    class FakeRun(QObject):
        status, sources_found, partial = pyqtSignal(str), pyqtSignal(list), pyqtSignal(str)
        finished, failed = pyqtSignal(str, str), pyqtSignal(str)
        made = []

        def __init__(self, *args):
            super().__init__()
            self.cancelled = False
            FakeRun.made.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True
    import browser.claude_feature as claude_module
    claude = window.feature("claude")
    monkeypatch.setattr(claude_module, "BrowseForMe", FakeRun)
    monkeypatch.setattr(type(claude.claude), "available", property(lambda self: True))
    claude.browse_for_me("best espresso grinder")
    run = FakeRun.made[-1]
    tab = window.current_tab
    assert not run.cancelled
    window.close_tab(tab)
    assert wait_until(lambda: run.cancelled, 1)
    assert run not in claude._browse_runs


def test_easel_handles_resize_and_the_easel_exports(window, tmp_path, monkeypatch):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QGraphicsRectItem
    from browser.easel import _movable, geometry
    window.store.settings["ytdlp"] = {"folder": str(tmp_path)}
    tab = window.feature("easels").new_easel()
    easel = tab.view
    assert wait_until(lambda: easel.isVisible(), 3)
    box = _movable(QGraphicsRectItem(QRectF(0, 0, 120, 80)), "rect")
    easel.scene.addItem(box)
    image = QImage(200, 100, QImage.Format.Format_ARGB32)
    image.fill(QColor("teal"))
    picture = easel.add_image(image, at=QRectF(0, 200, 0, 0).topLeft())
    easel.canvas.resetTransform()
    easel.canvas.centerOn(100, 150)
    pause(0.1)
    # Pick the box and drag its bottom-right handle with the mouse
    easel.scene.clearSelection()
    box.setSelected(True)
    viewport = easel.canvas.viewport()
    start = easel.canvas.mapFromScene(geometry(box).bottomRight())
    end = start + QPoint(80, 40)

    def mouse(kind, at, buttons):
        event = QMouseEvent(kind, QPointF(at), QPointF(viewport.mapToGlobal(at)), Qt.MouseButton.LeftButton,
                            buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(viewport, event)

    mouse(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
    for step in range(1, 6):
        mouse(QEvent.Type.MouseMove, start + (end - start) * step / 5, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)
    assert geometry(box).size().toSize().width() == 200 and geometry(box).size().toSize().height() == 120
    assert geometry(box).topLeft() == QPointF(0, 0)  # the opposite corner stayed put
    # One undo step for the whole drag
    easel.undo()
    pause(0.05)
    restored = [i for i in easel.scene.items() if i.data(0) == "rect"][0]
    assert geometry(restored).width() == 120
    # A picture keeps its shape however the handle is dragged
    easel.scene.clearSelection()
    picture = [i for i in easel.scene.items() if i.data(0) == "image"][0]
    picture.setSelected(True)
    start = easel.canvas.mapFromScene(geometry(picture).bottomRight())
    mouse(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseMove, start + QPoint(200, 0), Qt.MouseButton.LeftButton)
    mouse(QEvent.Type.MouseButtonRelease, start + QPoint(200, 0), Qt.MouseButton.NoButton)
    shape = geometry(picture)
    assert round(shape.width()) == 400 and round(shape.height()) == 200

    # Export: copy, PNG, PDF, each from the toolbar's menu
    saved = {}
    monkeypatch.setattr(window, "toast", lambda text, *a, **k: saved.update(text=text))
    easel.export.emit("copy")
    assert saved["text"] == "Copied the easel as an image" and not QApplication.clipboard().image().isNull()
    easel.export.emit("png")
    easel.export.emit("pdf")
    pngs, pdfs = list(tmp_path.glob("*.png")), list(tmp_path.glob("*.pdf"))
    assert [p.name for p in pngs] == ["Untitled Easel.png"] and [p.name for p in pdfs] == ["Untitled Easel.pdf"]
    assert QImage(str(pngs[0])).width() > 0 and pdfs[0].read_bytes().startswith(b"%PDF")
    easel.export.emit("png")  # a second one doesn't overwrite the first
    assert (tmp_path / "Untitled Easel 2.png").exists()
    from PyQt6.QtWidgets import QToolButton
    share = next(b for b in easel.toolbar.findChildren(QToolButton) if b.menu() is not None)
    assert [a.text() for a in share.menu().actions()] == ["Copy as Image", "Save as PNG", "Save as PDF"]
    window.close_tab(tab)


def test_follow_releases_of_a_github_repo(window, monkeypatch):
    from browser.models import Tab
    asked = []

    def fake_get(url, done, fail, headers=None, secret=False):  # (GitHub, without the network)
        asked.append(url)
        QTimer.singleShot(0, lambda: done(GITHUB_RELEASES, url))
    monkeypatch.setattr(window.feature("live_folders").folders, "_get", fake_get)
    toasts = []
    monkeypatch.setattr(window, "toast", lambda text, *a, **k: toasts.append(text))
    # ⌘T offers it on any page of a repo, and nowhere else
    previous = window.current_tab
    window.current_tab = Tab("https://github.com/qt/qtwebengine/pulls", "Pull requests")
    try:
        follow = [c for c in window.commands() if c[0].startswith("Follow Releases")]
    finally:
        window.current_tab = previous
    assert [c[0] for c in follow] == ["Follow Releases of qt/qtwebengine"]
    assert not [c for c in window.commands() if c[0].startswith("Follow Releases")]
    follow[0][2]()
    assert wait_until(lambda: any("up to date" in t for t in toasts), 3), toasts
    assert asked == ["https://github.com/qt/qtwebengine/releases.atom"]
    folder = next(f for f in window.store.active_space.pinned if getattr(f, "live", None)
                  and f.live.get("url") == "https://github.com/qt/qtwebengine")
    assert folder.name == "qtwebengine Releases"
    assert [t.title for t in folder.children] == ["v6.12.0", "v6.11.2"]
    # A feed folder given a repo's address names itself from GitHub's feed title, tidied
    window.feature("live_folders").create_live_folder({"kind": "feed", "url": "https://github.com/qt/qtbase"}, "qtbase")
    assert wait_until(lambda: any(f.name == "qtwebengine Releases" and f is not folder
                                  for f in window.store.active_space.pinned if getattr(f, "live", None)), 3)


def test_github_folder_fills_its_sub_folders(window, site, monkeypatch):
    from browser.live_folders import OVERVIEW
    from browser.models import Folder, walk_tabs
    from browser.sidebar import FolderRow
    answers = {
        "review": [{"title": "Fix login", "url": f"{site}/index.html?pr7", "repository": {"name": "parliament"},
                    "number": 7, "isPullRequest": True}],
        "mine": [], "assigned": [], "notifications": None,  # (None: gh can't read them)
        "yours": [{"title": "Dark mode", "url": f"{site}/index.html?i9", "repository": {"name": "eclipse"},
                   "number": 9, "isPullRequest": False}],
    }

    def fake_gh(args, done, fail):  # (gh, answered from here)
        key = next(k for k, a in OVERVIEW.items() if a == args)
        QTimer.singleShot(0, lambda: done(answers[key]) if answers[key] is not None else fail("HTTP 403"))
    monkeypatch.setattr(window.feature("live_folders").folders, "_gh", fake_gh)
    monkeypatch.setattr("browser.live_folders.find_tool", lambda name: "/usr/bin/true")
    toasts = []
    monkeypatch.setattr(window, "toast", lambda text, *a, **k: toasts.append(text))
    add = next(c for c in window.commands() if c[0] == "Add GitHub Folder (PRs, Issues, Repos)")
    add[2]()
    assert wait_until(lambda: "“GitHub” is up to date" in toasts, 3), toasts
    folder = next(f for f in window.store.active_space.pinned if isinstance(f, Folder) and f.live
                  and f.live.get("query") == "overview")
    assert [(f.name, [t.title for t in f.children]) for f in folder.children] == \
        [("Review Requested", ["Fix login · parliament"]), ("eclipse", ["#9 Dark mode"])]
    add[2]()  # one is enough
    assert toasts[-1] == "You have one already: “GitHub”"

    # Something new arrives: counted on the folder and on its sub-folder, until opened
    answers["yours"].append({"title": "Menu bug", "url": f"{site}/index.html?i10", "repository": {"name": "eclipse"},
                             "number": 10, "isPullRequest": False})
    window.feature("live_folders").folders.refresh(folder)
    assert wait_until(lambda: any(t.fresh for t in walk_tabs(folder.children)), 3)
    for sub in folder.children:
        sub.expanded = True
    window.store._changed()
    pause(0.2)
    rows = {r.folder.name: r for r in window.sidebar.findChildren(FolderRow) if r.isVisible()}
    assert rows["GitHub"].count_label.text() == "1 new" and rows["eclipse"].count_label.text() == "1 new"
    fresh = next(t for t in walk_tabs(folder.children) if t.fresh)
    window.activate_tab(fresh)
    pause(0.1)
    assert not fresh.fresh and rows["eclipse"].count_label.text() == "" and rows["GitHub"].count_label.text() == ""
    # A sub-folder can't be dragged out, and nothing can be dropped in
    from browser.models import PINNED, TODAY
    window.store.move_tab(folder.children[1], PINNED, index=0)
    assert folder.children[1].name == "eclipse"
    # What you're reading leaves the folder (an issue closed, a notification read): it stays open, in Today
    reading = window.current_tab
    answers["yours"] = [r for r in answers["yours"] if r["url"] != reading.url]
    toasts.clear()
    window.feature("live_folders").folders.refresh(folder)
    assert wait_until(lambda: toasts or not any(t is reading for t in walk_tabs(folder.children)), 3)
    pause(0.1)
    assert window.current_tab is reading and window.store.locate(reading)[0] == TODAY
    assert reading.view is not None


def test_a_pull_requests_chip_shows_its_checks(window, monkeypatch):
    from browser.models import Tab
    answer = {"state": "OPEN", "reviewDecision": "APPROVED", "statusCheckRollup": [
        {"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "FAILURE",
         "detailsUrl": "https://github.com/o/r/actions/runs/1/job/2", "workflowName": "CI"},
        {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS",
         "detailsUrl": "https://github.com/o/r/actions/runs/1/job/3", "workflowName": "CI"}]}
    asked = []

    def fake_ask(pull, url):  # (gh, answered from here)
        asked.append(pull)
        QTimer.singleShot(0, lambda: (window.feature("github").pr_status._answers.__setitem__(pull, (time.time(), dict(answer))),
                                      window.feature("github").pr_status.changed.emit(url)))
    monkeypatch.setattr(window.feature("github").pr_status, "_ask", fake_ask)
    chip = window.feature("github").chip
    previous = window.current_tab
    window.current_tab = Tab("https://github.com/o/r/pull/12/files", "PR")
    try:
        window.feature("github").update_pill(window.current_tab)
        assert not chip.isVisible() and asked == [("o", "r", 12)]  # (until gh answers)
        assert wait_until(lambda: chip.isVisible(), 2)
        assert chip.text() == "✕ 1 failing · Approved" and chip.property("state") == "fail"
        # Its menu: each check, failing first, then the checks page and Check Again
        shown = []

        class FakeMenu:
            def __init__(self, real):
                self.real = real

            def __getattr__(self, name):
                return getattr(self.real, name)

            def exec(self, *_):
                shown.extend(a.text() for a in self.real.actions() if a.text())
        real_menu = window._menu
        monkeypatch.setattr(window, "_menu", lambda: FakeMenu(real_menu()))
        window.feature("github").show_checks(QPoint(0, 0))
        assert shown == ["CI · test", "CI · lint", "Open Checks Page", "Check Again"]
        # Checks still running: it looks again by itself while you're there
        answer["statusCheckRollup"][0].update(status="IN_PROGRESS", conclusion="")
        window.feature("github").pr_status.lookup(window.current_tab.url, force=True)
        assert wait_until(lambda: chip.text() == "● 1 running · Approved", 2)
        assert window.feature("github").pr_status._poll.isActive()
    finally:
        window.current_tab = previous
        window.feature("github").update_pill(window.current_tab)
    assert not chip.isVisible() and not window.feature("github").pr_status._poll.isActive()  # elsewhere: no chip, no looking


def test_your_github_repos_are_in_the_command_bar(window):
    window.feature("github").repos.repos = [
        {"name": "gradtrack", "nameWithOwner": "me/gradtrack", "url": "https://github.com/me/gradtrack",
         "description": "Grad school deadlines", "isPrivate": True, "pushedAt": "2026-10-20"},
        {"name": "homebase", "nameWithOwner": "me/homebase", "url": "https://github.com/me/homebase",
         "description": "", "isPrivate": False, "pushedAt": "2026-10-26"}]
    bar = window.command_bar
    window.open_command_bar("new")
    bar.input.setText("grad")
    bar.update_results()
    rows = [bar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(bar.list.count())]
    hit = next(r for r in rows if r.badge == "GitHub")
    assert (hit.title, hit.subtitle) == ("gradtrack", "Grad school deadlines")
    assert not any(r.badge == "GitHub" and r.title == "homebase" for r in rows)
    bar.close_bar()


def test_zap_only_listens_to_itself(window, site):
    tab = open_page(window, f"{site}/index.html")
    window.feature("boosts").start_zap()
    pause(0.3)
    zapped = []
    real = window.feature("boosts").boosts.add_zapped
    window.feature("boosts").boosts.add_zapped = lambda site_, sel: (zapped.append(sel), real(site_, sel))[1]
    # The page pretending to be Zap: ignored
    run_js(tab.view, "console.log('ECLIPSE_ZAP:x{} input{background:url(//evil/a)}')")
    run_js(tab.view, "console.log('ECLIPSE_ZAP:h1')")
    pause(0.3)
    assert zapped == []
    # A real click with the tool: heard (its message comes from its own world, with its token)
    run_js(tab.view, "document.querySelector('h1').click()")
    assert wait_until(lambda: zapped == ["h1"], 3), zapped
    from browser.boosts import safe_selector
    assert not safe_selector("x{} input{background:url(//evil/a)}") and safe_selector("div.ad > p:nth-of-type(2)")
    window.feature("boosts").boosts.add_zapped = real


def test_ask_about_this_page_keeps_a_conversation_per_tab(window, site):
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal

    class Answer(QObject):
        partial = pyqtSignal(str)
        finished = pyqtSignal(str)
        failed = pyqtSignal(str)

        def cancel(self):
            pass

    asked = []

    class FakeAI:
        def ask(self, prompt):
            asked.append(prompt)
            answer = Answer()
            QTimer.singleShot(50, lambda: answer.finished.emit(f"Answer {len(asked)}"))
            return answer
    window.assistant = lambda other=False: None if other else FakeAI()
    window.assistant_name = lambda ai: "Claude"
    tab = open_page(window, f"{site}/article.html")
    page_width = window.content.width()
    window.feature("page_chat").toggle()
    chat = window.feature("page_chat").chat
    pause(0.1)
    # Beside the page, outside it, with the same gap as the sidebar's
    assert chat.isVisible() and window.content.width() == page_width - chat.width() - 12
    assert chat.mapTo(window, QPoint(0, 0)).x() == window.content.mapTo(window, QPoint(window.content.width(), 0)).x() + 12
    assert all(b.isVisible() for b in chat.starter_buttons)
    chat.starter_buttons[0].click()  # Summarize
    assert wait_until(lambda: "Answer 1" in chat.view.toPlainText(), 5)
    assert "<page>" in asked[0] and not any(b.isVisible() for b in chat.starter_buttons)
    chat.input.setText("And who wrote it?")
    chat.input.returnPressed.emit()
    assert wait_until(lambda: "Answer 2" in chat.view.toPlainText(), 5)
    assert "Answer: Answer 1" in asked[1]  # (the follow-up knows what was said)
    # Another tab has its own conversation; back again, this one's still there
    other = open_page(window, f"{site}/index.html")
    assert "Answer 1" not in chat.view.toPlainText() and chat.starter_buttons[0].isVisible()
    window.activate_tab(tab)
    pause(0.1)
    assert "Answer 2" in chat.view.toPlainText()
    chat.clear()
    assert "Answer" not in chat.view.toPlainText()
    window.feature("page_chat").toggle()
    pause(0.1)
    assert not chat.isVisible() and window.content.width() == page_width
    window.close_tab(other)


def test_the_ask_panel_resizes_and_pops_out_into_its_own_window(window, site):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from browser import page_chat
    window.store.settings[page_chat.ON_TOP_SETTING] = False  # (a test window must never float over yours)
    tab = open_page(window, f"{site}/article.html")
    chat = window.feature("page_chat").chat
    window.feature("page_chat").toggle()
    pause(0.1)
    width = chat.width()
    # Its edge, on the page's side, drags it wider
    edge = chat.edge
    assert edge.isVisible() and edge.mapTo(window, QPoint(0, 0)).x() == chat.mapTo(window, QPoint(0, 0)).x()
    QTest.mousePress(edge, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3, 100))
    QTest.mouseMove(edge, QPoint(3 - 60, 100))
    QTest.mouseRelease(edge, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(3 - 60, 100))
    pause(0.1)
    assert chat.width() == width + 60 and window.store.settings[page_chat.WIDTH_SETTING] == width + 60
    # Popped out: a window of its own, following the tab you're on
    chat.toggle_popped()
    pause(0.3)
    assert chat.popped and chat.window_.isVisible() and not window.content_layout.indexOf(chat) >= 0
    assert not chat.window_.windowFlags() & Qt.WindowType.WindowStaysOnTopHint and chat.pin_btn.isVisible()
    other = open_page(window, f"{site}/index.html")
    assert chat.tab is other
    chat.toggle_on_top()  # pinned: it stays on top...
    assert chat.window_.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    chat.toggle_on_top()  # ...and let go
    assert not chat.window_.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    # ⇧⌘A puts it away and brings it back where it was: in its window
    window.feature("page_chat").toggle()
    assert not chat.window_.isVisible()
    window.feature("page_chat").toggle()
    pause(0.2)
    assert chat.popped and chat.window_.isVisible()
    # And back beside the page, at the width it had
    chat.toggle_popped()
    pause(0.2)
    assert not chat.popped and chat.isVisible() and not chat.window_.isVisible() and chat.width() == width + 60
    window.feature("page_chat").toggle()
    window.close_tab(other)



def test_settings_shows_the_version_and_an_updates_progress(window):
    from PyQt6.QtWidgets import QLabel, QProgressBar, QPushButton
    updater = window.feature("self_update").updater
    window.open_settings("general")
    settings = window._settings
    title = next(l for l in settings.findChildren(QLabel, "SettingTitle") if l.text() == "Updates")
    row = title.parentWidget()
    line = row.findChildren(QLabel, "Hint")[0]
    install = next(b for b in row.findChildren(QPushButton) if b.text() == "Update && Restart")
    bar = row.findChildren(QProgressBar)[0]
    assert line.text().startswith("From source, commit") and install.isHidden() and bar.isHidden()
    updater._set_status({"state": "checking"})
    assert line.text().endswith("Checking…")
    found = {"state": "available", "build": 202611011200, "version": "Eclipse 2026.11.1",
             "subjects": ["Better tabs", "Faster"]}
    updater._set_status(found)
    assert "Eclipse 2026.11.1 is ready: Better tabs; Faster." in line.text() and not install.isHidden()
    updater._set_status({**found, "state": "downloading", "percent": 40})
    assert line.text().endswith("Downloading Eclipse 2026.11.1… 40%") and not bar.isHidden() and bar.value() == 40
    assert install.isHidden()
    updater._set_status({"state": "current", "behind": 0})
    assert line.text().endswith("Up to date.") and bar.isHidden()
    updater._set_status({"state": "idle"})
    settings.close()


def test_the_welcome_brings_bookmarks_history_and_passwords_over(window, tmp_path, monkeypatch):
    import hashlib
    import json
    import sqlite3
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton
    from browser import arc_import, browser_import, password_store, welcome
    from test_live import _stand_ins
    _stand_ins(monkeypatch)
    chrome = tmp_path / "Chrome" / "Default"
    chrome.mkdir(parents=True)
    (chrome / "Bookmarks").write_text(json.dumps({"roots": {"bookmark_bar": {"children": [
        {"type": "url", "name": "Docs", "url": "https://docs.example/"}]}}}))
    with sqlite3.connect(chrome / "History") as db:
        db.execute("CREATE TABLE urls (url TEXT, title TEXT, last_visit_time INTEGER, visit_count INTEGER, hidden INTEGER)")
        db.execute("INSERT INTO urls VALUES ('https://docs.example/', 'Docs', 13340000000000000, 9, 0)")
    key = hashlib.pbkdf2_hmac("sha1", b"chrome-key", b"saltysalt", 1003, 16)
    padder = padding.PKCS7(128).padder()
    sealed = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    value = b"v10" + sealed.update(padder.update(b"from chrome") + padder.finalize()) + sealed.finalize()
    with sqlite3.connect(chrome / "Login Data") as db:
        db.execute("CREATE TABLE logins (origin_url TEXT, username_value TEXT, password_value BLOB, "
                   "blacklisted_by_user INTEGER)")
        db.execute("INSERT INTO logins VALUES ('https://docs.example/login', 'me', ?, 0)", (value,))
    monkeypatch.setattr(password_store, "CHROMIUM_BROWSERS", {"Chrome": (tmp_path / "Chrome", "Chrome Safe Storage")})
    monkeypatch.setattr(browser_import, "CHROMIUM_BROWSERS", password_store.CHROMIUM_BROWSERS)
    monkeypatch.setattr(browser_import, "safari_installed", lambda: False)
    monkeypatch.setattr(arc_import, "arc_installed", lambda: False)
    monkeypatch.setattr(password_store, "browser_secret", lambda name: b"chrome-key")
    page = welcome.Welcome(window)
    page.show()
    boxes = [b.text() for b in page.findChildren(QCheckBox)]
    assert boxes[0] == "Chrome: Bookmarks and history" and boxes[1].startswith("Chrome's saved passwords")
    next(b for b in page.findChildren(QPushButton) if b.text() == "Import").click()
    assert wait_until(lambda: page.pages.currentIndex() == 1, 5), "didn't get to Make it yours"
    space = window.store.space("import-chrome-default")
    assert space is not None and space.pinned[0].url == "https://docs.example/"
    assert window.store.history[0]["url"] == "https://docs.example/"
    assert [(e["username"], e["password"]) for e in password_store.logins()] == [("me", "from chrome")]
    assert page.result.text() == ("Brought over: Chrome: 1 bookmark and 1 page of history; "
                                  "Chrome's passwords: 1 saved.")
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    page.swatches[2].click()  # (Mint)
    assert window.store.active_space.color == "#14b8a6"
    page.close()


def test_feedback_copies_a_report_and_starts_a_github_issue(window, monkeypatch):
    from PyQt6.QtWidgets import QApplication, QPushButton
    from browser import feedback
    opened = []
    monkeypatch.setattr(window, "open_url", opened.append)  # (the issue page isn't loaded from a test)
    window.send_feedback(crashed=True)
    page = window._feedback
    page.said.setPlainText("Opened Settings and it closed")
    next(b for b in page.findChildren(QPushButton) if b.text() == "Copy Report").click()
    copied = QApplication.clipboard().text()
    assert copied.startswith("Crash: Opened Settings and it closed\n\nOpened Settings and it closed\n\n---\nEclipse ")
    page.include.setChecked(False)
    assert page.text() == "Opened Settings and it closed"
    next(b for b in page.findChildren(QPushButton) if b.text() == "Open GitHub Issue").click()
    assert len(opened) == 1 and opened[0].startswith(feedback.ISSUES + "?title=Crash")


def test_settings_checks_for_breached_passwords_and_flags_them(window, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QPushButton
    from browser import password_store
    from test_live import _stand_ins
    _stand_ins(monkeypatch)
    password_store.add("https://leaky.example", "me", "password1")
    password_store.add("https://safe.example", "me", "Long-Unique-Pass-1")
    sent = []
    monkeypatch.setattr(password_store, "breached", lambda passwords: sent.append(sorted(passwords))
                        or {"password1": 2413945})
    window.open_settings("passwords")
    settings = window._settings
    title = next(l for l in settings.findChildren(QLabel, "SettingTitle") if l.text() == "Breached passwords")
    next(b for b in title.parentWidget().findChildren(QPushButton) if b.text() == "Check").click()
    flagged = lambda: [l.text() for l in settings.findChildren(QLabel) if "in a data breach" in l.text()]  # noqa: E731
    assert wait_until(lambda: flagged(), 5), "nothing flagged"
    assert flagged() == ["me · ⚠︎ in a data breach · ⚠︎ easy to guess"]
    assert sent == [["Long-Unique-Pass-1", "password1"]]
    settings.close()


def test_an_address_fills_a_checkout_but_not_a_newsletter(window, site):
    from browser import addresses
    addresses.save({"label": "Home", "name": "Arkan Example", "email": "arkan@example.com", "tel": "555 0100",
                    "street": "1 Main St\nApt 4", "city": "Springfield", "postal": "12345",
                    "country": "United States"})
    manager = window.feature("addresses").addresses
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/checkout.html")
    press = "document.getElementById('%s').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true})); 1"
    run_js(tab.view, press % "newsletter", world=0)  # (just an email: not worth a card)
    pause(0.6)
    assert manager.card_for(tab.view) is None
    run_js(tab.view, press % "city", world=0)
    assert wait_until(lambda: manager.card_for(tab.view) is not None, 5), "no card for the checkout"
    from test_live import _click_when_ready
    _click_when_ready(manager.card_for(tab.view), "Home · Arkan Example, 1 Main St")
    values = "['first','last','email','phone','line1','line2','city','zip','country'].map(i => document.getElementById(i).value)"
    assert wait_until(lambda: run_js(tab.view, values, world=0) == [
        "Arkan", "Example", "arkan@example.com", "already typed", "1 Main St", "Apt 4", "Springfield", "12345", "US"], 5)
    assert run_js(tab.view, "document.getElementById('newsletter').value", world=0) == ""  # (another form)
    addresses.delete(addresses.all_addresses()[0]["id"])
