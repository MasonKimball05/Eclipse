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



def test_a_shortcut_can_be_changed_in_settings(window, monkeypatch):
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QLabel, QMessageBox
    from browser import shortcuts
    window.open_settings("shortcuts")
    settings = window._settings
    item = {key: action for key, _g, _n, action in shortcuts.menu_items(window.menuBar())}

    def recorder(name):
        title = next(l for l in settings.shortcut_box.findChildren(QLabel) if l.text() == name and l.isVisible())
        return title.parentWidget().findChildren(shortcuts.KeyRecorder)[0]

    def type_into(name, key, mods):
        cap = recorder(name)
        cap.click()
        QTest.keyClick(cap, key, mods)
        pause(0.05)
    cmd, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
    type_into("Reader View", Qt.Key.Key_E, cmd | shift)
    assert shortcuts.portable(item["View › Reader View"].shortcut()) == "Ctrl+Shift+E"
    assert window.store.settings["shortcuts"] == {"View › Reader View": "Ctrl+Shift+E"}
    # Taking another item's shortcut asks first, and that item is left without one
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    type_into("Reader View", Qt.Key.Key_R, cmd)
    assert shortcuts.portable(item["View › Reload"].shortcut()) == "Ctrl+R"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    type_into("Reader View", Qt.Key.Key_R, cmd)
    assert shortcuts.portable(item["View › Reader View"].shortcut()) == "Ctrl+R"
    assert shortcuts.portable(item["View › Reload"].shortcut()) == ""
    # Searching finds items without one, to give them one
    settings.shortcut_filter.setText("share")
    pause(0.05)
    type_into("Share…", Qt.Key.Key_U, cmd | shift)
    assert shortcuts.portable(item["File › Share…"].shortcut()) == "Ctrl+Shift+U"
    settings.shortcut_filter.setText("")
    settings._reset_shortcuts(["View › Reload"])
    assert shortcuts.portable(item["View › Reload"].shortcut()) == "Ctrl+R"
    settings._reset_shortcuts()  # (Restore Defaults: asked, and said Yes above)
    assert window.store.settings["shortcuts"] == {}
    assert shortcuts.portable(item["View › Reader View"].shortcut()) == "Ctrl+Alt+R"
    assert shortcuts.portable(item["File › Share…"].shortcut()) == ""


def test_the_window_menu_lists_the_windows_and_minimizes_the_front_one(window):
    from browser import shortcuts
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Window")
    little = window.new_little_window("about:blank")
    wait_until(little.isVisible, 3)
    menu.aboutToShow.emit()
    listed = [a.text() for a in menu.actions() if a.property(shortcuts.DYNAMIC) and not a.isSeparator()]
    assert listed[0] == window.windowTitle() and "Little Window" in listed and listed[-1] == "Bring All to Front"
    menu.aboutToShow.emit()  # (refilled, not added to)
    assert [a.text() for a in menu.actions() if a.property(shortcuts.DYNAMIC) and not a.isSeparator()] == listed
    assert not any(key.startswith("Window › Little") for key, *_ in shortcuts.menu_items(window.menuBar()))
    # Minimize acts on the window in front (never really minimized here: it'd fly into your Dock as you work)
    minimized = []
    window._front_window = lambda: little
    little.showMinimized = lambda: minimized.append(little)
    window.showMinimized = lambda: minimized.append(window)
    next(a for a in menu.actions() if a.text() == "Minimize").trigger()
    assert minimized == [little]
    little.close()


def test_the_history_menu_lists_recent_pages(window):
    from browser import shortcuts
    for n in range(15):
        window.store.record_history(f"https://example{n}.com/", f"Example {n} & Co")
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "History")
    for _ in range(2):  # (refilled each time, not added to)
        menu.aboutToShow.emit()
        pages = [a for a in menu.actions() if a.property(shortcuts.DYNAMIC) and a.isEnabled() and not a.isSeparator()]
        assert len(pages) == 12 and pages[0].text() == "Example 14 && Co" and pages[0].toolTip() == "https://example14.com/"
    assert not any(key.startswith("History › Example") for key, *_ in shortcuts.menu_items(window.menuBar()))
    opened = []
    window.open_url = lambda url, **_: opened.append(url)
    pages[1].trigger()
    assert opened == ["https://example13.com/"]


def test_closing_the_window_keeps_eclipse_running_and_the_dock_brings_it_back(window, site):
    import sys
    from PyQt6.QtGui import QCloseEvent
    if sys.platform != "darwin":
        return

    class RedButton(QCloseEvent):  # (a close from the window system, as clicking the red button is)
        def spontaneous(self):
            return True
    tab = open_page(window, site + "/music.html")
    run_js(tab.view, "window.playing = document.createElement('audio'); window.playing.pause = () => "
                     "(window.paused = true); document.body.append(window.playing); 1", world=0)
    event = RedButton()
    window.closeEvent(event)
    assert not event.isAccepted() and window.isHidden()
    assert not QApplication.instance().quitOnLastWindowClosed()
    assert wait_until(lambda: run_js(tab.view, "window.paused === true", world=0), 3)  # (nothing plays on)
    window._app_state_changed(Qt.ApplicationState.ApplicationActive)  # (the Dock icon, clicked)
    assert wait_until(lambda: not window.isHidden(), 3) and window.current_tab is tab
    # Turned off, the red button closes it as before
    window.store.settings["keep_running"] = False
    window.apply_keep_running()
    assert not window.keeps_running() and QApplication.instance().quitOnLastWindowClosed()
    window.store.settings.pop("keep_running")
    window.apply_keep_running()
    window.close_tab(tab)


def test_a_space_is_shared_and_imported(window, monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    from browser import macos, space_share
    from browser.models import Folder, Tab
    space = window.store.active_space
    space.pinned.append(Folder("Shared", [Tab("https://example.com/", "Example", home_url="https://example.com/")]))
    shared = []
    monkeypatch.setattr(macos, "share", lambda widget, url, anchor: shared.append(url) or True)
    window.share_space(space)
    assert len(shared) == 1 and shared[0].startswith("file:///") and shared[0].endswith(".eclipse-space")
    assert "%20" in shared[0] or " " not in shared[0]  # (an address macOS takes: spaces encoded)
    from PyQt6.QtCore import QUrl
    path = QUrl(shared[0]).toLocalFile()
    # Opened on the other Mac (here, the same one): asked first, then added as a Space of its own
    answers = iter(["Cancel", "Add Space"])

    def answer(box):
        wanted = next(answers)
        next(b for b in box.buttons() if b.text().replace("&", "") == wanted).click()
    monkeypatch.setattr(QMessageBox, "exec", answer)
    count = len(window.store.spaces)
    window.import_space_file(path)
    assert len(window.store.spaces) == count  # (Cancel)
    window.import_space_file(path)
    added = window.store.spaces[-1]
    assert len(window.store.spaces) == count + 1 and window.store.active_space is added
    assert added.name == space.name and added.id != space.id
    folder = next(n for n in added.pinned if isinstance(n, Folder) and n.name == "Shared")
    assert [t.url for t in folder.children] == ["https://example.com/"]
    # Not a Space: says so, adds nothing
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warned.append(a[2]))
    bad = tmp_path / "bad.eclipse-space"
    bad.write_text("{}")
    window.import_space_file(str(bad))
    assert warned == ["That isn't a shared Space."] and len(window.store.spaces) == count + 1
    # From Finder (double-clicked, or dropped on the Dock icon): macOS sends it as a file to open
    from PyQt6.QtCore import QEvent
    from PyQt6.QtNetwork import QLocalServer
    from browser.app import LinkReceiver

    class FileOpen:  # (what macOS sends; Qt won't make one of its own here)
        type = staticmethod(lambda: QEvent.Type.FileOpen)
        url = staticmethod(lambda: QUrl.fromLocalFile(path))
        file = staticmethod(lambda: path)
    imported = []
    monkeypatch.setattr(window, "import_space_file", imported.append)
    assert LinkReceiver(window, QLocalServer()).eventFilter(None, FileOpen())
    assert imported == [path]
    window.store.remove_space(added)
    window.switch_space(space)


def test_the_page_youre_on_is_offered_to_your_other_devices(window, site, monkeypatch):
    from PyQt6.QtGui import QCloseEvent
    from browser import macos
    offered = []  # (never really offered from a test: it'd show on your iPhone)
    monkeypatch.setattr(macos, "handoff", lambda url, title="": offered.append((url, title)) or True)
    monkeypatch.setattr(macos, "end_handoff", lambda: offered.append(None))
    window.store.settings["handoff"] = True  # (off in tests: see conftest)
    tab = open_page(window, site + "/article.html")
    assert wait_until(lambda: offered and offered[-1] == (tab.url, tab.label) and tab.label != tab.url, 5)
    # Closing the window (Eclipse keeps running) stops it; the Dock bringing it back offers it again
    class RedButton(QCloseEvent):
        def spontaneous(self):
            return True
    if window.keeps_running():
        window.closeEvent(RedButton())
        assert offered[-1] is None
        window._app_state_changed(Qt.ApplicationState.ApplicationActive)
        window.feature("handoff")._app_state(Qt.ApplicationState.ApplicationActive)
        assert offered[-1] == (tab.url, tab.label)
    # Turned off in Settings: nothing's offered
    window.store.settings["handoff"] = False
    window.feature("handoff").offer()
    assert offered[-1] is None
    window.close_tab(tab)


def test_a_page_handed_over_from_your_iphone_opens_in_a_tab(window, monkeypatch):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtNetwork import QLocalServer
    from browser.app import LinkReceiver
    opened, fronted = [], []
    monkeypatch.setattr(window, "open_url", lambda url, **_: opened.append(url))
    monkeypatch.setattr(window, "bring_to_front", lambda: fronted.append(True))
    receiver = LinkReceiver(window, QLocalServer())
    receiver.listen_for_handoff()
    try:
        # (macOS gives Qt the page; Qt gives it to the app's handler for web links, as openUrl does)
        assert QDesktopServices.openUrl(QUrl("https://example.com/article?from=phone"))
        assert opened == ["https://example.com/article?from=phone"] and fronted == [True]
        receiver.continue_page(QUrl("file:///etc/passwd"))
        assert len(opened) == 1  # (only web pages)
    finally:
        receiver.stop()


def test_sync_brings_another_macs_changes_into_the_window(window, site, tmp_path, monkeypatch):
    import json
    from browser import sync
    from browser.models import Tab
    monkeypatch.setenv("ECLIPSE_SYNC_DIR", str(tmp_path / "iCloud"))
    (tmp_path / "iCloud").mkdir()
    space = window.store.active_space
    keep = Tab(site + "/index.html", "Kept", home_url=site.replace("127.0.0.1", "localhost") + "/index.html")
    drop = Tab(site + "/article.html", "Dropped", home_url="https://dropped.example/")
    space.pinned += [keep, drop]
    today = open_page(window, site + "/links.html")
    window.activate_tab(drop)
    window.activate_tab(keep)
    kept_view = keep.view
    assert kept_view is not None and drop.view is not None
    engine = window.feature("sync").sync
    window.store.settings["sync"] = True
    engine.refresh_setting()
    engine.run()
    folder = tmp_path / "iCloud" / sync.FOLDER
    mine = json.loads((folder / f"{engine.state['device']}.json").read_text())
    assert f"space:{space.id}" in mine["items"] and engine.devices == []
    # The other Mac renames the Space, unpins one tab, pins another, and adds a Space of its own
    other = json.loads(json.dumps(mine))
    other.update(device="mac2", name="Other Mac", written=mine["written"] + 60)
    item = other["items"][f"space:{space.id}"]
    item["modified"] = mine["written"] + 60
    item["data"]["name"] = "Renamed Elsewhere"
    item["data"]["pinned"] = [n for n in item["data"]["pinned"] if n["id"] != drop.id] + [
        {"id": "new-tab", "url": "https://new.example/", "title": "New", "custom_title": None}]
    other["items"]["space:mac2-space"] = {"modified": mine["written"] + 60, "data": {
        "name": "From the Other Mac", "color": "#22aa66", "icon": "", "theme": {},
        "pinned": [{"id": "x1", "url": "https://x.example/", "title": "X", "custom_title": None}]}}
    other["items"]["order"] = {"modified": mine["written"] + 60, "data": [s.id for s in window.store.spaces] + ["mac2-space"]}
    (folder / "mac2.json").write_text(json.dumps(other))
    engine.run()
    assert space.name == "Renamed Elsewhere" and engine.devices == ["Other Mac"]
    assert [t.id for t in space.pinned][-2:] == [keep.id, "new-tab"]
    assert keep.view is kept_view  # (still there: its page stays open)
    assert drop.view is None and window.current_tab is keep
    assert today in list(space.today)  # (Today's tabs aren't synced)
    added = window.store.space("mac2-space")
    assert added is not None and window.store.spaces[-1] is added and added.pinned[0].home_url == "https://x.example/"
    assert len(list((window.store.path.parent / "sync backups").glob("before-sync-*.zip"))) == 1
    # It settles: nothing more to do, and this Mac's file now has what came
    assert sync.merge(engine.state, engine.others()) == {"apply": {}, "delete": []}
    mine = json.loads((folder / f"{engine.state['device']}.json").read_text())
    assert mine["items"]["space:mac2-space"]["data"]["name"] == "From the Other Mac"
    # The other Mac deletes its Space: it goes here too
    other["items"].pop("space:mac2-space")
    other["deleted"] = {"space:mac2-space": mine["written"] + 120}
    (folder / "mac2.json").write_text(json.dumps(other))
    engine.run()
    assert window.store.space("mac2-space") is None
    window.store.settings["sync"] = False
    engine.refresh_setting()
    window.close_tab(today)


def test_turning_sync_on_can_use_the_other_macs_spaces(window, tmp_path, monkeypatch):
    import json
    from browser import sync
    monkeypatch.setenv("ECLIPSE_SYNC_DIR", str(tmp_path))
    folder = tmp_path / sync.FOLDER
    folder.mkdir()
    (folder / "mac2.json").write_text(json.dumps({
        "format": "eclipse-sync", "device": "mac2", "name": "Studio Mac", "written": 100, "deleted": {},
        "items": {"space:theirs": {"modified": 100, "data": {
            "name": "Their Work", "color": "#aa3366", "icon": "", "theme": {},
            "pinned": [{"id": "p1", "url": "https://their.example/", "title": "Theirs", "custom_title": None}]}},
                  "order": {"modified": 100, "data": ["theirs"]}}}))
    engine = window.feature("sync").sync
    mine = [s.name for s in window.store.spaces]
    assert engine.first_look() == {"name": "Studio Mac", "spaces": ["Their Work"]}
    engine.adopt()
    assert [s.name for s in window.store.spaces] == ["Their Work"] and window.store.active_space.name == "Their Work"
    assert len(list((window.store.path.parent / "sync backups").glob("*.zip"))) == 1  # (yours: kept first)
    assert mine and engine.first_look() is None  # (synced now: no more asking)
    window.store.settings["sync"] = True
    engine.refresh_setting()
    engine.run()
    assert sync.merge(engine.state, engine.others()) == {"apply": {}, "delete": []}
    assert [s.name for s in window.store.spaces] == ["Their Work"]
    window.store.settings["sync"] = False
    engine.refresh_setting()


def test_a_drag_that_goes_wrong_never_leaves_the_page_unclickable(window, site, monkeypatch):
    # Clicking a tab quickly while switching could start a drag that failed half-way: the page area's drop zone
    # (which takes clicks, to take a dropped tab) stayed up over the page, and the sidebar stopped rebuilding
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QDrag
    from PyQt6.QtTest import QTest
    from browser import sidebar as module
    first = open_page(window, site + "/index.html")
    second = open_page(window, site + "/links.html")
    zone = window.content.split_drop

    def broken(*_):
        raise RuntimeError("the row went while it was being dragged")
    monkeypatch.setattr(QDrag, "exec", broken)
    row = next(r for r in window.sidebar.today.rows if r.tab is first)
    try:
        module.start_drag(row, first.id, QPoint(5, 5))
    except RuntimeError:
        pass
    assert zone.isHidden() and not window.sidebar.drag_active
    # And should the zone ever be left up anyway, the first click on it puts it away
    zone.activate(True)
    assert not zone.isHidden()
    QTest.mouseClick(zone, Qt.MouseButton.LeftButton, pos=QPoint(50, 50))
    assert zone.isHidden()
    for tab in (first, second):
        window.close_tab(tab)


def test_a_folder_opens_and_closes_as_one_block(window):
    from browser.models import Folder, Tab
    from browser.sidebar import RowReveal
    space = window.store.active_space
    folder = Folder("Many", [Tab(f"https://example{n}.com/", f"Tab {n}") for n in range(12)], expanded=False)
    space.pinned.append(folder)
    window.store._changed()
    pause(0.3)
    section = window.sidebar.pinned
    window.toggle_folder(folder)
    pause(0.03)
    reveals = [r for r in section._reveals if not r.keep]
    assert len(reveals) == 1 and len(reveals[0].rows) == 12  # (one block, not twelve: and it animates, though it's big)
    block = reveals[0]
    animation = getattr(block, "_pb_animation", None)
    if animation is not None:
        animation.stop()  # (held at its last frame: where each row is then must be where it stays)
    block._set(1.0)
    pause(0.1)
    sliding = [section._rect(r).top() for r in block.rows]
    inserted = []
    real_insert = section.layout_.insertWidget
    # (when its rows go back into the list, their box must be out of it already: both in, for a frame
    # everything below dropped a block's height)
    section.layout_.insertWidget = lambda i, w: (inserted.append(section.layout_.indexOf(block)), real_insert(i, w))
    block._done()
    del section.layout_.insertWidget
    assert inserted and set(inserted) == {-1}
    pause(0.1)
    rows = [r for r, node, *_ in section.entries if node in folder.children]
    assert [section._rect(r).top() for r in rows] == sliding  # (no jump into place)
    assert [r.tab.title for r in rows] == [f"Tab {n}" for n in range(12)]
    assert all(r.parentWidget() is section and r.isVisible() for r in rows)
    tops = [section._rect(r).top() for r in rows]
    assert tops == sorted(tops) and len(set(tops)) == 12  # (in order, one under another)
    folder_row = next(r for r, n, *_ in section.entries if n is folder)
    folder_top = section._rect(folder_row).top()
    window.toggle_folder(folder)
    # (its first frame, before anything else happens: the box right under the folder, the list still tall
    # enough for it: the list was for a frame its closed height, everything in it on top of each other)
    closing = section._reveals[0]
    assert closing.geometry().top() == section._rect(folder_row).bottom() + 1 + section.layout_.spacing()
    assert section.height() >= closing.geometry().bottom()
    tops = []
    while closing in section._reveals:
        tops.append(section._rect(folder_row).top())
        QApplication.processEvents()
    assert set(tops) == {folder_top}  # (the folder's own row never moves: it bounced as its rows went)
    window.toggle_folder(folder)
    window.toggle_folder(folder)
    pause(0.03)
    assert len(section._reveals) == 1 and len(section._reveals[0].rows) == 12
    assert wait_until(lambda: not section._reveals, 2)
    assert not any(isinstance(r.parentWidget(), RowReveal) for r, *_ in section.entries)
    space.pinned.remove(folder)
    window.store._changed()


def test_a_page_opening_on_a_dark_space_is_never_a_white_flash(window, site):
    # Switching to a Space whose tab had no page yet (each one, the first time after launch; or asleep) showed
    # the page area white for half a second or more, until the page drew: a flash, on a dark Space
    from browser.window import DARK_PAGE_BLANK
    space = window.store.active_space
    space.theme = {**space.theme, "mode": "dark"}
    window.apply_theme()
    assert window.theme.dark
    tab = open_page(window, site + "/index.html")
    window._destroy_view(tab)
    view = window._ensure_view(tab, load=False)
    assert view.page().backgroundColor().name() == DARK_PAGE_BLANK
    view.load(QUrl(tab.url))
    assert wait_until(lambda: view.page().backgroundColor().name() == "#ffffff", 5)  # (then the web's own white)
    space.theme.pop("mode")
    window.apply_theme()
    window.close_tab(tab)


def test_youtubes_ads_are_taken_out_before_its_player_sees_them(window, site):
    from browser import adblock
    names = [(sc.name(), sc.worldId()) for sc in window.profile.scripts().toList()]
    assert ("eclipse-adblock", 0) in names  # (the pruner, in the page's own world, where YouTube's scripts are)
    tab = open_page(window, site + "/index.html")
    # (run here as if this were youtube.com)
    run_js(tab.view, adblock.YOUTUBE_PRUNER.replace("!/(^|\\.)youtube\\.com$/.test(location.hostname)", "false"),
           world=0)
    video = '{"videoDetails": {"title": "A video"}, "adPlacements": [1], "playerAds": [2], "adSlots": [3]}'
    assert run_js(tab.view, f"Object.keys(JSON.parse('{video}')).join()", world=0) == "videoDetails"
    assert run_js(tab.view, f"Object.keys(JSON.parse('{{\"playerResponse\": {video}}}').playerResponse).join()",
                  world=0) == "videoDetails"
    run_js(tab.view, f"window.ytInitialPlayerResponse = JSON.parse('{video}'.replace('adPlacements', 'x')); "
                     f"new Response('{video}').json().then(d => window.fetched = Object.keys(d).join()); 1", world=0)
    assert run_js(tab.view, "Object.keys(window.ytInitialPlayerResponse).join()", world=0) == "videoDetails,x"
    assert wait_until(lambda: run_js(tab.view, "window.fetched", world=0) == "videoDetails", 3)
    assert "native code" in run_js(tab.view, "JSON.parse.toString()", world=0)  # (looks untouched)
    window.close_tab(tab)


def test_a_narrow_sidebar_fits_its_search_pill_space_buttons_and_favorites(window):
    from browser.models import Tab
    from browser.sidebar import PILL_PLACEHOLDER, SPACE_BUTTON
    store, sidebar = window.store, window.sidebar
    for n in range(5):
        store.add_space(f"Space {n}", "#3366aa", "ph.code")
    store.favorites[:] = [Tab(f"https://f{n}.example/", f"F{n}", home_url=f"https://f{n}.example/") for n in range(5)]
    store._changed()
    sidebar.setFixedWidth(187)
    pause(0.5)
    # The placeholder is never cut from the left (only a site's address is): a narrow pill says "Search…"
    if window.current_tab is None:
        assert sidebar.url_pill.text() in (PILL_PLACEHOLDER, "Search…") and not sidebar.url_pill.text().startswith("…")
    # The Space buttons share the room: smaller, never on top of each other
    buttons = [b for b in sidebar.space_buttons if b.isVisible()]
    assert any(b.property("selected") for b in buttons)  # (the Space you're in always shows)
    assert all(b.width() < SPACE_BUTTON for b in buttons) and min(b.iconSize().width() for b in buttons) >= 12
    assert min(b.width() for b in buttons) >= 18
    lefts = [b.geometry().left() for b in buttons]
    rights = [b.geometry().right() for b in buttons]
    assert all(rights[i] < lefts[i + 1] for i in range(len(buttons) - 1))
    sidebar.setFixedWidth(420)
    pause(0.5)
    assert all(b.width() == SPACE_BUTTON and b.isVisible() for b in sidebar.space_buttons)
    sidebar.setMinimumWidth(0)
    sidebar.setMaximumWidth(16777215)


def test_a_swipe_over_the_space_buttons_shows_the_hidden_ones(window):
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QWheelEvent
    store, sidebar = window.store, window.sidebar
    while len(store.spaces) < 6:
        store.add_space(f"Space {len(store.spaces) + 1}", "#3366aa", "ph.code")
    store._changed()
    sidebar.setFixedWidth(187)
    pause(0.5)
    active = store.active_space

    def shown():
        return [i for i, b in enumerate(sidebar.space_buttons) if b.isVisible()]

    def swipe(dx):
        at = sidebar._switcher_area().center()
        for phase, delta in ((Qt.ScrollPhase.ScrollBegin, 0), (Qt.ScrollPhase.ScrollUpdate, dx),
                             (Qt.ScrollPhase.ScrollUpdate, dx), (Qt.ScrollPhase.ScrollEnd, 0)):
            event = QWheelEvent(QPointF(at), QPointF(sidebar.mapToGlobal(at)), QPoint(int(delta), 0),
                                QPoint(int(delta) * 4, 0), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                phase, False)
            assert sidebar._handle_swipe(event)
    first = shown()
    assert 0 < len(first) < 6 and first[0] == 0
    swipe(-30)  # (fingers to the left: the ones on the right)
    second = shown()
    assert second and second[0] == len(first) and store.active_space is active  # (no Space switched)
    swipe(-30)
    assert shown()[-1] == 5  # (the last page, and no further)
    swipe(30)
    swipe(30)
    swipe(30)
    assert shown() == first
    # An arrow on the side the hidden ones are on (only that side), and clicking it shows them
    back, ahead = sidebar.switcher_more
    assert ahead.isVisible() and not back.isVisible()
    assert ahead.toolTip() == f"{6 - len(first)} more Spaces"
    ahead.click()
    assert shown()[0] == len(first) and back.isVisible() and store.active_space is active
    assert back.toolTip() == f"{len(first)} more Space{'s' if len(first) != 1 else ''}"
    back.click()
    assert shown() == first and not back.isVisible()
    # (the arrows sit inside the bar, clear of the other buttons)
    assert ahead.geometry().right() < sidebar.new_space_btn.geometry().left()
    sidebar.setMinimumWidth(0)
    sidebar.setMaximumWidth(16777215)
    pause(0.3)
    while len(store.spaces) > 2:
        store.remove_space(store.spaces[-1])
    store._changed()
    pause(0.3)
    assert not any(m.isVisible() for m in sidebar.switcher_more)  # (all of them fit: no arrows)


def test_switching_spaces_slides_with_no_tab_open_too(window):
    # With no tab open the sidebar floats over the page (it stays out), and switching Spaces didn't slide at all
    # then: it just jumped. It slides over a floating sidebar as over a docked one.
    import time
    from browser.space_slide import SpaceSlide
    store = window.store
    second = store.spaces[1] if len(store.spaces) > 1 else store.add_space("Two", "#22aa66")
    if not window.sidebar.isHidden():
        window.toggle_sidebar()  # (the hidden sidebar: with no tab open, it floats out over the page and stays)
        pause(0.4)
    for tab in list(store.visible_tabs()):
        window.close_tab(tab)
    window._float_if_empty()
    pause(0.6)
    assert window.current_tab is None and window.floating_sidebar.active
    first = store.active_space
    window.switch_space(second)
    slides = [w for w in window.findChildren(SpaceSlide) if w.isVisible()]
    assert len(slides) == 1 and not slides[0].mask().isEmpty()
    side = window.sidebar.geometry()
    assert slides[0].mask().boundingRect().intersects(side)  # (over the sidebar's card, the page left live)
    travels = []
    end = time.monotonic() + 0.5
    from PyQt6 import sip
    while time.monotonic() < end and not sip.isdeleted(slides[0]):
        travels.append(slides[0].travel)
        QApplication.processEvents()
    width = window.sidebar.width()
    assert any(0 < t < width for t in travels)  # (frames part of the way across: a slide)
    assert store.active_space is second
    window.switch_space(first)
    pause(0.6)
    window.toggle_sidebar()
    pause(0.4)


def test_a_folder_opening_or_closing_never_moves_the_rows_below_the_wrong_way(window):
    # Seen in a screen recording: as a folder finished opening, everything below it dropped a block's height for a
    # frame and came back (its rows went back into the list before their sliding box came out)
    import time
    from PyQt6.QtCore import QEvent, QObject
    from browser.models import Folder, Tab

    class Frames(QObject):
        def __init__(self):
            super().__init__()
            self.ys = []

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Paint:
                now = time.perf_counter()
                if not self.ys or now - self.ys[-1][0] > 0.004:  # (one a frame, whichever part paints first)
                    self.ys.append((now, section._rect(row).top()))
            return False
    store = window.store
    space = store.active_space
    folder = Folder("Six", [Tab(f"https://f{n}.example/", f"Tab {n}") for n in range(6)], expanded=True)
    after = Tab("https://after.example/", "After", home_url="https://after.example/")
    space.pinned = [Tab("https://a.example/", "A", home_url="https://a.example/"), folder, after]
    store._changed()
    pause(0.6)
    section = window.sidebar.pinned
    row = next(r for r, n, *_ in section.entries if n is after)
    frames = Frames()
    for widget in (section, window.sidebar.scroll.viewport(), window.sidebar):
        widget.installEventFilter(frames)
    for closing in (True, False, True, False):
        frames.ys = []
        window.toggle_folder(folder)
        pause(0.4)
        ys = [y for _t, y in frames.ys]
        assert ys == (sorted(ys, reverse=True) if closing else sorted(ys)), ys  # (only ever the one way)
        assert len(set(ys)) > 2  # (and gradually: a step between start and end, not a jump; on a busy Mac
        # a 200 ms animation may only get one frame drawn between them)
    for widget in (section, window.sidebar.scroll.viewport(), window.sidebar):
        widget.removeEventFilter(frames)
    space.pinned = []
    store._changed()


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
    traps = "['trap-offscreen','trap-clear','trap-tiny','trap-invisible'].map(i => document.getElementById(i).value)"
    assert run_js(tab.view, traps, world=0) == ["", "", "", ""]  # (fields you can't see)
    # With no form, only the fields around the one you clicked, not ones anywhere on the page
    run_js(tab.view, press % "loose-name", world=0)
    assert wait_until(lambda: manager.card_for(tab.view) is not None, 5), "no card for the fields without a form"
    _click_when_ready(manager.card_for(tab.view), "Home · Arkan Example, 1 Main St")
    loose = "['loose-name','loose-email','loose-tel','far-street'].map(i => document.getElementById(i).value)"
    assert wait_until(lambda: run_js(tab.view, loose, world=0) == ["Arkan Example", "arkan@example.com", "555 0100", ""], 5)
    addresses.delete(addresses.all_addresses()[0]["id"])


def test_the_update_window_shows_whats_new_downloads_and_can_skip(window, monkeypatch):
    from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton
    from browser.update_window import SKIP_SETTING
    updater = window.feature("self_update").updater
    found = {"state": "available", "build": 202611011200, "version": "Eclipse 2026.11.1", "size": 168_000_000,
             "behind": 1, "subjects": ["Better tabs"], "notes": "- Better tabs\n- Passwords fill faster"}
    asked = []
    monkeypatch.setattr(updater, "update_and_restart", lambda: asked.append(1))
    updater._manual = False
    updater._offer(found)
    page = updater.window
    assert page is not None and page.isVisible()
    words = " ".join(l.text() for l in page.findChildren(QLabel))
    assert "Eclipse 2026.11.1 · 168 MB. You're running it from source." in words
    assert "Passwords fill faster" in page.notes.toPlainText()
    QApplication.processEvents()
    page.grab().save("/private/tmp/claude-501/-Users-masonkimball-projects-GitHub-Eclipse/626a4f58-39ed-47a1-a72d-0f6fa337de5b/scratchpad/update-window.png")
    next(b for b in page.findChildren(QPushButton) if b.text() == "Update && Restart").click()
    assert asked == [1] and not page.go.isEnabled()
    updater._set_status({**found, "state": "downloading", "percent": 40})
    assert page.findChildren(QProgressBar)[0].value() == 40 and "40%" in page.state.text()
    updater._set_status({"state": "failed", "reason": "The download didn't finish."})
    assert page.go.isEnabled() and page.go.text() == "Try Again" and "didn't finish" in page.state.text()
    page.skip.click()  # skipped: not offered again by itself...
    assert window.store.settings[SKIP_SETTING] == 202611011200
    QApplication.processEvents()
    updater._offer(found)
    from PyQt6 import sip
    assert updater.window is None or sip.isdeleted(updater.window)  # (the closed one, not a new one)
    updater._manual = True  # ...but Check Now still shows it
    updater._offer(found)
    assert updater.window.isVisible()
    updater.window.close()
    updater._set_status({"state": "idle"})


def test_videos_and_sound_can_wait_for_a_click(window, site):
    from browser.window import AUTOPLAY_SETTING
    window.store.settings[AUTOPLAY_SETTING] = True
    window.apply_autoplay()
    tab = open_page(window, f"{site}/music.html")  # (its audio starts itself)
    pause(0.8)
    assert run_js(tab.view, "a.paused", world=0) is True  # (waiting for a click)
    window.store.settings[AUTOPLAY_SETTING] = False
    window.apply_autoplay()
    tab.view.reload()
    assert wait_until(lambda: run_js(tab.view, "typeof a !== 'undefined' && !a.paused", world=0) is True, 5)
    window.close_tab(tab)


def test_a_download_pauses_and_resumes(window, tmp_path, monkeypatch):
    import http.server
    import threading
    import time as clock
    from PyQt6.QtCore import QUrl
    from browser import window as window_module
    monkeypatch.setattr(window_module, "ytdlp_settings", lambda store: {"folder": str(tmp_path)})  # (not ~/Downloads)
    monkeypatch.setattr(window, "_download_finished", lambda item, download: None)  # (no Claude renaming it)
    size = 64 * 1024

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="slow.bin"')
            self.send_header("Content-Length", str(size))
            self.end_headers()
            for _ in range(64):  # (a kilobyte at a time, slowly: time to pause it)
                try:
                    self.wfile.write(b"x" * 1024)
                    self.wfile.flush()
                except OSError:
                    return
                clock.sleep(0.05)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        window.open_url(f"http://127.0.0.1:{server.server_port}/slow.bin")
        downloads = window.downloads_window
        assert wait_until(lambda: any(getattr(i, "title", "") == "slow.bin" for i in downloads.items), 5)
        item = next(i for i in downloads.items if i.title == "slow.bin")
        assert item.can_pause and item.state == "running"
        from browser import macos
        assert downloads._badged == 1 and macos.dock_badge_text() == "↓1"  # (the Dock icon's count)
        item.toggle_pause()
        assert wait_until(lambda: item.paused and item.status.startswith("Paused"), 3)
        item.toggle_pause()
        assert wait_until(lambda: item.state == "done", 15)
        assert (tmp_path / "slow.bin").stat().st_size == size
        assert downloads._badged == 0 and macos.dock_badge_text() == ""
        # Click the finished one and press space: Quick Look (never really opened here: it'd come to the front)
        from PyQt6.QtTest import QTest
        from browser import downloads as module
        from browser.downloads import DownloadRow
        looked = []
        monkeypatch.setattr(module, "quick_look", lambda path, running=None: looked.append(path))
        row = next(r for r in downloads.findChildren(DownloadRow) if r.item is item)
        QTest.keyClick(row, Qt.Key.Key_Space)
        assert looked == [str(tmp_path / "slow.bin")]
        row.removed.emit(row)
        assert item not in downloads.items
    finally:
        server.shutdown()


def test_applescript_lists_opens_focuses_and_closes_tabs(window, site, monkeypatch):
    from browser import macos
    from browser.scripting import IN_SPACE, SUITE, TAB_RECORD
    if macos._objc is None:
        import pytest
        pytest.skip("macOS's Apple events")
    monkeypatch.setattr(window, "bring_to_front", lambda: None)  # (a test never takes the screen)
    ask = lambda command, **params: macos.ask_self(SUITE, command, params)
    names = [s.name for s in window.store.spaces]
    assert ask("LsSp") == names
    work = window.store.spaces[1]
    made = ask("OpTb", **{"----": site + "/article.html", IN_SPACE: work.name.upper()})  # (any case)
    assert made[TAB_RECORD["URL"]] == site + "/article.html" and made[TAB_RECORD["space"]] == work.name
    assert made[TAB_RECORD["section"]] == "today" and window.store.active_space is work
    tab = window.store.find_tab(made[TAB_RECORD["id"]])
    assert window.current_tab is tab and ask("CrTb")[TAB_RECORD["id"]] == tab.id
    in_work = ask("LsTb", **{IN_SPACE: work.name})
    assert tab.id in [t[TAB_RECORD["id"]] for t in in_work]
    assert len(ask("LsTb")) == len(list(window.store.all_tabs())) > len(in_work)
    # Back to the first Space, then focus brings the tab (and its Space) back
    ask("SwSp", **{"----": names[0]})
    assert wait_until(lambda: window.store.active_space is window.store.spaces[0], 3)
    ask("FcTb", **{"----": tab.id})
    assert wait_until(lambda: window.store.active_space is work and window.current_tab is tab, 3)
    ask("ClTb", **{"----": tab.id})
    assert window.store.find_tab(tab.id) is None
    # What it can't do, it says (and does nothing)
    for command, params, says in (("SwSp", {"----": "Nowhere"}, 'no Space called "Nowhere"'),
                                  ("FcTb", {"----": "gone"}, 'no tab with the id "gone"'),
                                  ("OpTb", {"----": "javascript:alert(1)"}, "web page's address"),
                                  ("OpTb", {"----": "file:///etc/passwd"}, "web page's address")):
        try:
            ask(command, **params)
            raise AssertionError(command)
        except macos.ScriptError as error:
            assert says in str(error), (command, str(error))


def test_eclipse_links_from_other_apps_open_pages_and_switch_spaces(window, site, monkeypatch):
    from PyQt6.QtNetwork import QLocalServer
    from browser.app import LinkReceiver
    from browser.webview import WebPage
    monkeypatch.setattr(window, "bring_to_front", lambda: None)
    receiver = LinkReceiver(window, QLocalServer())

    class FileOpen:  # (what macOS sends, as Shortcuts opens the link; Qt won't make one of its own here)
        def __init__(self, text):
            self.url = lambda: QUrl(text)
        type = staticmethod(lambda: QEvent.Type.FileOpen)
        file = staticmethod(lambda: "")
    link = lambda text: receiver.eventFilter(None, FileOpen(text))
    work, first = window.store.spaces[1], window.store.spaces[0]
    page = f"{site}/article.html?a=1&b=2"
    assert link("eclipse-browser://open?url=" + QUrl.toPercentEncoding(page).data().decode() + "&space=" + work.name)
    assert window.store.active_space is work and window.current_tab.url == page  # (its own & kept)
    opened = window.current_tab
    assert link(f"eclipse-browser://space?name={first.name}") and window.store.active_space is first
    # Only web pages: anything else does nothing but say so
    count = len(list(window.store.all_tabs()))
    for bad in ("eclipse-browser://open?url=javascript:alert(1)", "eclipse-browser://open?url=file:///etc/passwd",
                "eclipse-browser://delete-everything", "eclipse-browser://space?name=Nowhere"):
        window.toasts.clear()
        assert link(bad) and window.toasts, bad
    assert len(list(window.store.all_tabs())) == count and window.store.active_space is first
    # A page can never use them: they'd let it drive the browser it's in
    view_page = WebPage(window.profile, window)
    assert not view_page.acceptNavigationRequest(QUrl("eclipse-browser://open?url=https://evil.example/"),
                                                 WebPage.NavigationType.NavigationTypeLinkClicked, True)
    view_page.deleteLater()
    window.close_tab(opened)


def test_spaces_and_pinned_tabs_go_in_spotlight_and_open_from_it(window, site, monkeypatch):
    from browser import macos
    from browser.models import PINNED, TODAY, Tab
    feature = window.feature("spotlight")
    added, removed = [], []
    monkeypatch.setattr(macos, "is_built_app", lambda: True)  # (only the built app uses Spotlight: and never
    monkeypatch.setattr(macos, "spotlight_add", lambda items: added.append(items) or True)  # really, from a test)
    monkeypatch.setattr(macos, "spotlight_remove", lambda ids: removed.append(ids) or True)
    monkeypatch.setattr(window, "bring_to_front", lambda: None)
    work = window.store.spaces[1]
    pinned, today = Tab(site + "/article.html", "The Article"), Tab(site + "/index.html", "Today's")
    window.store.add_tab(pinned, PINNED, work, 0)
    window.store.add_tab(today, TODAY, work, 0)
    feature.refresh()
    ids = {item[0]: item for item in added[-1]}
    assert f"space:{work.id}" in ids and ids[f"space:{work.id}"][1] == work.name
    assert ids[f"tab:{pinned.id}"][1:3] == ("The Article", f"Pinned in {work.name} · 127.0.0.1")
    assert f"tab:{today.id}" not in ids  # (Today's come and go)
    assert any(i.startswith("tab:") and i[4:] in {t.id for t in window.store.favorites} for i in ids)
    assert window.store.settings["spotlight_ids"] == sorted(ids)
    feature.refresh()
    assert len(added) == 1  # (nothing changed: Spotlight isn't told again)
    # Picked in Spotlight: Eclipse comes up at it, in its Space
    window.switch_space(window.store.spaces[0])
    feature.open(f"tab:{pinned.id}")
    assert wait_until(lambda: window.store.active_space is work and window.current_tab is pinned, 3)
    feature.open(f"space:{window.store.spaces[0].id}")
    assert wait_until(lambda: window.store.active_space is window.store.spaces[0], 3)
    # Unpinned (closed for good): taken out of Spotlight
    window.store.remove_tab(pinned)
    feature.refresh()
    assert removed[-1] == [f"tab:{pinned.id}"]
    # Turned off: everything's taken out
    window.store.settings["spotlight"] = False
    feature.setting_changed()
    assert removed[-1] is None and window.store.settings["spotlight_ids"] == []
    window.close_tab(today)


def test_picking_an_eclipse_item_in_spotlight_reaches_eclipse(window, monkeypatch):
    import ctypes
    from browser import macos
    if not macos._cocoa():
        import pytest
        pytest.skip("macOS's app delegate")
    picked = []
    monkeypatch.setattr(window.feature("spotlight"), "open", picked.append)
    macos.on_spotlight_open(window.feature("spotlight").open)
    send, cls = macos._send, macos._objc.objc_getClass

    def activity(kind, info=None):
        made = send(send(cls(b"NSUserActivity"), "alloc"), "initWithActivityType:", macos._cfstr(kind),
                    argtypes=(ctypes.c_void_p,))
        if info:
            send(made, "setUserInfo:", send(cls(b"NSDictionary"), "dictionaryWithObject:forKey:", macos._cfstr(info[1]),
                                            macos._cfstr(info[0]), argtypes=(ctypes.c_void_p, ctypes.c_void_p)),
                 restype=None, argtypes=(ctypes.c_void_p,))
        return made
    app = send(cls(b"NSApplication"), "sharedApplication")
    delegate = send(app, "delegate")
    continue_ = lambda act: send(delegate, "application:continueUserActivity:restorationHandler:", app, act, None,
                                 restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,) * 3)
    # What macOS sends when you pick one of ours in Spotlight
    assert continue_(activity("com.apple.corespotlightitem", ("kCSSearchableItemActivityIdentifier", "tab:abc")))
    assert picked == ["tab:abc"]
    # Anything else is still Qt's (which says no to an activity it doesn't know)
    continue_(activity("com.example.something-else"))
    assert picked == ["tab:abc"]


def test_low_power_mode_pauses_background_tabs_and_motion(window, site, monkeypatch):
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from browser import macos, motion, power_saver
    feature = window.feature("power_saver")
    low = [True]
    monkeypatch.setattr(macos, "low_power", lambda: low[0])
    State = QWebEnginePage.LifecycleState
    background = open_page(window, site + "/article.html")
    front = open_page(window, site + "/index.html")
    background.last_active = time.time() - power_saver.FREEZE_AFTER - 5
    window.store.settings["power_saver"] = True
    feature.check()
    assert feature.saving and motion.reduced() and window.saving_minutes == power_saver.SLEEP_MINUTES
    assert window.toasts[-1].startswith("Low Power Mode")
    assert background.view.page().lifecycleState() == State.Frozen
    assert front.view.page().lifecycleState() == State.Active  # (the tab on screen carries on)
    # Switched to, a frozen tab carries on
    window.activate_tab(background)
    assert wait_until(lambda: background.view.page().lifecycleState() == State.Active, 3)
    assert run_js(background.view, "1 + 1") == 2
    # Idle tabs sleep sooner: 10 minutes, where you'd said an hour
    window.store.settings["sleep_after_minutes"] = 60
    front.last_active = time.time() - 11 * 60
    window.sleep_idle_tabs()
    assert window.is_sleeping(front)
    window.store.settings["sleep_after_minutes"] = 0  # ("Never" stays never)
    window.activate_tab(front)
    assert wait_until(lambda: not window.is_sleeping(front), 3)
    background.last_active = time.time() - 3600
    window.activate_tab(front)
    assert window.sleep_idle_tabs() == 0
    # Low Power Mode off: as it was
    feature.freeze_idle()
    assert background.view.page().lifecycleState() == State.Frozen
    low[0] = False
    feature.check()
    assert not feature.saving and not motion.saving and window.saving_minutes is None
    assert background.view.page().lifecycleState() == State.Active
    # Turned off in Settings: Low Power Mode changes nothing
    low[0] = True
    window.store.settings["power_saver"] = False
    feature.setting_changed()
    assert not feature.saving and not motion.saving
    window.close_tab(background)
    window.close_tab(front)


def test_the_privacy_report_shows_what_each_site_tried(window, site, monkeypatch):
    from PyQt6.QtWidgets import QMenu
    feature = window.feature("privacy_report")
    feature.log.clear()
    tab = open_page(window, site + "/article.html")
    window.adblock.tracker_blocked.emit("127.0.0.1", "doubleclick.net")  # (as the blocker says, from its thread)
    window.adblock.tracker_blocked.emit("127.0.0.1", "facebook.net")
    window.adblock.link_cleaned.emit("127.0.0.1")
    assert wait_until(lambda: feature.log.report("127.0.0.1")["cleaned"] == 1, 2)
    # In the shield's menu: the report for the site on screen
    menu = QMenu()
    window.emit("site_menu", menu, tab)
    item = next(a for a in menu.actions() if a.text().startswith("Privacy Report for"))
    item.trigger()
    text = feature.shown.text.toPlainText()
    assert "2 trackers blocked" in text and "Google" in text and "Meta" in text and "1 link" in text
    feature.shown.close()
    # Clearing history clears it
    window.emit("history_cleared")
    assert feature.log.report()["trackers"] == {}
    window.close_tab(tab)


def test_a_space_comes_forward_on_its_schedule_and_blocks_its_distractions(window, site, monkeypatch):
    import datetime
    feature = window.feature("space_rules")
    first, work = window.store.spaces[0], window.store.spaces[1]
    window.switch_space(first)
    monday_9 = datetime.datetime(2026, 10, 26, 9, 0)
    feature.rules()[work.id] = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}  # (not set_rule: that
    feature.wanted = feature.wanted_space(monday_9.replace(hour=8))  # acts on the real time) 8 am: nothing yet
    # 9 am, you're using Eclipse: it asks
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    monkeypatch.setattr("browser.space_rules.QApplication.applicationState",
                        staticmethod(lambda: __import__("PyQt6.QtCore").QtCore.Qt.ApplicationState.ApplicationActive))
    window.toasts.clear()
    feature.check(monday_9)
    assert window.toasts == [f"Time for {work.name}"] and window.store.active_space is first
    window.toast_action()
    assert wait_until(lambda: window.store.active_space is work, 3)
    # Switched back by hand: it stays, until the next change
    window.switch_space(first)
    feature.check(monday_9.replace(minute=30))
    assert window.store.active_space is first
    # 9 am the next day, Eclipse in the background: it just switches
    feature.check(monday_9.replace(hour=18))
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)
    feature.check(monday_9 + datetime.timedelta(days=1))
    assert wait_until(lambda: window.store.active_space is work, 3)
    # A site blocked in Work: a quiet page, and Open Anyway lets it through
    feature.set_rule(work, {"blocked": ["127.0.0.1"]})
    window.toasts.clear()
    tab = open_page(window, site + "/article.html", wait=False)
    assert wait_until(lambda: window.toasts == [f"127.0.0.1 is blocked in {work.name}"], 5)
    assert wait_until(lambda: "isn't in" in (run_js(tab.view, "document.body && document.body.innerText") or ""), 5)
    window.toast_action()
    assert wait_until(lambda: tab.view.url().toString() == site + "/article.html", 5)
    # The same site in another Space opens as ever
    window.switch_space(first)
    other = open_page(window, site + "/index.html")
    assert other.view.url().toString() == site + "/index.html"
    feature.set_rule(work, {})
    assert work.id not in window.store.settings.get("space_rules", {})
    window.close_tab(tab)
    window.close_tab(other)


def test_a_tab_recipe_saves_and_replays_a_routine(window, site, monkeypatch):
    from browser import macos
    from browser.models import walk_tabs
    from browser.scripting import SUITE
    feature = window.feature("recipes")
    monkeypatch.setattr(window, "bring_to_front", lambda: None)
    space = window.store.active_space
    for tab in list(walk_tabs(space.today)):
        window.close_tab(tab)
    a = open_page(window, site + "/article.html")
    b = open_page(window, site + "/index.html")
    recipe = feature.save_current("Morning")
    assert set(recipe["urls"]) == {a.url, b.url} and not recipe["split"] and recipe["space"] == space.id
    assert "Run Recipe: Morning" in [c[0] for c in window.commands()]
    # Run with one of its pages still open: that one's reused, the other comes back
    window.close_tab(a)
    tabs = feature.run(recipe)
    assert b in tabs and len(tabs) == 2 and window.current_tab is tabs[0]
    assert [t.url for t in walk_tabs(space.today)].count(b.url) == 1
    # Side by side
    recipe["split"] = True
    feature.run(recipe)
    assert window.store.split_of(window.current_tab) and len(window.store.split_of(window.current_tab)) == 2
    # From a link (Shortcuts) and from AppleScript
    ran = []
    monkeypatch.setattr(feature, "run", lambda r: ran.append(r["name"]))
    from browser.app import LinkReceiver
    from PyQt6.QtNetwork import QLocalServer

    class FileOpen:
        def __init__(self, text):
            self.url = lambda: QUrl(text)
        type = staticmethod(lambda: QEvent.Type.FileOpen)
        file = staticmethod(lambda: "")
    LinkReceiver(window, QLocalServer()).eventFilter(None, FileOpen("eclipse-browser://recipe?name=morning"))
    if macos._objc is not None:
        macos.ask_self(SUITE, "RnRc", {"----": "Morning"})
        try:
            macos.ask_self(SUITE, "RnRc", {"----": "Evening"})
            raise AssertionError("no such recipe")
        except macos.ScriptError as error:
            assert "no recipe called" in str(error)
    assert ran[:2] == ["Morning", "Morning"] if macos._objc is not None else ran == ["Morning"]
    window.store.settings["recipes"] = []
    window.separate_split() if window.store.split_of(window.current_tab) else None
    for tab in list(walk_tabs(space.today)):
        window.close_tab(tab)


def test_the_start_view_can_be_just_the_background_from_a_right_click(window, monkeypatch):
    from PyQt6.QtGui import QContextMenuEvent
    from browser import start_view
    view = window.content.empty
    window.store.settings[start_view.SETTING] = True
    view.refresh()
    assert view.body.count() > 0
    chosen = []
    monkeypatch.setattr("browser.window.PlainMenu.exec", lambda menu, *a: chosen.append(menu.actions()[0]))
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(20, 20), QPoint(20, 20)))
    assert chosen[-1].text() == "Show Just the Background"
    chosen[-1].trigger()
    assert window.store.settings[start_view.SETTING] is False and view.body.count() == 0
    assert "bring it back" in window.toasts[-1]
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(20, 20), QPoint(20, 20)))
    assert chosen[-1].text().startswith("Show Search")
    chosen[-1].trigger()
    assert window.store.settings[start_view.SETTING] is True and view.body.count() > 0


def test_settings_has_a_live_folders_guide_with_every_kind(window, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QPushButton
    from browser.live_folder_dialog import KINDS
    from browser.settings import SettingsWindow
    settings = SettingsWindow(window)  # (never shown)
    page = settings.pages.widget(settings.page_index["live"])
    text = " ".join(label.text() for label in page.findChildren(QLabel))
    for _kind, title, _says in KINDS:
        assert title in text, title
    assert "{" not in text.replace("{id}", "")  # (every placeholder filled in)
    started = []
    monkeypatch.setattr(window.feature("live_folders"), "new_live_folder", started.append)
    buttons = [b for b in page.findChildren(QPushButton) if b.text() == "New…"]
    assert len(buttons) == len(KINDS)
    buttons[1].click()
    assert started == [{"kind": KINDS[1][0]}]
    settings.deleteLater()
