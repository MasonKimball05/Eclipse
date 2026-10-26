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


def _sidebar_swipe(window, steps, step_dx, gap=0.016, end=True, shot=None, begin=True):
    """Two fingers sideways on the sidebar: `steps` moves of `step_dx` points each."""
    target = window.sidebar.scroll.viewport()
    at = QPointF(60, 200)

    def send(phase, dx):
        QApplication.sendEvent(target, QWheelEvent(at, QPointF(target.mapToGlobal(at.toPoint())), QPoint(int(dx), 0),
                                                   QPoint(int(dx) * 4, 0), Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier, phase, False))
        QApplication.processEvents()  # (one event per pass of the event loop, as from a trackpad)
    if begin:
        send(Qt.ScrollPhase.ScrollBegin, 0)
    for i in range(steps):
        send(Qt.ScrollPhase.ScrollUpdate, step_dx)
        time.sleep(gap)
        if shot and i == steps // 2:
            window.grab().save(shot)
    if end:
        send(Qt.ScrollPhase.ScrollEnd, 0)


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


def test_space_swipe_past_halfway_or_a_flick_switches(window, site):
    open_page(window, f"{site}/index.html")
    first, second = _two_spaces(window)
    width = window.sidebar.width()
    shot = "/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/space_mid.png"
    _sidebar_swipe(window, 14, -width * 0.05, gap=0.03, shot=shot)  # 70%, then let go
    assert wait_until(lambda: window.space_swipe.slide is None, 2)
    assert window.store.active_space is second
    _sidebar_swipe(window, 4, width * 0.05, gap=0.008)  # a short quick flick back
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
        window.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/page_mid.png")
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
    window.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/row_close.png")
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
    settings.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/settings_icons.png")
    settings.close()


def _drag_event(kind, section, y, tab):
    from PyQt6.QtCore import QMimeData
    from PyQt6.QtGui import QDragMoveEvent, QDropEvent
    from browser.sidebar import TAB_MIME
    mime = QMimeData()
    mime.setData(TAB_MIME, tab.id.encode())
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
    window.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/drag_gap.png")
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
    window.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/float_out.png")
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
    open_page(window, f"{site}/index.html")
    handle = window.splitter.handle(1)
    edge = handle.mapTo(window, handle.rect().topRight()).x()
    page = window.content.mapTo(window, window.content.rect().topLeft()).x()
    assert page == edge + 1  # the handle runs right up to the page: no dead strip in between
    assert window.content.mapTo(window, window.content.rect().topLeft()).y() <= 10  # (not the hidden-sidebar top strip)


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
    window.sidebar.favorites.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/favs.png")
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
    assert wait_until(lambda: 0 < row._hover < 1, 0.2)  # fading in, not on at once
    assert wait_until(lambda: row._hover == 1.0, 0.5)
    window.sidebar.today.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/hover.png")
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    assert wait_until(lambda: row._hover == 0.0, 0.6)


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
    window.grab().save("/private/tmp/claude-501/-Users-masonkimball-Documents-GitHub-PythonBrowser--claude-worktrees-arc-inspired-project-6bde43/54baf4d1-cb03-47ee-8e3f-a5154bdf41b6/scratchpad/toast.png")
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
