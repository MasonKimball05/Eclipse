# Eclipse's Windows side of its own links and of Low Power Mode, tested anywhere: the registry is a stand-in,
# and "Windows" is sys.platform pretending.

import sys
from pathlib import Path

from browser import power_saver, windows_system


class FakeRegistry:
    """Enough of winreg: keys under HKEY_CURRENT_USER, with what's been written to them."""
    HKEY_CURRENT_USER, REG_SZ = "HKCU", 1

    def __init__(self):
        self.keys, self.writes = {}, 0

    class _Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def OpenKey(self, root, path):
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self.keys[(root, path)])

    def CreateKey(self, root, path):
        return self._Key(self.keys.setdefault((root, path), {}))

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, _reserved, _kind, value):
        key.values[name] = value
        self.writes += 1


def test_windows_is_told_eclipse_opens_its_own_links_once():
    registry = FakeRegistry()
    command = r'"C:\Python\pythonw.exe" "C:\Eclipse\main.py" "%1"'
    assert windows_system.register_url_scheme("eclipse-browser", "Eclipse", command, registry)
    root = r"Software\Classes\eclipse-browser"
    assert registry.keys[("HKCU", root)] == {"": "URL:Eclipse", "URL Protocol": ""}
    assert registry.keys[("HKCU", root + r"\shell\open\command")] == {"": command}
    writes = registry.writes
    assert windows_system.register_url_scheme("eclipse-browser", "Eclipse", command, registry)
    assert registry.writes == writes  # (already this copy: nothing written again)
    moved = command.replace(r"C:\Eclipse", r"D:\Eclipse")
    windows_system.register_url_scheme("eclipse-browser", "Eclipse", moved, registry)
    assert registry.keys[("HKCU", root + r"\shell\open\command")] == {"": moved}  # (Eclipse moved: updated)


def test_windows_opens_links_with_this_copy_without_a_console(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_text("")
    monkeypatch.setattr(sys, "executable", str(python))
    assert windows_system.launch_command(Path(r"C:\Eclipse\main.py")) == f'"{python}" "C:\\Eclipse\\main.py" "%1"'
    (tmp_path / "pythonw.exe").write_text("")
    assert windows_system.launch_command(Path("main.py")).startswith(f'"{tmp_path / "pythonw.exe"}"')


def test_off_windows_nothing_is_written_and_battery_saver_is_never_on(monkeypatch):
    if sys.platform == "win32":
        return
    assert not windows_system.register_url_scheme("eclipse-browser", "Eclipse")
    assert not windows_system.battery_saver_on()


def test_low_power_mode_follows_battery_saver_on_windows(monkeypatch):
    from browser import macos
    monkeypatch.setattr(macos, "low_power", lambda: True)
    monkeypatch.setattr(windows_system, "battery_saver_on", lambda: False)
    monkeypatch.setattr(power_saver.sys, "platform", "win32")
    assert not power_saver.system_saving()  # (Windows: only Battery saver counts)
    monkeypatch.setattr(windows_system, "battery_saver_on", lambda: True)
    assert power_saver.system_saving()
    monkeypatch.setattr(power_saver.sys, "platform", "darwin")
    monkeypatch.setattr(windows_system, "battery_saver_on", lambda: False)
    assert power_saver.system_saving()


def test_eclipses_own_links_on_the_command_line_go_to_eclipse_not_a_little_window():
    from browser.app import LinkReceiver, _links
    assert _links(["--fresh", "https://a.example/", "eclipse-browser://recipe?name=Morning", "C:\\folder"]) == \
        ["https://a.example/", "eclipse-browser://recipe?name=Morning"]

    class Window:
        features, opened, toasts = {}, [], []
        open_external = lambda self, link: self.opened.append(link)
        bring_to_front = lambda self: None
        toast = lambda self, text: self.toasts.append(text)

    class Feature:
        def __init__(self):
            self.got = []

        def app_link(self, url):
            self.got.append(url.toString())
            return True
    window, feature = Window(), Feature()
    window.features = {"recipes": feature}
    receiver = LinkReceiver.__new__(LinkReceiver)  # (no server: only its link routing)
    receiver.window = window
    receiver.open_link("eclipse-browser://recipe?name=Morning")
    receiver.open_link("https://a.example/")
    assert feature.got == ["eclipse-browser://recipe?name=Morning"] and window.opened == ["https://a.example/"]
    window.features = {}
    receiver.open_link("eclipse-browser://nothing")
    assert window.toasts and "doesn't know" in window.toasts[-1]
