// Sync's format and merging, as browser/sync.py has them: each device writes its own file in iCloud Drive's
// "Eclipse Sync" folder ({"format": "eclipse-sync", "device", "name", "written", "items": {key: {"modified",
// "data"}}, "deleted": {key: when}}), an item being a Space ("space:<id>"), "favorites" or "order". Each item's
// newest change wins, a deletion counting as one. Another device's file is checked before it's used (it's yours,
// but it came through the cloud), as sync.clean_remote does.

import Foundation

public enum SyncKey {
    public static let favorites = "favorites"
    public static let order = "order"
    public static let format = "eclipse-sync"
    public static let folder = "Eclipse Sync"
    public static func space(_ id: String) -> String { "space:\(id)" }
}

/// One device's file, checked.
public struct SyncFile: Equatable, Sendable {
    public struct Item: Equatable, Sendable {
        public var modified: Double
        public var data: JSONValue
    }

    public var device: String?
    public var name: String
    public var items: [String: Item]
    public var deleted: [String: Double]
    public var written: Double

    /// A device's file as read (its JSON), checked part by part; nil if it isn't a sync file at all.
    public static func clean(_ raw: JSONValue) -> SyncFile? {
        guard let file = raw.object, file["format"]?.string == SyncKey.format else { return nil }
        var items: [String: Item] = [:]
        for (key, item) in file["items"]?.object ?? [:] {
            if let data = cleanItem(key, item["data"]), let modified = item["modified"]?.number {
                items[key] = Item(modified: modified, data: data)
            }
        }
        var deleted: [String: Double] = [:]
        for (key, when) in file["deleted"]?.object ?? [:] {
            if let when = when.number { deleted[key] = when }
        }
        return SyncFile(device: file["device"]?.string, name: cleanText(file["name"], 60), items: items,
                        deleted: deleted, written: file["written"]?.number ?? 0)
    }

    public static func read(_ url: URL) -> SyncFile? {
        guard let data = try? Data(contentsOf: url), data.count < 20_000_000,
              let raw = try? JSONValue(data: data) else { return nil }
        return clean(raw)
    }

    /// The other devices' files in a sync folder (`mine`: this device's id, left out).
    public static func readFolder(_ folder: URL, mine: String) -> [SyncFile] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return names.filter { $0.hasSuffix(".json") && !$0.hasPrefix(".") && $0 != "\(mine).json" }.sorted()
            .compactMap { read(folder.appendingPathComponent($0)) }
    }
}

// MARK: - Checking what came from another device (sync.clean_item, space_share's helpers)

/// Text without control or bidi characters, its runs of spaces made one, at most `limit` characters.
public func cleanText(_ value: JSONValue?, _ limit: Int) -> String {
    let kept = (value?.string ?? "").unicodeScalars.filter {
        $0.properties.generalCategory != .control && $0.properties.generalCategory != .format
    }
    let words = String(String.UnicodeScalarView(kept)).split(whereSeparator: { $0.unicodeScalars.allSatisfy(\.properties.isWhitespace) })
    let joined = words.joined(separator: " ")
    return String(String.UnicodeScalarView(joined.unicodeScalars.prefix(limit)))
}

/// A web address (http or https only: never javascript:, file:, data: or an app's), or nil.
public func cleanURL(_ value: JSONValue?) -> String? {
    guard let url = value?.string, url.unicodeScalars.count <= 4096,
          url.hasPrefix("https://") || url.hasPrefix("http://") else { return nil }
    let bad = url.unicodeScalars.contains { $0.properties.isWhitespace || $0.properties.generalCategory == .control }
    return bad ? nil : url
}

func isColor(_ text: String) -> Bool {
    let scalars = Array(text.unicodeScalars)
    return scalars.count == 7 && scalars[0] == "#" && scalars.dropFirst().allSatisfy {
        ("0"..."9").contains($0) || ("a"..."f").contains($0) || ("A"..."F").contains($0)
    }
}

func cleanIcon(_ value: JSONValue?) -> String {
    let icon = value?.string ?? ""
    let scalars = Array(icon.unicodeScalars)
    let line = icon.hasPrefix("ph.") && (4...43).contains(scalars.count) && scalars.dropFirst(3).allSatisfy {
        ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "-"
    }
    let emoji = scalars.count <= 8 && !scalars.contains { $0.isASCII }
    return line || emoji ? icon : ""
}

func cleanLook(_ value: JSONValue?) -> JSONValue {
    let theme = value?.object ?? [:]
    var look: [String: JSONValue] = [:]
    let colors = (theme["colors"]?.array ?? []).compactMap { $0.string }.filter(isColor).prefix(4)
    if !colors.isEmpty { look["colors"] = .array(colors.map { .string($0) }) }
    if let intensity = theme["intensity"]?.number { look["intensity"] = .double(min(max(intensity, 0), 1)) }
    if let mode = theme["mode"]?.string, ["auto", "light", "dark"].contains(mode) { look["mode"] = .string(mode) }
    if let grain = theme["grain"]?.bool { look["grain"] = .bool(grain) }
    return .object(look)
}

func cleanNode(_ value: JSONValue, depth: Int = 0) -> JSONValue? {
    guard let node = value.object, let id = node["id"]?.string, depth <= 8 else { return nil }
    let shortId = JSONValue.string(String(String.UnicodeScalarView(id.unicodeScalars.prefix(64))))
    if node["type"]?.string == "folder" {
        var out: [String: JSONValue] = ["type": .string("folder"), "id": shortId,
                                        "name": .string(cleanText(node["name"], 80).isEmpty ? "Folder" : cleanText(node["name"], 80))]
        if let live = node["live"]?.object {
            let kept = live.filter {
                switch $0.value { case .string, .int, .double, .bool: return true; default: return false }
            }
            // A folder that reads an address only ever reads the web (never file:)
            let readsAddress = !["github", "repos"].contains(kept["kind"]?.string ?? "")
            if (readsAddress || kept["url"] != nil) && cleanURL(kept["url"]) == nil { return nil }
            out["live"] = .object(kept)
        } else {
            out["children"] = .array((node["children"]?.array ?? []).compactMap { cleanNode($0, depth: depth + 1) })
        }
        return .object(out)
    }
    guard let url = cleanURL(node["url"]) else { return nil }
    let custom: JSONValue = node["custom_title"]?.string != nil ? .string(cleanText(node["custom_title"], 300)) : .null
    return .object(["id": shortId, "url": .string(url), "title": .string(cleanText(node["title"], 300)),
                    "custom_title": custom])
}

/// An item from another device's file, checked, or nil if it doesn't make sense.
public func cleanItem(_ key: String, _ data: JSONValue?) -> JSONValue? {
    guard let data else { return nil }
    if key == SyncKey.order {
        return data.array.map { .array($0.compactMap { $0.string }.map { .string(String(String.UnicodeScalarView($0.unicodeScalars.prefix(64)))) }) }
    }
    if key == SyncKey.favorites {
        return data.array.map { .array($0.compactMap { cleanNode($0) }.filter { $0["url"] != nil }) }
    }
    if key.hasPrefix("space:"), let space = data.object {
        let name = cleanText(space["name"], 60)
        let color = space["color"]?.string.flatMap { isColor($0) ? $0 : nil } ?? "#6b7280"
        return .object(["name": .string(name.isEmpty ? "Space" : name), "color": .string(color),
                        "icon": .string(cleanIcon(space["icon"])), "theme": cleanLook(space["theme"]),
                        "pinned": .array((space["pinned"]?.array ?? []).compactMap { cleanNode($0) })])
    }
    return nil
}

// MARK: - Merging (sync.note_local, sync.merge, sync.device_file)

/// What a device knows: each item's hash and when it last changed, and what's been deleted when.
public struct SyncState: Codable, Equatable, Sendable {
    public struct Known: Codable, Equatable, Sendable {
        public var hash: String
        public var modified: Double
    }

    public var device: String
    public var known: [String: Known]
    public var deleted: [String: Double]

    public init(device: String = UUID().uuidString.lowercased(), known: [String: Known] = [:], deleted: [String: Double] = [:]) {
        self.device = device
        self.known = known
        self.deleted = deleted
    }

    /// What changed here since last time (each changed or deleted item stamped `now`). True if anything did.
    @discardableResult
    public mutating func noteLocal(_ items: [String: JSONValue], now: Double) -> Bool {
        var changed = false
        for (key, data) in items {
            let hash = digest(data)
            if known[key]?.hash != hash {
                known[key] = Known(hash: hash, modified: now)
                deleted[key] = nil
                changed = true
            }
        }
        for key in known.keys where items[key] == nil {
            deleted[key] = now
            known[key] = nil
            changed = true
        }
        return changed
    }

    /// What to change here from the other devices' files, newest change of each item winning. The state is
    /// updated as if it's done.
    public mutating func merge(_ remotes: [SyncFile]) -> (apply: [String: JSONValue], delete: [String]) {
        var best: [String: (when: Double, data: JSONValue?)] = [:]
        for remote in remotes {
            for (key, item) in remote.items where best[key] == nil || item.modified > best[key]!.when {
                best[key] = (item.modified, item.data)
            }
            for (key, when) in remote.deleted where best[key] == nil || when > best[key]!.when {
                best[key] = (when, nil)
            }
        }
        var apply: [String: JSONValue] = [:], delete: [String] = []
        for (key, (when, data)) in best {
            let mine = known[key]?.modified ?? deleted[key] ?? -.infinity
            if when <= mine { continue }
            if let data {
                let hash = digest(data)
                if known[key]?.hash != hash { apply[key] = data }
                known[key] = Known(hash: hash, modified: when)
                deleted[key] = nil
            } else {
                if known[key] != nil {
                    delete.append(key)
                    known[key] = nil
                }
                deleted[key] = when
            }
        }
        return (apply, delete.sorted())
    }

    /// This device's own file, for the sync folder.
    public func deviceFile(_ items: [String: JSONValue], name: String, now: Double) -> JSONValue {
        var written: [String: JSONValue] = [:]
        for (key, data) in items {
            written[key] = .object(["modified": .double(known[key]?.modified ?? now), "data": data])
        }
        return .object(["format": .string(SyncKey.format), "version": .int(1), "device": .string(device),
                        "name": .string(name), "written": .double(now), "items": .object(written),
                        "deleted": .object(deleted.mapValues { .double($0) })])
    }
}
