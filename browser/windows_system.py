# Windows' side of two Mac things: Eclipse's own links (eclipse-browser://…, scripting.py), which Windows only
# opens with an app that's said it handles them, and Battery saver, Windows' Low Power Mode (power_saver.py).
#
# Links: on each start Eclipse tells Windows (in your own part of the registry, HKEY_CURRENT_USER: no admin
# needed, nothing for other accounts) to open eclipse-browser:// links with this copy of it, so the Run box
# (Win+R), a shortcut, PowerToys or AutoHotkey can open a page in a Space or run a recipe. It's only written when
# it's changed (Eclipse moved, say).

import sys
from pathlib import Path

SYSTEM_STATUS_BATTERY_SAVER = 1  # SYSTEM_POWER_STATUS.SystemStatusFlag: Battery saver (Energy saver) is on


def launch_command(main_py: Path | None = None) -> str:
    """How Windows should start this copy of Eclipse with a link: pythonw (no console window) if it's there."""
    python = Path(sys.executable)
    quiet = python.with_name("pythonw.exe")
    python = quiet if python.name.lower() == "python.exe" and quiet.exists() else python
    main_py = main_py or Path(__file__).resolve().parent.parent / "main.py"
    return f'"{python}" "{main_py}" "%1"'


def register_url_scheme(scheme: str, name: str, command: str | None = None, registry=None) -> bool:
    """Have Windows open scheme:// links with Eclipse. True if it's set (or already was). `registry`: winreg, or a
    stand-in for tests."""
    if registry is None:
        if sys.platform != "win32":
            return False
        import winreg as registry
    command = command or launch_command()
    key_path = rf"Software\Classes\{scheme}"
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, key_path + r"\shell\open\command") as key:
            if registry.QueryValueEx(key, "")[0] == command:
                return True  # (already this copy)
    except OSError:
        pass
    try:
        with registry.CreateKey(registry.HKEY_CURRENT_USER, key_path) as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, f"URL:{name}")
            registry.SetValueEx(key, "URL Protocol", 0, registry.REG_SZ, "")
        with registry.CreateKey(registry.HKEY_CURRENT_USER, key_path + r"\shell\open\command") as key:
            registry.SetValueEx(key, "", 0, registry.REG_SZ, command)
        return True
    except OSError:
        return False


def battery_saver_on() -> bool:
    """Whether Windows' Battery saver (Energy saver on Windows 11) is on."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    status = _SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return False
    return status.SystemStatusFlag == SYSTEM_STATUS_BATTERY_SAVER
