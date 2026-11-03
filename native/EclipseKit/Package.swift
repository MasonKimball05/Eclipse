// swift-tools-version:5.9
// EclipseKit: what Eclipse's apps share, in Swift, for the Mac's native pages and an iPhone or iPad app. It starts
// with Sync's format (browser/sync.py): reading the devices' files in iCloud Drive's "Eclipse Sync" folder into
// your Spaces, their pinned tabs and Favorites, merging them as Eclipse does, and writing a device's own file.

import PackageDescription

let package = Package(
    name: "EclipseKit",
    platforms: [.macOS(.v13), .iOS(.v16)],
    products: [.library(name: "EclipseKit", targets: ["EclipseKit"])],
    targets: [
        .target(name: "EclipseKit"),
        .testTarget(name: "EclipseKitTests", dependencies: ["EclipseKit"], resources: [.copy("Fixtures")]),
    ]
)
