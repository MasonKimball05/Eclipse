// The Spaces: one page each (swipe between them), tinted with the Space's look and in its light or dark, with
// Favorites on top and the pinned tabs and folders below. Search finds pinned tabs in every Space.

import EclipseKit
import SafariServices
import SwiftUI

struct Page: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

struct SpacesView: View {
    @Environment(SyncStore.self) private var store
    let synced: Synced
    @State private var current: String?
    @State private var query = ""
    @State private var opened: Page?

    private var space: Space? { synced.spaces.first { $0.id == current } ?? synced.spaces.first }

    var body: some View {
        NavigationStack {
            content
                .background { SpaceBackground(space: query.isEmpty ? space : nil).ignoresSafeArea() }
                .navigationTitle(query.isEmpty ? title(space) : "Search")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .topBarTrailing) { menu } }
                .searchable(text: $query, prompt: "Pinned tabs")
                .refreshable { await store.refresh() }
                .environment(\.openPage) { opened = Page(url: $0) }
        }
        .tint(space.map { Color(hex: $0.color) } ?? .accentColor)
        .preferredColorScheme(scheme(space?.look.mode))
        .fullScreenCover(item: $opened) { SafariView(url: $0.url).ignoresSafeArea() }
        .onAppear { if current == nil { current = synced.spaces.first?.id } }
        .onChange(of: synced.spaces.map(\.id)) { _, ids in
            // (the Space you were on, deleted on a Mac: the first, where the page stayed put on another's tabs)
            if let on = current, !ids.contains(on) { current = ids.first }
        }
    }

    @ViewBuilder private var content: some View {
        if synced.spaces.isEmpty && synced.favorites.isEmpty {
            ContentUnavailableView {
                Label(store.waiting > 0 ? "Downloading from iCloud" : "Nothing Synced Yet", systemImage: "icloud")
            } description: {
                Text(store.waiting > 0 ? "Your Macs' Spaces are on their way."
                     : "Turn on Sync in Eclipse on your Mac: Settings → General → Sync.")
            }
        } else {
            // (the pages stay put under the search's results, so they come back as they were)
            pages.overlay {
                if !query.isEmpty {
                    SearchResults(synced: synced, query: query)
                        .background(Color(uiColor: .systemGroupedBackground))
                }
            }
        }
    }

    private var pages: some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 0) {
                ForEach(synced.spaces) { space in
                    SpacePage(space: space, favorites: synced.favorites)
                        .containerRelativeFrame(.horizontal)
                        .id(Optional(space.id))
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollPosition(id: $current)
        .scrollIndicators(.hidden)
        .ignoresSafeArea(.keyboard)
        .safeAreaInset(edge: .bottom) {
            if synced.spaces.count > 1 && query.isEmpty { switcher }
        }
    }

    /// A button for each Space (its emoji, or a dot of its color), the one you're on lit.
    private var switcher: some View {
        HStack(spacing: 6) {
            ForEach(synced.spaces) { space in
                let on = space.id == (current ?? synced.spaces.first?.id)
                Button {
                    withAnimation(.snappy) { current = space.id }
                } label: {
                    Group {
                        if let emoji = space.emoji {
                            Text(emoji).font(.system(size: 17))
                        } else {
                            Circle().fill(Color(hex: space.color)).frame(width: 12, height: 12)
                        }
                    }
                    .frame(width: 36, height: 36)
                    .background(on ? AnyShapeStyle(.background.opacity(0.85)) : AnyShapeStyle(.clear), in: Circle())
                    .opacity(on ? 1 : 0.6)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(space.name)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(5)
        .background(.ultraThinMaterial, in: Capsule())
        .padding(.bottom, 6)
    }

    private var menu: some View {
        Menu {
            if !store.devices.isEmpty {
                Section("Synced From") {
                    ForEach(store.devices) { device in
                        Text("\(device.name), \(device.written.formatted(.relative(presentation: .named)))")
                    }
                }
            }
            if synced.spaces.count > 1 {
                Section("Spaces") {
                    Picker("Space", selection: $current) {
                        ForEach(synced.spaces) { Text(title($0)).tag(Optional($0.id)) }
                    }
                }
            }
            Button("Refresh", systemImage: "arrow.clockwise") { store.reload() }
            Button("Choose Another Folder…", systemImage: "folder") { store.choosing = true }
            Button("Disconnect", systemImage: "xmark.circle", role: .destructive) { store.forget() }
        } label: {
            Image(systemName: store.loading ? "arrow.triangle.2.circlepath" : "ellipsis.circle")
        }
        .accessibilityLabel("More")
    }

    private func title(_ space: Space?) -> String {
        guard let space else { return "Eclipse" }
        return space.emoji.map { "\($0) \(space.name)" } ?? space.name
    }

    private func scheme(_ mode: String?) -> ColorScheme? {
        switch mode {
        case "light": return .light
        case "dark": return .dark
        default: return nil
        }
    }
}

struct SpacePage: View {
    let space: Space
    let favorites: [EclipseKit.Tab]

    var body: some View {
        List {
            if !favorites.isEmpty {
                Section {
                    FavoritesGrid(favorites: favorites, color: space.color)
                        .listRowInsets(EdgeInsets(top: 12, leading: 12, bottom: 12, trailing: 12))
                        .listRowBackground(Rectangle().fill(.thinMaterial))
                }
            }
            Section("Pinned") {
                if space.pinned.isEmpty {
                    Text("Nothing pinned in this Space")
                        .foregroundStyle(.secondary)
                        .listRowBackground(Rectangle().fill(.thinMaterial))
                }
                ForEach(space.pinned) { node in
                    NodeRow(node: node, color: space.color)
                        .listRowBackground(Rectangle().fill(.thinMaterial))
                }
            }
        }
        .scrollContentBackground(.hidden)
    }
}

struct FavoritesGrid: View {
    let favorites: [EclipseKit.Tab]
    let color: String
    @Environment(\.openPage) private var openPage

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 64), spacing: 10)], spacing: 10) {
            ForEach(favorites) { tab in
                Button {
                    if let url = URL(string: tab.url) { openPage(url) }
                } label: {
                    VStack(spacing: 5) {
                        SiteTile(url: tab.url, color: color, size: 32)
                        Text(tab.label)
                            .font(.caption2)
                            .lineLimit(1)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 4)
                    .frame(maxWidth: .infinity, minHeight: 64)
                    .background(.background.opacity(0.6), in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
                .contextMenu { TabMenu(tab: tab) }
            }
        }
    }
}

struct NodeRow: View {
    let node: Node
    let color: String

    var body: some View {
        switch node {
        case .tab(let tab):
            TabRow(tab: tab, color: color)
        case .folder(let folder):
            if let live = folder.live {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(folder.name)
                        Text("Live folder\(live["url"]?.string.map { " · " + siteHost($0) } ?? ""): fills itself in on your Mac")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                } icon: {
                    Image(systemName: "dot.radiowaves.up.forward")
                }
            } else {
                DisclosureGroup {
                    ForEach(folder.children) { NodeRow(node: $0, color: color) }
                } label: {
                    Label(folder.name, systemImage: "folder")
                }
            }
        }
    }
}

struct TabRow: View {
    let tab: EclipseKit.Tab
    let color: String
    var detail: String?
    @Environment(\.openPage) private var openPage

    var body: some View {
        Button {
            if let url = URL(string: tab.url) { openPage(url) }
        } label: {
            HStack(spacing: 12) {
                SiteTile(url: tab.url, color: color, size: 28)
                VStack(alignment: .leading, spacing: 2) {
                    Text(tab.label).lineLimit(1)
                    Text(detail ?? siteHost(tab.url))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .contextMenu { TabMenu(tab: tab) }
    }
}

struct TabMenu: View {
    let tab: EclipseKit.Tab
    @Environment(\.openURL) private var openURL

    var body: some View {
        if let url = URL(string: tab.url) {
            Button("Open in Safari", systemImage: "safari") { openURL(url) }
            Button("Copy Link", systemImage: "doc.on.doc") { UIPasteboard.general.url = url }
            ShareLink(item: url) { Label("Share…", systemImage: "square.and.arrow.up") }
        }
    }
}

struct SearchResults: View {
    let synced: Synced
    let query: String

    var body: some View {
        let found = synced.spaces.map { space in (space, space.pinned.flatMap(tabs).filter(matches)) }.filter { !$0.1.isEmpty }
        if found.isEmpty {
            ContentUnavailableView.search(text: query)
        } else {
            List {
                ForEach(found, id: \.0.id) { space, tabs in
                    Section(space.name) {
                        ForEach(tabs) { TabRow(tab: $0, color: space.color) }
                    }
                }
            }
        }
    }

    private func tabs(_ node: Node) -> [EclipseKit.Tab] {
        switch node {
        case .tab(let tab): return [tab]
        case .folder(let folder): return folder.children.flatMap(tabs)
        }
    }

    private func matches(_ tab: EclipseKit.Tab) -> Bool {
        tab.label.localizedCaseInsensitiveContains(query) || tab.url.localizedCaseInsensitiveContains(query)
    }
}

/// A site's letter on its Space's color, deepened until the white letter reads (as the Mac's tiles are).
struct SiteTile: View {
    let url: String
    let color: String
    let size: CGFloat

    var body: some View {
        Text(siteLetter(siteHost(url)))
            .font(.system(size: size * 0.5, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Color(hex: color, readableUnderWhite: true), in: RoundedRectangle(cornerRadius: size * 0.26))
            .accessibilityHidden(true)
    }
}

/// A Space's look: its colors as a soft gradient, as strong as its intensity.
struct SpaceBackground: View {
    let space: Space?

    var body: some View {
        let look = space?.look
        let colors = (look?.colors.isEmpty == false ? look!.colors : space.map { [$0.color] } ?? []).map { Color(hex: $0) }
        let strength = 0.18 + 0.42 * (look?.intensity ?? 0.5)
        ZStack {
            Color(uiColor: .systemGroupedBackground)
            if !colors.isEmpty {
                LinearGradient(colors: colors.count == 1 ? [colors[0], colors[0].opacity(0.4)] : colors,
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                    .opacity(strength)
            }
        }
        .animation(.easeInOut(duration: 0.35), value: space?.id)
    }
}

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ controller: SFSafariViewController, context: Context) {}
}

private struct OpenPageKey: EnvironmentKey {
    static let defaultValue: (URL) -> Void = { _ in }
}

extension EnvironmentValues {
    /// Opens a page in the app (in Safari's view, over the Spaces).
    var openPage: (URL) -> Void {
        get { self[OpenPageKey.self] }
        set { self[OpenPageKey.self] = newValue }
    }
}

extension Space {
    /// The Space's icon when it's an emoji (the Mac's line icons aren't on iOS).
    var emoji: String? { icon.isEmpty || icon.hasPrefix("ph.") ? nil : icon }
}

extension Color {
    /// From "#rrggbb" (gray if it isn't one); `readableUnderWhite` deepens a pale color until white text reads.
    init(hex: String, readableUnderWhite: Bool = false) {
        let value = UInt32(hex.dropFirst(), radix: 16) ?? 0x6b7280
        var r = Double((value >> 16) & 0xff) / 255, g = Double((value >> 8) & 0xff) / 255, b = Double(value & 0xff) / 255
        if readableUnderWhite {
            func linear(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
            for _ in 0..<12 where 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b) > 0.32 {
                (r, g, b) = (r / 1.12, g / 1.12, b / 1.12)
            }
        }
        self.init(red: r, green: g, blue: b)
    }
}
