// Settings, natively: a sidebar of pages (with search, as System Settings has) and each page as a grouped form.
// What it shows comes from browser/settings_native.py, which reads Qt's Settings pages: pages → items, an item
// being a card of rows, a row (title, description, control), a heading, a note or a control on its own. What's
// done here goes back as an action on the control's id ("toggle", "choose", "press", "text"…), and Python sends
// the pages again as they are after it.

import AppKit
import SwiftUI

typealias JSON = [String: Any]

extension Dictionary where Key == String, Value == Any {
    func str(_ key: String) -> String { self[key] as? String ?? "" }
    func flag(_ key: String, _ fallback: Bool = false) -> Bool { self[key] as? Bool ?? fallback }
    func int(_ key: String) -> Int? { (self[key] as? NSNumber)?.intValue }
    func list(_ key: String) -> [JSON] { self[key] as? [JSON] ?? [] }
    var controlId: Int { (self["id"] as? NSNumber)?.intValue ?? 0 }
}

/// Every piece of text an item shows, for search.
func words(_ item: JSON) -> String {
    var all = [item.str("title"), item.str("description"), item.str("text"), item.str("tip")]
    if let control = item["control"] as? JSON { all.append(words(control)) }
    for key in ["items", "rows"] { all += item.list(key).map(words) }
    all += (item["options"] as? [String] ?? []) + (item["menu"] as? [String] ?? [])
    return all.joined(separator: " ")
}

struct SettingsView: View {
    @ObservedObject var model: PageModel
    @State private var selection: String?
    @State private var search = ""

    var pages: [JSON] { model.state["pages"] as? [JSON] ?? [] }

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                ForEach(shownPages, id: \.self.key) { page in
                    Label(page.str("title"), systemImage: page.str("symbol")).tag(page.str("key"))
                }
            }
            .searchable(text: $search, placement: .sidebar, prompt: "Search")
            .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 280)
            .safeAreaInset(edge: .bottom) {
                if model.bool("classic") {
                    Button("Classic Settings…") { model.act("classic") }
                        .buttonStyle(.link).font(.callout).padding(.bottom, 10)
                        .help("The same settings in Eclipse's own window")
                }
            }
        } detail: {
            if search.isEmpty {
                if let page = pages.first(where: { $0.str("key") == selection }) ?? pages.first {
                    PageForm(items: page.list("items"))
                        .onAppear { model.window?.title = page.str("title") }
                        .onChange(of: page.str("title")) { model.window?.title = $0 }
                }
            } else {
                SearchResults(pages: pages, query: search).onAppear { model.window?.title = "Search" }
            }
        }
        .onAppear { selection = model.string("selected", "general") }
        .onChange(of: model.string("selected")) { selection = $0 }
        .onChange(of: selection) { key in
            if let key, key != model.string("selected") {
                model.merge(["selected": key])
                model.act("select", ["key": key])
            }
        }
        .environmentObject(model)
    }

    /// While searching, only the pages with a match.
    var shownPages: [JSON] {
        guard !search.isEmpty else { return pages }
        return pages.filter { page in page.list("items").contains { words($0).localizedCaseInsensitiveContains(search) } }
    }
}

private extension Dictionary where Key == String, Value == Any {
    var key: String { str("key") }
}

/// Each page holding a match, its matching rows (a card's, too) under the page's name.
struct SearchResults: View {
    let pages: [JSON]
    let query: String

    var body: some View {
        Form {
            ForEach(Array(matches.enumerated()), id: \.offset) { _, match in
                Section(match.title) {
                    ForEach(Array(match.items.enumerated()), id: \.offset) { _, item in ItemView(item: item) }
                }
            }
            if matches.isEmpty {
                Text("No settings match “\(query)”.").foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Search")
    }

    var matches: [(title: String, items: [JSON])] {
        pages.compactMap { page in
            let items = page.list("items").flatMap { item -> [JSON] in
                item.str("kind") == "card" ? item.list("items") : [item]
            }.filter { $0.str("kind") != "heading" && words($0).localizedCaseInsensitiveContains(query) }
            return items.isEmpty ? nil : (page.str("title"), items)
        }
    }
}

/// A page: its cards as sections, a note after a card as that card's footer, a heading as the next card's header.
struct PageForm: View {
    let items: [JSON]

    struct Block {
        var header = ""
        var rows: [JSON] = []
        var footer: [String] = []
        var card = false
    }

    var blocks: [Block] {
        var blocks: [Block] = []
        var header = ""
        for item in items {
            switch item.str("kind") {
            case "heading":
                header = item.str("text")
            case "card":
                blocks.append(Block(header: header, rows: item.list("items"), card: true))
                header = ""
            case "note" where blocks.last?.card == true && header.isEmpty:
                blocks[blocks.count - 1].footer.append(item.str("text"))
            default:
                if blocks.last?.card == false && header.isEmpty && blocks.last?.footer.isEmpty == true {
                    blocks[blocks.count - 1].rows.append(item)
                } else {
                    blocks.append(Block(header: header, rows: [item]))
                    header = ""
                }
            }
        }
        return blocks
    }

    var body: some View {
        Form {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                Section {
                    ForEach(Array(block.rows.enumerated()), id: \.offset) { _, item in ItemView(item: item) }
                } header: {
                    if !block.header.isEmpty { Text(block.header) }
                } footer: {
                    if !block.footer.isEmpty {
                        Text(block.footer.joined(separator: "\n\n")).font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .formStyle(.grouped)
    }
}

struct ItemView: View {
    let item: JSON

    var body: some View {
        switch item.str("kind") {
        case "row":
            RowView(title: item.str("title"), description: item.str("description"), control: item["control"] as? JSON,
                    extras: item.list("extras"))
        case "note":
            Text(item.str("text")).font(item.flag("dim") ? .callout : .body)
                .foregroundStyle(item.flag("dim") ? .secondary : .primary)
                .fixedSize(horizontal: false, vertical: true)
        case "heading":
            Text(item.str("text")).font(.headline)
        case "card":
            ForEach(Array(item.list("items").enumerated()), id: \.offset) { _, inner in ItemView(item: inner) }
        default:
            if let control = item["control"] as? JSON { ControlView(control: control, wide: true) }
        }
    }
}

struct RowLabel: View {
    let title: String
    let description: String
    var extras: [JSON] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
            if !description.isEmpty {
                Text(description).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(extras.enumerated()), id: \.offset) { _, extra in ControlView(control: extra, wide: true) }
        }
    }
}

struct RowView: View {
    let title: String
    let description: String
    let control: JSON?
    var extras: [JSON] = []

    var label: RowLabel { RowLabel(title: title, description: description, extras: extras) }

    var body: some View {
        if let control, control.str("type") == "toggle", control.str("text").isEmpty {
            ToggleControl(control: control) { label }.id(control.controlId)  // (its own state: see ControlView)
        } else if let control, control.str("type") == "menu" {
            MenuControl(control: control) { label }.id(control.controlId)
        } else if let control, ["group", "list", "textarea"].contains(control.str("type")),
                  control.str("axis") != "h" {
            VStack(alignment: .leading, spacing: 10) {  // (too big to sit beside the title: under it)
                label
                ControlView(control: control, wide: true)
            }
        } else if let control {
            LabeledContent {
                ControlView(control: control)
            } label: {
                label
            }
        } else {
            label
        }
    }
}

/// A switch. It shows the new state at once (Python's answer, the pages read again, follows a moment later).
struct ToggleControl<Label: View>: View {
    @EnvironmentObject var model: PageModel
    let control: JSON
    @ViewBuilder let label: () -> Label
    @State private var pending: Bool?

    var body: some View {
        let actual = control.flag("checked")
        Toggle(isOn: Binding(get: { pending ?? actual }, set: { on in
            pending = on
            model.act("toggle", ["id": control.controlId, "value": on])
        })) { label() }
        .toggleStyle(.switch)
        .disabled(!control.flag("enabled", true))
        .help(control.str("tip"))
        .onChange(of: actual) { _ in pending = nil }
        .task(id: pending) {  // (Python didn't take it: back to how it is, after a moment)
            guard pending != nil else { return }
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            pending = nil
        }
    }
}

struct MenuControl<Label: View>: View {
    @EnvironmentObject var model: PageModel
    let control: JSON
    @ViewBuilder let label: () -> Label
    @State private var pending: Int?

    var body: some View {
        let actual = control.int("index") ?? 0
        let options = control["options"] as? [String] ?? []
        Picker(selection: Binding(get: { pending ?? actual }, set: { index in
            pending = index
            model.act("choose", ["id": control.controlId, "value": index])
        })) {
            ForEach(Array(options.enumerated()), id: \.offset) { index, option in Text(option).tag(index) }
        } label: { label() }
        .pickerStyle(.menu)
        .disabled(!control.flag("enabled", true))
        .help(control.str("tip"))
        .onChange(of: actual) { _ in pending = nil }
        .task(id: pending) {
            guard pending != nil else { return }
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            pending = nil
        }
    }
}

struct ControlView: View {
    @EnvironmentObject var model: PageModel
    let control: JSON
    var wide = false

    var id: Int { control.controlId }
    var enabled: Bool { control.flag("enabled", true) }

    var body: some View {
        content
            .disabled(!enabled)
            .help(control.str("tip"))
            // Its own control's: rows are drawn by place, so a row added above would hand this one's state (text being
            // typed, a switch just flipped) to the control that moved into its place, and send it with that one's id
            .id(id)
    }

    @ViewBuilder var content: some View {
        switch control.str("type") {
        case "toggle":
            ToggleControl(control: control) { Text(control.str("text")) }
        case "menu":
            MenuControl(control: control) { EmptyView() }.labelsHidden().fixedSize()
        case "button":
            ButtonControl(control: control)
        case "keys":
            KeysControl(control: control)
        case "icon":
            IconChoice(control: control)
        case "text":
            TextControl(control: control).frame(maxWidth: wide ? .infinity : 260)
        case "textarea":
            TextAreaControl(control: control)
        case "progress":
            ProgressView(value: Double(control.int("value") ?? 0), total: Double(control.int("total") ?? 100))
                .frame(minWidth: 120)
        case "list":
            ListControl(control: control)
        case "label":
            if control.flag("keycap") {
                Text(control.str("text")).font(.callout.monospaced()).padding(.horizontal, 7).padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 5).fill(.quaternary))
            } else {
                Text(control.str("text")).font(control.flag("dim") ? .callout : .body)
                    .foregroundStyle(control.flag("dim") ? .secondary : .primary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case "picture":
            if let image = picture(control.str("png")) {
                Image(nsImage: image).resizable().interpolation(.high)
                    .frame(width: CGFloat(control.int("width") ?? 100), height: CGFloat(control.int("height") ?? 60))
            }
        case "group":
            GroupControl(control: control)
        default:
            EmptyView()
        }
    }
}

func picture(_ base64: String) -> NSImage? {
    guard let data = Data(base64Encoded: base64), let image = NSImage(data: data) else { return nil }
    return image
}

struct ButtonControl: View {
    @EnvironmentObject var model: PageModel
    let control: JSON

    var body: some View {
        let text = control.str("text")
        if let items = control["menu"] as? [String] {
            Menu(text) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    Button(item) { model.act("menu_item", ["id": control.controlId, "value": index]) }
                }
            }
            .fixedSize()
        } else {
            let button = Button(text) { model.act("press", ["id": control.controlId]) }
            switch control.str("style") {
            case "primary": button.buttonStyle(.borderedProminent)
            case "link": button.buttonStyle(.link)
            default: button
            }
        }
    }
}

struct GroupControl: View {
    let control: JSON

    var body: some View {
        let items = control.list("items")
        switch control.str("axis") {
        case "grid":
            let size = CGFloat(items.first?.int("size") ?? 32) + 14
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(size), spacing: 8), count: control.int("columns") ?? 6),
                      alignment: .leading, spacing: 8) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in ControlView(control: item) }
            }
        case "h":
            HStack(spacing: 8) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in ControlView(control: item) }
            }
        default:
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in ControlView(control: item, wide: true) }
            }
        }
    }
}

/// One of a set of pictures to pick from (the app's icon), the chosen one ringed in the Space's color.
struct IconChoice: View {
    @EnvironmentObject var model: PageModel
    let control: JSON

    var body: some View {
        let size = CGFloat(control.int("size") ?? 32)
        Button { model.act("press", ["id": control.controlId]) } label: {
            Group {
                if let image = picture(control.str("png")) {
                    Image(nsImage: image).resizable().interpolation(.high)
                } else {
                    Color.clear
                }
            }
            .frame(width: size, height: size)
            .padding(5)
            .background(RoundedRectangle(cornerRadius: 10).strokeBorder(control.flag("checked") ? AnyShapeStyle(.tint)
                                                                        : AnyShapeStyle(.clear), lineWidth: 2.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(control.str("text"))
    }
}

/// A text field: what's typed goes over when Return is pressed or it loses focus (not each key).
struct TextControl: View {
    @EnvironmentObject var model: PageModel
    let control: JSON
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        Group {
            if control.flag("secure") {
                SecureField("", text: $text, prompt: Text(control.str("placeholder")))
            } else {
                TextField("", text: $text, prompt: Text(control.str("placeholder")))
            }
        }
        .textFieldStyle(.roundedBorder)
        .labelsHidden()
        .focused($focused)
        .disabled(control.flag("readonly"))
        .onAppear { text = control.str("text") }
        .onChange(of: control.str("text")) { new in if !focused { text = new } }
        .onSubmit { send(finished: true, submit: true) }
        .onChange(of: focused) { now in if !now { send(finished: true) } }
        .task(id: text) {  // (as it's typed, a moment after the last key: a search box filters as you go)
            guard focused, text != control.str("text") else { return }
            try? await Task.sleep(nanoseconds: 250_000_000)
            if !Task.isCancelled { send(finished: false) }
        }
    }

    func send(finished: Bool, submit: Bool = false) {
        guard text != control.str("text") || finished else { return }
        model.act("text", ["id": control.controlId, "value": text, "finished": finished, "submit": submit])
    }
}

struct TextAreaControl: View {
    @EnvironmentObject var model: PageModel
    let control: JSON
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        TextEditor(text: $text)
            .font(.body.monospaced())
            .frame(minHeight: 90)
            .focused($focused)
            .disabled(control.flag("readonly"))
            .onAppear { text = control.str("text") }
            .onChange(of: control.str("text")) { new in if !focused { text = new } }
            .onChange(of: focused) { now in
                if !now && text != control.str("text") {
                    model.act("text", ["id": control.controlId, "value": text])
                }
            }
    }
}

/// A list to pick from (profiles, permissions…): a click selects, a double-click opens.
struct ListControl: View {
    @EnvironmentObject var model: PageModel
    let control: JSON

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(Array(control.list("rows").enumerated()), id: \.offset) { index, row in
                Text(row.str("text"))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 8).padding(.vertical, 5)
                    .foregroundStyle(row.flag("selected") ? Color.white : Color.primary)
                    .background(RoundedRectangle(cornerRadius: 6).fill(row.flag("selected") ? AnyShapeStyle(.tint)
                                                                       : AnyShapeStyle(.clear)))
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) { open(index) }
                    .onTapGesture { select(index) }
                    // (VoiceOver: a row it can choose, and open)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(row.str("text"))
                    .accessibilityAddTraits(row.flag("selected") ? [.isButton, .isSelected] : .isButton)
                    .accessibilityAction { select(index) }
                    .accessibilityAction(named: "Open") { open(index) }
            }
        }
        // The keyboard: Tab to the list, ↑ ↓ to choose a row, Return to open it (macOS 14 and later)
        .focusable()
        .modifier(ListKeys(step: { step($0) }, open: { if let at = selected { open(at) } }))
        .accessibilityElement(children: .contain)
    }

    private var rows: [JSON] { control.list("rows") }
    private var selected: Int? { rows.firstIndex { $0.flag("selected") } }

    // (its words go too: the list can have changed since it was drawn, and Python finds the row by them then)
    private func text(_ index: Int) -> String { rows.indices.contains(index) ? rows[index].str("text") : "" }
    private func select(_ index: Int) {
        model.act("select_row", ["id": control.controlId, "value": index, "text": text(index)])
    }
    private func open(_ index: Int) {
        model.act("select_row", ["id": control.controlId, "value": index, "text": text(index), "open": true])
    }

    private func step(_ by: Int) {
        guard !rows.isEmpty else { return }
        select(Swift.min(Swift.max((selected ?? (by > 0 ? -1 : rows.count)) + by, 0), rows.count - 1))
    }
}

private struct ListKeys: ViewModifier {
    let step: (Int) -> Void
    let open: () -> Void

    func body(content: Content) -> some View {
        if #available(macOS 14.0, *) {
            content
                .onKeyPress(.upArrow) { step(-1); return .handled }
                .onKeyPress(.downArrow) { step(1); return .handled }
                .onKeyPress(.return) { open(); return .handled }
        } else {
            content
        }
    }
}

/// A shortcut: click, then type it. Esc leaves it as it was; Delete on its own removes it.
struct KeysControl: View {
    @EnvironmentObject var model: PageModel
    let control: JSON
    @State private var recording = false
    @State private var monitor: Any?
    @State private var window: NSWindow?  // (the window it's recording in: keys typed in any other aren't its)

    static let named: [UInt16: String] = [
        123: "left", 124: "right", 125: "down", 126: "up", 36: "return", 76: "return", 48: "tab", 49: "space",
        53: "escape", 51: "delete", 117: "forwarddelete", 115: "home", 119: "end", 116: "pageup", 121: "pagedown",
        122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7", 100: "F8", 101: "F9", 109: "F10",
        103: "F11", 111: "F12"]

    var body: some View {
        Button(recording ? "Type a shortcut…" : control.str("text")) { recording ? stop() : start() }
            .font(.callout.monospaced())
            .onDisappear { stop() }
    }

    func start() {
        recording = true
        window = NSApp.keyWindow
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            // Typed in another window (the browser's, ⌘T, say): that window's key, as ever, and recording stops
            guard let window, event.window === window else {
                stop()
                return event
            }
            let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            let key = KeysControl.named[event.keyCode] ?? (event.charactersIgnoringModifiers ?? "").lowercased()
            if key == "escape" && flags.intersection([.command, .option, .control, .shift]).isEmpty {
                stop()
                return nil
            }
            model.act("keys", ["id": control.controlId, "key": key, "cmd": flags.contains(.command),
                               "alt": flags.contains(.option), "ctrl": flags.contains(.control),
                               "shift": flags.contains(.shift)])
            stop()
            return nil  // (the menu item that has it now doesn't fire)
        }
    }

    func stop() {
        recording = false
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        window = nil
    }
}
