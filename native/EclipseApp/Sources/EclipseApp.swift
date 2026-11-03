// Eclipse for iPhone and iPad: your Spaces from Eclipse on your Mac, each in its own colors, with its pinned tabs
// and folders, and your Favorites above them. Swipe between Spaces; tap a tab to open it.

import EclipseKit
import SwiftUI

@main
struct EclipseApp: App {
    @State private var store = SyncStore()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(store)
                .onChange(of: phase, initial: true) { _, now in
                    if now == .active { store.reload() }
                }
        }
    }
}

struct RootView: View {
    @Environment(SyncStore.self) private var store

    var body: some View {
        @Bindable var store = store
        Group {
            if !store.hasFolder {
                WelcomeView()
            } else if let synced = store.synced {
                SpacesView(synced: synced)
            } else if let problem = store.problem {
                ProblemView(text: problem)
            } else {
                ProgressView("Reading your Spaces…")
            }
        }
        .fileImporter(isPresented: $store.choosing, allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result { store.choose(url) }
        }
    }
}

struct WelcomeView: View {
    @Environment(SyncStore.self) private var store

    var body: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "circle.lefthalf.filled")
                .font(.system(size: 64, weight: .light))
                .foregroundStyle(.tint)
            Text("Your Spaces, from your Mac")
                .font(.title2.bold())
            Text("Eclipse on your Mac syncs your Spaces, pinned tabs and Favorites through iCloud Drive. Turn on Sync there (Settings → General → Sync), then choose its folder here.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Spacer()
            Button {
                store.choosing = true
            } label: {
                Text("Choose the Eclipse Sync Folder").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            Text("In iCloud Drive, pick “\(SyncKey.folder)”. Eclipse only reads it.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .padding(28)
        .frame(maxWidth: 520)
    }
}

struct ProblemView: View {
    @Environment(SyncStore.self) private var store
    let text: String

    var body: some View {
        ContentUnavailableView {
            Label("Can't Read Your Spaces", systemImage: "icloud.slash")
        } description: {
            Text(text)
        } actions: {
            Button("Choose the Folder") { store.choosing = true }
                .buttonStyle(.borderedProminent)
        }
    }
}
