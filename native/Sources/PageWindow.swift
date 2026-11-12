// A native page's window, and the state it shows. The state is the JSON Python sent, kept as a dictionary
// (pages read what they need from it with the small accessors below), so a page's fields can grow on the Python
// side without a new type here.

import AppKit
import SwiftUI

@MainActor
final class PageModel: ObservableObject {
    let id: Int32
    @Published var state: [String: Any]
    /// Pictures sent once and kept ("icons_add": {key: base64 PNG}), for pages that show many (the sidebar's icons).
    @Published var images: [String: NSImage] = [:]
    weak var window: NSWindow?

    init(id: Int32, state: [String: Any]) {
        self.id = id
        self.state = state
        if let pictures = state["icons_add"] { merge(["icons_add": pictures]) }
    }

    func merge(_ more: [String: Any]) {
        var more = more
        if let pictures = more.removeValue(forKey: "icons_add") as? [String: String] {
            for (key, text) in pictures {
                if let data = Data(base64Encoded: text), let image = NSImage(data: data) { images[key] = image }
            }
        }
        if !more.isEmpty { state.merge(more) { _, new in new } }
    }

    func string(_ key: String, _ fallback: String = "") -> String { state[key] as? String ?? fallback }
    func bool(_ key: String, _ fallback: Bool = false) -> Bool { state[key] as? Bool ?? fallback }
    func double(_ key: String) -> Double? { (state[key] as? NSNumber)?.doubleValue }
    func sections(_ key: String = "sections") -> [NoteSection] {
        (state[key] as? [[String: Any]] ?? []).enumerated().map { NoteSection(index: $0.offset, json: $0.element) }
    }

    /// The page asks Python to do something ("skip", "update"…), with anything that goes with it.
    func act(_ name: String, _ extra: [String: Any] = [:]) {
        send(["page": Int(id), "event": "action", "name": name].merging(extra) { a, _ in a })
    }

    var accent: Color { Color(hex: string("accent")) ?? .accentColor }
}

/// A heading and its lines, as Python's notes() splits a Markdown page: each line is inline Markdown.
struct NoteSection: Identifiable {
    struct Line: Identifiable {
        let id: Int
        let text: String
        let bullet: Bool
    }

    let id: Int
    let heading: String
    let lines: [Line]

    init(index: Int, json: [String: Any]) {
        id = index
        heading = json["heading"] as? String ?? ""
        lines = (json["lines"] as? [[String: Any]] ?? []).enumerated().map {
            Line(id: $0.offset, text: $0.element["text"] as? String ?? "", bullet: $0.element["bullet"] as? Bool ?? true)
        }
    }
}

extension Color {
    init?(hex: String) {
        var text = hex.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        guard text.count == 6, let value = UInt32(text, radix: 16) else { return nil }
        self.init(red: Double((value >> 16) & 0xFF) / 255, green: Double((value >> 8) & 0xFF) / 255,
                  blue: Double(value & 0xFF) / 255)
    }
}

extension Text {
    /// A line of inline Markdown (**bold**, `code`, links); as plain text if it doesn't parse.
    init(markdown: String) {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        if let parsed = try? AttributedString(markdown: markdown, options: options) {
            self.init(parsed)
        } else {
            self.init(verbatim: markdown)
        }
    }
}

@MainActor
final class PageWindow: NSObject, NSWindowDelegate {
    static var pages: [Int32: PageWindow] = [:]
    static var nextId: Int32 = 1

    let window: NSWindow
    let model: PageModel

    static func open(_ spec: [String: Any]) -> Int32 {
        let id = nextId
        if spec["popover_view"] != nil {  // (a form as a popover from a point on a Qt window: Embedded.swift)
            guard EmbeddedView.openPopover(spec, id: id) else { return 0 }
            nextId += 1
            return id
        }
        if spec["page"] as? String == "alert" {  // (macOS's alert, as a sheet: Alert.swift)
            guard AlertSheet.open(spec, id: id) else { return 0 }
            nextId += 1
            return id
        }
        let model = PageModel(id: id, state: spec)
        guard let view = page(for: model) else { return 0 }
        nextId += 1
        let page = PageWindow(model: model, view: view)
        pages[id] = page
        page.front()
        return id
    }

    /// The SwiftUI view for a page kind, in the window's look (light or dark as the Space is, its color as tint).
    private static func page(for model: PageModel) -> AnyView? {
        let body: AnyView
        switch model.string("page") {
        case "whats_new": body = AnyView(WhatsNewView(model: model))
        case "update": body = AnyView(UpdateView(model: model))
        case "settings": body = AnyView(SettingsView(model: model))
        case "library": body = AnyView(LibraryView(model: model))
        case "downloads": body = AnyView(DownloadsView(model: model))
        case "form": body = AnyView(FormView(model: model))
        case "spaces_lab":  // (a trial, macOS 14 and up: SpacesLab.swift)
            guard #available(macOS 14, *) else { return nil }
            body = AnyView(SpacesLabView(model: model))
        default: return nil
        }
        return AnyView(Tinted(model: model, content: body))
    }

    private init(model: PageModel, view: AnyView) {
        self.model = model
        let width = model.double("width") ?? 560, height = model.double("height") ?? 520
        window = PageNSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                          styleMask: [.titled, .closable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        super.init()
        window.title = model.string("title")
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isReleasedWhenClosed = false
        window.animationBehavior = .documentWindow
        window.minSize = NSSize(width: model.double("min_width") ?? 420, height: model.double("min_height") ?? 320)
        let host = NSHostingView(rootView: view)
        host.sizingOptions = []  // (the window keeps the size it's given: a ScrollView would ask for the whole screen)
        window.contentView = host
        model.window = window
        if model.bool("show_title") {  // (the page's title in the toolbar, as System Settings has)
            window.titleVisibility = .visible
            window.toolbarStyle = .unified
        }
        applyLook()
        window.delegate = self
        place()
    }

    /// On the screen you're using (the one with the browser window in front), not the first one: center() goes by
    /// the window's own screen, and a window not yet anywhere is on the laptop's, so with the browser on a monitor
    /// Settings, the Library and the rest opened on the other display. Never bigger than that screen, either.
    private func place() {
        guard let area = (NSScreen.main ?? window.screen)?.visibleFrame else { window.center(); return }
        var frame = window.frame
        frame.size.width = min(frame.width, area.width)
        frame.size.height = min(frame.height, area.height)
        frame.origin.x = area.midX - frame.width / 2
        frame.origin.y = area.minY + (area.height - frame.height) * 2 / 3  // (a little above the middle, as center())
        window.setFrame(frame, display: false)
    }

    func applyLook() {
        if let dark = model.state["dark"] as? Bool {
            window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        }
    }

    func front() {
        if model.bool("behind") {  // (the live tests: shown, but behind everything, so they never take the screen)
            window.orderBack(nil)
            return
        }
        if window.sheetParent == nil, !window.isVisible, let parent = sheetParent() {
            parent.beginSheet(window)
            return
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: false)
    }

    /// The window a form is a sheet on ("sheet_view": a Qt window's content view, its winId; or "sheet_page": a
    /// native page's id), if it's still open.
    private func sheetParent() -> NSWindow? {
        if let page = (model.state["sheet_page"] as? NSNumber)?.int32Value {  // (on a native page: Downloads…)
            return PageWindow.pages[page].map(\.window).flatMap { $0.isVisible && $0.attachedSheet == nil ? $0 : nil }
        }
        guard let wanted = (model.state["sheet_view"] as? NSNumber)?.uintValue, wanted != 0 else { return nil }
        return NSApp.windows.first { window in
            window.isVisible && window.contentView.map { UInt(bitPattern: Unmanaged.passUnretained($0).toOpaque()) } == wanted
        }
    }

    func windowDidBecomeKey(_ notification: Notification) {
        model.act("focus")  // (back to it: what changed meanwhile, elsewhere, is read again)
    }

    func windowWillClose(_ notification: Notification) {
        PageWindow.pages[model.id] = nil
        send(["page": Int(model.id), "event": "closed"])
    }
}

/// A page in the Space's color, following it when it changes.
struct Tinted: View {
    @ObservedObject var model: PageModel
    let content: AnyView

    var body: some View { content.tint(model.accent).environmentObject(model) }
}

/// A native page's window. The menu bar is Qt's (the browser's), so the keys a window answers itself come here
/// first: ⌘W closes this window, not a tab, and editing keys in a text field (⌘C, ⌘V, ⌘X, ⌘A, ⌘Z) edit the field,
/// not the page behind.
final class PageNSWindow: NSWindow {
    override func close() {
        sheetParent?.endSheet(self)  // (a sheet's put away first, so its browser window takes clicks again)
        super.close()
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let key = event.charactersIgnoringModifiers?.lowercased() ?? ""
        if flags == .command && key == "w" {
            if sheetParent != nil { close() } else { performClose(nil) }  // (a sheet has no close button to press)
            return true
        }
        if firstResponder is NSText, flags.contains(.command) {
            let shift = flags.contains(.shift)
            let action: Selector? = switch key {
            case "c": #selector(NSText.copy(_:))
            case "v": #selector(NSText.paste(_:))
            case "x": #selector(NSText.cut(_:))
            case "a": #selector(NSText.selectAll(_:))
            case "z": shift ? Selector(("redo:")) : Selector(("undo:"))
            default: nil
            }
            if let action, NSApp.sendAction(action, to: nil, from: self) { return true }
        }
        return super.performKeyEquivalent(with: event)
    }
}
