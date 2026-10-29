# Eclipse on Windows: what reads or works differently there. These pretend to be Windows (sys.platform), as CI's
# Windows job runs them for real.

import sys

import pytest

from browser import shortcuts


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


# --- Shortcuts: what each key is on Windows --------------------------------------------------------------------

def test_mac_control_key_shortcuts_become_ctrl_or_alt_on_windows(windows):
    assert shortcuts.here("Meta+Tab") == "Ctrl+Tab"  # (Windows+Tab is Task View: Eclipse would never see it)
    assert shortcuts.here("Meta+Shift+Tab") == "Ctrl+Shift+Tab"
    assert shortcuts.here("Meta+3") == "Alt+3"  # (the Spaces: Ctrl+3 is the third tab, as in every browser)
    assert shortcuts.here("Ctrl+Y") == "Ctrl+H"  # (Library: Ctrl+Y is Redo on Windows, pages included)
    assert shortcuts.here("Ctrl+Shift+T") == "Ctrl+Shift+T" and shortcuts.here("") == ""


def test_shortcuts_are_left_alone_on_a_mac(mac):
    for keys in ("Meta+Tab", "Meta+3", "Ctrl+Y", "Ctrl+Meta+F"):
        assert shortcuts.here(keys) == keys


def test_text_names_the_keys_as_they_are_on_windows(windows):
    t = shortcuts.for_here
    assert t("Reopen it with ⇧⌘T") == "Reopen it with Ctrl+Shift+T"
    assert t("Downloads (⌥⌘L)") == "Downloads (Ctrl+Alt+L)"
    assert t("Back (⌘[)") == "Back (Ctrl+[)" and t("Forward (⌘])") == "Forward (Ctrl+])"
    assert t("Library (⌘Y).") == "Library (Ctrl+H)."  # (the key it really is there)
    assert t("Work  (⌃3)") == "Work  (Alt+3)"
    assert t("⌃Tab switches between") == "Ctrl+Tab switches between"
    assert t("Hold ⌃, tap Tab") == "Hold Ctrl, tap Tab" and t("Tap ⇧ on a link") == "Tap Shift on a link"
    assert t("⌘↩") == "Ctrl+Enter" and t("⌘F, a question, ↩") == "Ctrl+F, a question, Enter"
    assert t("⌘B  ⌘I  ⌘U") == "Ctrl+B  Ctrl+I  Ctrl+U"
    assert t("⌘-click tabs to pick several") == "Ctrl+click tabs to pick several"
    assert t("Settings (⌘,)") == "Settings (Ctrl+,)"
    assert t("Zoom out (⌘-)") == "Zoom out (Ctrl+-)"


def test_text_says_what_windows_calls_things(windows):
    t = shortcuts.for_here
    assert t("Show in Finder") == "Show in File Explorer"
    assert t("kept in the Keychain") == "kept in Credential Manager" and t("Keychain") == "Credential Manager"
    assert t("Kept on this Mac, readable only by your account.") == "Kept on this PC, readable only by your account."


def test_text_is_left_alone_on_a_mac(mac):
    for text in ("Reopen it with ⇧⌘T", "Show in Finder", "kept in the Keychain", "Hold ⌃, tap Tab"):
        assert shortcuts.for_here(text) == text


def test_mac_keys_that_differ_stay_different_on_windows(windows):
    """On a Mac ⌘3 and ⌃3 are two keys (a tab, a Space); were both Ctrl+3 on Windows, Qt would run neither."""
    for n in range(1, 10):
        assert shortcuts.here(f"Ctrl+{n}") != shortcuts.here(f"Meta+{n}"), n
    assert shortcuts.here(shortcuts.here("Meta+3")) == "Alt+3"  # (already Windows': left as it is)


def test_tooltips_are_shown_with_windows_keys(qapp, windows):
    from PyQt6.QtCore import QEvent, QPoint
    from PyQt6.QtGui import QHelpEvent
    from PyQt6.QtWidgets import QPushButton, QToolTip
    shown = []
    real = QToolTip.showText
    QToolTip.showText = staticmethod(lambda pos, text, widget=None, *rest: shown.append(text))
    try:
        button = QPushButton()
        button.setToolTip("Reload (⌘R)")
        tips = shortcuts.NativeTooltips()
        event = QHelpEvent(QEvent.Type.ToolTip, QPoint(1, 1), QPoint(1, 1))
        assert tips.eventFilter(button, event) and shown == ["Reload (Ctrl+R)"]
        button.setToolTip("Plain words")
        assert not tips.eventFilter(button, event)  # (nothing to change: Qt shows it itself)
    finally:
        QToolTip.showText = real


# --- An extension's suggested keys -------------------------------------------------------------------------------

def test_an_extensions_shortcut_is_the_one_it_suggests_for_windows(windows):
    from browser.ext.chrome import ChromeExtension
    ext = ChromeExtension("x", None, {"name": "X", "version": "1", "commands": {
        "_execute_action": {"suggested_key": {"default": "Ctrl+Shift+Y", "mac": "MacCtrl+Shift+Y"}},
        "other": {"suggested_key": {"windows": "Alt+Shift+O", "mac": "Command+Shift+O", "default": "Ctrl+Shift+O"},
                  "description": "Other"}}})
    assert {name: keys for name, keys, _ in ext.commands} == {"_execute_action": "Ctrl+Shift+Y", "other": "Alt+Shift+O"}


def test_an_extensions_mac_shortcut_is_kept_on_a_mac(mac):
    from browser.ext.chrome import ChromeExtension
    ext = ChromeExtension("x", None, {"name": "X", "version": "1", "commands": {
        "_execute_action": {"suggested_key": {"default": "Ctrl+Shift+Y", "mac": "MacCtrl+Shift+Y"}}}})
    assert ext.commands[0][1] == "Meta+Shift+Y"


# --- Updating: starting again afterwards ---------------------------------------------------------------------------

def test_relaunching_after_an_update_doesnt_need_a_unix_shell_on_windows(windows):
    from browser.self_update import relaunch_after
    command = relaunch_after(4242, [r"C:\Users\Ana Lee\AppData\Local\Eclipse Build\venv\Scripts\python.exe",
                                    r"C:\Code\It's Eclipse\main.py"])
    assert command[0].lower().endswith("powershell.exe") and "/bin/sh" not in command
    script = command[-1]
    assert "Wait-Process -Id 4242" in script and "Start-Process" in script
    assert r"'C:\Users\Ana Lee\AppData\Local\Eclipse Build\venv\Scripts\python.exe'" in script
    assert "It''s Eclipse" in script  # (a quote in the path can't end PowerShell's string early)
    assert '\\"C:\\Code\\It\'\'s Eclipse\\main.py\\"' not in script and '"C:\\Code\\It\'\'s Eclipse\\main.py"' in script


def test_relaunching_on_a_mac_waits_with_the_shell(mac):
    from browser.self_update import relaunch_after
    command = relaunch_after(4242, ["/usr/bin/python3", "/Users/me/Eclipse/main.py"])
    assert command[:2] == ["/bin/sh", "-c"] and command[3:] == ["4242", "/usr/bin/python3", "/Users/me/Eclipse/main.py"]


# --- Fonts ---------------------------------------------------------------------------------------------------------

def test_code_and_passwords_are_set_in_a_monospaced_font_without_menlo(qapp):
    from PyQt6.QtGui import QFontInfo
    from PyQt6.QtWidgets import QLabel
    from browser.theme import MONO_CSS, mono_font
    assert QFontInfo(mono_font(12)).fixedPitch()  # (Menlo is a Mac's: Consolas on Windows, or the system's own)
    label = QLabel("x")
    label.setStyleSheet(f"font-family: {MONO_CSS};")
    label.ensurePolished()
    assert QFontInfo(label.font()).fixedPitch()
    assert "Consolas" in MONO_CSS and MONO_CSS.startswith("Menlo")


# --- File names --------------------------------------------------------------------------------------------------

def test_names_windows_keeps_for_devices_are_never_file_names(tmp_path):
    from browser.models import Space
    from browser.page_actions import not_reserved, safe_filename
    from browser.space_share import write
    from browser.tidy_downloads import new_path
    assert safe_filename("Con") == "Con_" and safe_filename("nul.tar") == "nul_.tar" and safe_filename("COM1") == "COM1_"
    assert safe_filename("Console") == "Console" and safe_filename("Aux Cables") == "Aux Cables"
    assert not_reserved("LPT9") == "LPT9_" and not_reserved("prn ") == "prn _"  # (Windows ignores the trailing space too)
    assert write(Space("Aux", "#3366ff", ""), tmp_path).name == "Aux_.eclipse-space"
    download = tmp_path / "scan_0001.pdf"
    assert new_path(download, "CON") == tmp_path / "CON_.pdf"


def test_install_hints_name_this_systems_installer(monkeypatch):
    from browser.downloads import install_hint
    monkeypatch.setattr(sys, "platform", "win32")
    assert install_hint("yt-dlp", "ffmpeg") == "winget install yt-dlp.yt-dlp Gyan.FFmpeg"
    assert install_hint("gh") == "winget install GitHub.cli"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert install_hint("yt-dlp", "ffmpeg") == "brew install yt-dlp ffmpeg" and install_hint("gh") == "brew install gh"


def test_windows_animation_setting_is_reduce_motion(monkeypatch):
    import ctypes
    from browser import motion

    class User32:
        def __init__(self, animations):
            self.animations = animations

        def SystemParametersInfoW(self, action, _param, out, _flags):
            assert action == motion.SPI_GETCLIENTAREAANIMATION
            ctypes.cast(out, ctypes.POINTER(ctypes.c_int)).contents.value = int(self.animations)
            return 1
    monkeypatch.setattr(sys, "platform", "win32")
    for animations, reduced in ((False, True), (True, False)):
        monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": User32(animations)})(), raising=False)
        assert motion._reduce_motion() is reduced
    monkeypatch.setattr(ctypes, "windll", None, raising=False)  # (not there to ask: animate, as before)
    assert motion._reduce_motion() is False
