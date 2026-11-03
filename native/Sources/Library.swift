// The Library (⌘Y), natively: its sections in a sidebar, the section's entries in a list grouped by day, with
// search. What it shows comes from browser/library_native.py; opening, removing and clearing are done there.

import AppKit
import SwiftUI

struct LibraryView: View {
    @ObservedObject var model: PageModel
    @State private var section: String?
    @State private var search = ""
    @State private var selection: Int?

    var sections: [JSON] { model.state["sections"] as? [JSON] ?? [] }
    var entries: [JSON] { model.state["entries"] as? [JSON] ?? [] }
    var icons: [String: String] { model.state["icons"] as? [String: String] ?? [:] }

    /// Consecutive entries of the same day, for the day headers (one group, no header, where there are no days).
    var groups: [(day: String, entries: [JSON])] {
        guard model.bool("grouped") else { return [("", entries)] }
        var groups: [(day: String, entries: [JSON])] = []
        for entry in entries {
            if groups.last?.day == entry.str("day") {
                groups[groups.count - 1].entries.append(entry)
            } else {
                groups.append((entry.str("day"), [entry]))
            }
        }
        return groups
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                ForEach(sections, id: \.self.sectionKey) { item in
                    Label(item.str("title"), systemImage: item.str("symbol")).tag(item.str("key"))
                }
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 210, max: 260)
        } detail: {
            Group {
                if entries.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: currentSymbol).font(.system(size: 40, weight: .light))
                            .foregroundStyle(.tertiary)
                        Text(model.string("empty")).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                    .padding(40)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(selection: $selection) {
                        ForEach(Array(groups.enumerated()), id: \.offset) { _, group in
                            Section(group.day) {
                                ForEach(group.entries, id: \.self.entryId) { entry in
                                    EntryRow(entry: entry, icon: icons[entry.str("icon")])
                                        .tag(entry.int("id") ?? 0)
                                        .contextMenu { menu(for: entry) }
                                }
                            }
                        }
                    }
                    .listStyle(.inset(alternatesRowBackgrounds: false))
                    .contextMenu(forSelectionType: Int.self) { _ in } primaryAction: { ids in
                        if let id = ids.first { model.act("open", ["id": id]) }  // (a double-click, or Return)
                    }
                }
            }
            .searchable(text: $search, placement: .toolbar, prompt: "Search")
            .toolbar {
                if !model.string("clear").isEmpty {
                    ToolbarItem(placement: .primaryAction) {
                        Button(model.string("clear")) { model.act("clear") }.disabled(!model.bool("can_clear"))
                    }
                }
            }
            .navigationTitle(currentTitle)
        }
        .onAppear {
            section = model.string("section", "history")
            model.window?.title = currentTitle
        }
        .onChange(of: model.string("section")) { section = $0 }
        .onChange(of: section) { key in
            guard let key else { return }
            model.window?.title = sections.first { $0.str("key") == key }?.str("title") ?? "Library"
            if key != model.string("section") {
                model.merge(["section": key])
                search = ""
                selection = nil
                model.act("section", ["key": key])
            }
        }
        .task(id: search) {  // (a moment after the last key, as Settings' shortcut search)
            guard search != model.string("query") else { return }
            try? await Task.sleep(nanoseconds: 200_000_000)
            if !Task.isCancelled { model.act("search", ["value": search]) }
        }
    }

    var currentTitle: String { sections.first { $0.str("key") == (section ?? "history") }?.str("title") ?? "Library" }
    var currentSymbol: String { sections.first { $0.str("key") == (section ?? "history") }?.str("symbol") ?? "clock" }

    @ViewBuilder func menu(for entry: JSON) -> some View {
        let id = entry.int("id") ?? 0
        Button("Open") { model.act("open", ["id": id]) }
        if !entry.flag("note") {
            Button("Open in Little Window") { model.act("little", ["id": id]) }
        }
        if entry.flag("web") {
            Button("Copy Link") { model.act("copy", ["id": id]) }
        }
        Divider()
        Button(entry.flag("file") ? "Delete…" : "Remove", role: .destructive) { model.act("remove", ["id": id]) }
    }
}

private extension Dictionary where Key == String, Value == Any {
    var sectionKey: String { str("key") }
    var entryId: Int { int("id") ?? 0 }
}

struct EntryRow: View {
    let entry: JSON
    let icon: String?

    var body: some View {
        HStack(spacing: 10) {
            Group {
                if let icon, let image = picture(icon) {
                    Image(nsImage: image).resizable().interpolation(.high)
                } else {
                    Image(systemName: "globe").foregroundStyle(.secondary)
                }
            }
            .frame(width: 18, height: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(entry.str("title")).lineLimit(1).truncationMode(.tail)
                if !entry.str("subtitle").isEmpty {
                    Text(entry.str("subtitle")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer(minLength: 12)
            Text(entry.str("when")).font(.caption).foregroundStyle(.secondary).monospacedDigit()
        }
        .padding(.vertical, 2)
        .help(entry.str("url"))
    }
}
