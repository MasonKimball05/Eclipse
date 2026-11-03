// A small form, as a sheet on the browser window (or a window of its own): Space Rules, a recipe, a Boost. Python
// describes it (browser/forms.py): sections of fields, each with an id, and the buttons along the bottom. Nothing
// goes back while you fill it in (unless it's "live": then each change, as {"name": "changed", "values"}); a button sends its id with every field's value ({"name": "button", "id": …,
// "values": {…}}), and Python closes the form, or sends an "error" to show above the buttons. Closing the window
// otherwise is Cancel.
//
// Fields ("kind"): text, secret (typed hidden), toggle, menu (options [{value, label}]; "style": "segmented"), time
// ("HH:mm"), days (seven toggles, value the indexes on, Monday first), lines and code (several lines; code in a
// fixed-width font), list (lines you can take out), folder (a path, with Choose…), color (the system's color well),
// colors (one to "max" wells), swatches (a few colors that set a "target" field), slider ("min"…"max"), choice (a
// grid of pictures, from "icons_add", or emoji to pick one from), action (a button: {"name":
// "field", "id", "values"} to Python, which can fill fields in with "set"), and hint (a note). A secret field can be
// "shown_by" a toggle. A block is text to read and select. Any field can be "enabled_by" a toggle's id,
// or "shown_when" / "enabled_when" a menu says one of some values ({"id": the menu, "is": [values]}).

import AppKit
import SwiftUI

@MainActor
final class FormValues: ObservableObject {
    @Published var text: [String: String] = [:] { didSet { changed() } }
    @Published var flag: [String: Bool] = [:] { didSet { changed() } }
    @Published var days: [String: Set<Int>] = [:] { didSet { changed() } }
    @Published var time: [String: Date] = [:] { didSet { changed() } }
    @Published var list: [String: [String]] = [:] { didSet { changed() } }
    @Published var number: [String: Double] = [:] { didSet { changed() } }
    /// A live form's: told of each change you make (not of what Python fills in).
    var onChange: (() -> Void)?
    private var loading = false

    private func changed() {
        if !loading { onChange?() }
    }

    func load(_ fields: [JSON]) {
        loading = true
        defer { loading = false }
        for field in fields {
            let id = field.str("id"), value = field["value"]
            switch field.str("kind") {
            case "text", "secret", "menu", "lines", "code", "folder", "color", "choice":
                text[id] = value as? String ?? ""
            case "toggle": flag[id] = value as? Bool ?? false
            case "days": days[id] = Set((value as? [NSNumber] ?? []).map(\.intValue))
            case "time": time[id] = Self.date(value as? String ?? "09:00")
            case "list", "colors", "gradient": list[id] = value as? [String] ?? []
            case "slider": number[id] = (value as? NSNumber)?.doubleValue ?? 0
            default: break
            }
        }
    }

    /// As set from outside (the tests): {id: value}, as Python would send a field's value.
    func set(_ values: JSON, fields: [JSON]) {
        load(fields.compactMap { field in values[field.str("id")].map { field.merging(["value": $0]) { _, new in new } } })
    }

    func collect(_ fields: [JSON]) -> JSON {
        var out: JSON = [:]
        for field in fields {
            let id = field.str("id")
            switch field.str("kind") {
            case "text", "secret", "menu", "lines", "code", "folder", "color", "choice": out[id] = text[id] ?? ""
            case "toggle": out[id] = flag[id] ?? false
            case "days": out[id] = (days[id] ?? []).sorted()
            case "time": out[id] = Self.text(time[id] ?? Date())
            case "list", "colors", "gradient": out[id] = list[id] ?? []
            case "slider": out[id] = number[id] ?? 0
            default: break
            }
        }
        return out
    }

    private static let clock: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        return f
    }()

    static func date(_ text: String) -> Date { clock.date(from: text) ?? clock.date(from: "09:00")! }
    static func text(_ date: Date) -> String { clock.string(from: date) }
}

struct FormView: View {
    @ObservedObject var model: PageModel
    @StateObject private var values = FormValues()
    @State private var confirming: JSON?
    @State private var inputs = 0
    @State private var pendingChange: DispatchWorkItem?

    private var sections: [JSON] { model.state["sections"] as? [JSON] ?? [] }
    private var fields: [JSON] { sections.flatMap { $0.list("fields") } }
    private var buttons: [JSON] { model.state["buttons"] as? [JSON] ?? [] }

    var body: some View {
        VStack(spacing: 0) {
            if !model.string("heading").isEmpty {
                Text(model.string("heading"))
                    .font(.headline)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20)
                    .padding(.top, model.bool("sheet") ? 18 : 30)
            }
            Form {
                ForEach(Array(sections.enumerated()), id: \.offset) { _, section in
                    Section {
                        ForEach(Array(section.list("fields").enumerated()), id: \.offset) { _, field in
                            if shown(field) {
                                FieldView(field: field, values: values, images: model.images) { id in
                                    model.act("field", ["id": id, "values": values.collect(fields)])
                                }
                                    .disabled(!enabled(field))
                            }
                        }
                    } header: {
                        if !section.str("header").isEmpty { Text(section.str("header")) }
                    } footer: {
                        if !section.str("footer").isEmpty {
                            Text(section.str("footer")).font(.footnote).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            .formStyle(.grouped)
            if !model.string("error").isEmpty {
                Text(model.string("error"))
                    .font(.callout)
                    .foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20)
            }
            if !buttons.isEmpty { bar }
        }
        .onAppear {
            values.load(fields)
            if model.bool("live") {  // (each change as you make it: the theme editor previews on the window)
                values.onChange = { [weak values] in
                    pendingChange?.cancel()
                    let work = DispatchWorkItem {
                        if let values { model.act("changed", ["values": values.collect(fields)]) }
                    }
                    pendingChange = work
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.03, execute: work)
                }
            }
            takeInput()
        }
        .onDisappear {  // (the change hook holds this view: let go of it, so the form's freed with its window)
            values.onChange = nil
            pendingChange?.cancel()
        }
        .onChange(of: (model.state["test_input"] as? JSON)?["n"] as? Int) { _ in takeInput() }
        .onChange(of: (model.state["set"] as? JSON)?["n"] as? Int) { _ in
            // (Python filling fields in: a suggested password, say)
            values.set((model.state["set"] as? JSON)?["values"] as? JSON ?? [:], fields: fields)
        }
        .confirmationDialog(confirming?.str("confirm") ?? "", isPresented: Binding(
            get: { confirming != nil }, set: { if !$0 { confirming = nil } })) {
            if let button = confirming {
                Button(button.str("label"), role: .destructive) { press(button.str("id"), confirmed: true) }
            }
        }
    }

    /// The tests: fields filled in, then a button pressed, as a person would (each input once, even one that came
    /// before the form appeared).
    private func takeInput() {
        guard let input = model.state["test_input"] as? JSON, let n = input["n"] as? Int, n > inputs else { return }
        inputs = n
        values.set(input["values"] as? JSON ?? [:], fields: fields)
        if model.bool("live") { values.onChange?() }  // (as if typed: a live form hears of it)
        if let id = input["press"] as? String, !id.isEmpty { press(id, confirmed: true) }
    }

    private var bar: some View {
        HStack(spacing: 10) {
            ForEach(Array(buttons.filter { $0.str("role") == "destructive" }.enumerated()), id: \.offset) { _, button in
                buttonView(button)
            }
            Spacer()
            ForEach(Array(buttons.filter { $0.str("role") != "destructive" }.enumerated()), id: \.offset) { _, button in
                buttonView(button)
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 6)
        .padding(.bottom, 16)
    }

    @ViewBuilder private func buttonView(_ button: JSON) -> some View {
        let role = button.str("role")
        let view = Button(button.str("label"), role: role == "destructive" ? .destructive : role == "cancel" ? .cancel : nil) {
            press(button.str("id"))
        }
        switch role {
        case "default": view.keyboardShortcut(.defaultAction)
        case "cancel": view.keyboardShortcut(.cancelAction)
        default: view
        }
    }

    private func press(_ id: String, confirmed: Bool = false) {
        guard let button = buttons.first(where: { $0.str("id") == id }) else { return }
        if !confirmed && !button.str("confirm").isEmpty {
            confirming = button
            return
        }
        if button.str("role") == "cancel" {
            let window = model.window
            DispatchQueue.main.async { window?.close() }  // (after this update: see eclipse_native_close)
            return
        }
        model.act("button", ["id": id, "values": values.collect(fields)])
    }

    private func enabled(_ field: JSON) -> Bool {
        let by = field.str("enabled_by")
        return (by.isEmpty || values.flag[by] == true) && matches(field["enabled_when"] as? JSON)
    }

    /// "shown_when": {"id": a menu's id, "is": [its values]}: only there while that menu says one of them.
    private func shown(_ field: JSON) -> Bool { matches(field["shown_when"] as? JSON) }

    private func matches(_ when: JSON?) -> Bool {
        guard let when else { return true }
        return (when["is"] as? [String] ?? []).contains(values.text[when.str("id")] ?? "")
    }
}

struct FieldView: View {
    let field: JSON
    @ObservedObject var values: FormValues
    var images: [String: NSImage] = [:]
    var act: (String) -> Void = { _ in }

    /// A new color to start from: the last one, turned 60° round the wheel (as the Qt theme editor did).
    static func nextColor(_ hex: String) -> String {
        guard let color = NSColor(Color(hex: hex) ?? .purple).usingColorSpace(.sRGB) else { return hex }
        var h: CGFloat = 0, s: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        color.getHue(&h, saturation: &s, brightness: &b, alpha: &a)
        return Color(nsColor: NSColor(hue: (h + 1 / 6).truncatingRemainder(dividingBy: 1), saturation: s, brightness: b,
                                      alpha: 1)).hexString
    }

    private var id: String { field.str("id") }
    private var label: String { field.str("label") }

    var body: some View {
        switch field.str("kind") {
        case "text":
            TextField(label, text: binding(\.text, ""), prompt: Text(field.str("placeholder")))
        case "secret":
            if values.flag[field.str("shown_by")] == true {  // ("Show password": the same text, readable)
                TextField(label, text: binding(\.text, ""), prompt: Text(field.str("placeholder")))
            } else {
                SecureField(label, text: binding(\.text, ""), prompt: Text(field.str("placeholder")))
            }
        case "action":
            LabeledContent(label) {
                Button(field.str("title")) { act(field.str("id")) }
            }
        case "toggle":
            Toggle(label, isOn: binding(\.flag, false))
        case "menu":
            let picker = Picker(label, selection: binding(\.text, "")) {
                ForEach(Array(field.list("options").enumerated()), id: \.offset) { _, option in
                    Text(option.str("label")).tag(option.str("value"))
                }
            }
            if field.str("style") == "segmented" {
                picker.pickerStyle(.segmented)
            } else {
                picker
            }
        case "time":
            DatePicker(label, selection: binding(\.time, Date()), displayedComponents: .hourAndMinute)
        case "days":
            LabeledContent(label) {
                HStack(spacing: 4) {
                    ForEach(Array((field["labels"] as? [String] ?? []).enumerated()), id: \.offset) { index, day in
                        Toggle(day, isOn: Binding(
                            get: { values.days[id, default: []].contains(index) },
                            set: { on in
                                if on { values.days[id, default: []].insert(index) } else { values.days[id]?.remove(index) }
                            }))
                        .toggleStyle(DayStyle())
                        .help((field["names"] as? [String])?[safe: index] ?? day)
                    }
                }
            }
        case "lines", "code":
            VStack(alignment: .leading, spacing: 6) {
                if !label.isEmpty { Text(label) }
                ZStack(alignment: .topLeading) {
                    TextEditor(text: binding(\.text, ""))
                        .font(field.str("kind") == "code" ? .system(size: 12, design: .monospaced) : .body)
                        .scrollContentBackground(.hidden)
                        .padding(4)
                    if values.text[id, default: ""].isEmpty {
                        Text(field.str("placeholder"))
                            .font(field.str("kind") == "code" ? .system(size: 12, design: .monospaced) : .body)
                            .foregroundStyle(.tertiary)
                            .padding(.horizontal, 9).padding(.vertical, 4)
                            .allowsHitTesting(false)
                    }
                }
                .frame(height: (field["height"] as? NSNumber).map { CGFloat($0.doubleValue) } ?? 90)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color(nsColor: .textBackgroundColor)))
                .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color(nsColor: .separatorColor)))
            }
        case "list":
            VStack(alignment: .leading, spacing: 6) {
                if !label.isEmpty { Text(label) }
                let items = values.list[id, default: []]
                if items.isEmpty {
                    Text(field.str("placeholder")).foregroundStyle(.secondary)
                }
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack {
                        Text(item).font(.system(size: 12, design: .monospaced)).lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Button {
                            if values.list[id]?.indices.contains(index) == true { values.list[id]?.remove(at: index) }
                        } label: {
                            Image(systemName: "minus.circle.fill").foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                        .help(field.str("remove_help"))
                    }
                }
            }
        case "folder":
            LabeledContent(label) {
                HStack {
                    Text(((values.text[id] ?? "") as NSString).abbreviatingWithTildeInPath)
                        .lineLimit(1).truncationMode(.middle).foregroundStyle(.secondary)
                    Button("Choose…") { chooseFolder() }
                }
            }
        case "color":  // (the system's color well and panel)
            ColorPicker(label, selection: Binding(
                get: { Color(hex: values.text[id] ?? "") ?? .gray },
                set: { values.text[id] = $0.hexString }), supportsOpacity: false)
        case "colors":  // (one to "max" colors, a well each; "+" adds one, a right-click takes one out)
            LabeledContent(label) {
                HStack(spacing: 8) {
                    let colors = values.list[id] ?? []
                    ForEach(Array(colors.enumerated()), id: \.offset) { index, hex in
                        ColorPicker("", selection: Binding(
                            get: { Color(hex: hex) ?? .gray },
                            set: { new in
                                var now = values.list[id] ?? []
                                if now.indices.contains(index) { now[index] = new.hexString }
                                values.list[id] = now
                            }), supportsOpacity: false)
                            .labelsHidden()
                            .contextMenu {
                                if colors.count > 1 {
                                    Button("Remove Color") {
                                        if values.list[id]?.indices.contains(index) == true { values.list[id]?.remove(at: index) }
                                    }
                                }
                            }
                    }
                    if colors.count < ((field["max"] as? NSNumber)?.intValue ?? 3) {
                        Button { values.list[id] = colors + [Self.nextColor(colors.last ?? "#8b7cf6")] } label: {
                            Image(systemName: "plus")
                        }
                        .help("Add a color")
                    }
                }
            }
        case "gradient":  // (up to "max" colors as dots on a field of color: GradientField.swift)
            GradientField(id: id, max: (field["max"] as? NSNumber)?.intValue ?? 3, values: values)
        case "swatches":  // (a color to pick from a few: sets the "target" field, a color or the first of colors)
            LabeledContent(label) {
                HStack(spacing: 6) {
                    ForEach(Array(field.list("options").enumerated()), id: \.offset) { _, option in
                        let color = option.str("value")
                        let on = (values.text[field.str("target")] ?? values.list[field.str("target")]?.first ?? "")
                            .lowercased() == color.lowercased()
                        Button {
                            let target = field.str("target")
                            if values.list[target] != nil, var list = values.list[target], !list.isEmpty {
                                list[0] = color
                                values.list[target] = list
                            } else {
                                values.text[target] = color
                            }
                        } label: {
                            Circle().fill(Color(hex: color) ?? .gray).frame(width: 18, height: 18)
                                .overlay(Circle().stroke(Color.primary.opacity(on ? 0.8 : 0), lineWidth: 2).padding(-3))
                        }
                        .buttonStyle(.plain)
                        .help(option.str("label"))
                    }
                }
            }
        case "slider":
            let low = (field["min"] as? NSNumber)?.doubleValue ?? 0, high = (field["max"] as? NSNumber)?.doubleValue ?? 1
            LabeledContent(label) {
                Slider(value: Binding(get: { values.number[id] ?? 0 }, set: { values.number[id] = $0 }),
                       in: min(low, high)...max(low, high, min(low, high) + 0.001))
            }
        case "choice":  // (a grid to pick one from: pictures or emoji, the one chosen marked)
            let options = field.list("options")
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(30), spacing: 4),
                                     count: (field["columns"] as? NSNumber)?.intValue ?? 8), spacing: 4) {
                // (plain cells, not a button and tooltip each: a hundred of those took most of the Space editor's
                // time to open)
                ForEach(Array(options.enumerated()), id: \.offset) { _, option in
                    let on = values.text[id] == option.str("value")
                    Group {
                        if let image = images[option.str("image")] {
                            Image(nsImage: image).resizable().frame(width: 18, height: 18)
                        } else {
                            Text(option.str("text")).font(.system(size: 18))
                        }
                    }
                    .frame(width: 30, height: 30)
                    .background(RoundedRectangle(cornerRadius: 7).fill(on ? Color.accentColor.opacity(0.25) : .clear))
                    .contentShape(Rectangle())
                    .onTapGesture { values.text[id] = option.str("value") }
                    .accessibilityElement()
                    .accessibilityLabel(option.str("label"))
                    .accessibilityAddTraits(on ? [.isButton, .isSelected] : .isButton)
                }
            }
        case "hint":
            Text(field.str("text")).font(.callout).foregroundStyle(.secondary)
        case "block":  // (text to read and select, not change: a page's changes, say)
            ScrollView {
                Text(field.str("text"))
                    .font(.system(size: 12, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(6)
            }
            .frame(height: (field["height"] as? NSNumber).map { CGFloat($0.doubleValue) } ?? 240)
            .background(RoundedRectangle(cornerRadius: 6).fill(Color(nsColor: .textBackgroundColor)))
        default:
            EmptyView()
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Choose"
        panel.directoryURL = URL(fileURLWithPath: values.text[id] ?? NSHomeDirectory())
        panel.begin { response in
            if response == .OK, let url = panel.url { values.text[id] = url.path }
        }
    }

    private func binding<T>(_ path: ReferenceWritableKeyPath<FormValues, [String: T]>, _ fallback: T) -> Binding<T> {
        Binding(get: { values[keyPath: path][id] ?? fallback }, set: { values[keyPath: path][id] = $0 })
    }
}

/// A day's toggle: a small rounded square, filled with the tint while it's on.
struct DayStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.medium))
            .frame(width: 28, height: 24)
            .foregroundStyle(configuration.isOn ? Color.white : Color.primary)
            .background(RoundedRectangle(cornerRadius: 6).fill(configuration.isOn ? AnyShapeStyle(.tint)
                                                                : AnyShapeStyle(.quaternary)))
            .contentShape(Rectangle())
            .onTapGesture { configuration.isOn.toggle() }
            .accessibilityAddTraits(configuration.isOn ? [.isButton, .isSelected] : .isButton)
    }
}

private extension Array {
    subscript(safe index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}

extension Color {
    /// "#rrggbb", as Python keeps colors.
    var hexString: String {
        guard let c = NSColor(self).usingColorSpace(.sRGB) else { return "#808080" }
        return String(format: "#%02x%02x%02x", Int(round(c.redComponent * 255)), Int(round(c.greenComponent * 255)),
                      Int(round(c.blueComponent * 255)))
    }
}
