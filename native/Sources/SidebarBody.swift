// The sidebar's Space part, drawn by SwiftUI inside the Qt window (Embedded.swift): each Space's name, its pinned
// tabs and folders, the divider with Tidy and Clear, New Tab and its Today tabs. The rest of the sidebar (the
// navigation buttons, the address pill, Favorites, the cards and the bottom bar) stays Qt's. Python keeps the
// tabs and does what's asked (browser/sidebar_native.py): this only draws them and says what was done.
//
// The Spaces are pages of a paging scroll view: a two-finger swipe moves them with your fingers and lands with the
// system's spring, and a switch from elsewhere (⌃2, the switcher) is the same scroll, animated, so the frames come
// from the system's compositor at the display's own rate. A swipe that lands on another Space says so ("swiped"),
// once the scroll has come to rest: the view's position turns to the next Space as soon as it's halfway over, and
// the window's change of Space (its colors, its page: 40 ms and more of Python) done then stopped the scroll mid-swipe.
//
// State: "spaces": [{id, name, emoji, icon, profile, profile_tip, hint, tidy {label, enabled, tip}, clear, pinned:
// [node], today: [node]}], "current", "go" (a counter: animate to "current" when it changes), "selected_id" (the
// tab you're on: scrolled into view when it changes), "colors" {text, accent, accent_text, hover, selected: [r, g,
// b, a]}, and pictures as "icons_add" {key: base64 PNG}. A node is a tab {id, kind: "tab", title, icon, away, dim,
// fresh, badge, audio ("playing", "muted"), selected, marked, insplit, close ("close", "unload", "" for none), tip,
// label, hint (VoiceOver's)} or a folder {id, kind: "folder", name, icon, count, expanded, fixed (a Live Folder's
// own), locked (nothing's dropped in), size, tip, children}.

import AppKit
import SwiftUI
import UniformTypeIdentifiers

private let rowHeight: CGFloat = 34
private let indent: CGFloat = 16

private func rgba(_ value: Any?) -> Color {
    let v = (value as? [NSNumber] ?? []).map(\.doubleValue)
    guard v.count == 4 else { return .primary }
    return Color(.sRGB, red: v[0], green: v[1], blue: v[2], opacity: v[3])
}

/// What the rows need of the Space's look.
struct SidebarLook {
    let text: Color
    let accent: Color
    let accentText: Color
    let hover: Color
    let selected: Color

    init(_ json: JSON) {
        text = rgba(json["text"])
        accent = rgba(json["accent"])
        accentText = rgba(json["accent_text"])
        hover = rgba(json["hover"])
        selected = rgba(json["selected"])
    }
}

@available(macOS 14, *)
struct SidebarBodyView: View {
    @ObservedObject var model: PageModel
    @State private var current: String?
    @State private var going = false  // (a switch from Python under way: its scroll isn't a swipe)
    @StateObject private var settling = Settling()  // (when a swipe's done with: said then)
    @State private var moving = false  // (the Spaces scrolling: rows passing under the pointer aren't pointed at)
    @State private var position = 0.0  // (which Space it's scrolled to, from its offset: 1.5 is halfway to the second)
    @State private var headingFor: String?  // (the Space a swipe's been said to be on its way to)
    @State private var landing = false  // (done with, its spring still taking it into place: told once it's there)
    @StateObject private var meter = FrameMeter()  // (the benchmark's: frames counted while a switch is measured)

    private var spaces: [JSON] { model.state["spaces"] as? [JSON] ?? [] }
    private var look: SidebarLook { SidebarLook(model.state["colors"] as? JSON ?? [:]) }

    var body: some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 0) {
                ForEach(Array(spaces.enumerated()), id: \.element.spaceId) { _, space in
                    SpaceList(space: space, look: (space["colors"] as? JSON).map(SidebarLook.init) ?? look, model: model)
                        .containerRelativeFrame(.horizontal)
                        .id(Optional(space.spaceId))
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(OneSpaceAtATime())
        .scrollPosition(id: $current)
        .scrollIndicators(.never)
        .scrollClipDisabled(false)
        .coordinateSpace(.named("sidebarBody"))
        .background(MeterView(meter: meter).frame(width: 1, height: 1))
        .onAppear { current = model.string("current"); settling.start() }
        .onDisappear { meter.stop(); settling.stop() }
        .onChange(of: model.state["go"] as? Int) { _ in
            let target = model.string("current")
            settling.cancel()  // (a switch from Python has the last word over a swipe not yet told)
            landing = false
            guard target != current else { return }
            if let seconds = (model.state["measure"] as? NSNumber)?.doubleValue {  // (tests/live/test_spaces_benchmark.py)
                meter.measure(for: seconds) { ticks, started in
                    model.act("bench", ["first_ms": ((ticks.first ?? started) - started) * 1000,
                                        "frames_ms": zip(ticks.dropFirst(), ticks).map { ($0 - $1) * 1000 },
                                        "took_ms": ((ticks.last ?? started) - started) * 1000])
                }
            }
            if model.bool("animate", true) {
                going = true
                withAnimation(.smooth(duration: 0.3)) { current = target } completion: { going = false }
            } else {
                current = target
            }
        }
        .onChange(of: current) { now in
            if #unavailable(macOS 15), let now, let page = spaces.firstIndex(where: { $0.spaceId == now }) {
                position = Double(page)  // (no scroll geometry: the scroll position's id, which can lag)
            }
        }
        .modifier(AtRest(moving: $moving, position: $position) { page in
            // A switch from Python come to rest: on its Space (switches interrupting each other could leave it on
            // another, the window on one Space and the sidebar showing the next, and `going` set for good)
            going = false
            let target = model.string("current")
            guard spaces.indices.contains(page), spaces[page].spaceId != target,
                  spaces.contains(where: { $0.spaceId == target }) else { return }
            current = nil  // (then the Space: set straight to it, it may already think it's there)
            DispatchQueue.main.async { current = target }
        })
        .onChange(of: settling.settled) { _ in landed() }
        .onChange(of: settling.touched) { _ in landing = false }  // (fingers on it again: not done with after all)
        .onChange(of: position) { _ in
            if landing { landed() }
            heading()
        }
        .environment(\.sidebarScrolling, moving)
    }

    /// A swipe more than halfway to another Space: said, so its tab starts loading before it's there.
    private func heading() {
        let index = Int(position.rounded())
        guard settling.waiting, spaces.indices.contains(index) else { return }
        let id = spaces[index].spaceId
        guard id != model.string("current"), id != headingFor else { return }
        headingFor = id
        model.act("heading", ["id": id])
    }

    /// A swipe done with (Settling): told where it's come to rest. Read from the scroll's own offset, not the scroll
    /// position's id or its phases, neither of which is always told of a swipe: the id was left on the Space before
    /// till something redrew the sidebar (a page open in a tab, say: the window changed Space seconds late, or with
    /// nothing to redraw it, not at all), and the phase could say it was at rest a few points into a swipe.
    private func landed() {
        going = false  // (it's your fingers' scroll: a switch from Python it interrupted has given way)
        let page = position.rounded()
        guard abs(position - page) < 0.02 else {
            // Its spring's still taking it into place: told the moment it's there (waiting another moment and
            // looking again had it told up to a quarter of a second after it had arrived)
            landing = true
            return settling.poke()  // (and looked at again in a moment, should it stop just short)
        }
        landing = false
        settling.waiting = false
        headingFor = nil
        let index = Int(page)
        guard spaces.indices.contains(index) else { return }
        let id = spaces[index].spaceId
        guard id != model.string("current") else { return }
        model.merge(["current": id])  // (so a refresh before Python answers doesn't scroll it back)
        if current != id { current = id }
        model.act("swiped", ["id": id])
    }
}

/// When a swipe is done with: the scroll at rest and no more scrolling from the trackpad for a moment, so swiping
/// across several Spaces quickly, the window changes Space once, at the end (its change and fade, done after each
/// one, stalled the next). AppKit reports every live scroll of any scroll view in the app (not an animation's): each
/// from your fingers puts it off again. Their momentum doesn't: a trackpad's runs on a second or more after a flick,
/// long after the Space has snapped into place, and waiting it out had the window change Space seconds late.
@MainActor
private final class Settling: ObservableObject {
    @Published private(set) var settled = 0
    var waiting = false
    private var task: Task<Void, Never>?
    private var observers: [NSObjectProtocol] = []
    @Published private(set) var touched = 0  // (a scroll from your fingers: counted)
    private let quiet: UInt64 = 150_000_000

    func start() {
        guard observers.isEmpty else { return }
        for name in [NSScrollView.willStartLiveScrollNotification, NSScrollView.didLiveScrollNotification,
                     NSScrollView.didEndLiveScrollNotification] {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    if let event = NSApp.currentEvent, event.type == .scrollWheel, !event.momentumPhase.isEmpty {
                        return
                    }
                    self?.waiting = true
                    self?.touched += 1
                    self?.poke()
                }
            })
        }
    }

    func stop() {
        observers.forEach(NotificationCenter.default.removeObserver)
        observers = []
        cancel()
    }

    /// Wait a moment (again) before it's settled.
    func poke() {
        task?.cancel()
        task = Task { @MainActor [weak self, quiet] in
            try? await Task.sleep(nanoseconds: quiet)
            if !Task.isCancelled { self?.settled += 1 }
        }
    }

    func cancel() {
        waiting = false
        task?.cancel()
    }
}

/// `moving` while the scroll is (fingers on it, or its spring still settling), `position` the Space it's scrolled to
/// (from its offset), and `animated` with the page an animated switch came to rest on, on macOS 15 and later.
private struct AtRest: ViewModifier {
    @Binding var moving: Bool
    @Binding var position: Double
    let animated: (Int) -> Void

    func body(content: Content) -> some View {
        if #available(macOS 15, *) {
            content
                .onScrollGeometryChange(for: Double.self) { geometry in
                    geometry.containerSize.width > 0 ? geometry.contentOffset.x / geometry.containerSize.width : 0
                } action: { _, now in
                    position = now
                }
                .onScrollPhaseChange { old, phase, context in
                    moving = phase != .idle
                    let width = context.geometry.containerSize.width
                    if old == .animating, phase == .idle, width > 0 {
                        animated(Int((context.geometry.contentOffset.x / width).rounded()))
                    }
                }
        } else {
            content
        }
    }
}

/// Paging, a Space at a time: on a Mac, the system's paging let a quick swipe's momentum carry it past the next Space
/// to the one after. Wherever the swipe would have ended, it lands on the next Space over (or back where it started,
/// for a swipe that hardly went anywhere).
@available(macOS 14, *)
private struct OneSpaceAtATime: ScrollTargetBehavior {
    func updateTarget(_ target: inout ScrollTarget, context: TargetContext) {
        let width = context.containerSize.width
        guard width > 0 else { return }
        let start = (context.originalTarget.rect.minX / width).rounded()
        let travel = target.rect.minX - start * width
        let step: CGFloat = abs(travel) >= width * 0.3 ? (travel > 0 ? 1 : -1) : 0
        let last = max(0, (context.contentSize.width / width).rounded() - 1)
        target.rect.origin.x = min(max(start + step, 0), last) * width
    }
}

private struct SidebarScrolling: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// The Spaces scrolling sideways: rows don't light up or open previews as they pass under the pointer.
    var sidebarScrolling: Bool {
        get { self[SidebarScrolling.self] }
        set { self[SidebarScrolling.self] = newValue }
    }
}

private extension Dictionary where Key == String, Value == Any {
    var spaceId: String { str("id") }
}

@available(macOS 14, *)
private struct SpaceList: View {
    let space: JSON
    let look: SidebarLook
    @ObservedObject var model: PageModel

    var body: some View {
        let pinned = space.list("pinned"), today = space.list("today")
        ScrollViewReader { proxy in
            ScrollView(.vertical) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    header
                    if pinned.isEmpty, !space.str("hint").isEmpty {
                        Text(space.str("hint"))
                            .font(.system(size: 12))
                            .foregroundStyle(look.text.opacity(0.5))
                            .frame(maxWidth: .infinity, minHeight: rowHeight)
                            .modifier(DropAt(model: model, look: look, space: space.str("id"), section: "pinned",
                                             index: 0))
                    }
                    ForEach(Array(pinned.enumerated()), id: \.element.nodeId) { index, node in
                        NodeView(node: node, depth: 0, section: "pinned", index: index, parent: nil, locked: false,
                                 look: look, model: model, space: space.str("id"))
                            .transition(.opacity.combined(with: .move(edge: .leading)))
                    }
                    divider(after: pinned.count)
                    NewTabRow(look: look, model: model)
                    ForEach(Array(today.enumerated()), id: \.element.nodeId) { index, node in
                        NodeView(node: node, depth: 0, section: "today", index: index, parent: nil, locked: false,
                                 look: look, model: model, space: space.str("id"))
                            .transition(.asymmetric(insertion: .opacity.combined(with: .move(edge: .top)),
                                                    removal: .opacity.combined(with: .move(edge: .bottom))))
                    }
                    Color.clear.frame(height: 40)  // (room to drop a tab at the end of Today)
                        .contentShape(Rectangle())
                        .modifier(DropAt(model: model, look: look, space: space.str("id"), section: "today",
                                         index: today.count))
                }
                .padding(.top, 2)
                // Rows grow in and shrink out, folders open and close, Clear sweeps Today away (as Qt's do)
                .animation(.snappy(duration: 0.22), value: structure(pinned + today))
            }
            .scrollIndicators(.automatic)
            .mask {  // (the list fades out at the bottom, as Qt's ListFade)
                VStack(spacing: 0) {
                    Color.black
                    LinearGradient(colors: [.black, .clear], startPoint: .top, endPoint: .bottom).frame(height: 14)
                }
            }
            .onChange(of: model.string("selected_id")) { id in
                // A tab you switch to that's out of sight comes into view (only on switching, as Qt's)
                guard !id.isEmpty, space.str("id") == model.string("current") else { return }
                withAnimation(.smooth(duration: 0.25)) { proxy.scrollTo(id) }
            }
        }
    }

    /// What's in the list and which folders are open: what an animation follows (not titles or icons changing).
    private func structure(_ nodes: [JSON]) -> [String] {
        nodes.flatMap { node in
            [node.str("id") + (node.flag("expanded") ? "+" : "")] + structure(node.list("children"))
        }
    }

    private var header: some View {
        Button {
            model.act("space_title")
        } label: {
            HStack(spacing: 6) {
                if let icon = model.images[space.str("icon")] {
                    Image(nsImage: icon).resizable().frame(width: 16, height: 16)
                } else if !space.str("emoji").isEmpty {
                    Text(space.str("emoji")).font(.system(size: 14))
                }
                if !model.bool("compact") {
                    Text(space.str("name")).font(.system(size: 13, weight: .semibold)).foregroundStyle(look.text)
                }
                if !space.str("profile").isEmpty && !model.bool("compact") {
                    Text(space.str("profile"))
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(look.text.opacity(0.6))
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(look.text.opacity(0.1), in: Capsule())
                        .help(space.str("profile_tip"))
                }
            }
            .padding(.horizontal, 8).padding(.vertical, 5)
            .frame(maxWidth: model.bool("compact") ? .infinity : nil)
            .contentShape(Rectangle())
        }
        .buttonStyle(HoverButtonStyle(look: look))
        .help("Edit Space: name, icon, color, profile")
        .overlay(RightClick { model.act("space_title") } middle: {})  // (as Qt's: right-click edits it too)
        .padding(.bottom, 4)
    }

    /// The line between Pinned and Today, with Tidy and Clear; a tab dropped on it goes at the end of Pinned.
    private func divider(after pinned: Int) -> some View {
        HStack(spacing: 4) {
            Rectangle().fill(look.text.opacity(0.10)).frame(height: 1)
            let tidy = space["tidy"] as? JSON ?? [:]
            if !tidy.str("label").isEmpty {
                Button(tidy.str("label")) { model.act("tidy") }
                    .buttonStyle(SmallButtonStyle(look: look))
                    .disabled(!tidy.flag("enabled", true))
                    .help(tidy.str("tip"))
            }
            if space.flag("clear") {
                Button("↓ Clear") { model.act("clear") }
                    .buttonStyle(SmallButtonStyle(look: look))
                    .help("Archive all of today's tabs in this Space")
            }
        }
        .padding(.leading, 6).padding(.top, 8).padding(.bottom, 2)
        .contentShape(Rectangle())
        .modifier(DropAt(model: model, look: look, space: space.str("id"), section: "pinned", index: pinned))
    }
}

private extension Dictionary where Key == String, Value == Any {
    var nodeId: String { str("id") }
}

@available(macOS 14, *)
private struct NodeView: View {
    let node: JSON
    let depth: Int
    let section: String
    let index: Int
    let parent: String?
    let locked: Bool  // (its folder's a Live Folder's: nothing's dropped beside it)
    let look: SidebarLook
    @ObservedObject var model: PageModel
    let space: String

    var body: some View {
        if node.str("kind") == "folder" {
            VStack(alignment: .leading, spacing: 0) {
                FolderRow(folder: node, depth: depth, look: look, model: model)
                    .modifier(RowDrop(model: model, look: look, space: space, section: section, index: index,
                                      parent: parent, depth: depth, locked: locked, folder: node))
                if node.flag("expanded") {
                    let children = node.list("children")
                    ForEach(Array(children.enumerated()), id: \.element.nodeId) { i, child in
                        NodeView(node: child, depth: depth + 1, section: section, index: i, parent: node.str("id"),
                                 locked: node.flag("locked"), look: look, model: model, space: space)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    }
                }
            }
            .clipped()  // (its tabs slide out from under it as it opens)
        } else {
            TabRow(tab: node, depth: depth, look: look, model: model)
                .id(node.str("id"))  // (scrolled to when it's the tab you switch to)
                .modifier(RowDrop(model: model, look: look, space: space, section: section, index: index,
                                  parent: parent, depth: depth, locked: locked, folder: nil))
        }
    }
}

/// Where a dragged tab would go, as Qt's sidebar decides: the top half of a row is before it, the bottom half after
/// (or first inside an open folder), and the middle third of a folder row is into it, at its end.
private enum DropMark: Equatable {
    case above
    case below(depth: Int)
    case into
}

private struct DropLine: View {
    let look: SidebarLook
    let depth: Int

    var body: some View {
        Capsule().fill(look.text.opacity(0.85)).frame(height: 2)
            .padding(.leading, 8 + CGFloat(depth) * indent).padding(.trailing, 8)
    }
}

private struct RowDrop: ViewModifier {
    @ObservedObject var model: PageModel
    let look: SidebarLook
    let space: String
    let section: String
    let index: Int
    let parent: String?
    let depth: Int
    let locked: Bool
    let folder: JSON?
    @State private var mark: DropMark?

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .top) { if mark == .above { DropLine(look: look, depth: depth).offset(y: -1) } }
            .overlay(alignment: .bottom) {
                if case .below(let d) = mark { DropLine(look: look, depth: d).offset(y: 1) }
            }
            .overlay {
                if mark == .into {
                    RoundedRectangle(cornerRadius: 9, style: .continuous).stroke(look.text.opacity(0.7), lineWidth: 1.5)
                        .padding(1)
                }
            }
            .onDrop(of: TabDrag.types, delegate: Delegate(row: self, mark: $mark))
    }

    /// The mark, folder and index for a drop `y` points down the row; nil where it can't go (a Live Folder).
    func target(_ y: CGFloat) -> (mark: DropMark, folder: String?, index: Int)? {
        if let folder {
            let third = rowHeight / 3
            if y >= third && y <= rowHeight - third {
                return folder.flag("locked") ? nil : (.into, folder.str("id"), folder.int("size") ?? 0)
            }
        }
        if y < rowHeight / 2 { return locked ? nil : (.above, parent, index) }
        if let folder, folder.flag("expanded") {
            return folder.flag("locked") ? nil : (.below(depth: depth + 1), folder.str("id"), 0)
        }
        return locked ? nil : (.below(depth: depth), parent, index + 1)
    }

    struct Delegate: DropDelegate {
        let row: RowDrop
        @Binding var mark: DropMark?

        func validateDrop(info: DropInfo) -> Bool { info.hasItemsConforming(to: TabDrag.types) }

        func dropUpdated(info: DropInfo) -> DropProposal? {
            let target = row.target(info.location.y)
            mark = target?.mark
            return DropProposal(operation: target == nil ? .forbidden : .move)
        }

        func dropExited(info: DropInfo) { mark = nil }

        func performDrop(info: DropInfo) -> Bool {
            let target = row.target(info.location.y)
            mark = nil
            guard let target else { return false }
            let model = row.model, space = row.space, section = row.section
            return TabDrag.take(info.itemProviders(for: TabDrag.types)) { id in
                var event: [String: Any] = ["id": id, "space": space, "section": section, "index": target.index]
                if let folder = target.folder { event["folder"] = folder }
                model.act("move", event)
            }
        }
    }
}

/// A place that isn't a row that a tab drops on (the empty Pinned hint, the divider, the end of Today): there, in
/// that section, with the line above it.
private struct DropAt: ViewModifier {
    @ObservedObject var model: PageModel
    let look: SidebarLook
    let space: String
    let section: String
    let index: Int
    @State private var over = false

    func body(content: Content) -> some View {
        content
            .overlay(alignment: .top) { if over { DropLine(look: look, depth: 0).offset(y: -1) } }
            .onDrop(of: TabDrag.types, isTargeted: $over) { providers in
                TabDrag.take(providers) { id in
                    model.act("move", ["id": id, "space": space, "section": section, "index": index])
                }
            }
    }
}

@available(macOS 14, *)
private struct TabRow: View {
    let tab: JSON
    let depth: Int
    let look: SidebarLook
    @ObservedObject var model: PageModel
    @State private var hovering = false
    @Environment(\.sidebarScrolling) private var scrolling
    @State private var part: String?
    @State private var frame = FrameBox()

    private var id: String { tab.str("id") }

    /// The pointer on the row or off it: its highlight and its tab preview (none while the Spaces scroll past it).
    private func hovered(_ now: Bool) {
        guard now != hovering, !(now && scrolling) else { return }
        hovering = now
        model.act("hover", now ? ["id": id, "y": frame.rect.minY, "height": frame.rect.height] : ["id": ""])
    }

    var body: some View {
        if model.bool("compact") { compactRow } else { fullRow }
    }

    /// Collapsed to icons: the icon alone, in the middle, with a dot for anything unread.
    private var compactRow: some View {
        icon
            .overlay(alignment: .topTrailing) {
                if !tab.str("badge").isEmpty { Circle().fill(look.accent).frame(width: 7, height: 7).offset(x: 3, y: -3) }
            }
            .frame(maxWidth: .infinity)
            .frame(height: rowHeight)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(rowFill))
            .contentShape(Rectangle())
            .onHover(perform: hovered)
            .onChange(of: scrolling) { now in if now { hovered(false) } }
            .background(GeometryReader { geometry in
                let _ = frame.rect = geometry.frame(in: .named("sidebarBody"))
                Color.clear
            })
            .gesture(TapGesture().modifiers(.command).onEnded { model.act("activate", ["id": id, "mark": "add"]) })
            .gesture(TapGesture().modifiers(.shift).onEnded { model.act("activate", ["id": id, "mark": "extend"]) })
            .onTapGesture { model.act("activate", ["id": id]) }
            .overlay(RightClick { model.act("menu", ["id": id, "kind": "tab"]) } middle: { model.act("middle", ["id": id]) })
            .onDrag { TabDrag.provider(id, model: model) } preview: { dragPicture }
            .help(tab.str("tip").isEmpty ? "" : tab.str("tip"))
            .accessibilityElement(children: .combine)
            .accessibilityLabel(tab.str("label").isEmpty ? tab.str("title") : tab.str("label"))
            .accessibilityHint(tab.str("hint"))
            .accessibilityAddTraits(tab.flag("selected") ? [.isButton, .isSelected] : .isButton)
    }

    /// What's dragged: the row, or a stack with a count when it's one of several picked.
    private var dragPicture: some View {
        let marked = model.state["marked"] as? [String] ?? []
        return DragPicture(title: tab.str("title"), image: model.images[tab.str("icon")],
                           count: marked.count > 1 && marked.contains(id) ? marked.count : 1)
    }

    private var rowFill: Color {
        tab.flag("selected") || tab.flag("marked") ? look.selected
            : hovering || tab.flag("insplit") ? look.hover : .clear  // (the other tabs of your split, as Qt's)
    }

    private var fullRow: some View {
        HStack(spacing: 9) {
            icon
            if tab.flag("away") {
                Text("/").font(.system(size: 11)).foregroundStyle(look.text.opacity(0.4))
            }
            Text(tab.str("title"))
                .font(.system(size: 13, weight: tab.flag("fresh") ? .bold : .medium))
                .foregroundStyle(look.text.opacity(tab.flag("dim") ? 0.72 : 1))
                .lineLimit(1)
            Spacer(minLength: 0)
            if !tab.str("badge").isEmpty {
                Text(tab.str("badge"))
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(look.accentText)
                    .padding(.horizontal, 5)
                    .frame(minWidth: 16, minHeight: 16)
                    .background(look.accent, in: Capsule())
            }
            if !tab.str("audio").isEmpty {
                RowButton(symbol: tab.str("audio") == "muted" ? "speaker.slash.fill" : "speaker.wave.2.fill",
                          look: look) { model.act("audio", ["id": id]) }
                    .help(tab.str("audio") == "muted" ? "Unmute Tab" : "Mute Tab")
            }
            if (hovering || tab.flag("selected")) && !tab.str("close").isEmpty {
                RowButton(symbol: tab.str("close") == "unload" ? "minus" : "xmark", look: look) {
                    model.act("close", ["id": id])
                }
                .help(tab.str("close") == "unload" ? "Unload Tab" : tab.flag("selected") ? "Close Tab (⌘W)" : "Close Tab")
            }
        }
        .padding(.leading, 10 + CGFloat(depth) * indent)
        .padding(.trailing, 5)
        .frame(height: rowHeight)
        .background { RoundedRectangle(cornerRadius: 9, style: .continuous).fill(rowFill) }
        .contentShape(Rectangle())
        .background(GeometryReader { geometry in
            // (where the row is, kept without redrawing it: its tab preview opens beside it)
            let _ = frame.rect = geometry.frame(in: .named("sidebarBody"))
            Color.clear
        })
        .onHover(perform: hovered)
        .onChange(of: scrolling) { now in if now { hovered(false) } }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .gesture(TapGesture().modifiers(.command).onEnded { model.act("activate", ["id": id, "mark": "add"]) })
        .gesture(TapGesture().modifiers(.shift).onEnded { model.act("activate", ["id": id, "mark": "extend"]) })
        .onTapGesture { model.act("activate", ["id": id]) }
        .overlay(RightClick { model.act("menu", ["id": id, "kind": "tab"]) } middle: { model.act("middle", ["id": id]) })
        .onDrag { TabDrag.provider(id, model: model) } preview: { dragPicture }
        .help(tab.str("tip"))
        .accessibilityElement(children: .combine)
        .accessibilityLabel(tab.str("label").isEmpty ? tab.str("title") : tab.str("label"))
        .accessibilityHint(tab.str("hint"))
        .accessibilityAddTraits(tab.flag("selected") ? [.isButton, .isSelected] : .isButton)
    }

    @ViewBuilder private var icon: some View {
        let picture = model.images[tab.str("icon")]
        Group {
            if tab.flag("away") && part == "home" {
                Image(systemName: "arrow.uturn.left").font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(look.text)
            } else if let picture {
                Image(nsImage: picture).resizable().interpolation(.high)
            } else {
                RoundedRectangle(cornerRadius: 4).fill(look.text.opacity(0.15))
            }
        }
        .frame(width: 16, height: 16)
        .modifier(HomeButton(away: tab.flag("away"), part: $part) { model.act("home", ["id": id]) })
    }
}

/// A tab that's wandered from its pinned page: its icon takes it back (the row's own clicks everywhere else).
private struct HomeButton: ViewModifier {
    let away: Bool
    @Binding var part: String?
    let home: () -> Void

    func body(content: Content) -> some View {
        if away {
            content
                .contentShape(Rectangle())
                .onHover { part = $0 ? "home" : nil }
                .onTapGesture(perform: home)
                .help("Back to the pinned page")
        } else {
            content
        }
    }
}

@available(macOS 14, *)
private struct FolderRow: View {
    let folder: JSON
    let depth: Int
    let look: SidebarLook
    @ObservedObject var model: PageModel
    @State private var hovering = false
    @Environment(\.sidebarScrolling) private var scrolling

    var body: some View {
        let compact = model.bool("compact")
        HStack(spacing: 9) {
            if compact { Spacer(minLength: 0) }
            Group {
                if let picture = model.images[folder.str("icon")] {
                    Image(nsImage: picture).resizable()
                } else {
                    Image(systemName: folder.flag("expanded") ? "folder" : "folder.fill")
                }
            }
            .frame(width: 16, height: 16)
            .overlay(alignment: .topTrailing) {
                if compact && !folder.str("count").isEmpty {
                    Circle().fill(look.accent).frame(width: 7, height: 7).offset(x: 3, y: -3)
                }
            }
            if !compact {
                Text(folder.str("name")).font(.system(size: 13, weight: .medium)).foregroundStyle(look.text).lineLimit(1)
            }
            Spacer(minLength: 0)
            if !compact && !folder.str("count").isEmpty {
                Text(folder.str("count")).font(.system(size: 11)).foregroundStyle(look.text.opacity(0.55))
            }
        }
        .padding(.leading, compact ? 0 : 10 + CGFloat(depth) * indent)
        .padding(.trailing, compact ? 0 : 10)
        .help(compact ? "\(folder.str("name"))\n\(folder.str("tip"))".trimmingCharacters(in: .whitespacesAndNewlines)
                      : folder.str("tip"))
        .frame(height: rowHeight)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(hovering ? look.hover : .clear))
        .contentShape(Rectangle())
        .onHover { hovering = $0 && !scrolling }
        .onChange(of: scrolling) { now in if now { hovering = false } }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .onTapGesture { model.act("folder", ["id": folder.str("id")]) }
        .overlay(RightClick { model.act("menu", ["id": folder.str("id"), "kind": "folder"]) } middle: {})
        .modifier(DraggableUnless(model: model, fixed: folder.flag("fixed"), id: folder.str("id"), title: folder.str("name")))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(folder.str("name")), folder, \(folder.flag("expanded") ? "open" : "closed")")
    }
}

private struct DraggableUnless: ViewModifier {
    @ObservedObject var model: PageModel
    let fixed: Bool
    let id: String
    let title: String

    func body(content: Content) -> some View {
        if fixed {
            content
        } else {
            content.onDrag { TabDrag.provider(id, model: model) } preview: { DragPicture(title: title, image: nil, count: 1) }
        }
    }
}

@available(macOS 14, *)
private struct NewTabRow: View {
    let look: SidebarLook
    @ObservedObject var model: PageModel
    @State private var hovering = false
    @Environment(\.sidebarScrolling) private var scrolling

    var body: some View {
        let compact = model.bool("compact")
        HStack(spacing: 9) {
            if compact { Spacer() }
            Image(systemName: "plus").font(.system(size: 12, weight: .medium)).frame(width: 16, height: 16)
            if !compact { Text("New Tab").font(.system(size: 13, weight: .medium)) }
            Spacer()
        }
        .foregroundStyle(look.text.opacity(0.65))
        .padding(.leading, compact ? 0 : 10)
        .help(compact ? "New Tab" : "")
        .frame(height: rowHeight)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(hovering ? look.hover : .clear))
        .contentShape(Rectangle())
        .onHover { hovering = $0 && !scrolling }
        .onChange(of: scrolling) { now in if now { hovering = false } }
        .onTapGesture { model.act("new_tab") }
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel("New Tab")
    }
}

/// A row's place, kept by reference: written as it's laid out, read when the pointer comes, never a redraw.
final class FrameBox {
    var rect = CGRect.zero
}

private struct RowButton: View {
    let symbol: String
    let look: SidebarLook
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(look.text.opacity(hovering ? 0.9 : 0.6))
                .frame(width: 22, height: 22)
                .background(RoundedRectangle(cornerRadius: 6).fill(hovering ? look.text.opacity(0.1) : .clear))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

private struct HoverButtonStyle: ButtonStyle {
    let look: SidebarLook
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(RoundedRectangle(cornerRadius: 8).fill(hovering ? look.hover : .clear))
            .onHover { hovering = $0 }
            .opacity(configuration.isPressed ? 0.7 : 1)
    }
}

private struct SmallButtonStyle: ButtonStyle {
    let look: SidebarLook
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(look.text.opacity(hovering ? 1 : 0.55))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 6).fill(hovering ? look.hover : .clear))
            .onHover { hovering = $0 }
    }
}

/// A tab dragged, in Qt's own form (its TAB_MIME, as Qt names it on the Mac's pasteboard), so it drops onto Qt's
/// Favorites, the page (to split) and the Space buttons, and a tab dragged from Qt drops here. Python hears when a
/// drag starts ("drag"), to open the page's drop zone for it.
enum TabDrag {
    static let qtType = "com.trolltech.anymime.application--x-eclipse-tab"
    static let qtManyType = "com.trolltech.anymime.application--x-eclipse-tabs"  // (TABS_MIME: ids, in order)
    static let types = [UTType(importedAs: qtType)]

    @MainActor
    static func provider(_ id: String, model: PageModel) -> NSItemProvider {
        model.act("drag", ["id": id])
        let provider = NSItemProvider()
        provider.registerDataRepresentation(forTypeIdentifier: qtType, visibility: .ownProcess) { done in
            done(Data(id.utf8), nil)
            return nil
        }
        let marked = model.state["marked"] as? [String] ?? []
        if marked.count > 1 && marked.contains(id) {  // (one of several picked: they all go)
            provider.registerDataRepresentation(forTypeIdentifier: qtManyType, visibility: .ownProcess) { done in
                done(Data(marked.joined(separator: ",").utf8), nil)
                return nil
            }
        }
        return provider
    }

    /// The dropped tab's id, to `then` (on the main thread); false if it isn't one.
    static func take(_ providers: [NSItemProvider], then: @escaping @MainActor (String) -> Void) -> Bool {
        guard let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(qtType) }) else { return false }
        // Several dragged together: their ids, comma-separated, as one (Python moves them all, in order)
        let type = provider.hasItemConformingToTypeIdentifier(qtManyType) ? qtManyType : qtType
        provider.loadDataRepresentation(forTypeIdentifier: type) { data, _ in
            guard let data, let id = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { MainActor.assumeIsolated { then(id) } }
        }
        return true
    }
}

private struct DragPicture: View {
    let title: String
    let image: NSImage?
    let count: Int

    var body: some View {
        HStack(spacing: 8) {
            if let image { Image(nsImage: image).resizable().frame(width: 16, height: 16) }
            Text(title).font(.system(size: 13, weight: .medium)).lineLimit(1)
            if count > 1 {  // (several picked: how many go)
                Text("\(count)").font(.system(size: 11, weight: .bold)).foregroundStyle(.white)
                    .padding(.horizontal, 6).padding(.vertical, 1)
                    .background(Color.accentColor, in: Capsule())
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 9))
        .background {  // (and a card or two behind it, as Qt's stack)
            if count > 1 {
                RoundedRectangle(cornerRadius: 9).fill(.regularMaterial).offset(x: 4, y: 4).opacity(0.7)
            }
        }
        .padding(count > 1 ? 4 : 0)
    }
}

/// Right and middle clicks, which SwiftUI's gestures don't give: right opens Python's own menu (the same one Qt's
/// sidebar shows), middle closes the tab.
private struct RightClick: NSViewRepresentable {
    let right: () -> Void
    let middle: () -> Void

    func makeNSView(context: Context) -> ClickCatcher {
        let view = ClickCatcher()
        view.right = right
        view.middle = middle
        return view
    }

    func updateNSView(_ view: ClickCatcher, context: Context) {
        view.right = right
        view.middle = middle
    }

    final class ClickCatcher: NSView {
        var right: () -> Void = {}
        var middle: () -> Void = {}

        // Only right and middle clicks stop here: everything else goes on to the row beneath
        override func hitTest(_ point: NSPoint) -> NSView? {
            guard let event = NSApp.currentEvent else { return nil }
            switch event.type {
            case .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp: return super.hitTest(point)
            default: return nil
            }
        }

        override func rightMouseDown(with event: NSEvent) { right() }
        override func otherMouseDown(with event: NSEvent) { if event.buttonNumber == 2 { middle() } }
        override func menu(for event: NSEvent) -> NSMenu? { nil }
    }
}
