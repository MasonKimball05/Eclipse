// A native page that lives inside a Qt window rather than in a window of its own: a SwiftUI view in an
// NSHostingView, which Qt takes in as a child (QWindow.fromWinId and QWidget.createWindowContainer: Qt's own way to
// hold a foreign view), sizes with its layout and shows and hides with it. Python opens it like a page
// (eclipse_native_embed: the same JSON, the same update, actions and close) and asks for its view
// (eclipse_native_view) to hand to Qt. The view draws no background of its own: what Qt paints shows through.

import AppKit
import SwiftUI

@MainActor
final class EmbeddedView: NSObject, NSPopoverDelegate {
    static var views: [Int32: EmbeddedView] = [:]

    let model: PageModel
    let host: NSView
    private var popover: NSPopover?

    static func open(_ spec: [String: Any], id: Int32) -> Bool {
        let model = PageModel(id: id, state: spec)
        let body: AnyView
        switch model.string("page") {
        case "sidebar_body":
            guard #available(macOS 14, *) else { return false }
            body = AnyView(SidebarBodyView(model: model))
        default:
            return false
        }
        views[id] = EmbeddedView(model: model, view: AnyView(Tinted(model: model, content: body)))
        return true
    }

    /// A form as a popover from a point on a Qt window ("popover_view": its content view; "popover_at": [x, y] in
    /// it, from its top left), as the Space editor opens from the Space's name. Closing it (a click elsewhere, Esc)
    /// is its Cancel.
    static func openPopover(_ spec: [String: Any], id: Int32) -> Bool {
        guard let wanted = (spec["popover_view"] as? NSNumber)?.uintValue,
              let at = spec["popover_at"] as? [NSNumber], at.count == 2,
              let window = NSApp.windows.first(where: { window in
                  window.isVisible && window.contentView.map { UInt(bitPattern: Unmanaged.passUnretained($0).toOpaque()) } == wanted
              }), let anchor = window.contentView else { return false }
        let model = PageModel(id: id, state: spec)
        let root = AnyView(Tinted(model: model, content: AnyView(FormView(model: model))))
        let controller = NSHostingController(rootView: root)
        controller.preferredContentSize = NSSize(width: model.double("width") ?? 320, height: model.double("height") ?? 400)
        let page = EmbeddedView(model: model, host: controller.view)
        let popover = NSPopover()
        popover.contentViewController = controller
        popover.behavior = .transient
        popover.animates = true
        popover.delegate = page
        if let dark = spec["dark"] as? Bool { popover.appearance = NSAppearance(named: dark ? .darkAqua : .aqua) }
        page.popover = popover
        views[id] = page
        if let r = (spec["popover_rect"] as? [NSNumber])?.map({ CGFloat($0.doubleValue) }), r.count == 4 {
            // Beside a place ("popover_rect": the Space's name), to its right, its arrow pointing at it
            let rect = NSRect(x: r[0], y: anchor.isFlipped ? r[1] : anchor.bounds.height - r[1] - r[3], width: r[2], height: r[3])
            popover.show(relativeTo: rect, of: anchor, preferredEdge: .maxX)
        } else {
            let x = CGFloat(at[0].doubleValue), y = CGFloat(at[1].doubleValue)
            let point = NSPoint(x: x, y: anchor.isFlipped ? y : anchor.bounds.height - y)
            popover.show(relativeTo: NSRect(origin: point, size: NSSize(width: 1, height: 1)), of: anchor, preferredEdge: .maxY)
        }
        model.window = controller.view.window
        return true
    }

    private init(model: PageModel, host: NSView) {
        self.model = model
        self.host = host
    }

    func popoverDidClose(_ notification: Notification) {
        EmbeddedView.views[model.id] = nil
        send(["page": Int(model.id), "event": "closed"])
    }

    private init(model: PageModel, view: AnyView) {
        self.model = model
        let host = PassThroughHostingView(rootView: view)
        host.sizingOptions = []  // (Qt's layout gives it its size)
        host.wantsLayer = true
        host.layer?.backgroundColor = .clear
        self.host = host
        super.init()
        if let dark = model.state["dark"] as? Bool {
            host.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        }
    }

    func applyLook() {
        if let dark = model.state["dark"] as? Bool {
            host.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        }
    }

    func close() {
        if let popover {
            popover.close()  // (popoverDidClose says so)
            return
        }
        host.removeFromSuperview()
        EmbeddedView.views[model.id] = nil
        send(["page": Int(model.id), "event": "closed"])
    }
}

/// A hosting view that's clear (Qt's window shows through it) and takes the first click: a click on a tab while the
/// window's in the background should go to the tab, as Qt's sidebar does.
final class PassThroughHostingView<Content: View>: NSHostingView<Content> {
    override var isOpaque: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}
