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
// "drop_test" (the tests'): {ids, x, y, width, height} drops tabs at a point as a drag would, with "hover_only" holds
// one there, or without ids, says where every drop spot is (SidebarDrops). "dragging": the tab being carried.

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
    @State private var leash = FingerLeash()  // (a hard swipe's fingers drag the Spaces most of one over, no further)
    @State private var chosen = SwipeChoice()  // (the Space a swipe chose as your fingers left: OneSpaceAtATime)
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
            .background(ScrollViewProbe { leash.scrollView = $0 })
        }
        .scrollTargetBehavior(OneSpaceAtATime(chosen: chosen))
        .scrollPosition(id: $current)
        .scrollIndicators(.never)
        .scrollClipDisabled(false)
        .coordinateSpace(.named("sidebarBody"))
        .background(MeterView(meter: meter).frame(width: 1, height: 1))
        .onAppear { current = model.string("current"); settling.start(); leash.start() }
        .onDisappear { meter.stop(); settling.stop(); leash.stop() }
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
        .modifier(AtRest(moving: $moving, position: $position, touched: { here in chosen.from = here.rounded() },
                             stopped: { if settling.waiting { settling.poke(after: 0) } }) { page in
            // A switch from Python come to rest: on its Space (switches interrupting each other could leave it on
            // another, the window on one Space and the sidebar showing the next, and `going` set for good)
            going = false
            chosen.from = Double(page)  // (where the next swipe starts)
            let target = model.string("current")
            guard spaces.indices.contains(page), spaces[page].spaceId != target,
                  spaces.contains(where: { $0.spaceId == target }) else { return }
            current = nil  // (then the Space: set straight to it, it may already think it's there)
            DispatchQueue.main.async { current = target }
        })
        .onChange(of: settling.settled) { _ in landed() }
        .onChange(of: position) { now in chosen.position = now }
        .onChange(of: model.string("dragging").isEmpty) { idle in
            // (the button's up, as Python sees it: however the drag ended, the room it opened closes)
            if idle { SidebarDrops.of(model.id).ended() }
        }
        .onChange(of: model.state["drop_test"] as? JSON != nil) { _ in
            // (tests/live: a tab dropped at a point, as a drag would, and where every drop spot is)
            guard let test = model.state["drop_test"] as? JSON else { return }
            let point = CGPoint(x: (test["x"] as? NSNumber)?.doubleValue ?? 0, y: (test["y"] as? NSNumber)?.doubleValue ?? 0)
            let bounds = CGRect(x: 0, y: 0, width: (test["width"] as? NSNumber)?.doubleValue ?? 10_000,
                                height: (test["height"] as? NSNumber)?.doubleValue ?? 10_000)
            if let path = test["path"] as? [[NSNumber]], let ids = test["ids"] as? String {
                // (a whole drag, as macOS sends it, through the sidebar view's own handlers: Embedded.swift)
                let points = path.map { CGPoint(x: $0[0].doubleValue, y: $0[1].doubleValue) }
                // ("stale": the drag pasteboard still holding the drag before's tab, as a quick drop could read it)
                if test["stale"] is String { SidebarDrops.of(model.id).carrying = ids }
                let pasted = test["stale"] as? String ?? ids
                let result = (EmbeddedView.views[model.id]?.host as? DragTesting)?.testDrag(points, ids: pasted)
                model.act("drop_test", result ?? ["error": "no sidebar view"])
            } else if test["ghosts"] as? Bool == true {
                // (another Space's spots left where it last showed, over this one's: none of them should take a drop)
                SidebarDrops.of(model.id).haunt { section in
                    { _, ids in
                        model.act("move", ["id": ids, "space": "ghost", "section": section, "index": 0])
                        return true
                    }
                }
                model.act("drop_test", ["ghosts": true])
            } else if test["room"] as? Bool == true {  // (where the rows part, if they do)
                let room = SidebarDrops.of(model.id).room
                model.act("drop_test", room.map { ["y": $0.y, "depth": $0.depth] } ?? ["y": -1])
            } else if test["hover_only"] as? Bool == true {  // (held over a spot: no drop)
                model.act("drop_test", ["over": SidebarDrops.of(model.id).over(point, in: bounds)])
            } else if let ids = test["ids"] as? String {
                let over = SidebarDrops.of(model.id).over(point, in: bounds)
                let dropped = SidebarDrops.of(model.id).drop(point, in: bounds, ids: ids)
                model.act("drop_test", ["over": over, "dropped": dropped])
            } else {
                model.act("drop_test", ["spots": SidebarDrops.of(model.id).places.mapValues {
                    [$0.minX, $0.minY, $0.width, $0.height]
                }])
            }
        }
        .onChange(of: settling.touched) { _ in landing = false }  // (fingers on it again: not done with after all)
        .onChange(of: position) { _ in
            if landing { landed() }
            heading()
        }
        .environment(\.sidebarScrolling, moving)
        .environment(\.sidebarDrops, SidebarDrops.of(model.id))
        .onAppear { SidebarDrops.of(model.id).space = model.string("current") }
        .onChange(of: model.string("current")) { now in SidebarDrops.of(model.id).space = now }
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
        let heading = chosen.page.flatMap { ProcessInfo.processInfo.systemUptime - chosen.at < SwipeChoice.lasts ? $0 : nil }
        guard abs(position - page) < 0.02, heading.map({ $0 == CGFloat(page) }) ?? true else {
            // (or passing another Space on its way to the one the swipe chose: said there, the window went to the one
            // passed, and pulled the sidebar back from the one it was going to)
            // Its spring's still taking it into place: told the moment it's there (waiting another moment and
            // looking again had it told up to a quarter of a second after it had arrived)
            landing = true
            return settling.poke()  // (and looked at again in a moment, should it stop just short)
        }
        landing = false
        settling.waiting = false
        chosen.from = Double(page)  // (where the next swipe starts, should its fingers going down not be told)
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

    /// Wait a moment (again) before it's settled: `after` nanoseconds, the quiet moment if not given. (0: the
    /// Spaces have stopped, a swipe's done with now. Waited for the moment after the last scroll from the fingers,
    /// only told late of it, the window changed Space up to two seconds after the sidebar)
    func poke(after: UInt64? = nil) {
        task?.cancel()
        task = Task { @MainActor [weak self, quiet] in
            try? await Task.sleep(nanoseconds: after ?? quiet)
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
    var touched: (Double) -> Void = { _ in }  // (fingers down on the trackpad: where the Spaces are scrolled to then)
    var stopped: () -> Void = {}  // (the Spaces at rest again, however they moved)
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
                    if phase == .interacting, old != .interacting, width > 0 {
                        touched(context.geometry.contentOffset.x / width)
                    }
                    if phase == .idle, old != .idle {
                        stopped()
                    }
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
///
/// It's asked as your fingers leave the trackpad, and asked again as the momentum after ends, with the scroll mid-way
/// as its "original target" (0.3 of the way from the Space two over, say): measured from there, a hard flick now and
/// then landed two Spaces over. So what was chosen as your fingers left stands.
@available(macOS 14, *)
private struct OneSpaceAtATime: ScrollTargetBehavior {
    let chosen: SwipeChoice

    func updateTarget(_ target: inout ScrollTarget, context: TargetContext) {
        let width = context.containerSize.width
        guard width > 0 else { return }
        let event = MainActor.assumeIsolated { NSApp?.currentEvent.flatMap { $0.type == .scrollWheel ? $0 : nil } }
        // A switch from Python (a click, a shortcut), not a swipe: its own Space, however far. (Asked at every step
        // of its animation, it was held to a Space at a time and taken for a swipe's choice)
        guard let event else { return }
        let momentum = !event.momentumPhase.isEmpty  // (phases are only a scroll's to ask: another event's raise)
        let now = ProcessInfo.processInfo.systemUptime
        if momentum, let page = chosen.page, now - chosen.at < SwipeChoice.lasts {
            target.rect.origin.x = page * width
            return
        }
        // From the Space your fingers went down on (mid-way through a swipe before, the one it was nearest), or where
        // it last came to rest, if that wasn't told. Not the "original target", which is only the last target asked
        // for: Space 0 on a launch on Space 1, say, and a hard swipe measured from there was a Space short, then
        // pulled back one. Nor where it is now, rounded: a swipe's fingers take it past halfway
        var start = CGFloat(chosen.from ?? chosen.position.rounded())
        if abs(CGFloat(chosen.position) - start) > 1, let page = chosen.page, now - chosen.at < SwipeChoice.lasts {
            start = page  // (more than a Space from there: swipes in a row, the fingers not said to go down again)
        }
        let travel = target.rect.minX - start * width
        let step: CGFloat = abs(travel) >= width * 0.3 ? (travel > 0 ? 1 : -1) : 0
        let last = max(0, (context.contentSize.width / width).rounded() - 1)
        target.rect.origin.x = min(max(start + step, 0), last) * width
        if !momentum {
            chosen.page = target.rect.origin.x / width
            chosen.at = now
        }
    }
}

/// How far your fingers can drag the Spaces: most of a Space from the one the swipe started on. The sidebar's narrow, and
/// a hard swipe's fingers dragged it three or four Spaces along before they let go, for it to fly back to the next one
/// (OneSpaceAtATime). Your fingers move it as they go for half a Space, then it eases towards three quarters (a wall
/// felt like a snag, and easing from the start like pushing against something), and letting go glides it the rest of
/// the way. The momentum after is dropped: the swipe's chosen its Space by then.
@MainActor
final class FingerLeash {
    static let free: CGFloat = 0.55  // (of a Space: as far as the Spaces go just as your fingers do)
    static let most: CGFloat = 0.75  // (and the most, held there: the rest is the glide onto it as your fingers let go)

    /// How far fingers that have gone `fingers` move the Spaces: just as far, to `free` of a Space, then easing towards
    /// `most`. Short of the next Space, so letting go glides onto it: brought all the way there under your fingers,
    /// there was nothing left to happen as they let go, and a hard swipe didn't feel like one.
    static func shown(_ fingers: CGFloat, width: CGFloat) -> CGFloat {
        let free = Self.free * width, ease = (Self.most - Self.free) * width
        let along = abs(fingers)
        let moved = along <= free ? along : free + ease * tanh((along - free) / ease)
        return fingers < 0 ? -moved : moved
    }
    weak var scrollView: NSScrollView? {
        didSet {
            guard let scrollView else { return }
            Self.leashes[ObjectIdentifier(scrollView)] = Weak(self)
            Self.intercept(type(of: scrollView))
        }
    }
    private var active = false
    private var fingers: CGFloat = 0  // (how far your fingers have gone this swipe, in points)
    private var shown: CGFloat = 0  // (and how far they've been let move the Spaces)
    private var dragging = false
    private var sideways = false  // (this swipe went sideways: its momentum's dropped)

    func start() { active = true }
    func stop() { active = false }

    // Scrolls are taken at the scroll view itself: in Eclipse they don't all go through the app's event queue (a
    // monitor there dropped them, and the Spaces moved anyway), but every one goes through its scrollWheel(with:)
    private final class Weak {
        weak var leash: FingerLeash?
        init(_ leash: FingerLeash) { self.leash = leash }
    }
    private static var leashes: [ObjectIdentifier: Weak] = [:]
    private static var intercepted: Set<ObjectIdentifier> = []

    private static func intercept(_ kind: AnyClass) {
        guard intercepted.insert(ObjectIdentifier(kind)).inserted else { return }
        let selector = #selector(NSResponder.scrollWheel(with:))
        guard let method = class_getInstanceMethod(kind, selector) else { return }
        typealias Original = @convention(c) (AnyObject, Selector, NSEvent) -> Void
        let original = unsafeBitCast(method_getImplementation(method), to: Original.self)
        let replacement: @convention(block) (AnyObject, NSEvent) -> Void = { view, event in
            let leash = MainActor.assumeIsolated { leashes[ObjectIdentifier(view)]?.leash }
            guard let leash else { return original(view, selector, event) }
            if let let_through = MainActor.assumeIsolated({ leash.leash(event) }) {
                original(view, selector, let_through)
            }
        }
        class_replaceMethod(kind, selector, imp_implementationWithBlock(replacement), method_getTypeEncoding(method))
    }

    /// The scroll to give the scroll view in place of `event`: itself, a shorter one, or none.
    private func leash(_ event: NSEvent) -> NSEvent? {
        guard active, let view = scrollView else { return event }
        if !event.momentumPhase.isEmpty {
            // A sideways swipe's momentum: it's chosen its Space, and is on its way there. Let through, the momentum
            // kept pushing the Spaces about as they went, and had the Space asked for again and again: a stutter as
            // your fingers let go. (An up-and-down scroll's, in a Space's tabs, is its own)
            guard sideways, abs(event.scrollingDeltaX) >= abs(event.scrollingDeltaY) else { return event }
            return nil
        }
        guard !event.phase.isEmpty else { return event }
        let width = view.contentView.bounds.width
        guard width > 0 else { return event }
        if event.phase.contains(.began) || event.phase.contains(.mayBegin) {
            fingers = 0
            shown = 0
            dragging = true
            sideways = false
        }
        if event.phase.contains(.ended) || event.phase.contains(.cancelled) {
            dragging = false
            return event
        }
        let dx = event.scrollingDeltaX
        guard dragging, dx != 0, abs(dx) >= abs(event.scrollingDeltaY) else { return event }
        sideways = true
        // Counted here, not read from the scroll view: its offset's told late, the same for several scrolls in a row,
        // and measured against that, the Spaces were pushed back against your fingers at the limit
        fingers += dx
        let now = Self.shown(fingers, width: width)
        let allowed = now - shown
        shown = now
        if abs(allowed - dx) < 0.01 { return event }
        if abs(allowed) < 0.05, !event.phase.contains(.began) { return nil }
        return Self.scaled(event, by: allowed / dx)
    }

    /// The same scroll, its sideways part `factor` as far: a new one, not a copy (a trackpad's scroll carries its
    /// fingers' own movement too, and a copy with smaller numbers still scrolled the whole way).
    static func scaled(_ event: NSEvent, by factor: CGFloat) -> NSEvent {
        guard let source = event.cgEvent else { return event }
        // (in points: the fixed-point fields are lines, a tenth as much)
        let dx = Double(source.getIntegerValueField(.scrollWheelEventPointDeltaAxis2)) * factor
        let dy = Double(source.getIntegerValueField(.scrollWheelEventPointDeltaAxis1))
        let lines = source.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2) * factor
        let linesY = source.getDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1)
        guard let fresh = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2,
                                  wheel1: Int32(dy.rounded()), wheel2: Int32(dx.rounded()), wheel3: 0) else { return event }
        fresh.location = source.location
        fresh.flags = source.flags
        fresh.timestamp = source.timestamp
        for raw: UInt32 in [88, 99, 123, 91, 92] {  // (continuous, its phase, momentum's phase, the windows it's for)
            if let field = CGEventField(rawValue: raw) {
                fresh.setIntegerValueField(field, value: source.getIntegerValueField(field))
            }
        }
        fresh.setIntegerValueField(.scrollWheelEventPointDeltaAxis1, value: Int64(dy.rounded()))
        fresh.setIntegerValueField(.scrollWheelEventPointDeltaAxis2, value: Int64(dx.rounded()))
        fresh.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis1, value: linesY)
        fresh.setDoubleValueField(.scrollWheelEventFixedPtDeltaAxis2, value: lines)
        guard let made = NSEvent(cgEvent: fresh) else { return event }
        return made
    }
}

/// Hands over the scroll view it's inside, once it's in one.
private struct ScrollViewProbe: NSViewRepresentable {
    let found: (NSScrollView) -> Void

    func makeNSView(context: Context) -> NSView { Probe(found: found) }
    func updateNSView(_ view: NSView, context: Context) {}

    final class Probe: NSView {
        let found: (NSScrollView) -> Void
        init(found: @escaping (NSScrollView) -> Void) {
            self.found = found
            super.init(frame: .zero)
        }
        required init?(coder: NSCoder) { nil }
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let scroll = enclosingScrollView { found(scroll) }
        }
        override func hitTest(_ point: NSPoint) -> NSView? { nil }  // (never in the way of a click)
    }
}

/// What a swipe chose as your fingers left the trackpad, kept for its momentum, and where the Spaces are scrolled to
/// (OneSpaceAtATime).
final class SwipeChoice {
    static let lasts: TimeInterval = 3  // (a trackpad's momentum runs a second or two: after that it's no swipe's)
    var page: CGFloat?
    var at: TimeInterval = 0
    var position = 0.0  // (the Spaces' scroll, in Spaces, as it is: kept up to date by the sidebar)
    var from: Double?  // (the Space your fingers last went down on, or the sidebar last came to rest on)
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
                            .modifier(DropAt(model: model, look: look, space: space.str("id"), place: "hint",
                                             section: "pinned", index: 0))
                    }
                    ForEach(Array(pinned.enumerated()), id: \.element.nodeId) { index, node in
                        NodeView(node: node, depth: 0, section: "pinned", index: index, parent: nil, locked: false,
                                 look: look, model: model, space: space.str("id"))
                            .transition(.opacity.combined(with: .move(edge: .leading)))
                    }
                    // The divider and New Tab are one place to drop: first in Today, the room under New Tab (on its own
                    // the divider was the end of Pinned, a thin strip right under the last pinned tab where a tab
                    // dragged out of Pinned landed back where it was, the room it opened pushing New Tab down)
                    VStack(alignment: .leading, spacing: 0) {
                        divider()
                        NewTabRow(look: look, model: model)
                    }
                    .modifier(DropAt(model: model, look: look, space: space.str("id"), place: "new tab",
                                     section: "today", index: 0, below: true))
                    ForEach(Array(today.enumerated()), id: \.element.nodeId) { index, node in
                        NodeView(node: node, depth: 0, section: "today", index: index, parent: nil, locked: false,
                                 look: look, model: model, space: space.str("id"))
                            .transition(.asymmetric(insertion: .opacity.combined(with: .move(edge: .top)),
                                                    removal: .opacity.combined(with: .move(edge: .bottom))))
                    }
                    Color.clear.frame(height: 40)  // (room to drop a tab at the end of Today)
                        .contentShape(Rectangle())
                        .modifier(DropAt(model: model, look: look, space: space.str("id"), place: "end",
                                         section: "today", index: today.count))
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
    private func divider() -> some View {
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
    var outer: FolderPlace? = nil  // (the folder it's in, where that is)
    var last = false  // (the last in its folder)

    /// Being dragged (it, or the picked tabs it's one of): faded where it was, so it isn't in two places at once.
    private var carried: Bool {
        let dragging = model.string("dragging"), id = node.str("id")
        guard !dragging.isEmpty else { return false }
        let marked = model.state["marked"] as? [String] ?? []
        return id == dragging || (marked.contains(dragging) && marked.contains(id))
    }

    var body: some View {
        if node.str("kind") == "folder" {
            VStack(alignment: .leading, spacing: 0) {
                FolderRow(folder: node, depth: depth, look: look, model: model)
                    .opacity(carried ? 0.35 : 1)
                    .modifier(RowDrop(model: model, look: look, id: node.str("id"), space: space, section: section,
                                      index: index, parent: parent, depth: depth, locked: locked, folder: node,
                                      outer: outer, last: last && !node.flag("expanded")))
                if node.flag("expanded") {
                    let children = node.list("children")
                    ForEach(Array(children.enumerated()), id: \.element.nodeId) { i, child in
                        NodeView(node: child, depth: depth + 1, section: section, index: i, parent: node.str("id"),
                                 locked: node.flag("locked"), look: look, model: model, space: space,
                                 outer: FolderPlace(parent: parent, index: index, depth: depth),
                                 last: i == children.count - 1)
                            .transition(.opacity.combined(with: .move(edge: .top)))
                    }
                }
            }
            .modifier(FolderClip())  // (its tabs slide out from under it as it opens)
        } else {
            TabRow(tab: node, depth: depth, look: look, model: model)
                .opacity(carried ? 0.35 : 1)
                .animation(.easeOut(duration: 0.15), value: carried)
                .id(node.str("id"))  // (scrolled to when it's the tab you switch to)
                .modifier(RowDrop(model: model, look: look, id: node.str("id"), space: space, section: section,
                                  index: index, parent: parent, depth: depth, locked: locked, folder: nil,
                                  outer: outer, last: last))
        }
    }
}

/// Where a folder is: the folder it's in (nil, at the top of its section), its place there, and how deep it is.
struct FolderPlace {
    let parent: String?
    let index: Int
    let depth: Int
}

/// Where a dragged tab would go, as Qt's sidebar decides: the top half of a row is before it, the bottom half after
/// (or first inside an open folder), and the middle third of a folder row is into it, at its end.
private enum DropMark: Equatable {
    case above
    case below(depth: Int)
    case into
}

private let dropGap: CGFloat = rowHeight  // (the room the rows make for a dragged tab)

/// The room the rows make for a dragged tab: where it'll go, shown faintly.
private struct DropRoom: View {
    let look: SidebarLook
    let depth: Int

    var body: some View {
        RoundedRectangle(cornerRadius: 9, style: .continuous)
            .fill(look.text.opacity(0.08))
            .frame(height: dropGap - 4)
            .padding(.vertical, 2)
            .padding(.leading, CGFloat(depth) * indent)
            .transition(.opacity)
    }
}

private struct RowDrop: ViewModifier {
    @ObservedObject var model: PageModel
    let look: SidebarLook
    let id: String
    let space: String
    let section: String
    let index: Int
    let parent: String?
    let depth: Int
    let locked: Bool
    let folder: JSON?
    var outer: FolderPlace? = nil  // (the folder it's in, where that is: the last row in it can drop after it)
    var last = false  // (the last row in its folder)
    @State private var into = false  // (a tab held over its middle: dropped into it, outlined)
    @State private var opening: Task<Void, Never>?  // (a closed folder held over a moment: it opens)

    func body(content: Content) -> some View {
        let into = $into
        content
            .overlay {
                if self.into {
                    RoundedRectangle(cornerRadius: 9, style: .continuous).stroke(look.text.opacity(0.7), lineWidth: 1.5)
                        .padding(1)
                }
            }
            .modifier(DropSpotPlace(key: "row \(space) \(id)", look: look, spot: DropSpot(
                section: section,
                over: { at in
                    // (the tab being carried, faded where it was, isn't somewhere to put it: before or after itself
                    // changes nothing)
                    guard model.string("dragging") != id, let target = target(at) else {
                        into.wrappedValue = false
                        return nil
                    }
                    into.wrappedValue = target.mark == .into
                    springOpen(target.mark == .into)
                    switch target.mark {
                    case .into: return .outline
                    case .above: return .room(top: true, depth: depth)
                    case .below(let d): return .room(top: false, depth: d)
                    }
                },
                left: {
                    into.wrappedValue = false
                    springOpen(false)
                },
                drop: { at, ids in
                    into.wrappedValue = false
                    springOpen(false)
                    let carried = model.string("dragging") == id
                    model.merge(["dragging": ""])
                    guard !carried, let target = target(at) else { return false }
                    var event: [String: Any] = ["id": ids, "space": space, "section": section, "index": target.index]
                    if let folder = target.folder { event["folder"] = folder }
                    model.act("move", event)
                    return true
                })))
    }

    /// A closed folder with a tab held over its middle opens after a moment (as Arc's do), to drop it among what's in
    /// it; anything else held over, or the tab moved on, and it doesn't.
    private func springOpen(_ held: Bool) {
        guard held, let folder, !folder.flag("expanded"), !folder.flag("locked") else {
            opening?.cancel()
            opening = nil
            return
        }
        guard opening == nil else { return }
        let id = folder.str("id")
        opening = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 600_000_000)
            if !Task.isCancelled { model.act("folder", ["id": id]) }
        }
    }

    /// The mark, folder and index for a drop at `at` in the row (from its top-left); nil where it can't go. A folder's
    /// middle half is into it (a third, it was too thin a band to find), its top and bottom quarters beside it; a Live
    /// Folder (its feed's) takes nothing, its bottom half after it even when it's open. The bottom of the last row in a
    /// folder, to the left of where its indent starts, is after that folder: past an open folder, as in Arc (there
    /// was otherwise nowhere that meant after it).
    func target(_ at: CGPoint) -> (mark: DropMark, folder: String?, index: Int)? {
        let y = at.y, live = folder?.flag("locked") ?? false
        if let folder, !live {
            let quarter = rowHeight / 4
            if y >= quarter && y <= rowHeight - quarter { return (.into, folder.str("id"), folder.int("size") ?? 0) }
        }
        if y >= rowHeight / 2, last, let outer, at.x < 10 + CGFloat(depth) * indent {
            return (.below(depth: outer.depth), outer.parent, outer.index + 1)
        }
        if y < rowHeight / 2 { return locked ? nil : (.above, parent, index) }
        if let folder, folder.flag("expanded"), !live {
            return (.below(depth: depth + 1), folder.str("id"), 0)
        }
        return locked ? nil : (.below(depth: depth), parent, index + 1)
    }
}

/// A place that isn't a row that a tab drops on (the empty Pinned hint, the divider, the end of Today): there, in
/// that section, with the line above it.
private struct DropAt: ViewModifier {
    @ObservedObject var model: PageModel
    let look: SidebarLook
    let space: String
    let place: String  // (which it is: its spot's own name, two of them can drop at the same place)
    let section: String
    let index: Int
    var below = false  // (the room opens under it: New Tab, which Today's tabs come after)

    func body(content: Content) -> some View {
        content
            .modifier(DropSpotPlace(key: "at \(space) \(place)", look: look, spot: DropSpot(
                section: section,
                over: { _ in .room(top: !below, depth: 0) },
                left: {},
                drop: { _, ids in
                    model.merge(["dragging": ""])
                    model.act("move", ["id": ids, "space": space, "section": section, "index": index])
                    return true
                })))
    }
}

/// What a spot shows for a tab held over it: room made for it (above the spot, or below), or an outline (into a folder).
enum DropShow: Equatable {
    case room(top: Bool, depth: Int)
    case outline
}

/// Where a dragged tab can be dropped, and what it does there: `over` with where in it the pointer is, from its
/// top-left (what it shows, or nil if it can't go there), `left` when it's gone elsewhere, and `drop` with the tabs'
/// ids (comma-separated).
struct DropSpot {
    let section: String  // ("pinned" or "today": which side of the divider it drops on)
    let over: (CGPoint) -> DropShow?
    let left: () -> Void
    let drop: (CGPoint, String) -> Bool
}

/// A sidebar's drop spots, by where they're laid out (in the sidebar view's own coordinates), for the view to hand a
/// dragged tab to the one it's over (Embedded.swift: PassThroughHostingView); SwiftUI's own drops (onDrop) never
/// reach a row in a view inside Qt's window. The rows make room where it'd go by sliding down on screen (RoomShift),
/// not by moving in the layout: places stay as they were laid out while a tab's carried, so what's under the pointer
/// never shifts with the room it opens (opened in the layout, the room had the spot under the pointer change and the
/// room jump about, and a drop in it land nowhere).
@MainActor
final class SidebarDrops: ObservableObject {
    struct Room: Equatable {
        let y: CGFloat  // (where the rows part: those laid out from here down slide down)
        let depth: Int
    }

    static let none = SidebarDrops()  // (outside a sidebar: nothing to drop on)
    private static var pages: [Int32: SidebarDrops] = [:]

    /// The drop spots of the sidebar that's page `page` (one a window: a closed window's rows mustn't take a drop).
    static func of(_ page: Int32) -> SidebarDrops {
        if let drops = pages[page] { return drops }
        let drops = SidebarDrops()
        pages[page] = drops
        return drops
    }

    static func forget(_ page: Int32) { pages[page] = nil }

    @Published private(set) var room: Room?
    var carrying: String?  // (the tabs being dragged from this sidebar, their ids comma-separated)
    var space = ""  // (the Space showing: only its spots take a drop)
    private var spots: [String: (rect: CGRect, spot: DropSpot)] = [:]
    private var current: String?  // (the spot the pointer's over, showing what a drop there does)
    private var roomFor: (key: String, at: CGPoint)?  // (the spot that made the room: a drop in it goes where it says)

    func place(_ key: String, _ rect: CGRect, _ spot: DropSpot) {
        // (while room's made, rows that slid keep where they were laid out)
        spots[key] = (room == nil ? rect : spots[key]?.rect ?? rect, spot)
    }

    func remove(_ key: String) {
        spots[key] = nil
        if current == key { current = nil }
        if roomFor?.key == key { roomFor = nil }
    }

    /// Whether the spot `key` slides down out of the way (laid out where the rows part, or below).
    func shifts(_ key: String) -> Bool {
        guard let room, let rect = spots[key]?.rect else { return false }
        return rect.minY >= room.y - 0.5
    }

    /// Whether the room opens just above the spot `key` (it shows the room, faintly).
    func opensAbove(_ key: String) -> Bool {
        guard let room, let rect = spots[key]?.rect else { return false }
        return abs(rect.minY - room.y) < 0.5
    }

    /// Whether the spot `key` ("row <space> <id>", "at <space> <place>") is the showing Space's. The other Spaces'
    /// pages, scrolled away, keep the places they had when they last showed (just where this one's are): a drop
    /// could be taken for one of theirs, and Python, rightly, wouldn't move a tab into a Space that isn't showing.
    private func shows(_ key: String) -> Bool {
        space.isEmpty || key.split(separator: " ").dropFirst().first.map(String.init) == space
    }

    /// The tests': a copy of every spot of the Space showing, as another Space's ("ghost"), a little smaller (so it
    /// would be the one a drop's taken for, were it looked at), dropping as `drop` says.
    func haunt(_ drop: (String) -> (CGPoint, String) -> Bool) {
        for (key, held) in spots where shows(key) {
            let ghost = key.split(separator: " ").enumerated().map { $0.offset == 1 ? "ghost" : String($0.element) }
            spots[ghost.joined(separator: " ")] = (held.rect.insetBy(dx: 1, dy: 1),
                                                   DropSpot(section: held.spot.section, over: held.spot.over, left: {},
                                                            drop: drop(held.spot.section)))
        }
    }

    /// Every spot's place, for the tests ("drop_spots").
    var places: [String: CGRect] { spots.mapValues(\.rect) }

    /// The spot at `point` (the smallest, should two overlap), among those in `bounds` (the Space showing); below
    /// them all, the lowest (the end of Today: a tab dragged out of Pinned into the empty sidebar under it goes there,
    /// as in Arc, where it had nowhere to go).
    private func spot(at point: CGPoint, in bounds: CGRect) -> (key: String, rect: CGRect, spot: DropSpot)? {
        let showing = spots.filter { shows($0.key) && bounds.intersects($0.value.rect) }
        let found = showing.filter { $0.value.rect.contains(point) }
            .min { $0.value.rect.width * $0.value.rect.height < $1.value.rect.width * $1.value.rect.height }
            ?? showing.max { $0.value.rect.maxY < $1.value.rect.maxY }.flatMap { $0.value.rect.maxY <= point.y ? $0 : nil }
        return found.map { ($0.key, $0.value.rect, $0.value.spot) }
    }

    /// Where `point` (on screen) is in the layout: below the room, the rows are drawn a room lower than they're laid
    /// out; in the room's top half, nil (it's where the tab will go already); in its bottom half, the top of what
    /// comes after it. Judged by the layout alone, the row drawn just below the room was taken for the one after it:
    /// the room jumped a row further down than the pointer, and back, flickering. Between two rows both halves are
    /// the same place; where Pinned ends, the top half is the end of Pinned and the bottom half Today (a tab dragged
    /// out of Pinned quickly and let go past the room just opened after the last pinned tab went back into Pinned).
    private func laidOut(_ point: CGPoint) -> CGPoint? {
        guard let room, point.y >= room.y else { return point }
        if point.y < room.y + dropGap / 2 { return nil }
        return CGPoint(x: point.x, y: point.y < room.y + dropGap ? room.y + 1 : point.y - dropGap)
    }

    /// What a drop at `point` (on screen) is on: the spot, and where in it. Which side of the divider is decided by
    /// where the divider's laid out, which never moves while a tab's carried; where on that side, by what's drawn
    /// (the room taken into account: laidOut). In a quick drag the rows are always still sliding to a room just
    /// opened, drawn a room lower than they'll look a moment later: let go just past the last pinned tab, a tab was
    /// judged against them and went back into Pinned.
    private func target(_ point: CGPoint, in bounds: CGRect) -> (key: String, rect: CGRect, spot: DropSpot, at: CGPoint)? {
        var drawn: (key: String, rect: CGRect, spot: DropSpot, at: CGPoint)?
        if let laid = laidOut(point) {
            drawn = spot(at: laid, in: bounds).map { ($0.key, $0.rect, $0.spot, CGPoint(x: laid.x - $0.rect.minX, y: laid.y - $0.rect.minY)) }
        } else if let roomFor, let held = spots[roomFor.key] {  // (in the room: where it said)
            drawn = (roomFor.key, held.rect, held.spot, roomFor.at)
        }
        guard let divider = spots.first(where: { $0.key.hasSuffix(" new tab") && shows($0.key) })?
            .value.rect.minY else { return drawn }
        let side = point.y >= divider ? "today" : "pinned"
        if let drawn, drawn.spot.section == side { return drawn }
        let under = spot(at: point, in: bounds)  // (the other side: what's laid out under the pointer, on this one)
        guard let under, under.spot.section == side else { return drawn }
        return (under.key, under.rect, under.spot, CGPoint(x: point.x - under.rect.minX, y: point.y - under.rect.minY))
    }

    /// A tab dragged to `point`: whether it can be dropped there (room's made for it, or its folder outlined). A
    /// folder outlined to take it leaves any room as it is: closed, the rows moved back up and the folder slid out
    /// from under the pointer, the pointer was beside it again, the room opened again, and so on (it flickered).
    func over(_ point: CGPoint, in bounds: CGRect) -> Bool {
        let found = target(point, in: bounds)
        if found?.key != current {
            if let current { spots[current]?.spot.left() }
            current = found?.key
        }
        guard let found, let show = found.spot.over(found.at) else {
            closeRoom()
            return false
        }
        if case .room(let top, let depth) = show {
            let made = Room(y: top ? found.rect.minY : found.rect.maxY, depth: depth)
            if room != made { room = made }
            roomFor = (found.key, found.at)
        }
        return true
    }

    private func closeRoom() {
        if room != nil { room = nil }
        roomFor = nil
    }

    func left() {
        if let current { spots[current]?.spot.left() }
        current = nil
        closeRoom()
    }

    /// The drag's over, however it ended.
    func ended() {
        left()
        carrying = nil
    }


    /// Tabs (`ids`, comma-separated) dropped at `point`: moved there, if they can go.
    func drop(_ point: CGPoint, in bounds: CGRect, ids: String) -> Bool {
        defer { left() }
        guard let found = target(point, in: bounds) else { return false }
        return found.spot.drop(found.at, ids)
    }

    /// The tab ids a drag carries (several picked, or one), from Eclipse's own or Qt's sidebar.
    static func ids(on pasteboard: NSPasteboard) -> String? {
        for type in [TabDrag.qtManyType, TabDrag.qtType] {
            if let data = pasteboard.data(forType: NSPasteboard.PasteboardType(type)),
               let ids = String(data: data, encoding: .utf8), !ids.isEmpty {
                return ids
            }
        }
        return nil
    }
}

/// Puts a drop spot in its sidebar's SidebarDrops, where it's laid out in the sidebar view (kept up to date as it
/// moves), and slides it down out of the way when room's made above it for a dragged tab.
private struct DropSpotPlace: ViewModifier {
    let key: String
    let look: SidebarLook
    let spot: DropSpot
    @Environment(\.sidebarDrops) private var drops

    func body(content: Content) -> some View {
        content
            .background(GeometryReader { geometry in
                let _ = drops?.place(key, geometry.frame(in: .named("sidebarBody")), spot)
                Color.clear
            })
            .modifier(RoomShift(drops: drops ?? SidebarDrops.none, key: key, look: look))
            .onDisappear { drops?.remove(key) }
    }
}

/// A folder's tabs, clipped to it (they slide out from under it as it opens), but not while room's made for a dragged
/// tab: its rows that slid down out of the way would go out of sight.
private struct FolderClip: ViewModifier {
    @Environment(\.sidebarDrops) private var drops

    func body(content: Content) -> some View {
        Clipped(drops: drops ?? SidebarDrops.none) { content }
    }

    private struct Clipped<Inner: View>: View {
        @ObservedObject var drops: SidebarDrops
        @ViewBuilder let inner: () -> Inner

        var body: some View {
            inner().clipShape(Bounds(open: drops.room != nil))  // (one view either way: its rows stay where they are)
        }
    }

    private struct Bounds: Shape {
        let open: Bool

        func path(in rect: CGRect) -> Path { Path(open ? rect.insetBy(dx: -2000, dy: -2000) : rect) }
    }
}

/// A spot below where the rows part for a dragged tab: slid down a row's room (the one just below shows the room).
private struct RoomShift: ViewModifier {
    @ObservedObject var drops: SidebarDrops
    let key: String
    let look: SidebarLook

    func body(content: Content) -> some View {
        let shifted = drops.shifts(key)
        content
            .background(alignment: .top) {
                if drops.opensAbove(key), let room = drops.room {
                    DropRoom(look: look, depth: room.depth).offset(y: -dropGap)
                }
            }
            .offset(y: shifted ? dropGap : 0)
            .animation(.snappy(duration: 0.18), value: shifted)
    }
}

private struct SidebarDropsKey: EnvironmentKey {
    static let defaultValue: SidebarDrops? = nil
}

extension EnvironmentValues {
    /// The sidebar's drop spots, for its rows to put themselves in.
    var sidebarDrops: SidebarDrops? {
        get { self[SidebarDropsKey.self] }
        set { self[SidebarDropsKey.self] = newValue }
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
        guard now != hovering, !(now && (scrolling || !model.string("dragging").isEmpty)) else { return }
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
            .onChange(of: model.string("dragging").isEmpty) { idle in if !idle { hovered(false) } }
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
        .onChange(of: model.string("dragging").isEmpty) { idle in if !idle { hovered(false) } }
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
                if folder.flag("live") {  // (a Live Folder: fills itself from its feed)
                    Image(systemName: "dot.radiowaves.left.and.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(look.text.opacity(0.5))
                        .help(folder.str("live_tip"))
                }
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
        .onHover { hovering = $0 && !scrolling && model.string("dragging").isEmpty }
        .onChange(of: scrolling) { now in if now { hovering = false } }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .onTapGesture { model.act("folder", ["id": folder.str("id")]) }
        .overlay(RightClick { model.act("menu", ["id": folder.str("id"), "kind": "folder"]) } middle: {})
        .modifier(DraggableUnless(model: model, fixed: folder.flag("fixed"), id: folder.str("id"), title: folder.str("name")))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(folder.str("name")), \(folder.flag("live") ? "Live Folder" : "folder"), \(folder.flag("expanded") ? "open" : "closed")")
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
        .onHover { hovering = $0 && !scrolling && model.string("dragging").isEmpty }
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
        model.merge(["dragging": id])  // (its row fades while it's carried: Python clears it when the button's up)
        let marked = model.state["marked"] as? [String] ?? []
        // (what's carried, known here too: a quick drop could come before the drag's own data is there to read)
        SidebarDrops.of(model.id).carrying = marked.count > 1 && marked.contains(id) ? marked.joined(separator: ",") : id
        let provider = NSItemProvider()
        provider.registerDataRepresentation(forTypeIdentifier: qtType, visibility: .ownProcess) { done in
            done(Data(id.utf8), nil)
            return nil
        }
        if marked.count > 1 && marked.contains(id) {  // (one of several picked: they all go)
            provider.registerDataRepresentation(forTypeIdentifier: qtManyType, visibility: .ownProcess) { done in
                done(Data(marked.joined(separator: ",").utf8), nil)
                return nil
            }
        }
        return provider
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
