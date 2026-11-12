// The theme editor's colors as dots on a field of color: hue from left to right, and from soft (pale) at the top
// through vivid in the middle to deep at the bottom. Drag a dot to change its color; double-click the field to add
// one (up to "max"), double-click a dot (or right-click it) to take it out. Without dragging: a dot takes the
// keyboard (Tab to it; the arrows move it, ← → through the hues and ↑ ↓ softer or deeper; Return chooses its color in
// macOS's color panel; Delete takes it out), VoiceOver adjusts it (its hue) and has the same actions, and right-click
// has Choose Color…. The strip below shows them blended, as
// the window will be. The value is the colors as hex, the first the Space's own (a "gradient" field in Form.swift).

import AppKit
import SwiftUI

/// Where on the field a color is, and back: x is hue, y runs soft → vivid → deep.
enum ColorField {
    static func color(x: Double, y: Double) -> (h: Double, s: Double, b: Double) {
        let h = min(max(x, 0), 1), t = min(max(y, 0), 1)
        if t < 0.5 {
            let u = t / 0.5
            return (h, 0.18 + 0.67 * u, 1.0 - 0.05 * u)
        }
        let u = (t - 0.5) / 0.5
        return (h, 0.85 + 0.1 * u, 0.95 - 0.6 * u)
    }

    static func hex(x: Double, y: Double) -> String {
        let c = color(x: x, y: y)
        return Color(nsColor: NSColor(hue: c.h, saturation: c.s, brightness: c.b, alpha: 1)).hexString
    }

    /// The point whose color is nearest `hex` (its hue, and the softness and depth closest to it).
    static func place(_ hex: String) -> CGPoint {
        guard let c = NSColor(Color(hex: hex) ?? .gray).usingColorSpace(.sRGB) else { return CGPoint(x: 0.5, y: 0.5) }
        var h: CGFloat = 0, s: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
        c.getHue(&h, saturation: &s, brightness: &b, alpha: &a)
        var best = 0.5, nearest = Double.infinity
        for step in 0...100 {
            let y = Double(step) / 100, here = color(x: 0, y: y)
            let distance = pow(here.s - Double(s), 2) + pow(here.b - Double(b), 2)
            if distance < nearest { nearest = distance; best = y }
        }
        return CGPoint(x: Double(h), y: best)
    }
}

/// The field itself, drawn once (it never changes, so dragging a dot doesn't redraw it).
private struct ColorFieldPicture: View {
    var body: some View {
        Canvas { context, size in
            let columns = Int(size.width / 2), rows = Int(size.height / 2)  // (a cell every 2 points: smooth)
            let w = size.width / CGFloat(columns), h = size.height / CGFloat(rows)
            for column in 0..<columns {
                for row in 0..<rows {
                    let c = ColorField.color(x: (Double(column) + 0.5) / Double(columns), y: (Double(row) + 0.5) / Double(rows))
                    let rect = CGRect(x: CGFloat(column) * w, y: CGFloat(row) * h, width: w + 0.5, height: h + 0.5)
                    context.fill(Path(rect), with: .color(Color(hue: c.h, saturation: c.s, brightness: c.b)))
                }
            }
        }
        .drawingGroup()
    }
}

struct GradientField: View {
    let id: String
    let max: Int
    @ObservedObject var values: FormValues
    @State private var dragging: (index: Int, at: CGPoint)?

    private var colors: [String] { values.list[id] ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            GeometryReader { geometry in
                let size = geometry.size
                ZStack(alignment: .topLeading) {
                    ColorFieldPicture()
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Color.primary.opacity(0.12)))
                        .contentShape(Rectangle())
                        .gesture(SpatialTapGesture(count: 2).onEnded { tap in add(at: tap.location, in: size) })
                    if colors.count > 1 {  // (a line through them, in order: the way the blend runs)
                        Path { path in
                            for (index, hex) in colors.enumerated() {
                                let point = spot(index, hex, in: size)
                                index == 0 ? path.move(to: point) : path.addLine(to: point)
                            }
                        }
                        .stroke(Color.white.opacity(0.7), style: StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                        .allowsHitTesting(false)
                    }
                    ForEach(Array(colors.enumerated()), id: \.offset) { index, hex in
                        dot(index, hex, in: size)
                    }
                }
            }
            .frame(height: 170)
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(LinearGradient(colors: colors.compactMap { Color(hex: $0) }.paddedToTwo,
                                         startPoint: .leading, endPoint: .trailing))
                    .frame(height: 14)
                    .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).stroke(Color.primary.opacity(0.12)))
                if colors.count < max {
                    Button { values.list[id] = colors + [FieldView.nextColor(colors.last ?? "#8b7cf6")] } label: {
                        Image(systemName: "plus")
                    }
                    .help("Add a color")
                }
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Colors")
        .onDisappear { ColorChooser.shared.stop() }  // (the form's gone: the panel changes nothing now)
    }

    private func spot(_ index: Int, _ hex: String, in size: CGSize) -> CGPoint {
        if let dragging, dragging.index == index { return dragging.at }
        let place = ColorField.place(hex)
        return CGPoint(x: place.x * size.width, y: place.y * size.height)
    }

    private func dot(_ index: Int, _ hex: String, in size: CGSize) -> some View {
        let point = spot(index, hex, in: size)
        let diameter: CGFloat = index == 0 ? 26 : 20  // (the first is the Space's own color)
        return Circle()
            .fill(Color(hex: hex) ?? .gray)
            .frame(width: diameter, height: diameter)
            .overlay(Circle().stroke(Color.white, lineWidth: 3))
            .shadow(color: .black.opacity(0.35), radius: 3, y: 1)
            .scaleEffect(dragging?.index == index ? 1.15 : 1)
            .position(point)
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { drag in
                        // Only once it's moved: a click (or a double-click's first) changed the color to the field's
                        // at that spot, and a color the field doesn't have, a gray, say, snapped to a pale tint
                        guard dragging?.index == index || hypot(drag.translation.width, drag.translation.height) >= 3
                        else { return }
                        let at = CGPoint(x: Swift.min(Swift.max(drag.location.x, 0), size.width),
                                         y: Swift.min(Swift.max(drag.location.y, 0), size.height))
                        dragging = (index, at)
                        set(index, ColorField.hex(x: at.x / size.width, y: at.y / size.height))
                    }
                    .onEnded { _ in withAnimation(.snappy(duration: 0.15)) { dragging = nil } }
            )
            .simultaneousGesture(TapGesture(count: 2).onEnded { remove(index) })
            .contextMenu {
                Button("Choose Color…") { choose(index) }
                if colors.count > 1 { Button("Remove Color") { remove(index) } }
            }
            .focusable()
            .modifier(DotKeys(move: { dx, dy in nudge(index, dx: dx, dy: dy) }, choose: { choose(index) },
                              remove: { remove(index) }))
            .help(index == 0 ? "The Space's color: drag to change it" : "Drag to change; double-click to take it out")
            .accessibilityElement()
            .accessibilityLabel(index == 0 ? "Main color" : "Color \(index + 1)")
            .accessibilityValue(hex)
            .accessibilityAdjustableAction { direction in
                nudge(index, dx: direction == .increment ? 1 : -1, dy: 0)
            }
            .accessibilityAction(named: "Choose Color") { choose(index) }
            .accessibilityAction(named: "Remove Color") { remove(index) }
            .animation(.snappy(duration: 0.12), value: dragging?.index == index)
    }

    /// A dot moved a step on the field without dragging it: `dx` through the hues (round the end), `dy` softer or
    /// deeper.
    private func nudge(_ index: Int, dx: Int, dy: Int) {
        guard colors.indices.contains(index) else { return }
        let place = ColorField.place(colors[index])
        var x = place.x + Double(dx) * 0.02
        x -= x.rounded(.down)  // (past red at either end: round to the other)
        let y = Swift.min(Swift.max(place.y + Double(dy) * 0.05, 0), 1)
        set(index, ColorField.hex(x: x, y: y))
    }

    /// macOS's color panel, for the dot's color: any color, typed in (hex, RGB) or picked from the screen.
    private func choose(_ index: Int) {
        guard colors.indices.contains(index) else { return }
        ColorChooser.shared.choose(colors[index]) { hex in set(index, hex) }
    }

    private func set(_ index: Int, _ hex: String) {
        var now = colors
        guard now.indices.contains(index), now[index] != hex else { return }
        now[index] = hex
        values.list[id] = now
    }

    private func add(at point: CGPoint, in size: CGSize) {
        guard colors.count < max, size.width > 0, size.height > 0 else { return }
        withAnimation(.snappy(duration: 0.18)) {
            values.list[id] = colors + [ColorField.hex(x: point.x / size.width, y: point.y / size.height)]
        }
    }

    private func remove(_ index: Int) {
        guard colors.count > 1, colors.indices.contains(index) else { return }
        ColorChooser.shared.stop()  // (the color panel was for a dot by its place: the one moving into it isn't it)
        withAnimation(.snappy(duration: 0.18)) { _ = values.list[id]?.remove(at: index) }
    }
}

/// A dot's keys (macOS 14 and later; before, right-click and VoiceOver have the same).
private struct DotKeys: ViewModifier {
    let move: (Int, Int) -> Void
    let choose: () -> Void
    let remove: () -> Void

    func body(content: Content) -> some View {
        if #available(macOS 14.0, *) {
            content
                .onKeyPress(.leftArrow) { move(-1, 0); return .handled }
                .onKeyPress(.rightArrow) { move(1, 0); return .handled }
                .onKeyPress(.upArrow) { move(0, -1); return .handled }
                .onKeyPress(.downArrow) { move(0, 1); return .handled }
                .onKeyPress(.return) { choose(); return .handled }
                .onKeyPress(.delete) { remove(); return .handled }
                .onKeyPress(.deleteForward) { remove(); return .handled }
        } else {
            content
        }
    }
}

/// macOS's shared color panel, telling one dot what's picked in it.
final class ColorChooser: NSObject {
    static let shared = ColorChooser()
    private var picked: ((String) -> Void)?
    private var picking = false  // (the panel's ours: it was shown for a dot)

    func choose(_ hex: String, picked: @escaping (String) -> Void) {
        self.picked = nil  // (setting its color below would tell the last dot)
        let panel = NSColorPanel.shared
        panel.setTarget(self)
        panel.setAction(#selector(changed(_:)))
        panel.isContinuous = true
        panel.showsAlpha = false
        panel.color = NSColor(Color(hex: hex) ?? .gray)
        self.picked = picked
        picking = true
        panel.makeKeyAndOrderFront(nil)
    }

    func stop() {
        picked = nil
        if picking { NSColorPanel.shared.setTarget(nil) }
        picking = false
    }

    @objc private func changed(_ panel: NSColorPanel) {
        picked?(Color(nsColor: panel.color).hexString)
    }
}

private extension Array where Element == Color {
    /// A gradient needs two: one color is drawn as itself.
    var paddedToTwo: [Color] { count == 1 ? [self[0], self[0]] : isEmpty ? [.gray, .gray] : self }
}
