// The doorway between Eclipse's Python and its native pages: a few C functions (loaded with ctypes, see
// browser/native.py) that open, update and close a page. Everything crosses as JSON text, so neither side needs
// the other's types: Python says what a page shows ({"page": "update", "title": …}), the page says what was done
// ({"page": 3, "event": "action", "name": "skip"}).
//
// Qt runs Cocoa's main loop, and Python calls in on the main thread, so the pages are ordinary AppKit windows in
// the same process.

import AppKit
import Foundation

/// Bumped whenever these functions or the pages' JSON change in a way the other side must know about: Python
/// only uses a library with the version it expects (an older build left in native/build falls back to Qt).
let ABI: Int32 = 2

public typealias EventCallback = @convention(c) (UnsafePointer<CChar>) -> Void

@MainActor var callback: EventCallback?

/// Tell Python something happened on a page.
@MainActor func send(_ event: [String: Any]) {
    guard let callback, let data = try? JSONSerialization.data(withJSONObject: event),
          let text = String(data: data, encoding: .utf8) else { return }
    text.withCString { callback($0) }
}

func decode(_ json: UnsafePointer<CChar>) -> [String: Any] {
    let data = Data(String(cString: json).utf8)
    return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
}

@_cdecl("eclipse_native_abi")
public func eclipseNativeAbi() -> Int32 { ABI }

@_cdecl("eclipse_native_set_callback")
public func eclipseNativeSetCallback(_ fn: EventCallback?) {
    MainActor.assumeIsolated { callback = fn }
}

/// Open a page; its id (for update and close), or 0 if there's no such page.
@_cdecl("eclipse_native_open")
public func eclipseNativeOpen(_ json: UnsafePointer<CChar>) -> Int32 {
    let spec = decode(json)
    return MainActor.assumeIsolated { PageWindow.open(spec) }
}

/// An alert answered before this returns (Alert.swift's ask): the button's index, or -1 if it couldn't be shown.
@_cdecl("eclipse_native_ask")
public func eclipseNativeAsk(_ json: UnsafePointer<CChar>) -> Int32 {
    let spec = decode(json)
    return MainActor.assumeIsolated { Int32(AlertSheet.ask(spec)) }
}

/// Open a page inside a Qt window (Embedded.swift); its id, or 0. Its view, for Qt: eclipse_native_view.
@_cdecl("eclipse_native_embed")
public func eclipseNativeEmbed(_ json: UnsafePointer<CChar>) -> Int32 {
    let spec = decode(json)
    return MainActor.assumeIsolated {
        let id = PageWindow.nextId
        guard EmbeddedView.open(spec, id: id) else { return 0 }
        PageWindow.nextId += 1
        if let pictures = spec["icons_add"] { EmbeddedView.views[id]?.model.merge(["icons_add": pictures]) }
        return id
    }
}

/// A picture of an embedded page as it looks now (base64 PNG, at the screen's scale, in memory the caller frees), for
/// Qt's own pictures of the window it's in (QWidget.grab can't see a native view); nil if it isn't open.
@_cdecl("eclipse_native_snapshot")
public func eclipseNativeSnapshot(_ id: Int32) -> UnsafeMutablePointer<CChar>? {
    MainActor.assumeIsolated {
        guard let host = EmbeddedView.views[id]?.host, host.bounds.width > 0, host.bounds.height > 0,
              let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return nil }
        host.cacheDisplay(in: host.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return nil }
        return strdup(png.base64EncodedString())
    }
}

/// An embedded page's NSView (to hand to QWindow.fromWinId), or nil.
@_cdecl("eclipse_native_view")
public func eclipseNativeView(_ id: Int32) -> UnsafeMutableRawPointer? {
    MainActor.assumeIsolated {
        EmbeddedView.views[id].map { Unmanaged.passUnretained($0.host).toOpaque() }
    }
}

/// New state for an open page (only what changed is needed: it's merged into what it had).
@_cdecl("eclipse_native_update")
public func eclipseNativeUpdate(_ id: Int32, _ json: UnsafePointer<CChar>) {
    let state = decode(json)
    MainActor.assumeIsolated {
        AlertSheet.alerts[id]?.update(state)
        if let embedded = EmbeddedView.views[id] {
            embedded.model.merge(state)
            if state["dark"] != nil { embedded.applyLook() }
        }
        guard let page = PageWindow.pages[id] else { return }
        page.model.merge(state)
        if state["dark"] != nil { page.applyLook() }  // (the Space's look changed: light or dark, and its color)
    }
}

@_cdecl("eclipse_native_front")
public func eclipseNativeFront(_ id: Int32) {
    MainActor.assumeIsolated { PageWindow.pages[id]?.front() }
}

@_cdecl("eclipse_native_close")
public func eclipseNativeClose(_ id: Int32) {
    // (a moment later, not now: Python often closes a page from its own action, in the middle of a SwiftUI update,
    // and a sheet sliding away runs AppKit's loop there, which SwiftUI doesn't survive)
    DispatchQueue.main.async {
        AlertSheet.alerts[id]?.close()
        EmbeddedView.views[id]?.close()
        PageWindow.pages[id]?.window.close()
    }
}

@_cdecl("eclipse_native_is_open")
public func eclipseNativeIsOpen(_ id: Int32) -> Bool {
    MainActor.assumeIsolated { PageWindow.pages[id] != nil || AlertSheet.alerts[id] != nil || EmbeddedView.views[id] != nil }
}

// --- For tests: what a page holds, its window's number (for screencapture -l), and a press of one of its buttons

/// The page's state as JSON, in memory the caller frees (free()); nil for a page that isn't open.
@_cdecl("eclipse_native_state")
public func eclipseNativeState(_ id: Int32) -> UnsafeMutablePointer<CChar>? {
    MainActor.assumeIsolated {
        guard let state = PageWindow.pages[id]?.model.state ?? AlertSheet.alerts[id]?.state
                ?? EmbeddedView.views[id]?.model.state,
              let data = try? JSONSerialization.data(withJSONObject: state),
              let text = String(data: data, encoding: .utf8) else { return nil }
        return strdup(text)
    }
}

@_cdecl("eclipse_native_window_number")
public func eclipseNativeWindowNumber(_ id: Int32) -> Int {
    MainActor.assumeIsolated {
        (PageWindow.pages[id]?.window ?? AlertSheet.alerts[id]?.alert.window ?? EmbeddedView.views[id]?.host.window)?
            .windowNumber ?? 0
    }
}

@_cdecl("eclipse_native_press")
public func eclipseNativePress(_ id: Int32, _ name: UnsafePointer<CChar>) {
    let action = String(cString: name)
    MainActor.assumeIsolated {
        AlertSheet.alerts[id]?.press(action)
        (PageWindow.pages[id]?.model ?? EmbeddedView.views[id]?.model)?.act(action)
    }
}

/// Where a page's window is, as Qt counts (points from the top left of the main screen): x, y, width, height into
/// `out`. False for a page that isn't open.
@_cdecl("eclipse_native_frame")
public func eclipseNativeFrame(_ id: Int32, _ out: UnsafeMutablePointer<Double>) -> Bool {
    MainActor.assumeIsolated {
        guard let window = PageWindow.pages[id]?.window ?? AlertSheet.alerts[id]?.alert.window
                ?? EmbeddedView.views[id]?.host.window,
              let main = NSScreen.screens.first else { return false }
        let frame = window.frame
        out[0] = frame.minX
        out[1] = main.frame.maxY - frame.maxY
        out[2] = frame.width
        out[3] = frame.height
        return true
    }
}

/// A key with ⌘ (and ⇧) sent to a page's window as a key equivalent, as if pressed there: whether it took it.
@_cdecl("eclipse_native_key")
public func eclipseNativeKey(_ id: Int32, _ key: UnsafePointer<CChar>, _ shift: Bool) -> Bool {
    let characters = String(cString: key)
    return MainActor.assumeIsolated {
        guard let window = PageWindow.pages[id]?.window,
              let event = NSEvent.keyEvent(with: .keyDown, location: .zero,
                                           modifierFlags: shift ? [.command, .shift] : [.command],
                                           timestamp: 0, windowNumber: window.windowNumber, context: nil,
                                           characters: characters, charactersIgnoringModifiers: characters,
                                           isARepeat: false, keyCode: 0) else { return false }
        return window.performKeyEquivalent(with: event)
    }
}
