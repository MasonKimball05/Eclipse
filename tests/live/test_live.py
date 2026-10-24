# The browser's main features, end to end, in the real window. Run with:  ./run.sh --live-tests

import functools
import http.server
import os
import shutil
import signal
import threading

import pytest

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QMouseEvent, QWheelEvent
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QApplication

from live_helpers import loaded, open_page, pause, run_js, wait_until


def _rows(window):
    bar = window.command_bar
    return [bar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(bar.list.count())]


def _swipe(view, dx, at=QPoint(600, 450)):
    target = view.focusProxy()
    phases = [(0, Qt.ScrollPhase.ScrollBegin)] + [(dx / 12, Qt.ScrollPhase.ScrollUpdate)] * 12
    for step, phase in phases:
        QApplication.sendEvent(target, QWheelEvent(QPointF(at), QPointF(target.mapToGlobal(at)), QPoint(int(step), 0),
                                                   QPoint(int(step) * 4, 0), Qt.MouseButton.NoButton,
                                                   Qt.KeyboardModifier.NoModifier, phase, False))
        pause(0.016)
    pause(0.3)
    QApplication.sendEvent(target, QWheelEvent(QPointF(at), QPointF(target.mapToGlobal(at)), QPoint(0, 0),
                                               QPoint(0, 0), Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollEnd, False))


def test_swipe_goes_back_and_forward_but_not_over_a_scroller(window, site):
    tab = open_page(window, f"{site}/index.html")
    tab.view.load(QUrl(f"{site}/carousel.html"))
    assert loaded(tab.view)
    run_js(tab.view, "document.getElementById('c').scrollLeft = 500")
    _swipe(tab.view, 300, QPoint(200, 150))  # over the carousel, which can still scroll left
    pause(1)
    assert tab.view.url().fileName() == "carousel.html"
    _swipe(tab.view, 300)  # on the page itself
    assert wait_until(lambda: tab.view.url().fileName() == "index.html", 5)
    _swipe(tab.view, -300)
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
