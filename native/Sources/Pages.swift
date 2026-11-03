// The native pages themselves. Each reads its PageModel's state and calls act() for what Python does.

import SwiftUI

/// Sections of notes: a heading, then its lines (bulleted, mostly), each inline Markdown.
struct NotesView: View {
    let sections: [NoteSection]
    var headingFont: Font = .title2

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            ForEach(sections) { section in
                VStack(alignment: .leading, spacing: 10) {
                    if !section.heading.isEmpty {
                        Text(section.heading).font(headingFont.weight(.semibold))
                    }
                    ForEach(section.lines) { line in
                        HStack(alignment: .firstTextBaseline, spacing: 9) {
                            if line.bullet {
                                Circle().fill(.tint).frame(width: 5, height: 5).alignmentGuide(.firstTextBaseline) {
                                    $0[VerticalAlignment.center] + 4
                                }
                            }
                            Text(markdown: line.text)
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// What's New: this week's notes and the ones before, scrolling, and Close.
struct WhatsNewView: View {
    @ObservedObject var model: PageModel

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(model.string("heading")).font(.largeTitle.weight(.bold))
                    NotesView(sections: model.sections())
                }
                .padding(.horizontal, 32)
                .padding(.top, 38)
                .padding(.bottom, 20)
            }
            Divider()
            HStack {
                Spacer()
                Button("Close") { model.act("close") }
                    .keyboardShortcut(.defaultAction)
                    .controlSize(.large)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 14)
        }
        .background(.background)
    }
}

/// The update window: the new version, what's new in it, and Update & Restart with its download's progress.
struct UpdateView: View {
    @ObservedObject var model: PageModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                Text(model.string("heading")).font(.title.weight(.bold))
                Text(model.string("about")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 28)
            .padding(.top, 34)
            .padding(.bottom, 14)
            Divider().padding(.horizontal, 28)
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if model.sections().first?.heading.isEmpty ?? true {  // (a release's notes bring their own)
                        Text("What's new").font(.headline)
                    }
                    NotesView(sections: model.sections(), headingFont: .headline)
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 14)
            }
            VStack(alignment: .leading, spacing: 12) {
                if let percent = model.double("progress") {
                    ProgressView(value: percent, total: 100)
                }
                if !model.string("status").isEmpty {
                    Text(model.string("status")).font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                HStack(spacing: 10) {
                    if model.bool("can_skip") {
                        Button("Skip This Version") { model.act("skip") }
                            .disabled(model.bool("busy"))
                    }
                    Spacer()
                    Button("Later") { model.act("later") }
                        .keyboardShortcut(.cancelAction)
                    Button(model.string("go_label", "Update & Restart")) { model.act("update") }
                        .keyboardShortcut(.defaultAction)
                        .buttonStyle(.borderedProminent)
                        .disabled(model.bool("busy"))
                }
                .controlSize(.large)
            }
            .padding(.horizontal, 28)
            .padding(.top, 6)
            .padding(.bottom, 22)
        }
        .background(.background)
    }
}
