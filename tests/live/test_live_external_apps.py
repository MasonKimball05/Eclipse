# Links that open other apps, end to end through macOS: a tiny helper app (built here, background-only,
# so it never takes the screen) handles "eclipse-linktest:" links by writing each one it gets to a
# file. That shows what really reached macOS: nothing a page did by itself, only what you opened.

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from live_helpers import open_page, pause, run_js, wait_until

SCHEME = "eclipse-linktest"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/"
              "Support/lsregister")
# (somewhere macOS will take a link handler from: not a temporary folder)
HELPERS = Path.home() / "Library" / "Application Support" / "Eclipse Build" / "test-helpers"


@pytest.fixture()
def helper(tmp_path):
    """The helper app, registered for eclipse-linktest: links; yields the file it writes them to."""
    if not shutil.which("osacompile") or not Path(LSREGISTER).exists():
        pytest.skip("needs macOS's osacompile and lsregister")
    received = tmp_path / "opened.txt"
    built = tmp_path / "EclipseLinkTest.app"
    subprocess.run(["osacompile", "-o", str(built), "-e", "on open location theURL",
                    "-e", f"do shell script \"echo \" & quoted form of theURL & \" >> '{received}'\"",
                    "-e", "end open location"], check=True, capture_output=True)
    plist = built / "Contents" / "Info.plist"
    for command in ("Add :CFBundleIdentifier string local.eclipse.linktest", "Add :LSBackgroundOnly bool true",
                    "Add :CFBundleURLTypes array", "Add :CFBundleURLTypes:0 dict",
                    "Add :CFBundleURLTypes:0:CFBundleURLName string EclipseLinkTest",
                    "Add :CFBundleURLTypes:0:CFBundleURLSchemes array",
                    f"Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string {SCHEME}"):
        subprocess.run(["/usr/libexec/PlistBuddy", "-c", command, str(plist)], check=True, capture_output=True)
    subprocess.run(["codesign", "-f", "-s", "-", str(built)], check=True, capture_output=True)
    HELPERS.mkdir(parents=True, exist_ok=True)
    app = HELPERS / "EclipseLinkTest.app"
    shutil.rmtree(app, ignore_errors=True)
    shutil.copytree(built, app)
    subprocess.run([LSREGISTER, "-f", str(app)], check=True)
    time.sleep(1)  # (Launch Services takes a moment)
    try:
        yield received
    finally:
        subprocess.run([LSREGISTER, "-u", str(app)], check=False)
        shutil.rmtree(app, ignore_errors=True)


def _links(received: Path) -> list[str]:
    return received.read_text().split() if received.exists() else []


def _card(window):
    from browser.external_links import ExternalPrompt
    return next((p for p in window.findChildren(ExternalPrompt) if p.isVisible()), None)


def _click(button):
    """A real click: press and release on the button, as the mouse does."""
    QTest.mouseClick(button, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(button.width() // 2, button.height() // 2))


def test_open_reaches_the_app_and_nothing_else_does(window, site, helper):
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from browser import macos
    assert macos.app_for_url(f"{SCHEME}://x") == "EclipseLinkTest"
    tab = open_page(window, f"{site}/index.html?apps")

    # The page tries by itself: refused. macOS gets nothing while the card waits for you
    run_js(tab.view, f"location.href = '{SCHEME}://by-itself'; true")
    assert wait_until(lambda: _card(window) is not None, 3)
    card = _card(window)
    assert card.text.text() == "127.0.0.1 wants to open EclipseLinkTest"
    # Clicked straight away, before the card's ready (a click meant for something else): nothing
    assert not card.open_button.isEnabled()
    _click(card.open_button)
    _click(card.always_button)
    pause(2.5)
    assert _links(helper) == [] and _card(window) is card

    # Clicked once it's ready: the app gets exactly that link, once, and the card goes
    assert wait_until(lambda: card.open_button.isEnabled(), 2)
    _click(card.open_button)
    assert wait_until(lambda: _links(helper) == [f"{SCHEME}://by-itself"], 10), _links(helper)
    assert _card(window) is None
    assert tab.view.url().scheme() == "http"  # the page itself stayed where it was

    # Cancel: nothing
    run_js(tab.view, f"location.href = '{SCHEME}://cancelled'; true")
    assert wait_until(lambda: _card(window) is not None, 3)
    card = _card(window)
    assert wait_until(lambda: card.cancel_button.isEnabled(), 2)
    _click(card.cancel_button)
    pause(2)
    assert _links(helper) == [f"{SCHEME}://by-itself"] and _card(window) is None

    # And asking again straight away is ignored for a moment, so a page can't keep the card coming back
    run_js(tab.view, f"location.href = '{SCHEME}://nagging'; true")
    pause(1)
    assert _card(window) is None
    from browser.external_links import QUIET_AFTER_CANCEL
    pause(QUIET_AFTER_CANCEL)

    # Always Allow: opens it, and this site's eclipse-linktest: links open without asking from then on
    run_js(tab.view, f"location.href = '{SCHEME}://always-1'; true")
    assert wait_until(lambda: _card(window) is not None, 3)
    card = _card(window)
    assert card.always_button.isVisible()
    assert wait_until(lambda: card.always_button.isEnabled(), 2)
    _click(card.always_button)
    assert wait_until(lambda: f"{SCHEME}://always-1" in _links(helper), 10)
    run_js(tab.view, f"location.href = '{SCHEME}://always-2'; true")
    assert wait_until(lambda: f"{SCHEME}://always-2" in _links(helper), 20)  # (macOS can be slow handing it over)
    assert _card(window) is None  # (no card that time)

    # A page you can't see (like an extension's background) opens nothing, ever
    hidden = QWebEnginePage(window.profile)
    hidden.setHtml("<p>hidden</p>")
    pause(0.5)
    hidden.runJavaScript(f"location.href = '{SCHEME}://from-a-hidden-page'")
    pause(3)
    assert f"{SCHEME}://from-a-hidden-page" not in _links(helper)
    hidden.deleteLater()

    # Nor does a tab in the background (and it doesn't get to ask)
    background = open_page(window, f"{site}/index.html?background")
    window.activate_tab(tab)
    pause(0.3)
    run_js(background.view, f"location.href = '{SCHEME}://from-the-background'; true")
    pause(3)
    assert f"{SCHEME}://from-the-background" not in _links(helper) and _card(window) is None
    window.store.settings.pop("external_apps", None)  # (the test's Always Allow, forgotten)
