# Small bits of native macOS window behaviour Qt doesn't expose, done through the
# Objective-C runtime with ctypes (no extra dependency like PyObjC needed).

import ctypes
import ctypes.util
import sys

NS_WINDOW_TITLE_HIDDEN = 1
APP_BUNDLE_ID = "com.masonkimball.pythonbrowser"  # must match tools/build_app.py

_objc = None
if sys.platform == "darwin":
    _objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    _objc.sel_registerName.restype = ctypes.c_void_p
    _objc.sel_registerName.argtypes = [ctypes.c_char_p]
    _objc.objc_getClass.restype = ctypes.c_void_p
    _objc.objc_getClass.argtypes = [ctypes.c_char_p]


def _send(receiver, selector, *args, restype=ctypes.c_void_p, argtypes=()):
    # objc_msgSend must be called with the exact argument types (it's variadic on arm64)
    send = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", _objc))
    return send(receiver, _objc.sel_registerName(selector.encode()), *args)


def _cocoa() -> bool:
    """Windows are real NSViews only on Qt's Cocoa platform (not offscreen, say): anything that asks
    a window's NSView for something must check this first, or it crashes."""
    if _objc is None:
        return False
    from PyQt6.QtGui import QGuiApplication
    return QGuiApplication.platformName() == "cocoa"


def hide_title_text(widget):
    """Keep the window title for Mission Control and the Window menu, but don't draw it.

    With the title bar made transparent (Arc-style), macOS would otherwise paint the
    page title across the top of the sidebar.
    """
    if not _cocoa():
        return
    view = ctypes.c_void_p(int(widget.winId()))  # the NSView Qt created for the window
    window = _send(view, "window")
    if window:
        _send(window, "setTitleVisibility:", NS_WINDOW_TITLE_HIDDEN, restype=None, argtypes=(ctypes.c_long,))


# --- Default browser (LaunchServices) ------------------------------------------------------

_K_UTF8 = 0x08000100
_cf = _ls = None
if sys.platform == "darwin":
    _cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    _ls = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreServices"))
    _cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    _cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    _cf.CFStringGetCString.restype = ctypes.c_bool
    _cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    _cf.CFBundleGetMainBundle.restype = ctypes.c_void_p
    _cf.CFBundleGetIdentifier.restype = ctypes.c_void_p
    _cf.CFBundleGetIdentifier.argtypes = [ctypes.c_void_p]
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _ls.LSSetDefaultHandlerForURLScheme.restype = ctypes.c_int32
    _ls.LSSetDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _ls.LSCopyDefaultHandlerForURLScheme.restype = ctypes.c_void_p
    _ls.LSCopyDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p]


def _cfstr(text):
    return _cf.CFStringCreateWithCString(None, text.encode(), _K_UTF8)


def _pystr(cfstring):
    if not cfstring:
        return None
    buffer = ctypes.create_string_buffer(512)
    return buffer.value.decode() if _cf.CFStringGetCString(cfstring, buffer, 512, _K_UTF8) else None


def bundle_identifier() -> str | None:
    """The app's bundle ID when running as the built app; None when run from a terminal."""
    if _cf is None:
        return None
    return _pystr(_cf.CFBundleGetIdentifier(_cf.CFBundleGetMainBundle()))


def is_built_app() -> bool:
    """Running as the built Halo.app (not from a terminal, where the process is Python's)."""
    return bundle_identifier() == APP_BUNDLE_ID


def default_browser() -> str | None:
    if _ls is None:
        return None
    scheme = _cfstr("https")
    handler = _ls.LSCopyDefaultHandlerForURLScheme(scheme)
    _cf.CFRelease(scheme)
    name = _pystr(handler)
    if handler:
        _cf.CFRelease(handler)
    return name


def set_default_browser(bundle_id) -> bool:
    """Ask macOS to make this the default browser. macOS shows its own confirmation dialog."""
    if _ls is None:
        return False
    ok = True
    for scheme in ("http", "https"):
        s, b = _cfstr(scheme), _cfstr(bundle_id)
        ok = _ls.LSSetDefaultHandlerForURLScheme(s, b) == 0 and ok
        _cf.CFRelease(s)
        _cf.CFRelease(b)
    return ok


def name_in_menu_bar(name):
    """Run from a terminal, macOS would call the app "Python" in the menu bar: it reads the name
    from the process's Info.plist. Call before the QApplication exists. (The built app has its own.)"""
    if _objc is None or is_built_app():
        return
    bundle = _send(_objc.objc_getClass(b"NSBundle"), "mainBundle")
    info = _send(bundle, "infoDictionary") if bundle else None
    if not info or not _send(info, "respondsToSelector:", _objc.sel_registerName(b"setObject:forKey:"),
                             restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,)):
        return  # (only a dictionary that can be changed; anything else is left alone)
    nsstring = _objc.objc_getClass(b"NSString")
    value = _send(nsstring, "stringWithUTF8String:", name.encode(), argtypes=(ctypes.c_char_p,))
    key = _send(nsstring, "stringWithUTF8String:", b"CFBundleName", argtypes=(ctypes.c_char_p,))
    _send(info, "setObject:forKey:", value, key, restype=None, argtypes=(ctypes.c_void_p, ctypes.c_void_p))


# --- Traffic lights -------------------------------------------------------------------------

class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


def traffic_lights(widget) -> tuple[float, float] | None:
    """(right edge, vertical centre) of the window's traffic lights, in points from the window's
    top-left, so our own buttons can line up with them. None off macOS or before the window exists."""
    if not _cocoa():
        return None
    window = _send(ctypes.c_void_p(int(widget.window().winId())), "window")
    zoom = _send(window, "standardWindowButton:", 2, argtypes=(ctypes.c_long,)) if window else None
    if not zoom:
        return None
    bounds = _send(zoom, "bounds", restype=_CGRect)
    frame = _send(zoom, "convertRect:toView:", bounds, None, restype=_CGRect, argtypes=(_CGRect, ctypes.c_void_p))
    height = widget.window().height()
    return frame.x + frame.w, height - (frame.y + frame.h / 2)  # Cocoa measures y from the bottom


# --- Dock badge --------------------------------------------------------------------------------

_badge = None


def set_dock_badge(text: str):
    """The number on the Dock icon (unread messages across your sites); "" removes it."""
    global _badge
    if _objc is None or text == _badge:
        return
    _badge = text
    app = _send(_objc.objc_getClass(b"NSApplication"), "sharedApplication")
    tile = _send(app, "dockTile") if app else None
    if not tile:
        return
    label = _cfstr(text) if text else None  # a CFString is an NSString (toll-free bridged)
    _send(tile, "setBadgeLabel:", label, restype=None, argtypes=(ctypes.c_void_p,))
    _send(tile, "display", restype=None)
    if label:
        _cf.CFRelease(label)


# --- Busy: keep App Nap away while pages load ----------------------------------------------------
#
# macOS naps apps whose windows are hidden behind others, and Qt WebEngine's pages then load at a
# crawl (measured: GitHub took twice as long, or stalled). While anything is loading (a tab, a Live
# Folder reading a page) the app tells macOS it's doing work you asked for; once nothing is, it
# lets go, so an idle browser still naps and saves battery. It never stops the Mac sleeping.

_NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SLEEP = 0x00FFFFFF
_busy = {"count": 0, "token": None}


def busy(start: bool):
    """Count one piece of loading work in (start=True) or out."""
    if _objc is None:
        return
    _busy["count"] = max(0, _busy["count"] + (1 if start else -1))
    if _busy["count"] and _busy["token"] is None:
        info = _send(_objc.objc_getClass(b"NSProcessInfo"), "processInfo")
        reason = _cfstr("Loading pages")
        token = _send(info, "beginActivityWithOptions:reason:",
                      ctypes.c_uint64(_NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SLEEP), reason,
                      argtypes=(ctypes.c_uint64, ctypes.c_void_p))
        _cf.CFRelease(reason)
        if token:
            _send(token, "retain")
            _busy["token"] = token
    elif not _busy["count"] and _busy["token"] is not None:
        info = _send(_objc.objc_getClass(b"NSProcessInfo"), "processInfo")
        _send(info, "endActivity:", _busy["token"], restype=None, argtypes=(ctypes.c_void_p,))
        _send(_busy["token"], "release", restype=None)
        _busy["token"] = None
