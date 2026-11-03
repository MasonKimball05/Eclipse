// What's synced, for the app: the Eclipse Sync folder you chose in iCloud Drive (kept as a bookmark, since an app
// can only open a folder of iCloud Drive's that you picked), read again whenever the app comes back and when you
// pull down. The app only reads it: your Macs' files are theirs. The last read is kept, so the app opens with your
// Spaces even before iCloud has answered (or with no connection).

import EclipseKit
import Foundation
import Observation

@MainActor @Observable
final class SyncStore {
    struct Device: Identifiable, Hashable {
        var id: String
        var name: String
        var written: Date
    }

    private(set) var synced: Synced?
    private(set) var devices: [Device] = []
    private(set) var loading = false
    private(set) var problem: String?
    /// iCloud is still bringing some devices' files down.
    private(set) var waiting = 0
    var choosing = false

    private static let bookmarkKey = "syncFolder"
    private var folder: URL?
    private var retries = 0

    init() {
        folder = Self.launchFolder() ?? Self.bookmarkedFolder()
        if folder != nil { synced = Cache.read() }
    }

    var hasFolder: Bool { folder != nil }

    /// The folder you picked (a security-scoped address, good while the picker's access lasts: kept as a bookmark).
    func choose(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let bookmark = try? url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil) else {
            problem = "Eclipse couldn't keep that folder. Try choosing it again."
            return
        }
        UserDefaults.standard.set(bookmark, forKey: Self.bookmarkKey)
        folder = url
        problem = nil
        synced = nil
        Cache.clear()
        reload()
    }

    func forget() {
        UserDefaults.standard.removeObject(forKey: Self.bookmarkKey)
        folder = nil
        synced = nil
        devices = []
        problem = nil
        Cache.clear()
    }

    func reload() {
        guard let folder, !loading else { return }
        loading = true
        Task.detached(priority: .userInitiated) {
            let read = Self.read(folder)
            await self.finish(read)
        }
    }

    /// For pull to refresh: back when the read is done.
    func refresh() async {
        reload()
        while loading { try? await Task.sleep(for: .milliseconds(100)) }
    }

    private func finish(_ read: Read) {
        loading = false
        guard read.readable else {
            problem = "Eclipse can't open the sync folder any more. Choose it again."
            return
        }
        problem = nil
        waiting = read.pending
        var state = SyncState(device: "reader")
        let items = state.merge(read.files).apply
        synced = Synced(items: items)
        Cache.write(items)
        devices = read.files.map {
            Device(id: $0.device ?? $0.name, name: $0.name.isEmpty ? "A Mac" : $0.name,
                   written: Date(timeIntervalSince1970: $0.written))
        }.sorted { $0.written > $1.written }
        // Files iCloud is still bringing down: read again shortly (a few times, then on the next pull or return)
        if read.pending > 0 && retries < 5 {
            retries += 1
            Task {
                try? await Task.sleep(for: .seconds(2))
                reload()
            }
        } else {
            retries = 0
        }
    }

    // MARK: Reading

    struct Read: Sendable {
        var files: [SyncFile] = []
        var pending = 0
        var readable = true
    }

    nonisolated static func read(_ folder: URL) -> Read {
        let scoped = folder.startAccessingSecurityScopedResource()
        defer { if scoped { folder.stopAccessingSecurityScopedResource() } }
        var read = Read()
        var failed: NSError?
        NSFileCoordinator().coordinate(readingItemAt: folder, options: [], error: &failed) { url in
            var isFolder: ObjCBool = false
            guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isFolder), isFolder.boolValue else {
                read.readable = false
                return
            }
            let pending = SyncFile.pending(in: url)
            for file in pending { try? FileManager.default.startDownloadingUbiquitousItem(at: file) }
            read.pending = pending.count
            read.files = SyncFile.readFolder(url, mine: "")
        }
        if failed != nil { read.readable = false }
        return read
    }

    /// ECLIPSE_SYNC_DIR, as the Mac app has it: a folder to read straight away (for trying it in the Simulator).
    private static func launchFolder() -> URL? {
        ProcessInfo.processInfo.environment["ECLIPSE_SYNC_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) }
    }

    private static func bookmarkedFolder() -> URL? {
        guard let bookmark = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: bookmark, options: [], relativeTo: nil, bookmarkDataIsStale: &stale) else {
            return nil
        }
        if stale, let fresh = try? url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil) {
            UserDefaults.standard.set(fresh, forKey: bookmarkKey)
        }
        return url
    }
}

/// The last read's items ({key: data}, as merging leaves them), in the app's own folder.
enum Cache {
    private static var file: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("synced.json")
    }

    static func read() -> Synced? {
        guard let data = try? Data(contentsOf: file), let items = try? JSONValue(data: data).object else { return nil }
        return Synced(items: items)
    }

    static func write(_ items: [String: JSONValue]) {
        try? Data(JSONValue.object(items).canonical.utf8).write(to: file, options: .atomic)
    }

    static func clear() {
        try? FileManager.default.removeItem(at: file)
    }
}
