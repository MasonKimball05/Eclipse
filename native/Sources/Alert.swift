// macOS's own alert (NSAlert), as a sheet on the window that asks: a question to confirm ("Delete Folder"), a note,
// or a line to type (a name: "Rename Space"). Python sends {"page": "alert", "sheet_view": a Qt window's content
// view (or "sheet_page": a native page's id), "title", "text", "buttons": [labels, the first the default, a
// "Cancel" one answering Esc], "destructive": index, "field": {"value", "placeholder"}, "style": "warning"|"info"}.
// Once a button's pressed it sends {"name": "button", "index": i, "text": the field's text}, then "closed". It's
// only opened on a window that's showing: Python asks Qt otherwise.

import AppKit

@MainActor
final class AlertSheet {
    static var alerts: [Int32: AlertSheet] = [:]

    let id: Int32
    let alert = NSAlert()
    private(set) var state: [String: Any]
    private var field: NSTextField?

    static func open(_ spec: [String: Any], id: Int32) -> Bool {
        guard let parent = sheetParent(spec) else { return false }
        let sheet = AlertSheet(id: id, spec: spec)
        alerts[id] = sheet
        sheet.alert.beginSheetModal(for: parent) { response in
            sheet.finished(response)
        }
        if let field = sheet.field { sheet.alert.window.makeFirstResponder(field) }
        return true
    }

    /// The window an alert is a sheet on ("sheet_view": its content view, Qt's winId), if it's showing.
    /// Asked and answered before this returns (for a caller that can't wait: a download's yes or no, which Qt
    /// needs within its signal): still a sheet, with the app's own modal loop running till a button's pressed. The
    /// button's index, or -1 if it couldn't be shown.
    static func ask(_ spec: [String: Any]) -> Int {
        guard alerts[-1] == nil, let parent = sheetParent(spec) else { return -1 }  // (one at a time: not nested)
        let sheet = AlertSheet(id: -1, spec: spec)
        alerts[-1] = sheet  // (the tests answer it: eclipse_native_press(-1, "button:N"))
        var answer = -1
        sheet.alert.beginSheetModal(for: parent) { response in
            answer = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
            alerts[-1] = nil
            NSApp.stopModal()
        }
        NSApp.runModal(for: sheet.alert.window)
        return answer
    }

    static func sheetParent(_ spec: [String: Any]) -> NSWindow? {
        if let page = (spec["sheet_page"] as? NSNumber)?.int32Value {  // (a native page's window: Settings…)
            return PageWindow.pages[page].map(\.window).flatMap { $0.isVisible && $0.attachedSheet == nil ? $0 : nil }
        }
        guard let wanted = (spec["sheet_view"] as? NSNumber)?.uintValue, wanted != 0 else { return nil }
        return NSApp.windows.first { window in
            window.isVisible && window.attachedSheet == nil
                && window.contentView.map { UInt(bitPattern: Unmanaged.passUnretained($0).toOpaque()) } == wanted
        }
    }

    private init(id: Int32, spec: [String: Any]) {
        self.id = id
        state = spec
        alert.messageText = spec["title"] as? String ?? ""
        alert.informativeText = spec["text"] as? String ?? ""
        alert.alertStyle = spec["style"] as? String == "warning" ? .warning : .informational
        if let dark = spec["dark"] as? Bool {
            alert.window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        }
        let labels = spec["buttons"] as? [String] ?? ["OK"]
        for label in labels { alert.addButton(withTitle: label) }
        if let destructive = (spec["destructive"] as? NSNumber)?.intValue, alert.buttons.indices.contains(destructive) {
            alert.buttons[destructive].hasDestructiveAction = true
        }
        if let wanted = spec["field"] as? [String: Any] {
            let field = wanted["secret"] as? Bool == true ? NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
                : NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
            field.stringValue = wanted["value"] as? String ?? ""
            field.placeholderString = wanted["placeholder"] as? String
            field.lineBreakMode = .byTruncatingTail
            field.cell?.isScrollable = true
            alert.accessoryView = field
            self.field = field
        }
        alert.layout()
    }

    private func finished(_ response: NSApplication.ModalResponse) {
        let index = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        AlertSheet.alerts[id] = nil
        if response != .abort && response != .cancel {
            send(["page": Int(id), "event": "action", "name": "button", "index": index, "text": field?.stringValue ?? ""])
        }
        send(["page": Int(id), "event": "closed"])
    }

    /// Put away unanswered (as Cancel would be, but with no button sent).
    func close() {
        alert.window.sheetParent?.endSheet(alert.window, returnCode: .abort)
    }

    /// The tests: the field's text set as if typed ("text").
    func update(_ more: [String: Any]) {
        state.merge(more) { _, new in new }
        if let text = more["text"] as? String { field?.stringValue = text }
    }

    /// The tests: a button pressed ("button:<index>").
    func press(_ name: String) {
        guard name.hasPrefix("button:"), let index = Int(name.dropFirst("button:".count)),
              alert.buttons.indices.contains(index) else { return }
        alert.buttons[index].performClick(nil)
    }
}
