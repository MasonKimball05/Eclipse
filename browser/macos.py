# Small bits of native macOS window behaviour Qt doesn't expose, done through the
# Objective-C runtime with ctypes (no extra dependency like PyObjC needed).

import ctypes
import ctypes.util
import sys

NS_WINDOW_TITLE_HIDDEN = 1

_objc = None
if sys.platform == "darwin":
    _objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    _objc.sel_registerName.restype = ctypes.c_void_p
    _objc.sel_registerName.argtypes = [ctypes.c_char_p]


def _send(receiver, selector, *args, restype=ctypes.c_void_p, argtypes=()):
    # objc_msgSend must be called with the exact argument types (it's variadic on arm64)
    send = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(("objc_msgSend", _objc))
    return send(receiver, _objc.sel_registerName(selector.encode()), *args)


def hide_title_text(widget):
    """Keep the window title for Mission Control and the Window menu, but don't draw it.

    With the title bar made transparent (Arc-style), macOS would otherwise paint the
    page title across the top of the sidebar.
    """
    if _objc is None:
        return
    view = ctypes.c_void_p(int(widget.winId()))  # the NSView Qt created for the window
    window = _send(view, "window")
    if window:
        _send(window, "setTitleVisibility:", NS_WINDOW_TITLE_HIDDEN, restype=None, argtypes=(ctypes.c_long,))
