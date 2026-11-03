// EclipseKit against sync.py itself: Fixtures/sync.json is made by tests/test_sync_swift_fixture.py from sync.py's
// own functions, so these pass only while the Swift side reads, checks, hashes and merges exactly as Python does.

import Foundation
import XCTest
@testable import EclipseKit

final class SyncTests: XCTestCase {
    var fixture: JSONValue!

    override func setUpWithError() throws {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "sync", withExtension: "json", subdirectory: "Fixtures"))
        fixture = try JSONValue(data: Data(contentsOf: url))
    }

    func testJSONTextIsPythonsByteForByte() throws {
        for case let .object(pair) in fixture["canonical"]?.array ?? [] {
            XCTAssertEqual(pair["value"]?.canonical, pair["text"]?.string)
        }
        XCTAssertEqual(pythonFloat(1e15), "1000000000000000.0")
        XCTAssertEqual(pythonFloat(1e16), "1e+16")
        XCTAssertEqual(pythonFloat(1e-05), "1e-05")
        XCTAssertEqual(pythonFloat(0.0001), "0.0001")
    }

    func testAnotherDevicesFileIsCheckedAsSyncPyDoes() throws {
        let files = (fixture["files"]?.array ?? []).compactMap(SyncFile.clean)
        let expected = fixture["clean"]?.array ?? []  // (sync.clean_remote's own output)
        XCTAssertEqual(files.count, 2)
        for (file, python) in zip(files, expected) {
            XCTAssertEqual(file.device, python["device"]?.string)
            XCTAssertEqual(file.name, python["name"]?.string)
            XCTAssertEqual(file.written, python["written"]?.number)
            XCTAssertEqual(file.deleted, (python["deleted"]?.object ?? [:]).compactMapValues { $0.number })
            let items = python["items"]?.object ?? [:]
            XCTAssertEqual(Set(file.items.keys), Set(items.keys))
            for (key, item) in items {
                XCTAssertEqual(file.items[key]?.data, item["data"], key)
                XCTAssertEqual(file.items[key]?.modified, item["modified"]?.number, key)
            }
        }
        let work = try XCTUnwrap(files[0].items["space:work"]?.data)
        XCTAssertEqual(work["name"]?.string, "Wörk tricks")  // (no bidi trick, no line break, one space)
        XCTAssertNil(files[0].items["space:broken"])  // (no time it changed)
        XCTAssertNil(files[0].items["unknown"])
        XCTAssertEqual(files[0].deleted, ["space:old": 80])
    }

    func testEachItemsHashIsSyncPys() throws {
        // (sync.py's dict of hashes: each key's from the last file that has it)
        let files = (fixture["files"]?.array ?? []).compactMap(SyncFile.clean)
        for (key, hash) in fixture["digests"]?.object ?? [:] {
            let item = try XCTUnwrap(files.reversed().lazy.compactMap { $0.items[key] }.first, key)
            XCTAssertEqual(digest(item.data), hash.string, key)
        }
    }

    func testMergingIsSyncPys() throws {
        let files = (fixture["files"]?.array ?? []).compactMap(SyncFile.clean)
        var state = SyncState(device: "phone", known: ["space:home": .init(hash: "old", modified: 130)])
        let plan = state.merge(files)
        let expected = try XCTUnwrap(fixture["plan"])
        XCTAssertEqual(JSONValue.object(plan.apply), expected["apply"])
        XCTAssertEqual(plan.delete, (expected["delete"]?.array ?? []).compactMap { $0.string }.sorted())
        let after = try XCTUnwrap(fixture["state"])
        for (key, known) in after["known"]?.object ?? [:] {
            XCTAssertEqual(state.known[key]?.hash, known["hash"]?.string, key)
            XCTAssertEqual(state.known[key]?.modified, known["modified"]?.number, key)
        }
        XCTAssertEqual(state.deleted, (after["deleted"]?.object ?? [:]).compactMapValues { $0.number })
    }

    func testWhatsSyncedReadsAsSpacesAndFavorites() throws {
        let files = (fixture["files"]?.array ?? []).compactMap(SyncFile.clean)
        let synced = Synced(files: files)
        XCTAssertEqual(synced.spaces.map(\.id), ["work", "home"])  // (in the synced order)
        let work = synced.spaces[0]
        XCTAssertEqual(work.look.colors, ["#3b82f6", "#f59e0b"])
        XCTAssertEqual(work.look.intensity, 1.0)
        XCTAssertEqual(work.pinned.map(\.id), ["t1", "f1", "f2"])  // (javascript: and file: left out)
        guard case .folder(let docs) = work.pinned[1] else { return XCTFail("a folder") }
        guard case .tab(let q3) = docs.children[0] else { return XCTFail("a tab") }
        XCTAssertEqual(q3.label, "Q3")
        XCTAssertEqual(synced.favorites.map(\.url), ["https://example.org"])
    }

    func testSitesLettersAreThemeSitePys() throws {
        for (host, letter) in fixture["letters"]?.object ?? [:] {
            XCTAssertEqual(siteLetter(host), letter.string, host)
        }
        XCTAssertEqual(siteHost("https://www.example.com/a"), "example.com")
        XCTAssertEqual(siteHost("https://mail.example.com"), "mail.example.com")
    }

    func testFilesStillInICloudAreNamedAsTheyWillBe() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        for name in ["mac-1.json", ".mac-2.json.icloud", ".DS_Store", ".notes.txt.icloud"] {
            FileManager.default.createFile(atPath: folder.appendingPathComponent(name).path, contents: Data())
        }
        XCTAssertEqual(SyncFile.pending(in: folder).map(\.lastPathComponent), ["mac-2.json"])
    }

    func testThisDevicesOwnFileReadsBackTheSame() throws {
        var state = SyncState(device: "phone")
        let items: [String: JSONValue] = [SyncKey.order: .array([.string("work")]),
                                          SyncKey.space("work"): .object(["name": .string("Work"), "pinned": .array([])])]
        XCTAssertTrue(state.noteLocal(items, now: 10))
        XCTAssertFalse(state.noteLocal(items, now: 11))  // (nothing changed)
        let file = state.deviceFile(items, name: "iPhone", now: 12)
        let read = try XCTUnwrap(SyncFile.clean(try JSONValue(data: JSONSerialization.data(withJSONObject: file.any))))
        XCTAssertEqual(read.device, "phone")
        XCTAssertEqual(read.items[SyncKey.order]?.modified, 10)
        XCTAssertTrue(state.noteLocal([:], now: 13))  // (both gone: deletions, for the other devices)
        XCTAssertEqual(state.deleted.keys.sorted(), [SyncKey.order, SyncKey.space("work")])
    }
}
