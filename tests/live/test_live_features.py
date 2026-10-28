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
