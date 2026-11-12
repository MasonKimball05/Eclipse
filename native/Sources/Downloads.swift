// Downloads, natively: a link to save with yt-dlp (what it found, and how to save it), then the downloads with
// their progress. A finished one opens with a double-click, shows in Quick Look with the space bar, and drags out
// into Finder or any app. What it shows comes from browser/downloads_native.py; what's done goes back there.

import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct DownloadsView: View {
    @ObservedObject var model: PageModel
    @State private var link = ""
    @State private var selection: Int?

    var items: [JSON] { model.state["items"] as? [JSON] ?? [] }
    var options: JSON? { model.state["options"] as? JSON }

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    TextField("", text: $link,
                              prompt: Text("Paste a video or playlist link (YouTube, Vimeo, SoundCloud, …)"))
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { model.act("url", ["value": link, "submit": true]) }
                    Button("Get Info") { model.act("url", ["value": link, "submit": true]) }
                        .disabled(!model.bool("can_fetch") || link.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if !model.string("message").isEmpty {
                    Text(model.string("message")).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let options { OptionsCard(options: options) }
            }
            .padding(16)
            Divider()
            if items.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "arrow.down.circle").font(.system(size: 40, weight: .light)).foregroundStyle(.tertiary)
                    Text("No downloads yet").font(.headline)
                    Text("Files you download appear here. Paste a video link above to save it with yt-dlp.")
                        .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                .padding(32)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(selection: $selection) {
                    ForEach(items, id: \.self.itemId) { item in
                        DownloadRowView(item: item).tag(item.itemId)
                            .contextMenu { menu(for: item) }
                    }
                }
                .listStyle(.inset(alternatesRowBackgrounds: false))
                .contextMenu(forSelectionType: Int.self) { _ in } primaryAction: { ids in
                    if let id = ids.first { model.act("open", ["id": id]) }
                }
                .modifier(SpaceForQuickLook { if let id = selection { model.act("look", ["id": id]) } })
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button("Clear Finished") { model.act("clear") }
                    .disabled(!items.contains { $0.str("state") != "running" })
                Button { model.act("settings") } label: { Image(systemName: "gearshape") }
                    .help("Download settings: folder, file names, default formats")
            }
        }
        .onAppear { link = model.string("url") }
        .onChange(of: model.string("url")) { link = $0 }
    }

    @ViewBuilder func menu(for item: JSON) -> some View {
        let id = item.itemId
        if !item.str("path").isEmpty {
            Button("Open") { model.act("open", ["id": id]) }
            Button("Quick Look") { model.act("look", ["id": id]) }
            Button(model.string("finder")) { model.act("reveal", ["id": id]) }
            Button("Copy") { model.act("copy", ["id": id]) }
            Divider()
        }
        if item.str("state") == "running" {
            if item.flag("can_pause") {
                Button(item.flag("paused") ? "Resume" : "Pause") { model.act("pause", ["id": id]) }
            }
            Button("Cancel") { model.act("cancel", ["id": id]) }
        } else {
            Button("Remove from List") { model.act("remove", ["id": id]) }
        }
    }
}

private extension Dictionary where Key == String, Value == Any {
    var itemId: Int { int("id") ?? 0 }
}

/// What yt-dlp found at the link, and how to save it.
struct OptionsCard: View {
    @EnvironmentObject var model: PageModel
    let options: JSON
    @State private var subs = ""

    func choice(_ field: String, _ labels: String) -> some View {
        Picker("", selection: Binding(get: { options.int(field) ?? 0 }, set: { model.act(field, ["value": $0]) })) {
            ForEach(Array((options[labels] as? [String] ?? []).enumerated()), id: \.offset) { i, label in
                Text(label).tag(i)
            }
        }
        .labelsHidden()
        .fixedSize()
    }

    var body: some View {
        let video = options.flag("video")
        VStack(alignment: .leading, spacing: 10) {
            Text(options.str("title")).font(.headline).fixedSize(horizontal: false, vertical: true)
            if !options.str("meta").isEmpty {
                Text(options.str("meta")).font(.callout).foregroundStyle(.secondary)
            }
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 8) {
                GridRow {
                    Text("Type").foregroundStyle(.secondary).gridColumnAlignment(.trailing)
                    Picker("", selection: Binding(get: { video ? "video" : "audio" },
                                                  set: { model.act("kind", ["value": $0]) })) {
                        Text("Video").tag("video")
                        Text("Audio only").tag("audio")
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                    .disabled(!options.flag("video_enabled", true))
                }
                if video {
                    GridRow { Text("Format").foregroundStyle(.secondary); choice("video_format", "video_formats") }
                    GridRow { Text("Quality").foregroundStyle(.secondary); choice("quality", "qualities") }
                } else {
                    GridRow { Text("Format").foregroundStyle(.secondary); choice("audio_format", "audio_formats") }
                }
                GridRow {
                    Text("Subtitles").foregroundStyle(.secondary)
                    TextField("", text: $subs, prompt: Text("Language codes, e.g. en, es (blank for none)"))
                        .textFieldStyle(.roundedBorder).frame(maxWidth: 280)
                        .onSubmit { model.act("subs", ["value": subs]) }
                        .onChange(of: subs) { model.act("subs", ["value": $0]) }
                }
                if video {
                    GridRow {
                        Color.clear.frame(width: 1, height: 1)
                        Toggle("Embed in the video", isOn: Binding(get: { options.flag("embed") },
                                                                   set: { model.act("embed", ["value": $0]) }))
                    }
                }
                GridRow {
                    Text("Save to").foregroundStyle(.secondary)
                    Button { model.act("folder") } label: {
                        Label(options.str("folder_name"), systemImage: "folder").lineLimit(1).truncationMode(.middle)
                    }
                    .help(options.str("folder"))
                }
            }
            HStack {
                Spacer()
                Button(options.str("download").isEmpty ? "Download" : options.str("download")) { model.act("download") }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            }
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 10).fill(.quaternary.opacity(0.5)))
        .onAppear { subs = options.str("subs") }
        .onChange(of: options.str("subs")) { if $0 != subs { subs = $0 } }  // (reset from Eclipse: Settings saved, say)
    }
}

struct DownloadRowView: View {
    @EnvironmentObject var model: PageModel
    let item: JSON

    var body: some View {
        let running = item.str("state") == "running"
        let path = item.str("path")
        HStack(alignment: .top, spacing: 10) {
            Group {
                if !path.isEmpty {
                    Image(nsImage: NSWorkspace.shared.icon(forFile: path)).resizable()
                } else {
                    Image(systemName: item.str("symbol")).font(.title3).foregroundStyle(.secondary)
                }
            }
            .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 4) {
                Text(item.str("title")).lineLimit(1).truncationMode(.middle)
                if running {
                    if let fraction = (item["fraction"] as? NSNumber)?.doubleValue {
                        ProgressView(value: fraction).controlSize(.small)
                    } else {
                        ProgressView().progressViewStyle(.linear).controlSize(.small)
                    }
                }
                Text(item.str("status")).font(.caption)
                    .foregroundStyle(item.str("state") == "failed" ? AnyShapeStyle(.red) : AnyShapeStyle(.secondary))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            if running {
                if item.flag("can_pause") {
                    Button { model.act("pause", ["id": item.itemId]) } label: {
                        Image(systemName: item.flag("paused") ? "play.circle.fill" : "pause.circle.fill")
                    }
                    .buttonStyle(.borderless).help(item.flag("paused") ? "Resume" : "Pause")
                }
                Button { model.act("cancel", ["id": item.itemId]) } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.borderless).help("Cancel")
            } else if !path.isEmpty {
                Button { model.act("reveal", ["id": item.itemId]) } label: { Image(systemName: "magnifyingglass.circle.fill") }
                    .buttonStyle(.borderless).help(model.string("finder"))
            }
        }
        .padding(.vertical, 4)
        .foregroundStyle(.primary)
        .onDrag {  // (a finished file, out into Finder, Mail, Messages…)
            path.isEmpty ? NSItemProvider() : NSItemProvider(contentsOf: URL(fileURLWithPath: path)) ?? NSItemProvider()
        }
        .help(path.isEmpty ? item.str("title") : "Drag it into another app, double-click to open it, or press space for Quick Look")
    }
}

/// The space bar on the list: Quick Look, as in Finder (macOS 14 and later: before, the context menu has it).
struct SpaceForQuickLook: ViewModifier {
    let look: () -> Void

    func body(content: Content) -> some View {
        if #available(macOS 14.0, *) {
            content.onKeyPress(.space) {
                look()
                return .handled
            }
        } else {
            content
        }
    }
}
