# The browser's main features, end to end, in the real window. Run with:  ./run.sh --live-tests

import functools
import http.server
import os
import shutil
import signal
import threading
import time

import pytest
from PyQt6 import sip

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QApplication

from live_helpers import loaded, open_page, pause, run_js, wait_until


def _rows(window):
    bar = window.command_bar
    return [bar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(bar.list.count())]


def _swipe(view, dx, at=QPoint(600, 450), end=True, during=None):
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
    _lift(view, at)


def _lift(view, at=QPoint(600, 450)):
    target = view.focusProxy()
    QApplication.sendEvent(target, QWheelEvent(QPointF(at), QPointF(target.mapToGlobal(at)), QPoint(0, 0),
                                               QPoint(0, 0), Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollEnd, False))


def test_swipe_goes_back_and_forward_but_not_over_a_scroller(window, site):
    tab = open_page(window, f"{site}/index.html")
    tab.view.load(QUrl(f"{site}/carousel.html"))
    assert loaded(tab.view)
    run_js(tab.view, "document.getElementById('c').scrollLeft = 500")
    _swipe(tab.view, 400, QPoint(200, 150))  # over the carousel, which can still scroll left
    pause(1)
    assert tab.view.url().fileName() == "carousel.html"
    _swipe(tab.view, 400)  # on the page itself
    assert wait_until(lambda: tab.view.url().fileName() == "index.html", 5)
    _swipe(tab.view, -400)
    assert wait_until(lambda: tab.view.url().fileName() == "carousel.html", 5)


def test_cookie_banners_are_rejected_and_nothing_else_is_clicked(window, site):
    onetrust = open_page(window, f"{site}/consent.html")
    generic = open_page(window, f"{site}/consent2.html")
    aem = open_page(window, f"{site}/aem.html")
    assert wait_until(lambda: onetrust.view.title() == "REJECTED-ONETRUST", 8)
    assert wait_until(lambda: generic.view.title() == "REJECTED-GENERIC", 8)  # a banner that came late
    pause(1)
    assert aem.view.title() == "Store"  # a "No thanks" that isn't a cookie banner
    assert generic.view.url().path() == "/consent2.html"  # its "Reject" link wasn't followed


def test_private_window_keeps_nothing(window, site):
    main = open_page(window, f"{site}/index.html")
    run_js(main.view, "document.cookie = 'who=main; max-age=999'", 0)
    history = len(window.store.history)
    private = window.private.new_window(f"{site}/links.html")
    view = private.current
    assert loaded(view)
    assert view.page().profile().isOffTheRecord()
    assert run_js(view, "document.cookie", 0) == ""
    run_js(view, "window.open('img.html', '_blank')", 0)
    assert wait_until(lambda: len(private.views) == 2, 5)  # popups stay in the private window
    assert len(window.store.history) == history
    private.close()
    assert wait_until(lambda: window.private.profile is None, 5)


def test_command_bar_completes_answers_and_finds_what_you_read(window, site):
    window.store.record_history("https://github.com/", "GitHub")
    article = open_page(window, f"{site}/article.html")
    window.open_command_bar("new")
    bar = window.command_bar
    bar.input.setFocus()
    bar.input.setText("git")  # what typing does, minus the keyboard
    bar._prev_typed = ""
    bar.update_results()
    assert bar.input.text().startswith("git") and _rows(window)[0].title.startswith("github.com")
    bar.input.setText("23*4+7")
    assert _rows(window)[0].title == "99"
    bar.close_bar()
    assert wait_until(lambda: window.page_index.count() >= 1, 10), "the article wasn't indexed"
    hits = window.page_index.search("tostadores independientes")
    assert hits and hits[0]["url"] == article.url


def test_reader_shows_the_article_without_scripts(window, site):
    open_page(window, f"{site}/article.html")
    window.toggle_reader()
    reader = window.reader
    assert wait_until(lambda: reader.isVisible(), 8)
    assert loaded(reader.view)
    text = run_js(reader.view, "document.body.innerText")
    assert "tostadores independientes" in text
    assert "Deportes" not in text and "boletín" not in text  # the nav and the newsletter box are gone
    assert run_js(reader.view, "1 + 1", 0) is None  # JavaScript is off in the reader
    reader.close_reader()


def test_dev_console_runs_code_and_reloads_on_save(window, tmp_path):
    page = tmp_path / "site" / "app.html"
    page.parent.mkdir()
    page.write_text("<title>App</title><h1 id=h>Version 1</h1><script>window.n = 41; nope()</script>")
    tab = open_page(window, QUrl.fromLocalFile(str(page)).toString())
    assert wait_until(lambda: window.devmode.errors(tab.view) == 1, 5)
    window.toggle_dev_console()
    drawer = window.devmode.drawer
    drawer.input.setText("n + 1")
    drawer.input.returnPressed.emit()
    assert wait_until(lambda: "42" in drawer.log.toPlainText(), 5)
    page.write_text(page.read_text().replace("Version 1", "Version 2"))
    assert wait_until(lambda: run_js(tab.view, "document.getElementById('h') && "
                                               "document.getElementById('h').textContent", 1, 1) == "Version 2", 8)


def test_a_crashed_page_offers_reload(window, site):
    tab = open_page(window, f"{site}/carousel.html")
    os.kill(tab.view.page().renderProcessPid(), signal.SIGKILL)
    assert wait_until(lambda: bool(window.crashes._cards), 8)
    next(iter(window.crashes._cards.values())).findChild(type(window.sidebar.clear_btn)).click()
    assert wait_until(lambda: not window.crashes._cards, 8)
    assert loaded(tab.view)


def test_pinned_tab_away_from_home(window, site):
    from browser.sidebar import TabRow
    tab = open_page(window, f"{site}/index.html")
    window.toggle_pin(tab)
    tab.view.load(QUrl(f"{site}/links.html"))
    assert loaded(tab.view)
    row = wait_until(lambda: next((r for r in window.sidebar.findChildren(TabRow) if r.tab is tab), None), 3)
    assert wait_until(lambda: row.away_label.isVisible(), 3)
    window.go_home(tab)
    assert wait_until(lambda: tab.view.url().fileName() == "index.html", 5)
    assert wait_until(lambda: not row.away_label.isVisible(), 3)


def test_now_playing_card_controls_a_background_tab(window, site):
    window.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    radio = open_page(window, f"{site}/music.html")
    assert wait_until(lambda: window.tab_audio(radio)[0], 8), "the page never played"
    open_page(window, f"{site}/index.html")
    card = window.sidebar.now_playing
    assert wait_until(lambda: card.isVisible(), 5)
    assert card.title.text() == "Blue in Green" and card.subtitle.text() == "Miles Davis"
    card.play_btn.click()
    assert wait_until(lambda: run_js(radio.view, "document.documentElement.getAttribute('data-pb-media')") == "paused", 5)


def test_menus_show_ampersands(window, site):
    menu = window._menu()
    menu.addAction("Ads & Trackers")
    assert menu.actions()[0].text() == "Ads && Trackers"  # Qt draws "&&" as one "&"


def test_links_open_without_tracking_tags(window, site):
    tab = open_page(window, f"{site}/index.html?utm_source=newsletter&id=7&fbclid=abc")
    assert tab.view.url().query() == "id=7"
    window.copy_url(tab)
    assert QApplication.clipboard().text() == f"{site}/index.html?id=7"


def test_a_bad_certificate_warns_and_go_back_returns(window, site, bad_https):
    tab = open_page(window, f"{site}/index.html")
    tab.view.load(QUrl(f"{bad_https}/links.html"))
    card = wait_until(lambda: window.certificates.warning(tab.view), 8)
    assert card and card.isVisible() and card.geometry() == tab.view.rect()
    assert card.anyway_btn is not None and not card.anyway_btn.isVisible()  # behind Details
    card.back_btn.click()
    assert wait_until(lambda: tab.view.url().toString() == f"{site}/index.html", 5)
    assert window.certificates.warning(tab.view) is None


def test_a_bad_certificate_can_be_visited_anyway(window, bad_https):
    tab = open_page(window, f"{bad_https}/index.html", wait=False)
    card = wait_until(lambda: window.certificates.warning(tab.view), 8)
    card.details_btn.click()
    assert card.anyway_btn.isVisible()
    card.anyway_btn.click()
    assert loaded(tab.view) and tab.view.url().toString() == f"{bad_https}/index.html"
    assert window.certificates.warning(tab.view) is None


def _real_dictionary():
    """Chrome's en-US dictionary, if this Mac has one (Settings downloads it the first time spell check is on)."""
    for path in (os.environ.get("PB_TEST_DICTIONARY", ""),
                 os.path.expanduser("~/Library/Application Support/MasonKimball05/PythonBrowser/dictionaries/en-US.bdic")):
        if path and os.path.isfile(path):
            return path
    pytest.skip("no en-US dictionary here yet: turn spell check on once, or set PB_TEST_DICTIONARY")


def test_spell_check_turns_on_from_settings_and_suggests(window, site, tmp_path, monkeypatch):
    from browser import spelling
    served = tmp_path / "dictionaries"
    served.mkdir()
    shutil.copy(_real_dictionary(), served / "en-us-10-1.bdic")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(served)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(spelling, "SOURCE", f"http://127.0.0.1:{server.server_port}/")
    try:
        window.store.settings["spell_check_language"] = "en_US"
        window.open_settings("general")
        window._settings.spell_box.setChecked(True)  # downloads it, then turns checking on
        assert wait_until(lambda: window.profile.isSpellCheckEnabled(), 10), window._settings.spell_status.text()
        window._settings.close()
    finally:
        server.shutdown()
    tab = open_page(window, f"{site}/typing.html")
    run_js(tab.view, "t.focus(); document.execCommand('insertText', false, 'teh '); t.setSelectionRange(1, 1)", 0)
    menus = []
    hook = tab.view.context_menu_hook

    def capture(view, menu, request):
        hook(view, menu, request)
        menus.append((request.misspelledWord(), [a.text() for a in menu.actions()]))
        if request.misspelledWord() and menu.actions():
            menu.actions()[0].trigger()  # choose the first correction
        QTimer.singleShot(0, menu.close)  # (it pops up once this returns)
    tab.view.context_menu_hook = capture
    target, at = tab.view.focusProxy(), QPointF(40, 40)

    def right_click():  # (the checking happens a moment after typing, so try until it has)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            QApplication.sendEvent(target, QMouseEvent(kind, at, target.mapToGlobal(at), Qt.MouseButton.RightButton,
                                                       Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier))
        pause(0.4)
        return menus and menus[-1][0]
    assert wait_until(right_click, 8), menus
    word, items = menus[-1]
    assert word == "teh" and items[0] == "the", items  # corrections first, before Ask/Translate
    assert wait_until(lambda: run_js(tab.view, "t.value", 0) == "the ", 3)


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


def test_space_swipe_follows_the_fingers_and_springs_back(window, site):
    open_page(window, f"{site}/index.html")
    first, second = _two_spaces(window)
    width = window.sidebar.width()
    _sidebar_swipe(window, 10, -width * 0.04, gap=0.03, end=False)  # 40%, slowly, fingers still down
    slide = window.space_swipe.slide
    assert slide is not None and slide.after is not None
    assert abs(slide.travel - width * 0.4) < width * 0.05  # it's where the fingers are, not snapped
    pause(0.15)  # resting before letting go: no flick
    _sidebar_swipe(window, 0, 0, begin=False)
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    pause(0.1)
    assert window.store.active_space is first  # not far enough: back where it was


def test_space_swipe_past_halfway_or_a_flick_switches(window, site, monkeypatch):
    open_page(window, f"{site}/index.html")
    first, second = _two_spaces(window)
    width = window.sidebar.width()
    clock = _steady_clock(monkeypatch)
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.03, clock=clock)  # 70%, then let go
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    assert window.store.active_space is second
    _sidebar_swipe(window, 4, width * 0.05, gap=0.008, clock=clock)  # a short quick flick back
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    assert window.store.active_space is first


def test_space_swipe_with_one_space_only_gives_a_little(window, site):
    open_page(window, f"{site}/index.html")
    only = window.store.active_space
    window.store.spaces[:] = [only]
    window.sidebar.rebuild()
    pause(0.2)
    _sidebar_swipe(window, 20, -30, end=False)
    assert window.space_swipe.slide.travel < 80  # 600 points of swiping, under 80 of movement
    _sidebar_swipe(window, 0, 0, begin=False)
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    assert window.store.active_space is only


def test_the_page_follows_a_back_swipe_over_a_picture_of_the_last_one(window, site):
    tab = open_page(window, f"{site}/index.html")
    pause(0.3)
    tab.view.load(QUrl(f"{site}/links.html"))
    assert loaded(tab.view)
    pause(0.3)
    seen = {}

    def look():
        gesture = window.swipes._gestures.get(id(tab.view))
        seen["slide"] = gesture and gesture.slide
        seen["travel"] = gesture and gesture.slide and gesture.slide.travel
    _swipe(tab.view, 240, end=False, during=look)  # 9 of 12 steps in: 180 points
    slide = seen["slide"]
    assert slide is not None and abs(seen["travel"] - (180 - 14)) < 25  # right under the fingers
    shot = window.swipes.snapshots.get(tab.view, 0, QUrl(f"{site}/index.html"))
    assert shot is not None and slide.other is shot  # the real page underneath, not a placeholder
    _lift(tab.view)  # resting at 240 of ~830: not far enough
    assert wait_until(lambda: not slide.isVisible(), 3)
    pause(0.3)
    assert tab.view.url().fileName() == "links.html"
    _swipe(tab.view, 420)  # far enough this time
    assert wait_until(lambda: tab.view.url().fileName() == "index.html", 5)
    assert wait_until(lambda: not window.swipes._landing, 3)  # the picture lifted once it arrived


def test_closing_a_tab_and_clearing_today_slide_rows_away(window, site):
    from browser.sidebar import RowReveal
    first = open_page(window, f"{site}/index.html")
    second = open_page(window, f"{site}/links.html")
    third = open_page(window, f"{site}/img.html")
    pause(0.4)
    today = window.sidebar.today
    window.close_tab(second)
    assert wait_until(lambda: today.findChildren(RowReveal), 1)  # it shrinks away, not vanishes
    pause(0.08)
    assert wait_until(lambda: not today._reveals, 2)
    pause(0.1)
    assert [row.tab for row in today.rows] == [third, first]
    assert all(row.isVisible() and row.parentWidget() is today for row in today.rows)
    window.open_url(f"{site}/article.html")
    assert wait_until(lambda: any(r.growing for r in today._reveals), 1)  # the new one grows in
    assert wait_until(lambda: not today._reveals, 2)
    pause(0.1)
    assert len(today.rows) == 3 and all(row.parentWidget() is today for row in today.rows)
    window.clear_today()
    assert wait_until(lambda: len(today._reveals) == 3, 1)
    assert wait_until(lambda: not today._reveals, 3)
    assert today.rows == [] and not today.findChildren(RowReveal)


def test_picking_an_app_icon_changes_the_dock_icon(window):
    from browser import brand
    window.open_settings("general")
    settings = window._settings
    before = QApplication.windowIcon().pixmap(64).toImage()
    violet = next(b for b in settings.icon_group.buttons() if b.toolTip() == "Violet")
    violet.click()
    assert window.store.settings[brand.SETTING] == "violet" and violet.isChecked()
    after = QApplication.windowIcon().pixmap(64).toImage()
    assert after != before
    settings.close()


def _drag_event(kind, section, y, tab, together=()):
    from PyQt6.QtCore import QMimeData
    from PyQt6.QtGui import QDragMoveEvent, QDropEvent
    from browser.sidebar import TAB_MIME, TABS_MIME
    mime = QMimeData()
    mime.setData(TAB_MIME, tab.id.encode())
    if together:
        mime.setData(TABS_MIME, ",".join(t.id for t in together).encode())
    cls = QDragMoveEvent if kind == "move" else QDropEvent
    event = cls(QPointF(60, y) if cls is QDropEvent else QPoint(60, int(y)), Qt.DropAction.MoveAction, mime,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    (section.dragMoveEvent if kind == "move" else section.dropEvent)(event)
    return mime  # (kept alive while the event is used)


def test_dragging_a_tab_parts_the_rows_where_it_will_land(window, site):
    from browser.sidebar import DropGap
    a = open_page(window, f"{site}/index.html")
    b = open_page(window, f"{site}/links.html")
    c = open_page(window, f"{site}/img.html")
    pause(0.4)
    today = window.sidebar.today
    assert [r.tab for r in today.rows] == [c, b, a]  # (newest on top)
    row_b = next(r for r in today.rows if r.tab is b)
    today.lift(row_b)
    assert wait_until(lambda: row_b.parentWidget().height() == 0, 1)  # picked up: its slot closes
    rows = {r.tab: r for r in today.rows}
    top_c, top_a = rows[c].geometry(), rows[a].geometry()
    _drag_event("move", today, top_a.center().y() + 8, b)  # the lower half of A: below A
    gap = today._gap
    assert gap is not None and wait_until(lambda: gap.height() == 34, 1)
    assert gap.geometry().top() > rows[a].geometry().top()  # after A, at the end
    _drag_event("move", today, top_a.center().y() + 9, b)  # a nudge: the same place, no new gap
    assert today._gap is gap
    _drag_event("move", today, top_c.center().y() - 6, b)  # the top half of C: before C
    assert today._gap is not gap and wait_until(lambda: gap.height() == 0, 1)  # the old one closes
    _drag_event("drop", today, top_c.center().y() - 6, b)
    assert wait_until(lambda: [r.tab for r in today.rows] == [b, c, a], 2)
    assert wait_until(lambda: not today.findChildren(DropGap), 2)
    # Picked up and let go of nowhere: it comes back
    row = next(r for r in today.rows if r.tab is a)
    today.lift(row)
    assert wait_until(lambda: row.parentWidget().height() == 0, 1)
    today.drag_ended(row)
    assert wait_until(lambda: row.parentWidget() is today and row.isVisible(), 1)
    assert [r.tab for r in today.rows] == [b, c, a]


def test_swiping_straight_back_shows_only_the_two_spaces(window, site):
    from browser.gestures import SpaceSlide
    first, second = _two_spaces(window)
    open_page(window, f"{site}/index.html")
    width = window.sidebar.width()
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.01)  # over to the second, let go
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    _sidebar_swipe(window, 6, width * 0.05, gap=0.01, end=False)  # straight back, while it's still fading
    covers = [c for c in window.findChildren(SpaceSlide) if c.isVisible()]
    assert covers == [window.space_swipe.slide]  # the last one's gone, not baked into this one's pictures
    _sidebar_swipe(window, 8, width * 0.05, gap=0.01, begin=False)
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    pause(0.3)
    assert window.store.active_space is first
    assert not [c for c in window.findChildren(SpaceSlide) if c.isVisible()]


def test_the_hidden_sidebar_slides_out_stays_and_turns_round(window, site, monkeypatch):
    from browser import floating_sidebar
    open_page(window, f"{site}/index.html")
    window.toggle_sidebar()  # hide it
    pause(0.4)
    assert not window.sidebar.isVisible()
    root = window.centralWidget()
    pointer = {"at": QPoint(3, 300)}
    monkeypatch.setattr(floating_sidebar.QCursor, "pos", staticmethod(lambda: root.mapToGlobal(pointer["at"])))
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window._reveal_sidebar()  # the pointer at the very edge
    fs = window.floating_sidebar
    assert wait_until(lambda: window.sidebar.x() == floating_sidebar.MARGIN, 1)
    assert fs.shadow is not None and fs.shadow.isVisible()
    pause(0.5)  # resting at the edge, left of the sidebar itself: it stays (it used to go and come back)
    assert fs.state == "in" and window.sidebar.x() == floating_sidebar.MARGIN
    pointer["at"] = QPoint(window.sidebar.geometry().right() + 200, 300)  # off over the page
    assert wait_until(lambda: fs.state == "out", 1.5)
    pause(0.04)  # (a few frames into sliding away, which takes ~180 ms)
    halfway = window.sidebar.x()
    pointer["at"] = QPoint(4, 300)  # back before it's gone: it turns round from where it is
    fs._check()  # (what its pointer watch does next; called now so the test doesn't race the slide)
    assert fs.state == "in"
    assert wait_until(lambda: window.sidebar.x() == floating_sidebar.MARGIN, 1)
    assert halfway < floating_sidebar.MARGIN
    pointer["at"] = QPoint(900, 300)
    assert wait_until(lambda: fs.state is None, 2) and not window.sidebar.isVisible()
    assert fs.shadow is None
    window.toggle_sidebar()  # and showing it for good puts it back in its place
    pause(0.4)
    assert window.sidebar.isVisible() and window.sidebar.parentWidget() is window.splitter


def test_the_sidebar_resize_handle_is_on_the_page_edge(window, site):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from browser.sidebar_edge import ONTO_PAGE
    open_page(window, f"{site}/index.html")
    pause(0.2)
    handle = window.splitter.handle(1)
    edge = handle.mapTo(window, handle.rect().topRight()).x()
    page = window.content.mapTo(window, window.content.rect().topLeft()).x()
    assert page == edge + 1  # the handle runs right up to the page: no dead strip in between
    assert window.content.mapTo(window, window.content.rect().topLeft()).y() <= 10  # (not the hidden-sidebar top strip)
    # And the place you'd grab, the page's own edge, takes hold: from the end of the sidebar's contents
    # to a few points onto the page
    grip = window.sidebar_edge
    assert grip.isVisible()
    left = grip.mapTo(window, QPoint(0, 0)).x()
    pill_end = window.sidebar.url_pill.mapTo(window, QPoint(window.sidebar.url_pill.width(), 0)).x()
    assert left == pill_end and grip.mapTo(window, QPoint(grip.width(), 0)).x() == page + ONTO_PAGE
    width = window.sidebar.width()
    at = QPoint(grip.width() - 2, grip.height() // 2)  # (on the page's edge itself)
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    QTest.mouseMove(grip, at + QPoint(30, 0))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at + QPoint(30, 0))
    pause(0.1)
    assert abs(window.sidebar.width() - (width + 30)) <= 1 and window.store.settings["sidebar_width"] == window.sidebar.width()
    assert grip.mapTo(window, QPoint(0, 0)).x() == left + 30  # (it went with the edge)
    QTest.mouseDClick(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    pause(0.1)
    assert window.sidebar.width() == 250


def test_peek_eases_in_and_out(window, site):
    peek = window.peek
    peek.open(window.profile, url=f"{site}/article.html")
    assert peek.isVisible() and peek.dim < 0.5  # (on its way, not there at once)
    assert wait_until(lambda: peek.dim == 1.0 and peek.rise == 0.0, 1)
    final = peek.card.pos()
    peek.close_peek()
    assert peek.isVisible() and peek.closing  # it sinks away first
    assert wait_until(lambda: not peek.isVisible(), 1) and peek.view is None
    peek.open(window.profile, url=f"{site}/article.html")
    peek.close_peek()
    peek.open(window.profile, url=f"{site}/index.html")  # opened again while closing: just opens
    assert wait_until(lambda: peek.dim == 1.0, 1) and peek.isVisible() and peek.card.pos() == final
    peek.close_peek(animate=False)
    assert not peek.isVisible()


def test_split_view_crossfades_and_tidies_up(window, site):
    from PyQt6.QtWidgets import QWidget
    a = open_page(window, f"{site}/index.html")
    b = open_page(window, f"{site}/links.html")
    covers = lambda: [w for w in window.centralWidget().children()
                      if isinstance(w, QWidget) and type(w).__name__ == "Cover" and w.isVisible()]
    window.add_to_split(a, b)
    assert covers() and window.content.splitting  # the old look fades over the new panes
    assert wait_until(lambda: not covers(), 1)
    window.separate_split()
    assert wait_until(lambda: not covers(), 1) and not window.content.splitting


def test_a_favorite_squeezes_when_pressed_and_springs_back(window, site):
    from PyQt6.QtGui import QMouseEvent
    tab = open_page(window, f"{site}/index.html")
    window.move_tab(tab, "favorites")
    pause(0.3)
    tile = next(t for t in window.sidebar.favorites.tiles if t.tab is tab)
    at = QPointF(tile.rect().center())
    press = QMouseEvent(QEvent.Type.MouseButtonPress, at, tile.mapToGlobal(at), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(tile, press)
    assert wait_until(lambda: tile.press < 0.9, 0.5)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, at, tile.mapToGlobal(at), Qt.MouseButton.LeftButton,
                          Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(tile, release)
    overshoot = wait_until(lambda: tile.press > 1.0, 0.5)  # past full size for a moment: a spring
    assert overshoot and wait_until(lambda: tile.press == 1.0, 1)
    image = tile.grab().toImage()
    middle = {image.pixelColor(x, image.height() // 2).name() for x in range(image.width() // 2 - 12, image.width() // 2 + 12)}
    assert len(middle) > 1  # the icon is still drawn in the middle of the tile (not just its background)


def test_the_command_bar_highlight_glides_between_rows(window, site):
    from PyQt6.QtGui import QKeyEvent
    for url in ("https://github.com/", "https://gitlab.com/", "https://gitea.io/"):
        window.store.record_history(url, url)
    open_page(window, f"{site}/index.html")
    window.open_command_bar("new")
    bar = window.command_bar
    bar.input.setText("git")
    bar.update_results()
    assert bar.list.count() >= 3
    first = bar.list.visualItemRect(bar.list.item(0)).top()
    QApplication.sendEvent(bar.input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier))
    assert bar.list.currentRow() == 1  # the choice moves at once…
    second = bar.list.visualItemRect(bar.list.item(1)).top()
    assert wait_until(lambda: bar.highlight_y is not None, 0.3)
    assert first <= bar.highlight_y < second  # …and the highlight glides after it
    assert wait_until(lambda: bar.highlight_y is None, 1)
    bar.input.setText("gith")
    bar.update_results()
    assert bar.highlight_y is None
    bar.close_bar()


def test_a_keyboard_switch_mid_swipe_never_wedges_the_sidebar(window, site):
    first, second = _two_spaces(window)
    open_page(window, f"{site}/index.html")
    width = window.sidebar.width()
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.01)  # let go: it's settling…
    assert window.space_swipe.state == "settling"
    window.cycle_space(1)  # …and ⌃⌥→ (or a Space dot) comes in now
    pause(0.4)
    assert window.space_swipe.slide is None and window.space_swipe.state not in ("mine", "settling")
    # The tab list still scrolls, and Space swipes still work
    target = window.sidebar.scroll.viewport()
    at = QPointF(60, 200)
    for phase, dy in ((Qt.ScrollPhase.ScrollBegin, 0), (Qt.ScrollPhase.ScrollUpdate, 20), (Qt.ScrollPhase.ScrollUpdate, 20)):
        event = QWheelEvent(at, QPointF(target.mapToGlobal(at.toPoint())), QPoint(0, dy), QPoint(0, dy * 4),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, phase, False)
        used = window.space_swipe.wheel(event)
        QApplication.processEvents()
    assert used is False  # a vertical scroll is the tab list's, not swallowed
    _sidebar_swipe(window, 0, 0, begin=False)
    before = window.store.active_space
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.01)
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    assert window.store.active_space is not before


def test_dragging_a_tab_to_pinned_leaves_no_gap_behind(window, site):
    from browser.sidebar import RowReveal
    a = open_page(window, f"{site}/index.html")
    b = open_page(window, f"{site}/links.html")
    pause(0.4)
    today, pinned = window.sidebar.today, window.sidebar.pinned
    row = next(r for r in today.rows if r.tab is b)
    today.lift(row)
    assert wait_until(lambda: row.parentWidget().height() == 0, 1)
    _drag_event("move", pinned, 10, b)
    _drag_event("drop", pinned, 10, b)
    assert wait_until(lambda: any(r.tab is b for r in pinned.rows), 2)
    pause(0.05)
    shrinking = [r for r in today.findChildren(RowReveal) if not r.growing and not sip.isdeleted(r)]
    assert not shrinking  # no empty 34-point slot reopening where it was
    assert [r.tab for r in today.rows] == [a]


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
    _swipe(tab.view, 120)  # a short, slow swipe: it springs back, the fingers lift without a glide
    pause(0.4)
    assert id(tab.view) not in window.swipes._gliding


def test_everything_in_the_sidebar_has_a_name_for_voiceover(window, site):
    from PyQt6.QtWidgets import QAbstractButton, QFrame
    open_page(window, f"{site}/index.html")
    tab = open_page(window, f"{site}/links.html")
    window.move_tab(tab, "favorites")
    pause(0.4)
    unnamed = []
    controls = window.sidebar.findChildren(QAbstractButton) + [
        f for f in window.sidebar.findChildren(QFrame) if f.objectName() == "TabRow"]
    for w in controls:
        if w.isVisible() and not (w.accessibleName() or (hasattr(w, "text") and w.text())):
            unnamed.append(f"{type(w).__name__}[{w.objectName()}] {w.toolTip()!r}")
    assert not unnamed, unnamed
    row = window.sidebar.today.rows[0]
    assert row.accessibleName().startswith(row.tab.label)
    assert window.sidebar.back_btn.accessibleName() == "Back"  # (not "Back (⌘[)")


def test_tab_row_hover_fades_in_and_out(window, site):
    from PyQt6.QtGui import QEnterEvent
    open_page(window, f"{site}/index.html")
    other = open_page(window, f"{site}/links.html")
    pause(0.3)
    row = next(r for r in window.sidebar.today.rows if r.tab is not other)  # (not the selected one)
    at = QPointF(row.rect().center())
    QApplication.sendEvent(row, QEnterEvent(at, at, QPointF(row.mapToGlobal(at.toPoint()))))
    # Not on at once (checked straight away: a busy machine can stall past the whole fade between polls)
    assert row._hover < 1
    assert wait_until(lambda: row._hover == 1.0, 1)
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    assert row._hover > 0  # (nor off at once)
    assert wait_until(lambda: row._hover == 0.0, 1)


def test_a_finished_download_can_be_dragged_out_or_opened(window, tmp_path, monkeypatch):
    from browser import downloads
    item = downloads.DownloadItem("report.pdf", "ph.file")
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")
    row = downloads.DownloadRow(item, "#888888")
    assert row._file() is None  # still running: nothing to drag yet
    item.state, item.path = "done", str(tmp_path / "report.pdf")
    item.changed.emit()
    assert row._file() == item.path and "Drag" in row.toolTip()
    opened = []
    monkeypatch.setattr(downloads.QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url)))
    row.mouseDoubleClickEvent(None)
    assert opened and opened[0].toLocalFile() == item.path
    row.deleteLater()


def test_clear_can_be_undone(window, site):
    a = open_page(window, f"{site}/index.html")
    b = open_page(window, f"{site}/links.html")
    folder = window.store.add_folder(window.store.active_space, "Reading", kind="today")
    window.move_tab(a, "today", 0, folder)
    pause(0.3)
    before = window.store.today_shape(window.store.active_space)
    archived_before = len(window.store.archive)
    window.clear_today()
    assert wait_until(lambda: not window.store.active_space.today, 1)
    assert len(window.store.archive) == archived_before + 2
    assert window.toasts[-1].startswith("Archived 2") and window.toast_action is not None
    fresh = open_page(window, f"{site}/img.html")  # opened in the meantime: kept
    window.toast_action()  # Undo
    today = window.store.active_space.today
    assert today[0] is fresh and folder in today and b in today and a in folder.children
    assert len(window.store.archive) == archived_before  # back out of the archive
    assert window.current_tab is b


def test_a_toast_with_a_button_takes_clicks(window):
    pressed = []
    window.real_toast("Archived 3 tabs", lambda: pressed.append(True))
    toast = window.content.toast
    assert wait_until(lambda: toast.isVisible(), 1)
    button = window.content.toast_button
    assert button.isVisible() and button.text() == "Undo"
    assert not toast.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    button.click()
    assert pressed == [True] and wait_until(lambda: not toast.isVisible(), 1)
    window.real_toast("Copied URL to clipboard")  # a plain one lets clicks through to the page
    assert toast.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not window.content.toast_button.isVisibleTo(toast)


def test_a_little_window_fades_in_and_out(window, site):
    from browser.little import LittleWindow
    little = window._track(LittleWindow(window, url=f"{site}/index.html"))
    little.show()
    assert little.windowOpacity() < 0.5  # on its way in, not there at once
    assert wait_until(lambda: little.windowOpacity() == 1.0, 1)
    home = little.pos()
    pause(0.1)
    assert little.pos() == home  # (and it's settled where it was placed)
    closed = []
    little.destroyed.connect(lambda: closed.append(True))
    little.close()
    assert not closed and little.isVisible()  # fading out first…
    assert wait_until(lambda: closed, 1.5)  # …then gone


def test_command_bar_results_have_text_for_voiceover(window, site):
    window.store.record_history("https://github.com/", "GitHub")
    open_page(window, f"{site}/index.html")
    window.open_command_bar("new")
    bar = window.command_bar
    bar.input.setText("git")
    bar.update_results()
    texts = [bar.list.item(i).data(Qt.ItemDataRole.AccessibleTextRole) for i in range(bar.list.count())]
    assert texts and all(texts)
    bar.close_bar()


def test_panels_fade_in_when_they_open(window):
    window.open_settings("general")
    panel = window._settings
    assert panel.windowOpacity() < 0.5  # on its way in
    assert wait_until(lambda: panel.windowOpacity() == 1.0, 1)
    window.open_settings("privacy")  # already open: brought forward, no fade again
    assert panel.windowOpacity() == 1.0
    panel.close()


def test_the_find_bar_slides_down_and_back_up(window, site):
    open_page(window, f"{site}/article.html")
    bar = window.content.find_bar
    window.open_find()
    assert bar.isVisible() and bar.y() < bar.TOP  # coming down from above the page
    assert wait_until(lambda: bar.y() == bar.TOP, 1)
    bar.close_bar()
    assert bar.isVisible()  # on its way back up…
    assert wait_until(lambda: not bar.isVisible(), 1)  # …then gone
    window.open_find()  # and again
    assert wait_until(lambda: bar.y() == bar.TOP and bar.isVisible(), 1)
    bar.close_bar()


def test_a_tab_keeps_its_place_through_sleep_and_restart(window, site, data_dir, qapp):
    from browser import storage
    from browser.models import Store
    tab = open_page(window, f"{site}/long.html")
    run_js(tab.view, "window.scrollTo(0, 3000)", 0)
    pause(0.3)
    open_page(window, f"{site}/index.html")  # away from it…
    window.sleep_idle_tabs(force=True)  # …and it sleeps
    assert wait_until(lambda: window.is_sleeping(tab), 2)
    window.activate_tab(tab)  # back: it reloads…
    assert wait_until(lambda: tab.view.page().scrollPosition().y() > 2900, 5)  # …where you were
    # And after quitting and opening again
    run_js(tab.view, "window.scrollTo(0, 5000)", 0)
    pause(0.3)
    window.close()  # (saves the session)
    restored = Store(storage.session_path())
    again = next(t for t in restored.all_tabs() if t.id == tab.id)
    assert again.scroll and again.scroll["y"] >= 4900 and again.scroll["url"].endswith("long.html")


def test_a_restored_tab_opens_where_it_was_left(window, site):
    tab = open_page(window, f"{site}/long.html")
    open_page(window, f"{site}/index.html")
    window._destroy_view(tab)  # as after a restart: no page yet, just what was saved
    tab.scroll = {"url": f"{site}/long.html", "x": 0, "y": 4000}
    window.activate_tab(tab)
    assert wait_until(lambda: tab.view is not None and tab.view.page().scrollPosition().y() > 3900, 6)
    assert tab.scroll is None  # used once


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
    monkeypatch.setattr(window_module, "BrowseForMe", FakeRun)
    monkeypatch.setattr(type(window.claude), "available", property(lambda self: True))
    window.browse_for_me("best espresso grinder")
    run = FakeRun.made[-1]
    tab = window.current_tab
    assert not run.cancelled
    window.close_tab(tab)
    assert wait_until(lambda: run.cancelled, 1)
    assert run not in window._browse_runs


def test_dark_websites_turn_on_at_once_and_a_site_can_stay_light(window, site):
    from browser import dark_pages

    def colour(tab):
        image = tab.view.grab().toImage()
        return image.pixelColor(image.width() // 2, image.height() - 20).lightness()
    tab = open_page(window, f"{site}/index.html")
    pause(0.4)
    assert colour(tab) > 200  # a light page
    window.open_settings("general")
    window._settings.dark_box.setChecked(True)
    window._settings.close()
    assert wait_until(lambda: colour(tab) < 60, 2)  # darkened, no reload
    window._set_dark_here(QUrl(tab.url).host(), False)  # this site: leave it light
    assert wait_until(lambda: colour(tab) > 200, 2)
    window._set_dark_here(QUrl(tab.url).host(), True)
    fresh = open_page(window, f"{site}/links.html")  # a new page: dark from the start
    pause(0.4)
    assert colour(fresh) < 60
    window.store.settings[dark_pages.SETTING] = False
    window.apply_dark_pages()
    assert wait_until(lambda: colour(fresh) > 200, 2)


def test_whole_page_screenshot_is_stitched_without_repeating_the_header(window, site, tmp_path):
    window.store.settings["ytdlp"] = {"folder": str(tmp_path)}
    tab = open_page(window, f"{site}/shot.html")
    run_js(tab.view, "scrollTo(0, 700)")
    pause(0.3)
    saved = {}
    window.toast = lambda text, action=None, label="Undo": saved.update(text=text, action=action)
    window.save_page_screenshot()
    assert wait_until(lambda: saved.get("text", "").startswith("Saved"), 15)
    from PyQt6.QtGui import QImage
    image = QImage(str(tmp_path / "Tall page.png"))
    ratio = round(image.width() / tab.view.width())
    assert abs(image.height() - 3000 * ratio) <= ratio  # every band, top to bottom

    def at(y):
        c = image.pixelColor(image.width() // 2, y * ratio)
        return c.red() > 200, c.green() > 200, c.blue() > 200
    assert at(10) == (True, False, False)        # the fixed header, once, at the top
    assert at(100) == (False, False, True)       # blue
    assert at(900) == (False, True, False)       # green, with no header painted over it at a frame's top
    for y in range(600, 3000, 50):
        c = image.pixelColor(image.width() // 2, y * ratio)
        assert not (c.red() > 200 and c.green() < 60 and c.blue() < 60), y  # the header never repeats
    assert at(2990) == (True, False, True)       # magenta: the very bottom
    orange = image.pixelColor(image.width() // 2, 1210 * ratio)
    assert orange.red() > 200 and 90 < orange.green() < 170  # a sticky heading down the page stays in place
    assert wait_until(lambda: run_js(tab.view, "scrollY") == 700, 2)  # put back where it was
    assert run_js(tab.view, "getComputedStyle(document.querySelector('header')).visibility") == "visible"


def test_copy_links_and_share(window, site):
    from browser import macos
    from browser.models import Folder
    a = open_page(window, f"{site}/index.html")
    b = open_page(window, f"{site}/links.html")
    window.copy_today_links()
    text = QApplication.clipboard().text().splitlines()
    assert f"- [{b.label}]({b.url})" in text and f"- [{a.label}]({a.url})" in text
    folder = Folder("Reading", [a])
    window.copy_links(folder.children)
    assert QApplication.clipboard().text() == f"- [{a.label}]({a.url})"
    assert macos.share_picker(b.url)  # the share menu has something to share (showing it would take focus)


def test_several_tabs_picked_with_command_and_shift_click(window, site, monkeypatch):
    from PyQt6.QtTest import QTest
    tabs = [open_page(window, f"{site}/index.html?{i}") for i in range(5)]
    sidebar = window.sidebar
    rows = {id(r.tab): r for r in sidebar.today.rows}
    order = [r.tab for r in sidebar.today.rows if any(r.tab is t for t in tabs)]
    command, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier

    def click(tab, mods=Qt.KeyboardModifier.NoModifier):
        QTest.mouseClick(rows[id(tab)], Qt.MouseButton.LeftButton, mods, QPoint(60, 17))
    click(order[0])
    assert window.current_tab is order[0]
    click(order[2], command)  # the tab you're on, and this one
    assert sidebar.marked == [order[0], order[2]] and window.current_tab is order[0]
    click(order[4], shift)    # and everything down to here
    assert [t for t in order if t in sidebar.marked] == [order[0], order[2], order[3], order[4]]
    assert rows[id(order[3])].property("marked") is True and rows[id(order[1])].property("marked") is not True
    click(order[2], command)  # taken back out
    assert order[2] not in sidebar.marked
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert sidebar.marked == []

    saved = {}
    window.toast = lambda text, action=None, label="Undo": saved.update(text=text, action=action)
    click(order[1], command)  # (with the tab you're on, order[0])
    click(order[3], shift)
    window.close_tabs(list(sidebar.marked))
    assert saved["text"] == "Closed 4 tabs" and sidebar.marked == []
    assert [t for t in order if window.store.locate(t)] == [order[4]]
    assert window.current_tab is order[4]
    saved["action"]()  # Undo: the same four, in the same order, on top
    today = [t.url for t in window.store.active_space.today[:4]]
    assert today == [t.url for t in order[:4]]

    below = window._tabs_below(window.store.active_space.today[0], window.store.active_space)
    assert [t.url for t in below[:2]] == [order[1].url, order[2].url]

    first, second = window.store.active_space.today[:2]
    monkeypatch.setattr("browser.window.QInputDialog.getText", lambda *a, **k: ("Reading", True))
    window.folder_from([first, second])
    folder = next(f for f in window.store.active_space.pinned if getattr(f, "name", "") == "Reading")
    assert folder.children == [first, second]


def test_picked_tabs_are_dragged_together(window, site):
    from PyQt6.QtTest import QTest
    from browser.sidebar import dragged_together, stack_picture
    a, b, c, d, e = (open_page(window, f"{site}/index.html?{i}") for i in "abcde")
    pause(0.4)
    sidebar = window.sidebar
    today, pinned = sidebar.today, sidebar.pinned
    assert [r.tab for r in today.rows] == [e, d, c, b, a]
    rows = {id(r.tab): r for r in today.rows}
    window.activate_tab(d)
    sidebar.mark(b)  # d (the tab you're on) and b
    together = dragged_together(rows[id(b)])
    assert [r.tab for r in together] == [d, b]  # in sidebar order, whichever one you grab
    assert dragged_together(rows[id(c)]) == []  # grabbing a tab that isn't picked drags just that one
    picture = stack_picture(rows[id(b)], 2)
    assert picture.width() > rows[id(b)].grab().width()  # the stack, with the count

    # A plain click on a picked tab waits for the release (it may be a drag), then goes to it
    QTest.mousePress(rows[id(b)], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(60, 17))
    assert len(sidebar.marked) == 2
    QTest.mouseRelease(rows[id(b)], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(60, 17))
    assert sidebar.marked == [] and window.current_tab is b

    window.activate_tab(d)
    sidebar.mark(b)
    for row in together:
        today.lift(row)
    assert wait_until(lambda: all(r.parentWidget().height() == 0 for r in together), 1)  # both fold away
    _drag_event("move", pinned, 10, b, [d, b])
    _drag_event("drop", pinned, 10, b, [d, b])
    assert wait_until(lambda: [r.tab for r in pinned.rows] == [d, b], 2)
    assert wait_until(lambda: [r.tab for r in today.rows] == [e, c, a], 2)
    assert sidebar.marked == []

    # Back into Today, between e and c
    window.activate_tab(d)
    sidebar.mark(b)
    rows = {id(r.tab): r for r in today.rows}
    _drag_event("drop", today, rows[id(c)].geometry().top() + 4, d, [d, b])
    assert wait_until(lambda: [r.tab for r in today.rows] == [e, d, b, c, a], 2)

    # Onto the page: beside the tab you're on, in Split View
    window.activate_tab(e)
    sidebar.mark(c)
    sidebar.mark(a)
    window.content.split_drop.activate(True)
    _drag_event("drop", window.content.split_drop, 50, c, [e, c, a])
    assert wait_until(lambda: [t.url for t in window.store.split_of(e) or []] == [e.url, c.url, a.url], 2)


def test_keep_awake_mute_others_and_reload_a_folder(window, site):
    from browser.models import Folder
    window.profile.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    saved = {}
    window.toast = lambda text, action=None, label="Undo": saved.update(text=text, action=action)
    radio = open_page(window, f"{site}/music.html")
    assert wait_until(lambda: window.tab_audio(radio)[0], 8), "the page never played"
    kept = open_page(window, f"{site}/index.html")
    other = open_page(window, f"{site}/links.html")
    assert window.others_audible(other)
    window.mute_other_tabs(other)
    assert saved["text"] == "Muted 1 tab" and window.tab_audio(radio)[1]
    saved["action"]()  # Undo
    assert not window.tab_audio(radio)[1]
    radio.view.page().setAudioMuted(True)  # (quiet for the rest of the run)

    window.toggle_awake(kept)
    assert kept.awake
    window.sleep_idle_tabs(force=True)
    assert not window.is_sleeping(kept)
    window.toggle_awake(kept)
    window.sleep_idle_tabs(force=True)
    assert window.is_sleeping(kept)

    run_js(other.view, "window.marker = 1")
    window.reload_tabs(Folder("Work", [other, kept]).children)
    assert saved["text"] == "Reloaded 1 tab"  # the sleeping one isn't open
    assert wait_until(lambda: run_js(other.view, "window.marker === undefined"), 5)


def test_resting_on_a_tab_shows_its_preview(window, site):
    from browser.tab_preview import DELAY_MS
    preview = window.tab_preview
    a = open_page(window, f"{site}/shot.html")
    def blue(c):
        return c.blue() > 200 and c.red() < 60

    assert wait_until(lambda: blue(a.view.grab().toImage().pixelColor(200, 200)), 5)  # painted
    b = open_page(window, f"{site}/links.html")
    pause(0.2)
    rows = {id(r.tab): r for r in window.sidebar.today.rows}
    thumbnail = window.thumbnails[a.id].toImage()  # taken as you left it
    assert blue(thumbnail.pixelColor(thumbnail.width() // 2, 60))
    preview.hover(rows[id(a)])
    pause(DELAY_MS / 1000 / 2)
    assert not preview.isVisible()  # not straight away
    assert wait_until(lambda: preview.isVisible(), 1)
    assert preview.picture.isVisible() and preview.title.text() == "Tall page"
    shown = preview.picture.pixmap()
    assert abs(shown.height() / shown.width() - 0.625) < 0.02  # the top of the page, 16:10
    assert blue(shown.toImage().pixelColor(shown.width() // 2, shown.height() // 2))
    assert preview.host.text().startswith("127.0.0.1")
    sidebar_right = window.sidebar.mapTo(window, QPoint(window.sidebar.width(), 0)).x()
    assert preview.x() > sidebar_right  # beside the sidebar, not over it
    preview.leave(rows[id(a)])
    assert wait_until(lambda: not preview.isVisible(), 1)
    preview.hover(rows[id(b)])  # the tab you're on: nothing to preview
    pause(0.1)
    assert not preview.isVisible()
    preview.leave(rows[id(b)])
    window.store.settings["tab_previews"] = False
    preview.dismiss()
    preview.hover(rows[id(a)])
    pause(DELAY_MS / 1000 + 0.2)
    assert not preview.isVisible()
    window.store.settings["tab_previews"] = True


def test_the_sidebar_can_go_on_the_right(window, site, monkeypatch):
    from browser import floating_sidebar
    from browser.tab_preview import GAP
    a = open_page(window, f"{site}/index.html")
    open_page(window, f"{site}/links.html")
    width = window.store.settings["sidebar_width"]
    window.set_sidebar_side("right")
    pause(0.4)
    sidebar, content, root = window.sidebar, window.content, window.centralWidget()
    assert window.splitter.widget(1) is sidebar
    assert sidebar.mapTo(window, QPoint(0, 0)).x() > content.mapTo(window, QPoint(0, 0)).x()
    assert abs(sidebar.width() - width) <= 1 and window.store.settings["sidebar_width"] == width
    handle = window.splitter.handle(1)
    assert handle.mapTo(window, QPoint(0, 0)).x() == content.mapTo(window, content.rect().topRight()).x() + 1
    assert content.mapTo(window, QPoint(0, 0)).y() == 8  # (the traffic lights moved over into the sidebar)

    row = next(r for r in sidebar.today.rows if r.tab is a)  # previews open to the left of it
    window.tab_preview.hover(row)
    assert wait_until(lambda: window.tab_preview.isVisible(), 1.5)
    assert window.tab_preview.geometry().right() < sidebar.mapTo(window, QPoint(0, 0)).x() - GAP + 2
    window.tab_preview.dismiss()

    window.toggle_sidebar()  # hidden: touching the right edge brings it out on that side
    pause(0.4)
    assert not sidebar.isVisible()
    pointer = {"at": QPoint(root.width() - 3, 300)}
    monkeypatch.setattr(floating_sidebar.QCursor, "pos", staticmethod(lambda: root.mapToGlobal(pointer["at"])))
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window._reveal_sidebar()
    out_x = root.width() - sidebar.width() - floating_sidebar.MARGIN
    assert wait_until(lambda: sidebar.x() == out_x, 1)
    pause(0.3)
    assert window.floating_sidebar.state == "in"  # resting at the right edge: it stays
    pointer["at"] = QPoint(100, 300)  # off over the page, to the left
    assert wait_until(lambda: window.floating_sidebar.state is None, 2) and not sidebar.isVisible()
    window.toggle_sidebar()
    pause(0.4)
    assert sidebar.isVisible() and window.splitter.widget(1) is sidebar

    window.set_sidebar_side("left")
    pause(0.3)
    assert window.splitter.widget(0) is sidebar and abs(sidebar.width() - width) <= 1
    assert content.mapTo(window, QPoint(0, 0)).y() <= 10


def test_review_fixes_for_picking_tabs_and_screenshots(window, site, tmp_path):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent
    sidebar = window.sidebar
    a, b, c = (open_page(window, f"{site}/index.html?{i}") for i in "abc")
    # Esc clears picked tabs, but still reaches a page or field it was meant for
    sidebar.mark(a)
    assert len(sidebar.marked) == 2
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    assert window.eventFilter(window.content, esc) is False and sidebar.marked == []
    sidebar.mark(a)
    assert window.eventFilter(sidebar, esc) is True and sidebar.marked == []
    # A picked tab closing leaves no one-tab selection behind
    sidebar.mark(a)
    window.close_tab(a)
    assert sidebar.marked == [] and not any(r.property("marked") is True for r in sidebar.today.rows)
    # With a favorite as the current tab, ⌘-click still picks tabs
    fav = window.store.favorites[0]
    window.activate_tab(fav)
    sidebar.mark(b)
    sidebar.mark(c)
    assert sidebar.marked == [b, c]
    sidebar.clear_marks()
    # Pinned tabs moved to another Space keep their order
    from browser.models import PINNED
    window.move_tab(b, PINNED)
    window.move_tab(c, PINNED)
    other = window.store.add_space("Elsewhere", "#3b82f6")
    window.move_tabs_to_space([b, c], other)
    assert [t for t in other.pinned if t is b or t is c] == [b, c]
    window.switch_space(window.store.spaces[0])

    # A whole-page screenshot stops (and puts the page back) when you switch away mid-way
    window.store.settings["ytdlp"] = {"folder": str(tmp_path)}
    tall = open_page(window, f"{site}/shot.html")
    pause(0.5)
    saved = {}
    window.toast = lambda text, action=None, label="Undo": saved.update(text=text, action=action)
    window.save_page_screenshot()
    pause(0.15)
    window.activate_tab(fav)
    assert wait_until(lambda: saved.get("text", "").startswith("Stopped the screenshot"), 5)
    assert window._page_shot is None and not list(tmp_path.glob("*.png"))
    assert wait_until(lambda: run_js(tall.view, "document.getElementById('__ecShotStyle') === null"), 3)


def test_easel_handles_resize_and_the_easel_exports(window, tmp_path, monkeypatch):
    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QGraphicsRectItem
    from browser.easel import _movable, geometry
    window.store.settings["ytdlp"] = {"folder": str(tmp_path)}
    tab = window.new_easel()
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


GITHUB_RELEASES = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Release notes from qtwebengine</title>
<entry><title>v6.12.0</title><link rel="alternate" type="text/html" href="https://github.com/qt/qtwebengine/releases/tag/v6.12.0"/></entry>
<entry><title>v6.11.2</title><link rel="alternate" type="text/html" href="https://github.com/qt/qtwebengine/releases/tag/v6.11.2"/></entry>
</feed>"""


def test_follow_releases_of_a_github_repo(window, monkeypatch):
    from browser.models import Tab
    asked = []

    def fake_get(url, done, fail, headers=None, secret=False):  # (GitHub, without the network)
        asked.append(url)
        QTimer.singleShot(0, lambda: done(GITHUB_RELEASES, url))
    monkeypatch.setattr(window.live_folders, "_get", fake_get)
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
    window.create_live_folder({"kind": "feed", "url": "https://github.com/qt/qtbase"}, "qtbase")
    assert wait_until(lambda: any(f.name == "qtwebengine Releases" and f is not folder
                                  for f in window.store.active_space.pinned if getattr(f, "live", None)), 3)


def test_undo_of_closing_several_tabs_puts_them_back_in_their_folders(window, site):
    from browser.models import TODAY, Folder, walk_tabs
    space = window.store.active_space
    a, b, c, d = (open_page(window, f"{site}/index.html?{n}") for n in "abcd")
    folder = window.store.add_folder(space, "Reading", kind=TODAY)
    window.store.move_tab(folder, TODAY, index=0)
    window.store.move_tab(a, TODAY, folder=folder)
    window.store.move_tab(b, TODAY, folder=folder)
    before = [(n.name, [t.url for t in n.children]) if isinstance(n, Folder) else n.url for n in space.today]
    saved = {}
    window.toast = lambda text, action=None, label="Undo": saved.update(text=text, action=action)
    window.close_tabs([a, b, c])  # the folder goes with its last tab
    assert saved["text"] == "Closed 3 tabs" and folder not in space.today
    saved["action"]()
    after = [(n.name, [t.url for t in n.children]) if isinstance(n, Folder) else n.url for n in space.today]
    assert after == before
    back = [t for t in walk_tabs(space.today) if t.url in (a.url, b.url, c.url)]
    assert len(back) == 3 and all(t.view is None for t in back)  # (asleep until clicked)
    assert window.current_tab is d


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
    monkeypatch.setattr(window.live_folders, "_gh", fake_gh)
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
    window.live_folders.refresh(folder)
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
    window.live_folders.refresh(folder)
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
        QTimer.singleShot(0, lambda: (window.pr_status._answers.__setitem__(pull, (time.time(), dict(answer))),
                                      window.pr_status.changed.emit(url)))
    monkeypatch.setattr(window.pr_status, "_ask", fake_ask)
    chip = window.sidebar.pr_btn
    previous = window.current_tab
    window.current_tab = Tab("https://github.com/o/r/pull/12/files", "PR")
    try:
        window.sidebar.update_pr()
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
        window.show_pr_checks(QPoint(0, 0))
        assert shown == ["CI · test", "CI · lint", "Open Checks Page", "Check Again"]
        # Checks still running: it looks again by itself while you're there
        answer["statusCheckRollup"][0].update(status="IN_PROGRESS", conclusion="")
        window.pr_status.lookup(window.current_tab.url, force=True)
        assert wait_until(lambda: chip.text() == "● 1 running · Approved", 2)
        assert window.pr_status._poll.isActive()
    finally:
        window.current_tab = previous
        window.sidebar.update_pr()
    assert not chip.isVisible() and not window.pr_status._poll.isActive()  # elsewhere: no chip, no looking


def test_your_github_repos_are_in_the_command_bar(window):
    window.github_repos.repos = [
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


def _lights_state(window):
    """(hidden, alpha, x, y from the title bar's bottom) of the window's close button."""
    import ctypes
    from browser.macos import _CGRect, _send
    native = _send(ctypes.c_void_p(int(window.winId())), "window")
    button = _send(native, "standardWindowButton:", 0, argtypes=(ctypes.c_long,))
    frame = _send(button, "frame", restype=_CGRect)
    return (_send(button, "isHidden", restype=ctypes.c_bool), round(_send(button, "alphaValue", restype=ctypes.c_double), 2),
            round(frame.x), round(frame.y))


def test_traffic_lights_go_with_the_sidebar(window, monkeypatch):
    from browser import floating_sidebar, macos, traffic_lights
    if not macos._cocoa():
        pytest.skip("macOS's own window buttons")
    pause(0.3)
    hidden, alpha, x, y = _lights_state(window)
    assert not hidden and alpha == traffic_lights.DIM  # docked: there, dim until pointed at
    home = (x, y)
    # Pointing at them lights them up; moving away dims them again
    monkeypatch.setattr(window.traffic_lights, "near", lambda: True)
    window.traffic_lights._check()
    assert wait_until(lambda: _lights_state(window)[1] == 1.0, 2)
    monkeypatch.setattr(window.traffic_lights, "near", lambda: False)
    window.traffic_lights._check()
    assert wait_until(lambda: _lights_state(window)[1] == traffic_lights.DIM, 2)
    # Sidebar hidden: gone, and the page has the whole window
    window.toggle_sidebar()
    pause(0.5)
    assert _lights_state(window)[0] and window.content_layout.contentsMargins().top() == 8
    # Out over the page: they come out with it, as far in from the corner as it is
    fs = window.floating_sidebar
    monkeypatch.setattr(fs, "_wanted", lambda: True)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    fs.edge_touched()
    assert _lights_state(window)[0]  # (not while it's on its way)
    assert wait_until(lambda: not _lights_state(window)[0], 2)
    m = floating_sidebar.MARGIN
    assert _lights_state(window)[2:] == (home[0] + m, home[1] - m)
    monkeypatch.setattr(fs, "_wanted", lambda: False)
    fs.slide_away()
    assert _lights_state(window)[0]
    assert wait_until(lambda: not fs.active, 2)
    assert _lights_state(window)[2:] == home
    # Back, then on the right: they move over into the sidebar
    window.toggle_sidebar()
    pause(0.5)
    window.set_sidebar_side("right")
    pause(0.5)
    hidden, _alpha, x, y = _lights_state(window)
    assert not hidden and x == home[0] + window.sidebar.mapTo(window, QPoint(0, 0)).x()
    assert window.content_layout.contentsMargins().top() == 8  # (no strip for them over the page)
    # A new title has macOS lay them out again, in the corner: the window puts them back
    window.setWindowTitle("Another page")
    assert _lights_state(window)[2] == x
    window.set_sidebar_side("left")
    pause(0.5)
    assert _lights_state(window)[2:] == home


def test_favorites_fill_the_row_however_many_there_are(window):
    grid = window.sidebar.favorites
    favorites = list(window.store.favorites)
    pause(0.2)

    def widths():
        return [t.width() for t in grid.tiles if t.isVisible()]
    assert len(favorites) >= 4
    full = grid.width()
    for n in (4, 3, 2, 1):
        window.store.favorites[:] = favorites[:n]
        window.store._changed()
        pause(0.2)
        tiles = [t for t in grid.tiles if t.isVisible()]
        assert len(tiles) == n
        assert abs(tiles[-1].geometry().right() - (full - 1)) <= 1, n  # out to the end of the row
        assert max(widths()) - min(widths()) <= 1  # all the same width
    window.store.favorites[:] = favorites
    window.store._changed()


def test_the_sidebar_scrollbar_stays_out_of_sight_until_you_scroll(window, site):
    from PyQt6.QtCore import QRect, Qt
    from browser.models import Tab
    rows_before = window.sidebar.today.rows[0].width() if window.sidebar.today.rows else None
    for i in range(25):  # enough tabs to scroll
        window.store.add_tab(Tab(f"{site}/index.html?{i}", f"Tab {i}"))
    inner = window.sidebar.scroll.verticalScrollBar()  # (keeps the position; never shown)
    bar = window.sidebar.scroll.overlay_scrollbar      # (the one you see, floating over the rows)
    assert wait_until(lambda: bar.isVisible() and bar.maximum() > 0, 3)  # (once the new rows have grown in)
    pause(0.4)
    assert bar.isVisible() and bar.maximum() > 0 and not inner.isVisible(), (
        bar.isHidden(), bar.maximum(), inner.maximum(), window.sidebar.scroll.isVisible(), window.sidebar.isVisible(), bar.geometry())
    if rows_before is not None:  # it takes no width: rows are as wide as before the list could scroll
        assert window.sidebar.today.rows[0].width() == rows_before
    # Out of sight, it lets clicks through to the rows' close buttons under it
    assert bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def drawn():
        region = QRect(bar.mapTo(window.sidebar, QPoint(0, 0)), bar.size())
        with_bar = window.sidebar.grab(region).toImage()
        bar.setVisible(False)
        without = window.sidebar.grab(region).toImage()
        bar.setVisible(True)
        return max(abs(with_bar.pixelColor(x, y).lightness() - without.pixelColor(x, y).lightness())
                   for x in range(with_bar.width()) for y in range(0, with_bar.height(), 4))
    assert drawn() == 0
    inner.setValue(inner.maximum())  # the sidebar moving by itself (a rebuild): still nothing
    pause(0.1)
    assert drawn() == 0 and bar.value() == inner.value()
    inner.triggerAction(inner.SliderAction.SliderSingleStepSub)  # you scrolling (the wheel goes to this one)
    pause(0.1)
    assert drawn() > 10 and not bar.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert wait_until(lambda: drawn() == 0, 2)  # and gone again a moment later
    # Dragging the one you see scrolls the list
    bar.setValue(0)
    assert inner.value() == 0


def test_pinned_tabs_get_their_icons_without_loading(window, site, monkeypatch):
    import os
    from PyQt6.QtGui import QColor, QImage
    from conftest import _site_root
    from browser import storage
    from browser.favicons import Favicons
    from browser.models import PINNED, Tab
    from browser.sidebar import TabRow
    icon_file = os.path.join(_site_root[0], "favicon.ico")
    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor("#22c55e"))
    image.save(icon_file, "ICO")
    try:
        monkeypatch.setattr(window.favicons, "want", Favicons.want.__get__(window.favicons))  # (the real one)
        monkeypatch.setattr("browser.window.is_dev_url", lambda url: False)  # (the test site is on localhost)
        url = site.replace("127.0.0.1", "localhost") + "/index.html?pinned"  # (a site with no icon yet)
        tab = Tab(url, "Pinned, never opened")
        window.store.add_tab(tab, PINNED)
        assert storage.favicon(url) is None
        window._want_icons()
        assert wait_until(lambda: storage.favicon(url) is not None, 5)
        assert tab.view is None  # (the page itself never loaded)
        pause(0.2)
        row = next(r for r in window.sidebar.findChildren(TabRow) if r.tab is tab)
        picture = row.icon_label.pixmap().toImage()
        middle = picture.pixelColor(picture.width() // 2, picture.height() // 2)
        assert middle.green() > 150 and middle.red() < 100  # the site's own green icon, not a letter
    finally:
        os.remove(icon_file)


def test_settings_cards_keep_their_dividers(window):
    from PyQt6.QtWidgets import QFrame
    window.open_settings()
    pause(0.4)
    settings = window._settings
    lines = [f for f in settings.findChildren(QFrame) if f.objectName() == "SettingsLine" and f.isVisible()]
    assert len(lines) >= 5
    settings.close()


def test_live_folder_items_and_dev_pages_dont_fetch_icons(window, monkeypatch):
    from browser.models import PINNED, Tab
    wanted = []
    monkeypatch.setattr(window.favicons, "want", lambda urls: wanted.extend(urls))
    feed = window.store.add_folder(window.store.active_space, "Feed")
    feed.live = {"kind": "feed", "url": "https://example.com/feed"}
    feed.children = [Tab("https://some-site-in-a-feed.example/post", "Post")]
    window.store.add_tab(Tab("https://mine.example/", "Mine"), PINNED)
    window.store.add_tab(Tab("http://localhost:3000/", "Dev"), PINNED)
    window._want_icons()
    assert "https://mine.example/" in wanted
    assert not any("feed.example" in u or "localhost" in u for u in wanted)


def test_zap_only_listens_to_itself(window, site):
    tab = open_page(window, f"{site}/index.html")
    window.start_zap()
    pause(0.3)
    zapped = []
    real = window.boosts.add_zapped
    window.boosts.add_zapped = lambda site_, sel: (zapped.append(sel), real(site_, sel))[1]
    # The page pretending to be Zap: ignored
    run_js(tab.view, "console.log('PYBROWSER_ZAP:x{} input{background:url(//evil/a)}')")
    run_js(tab.view, "console.log('PYBROWSER_ZAP:h1')")
    pause(0.3)
    assert zapped == []
    # A real click with the tool: heard (its message comes from its own world, with its token)
    run_js(tab.view, "document.querySelector('h1').click()")
    assert wait_until(lambda: zapped == ["h1"], 3), zapped
    from browser.boosts import safe_selector
    assert not safe_selector("x{} input{background:url(//evil/a)}") and safe_selector("div.ad > p:nth-of-type(2)")
    window.boosts.add_zapped = real


def test_a_local_file_cant_read_other_files(window, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read")
    page = tmp_path / "invoice.html"
    page.write_text("<p>hi</p>")
    tab = open_page(window, page.as_uri())
    run_js(tab.view, f"""fetch({secret.as_uri()!r}).then(r => r.text()).then(t => window.__got = t,
                                                                         () => window.__got = 'blocked')""")
    assert wait_until(lambda: run_js(tab.view, "window.__got || ''") != "", 3)
    assert run_js(tab.view, "window.__got") == "blocked"


def test_tab_searches_a_site_even_after_autocomplete(window, site):
    from PyQt6.QtGui import QKeyEvent
    window.store.history.insert(0, {"url": "https://www.youtube.com/", "title": "YouTube", "visits": 30,
                                    "last": time.time()})
    bar = window.command_bar
    window.open_command_bar("new")
    bar.input.setText("you")
    bar.input.textEdited.emit("you")
    pause(0.2)
    tab_key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    assert bar.eventFilter(bar.input, tab_key) is True
    assert bar.site is not None
    bar.close_bar()


def test_a_tab_you_leave_counts_as_used_until_then(window, site):
    a = open_page(window, f"{site}/index.html?a")
    b = open_page(window, f"{site}/index.html?b")
    window.activate_tab(a)
    a.last_active = time.time() - 7200  # (opened two hours ago, read since)
    window.activate_tab(b)
    assert time.time() - a.last_active < 5  # in use until you left it, not idle since it opened


def test_moving_a_split_tab_to_another_space_takes_it_out_of_the_split(window, site):
    a = open_page(window, f"{site}/index.html?a")
    b = open_page(window, f"{site}/index.html?b")
    window.activate_tab(a)
    window.add_to_split(b)
    pause(0.4)
    assert window.store.split_of(a)
    other = window.store.add_space("Elsewhere", "#3b82f6")
    window.move_to_space(b, other)
    pause(0.3)
    assert not window.store.split_of(a) and not window.store.split_of(b)
    window.switch_space(window.store.spaces[0])


def test_nothing_drops_into_a_live_folder(window, site):
    tab = open_page(window, f"{site}/index.html?drop")
    feed = window.store.add_folder(window.store.active_space, "Feed")
    feed.live = {"kind": "feed", "url": "https://example.com/feed", "refreshed": 1}
    window.store._changed()
    pause(0.3)
    section = window.sidebar.pinned
    from browser.sidebar import FolderRow
    folder_row = next(r for r in section.findChildren(FolderRow) if r.folder is feed)
    middle = folder_row.mapTo(section, QPoint(0, folder_row.height() // 2)).y()
    keep = _drag_event("drop", section, middle, tab)
    assert keep is not None and tab not in feed.children  # (refused: it stays where it was)


def test_a_page_cant_open_a_note(window, site):
    tab = open_page(window, f"{site}/index.html?note")
    before = len(list(window.store.all_tabs()))
    run_js(tab.view, "window.open('note:../../../../tmp/x'); window.open('easel:../x'); true")
    pause(0.5)
    assert len(list(window.store.all_tabs())) == before


def test_a_page_asks_before_opening_another_app(window, site, monkeypatch):
    import browser.external_links as external
    from browser.external_links import ExternalPrompt
    opened = []
    monkeypatch.setattr(external.macos, "app_for_url", lambda url: "Test App")
    monkeypatch.setattr(external.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    tab = open_page(window, f"{site}/index.html?apps")
    # The page sends itself to another app's link, no click needed: refused, and you're asked
    run_js(tab.view, "location.href = 'eclipsetest://join?id=1'; true")
    assert wait_until(lambda: any(p.isVisible() for p in window.findChildren(ExternalPrompt)), 3)
    prompt = next(p for p in window.findChildren(ExternalPrompt) if p.isVisible())
    assert "wants to open Test App" in prompt.text.text() and opened == []
    assert not prompt.open_button.isEnabled()  # (a moment before it can be clicked)
    assert wait_until(lambda: prompt.open_button.isEnabled(), 2)
    assert tab.view.url().scheme() == "http"  # still on its page
    prompt.always_button.click()
    assert opened == ["eclipsetest://join?id=1"]
    pause(0.2)
    assert not [p for p in window.findChildren(ExternalPrompt) if p.isVisible()]
    # Allowed for this site and this kind of link: straight through next time, no card
    run_js(tab.view, "location.href = 'eclipsetest://join?id=2'; true")
    assert wait_until(lambda: len(opened) == 2, 3)
    assert not [p for p in window.findChildren(ExternalPrompt) if p.isVisible()]
    # Another kind of link from it still asks; Cancel opens nothing
    run_js(tab.view, "location.href = 'eclipseother://x'; true")
    assert wait_until(lambda: any(p.isVisible() for p in window.findChildren(ExternalPrompt)), 3)
    prompt = next(p for p in window.findChildren(ExternalPrompt) if p.isVisible())
    assert prompt.link.text() == "eclipseother://x"  # (the card says which link)
    prompt.cancel_button.click()
    assert len(opened) == 2
    # Turned down: the page asking again straight away doesn't bring the card back
    run_js(tab.view, "location.href = 'eclipseother://again'; true")
    pause(0.6)
    assert not [p for p in window.findChildren(ExternalPrompt) if p.isVisible()] and len(opened) == 2
    # A frame in it asks as a frame, even for a kind of link the site may open, and gets no Always Allow
    run_js(tab.view, "const f = document.createElement('iframe'); f.src = 'eclipsetest://frame'; "
                     "document.body.append(f); true")
    assert wait_until(lambda: any(p.isVisible() for p in window.findChildren(ExternalPrompt)), 3)
    prompt = next(p for p in window.findChildren(ExternalPrompt) if p.isVisible())
    assert prompt.text.text() == "A frame on 127.0.0.1 wants to open Test App"
    assert not prompt.always_button.isVisible() and len(opened) == 2
    # Asking about another link restarts the wait, so an earlier question can't arm a later one
    assert wait_until(lambda: prompt.open_button.isEnabled(), 2)
    run_js(tab.view, "location.href = 'eclipseframe://swapped'; true")
    assert wait_until(lambda: prompt.link.text() == "eclipseframe://swapped", 3)
    assert not prompt.open_button.isEnabled()
    # A tab in the background can't ask
    background = open_page(window, f"{site}/index.html?bg")
    window.activate_tab(tab)
    pause(0.2)
    for p in window.findChildren(ExternalPrompt):
        p.manager._close(id(p.view))
    pause(0.1)
    run_js(background.view, "location.href = 'eclipseother://quiet'; true")
    pause(0.6)
    assert not [p for p in window.findChildren(ExternalPrompt) if p.isVisible()] and len(opened) == 2


def test_with_nothing_open_its_just_the_spaces_background(window, site):
    content = window.content
    for tab in list(window.store.active_space.today):
        window.close_tab(tab)
    pause(0.3)
    if window.current_tab is None:
        assert content.blank and content.property("blank") is True
    open_page(window, f"{site}/index.html")
    assert not content.blank and content.property("blank") is False
