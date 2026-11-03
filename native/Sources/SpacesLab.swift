// A trial: the Spaces sidebar (Favorites, a Space's pinned tabs and folders and its Today tabs, the Space switcher,
// the Space's colors) drawn by SwiftUI, to compare with Qt's (sidebar.py, space_slide.py) for how smooth and quick
// switching Spaces is, and how much work its animations are. It isn't used by the browser: the benchmark
// (tests/live/test_spaces_benchmark.py) opens it with a window's Spaces as JSON.
//
// Switching is a paging scroll view, so a two-finger swipe moves it with your fingers and lands with the system's
// own spring, and a switch from Python (or the switcher) is the same scroll, animated. The colors blend as it
// goes. A display link counts the frames: {"bench": {"n", "to"}} switches to a Space and sends back
// {"name": "bench", "first_ms": till the first frame after, "frames_ms": [each frame's gap], "took_ms"}.

import AppKit
import QuartzCore
import SwiftUI

struct LabTab: Identifiable {
    let id: String
    let title: String
    let host: String
    let children: [LabTab]?

    init(_ json: JSON) {
        id = json.str("id")
        title = json.str("title")
        host = json.str("host")
        children = (json["children"] as? [JSON]).map { $0.map(LabTab.init) }
    }
}

struct LabSpace: Identifiable {
    let id: String
    let name: String
    let colors: [Color]
    let pinned: [LabTab]
    let today: [LabTab]

    init(_ json: JSON) {
        id = json.str("id")
        name = json.str("name")
        let hexes = (json["colors"] as? [String] ?? []).compactMap { Color(hex: $0) }
        colors = hexes.isEmpty ? [.gray, .gray] : hexes.count == 1 ? [hexes[0], hexes[0].opacity(0.6)] : hexes
        pinned = (json["pinned"] as? [JSON] ?? []).map(LabTab.init)
        today = (json["today"] as? [JSON] ?? []).map(LabTab.init)
    }
}

/// Counts frames: the display link's ticks, kept while a measurement's on. The link only runs then (one ticking all
/// the time would wake the main thread every frame, 120 times a second on a ProMotion Mac, for nothing).
@available(macOS 14, *)
@MainActor
final class FrameMeter: NSObject, ObservableObject {
    private weak var view: NSView?
    private var link: CADisplayLink?
    private var ticks: [CFTimeInterval] = []
    private var started: CFTimeInterval = 0
    private var done: (([CFTimeInterval], CFTimeInterval) -> Void)?
    private var until: CFTimeInterval = 0

    func attach(to view: NSView) {
        self.view = view
    }

    func measure(for seconds: Double, then: @escaping ([CFTimeInterval], CFTimeInterval) -> Void) {
        ticks = []
        started = CACurrentMediaTime()
        until = started + seconds
        done = then
        if link == nil, let view {
            let link = view.displayLink(target: self, selector: #selector(tick(_:)))
            link.add(to: .main, forMode: .common)
            self.link = link
        }
    }

    @objc private func tick(_ link: CADisplayLink) {
        guard let done else { return }
        let now = CACurrentMediaTime()
        ticks.append(now)
        if now >= until {
            self.done = nil
            stop()
            done(ticks, started)
        }
    }

    func stop() {
        link?.invalidate()
        link = nil
    }
}

@available(macOS 14, *)
struct MeterView: NSViewRepresentable {
    let meter: FrameMeter

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { meter.attach(to: view) }
        return view
    }

    func updateNSView(_ view: NSView, context: Context) {}
}

@available(macOS 14, *)
struct SpacesLabView: View {
    @ObservedObject var model: PageModel
    @StateObject private var meter = FrameMeter()
    @State private var current: String?
    @State private var benched = 0
    @State private var selected: String?

    private var spaces: [LabSpace] { (model.state["spaces"] as? [JSON] ?? []).map(LabSpace.init) }
    private var favorites: [LabTab] { (model.state["favorites"] as? [JSON] ?? []).map(LabTab.init) }
    private var space: LabSpace? { spaces.first { $0.id == current } ?? spaces.first }

    var body: some View {
        VStack(spacing: 10) {
            favoritesGrid
            pages
            switcher
        }
        .padding(10)
        .frame(width: 280)
        .frame(maxHeight: .infinity)
        .background {
            LinearGradient(colors: space?.colors ?? [.gray], startPoint: .topLeading, endPoint: .bottomTrailing)
                .opacity(0.55)
                .animation(.easeInOut(duration: 0.3), value: current)
                .ignoresSafeArea()
        }
        .background(MeterView(meter: meter).frame(width: 1, height: 1))
        .onAppear { current = spaces.first?.id }
        .onDisappear { meter.stop() }
        .onChange(of: (model.state["bench"] as? JSON)?["n"] as? Int) { _ in bench() }
    }

    private var favoritesGrid: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 6), count: 4), spacing: 6) {
            ForEach(favorites) { tab in
                Tile(text: tab.host, size: 22)
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .background(.background.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    private var pages: some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 0) {
                ForEach(spaces) { space in
                    SpaceColumn(space: space, selected: $selected)
                        .containerRelativeFrame(.horizontal)
                        .id(Optional(space.id))
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.paging)
        .scrollPosition(id: $current)
        .scrollIndicators(.never)
    }

    private var switcher: some View {
        HStack(spacing: 8) {
            ForEach(spaces) { space in
                Circle()
                    .fill(space.colors[0])
                    .frame(width: space.id == current ? 10 : 7, height: space.id == current ? 10 : 7)
                    .opacity(space.id == current ? 1 : 0.5)
                    .onTapGesture { withAnimation(.snappy) { current = space.id } }
            }
        }
        .animation(.snappy, value: current)
        .frame(height: 20)
    }

    private func bench() {
        guard let input = model.state["bench"] as? JSON, let n = input["n"] as? Int, n > benched else { return }
        benched = n
        let to = input.str("to")
        let seconds = (input["seconds"] as? NSNumber)?.doubleValue ?? 0.6
        meter.measure(for: seconds) { ticks, started in
            let gaps = zip(ticks.dropFirst(), ticks).map { ($0 - $1) * 1000 }
            model.act("bench", ["first_ms": ((ticks.first ?? started) - started) * 1000, "frames_ms": gaps,
                                "took_ms": ((ticks.last ?? started) - started) * 1000, "to": to])
        }
        withAnimation(.smooth(duration: 0.26)) { current = to }
    }
}

@available(macOS 14, *)
private struct SpaceColumn: View {
    let space: LabSpace
    @Binding var selected: String?

    var body: some View {
        ScrollView(.vertical) {
            LazyVStack(alignment: .leading, spacing: 1) {
                Text(space.name).font(.headline).padding(.horizontal, 8).padding(.bottom, 6)
                ForEach(space.pinned) { node in NodeRows(node: node, depth: 0, selected: $selected) }
                Divider().padding(.vertical, 6)
                ForEach(space.today) { tab in Row(tab: tab, depth: 0, selected: $selected) }
            }
            .padding(.vertical, 4)
        }
        .scrollIndicators(.never)
    }
}

private struct NodeRows: View {
    let node: LabTab
    let depth: Int
    @Binding var selected: String?
    @State private var open = true

    var body: some View {
        if let children = node.children {
            VStack(alignment: .leading, spacing: 1) {
                Button {
                    withAnimation(.snappy) { open.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: open ? "folder" : "folder.fill").frame(width: 18)
                        Text(node.title).lineLimit(1)
                        Spacer()
                    }
                    .padding(.horizontal, 8).padding(.vertical, 5)
                    .padding(.leading, CGFloat(depth) * 14)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if open {
                    ForEach(children) { NodeRows(node: $0, depth: depth + 1, selected: $selected) }
                }
            }
        } else {
            Row(tab: node, depth: depth, selected: $selected)
        }
    }
}

private struct Row: View {
    let tab: LabTab
    let depth: Int
    @Binding var selected: String?
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 8) {
            Tile(text: tab.host, size: 16)
            Text(tab.title).lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .padding(.leading, CGFloat(depth) * 14)
        .background(RoundedRectangle(cornerRadius: 7).fill(Color.primary.opacity(
            selected == tab.id ? 0.16 : hovering ? 0.07 : 0)))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { selected = tab.id }
        .animation(.easeOut(duration: 0.12), value: hovering)
    }
}

private struct Tile: View {
    let text: String
    let size: CGFloat

    var body: some View {
        Text(String(text.first ?? "?").uppercased())
            .font(.system(size: size * 0.55, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .frame(width: size, height: size)
            .background(Color.accentColor, in: RoundedRectangle(cornerRadius: size * 0.26))
    }
}
