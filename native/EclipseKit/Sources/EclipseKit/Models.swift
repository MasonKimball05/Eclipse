// Your Spaces, their pinned tabs and folders, and Favorites, as an app shows them: read from the synced items
// (each device's newest), in the Spaces' synced order.

import Foundation

public struct Tab: Identifiable, Equatable, Sendable {
    public var id: String
    public var url: String
    public var title: String
    public var customTitle: String?

    /// What a row shows: the name you gave it, else its page's title, else its address.
    public var label: String { customTitle ?? (title.isEmpty ? url : title) }
}

public struct Folder: Identifiable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var children: [Node]
    /// A Live Folder's source ({"kind": "feed", "url": …}): its items fill themselves on each device.
    public var live: [String: JSONValue]?
}

public indirect enum Node: Identifiable, Equatable, Sendable {
    case tab(Tab)
    case folder(Folder)

    public var id: String {
        switch self {
        case .tab(let t): return t.id
        case .folder(let f): return f.id
        }
    }

    init?(_ json: JSONValue) {
        guard let o = json.object, let id = o["id"]?.string else { return nil }
        if o["type"]?.string == "folder" {
            self = .folder(Folder(id: id, name: o["name"]?.string ?? "Folder",
                                  children: (o["children"]?.array ?? []).compactMap(Node.init), live: o["live"]?.object))
        } else if let url = o["url"]?.string {
            self = .tab(Tab(id: id, url: url, title: o["title"]?.string ?? "", customTitle: o["custom_title"]?.string))
        } else {
            return nil
        }
    }
}

/// A Space's look: up to four colors, how strongly they tint, light, dark or following the system, and grain.
public struct Look: Equatable, Sendable {
    public var colors: [String] = []
    public var intensity: Double = 0.5
    public var mode: String = "auto"
    public var grain = false
}

public struct Space: Identifiable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var color: String
    public var icon: String
    public var look: Look
    public var pinned: [Node]

    init(id: String, json: JSONValue) {
        self.id = id
        name = json["name"]?.string ?? "Space"
        color = json["color"]?.string ?? "#6b7280"
        icon = json["icon"]?.string ?? ""
        let theme = json["theme"]?.object ?? [:]
        look = Look(colors: (theme["colors"]?.array ?? []).compactMap { $0.string },
                    intensity: theme["intensity"]?.number ?? 0.5, mode: theme["mode"]?.string ?? "auto",
                    grain: theme["grain"]?.bool ?? false)
        pinned = (json["pinned"]?.array ?? []).compactMap(Node.init)
    }
}

/// Everything synced, as it stands: the Spaces in order, and Favorites.
public struct Synced: Equatable, Sendable {
    public var spaces: [Space]
    public var favorites: [Tab]

    /// From the synced items ({key: data}, as merging leaves them).
    public init(items: [String: JSONValue]) {
        var spaces: [String: Space] = [:]
        for (key, data) in items where key.hasPrefix("space:") {
            let id = String(key.dropFirst("space:".count))
            spaces[id] = Space(id: id, json: data)
        }
        let order = (items[SyncKey.order]?.array ?? []).compactMap { $0.string }
        let ordered = order.compactMap { spaces[$0] }
        let rest = spaces.values.filter { !order.contains($0.id) }.sorted { $0.name < $1.name }
        self.spaces = ordered + rest  // (a Space the order doesn't have yet: after the others)
        favorites = (items[SyncKey.favorites]?.array ?? []).compactMap(Node.init).compactMap {
            if case .tab(let t) = $0 { return t }
            return nil
        }
    }

    /// From the devices' files: each item's newest version (none deleted since).
    public init(files: [SyncFile]) {
        var state = SyncState(device: "reader")
        self.init(items: state.merge(files).apply)
    }

    /// What's in a sync folder now (on a Mac, iCloud Drive's "Eclipse Sync"; in an iOS app, its iCloud container's).
    public init(folder: URL) {
        self.init(files: SyncFile.readFolder(folder, mine: ""))
    }
}
