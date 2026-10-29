# Small bits of native macOS window behaviour Qt doesn't expose, done through the
# Objective-C runtime with ctypes (no extra dependency like PyObjC needed).

import ctypes
import ctypes.util
import sys

NS_WINDOW_TITLE_HIDDEN = 1
APP_BUNDLE_ID = "com.masonkimball.eclipse"  # must match tools/build_app.py
DEV_BUNDLE_ID = APP_BUNDLE_ID + ".dev"  # (Eclipse Dev, the copy that runs from the checkout: ./run.sh --build-app)

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
    """Running as the built Eclipse.app or Eclipse Dev.app (not from a terminal, where the process is Python's)."""
    return bundle_identifier() in (APP_BUNDLE_ID, DEV_BUNDLE_ID)


def is_ours(bundle_id) -> bool:
    """Eclipse, or Eclipse Dev."""
    return (bundle_id or "").lower() in (APP_BUNDLE_ID, DEV_BUNDLE_ID)


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


def activate_app():
    """Become the active app, so the keyboard comes here (⌘T and the rest). Qt asks the old way,
    which macOS 14 and later ignore: a window that took a moment to appear (Python starting up)
    could open without the app becoming active, and its first shortcut went to the app behind."""
    if not _cocoa():
        return
    app = _send(_objc.objc_getClass(b"NSApplication"), "sharedApplication")
    if not app:
        return
    if _send(app, "respondsToSelector:", _objc.sel_registerName(b"activate"), restype=ctypes.c_bool,
             argtypes=(ctypes.c_void_p,)):
        _send(app, "activate", restype=None)  # (macOS 14+: the launching app hands over to us)
    else:
        _send(app, "activateIgnoringOtherApps:", True, restype=None, argtypes=(ctypes.c_bool,))


# --- Other apps' links -------------------------------------------------------------------------------

def app_for_url(url: str) -> str | None:
    """The name of the app macOS would open `url` with ("zoom.us" for zoommtg:…, "Mail" for mailto:…),
    or None when no app on this Mac handles it."""
    if _objc is None:
        return None
    text = _cfstr(url)
    link = _send(_objc.objc_getClass(b"NSURL"), "URLWithString:", text, argtypes=(ctypes.c_void_p,))
    _cf.CFRelease(text)
    if not link:
        return None
    workspace = _send(_objc.objc_getClass(b"NSWorkspace"), "sharedWorkspace")
    app = _send(workspace, "URLForApplicationToOpenURL:", link, argtypes=(ctypes.c_void_p,)) if workspace else None
    if not app:
        return None
    path = _pystr(_send(app, "path"))
    if not path:
        return None
    manager = _send(_objc.objc_getClass(b"NSFileManager"), "defaultManager")
    shown = _pystr(_send(manager, "displayNameAtPath:", _send(app, "path"), argtypes=(ctypes.c_void_p,)))
    name = shown or path.rstrip("/").rsplit("/", 1)[-1]
    return name.removesuffix(".app")


# --- Quarantine ------------------------------------------------------------------------------------

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True) if sys.platform == "darwin" else None


def quarantine(path: str) -> bool:
    """Mark a downloaded file the way Safari and Chrome do, so that opening it (an app, a script, an
    installer in a .zip) goes through Gatekeeper's check first. Qt WebEngine doesn't, and nor does yt-dlp."""
    if _libc is None or not path:
        return False
    import time
    value = f"0081;{int(time.time()):08x};Eclipse;".encode()
    _libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
                               ctypes.c_uint32, ctypes.c_int]
    return _libc.setxattr(path.encode(), b"com.apple.quarantine", value, len(value), 0, 0) == 0


# --- Traffic lights -------------------------------------------------------------------------

class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


class _NSPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


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


def traffic_light_frames(widget) -> list[tuple[float, float, float, float]]:
    """Where the window's three buttons are (close, minimise, zoom): (x, y, width, height) in points from the
    window's top-left, hidden or not. [] off macOS or before the window exists."""
    if not _cocoa():
        return []
    window = _send(ctypes.c_void_p(int(widget.window().winId())), "window")
    height, frames = widget.window().height(), []
    for i in range(3):
        button = _send(window, "standardWindowButton:", i, argtypes=(ctypes.c_long,)) if window else None
        if not button:
            return []
        bounds = _send(button, "bounds", restype=_CGRect)
        f = _send(button, "convertRect:toView:", bounds, None, restype=_CGRect, argtypes=(_CGRect, ctypes.c_void_p))
        frames.append((f.x, height - f.y - f.h, f.w, f.h))  # (Cocoa measures y from the bottom)
    return frames


_WINDOW_ACTIONS = {"close": "performClose:", "minimize": "performMiniaturize:", "zoom": "performZoom:",
                   "full_screen": "toggleFullScreen:"}


def window_button_action(widget, action: str) -> bool:
    """Do what one of the window's buttons does, as macOS does it (the close as if its red button were clicked,
    so a window that keeps running on close hides). action: close, minimize, zoom or full_screen."""
    if not _cocoa():
        return False
    window = _send(ctypes.c_void_p(int(widget.window().winId())), "window")
    if not window:
        return False
    _send(window, _WINDOW_ACTIONS[action], None, restype=None, argtypes=(ctypes.c_void_p,))
    return True


_lights_home: dict[int, list[tuple[float, float]]] = {}   # per window: where macOS put each light
_lights_shift: dict[int, tuple[float, float]] = {}         # and how far we've moved them (Cocoa's way up)


def shift_traffic_lights(widget, dx: float, dy: float) -> bool:
    """Move the window's traffic lights `dx` right and `dy` down from where macOS puts them (0, 0 puts
    them back), e.g. to sit inside the hidden sidebar when it slides out, 6pt in from the corner.

    macOS lays them out again now and then (a resize, full screen): calling this again afterwards
    puts them right, telling its own layout from ours by where they are."""
    if not _cocoa():
        return False
    window = _send(ctypes.c_void_p(int(widget.window().winId())), "window")
    buttons = [_send(window, "standardWindowButton:", i, argtypes=(ctypes.c_long,)) for i in range(3)] if window else []
    if not all(buttons):
        return False
    key = int(window)
    now = [(f.x, f.y) for f in (_send(b, "frame", restype=_CGRect) for b in buttons)]
    flipped = _send(_send(buttons[0], "superview"), "isFlipped", restype=ctypes.c_bool)
    move = (dx, dy if flipped else -dy)  # (Cocoa measures y from the bottom, unless the view's flipped)
    home, (mx, my) = _lights_home.get(key), _lights_shift.get(key, (0.0, 0.0))
    if move == (0, 0) and (mx, my) == (0, 0):
        return True  # (where macOS keeps them, and it hasn't been told otherwise: leave its layout be)
    ours = home is not None and all(abs(x - (hx + mx)) < 0.5 and abs(y - (hy + my)) < 0.5
                                    for (x, y), (hx, hy) in zip(now, home))
    if not ours:  # (where macOS has them now is home: it's laid them out since, or it's the first time)
        home = _lights_home[key] = now
    for button, (hx, hy) in zip(buttons, home):
        _send(button, "setFrameOrigin:", _NSPoint(hx + move[0], hy + move[1]), restype=None, argtypes=(_NSPoint,))
    _lights_shift[key] = move
    return True


def style_traffic_lights(widget, hidden: bool, alpha: float = 1.0) -> bool:
    """Show or hide the window's traffic lights, at `alpha` (dim until you point at them, like Arc). Never faded:
    mid-fade, macOS draws them out of place (traffic_lights.py)."""
    if not _cocoa():
        return False
    window = _send(ctypes.c_void_p(int(widget.window().winId())), "window")
    if not window:
        return False
    for i in range(3):
        button = _send(window, "standardWindowButton:", i, argtypes=(ctypes.c_long,))
        if not button:
            continue
        # Only when it changes: telling a showing button to show has macOS lay the title bar out again, which
        # nudged the buttons under the pointer every time they lit up as it came near
        if _send(button, "isHidden", restype=ctypes.c_bool) != hidden:
            _send(button, "setHidden:", hidden, restype=None, argtypes=(ctypes.c_bool,))
        _send(button, "setAlphaValue:", alpha, restype=None, argtypes=(ctypes.c_double,))
    return True


# --- Dock badge --------------------------------------------------------------------------------

_badge = None
_badges = {"unread": "", "downloads": ""}  # (what each wants shown: unread messages win, then downloads)


def dock_badge_text() -> str:
    return _badges["unread"] or _badges["downloads"]


def set_dock_badge(text: str, source="unread"):
    """The Dock icon's badge: unread messages across your sites (source "unread", a number or "•"), or else the
    downloads still coming (source "downloads", "↓2"). "" removes that source's."""
    global _badge
    _badges[source] = text
    text = dock_badge_text()
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


# --- Handoff -------------------------------------------------------------------------------------------
#
# The page you're on, offered to your iPhone and iPad (and other Macs) signed in to the same Apple Account: it
# shows in their app switcher or Dock, and opens there in their browser. It's the activity macOS has for web
# browsing, so no setup is needed (only Handoff turned on, in System Settings → General → AirDrop & Handoff).

_activity = None  # (the activity being offered: kept, or macOS stops offering it)


def handoff(url: str, title: str = "") -> bool:
    """Offer this page to your other devices (in place of whatever was offered before)."""
    global _activity
    if not _cocoa() or not url.startswith(("https://", "http://")):
        end_handoff()
        return False
    text = _cfstr(url)
    link = _send(_objc.objc_getClass(b"NSURL"), "URLWithString:", text, argtypes=(ctypes.c_void_p,))
    _cf.CFRelease(text)
    if not link:
        end_handoff()
        return False
    if _activity is None:
        kind = _cfstr("NSUserActivityTypeBrowsingWeb")
        made = _send(_objc.objc_getClass(b"NSUserActivity"), "alloc")
        _activity = _send(made, "initWithActivityType:", kind, argtypes=(ctypes.c_void_p,)) if made else None
        _cf.CFRelease(kind)
        if not _activity:
            _activity = None
            return False
    _send(_activity, "setWebpageURL:", link, restype=None, argtypes=(ctypes.c_void_p,))
    name = _cfstr(title[:200]) if title else None
    _send(_activity, "setTitle:", name, restype=None, argtypes=(ctypes.c_void_p,))
    if name:
        _cf.CFRelease(name)
    _send(_activity, "becomeCurrent", restype=None)
    return True


def end_handoff():
    """Stop offering a page (a private window's in front, the window's closed, nothing's open)."""
    global _activity
    if _activity is not None:
        _send(_activity, "invalidate", restype=None)
        _send(_activity, "release", restype=None)
        _activity = None


# --- Share menu --------------------------------------------------------------------------------

_NS_MIN_Y_EDGE, _NS_MAX_Y_EDGE = 1, 3
_picker = None  # the share menu on screen: it has to outlive the call that shows it


def share_picker(url: str):
    """An NSSharingServicePicker for a link (AirDrop, Messages, Mail, Notes, Reminders…), or None."""
    if _objc is None:
        return None
    text = _cfstr(url)
    link = _send(_objc.objc_getClass(b"NSURL"), "URLWithString:", text, argtypes=(ctypes.c_void_p,))
    _cf.CFRelease(text)
    if not link:
        return None  # (not a URL macOS accepts)
    items = _send(_objc.objc_getClass(b"NSArray"), "arrayWithObject:", link, argtypes=(ctypes.c_void_p,))
    picker = _send(_objc.objc_getClass(b"NSSharingServicePicker"), "alloc")
    return _send(picker, "initWithItems:", items, argtypes=(ctypes.c_void_p,)) if picker else None


def share(widget, url: str, anchor) -> bool:
    """Show macOS's share menu for `url` under `anchor` (a QRect in `widget`'s window)."""
    global _picker
    if not _cocoa():
        return False
    picker = share_picker(url)
    if not picker:
        return False
    if _picker:
        _send(_picker, "release", restype=None)
    _picker = picker
    view = ctypes.c_void_p(int(widget.window().winId()))
    flipped = _send(view, "isFlipped", restype=ctypes.c_bool)
    y = anchor.y() if flipped else widget.window().height() - anchor.y() - anchor.height()
    rect = _CGRect(anchor.x(), y, anchor.width(), anchor.height())
    edge = _NS_MAX_Y_EDGE if flipped else _NS_MIN_Y_EDGE  # below the anchor, either way up
    _send(picker, "showRelativeToRect:ofView:preferredEdge:", rect, view, edge, restype=None,
          argtypes=(_CGRect, ctypes.c_void_p, ctypes.c_ulong))
    return True


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


# --- Touch ID, or the Mac's password ---------------------------------------------------------------
# LocalAuthentication answers through an Objective-C block: built here by hand (a "global" block: a struct
# whose function ctypes provides), since ctypes has no blocks of its own.

class _BlockDescriptor(ctypes.Structure):
    _fields_ = [("reserved", ctypes.c_ulong), ("size", ctypes.c_ulong)]


_REPLY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p)


class _Block(ctypes.Structure):
    _fields_ = [("isa", ctypes.c_void_p), ("flags", ctypes.c_int), ("reserved", ctypes.c_int),
                ("invoke", _REPLY), ("descriptor", ctypes.POINTER(_BlockDescriptor))]


_OWNER_POLICY = 2  # LAPolicyDeviceOwnerAuthentication: Touch ID, a paired Watch, or the Mac's password
_asked = []  # (each question's context and block, kept alive: macOS answers on a thread of its own, later)


def _auth_context():
    if _objc is None:
        return None
    try:
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/LocalAuthentication.framework/LocalAuthentication")
    except OSError:
        return None
    cls = _objc.objc_getClass(b"LAContext")
    return _send(_send(cls, "alloc"), "init") if cls else None


def can_verify_owner() -> bool:
    """Whether this Mac can check it's you (Touch ID or its password): asks nothing of you."""
    context = _auth_context()
    if not context:
        return False
    able = _send(context, "canEvaluatePolicy:error:", _OWNER_POLICY, None, restype=ctypes.c_bool,
                 argtypes=(ctypes.c_long, ctypes.c_void_p))
    _send(context, "release")
    return bool(able)


def verify_owner(reason: str, done, interactive=True) -> bool:
    """Ask macOS to check it's you: Touch ID, or the Mac's password ("Eclipse is trying to <reason>").
    done(ok) is called later, on a thread of macOS's: the caller takes it back to Qt's. False if it can't ask.
    (interactive=False: macOS may not show anything, so it answers no at once: for tests.)"""
    context = _auth_context()
    if not context or not reason:  # (an empty reason is an exception in macOS, not an answer)
        return False
    if not interactive:
        _send(context, "setInteractionNotAllowed:", True, restype=None, argtypes=(ctypes.c_bool,))
    isa = ctypes.addressof(ctypes.c_void_p.in_dll(ctypes.CDLL("/usr/lib/libSystem.B.dylib"), "_NSConcreteGlobalBlock"))
    reply = _REPLY(lambda _block, ok, _error: done(bool(ok)))
    descriptor = _BlockDescriptor(0, ctypes.sizeof(_Block))
    block = _Block(isa, 1 << 28, 0, reply, ctypes.pointer(descriptor))  # (1 << 28: BLOCK_IS_GLOBAL)
    _asked.append((context, reply, descriptor, block))
    _send(context, "evaluatePolicy:localizedReason:reply:", _OWNER_POLICY, _cfstr(reason), ctypes.byref(block),
          restype=None, argtypes=(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p))
    return True


# --- Objective-C classes of our own ------------------------------------------------------------------
# A few things macOS only tells an object with a method of its own (an Apple event's handler): the class is
# made at run time, its methods Python functions ctypes turns into C ones (kept alive, in _methods).

_methods = []


def _runtime():
    for name, restype, argtypes in (
            ("objc_allocateClassPair", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]),
            ("objc_registerClassPair", None, [ctypes.c_void_p]),
            ("class_addMethod", ctypes.c_bool, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]),
            ("class_getInstanceMethod", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
            ("method_setImplementation", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
            ("object_getClass", ctypes.c_void_p, [ctypes.c_void_p])):
        function = getattr(_objc, name)
        function.restype, function.argtypes = restype, argtypes


def _make_class(name: str, methods: dict):
    """A new NSObject subclass (or the one made already, by that name): methods {selector: (CFUNCTYPE'd function,
    type encoding)}."""
    _runtime()
    existing = _objc.objc_getClass(name.encode())
    if existing:
        return existing
    cls = _objc.objc_allocateClassPair(_objc.objc_getClass(b"NSObject"), name.encode(), 0)
    for selector, (function, types) in methods.items():
        _methods.append(function)
        _objc.class_addMethod(cls, _objc.sel_registerName(selector.encode()), ctypes.cast(function, ctypes.c_void_p),
                              types.encode())
    _objc.objc_registerClassPair(cls)
    return cls


def _text(nsstring) -> str | None:
    """An NSString as Python's (any length: _pystr stops at 512 bytes)."""
    if not nsstring:
        return None
    raw = _send(nsstring, "UTF8String", restype=ctypes.c_char_p)
    return raw.decode(errors="replace") if raw is not None else None


# --- Apple events: AppleScript, and Shortcuts' "Run AppleScript" ----------------------------------------------
# The commands' words are in the app's scripting dictionary (Eclipse.sdef, written by browser/scripting.py); a
# script using them sends Eclipse an Apple event, answered here. macOS asks you first, the first time each app
# (Script Editor, Shortcuts, Raycast…) wants to control Eclipse.

_AE_DIRECT = "----"
_AE_ERROR_NUMBER, _AE_ERROR_TEXT = "errn", "errs"
_NULL_TYPE = "null"
_events = {"handler": None, "object": None}


def four(code: str) -> int:
    """A four-character code ('Eclp') as macOS's number for it."""
    return int.from_bytes(code.encode("mac-roman"), "big")


def _descriptor(value):
    """A Python value as an Apple event descriptor: text, a true/false, a whole number, a list, or a record
    (a dict keyed by four-character codes). None: AppleScript's missing value."""
    cls = _objc.objc_getClass(b"NSAppleEventDescriptor")
    if value is None:
        return _send(cls, "nullDescriptor")
    if isinstance(value, bool):
        return _send(cls, "descriptorWithBoolean:", value, argtypes=(ctypes.c_ubyte,))
    if isinstance(value, int):
        return _send(cls, "descriptorWithInt32:", value, argtypes=(ctypes.c_int32,))
    if isinstance(value, (list, tuple)):
        made = _send(cls, "listDescriptor")
        for item in value:
            _send(made, "insertDescriptor:atIndex:", _descriptor(item), 0, restype=None,
                  argtypes=(ctypes.c_void_p, ctypes.c_long))  # (0: at the end)
        return made
    if isinstance(value, dict):
        made = _send(cls, "recordDescriptor")
        for code, item in value.items():
            _send(made, "setDescriptor:forKeyword:", _descriptor(item), four(code), restype=None,
                  argtypes=(ctypes.c_void_p, ctypes.c_uint32))
        return made
    text = _cfstr(str(value))
    made = _send(cls, "descriptorWithString:", text, argtypes=(ctypes.c_void_p,))
    _cf.CFRelease(text)
    return made


def _param(event, code: str) -> str | None:
    """A parameter of an event (its direct one: "----") as text, or None if it hasn't one."""
    found = _send(event, "paramDescriptorForKeyword:", four(code), argtypes=(ctypes.c_uint32,))
    return _text(_send(found, "stringValue")) if found else None


class ScriptError(Exception):
    """What a command says when it can't do what it was asked (AppleScript shows it as the error)."""


_EVENT_METHOD = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def _handle_event(_self, _selector, event, reply):
    handler = _events["handler"]
    command = _send(event, "eventID", restype=ctypes.c_uint32).to_bytes(4, "big").decode("mac-roman")
    answer, error = None, None
    try:
        answer = handler(command, lambda code: _param(event, code))
    except ScriptError as problem:
        error = str(problem)
    except Exception:  # (a command must never take the browser down)
        import traceback
        traceback.print_exc()
        error = "Eclipse couldn't do that."
    kind = _send(reply, "descriptorType", restype=ctypes.c_uint32) if reply else 0
    if not reply or kind == four(_NULL_TYPE):
        return  # (the script didn't wait for an answer)
    put = lambda value, code: _send(reply, "setParamDescriptor:forKeyword:", _descriptor(value), four(code),
                                    restype=None, argtypes=(ctypes.c_void_p, ctypes.c_uint32))
    if error is not None:
        put(-10000, _AE_ERROR_NUMBER)  # (errAEEventFailed)
        put(error, _AE_ERROR_TEXT)
    elif answer is not None:
        put(answer, _AE_DIRECT)


def listen_for_commands(suite: str, commands, handler) -> bool:
    """Answer the Apple events of `suite` (a four-character code) with ids in `commands`: handler(command id,
    param) returns the answer (see _descriptor), or raises ScriptError. param(code) is one of the event's
    parameters as text ("----": its direct one), or None."""
    if _objc is None:
        return False
    _events["handler"] = handler
    if _events["object"] is None:
        cls = _make_class("EclipseScriptCommands",
                          {"handleEvent:withReplyEvent:": (_EVENT_METHOD(_handle_event), "v@:@@")})
        _events["object"] = _send(_send(cls, "alloc"), "init")
    manager = _send(_objc.objc_getClass(b"NSAppleEventManager"), "sharedAppleEventManager")
    selector = _objc.sel_registerName(b"handleEvent:withReplyEvent:")
    for command in commands:
        _send(manager, "setEventHandler:andSelector:forEventClass:andEventID:", _events["object"], selector,
              four(suite), four(command), restype=None,
              argtypes=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32))
    return True


def ask_self(suite: str, command: str, params: dict | None = None):
    """Send this app one of its own commands, as a script would, and return the answer as Python (text, lists
    and records of text: a record as a dict by code), or raise ScriptError with its error. For tests."""
    cls = _objc.objc_getClass(b"NSAppleEventDescriptor")
    event = _send(cls, "appleEventWithEventClass:eventID:targetDescriptor:returnID:transactionID:",
                  four(suite), four(command), _send(cls, "currentProcessDescriptor"), -1, 0,
                  argtypes=(ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int16, ctypes.c_int32))
    for code, value in (params or {}).items():
        _send(event, "setParamDescriptor:forKeyword:", _descriptor(value), four(code), restype=None,
              argtypes=(ctypes.c_void_p, ctypes.c_uint32))
    reply = _send(event, "sendEventWithOptions:timeout:error:", 3, ctypes.c_double(10), None,
                  argtypes=(ctypes.c_ulong, ctypes.c_double, ctypes.c_void_p))  # (3: wait for the answer)
    if not reply:
        raise ScriptError("no answer")
    if _send(reply, "paramDescriptorForKeyword:", four(_AE_ERROR_NUMBER), argtypes=(ctypes.c_uint32,)):
        raise ScriptError(_param(reply, _AE_ERROR_TEXT) or "")
    found = _send(reply, "paramDescriptorForKeyword:", four(_AE_DIRECT), argtypes=(ctypes.c_uint32,))
    return _python(found) if found else None


def _python(descriptor):
    kind = _send(descriptor, "descriptorType", restype=ctypes.c_uint32).to_bytes(4, "big").decode("mac-roman")
    count = lambda: _send(descriptor, "numberOfItems", restype=ctypes.c_long)
    if kind == "list":
        return [_python(_send(descriptor, "descriptorAtIndex:", i, argtypes=(ctypes.c_long,)))
                for i in range(1, count() + 1)]
    if kind == "reco":
        keys = [_send(descriptor, "keywordForDescriptorAtIndex:", i, restype=ctypes.c_uint32, argtypes=(ctypes.c_long,))
                for i in range(1, count() + 1)]
        return {k.to_bytes(4, "big").decode("mac-roman"): _python(_send(descriptor, "descriptorForKeyword:", k,
                                                                          argtypes=(ctypes.c_uint32,))) for k in keys}
    if kind == _NULL_TYPE:
        return None
    if kind in ("true", "fals", "bool"):
        return _send(descriptor, "booleanValue", restype=ctypes.c_ubyte) != 0
    if kind == "long":
        return _send(descriptor, "int32Value", restype=ctypes.c_int32)
    return _text(_send(descriptor, "stringValue"))


# --- Spotlight ---------------------------------------------------------------------------------------
# Your pinned tabs and Spaces, found from Spotlight (⌘Space). Picking one opens Eclipse at it: macOS hands the
# app delegate (Qt's) an activity naming the item; we take ours before Qt sees the rest.

_SPOTLIGHT_ACTIVITY = "com.apple.corespotlightitem"
_SPOTLIGHT_ID_KEY = "kCSSearchableItemActivityIdentifier"
_spotlight = {"index": None, "opened": None, "pending": [], "qt": None}
_CONTINUE = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p)


def _spotlight_index():
    if _spotlight["index"] is None and _objc is not None:
        try:
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreSpotlight.framework/CoreSpotlight")
        except OSError:
            return None
        cls = _objc.objc_getClass(b"CSSearchableIndex")
        if cls and _send(cls, "isIndexingAvailable", restype=ctypes.c_bool):
            _spotlight["index"] = _send(cls, "defaultSearchableIndex")
    return _spotlight["index"]


def _strings(texts):
    made = _send(_objc.objc_getClass(b"NSMutableArray"), "array")
    for text in texts:
        string = _cfstr(text)
        _send(made, "addObject:", string, restype=None, argtypes=(ctypes.c_void_p,))
        _cf.CFRelease(string)
    return made


def spotlight_add(items) -> bool:
    """Put items in Spotlight (or update them, by id): each (id, title, description, keywords)."""
    index = _spotlight_index()
    if not index:
        return False
    made = _send(_objc.objc_getClass(b"NSMutableArray"), "array")
    attribute_set, item_class = _objc.objc_getClass(b"CSSearchableItemAttributeSet"), _objc.objc_getClass(b"CSSearchableItem")
    for item_id, title, description, keywords in items:
        kind = _cfstr("public.item")
        attributes = _send(_send(attribute_set, "alloc"), "initWithItemContentType:", kind, argtypes=(ctypes.c_void_p,))
        _cf.CFRelease(kind)
        for setter, value in (("setTitle:", title), ("setDisplayName:", title), ("setContentDescription:", description)):
            text = _cfstr(value)
            _send(attributes, setter, text, restype=None, argtypes=(ctypes.c_void_p,))
            _cf.CFRelease(text)
        _send(attributes, "setKeywords:", _strings(keywords), restype=None, argtypes=(ctypes.c_void_p,))
        identifier, domain = _cfstr(item_id), _cfstr(item_id.split(":", 1)[0])
        item = _send(_send(item_class, "alloc"), "initWithUniqueIdentifier:domainIdentifier:attributeSet:",
                     identifier, domain, attributes, argtypes=(ctypes.c_void_p,) * 3)
        _cf.CFRelease(identifier), _cf.CFRelease(domain)
        _send(made, "addObject:", item, restype=None, argtypes=(ctypes.c_void_p,))
        _send(item, "release", restype=None)
        _send(attributes, "release", restype=None)
    _send(index, "indexSearchableItems:completionHandler:", made, None, restype=None,
          argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    return True


def spotlight_remove(ids=None) -> bool:
    """Take items out of Spotlight by id (None: all of Eclipse's)."""
    index = _spotlight_index()
    if not index:
        return False
    if ids is None:
        _send(index, "deleteAllSearchableItemsWithCompletionHandler:", None, restype=None, argtypes=(ctypes.c_void_p,))
    elif ids:
        _send(index, "deleteSearchableItemsWithIdentifiers:completionHandler:", _strings(ids), None, restype=None,
              argtypes=(ctypes.c_void_p, ctypes.c_void_p))
    return True


def _continue_activity(delegate, selector, app, activity, restore):
    kind = _text(_send(activity, "activityType")) if activity else None
    if kind == _SPOTLIGHT_ACTIVITY:
        key = _cfstr(_SPOTLIGHT_ID_KEY)
        item_id = _text(_send(_send(activity, "userInfo"), "objectForKey:", key, argtypes=(ctypes.c_void_p,)))
        _cf.CFRelease(key)
        if item_id:
            opened = _spotlight["opened"]
            opened(item_id) if opened else _spotlight["pending"].append(item_id)
            return True
    return _spotlight["qt"](delegate, selector, app, activity, restore)  # (anything else is Qt's: Handoff, say)


def on_spotlight_open(opened) -> bool:
    """opened(item id) when you pick one of Eclipse's items in Spotlight (and for any picked before this)."""
    if not _cocoa():
        return False
    _spotlight["opened"] = opened
    if _spotlight["qt"] is None:
        _runtime()
        delegate = _send(_send(_objc.objc_getClass(b"NSApplication"), "sharedApplication"), "delegate")
        method = _objc.class_getInstanceMethod(_objc.object_getClass(delegate), _objc.sel_registerName(
            b"application:continueUserActivity:restorationHandler:")) if delegate else None
        if not method:
            return False
        ours = _CONTINUE(_continue_activity)
        _methods.append(ours)
        _spotlight["qt"] = _CONTINUE(_objc.method_setImplementation(method, ctypes.cast(ours, ctypes.c_void_p)))
    for item_id in _spotlight["pending"]:
        opened(item_id)
    _spotlight["pending"].clear()
    return True


# --- Power ---------------------------------------------------------------------------------------------

_THERMAL_SERIOUS = 2  # NSProcessInfoThermalStateSerious: the Mac's hot enough to be slowing itself down


def low_power() -> bool:
    """Low Power Mode is on (System Settings → Battery), or the Mac is running hot."""
    if _objc is None:
        return False
    info = _send(_objc.objc_getClass(b"NSProcessInfo"), "processInfo")
    saving = _send(info, "respondsToSelector:", _objc.sel_registerName(b"isLowPowerModeEnabled"),
                   restype=ctypes.c_bool, argtypes=(ctypes.c_void_p,)) and \
        _send(info, "isLowPowerModeEnabled", restype=ctypes.c_bool)
    return bool(saving) or _send(info, "thermalState", restype=ctypes.c_long) >= _THERMAL_SERIOUS
