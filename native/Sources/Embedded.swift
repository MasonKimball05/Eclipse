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
        if model.state["page"] as? String == "sidebar_body" {
            // A tab dragged over it: offered to it, for its rows to take. SwiftUI's own view does the taking but,
            // in Qt's window, never asked for them: dragged tabs went past it to Qt's view beneath, and no row
            // could be dropped on (they lifted, and nothing showed where they'd go)
            host.registerForDraggedTypes([NSPasteboard.PasteboardType(TabDrag.qtType),
                                          NSPasteboard.PasteboardType(TabDrag.qtManyType)])
            host.drops = SidebarDrops.of(model.id)
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
        SidebarDrops.forget(model.id)
        EmbeddedView.views[model.id] = nil
        send(["page": Int(model.id), "event": "closed"])
    }
}

/// A hosting view that's clear (Qt's window shows through it) and takes the first click: a click on a tab while the
/// window's in the background should go to the tab, as Qt's sidebar does.
final class PassThroughHostingView<Content: View>: NSHostingView<Content> {
    override var isOpaque: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    var drops: SidebarDrops?  // (the sidebar's: where a dragged tab can go)

    // A dragged tab, over the sidebar (only it asks for them): handed to the drop spot it's over (SidebarDrops),
    // SwiftUI's own drops never reaching a row in a view inside Qt's window
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { draggingUpdated(sender) }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        drops?.over(dropPoint(sender), in: bounds) == true ? .move : []
    }

    override func draggingExited(_ sender: NSDraggingInfo?) { drops?.left() }

    // Ready to take it wherever there's a spot under it: SwiftUI's answer (it knows none of them) could be no, and
    // the drop was called off, the tab flying back and the room left open
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool {
        drops?.over(dropPoint(sender), in: bounds) == true
    }

    // However it ends (dropped elsewhere, called off): the room closes, and the rows' places follow the layout again
    override func concludeDragOperation(_ sender: NSDraggingInfo?) { drops?.left() }

    override func draggingEnded(_ sender: NSDraggingInfo) { drops?.ended() }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        // What this sidebar knows it's carrying, if the drag started here; the drag's own data only for one from
        // elsewhere (Favorites, Qt's sidebar). The drag pasteboard is every drag's, filled when it's read: read at a
        // quick drop, it could still hold the drag before's tab, which was moved instead (as often as not, nowhere)
        guard let drops, let ids = drops.carrying ?? SidebarDrops.ids(on: sender.draggingPasteboard) else {
            drops?.left()
            return false
        }
        return drops.drop(dropPoint(sender), in: bounds, ids: ids)
    }

    /// Where the drag is, measured from the view's top-left (as SwiftUI lays the rows out).
    private func dropPoint(_ sender: NSDraggingInfo) -> CGPoint {
        let point = convert(sender.draggingLocation, from: nil)
        return isFlipped ? point : CGPoint(x: point.x, y: bounds.height - point.y)
    }
}

/// The tests' (tests/live/test_native_drag.py, through SidebarBody's "drop_test"): a whole drag of tabs `ids` along
/// `path` (points from the view's top-left), as macOS sends one, through the view's own handlers: in, along, asked if
/// it's ready, dropped, done, over. What each said comes back.
@MainActor
protocol DragTesting {
    func testDrag(_ path: [CGPoint], ids: String) -> [String: Any]
}

extension PassThroughHostingView: DragTesting {
    func testDrag(_ path: [CGPoint], ids: String) -> [String: Any] {
        guard let first = path.first, let last = path.last else { return ["error": "no path"] }
        let pasteboard = NSPasteboard(name: NSPasteboard.Name("eclipse.test.drag"))
        pasteboard.clearContents()
        pasteboard.setData(Data(ids.utf8), forType: NSPasteboard.PasteboardType(TabDrag.qtType))
        func at(_ point: CGPoint) -> TestDraggingInfo {
            let local = isFlipped ? point : CGPoint(x: point.x, y: bounds.height - point.y)
            return TestDraggingInfo(location: convert(local, to: nil), pasteboard: pasteboard, window: window)
        }
        var result: [String: Any] = ["entered": draggingEntered(at(first)).rawValue]
        result["updated"] = path.map { draggingUpdated(at($0)).rawValue }
        let ready = prepareForDragOperation(at(last))
        result["ready"] = ready
        if ready {
            result["performed"] = performDragOperation(at(last))
            concludeDragOperation(at(last))
        }
        draggingEnded(at(last))
        return result
    }
}

/// What macOS hands a view about a drag, for testDrag.
final class TestDraggingInfo: NSObject, NSDraggingInfo {
    let draggingLocation: NSPoint
    let draggingPasteboard: NSPasteboard
    let draggingDestinationWindow: NSWindow?
    var draggingFormation: NSDraggingFormation = .default
    var animatesToDestination = false
    var numberOfValidItemsForDrop = 1

    init(location: NSPoint, pasteboard: NSPasteboard, window: NSWindow?) {
        draggingLocation = location
        draggingPasteboard = pasteboard
        draggingDestinationWindow = window
    }

    var draggingSourceOperationMask: NSDragOperation { [.move, .copy, .generic] }
    var draggedImageLocation: NSPoint { draggingLocation }
    var draggedImage: NSImage? { nil }
    var draggingSource: Any? { nil }
    var draggingSequenceNumber: Int { 1 }
    var springLoadingHighlight: NSSpringLoadingHighlight { .none }
    func slideDraggedImage(to screenPoint: NSPoint) {}
    func resetSpringLoading() {}
    func enumerateDraggingItems(options enumOpts: NSDraggingItemEnumerationOptions = [], for view: NSView?,
                                classes classArray: [AnyClass], searchOptions: [NSPasteboard.ReadingOptionKey: Any] = [:],
                                using block: (NSDraggingItem, Int, UnsafeMutablePointer<ObjCBool>) -> Void) {}
}
