# Claude (Arc Max's features, through the Claude Code CLI on this Mac): Ask Claude in ⌘T, Ask on Page (⌘F, a
# question), asking about or translating selected text, Translate Page, Tidy Tab Titles (when you pin),
# Tidy Today's Tabs, link previews (hover a link, tap ⇧), Browse for Me (⌘↩), readable names for downloads,
# and the Claude page in Settings. Who answers can also be the local model your homebase serves (Settings →
# My Apps), when the integrations feature is here.

import html

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QCheckBox, QLabel

from .ai import Claude, looks_like_question, page_block
from .browse import BrowseForMe, PageReader, answer_path, js_set, new_run_id, render_page
from .features import Feature
from .models import TODAY, Folder, Tab, display_host, walk_tabs
from .page_index import ask_prompt, cite, question_words
from .shortcuts import for_here
from .tidy import MIN_TABS as TIDY_MIN_TABS, build_prompt as build_tidy_prompt, parse_groups as parse_tidy_groups
from .tidy_downloads import tidy as tidy_download
from .translate import Translator, target_language

CLAUDE_MODELS = [("haiku", "Haiku (fastest)"), ("sonnet", "Sonnet (smarter, slower)")]


class ClaudeFeature(Feature):
    name = "claude"

    def __init__(self, window):
        super().__init__(window)
        settings = window.store.settings
        self.claude = Claude(window, settings.get("claude_model", "haiku"))
        self.claude.enabled = settings.get("claude_enabled", True)
        self.translator = Translator(window)  # Translate Page
        self._preview_page = None   # a link being read for its preview
        self._preview_request = None
        self._preview_cache: dict[str, tuple[str, str]] = {}
        self._tidy_undo = None      # (space, Today's top-level nodes before the last Tidy)
        self._browse_runs: set = set()
        self.claude_action = None

    def ready(self):
        QTimer.singleShot(2000, self.claude.warm_up)

    def shutdown(self):
        self.claude.shutdown()
        if self._preview_page is not None:
            self._preview_page.cancel()
            page = self._preview_page.page
            from PyQt6 import sip
            if page is not None and not sip.isdeleted(page):
                sip.delete(page)

    # --- Who answers ---------------------------------------------------------------------------------------

    def available(self) -> bool:
        return self.claude.available

    def assistant(self, other=False):
        """Who answers questions: Claude, or the local model on homebase (Settings → My Apps).
        other: the one that isn't chosen, for the second row in ⌘T. None if it can't answer."""
        apps = self.window.feature("integrations")
        local = apps is not None and apps.ai_choice() == "local"
        if other:
            local = not local
        if local:
            return apps.local_ai if apps is not None and apps.local_ai.available else None
        return self.claude if self.claude.available else None

    def assistant_name(self, ai) -> str:
        apps = self.window.feature("integrations")
        return f"{ai.name} (local)" if apps is not None and ai is apps.local_ai else "Claude"

    def is_question(self, text) -> bool:
        return self.claude.available and looks_like_question(text)

    def set_model(self, model):
        window = self.window
        window.store.settings["claude_model"] = model
        window.store.schedule_save()
        self.claude.shutdown()  # the warm process was started with the old model
        self.claude.model = model
        self.claude.warm_up()
        window.toast(f"Claude features now use {model.capitalize()}")

    def toggle(self):
        window, claude = self.window, self.claude
        if claude.exe is None:
            window.toast("Claude Code CLI not found — install it, then run `claude` once to log in")
            return
        claude.enabled = not claude.enabled
        window.store.settings["claude_enabled"] = claude.enabled
        window.store.schedule_save()
        if self.claude_action is not None:
            self.claude_action.setChecked(claude.enabled)
        if claude.enabled:
            claude.warm_up()
        else:
            claude.shutdown()
        window.toast("Claude features on" if claude.enabled else "Claude features off")

    # --- Ask on Page, and selected text ---------------------------------------------------------------------

    def ask_page(self, question, on_request):
        """Arc's "Ask on Page": read the current page's text, then ask about it."""
        window = self.window
        tab = window.current_tab
        if window._current_view() is None:
            return
        ai = self.assistant() or self.claude

        def got_text(text):
            if tab.view is None:
                return  # (the tab closed while its text was being read)
            on_request(ai.ask(
                page_block(tab.label, tab.url, text) +
                "\n\nUsing the page above, answer the question below in at most 150 words, using Markdown "
                "where it helps. If the page doesn't cover it, say so briefly, then answer from general "
                "knowledge if you can.\n\nQuestion: " + question))
        tab.view.page().toPlainText(got_text)

    def selection_menu(self, menu, view, selected):
        """Right-clicking selected text: ask about it, translate it (at the top of the menu)."""
        window = self.window
        short = selected if len(selected) <= 28 else selected[:26].rstrip() + "…"
        title = view.title() or display_host(view.url().toString())
        page = page_block(title, view.url().toString(), selected[:6000])
        explain = (page + "\n\nExplain the passage above (from that page) plainly and briefly, in under 150 "
                   "words: what it means, and any term in it a reader might not know. Use Markdown where it helps.")
        language = target_language(window.store.settings)
        translate = (page + f"\n\nTranslate the passage above into {language}. Answer with only the "
                     "translation (if it's already in that language, say so in one line).")
        added = []
        for ai in (self.assistant(), self.assistant(other=True)):
            if ai is not None:
                action = QAction(f"Ask {self.assistant_name(ai)} About “{short}”", menu)
                action.triggered.connect(lambda _=False, a=ai: window.command_bar.ask_about(selected, explain, a))
                added.append(action)
        if self.assistant() is not None:
            action = QAction(f"Translate “{short}”", menu)
            action.triggered.connect(lambda: window.command_bar.ask_about(selected, translate))
            added.append(action)
        return added

    def toggle_translate(self):
        window = self.window
        if window.reader.is_open_for(window.current_tab):
            self.translator.toggle(window.reader.view, window.current_tab, reader=True)
        else:
            self.translator.toggle(window._current_view(), window.current_tab)

    # --- ⌘T: Ask, and Browse for Me -------------------------------------------------------------------------

    def query_rows(self, bar, query, results, url):
        """Ask rows for a question (first if it looks like one), and Browse for Me under them."""
        import qtawesome as qta
        from .command_bar import Result
        theme = self.window.theme
        ai = self.assistant()
        if ai is None or url or len(query.split()) < 2:
            return
        name = self.assistant_name(ai)
        ask = Result(query, f"Ask {name}", name.split(" ")[0].capitalize(),
                     qta.icon("ph.sparkle", color=theme.accent_text), lambda a=ai: bar.ask(query, a), keep_open=True)
        # A question goes straight to Claude on Return; anything else still searches first
        results.insert(0 if looks_like_question(query) else 1, ask)
        other = self.assistant(other=True)
        if other is not None:  # the other one, a row below, to compare or when one is busy
            other_name = self.assistant_name(other)
            local = getattr(self.window.feature("integrations"), "local_ai", None)
            results.insert(results.index(ask) + 1, Result(
                query, f"Ask {other_name}", other_name.split(" ")[0].capitalize(),
                qta.icon("ph.cpu" if other is local else "ph.sparkle", color=theme.accent_text),
                lambda a=other: bar.ask(query, a), keep_open=True))
        browse = Result(query, "Browse for Me · reads the top results", "⌘↩",
                        qta.icon("ph.compass", color=theme.accent_text), lambda: self.browse_for_me(query))
        results.insert(results.index(ask) + 1, browse)
        index = getattr(self.window, "page_index", None)
        if index is not None and index.enabled and question_words(query):
            results.insert(results.index(browse) + 1, Result(
                query, f"Ask {name} About Pages You've Read", "Pages",
                qta.icon("ph.books", color=theme.accent_text), lambda a=ai: self.ask_pages(bar, query, a),
                keep_open=True))

    def ask_pages(self, bar, question, ai):
        """An answer from pages you've read (page_index): the best few, with Claude's citations linked to them."""
        found = self.window.page_index.passages(question)
        if not found:
            bar._cancel_answer()
            bar._show_answer("Nothing you've read matches that. (Pages are searchable a few seconds after they "
                             "load; private windows, sign-in pages and excluded sites aren't kept.)")
            return
        bar.ask(question, ai, prompt=ask_prompt(question, found), shape=lambda text: cite(text, found))

    def browse_for_me(self, query):
        """Arc Max's "Browse for Me": read the top search results and open one answer page."""
        window = self.window
        query = query.strip()
        if not query:
            return
        if not self.claude.available:
            window.toast("Browse for Me needs Claude features turned on (View menu)")
            return
        path = answer_path(new_run_id())
        state = {"sources": [], "status": "Searching…", "body": ""}

        def write():
            path.write_text(render_page(query, state["sources"], state["body"], state["status"],
                                        window.theme.accent.name()))
        write()
        tab = Tab(QUrl.fromLocalFile(str(path)).toString(), "✦ " + query)
        window.store.add_tab(tab, TODAY, window.store.active_space, 0)
        window.activate_tab(tab)

        def update(element, content):
            view = tab.view
            if isinstance(view, QWebEngineView) and view.url().toLocalFile() == str(path):
                view.page().runJavaScript(js_set(element, content))

        def set_status(text):
            state["status"] = text
            update("status", html.escape(text))

        def got_sources(sources):
            state["sources"] = sources
            write()
            if isinstance(tab.view, QWebEngineView):
                tab.view.reload()  # show the source cards

        def finish(body, status=""):
            state["body"], state["status"] = body, status
            write()  # the saved page has the final answer, for reloads and restarts
            update("answer", body)
            update("status", html.escape(status))
            self._browse_runs.discard(run)

        run = BrowseForMe(self.claude, window.profile_for_tab(tab), query, window)
        self._browse_runs.add(run)
        run.status.connect(set_status)
        run.sources_found.connect(got_sources)
        run.partial.connect(lambda body: (state.__setitem__("body", body), update("answer", body)))
        run.finished.connect(finish)
        run.failed.connect(lambda message: finish(state["body"], f"Couldn't finish: {message}"))

        def tab_gone():
            # Its tab closed (or was archived): stop reading pages and asking Claude for nobody
            if window.store.locate(tab) is None:
                window.store.structure_changed.disconnect(tab_gone)
                if run in self._browse_runs:
                    self._browse_runs.discard(run)
                    run.cancel()
        window.store.structure_changed.connect(tab_gone)
        run.finished.connect(lambda *_: window._stop_watching(tab_gone))
        run.failed.connect(lambda *_: window._stop_watching(tab_gone))
        run.start()

    # --- Tidy Tab Titles, and Tidy Today's Tabs -------------------------------------------------------------

    def tab_pinned(self, tab):
        self.tidy_title(tab)

    def tidy_title(self, tab, force=False):
        """Arc's "Tidy Tab Titles": give a pinned tab a short, human name."""
        if not tab or not tab.url or not self.claude.available or (tab.custom_title and not force):
            return
        request = self.claude.ask(
            page_block(tab.title or "(none)", tab.url, "") +
            "\n\nRename this browser tab to a short, clean label of 1-4 words, the way a person would "
            "name a bookmark (for example 'GitHub Issues' or 'Q3 Budget'). Reply with the label only.")
        request.finished.connect(lambda name: self._apply_title(tab, name, force))
        if force:
            request.failed.connect(self.window.toast)

    def _apply_title(self, tab, name, force):
        store = self.window.store
        lines = name.strip().splitlines()
        name = lines[0].strip(" \"'“”‘’`*.") if lines else ""
        loc = store.locate(tab)
        if not name or len(name) > 40 or not loc or (tab.custom_title and not force):
            return
        if loc[0] == TODAY and not force:
            return  # it was unpinned while Claude was thinking
        tab.custom_title = name
        tab.changed.emit()
        store.schedule_save()

    def can_tidy(self, loose: int) -> bool:
        """Whether Tidy is on offer for this many loose Today tabs."""
        return self.claude.available and loose >= TIDY_MIN_TABS

    def tidy_today(self):
        """Ask Claude to group the loose Today tabs into a few topic folders."""
        window = self.window
        space = window.store.active_space
        loose = [n for n in space.today if isinstance(n, Tab)]
        if len(loose) < TIDY_MIN_TABS:
            window.toast(f"Tidy needs at least {TIDY_MIN_TABS} loose tabs in Today")
            return
        if not self.claude.available:
            window.toast("Tidy Tabs needs Claude features turned on (Settings → Claude)")
            return
        window.sidebar.set_tidy_state("busy")
        request = self.claude.ask(build_tidy_prompt(loose))
        request.finished.connect(lambda reply: self._apply_groups(space, loose, reply))
        request.failed.connect(lambda message: (window.sidebar.set_tidy_state("idle"),
                                                window.toast(f"Couldn't tidy: {message}")))

    def _apply_groups(self, space, tabs, reply):
        window = self.window
        before = list(space.today)
        still_loose = {id(n) for n in before}
        # Tabs closed or moved while Claude was thinking are left alone
        groups = [(name, [tabs[i] for i in indexes if id(tabs[i]) in still_loose])
                  for name, indexes in parse_tidy_groups(reply, len(tabs))]
        groups = [(name, members) for name, members in groups if len(members) >= 2]
        if not groups:
            window.sidebar.set_tidy_state("idle")
            window.toast("Your tabs already look tidy")
            return
        grouped = {id(t) for _, members in groups for t in members}
        space.today[:] = ([Folder(name, members, expanded=False) for name, members in groups] +
                          [n for n in before if id(n) not in grouped])
        self._tidy_undo = (space, before)
        window.store._changed()
        window.sidebar.set_tidy_state("undo")
        QTimer.singleShot(15_000, self._expire_undo)
        window.toast(f"Tidied {len(grouped)} tabs into {len(groups)} folders")

    def undo_tidy(self):
        window = self.window
        if not self._tidy_undo:
            return
        space, before = self._tidy_undo
        self._tidy_undo = None
        alive = {id(t) for t in walk_tabs(space.today)}
        restored = [n for n in before if isinstance(n, Folder) or id(n) in alive]  # closed tabs stay closed
        known = {id(t) for t in walk_tabs(restored)}
        opened_since = [t for t in walk_tabs(space.today) if id(t) not in known]
        space.today[:] = opened_since + restored
        window.store._prune_empty_folders(space.today)
        window.store._changed()
        window.sidebar.set_tidy_state("idle")
        window.toast("Put your tabs back")

    def _expire_undo(self):
        if self._tidy_undo:
            self._tidy_undo = None
            self.window.sidebar.set_tidy_state("idle")

    # --- Link previews (hover a link, tap ⇧) -----------------------------------------------------------------

    def preview_link(self, url):
        """Arc's "5-second previews": hover a link and tap Shift to see a summary of where it goes."""
        window = self.window
        card = window.content.preview_card
        if not url.startswith(("http://", "https://")) or not self.claude.available:
            return
        if url == card.url and card.isVisible():
            card.keep()
            return
        if self._preview_request is not None:
            self._preview_request.cancel()
            self._preview_request = None
        card.show_for(url)
        if url in self._preview_cache:
            card.set_content(*self._preview_cache[url])
            return
        # Read the page off-screen (same cookies as your tabs), then hand its text to Claude
        if self._preview_page is not None:
            self._preview_page.cancel()
        reader = self._preview_page = PageReader(window.profile, url, window, timeout_ms=8_000, enough_chars=800)
        reader.done.connect(lambda title, text: self._summarize(url, title or display_host(url), text))

    def _summarize(self, url, title, text):
        card = self.window.content.preview_card
        if card.url != url:
            return
        if not text.strip():
            card.set_content(title, "Couldn't read this page.")
            return
        card.set_content(title, "Summarizing…")
        request = self.claude.ask(
            page_block(title, url, text) +
            "\n\nWrite a link preview for this page: 2-3 short sentences on what it is and its key "
            "takeaway. Plain text only, no preamble.")
        self._preview_request = request
        request.partial.connect(lambda t: card.url == url and card.set_content(None, t))
        request.finished.connect(lambda t: self._finish_preview(url, title, t))
        request.failed.connect(lambda m: card.url == url and card.set_content(None, f"Couldn't summarize: {m}"))

    def _finish_preview(self, url, title, summary):
        self._preview_cache[url] = (title, summary)
        card = self.window.content.preview_card
        if card.url == url:
            card.set_content(title, summary)

    # --- Downloads -----------------------------------------------------------------------------------------

    def download_done(self, item, download):
        """Tidy Downloads: Claude gives the file a readable name (not for a private window's: that would send
        where it came from to Claude)."""
        if not getattr(item, "private", False) and self.window.store.settings.get("tidy_downloads", True):
            tidy_download(self.window, item, download)

    # --- Menus, commands and Settings -----------------------------------------------------------------------

    def commands(self):
        window = self.window
        return [("Tidy Today's Tabs", "ph.sparkle", self.tidy_today),
                ("Translate Page / Show Original", "ph.translate", self.toggle_translate),
                ("Tidy Tab Title", "ph.magic-wand", lambda: self.tidy_title(window.current_tab, force=True)),
                ("Turn Claude Features On / Off", "ph.sparkle", self.toggle)]

    def menus(self, add, menus, bar):
        window = self.window
        add(menus["View"], "Translate Page / Show Original", "", self.toggle_translate)
        self.claude_action = QAction("Claude Features", window, checkable=True)
        self.claude_action.setChecked(self.claude.available)
        self.claude_action.triggered.connect(lambda _=False: self.toggle())
        menus["View"].addAction(self.claude_action)
        add(menus["Tabs"], "Tidy Tab Title", "", lambda: self.tidy_title(window.current_tab, force=True))
        add(menus["Tabs"], "Tidy Today's Tabs", "", self.tidy_today)

    def settings_pages(self):
        return [("claude", "ph.sparkle", "Claude", self._settings_page, "profiles")]

    def _settings_page(self, settings):
        from .settings import _combo
        claude = self.claude
        box = self._settings_box = QCheckBox()
        box.setChecked(claude.enabled)
        box.toggled.connect(lambda on: on != claude.enabled and self.toggle())
        model = _combo(CLAUDE_MODELS, settings.store.settings.get("claude_model", "haiku"))
        model.currentIndexChanged.connect(lambda: self.set_model(model.currentData()))
        status = self._settings_status = QLabel()
        status.setObjectName("Hint")
        status.setWordWrap(True)
        about = QLabel(for_here(
            "Claude features run through the Claude Code app on this Mac (the claude command), using "
            "your Claude subscription. There's no API key, and nothing is sent unless you use a feature: "
            "Tidy Tab Titles (when you pin), Ask on Page (⌘F), command bar answers, link previews "
            "(tap ⇧) and Browse for Me (⌘↩). Each use counts toward your plan's limits."))
        about.setObjectName("Hint")
        about.setWordWrap(True)
        return settings._stack(
            settings._card([settings._row("Claude features", "", box),
                            settings._row("Model", "Haiku answers in about a second. Sonnet writes better Browse "
                                                   "for Me pages but is slower and uses more of your plan.", model)]),
            status, about)

    def settings_shown(self, settings):
        if getattr(self, "_settings_status", None) is None:
            return
        claude = self.claude
        if claude.exe is None:
            self._settings_status.setText("Claude Code isn't installed, so these features are off. Install it, "
                                          "then run `claude` once in Terminal to sign in.")
        else:
            self._settings_status.setText(f"Using {claude.exe}")
        self._settings_box.setChecked(claude.enabled)


FEATURE = ClaudeFeature
