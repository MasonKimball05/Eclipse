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
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QApplication

from live_helpers import loaded, open_page, pause, run_js, steady, wait_until


def _rows(window):
    bar = window.command_bar
    return [bar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(bar.list.count())]


def test_cookie_banners_are_rejected_and_nothing_else_is_clicked(window, site):
    onetrust = open_page(window, f"{site}/consent.html")
    generic = open_page(window, f"{site}/consent2.html")
    aem = open_page(window, f"{site}/aem.html")
    assert wait_until(lambda: onetrust.view.title() == "REJECTED-ONETRUST", 8)
    # A banner that came late, in a tab in the background (where Chromium slows timers: on a busy Mac, a while)
    assert wait_until(lambda: generic.view.title() == "REJECTED-GENERIC", 20)
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
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    # (a script run from here isn't a click, and pages can't open windows by themselves: allowed for this page)
    view.page().settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
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
                 *(os.path.expanduser(f"~/Library/Application Support/MasonKimball05/{name}/dictionaries/en-US.bdic")
                   for name in ("Eclipse", "PythonBrowser"))):  # (the folder's old name, until Eclipse moves it)
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
    today = window.sidebar.today
    assert wait_until(lambda: [r.tab for r in today.rows] == [c, b, a], 3)  # (newest on top)
    steady(lambda: [r.geometry() for r in today.rows])  # (the rows have slid in)
    row_b = next(r for r in today.rows if r.tab is b)
    today.lift(row_b)
    assert wait_until(lambda: row_b.parentWidget().height() == 0, 3)  # picked up: its slot closes
    rows = {r.tab: r for r in today.rows}
    top_c, top_a = steady(lambda: (rows[c].geometry(), rows[a].geometry()))  # (the rest have closed up)
    _drag_event("move", today, top_a.center().y() + 8, b)  # the lower half of A: below A
    gap = today._gap
    assert gap is not None and wait_until(lambda: gap.height() == 34, 3)
    assert gap.geometry().top() > rows[a].geometry().top()  # after A, at the end
    _drag_event("move", today, top_a.center().y() + 9, b)  # a nudge: the same place, no new gap
    assert today._gap is gap
    _drag_event("move", today, top_c.center().y() - 6, b)  # the top half of C: before C
    assert today._gap is not gap and wait_until(lambda: gap.height() == 0, 3)  # the old one closes
    _drag_event("drop", today, top_c.center().y() - 6, b)
    assert wait_until(lambda: [r.tab for r in today.rows] == [b, c, a], 3)
    assert wait_until(lambda: not today.findChildren(DropGap), 3)
    # Picked up and let go of nowhere: it comes back
    row = next(r for r in today.rows if r.tab is a)
    today.lift(row)
    assert wait_until(lambda: row.parentWidget().height() == 0, 3)
    today.drag_ended(row)
    assert wait_until(lambda: row.parentWidget() is today and row.isVisible(), 3)
    assert [r.tab for r in today.rows] == [b, c, a]


def test_with_no_tab_open_the_hidden_sidebar_comes_out_and_stays(window, site, monkeypatch):
    from browser import floating_sidebar
    tab = open_page(window, f"{site}/index.html")
    root = window.centralWidget()
    pointer = {"at": QPoint(root.width() - 50, 300)}  # (well away from the edge: over the page)
    monkeypatch.setattr(floating_sidebar.QCursor, "pos", staticmethod(lambda: root.mapToGlobal(pointer["at"])))
    window.toggle_sidebar()  # hide it, with a tab open: it stays hidden
    pause(0.6)
    fs = window.floating_sidebar
    assert not window.sidebar.isVisible() and not fs.active
    window.close_tab(tab)  # nothing open now: out it comes, and stays though the pointer's nowhere near
    assert wait_until(lambda: fs.state == "in" and window.sidebar.x() == floating_sidebar.MARGIN, 2)
    pause(0.6)
    assert fs.state == "in" and fs.kept
    tab = open_page(window, f"{site}/index.html")  # a tab again: it goes once the pointer's away
    assert not fs.kept
    assert wait_until(lambda: not fs.active and not window.sidebar.isVisible(), 2)
    window.close_tab(tab)
    assert wait_until(lambda: fs.state == "in", 2)
    window.toggle_sidebar()  # ⌘S while it's out: docked, as usual
    assert window.sidebar.isVisible() and not fs.active and not fs.kept
    window.toggle_sidebar()  # and hidden again with nothing open: straight back out, floating
    assert wait_until(lambda: fs.state == "in" and fs.kept, 2)


def test_closing_a_tab_opened_from_the_floating_sidebar_keeps_it_out(window, site, monkeypatch):
    from browser import floating_sidebar
    tab = open_page(window, f"{site}/index.html")
    root = window.centralWidget()
    pointer = {"at": QPoint(60, 300)}  # (over the sidebar, once it's out)
    monkeypatch.setattr(floating_sidebar.QCursor, "pos", staticmethod(lambda: root.mapToGlobal(pointer["at"])))
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window.toggle_sidebar()
    window.close_tab(tab)  # nothing open: out it comes
    fs = window.floating_sidebar
    assert wait_until(lambda: fs.state == "in" and fs.kept, 2)
    # A tab picked in it, then closed (its ✕ in the sidebar) while the sidebar's still out under the pointer
    tab = open_page(window, f"{site}/index.html")
    assert fs.state == "in" and not fs.kept
    window.close_tab(tab)
    assert fs.kept  # (nothing's open again: it stays)
    pointer["at"] = QPoint(root.width() - 50, 300)  # the pointer goes off over the page
    pause(floating_sidebar.HIDE_DELAY / 1000 + 0.6)
    assert fs.state == "in" and window.sidebar.isVisible()  # (it used to slide away, leaving no sidebar at all)
    window.toggle_sidebar()


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


def test_peek_eases_in_and_out(window, site, monkeypatch):
    from browser import peek as peek_module
    peek = window.peek
    # (no click to grow from: the pointer's off the window, so it rises into place)
    monkeypatch.setattr(peek_module.QCursor, "pos", staticmethod(lambda: window.mapToGlobal(QPoint(-500, -500))))
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


def test_peek_grows_out_of_the_link_and_back_into_it(window, site, monkeypatch):
    from browser import motion, peek as peek_module
    peek = window.peek
    monkeypatch.setattr(motion, "reduced", lambda: False)
    click = QPoint(300, 200)  # (where the link was clicked, in the window)
    monkeypatch.setattr(peek_module.QCursor, "pos", staticmethod(lambda: window.centralWidget().mapToGlobal(click)))
    peek.open(window.profile, url=f"{site}/article.html")
    assert peek.origin == peek.mapFrom(window.centralWidget(), click)
    assert peek.ghost is not None and not peek.card.isVisible()  # (a picture of it does the growing)
    seed = peek._seed()
    assert seed.center() == peek.origin
    start = peek_module.grown(seed, peek.card.geometry(), 0.0)
    assert start == seed and peek_module.grown(seed, peek.card.geometry(), 1.0) == peek.card.geometry()
    assert wait_until(lambda: peek.ghost is None and peek.card.isVisible() and peek.dim == 1.0, 1)
    assert peek.card.width() > 400  # (its real size, not the seed's)
    peek.close_peek()
    assert peek.ghost is not None and not peek.card.isVisible()  # (shrinking back into the link)
    assert wait_until(lambda: not peek.isVisible() and peek.view is None, 1)
    assert peek.card.isVisibleTo(peek)  # (ready for next time)


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


def _lights_state(window):
    """(hidden, how bright, x, y from the title bar's bottom) of the traffic lights: Eclipse's own (drawn over
    macOS's hidden buttons, which are what's moved, and where they are)."""
    import ctypes
    from browser import traffic_lights
    from browser.macos import _CGRect, _send
    native = _send(ctypes.c_void_p(int(window.winId())), "window")
    button = _send(native, "standardWindowButton:", 0, argtypes=(ctypes.c_long,))
    frame = _send(button, "frame", restype=_CGRect)
    overlay = window.traffic_lights.overlay
    return (not overlay.isVisible(), 1.0 if overlay.lit else traffic_lights.DIM, round(frame.x), round(frame.y))


def _lights_on_macos_buttons(window) -> bool:
    """Whether Eclipse's lights sit exactly over macOS's (hidden) buttons."""
    from PyQt6.QtCore import QPointF
    from browser import macos
    overlay = window.traffic_lights.overlay
    frames = macos.traffic_light_frames(window)
    return bool(frames) and all(
        abs((overlay.mapTo(window, centre.toPoint()) - QPointF(x + w / 2, y + h / 2).toPoint()).manhattanLength()) <= 1
        for centre, (x, y, w, h) in zip(overlay.centres, frames))


def test_the_top_row_never_runs_into_the_traffic_lights(window, site):
    from browser import macos
    if not macos._cocoa():
        pytest.skip("macOS's own window buttons")
    open_page(window, f"{site}/index.html")
    for width in (190, 215, 250, 320):  # (the narrowest sidebar squeezed the first button over them)
        window._set_sidebar_size(width)
        pause(0.3)
        lights_end, _ = macos.traffic_lights(window)
        bar = window.sidebar
        toggle = bar.toggle_btn.mapTo(window, QPoint(0, 0)).x()
        reload_end = bar.reload_btn.mapTo(window, QPoint(bar.reload_btn.width(), 0)).x()
        assert toggle >= lights_end + 8, (width, toggle, lights_end)
        assert reload_end <= bar.mapTo(window, QPoint(bar.width(), 0)).x(), width  # (and nothing falls off the end)


def test_traffic_lights_light_up_in_place(window, site, monkeypatch):
    from browser import macos, traffic_lights
    if not macos._cocoa():
        pytest.skip("macOS's own window buttons")
    open_page(window, f"{site}/index.html")
    pause(0.3)
    calls, real = [], macos.style_traffic_lights
    monkeypatch.setattr(macos, "style_traffic_lights",
                        lambda w, hidden, alpha=1.0: calls.append((hidden, alpha)) or real(w, hidden, alpha))
    home = _lights_state(window)[2:]
    assert _lights_on_macos_buttons(window) and window.traffic_lights.overlay.isVisible()
    seen = set()
    monkeypatch.setattr(window.traffic_lights, "near", lambda: True)
    window.traffic_lights._check()
    assert wait_until(lambda: seen.add(_lights_state(window)[2:]) or _lights_state(window)[1] == 1.0, 2)
    # (macOS draws its own a few points off while their brightness changes: ours light up, its stay hidden,
    # untouched)
    assert calls == [] and seen == {home} and _lights_on_macos_buttons(window)
    monkeypatch.setattr(window.traffic_lights, "near", lambda: False)
    window.traffic_lights._check()
    assert wait_until(lambda: _lights_state(window)[1] == traffic_lights.DIM, 2)


def test_traffic_lights_go_with_the_sidebar(window, site, monkeypatch):
    from browser import floating_sidebar, macos, traffic_lights
    if not macos._cocoa():
        pytest.skip("macOS's own window buttons")
    open_page(window, f"{site}/index.html")  # (a tab open: with none, the hidden sidebar would come out by itself)
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
    assert _lights_on_macos_buttons(window)
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
    assert _lights_on_macos_buttons(window)  # (ours went with them)
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


def test_pinned_tabs_get_their_icons_without_loading(window, site, site_root, monkeypatch):
    import os
    from PyQt6.QtGui import QColor, QImage
    from browser import storage
    from browser.favicons import Favicons
    from browser.models import PINNED, Tab
    from browser.sidebar import TabRow
    icon_file = os.path.join(site_root, "favicon.ico")
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




def _stand_ins(monkeypatch, answer=True):
    """Touch ID answers by itself, and the Keychain is a dict: nothing pops up, and your real one is left alone."""
    from PyQt6.QtCore import QTimer
    from browser import macos, passkey_store
    kept = {}
    monkeypatch.setattr(passkey_store.secure_store, "store", lambda a, secret: kept.__setitem__(a, secret) or True)
    monkeypatch.setattr(passkey_store.secure_store, "load", lambda a: kept.get(a))
    monkeypatch.setattr(passkey_store.secure_store, "delete", lambda a: kept.pop(a, None))
    monkeypatch.setattr(macos, "can_verify_owner", lambda: True)
    asked = []
    monkeypatch.setattr(macos, "verify_owner", lambda reason, done, interactive=True: (
        asked.append(reason), QTimer.singleShot(50, lambda: done(answer)))[0] is None)
    return asked


def _click_when_ready(card, text):
    from PyQt6.QtWidgets import QPushButton
    button = next(b for b in card.findChildren(QPushButton) if b.text() == text and not b.isHidden())
    assert wait_until(button.isEnabled, 3)  # (a moment after the card shows: never a click meant for the page)
    button.click()


ASK = """
    window.%(name)s = 'waiting';
    navigator.credentials.%(call)s.then(made => window.%(name)s = JSON.stringify({
        json: made.toJSON(), platform: made instanceof PublicKeyCredential, attachment: made.authenticatorAttachment,
        extensions: made.getClientExtensionResults(),
        key: made.response.getPublicKey ? made.response.getPublicKey().byteLength : null}),
      error => window.%(name)s = error.name);
"""
CREATE = """create({publicKey: {challenge: new Uint8Array([1, 2, 3, 4]), rp: {name: 'Test', id: '%(rp)s'},
    user: {id: new Uint8Array([7, 7, 7]), name: 'arkan@example.com', displayName: 'Arkan'},
    pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],
    authenticatorSelection: {residentKey: 'required', userVerification: 'required'},
    extensions: {credProps: true}, timeout: 60000}})"""


def test_eclipse_saves_a_passkey_and_signs_in_with_it(window, site, monkeypatch):
    from PyQt6.QtWidgets import QLabel
    import base64
    import hashlib
    import json
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from browser import passkey_store
    asked = _stand_ins(monkeypatch)
    page = site.replace("127.0.0.1", "localhost")
    tab = open_page(window, page + "/index.html?passkeys")
    # A message that looks like the passkey script's but isn't (from a frame in the page, an ad say) is ignored
    run_js(tab.view, "console.debug('__eclipse_passkey__:' + JSON.stringify({id: 1, kind: 'create', options: "
                     "{rp_id: 'localhost', user: 'forged', challenge: 'AQ'}})); 1", world=0)
    pause(0.4)
    assert window.passkeys.card_for(tab.view) is None
    # Sites ask first whether the browser has passkeys of its own
    assert run_js(tab.view, "PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(a => "
                            "window.able = a); 1", world=0) and wait_until(
        lambda: run_js(tab.view, "window.able", world=0) is True, 3)
    run_js(tab.view, ASK % {"name": "made", "call": CREATE % {"rp": "localhost"}}, world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    card = window.passkeys.card_for(tab.view)
    assert card.offer["kind"] == "create" and card.offer["rp_id"] == "localhost"
    _click_when_ready(card, "Save Passkey")
    assert wait_until(lambda: run_js(tab.view, "window.made", world=0) != "waiting", 10)
    made = json.loads(run_js(tab.view, "window.made", world=0))
    assert asked == ["save a passkey for localhost"]
    assert made["platform"] and made["attachment"] == "platform" and made["key"] > 60
    assert made["extensions"] == {"credProps": {"rk": True}}
    client = json.loads(passkey_store.unb64url(made["json"]["response"]["clientDataJSON"]))
    assert client["origin"] == page and client["type"] == "webauthn.create"
    assert client["challenge"] == passkey_store.b64url(bytes([1, 2, 3, 4]))
    public = serialization.load_der_public_key(passkey_store.unb64url(made["json"]["response"]["publicKey"]))
    # Signing in: the site lists the passkey it knows; the card offers the account
    credential = made["json"]["id"]
    run_js(tab.view, ASK % {"name": "signed", "call": f"""get({{publicKey: {{challenge: new Uint8Array([9, 9]),
        rpId: 'localhost', allowCredentials: [{{type: 'public-key', id: Uint8Array.from(atob('{
        base64.b64encode(passkey_store.unb64url(credential)).decode()}'), c => c.charCodeAt(0))}}],
        userVerification: 'preferred'}}}})"""}, world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    _click_when_ready(window.passkeys.card_for(tab.view), "arkan@example.com")
    assert wait_until(lambda: run_js(tab.view, "window.signed", world=0) != "waiting", 10)
    signed = json.loads(run_js(tab.view, "window.signed", world=0))["json"]
    assert signed["id"] == credential and signed["response"]["userHandle"] == passkey_store.b64url(bytes([7, 7, 7]))
    auth = passkey_store.unb64url(signed["response"]["authenticatorData"])
    data = passkey_store.unb64url(signed["response"]["clientDataJSON"])
    public.verify(passkey_store.unb64url(signed["response"]["signature"]), auth + hashlib.sha256(data).digest(),
                  ec.ECDSA(hashes.SHA256()))  # (what the site's server checks)
    assert auth[:32] == hashlib.sha256(b"localhost").digest() and auth[32] & passkey_store.UV
    # Saving one for the same account again: the site says which it has, and Eclipse says it's already there
    run_js(tab.view, ASK % {"name": "again", "call": (CREATE % {"rp": "localhost"}).replace(
        "timeout: 60000", "timeout: 60000, excludeCredentials: [{type: 'public-key', id: Uint8Array.from(atob('%s'), "
        "c => c.charCodeAt(0))}]" % base64.b64encode(passkey_store.unb64url(credential)).decode())}, world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    _click_when_ready(window.passkeys.card_for(tab.view), "OK")
    assert wait_until(lambda: run_js(tab.view, "window.again", world=0) == "InvalidStateError", 10)
    # Autofill: a sign-in page waiting in the background gets a quiet card, Not Now puts it away (the page keeps
    # waiting), and picking the account signs in
    assert run_js(tab.view, "PublicKeyCredential.isConditionalMediationAvailable().then(a => window.auto = a); 1",
                  world=0) and wait_until(lambda: run_js(tab.view, "window.auto", world=0) is True, 3)
    def autofill(name):
        return ASK % {"name": name, "call": "get({mediation: 'conditional', publicKey: {challenge: "
                                            "new Uint8Array([5]), rpId: 'localhost'}})"}
    run_js(tab.view, autofill("quiet"), world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    card = window.passkeys.card_for(tab.view)
    assert card.offer["conditional"] and "with your passkey?" in " ".join(l.text() for l in card.findChildren(QLabel))
    _click_when_ready(card, "Not Now")
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is None, 3)
    assert run_js(tab.view, "window.quiet", world=0) == "waiting"
    tab.view.reload()
    assert loaded(tab.view)
    run_js(tab.view, autofill("auto"), world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    _click_when_ready(window.passkeys.card_for(tab.view), "arkan@example.com")
    assert wait_until(lambda: run_js(tab.view, "window.auto", world=0) not in ("waiting", None), 10)
    assert json.loads(run_js(tab.view, "window.auto", world=0))["json"]["id"] == credential


def test_with_no_passkey_for_the_site_autofill_is_quiet_and_asking_says_so(window, site, monkeypatch):
    from PyQt6.QtWidgets import QLabel
    _stand_ins(monkeypatch)
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/index.html?none")
    run_js(tab.view, ASK % {"name": "auto", "call": "get({mediation: 'conditional', publicKey: {challenge: "
                                                    "new Uint8Array([5])}})"}, world=0)
    pause(1)
    assert window.passkeys.card_for(tab.view) is None  # (as a sign-in page on load should be left alone)
    tab.view.reload()
    assert loaded(tab.view)
    run_js(tab.view, ASK % {"name": "asked", "call": "get({publicKey: {challenge: new Uint8Array([5])}})"}, world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    card = window.passkeys.card_for(tab.view)
    assert "don't have a passkey for localhost in Eclipse yet" in " ".join(l.text() for l in card.findChildren(QLabel))
    _click_when_ready(card, "Cancel")
    assert wait_until(lambda: run_js(tab.view, "window.asked", world=0) == "NotAllowedError", 10)


def test_a_page_cant_ask_for_another_sites_passkey(window, site, monkeypatch):
    _stand_ins(monkeypatch)
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/index.html?other-site")
    run_js(tab.view, ASK % {"name": "made", "call": CREATE % {"rp": "example.com"}}, world=0)
    assert wait_until(lambda: run_js(tab.view, "window.made", world=0) == "SecurityError", 10)
    assert window.passkeys.card_for(tab.view) is None


def test_touch_id_saying_no_saves_nothing(window, site, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    from browser import passkey_store
    _stand_ins(monkeypatch, answer=False)
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/index.html?no")
    run_js(tab.view, ASK % {"name": "made", "call": CREATE % {"rp": "localhost"}}, world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10)
    card = window.passkeys.card_for(tab.view)
    _click_when_ready(card, "Save Passkey")
    assert wait_until(lambda: any(b.text() == "Save Passkey" for b in card.findChildren(QPushButton)
                                  if not b.isHidden()), 3)  # (back to the question)
    assert passkey_store.all_passkeys() == [] and run_js(tab.view, "window.made", world=0) == "waiting"
    _click_when_ready(card, "Cancel")
    assert wait_until(lambda: run_js(tab.view, "window.made", world=0) == "NotAllowedError", 10)


def test_a_passkey_request_says_to_plug_in_a_key_and_cancel_reaches_the_page(window, site, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QPushButton
    _stand_ins(monkeypatch)
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/index.html?webauthn")  # (a name, not an IP)
    ask = """
        window.%(name)s = 'waiting';
        navigator.credentials.%(call)s.then(() => window.%(name)s = 'done', error => window.%(name)s = error.name);
    """
    # Passkey autofill waits quietly for you to pick one: no card for that
    run_js(tab.view, ask % {"name": "autofill", "call": "get({mediation: 'conditional', "
                                                          "publicKey: {challenge: new Uint8Array(32)}})"}, world=0)
    pause(1)
    assert window.passkeys.card_for(tab.view) is None
    tab.view.reload()  # (a site stops autofill before asking outright; a fresh page does too)
    assert loaded(tab.view)
    run_js(tab.view, ask % {"name": "passkey", "call": """create({publicKey: {
            challenge: new Uint8Array(32), rp: {name: 'Test', id: 'localhost'},
            user: {id: new Uint8Array(8), name: 'arkan@example.com', displayName: 'Arkan'},
            pubKeyCredParams: [{type: 'public-key', alg: -7}], timeout: 60000}})"""}, world=0)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 10), "no card for the request"
    _click_when_ready(window.passkeys.card_for(tab.view), "Use a Security Key")  # (Eclipse offers its own first)
    assert wait_until(lambda: window.passkeys.card_for(tab.view) is not None, 3)
    card = window.passkeys.card_for(tab.view)
    assert wait_until(lambda: card.isVisible(), 3)
    assert "localhost</b> wants to use a passkey or security key" in " ".join(
        label.text() for label in card.findChildren(QLabel))
    next(b for b in card.findChildren(QPushButton) if b.text() == "Cancel").click()
    assert wait_until(lambda: run_js(tab.view, "window.passkey", world=0) == "NotAllowedError", 10)
    assert window.passkeys.card_for(tab.view) is None


def test_a_password_is_offered_to_save_then_filled_after_touch_id(window, site, monkeypatch):
    from PyQt6.QtWidgets import QLabel
    from browser import password_store
    from browser.webview import PASSWORD_CALL
    asked = _stand_ins(monkeypatch)
    login = site.replace("127.0.0.1", "localhost") + "/login.html"
    tab = open_page(window, login)
    run_js(tab.view, "document.getElementById('user').value = 'arkan@example.com';"
                     "document.getElementById('pass').value = 'correct horse';"
                     "document.querySelector('button').click(); 1", world=0)
    assert wait_until(lambda: tab.view.url().toString().endswith("welcome.html"), 5)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 5), "no offer to save"
    card = window.passwords.card_for(tab.view)
    assert "Save your password for <b>localhost</b>?" in " ".join(l.text() for l in card.findChildren(QLabel))
    _click_when_ready(card, "Save")
    assert [(e["origin"], e["username"], e["password"]) for e in password_store.logins()] == [
        (site.replace("127.0.0.1", "localhost"), "arkan@example.com", "correct horse")]
    # Back to sign in: the card offers the account, and Touch ID comes first
    tab.view.load(QUrl(login))
    assert loaded(tab.view)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 5), "no offer to fill"
    _click_when_ready(window.passwords.card_for(tab.view), "arkan@example.com")
    assert wait_until(lambda: run_js(tab.view, "document.getElementById('pass').value", world=0) == "correct horse", 5)
    assert run_js(tab.view, "document.getElementById('user').value", world=0) == "arkan@example.com"
    assert asked == ["fill your password for localhost"]
    # Again: once each time Eclipse opens, so no second ask
    tab.view.reload()
    assert loaded(tab.view)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 5)
    _click_when_ready(window.passwords.card_for(tab.view), "arkan@example.com")
    assert wait_until(lambda: run_js(tab.view, "document.getElementById('pass').value", world=0) == "correct horse", 5)
    assert len(asked) == 1
    # The same password typed again isn't offered for saving again
    run_js(tab.view, "document.querySelector('button').click(); 1", world=0)
    assert wait_until(lambda: tab.view.url().toString().endswith("welcome.html"), 5)
    pause(0.5)
    assert window.passwords.card_for(tab.view) is None


def test_a_page_cant_pretend_to_be_the_password_script(window, site, monkeypatch):
    import json
    from browser.webview import PASSWORD_CALL
    _stand_ins(monkeypatch)
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/index.html?forged")
    forged = PASSWORD_CALL + json.dumps({"token": "guess", "kind": "sent", "username": "x", "password": "y"})
    run_js(tab.view, f"console.debug({json.dumps(forged)}); 1", world=0)
    pause(0.8)
    assert window.passwords.card_for(tab.view) is None



def test_a_two_step_sign_in_saves_the_username_with_the_password(window, site, monkeypatch):
    from browser import password_store
    asked = _stand_ins(monkeypatch)
    page = site.replace("127.0.0.1", "localhost")
    tab = open_page(window, page + "/login-step1.html")
    run_js(tab.view, "document.getElementById('identifier').value = 'arkan@example.com';"
                     "document.querySelector('button').click(); 1", world=0)
    assert wait_until(lambda: tab.view.url().toString().endswith("login-step2.html"), 5)
    assert loaded(tab.view)
    run_js(tab.view, "document.getElementById('pass').value = 'two steps';"
                     "document.querySelector('button').click(); 1", world=0)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 5), "no offer to save"
    _click_when_ready(window.passwords.card_for(tab.view), "Save")
    assert [(e["username"], e["password"]) for e in password_store.logins()] == [("arkan@example.com", "two steps")]
    # Signing in again: the first page gets the username (nothing secret: no Touch ID), the second the password
    tab.view.load(QUrl(page + "/login-step1.html"))
    assert loaded(tab.view)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 5), "no offer on the first page"
    _click_when_ready(window.passwords.card_for(tab.view), "arkan@example.com")
    assert wait_until(lambda: run_js(tab.view, "document.getElementById('identifier').value", world=0)
                      == "arkan@example.com", 5)
    assert asked == []
    run_js(tab.view, "document.querySelector('button').click(); 1", world=0)
    assert wait_until(lambda: tab.view.url().toString().endswith("login-step2.html"), 5) and loaded(tab.view)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 5), "no offer on the second page"
    _click_when_ready(window.passwords.card_for(tab.view), "arkan@example.com")
    assert wait_until(lambda: run_js(tab.view, "document.getElementById('pass').value", world=0) == "two steps", 5)
    assert asked == ["fill your password for localhost"]


def _in_frame(view, host, code):
    """Run code in the page's frame from host (in the page's own world), and wait for the answer."""
    from browser.passwords import _frames
    frame = next((f for f in _frames(view.page().mainFrame()) if f.url().host() == host), None)
    if frame is None:
        return None
    answer = []
    frame.runJavaScript(code, 0, answer.append)
    wait_until(lambda: answer, 5)
    return answer[0] if answer else None


def test_a_sign_in_frame_of_the_same_site_is_filled_and_another_sites_never_is(window, site, monkeypatch):
    from PyQt6.QtWidgets import QLabel
    from browser import password_store
    asked = _stand_ins(monkeypatch)
    port = QUrl(site).port()
    accounts, other = f"http://accounts.example.localhost:{port}", f"http://other.localhost:{port}"
    password_store.add(accounts, "arkan@example.com", "in a frame")
    password_store.add(other, "arkan@example.com", "not for frames")
    # The site's own sign-in frame (accounts. inside shop.): offered, and filled in the frame alone
    tab = open_page(window, f"http://shop.example.localhost:{port}/frames.html?inner={accounts}/login.html")
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 10), "no offer for the frame"
    card = window.passwords.card_for(tab.view)
    assert "accounts.example.localhost" in " ".join(l.text() for l in card.findChildren(QLabel))
    _click_when_ready(card, "arkan@example.com")
    assert wait_until(lambda: _in_frame(tab.view, "accounts.example.localhost",
                                        "document.getElementById('pass').value") == "in a frame", 5)
    assert asked == ["fill your password for accounts.example.localhost"]
    # Another site's frame on the same page: never offered, though there's a password saved for it...
    tab = open_page(window, f"http://shop.example.localhost:{port}/frames.html?inner={other}/login.html")
    assert wait_until(lambda: _in_frame(tab.view, "other.localhost", "!!document.getElementById('pass')"), 10)
    pause(1)
    assert window.passwords.card_for(tab.view) is None
    # ...though it is on that site's own page
    tab = open_page(window, f"{other}/login.html")
    assert loaded(tab.view)
    assert wait_until(lambda: window.passwords.card_for(tab.view) is not None, 10)


def test_a_verification_code_is_filled_after_touch_id(window, site, monkeypatch):
    from browser import password_store
    asked = _stand_ins(monkeypatch)
    page = site.replace("127.0.0.1", "localhost")
    saved = password_store.add(page, "arkan@example.com", "with codes")
    password_store.update(saved["id"], otp=password_store.otp_link("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"))
    now = lambda: {password_store.otp_code(saved_otp, time.time() + d)[0] for d in (-30, 0)}  # noqa: E731
    saved_otp = password_store.logins()[0]["otp"]
    for page_name, read in (("verify.html", "document.getElementById('code').value"),
                            ("verify-boxes.html", "[...document.querySelectorAll('input')].map(i => i.value).join('')")):
        tab = open_page(window, f"{page}/{page_name}")
        assert wait_until(lambda: type(window.passwords.card_for(tab.view)).__name__ == "CodeCard", 5), page_name
        _click_when_ready(window.passwords.card_for(tab.view), "arkan@example.com")
        assert wait_until(lambda: run_js(tab.view, read, world=0) in now(), 5), page_name
    assert asked == ["fill your verification code for localhost"]  # (once each time Eclipse opens)


def test_a_code_field_heard_before_the_page_has_its_address_still_gets_its_card(window, site, monkeypatch):
    # The page's script can speak before Qt has told the view where it is (it did, now and then, on fast pages):
    # with no address, no login matched and the card never came, as the script says each thing only once
    import json
    from browser import password_store
    from browser.webview import PASSWORD_CALL
    _stand_ins(monkeypatch)
    page = site.replace("127.0.0.1", "localhost")
    saved = password_store.add(page, "arkan@example.com", "with codes")
    password_store.update(saved["id"], otp=password_store.otp_link("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"))
    tab = open_page(window, "about:blank")
    view = tab.view
    monkeypatch.setattr(view, "url", lambda: QUrl())  # (not told yet)
    window.passwords._said(view, PASSWORD_CALL + json.dumps({"token": window.passwords.token, "kind": "form",
                                                             "step": "code"}))
    pause(0.2)
    assert window.passwords.card_for(view) is None
    monkeypatch.setattr(view, "url", lambda: QUrl(page + "/verify.html"))  # (and now it is)
    assert wait_until(lambda: type(window.passwords.card_for(view)).__name__ == "CodeCard", 3)
    window.passwords.close(window.passwords.card_for(view))
    window.close_tab(tab)


def test_signing_up_gets_a_strong_password_thats_saved(window, site, monkeypatch):
    import re
    from browser import password_store
    _stand_ins(monkeypatch)
    tab = open_page(window, site.replace("127.0.0.1", "localhost") + "/signup.html")
    run_js(tab.view, "document.getElementById('user').value = 'new@example.com';"
                     "document.getElementById('new').dispatchEvent(new PointerEvent('pointerdown', "
                     "{bubbles: true})); 1", world=0)  # (pressing in the password field)
    assert wait_until(lambda: type(window.passwords.card_for(tab.view)).__name__ == "StrongCard", 5)
    made = window.passwords.card_for(tab.view).password
    assert re.fullmatch(r"[a-zA-Z2-9]{6}-[a-zA-Z2-9]{6}-[a-zA-Z2-9]{6}", made)
    _click_when_ready(window.passwords.card_for(tab.view), "Use Strong Password")
    both = "[document.getElementById('new').value, document.getElementById('again').value]"
    assert wait_until(lambda: run_js(tab.view, both, world=0) == [made, made], 5)
    run_js(tab.view, "document.querySelector('button').click(); 1", world=0)
    assert wait_until(lambda: [(e["username"], e["password"]) for e in password_store.logins()]
                      == [("new@example.com", made)], 5)  # (saved without asking: Eclipse made it)


def test_a_page_cant_open_windows_by_itself(window, site):
    before = len(window._extra_windows)
    tab = open_page(window, site + "/popup.html")
    pause(1.0)
    assert len(window._extra_windows) == before  # (no Little windows: neither at load nor from a timer)
    assert run_js(tab.view, "window.opened.length === 2 && window.opened.every(w => w === null)", world=0)
    window.close_tab(tab)


def test_the_traffic_lights_do_what_macos_buttons_do(window, site, monkeypatch):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication
    from browser import macos
    if not macos._cocoa():
        pytest.skip("macOS's own window buttons")
    open_page(window, f"{site}/index.html")
    pause(0.3)
    overlay, done = window.traffic_lights.overlay, []
    monkeypatch.setattr(macos, "window_button_action", lambda w, action: done.append(action) or True)

    def click(i, alt=False):
        at = overlay.centres[i]
        mods = Qt.KeyboardModifier.AltModifier if alt else Qt.KeyboardModifier.NoModifier
        for kind in (QMouseEvent.Type.MouseButtonPress, QMouseEvent.Type.MouseButtonRelease):
            QApplication.sendEvent(overlay, QMouseEvent(kind, at, overlay.mapToGlobal(at), Qt.MouseButton.LeftButton,
                                                        Qt.MouseButton.LeftButton, mods))
    click(0)
    click(1)
    click(2)
    click(2, alt=True)
    assert done == ["close", "minimize", "full_screen", "zoom"]
    # Between the circles: nothing
    gap = (overlay.centres[0] + overlay.centres[1]) / 2 + QPointF(0, overlay.diameter)
    assert overlay.which(gap) is None
    # macOS's own stay hidden while ours show; the window's close still goes through macOS (as from its red button)
    import ctypes
    from browser.macos import _send
    native = _send(ctypes.c_void_p(int(window.winId())), "window")
    for i in range(3):
        assert _send(_send(native, "standardWindowButton:", i, argtypes=(ctypes.c_long,)), "isHidden",
                     restype=ctypes.c_bool)


def test_the_traffic_lights_light_up_only_close_to_them(window, site, monkeypatch):
    from browser import macos, traffic_lights
    if not macos._cocoa():
        pytest.skip("macOS's own window buttons")
    open_page(window, f"{site}/index.html")
    pause(0.3)
    lights = window.traffic_lights
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    x, y, w, h = macos.traffic_light_frames(window)[1]  # (the middle one)
    pointer = {}
    monkeypatch.setattr(traffic_lights.QCursor, "pos", staticmethod(lambda: window.mapToGlobal(pointer["at"])))
    for at, lit in ((QPoint(round(x + w / 2), round(y + h / 2)), True),           # on it
                    (QPoint(round(x + w / 2), round(y + h + traffic_lights.REACH - 1)), True),  # just below
                    (QPoint(round(x + w / 2), round(y + h + traffic_lights.REACH + 4)), False),
                    (window.sidebar.url_pill.mapTo(window, QPoint(20, 4)), False)):  # (the search bar: not yet)
        pointer["at"] = at
        assert lights.near() is lit, (at, lit)
