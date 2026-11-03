// Small things about a site that each app shows the same way: its host as a row shows it, and the letter on its
// tile when there's no icon (theme.site_letter).

import Foundation

/// A web address's host without "www." ("" if it has none).
public func siteHost(_ url: String) -> String {
    let host = URLComponents(string: url)?.host ?? ""
    return host.hasPrefix("www.") ? String(host.dropFirst(4)) : host
}

/// The letter for a site: its name's, not a subdomain's (en.wikipedia.org is W, mail.google.com G, bbc.co.uk B).
public func siteLetter(_ text: String) -> String {
    let secondLevel: Set<String> = ["co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"]
    var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
    let labels = text.lowercased().components(separatedBy: ".")
    if labels.count >= 2 && !text.contains(" ") && !text.replacingOccurrences(of: ".", with: "").allSatisfy(\.isNumber) {
        let n = labels.count
        let name = n >= 3 && secondLevel.contains(labels[n - 2]) && labels[n - 1].count == 2 ? labels[n - 3] : labels[n - 2]
        if !name.isEmpty { text = name }
    }
    return text.first.map { String($0).uppercased() } ?? "?"
}

extension SyncFile {
    /// The devices' files in a sync folder that iCloud hasn't brought down yet (".<name>.json.icloud"), as the
    /// names they'll have: an app asks for them (FileManager.startDownloadingUbiquitousItem) and reads again.
    public static func pending(in folder: URL) -> [URL] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return names.filter { $0.hasPrefix(".") && $0.hasSuffix(".json.icloud") }.sorted()
            .map { folder.appendingPathComponent(String($0.dropFirst().dropLast(".icloud".count))) }
    }
}
