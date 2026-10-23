# PythonBrowser — Documentation

**Author:** MasonKimball05
**Stack:** Python 3, PyQt6, QtWebEngine (Chromium), qtawesome

An Arc-inspired browser. Arc is being wound down in favour of Dia, so this project carries its ideas forward: a vertical sidebar, Spaces, pinned and favorite tabs, a command bar in place of a new-tab page, and tabs that archive themselves.

---

## Running it

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python main.py
```

> **Name the venv `venv`, not `.venv`.** Qt 6 won't load its macOS platform plugin from a path that contains a hidden (dot-prefixed) folder, and you'll get *"Could not find the Qt platform plugin 'cocoa'"*.

We moved from PyQt5 to PyQt6 because PyQtWebEngine for Qt 5 has no Apple Silicon build.

To try things without touching your real session, set a throwaway data folder:

```bash
PYBROWSER_DATA_DIR=/tmp/pybrowser-test venv/bin/python main.py
```

Your session, favicons, cookies and cache normally live in `~/Library/Application Support/MasonKimball05/PythonBrowser/`.

---

## The Arc model

| Concept | What it does |
|---|---|
| **Favorites** | The icon grid at the top of the sidebar. Every Space shares them. |
| **Spaces** | Separate sets of tabs, each with its own name, colour and emoji. The whole window takes on the Space's colour. |
| **Pinned tabs** | Tabs kept above the divider. Each one remembers its home URL. Closing one only *unloads* it (the `—` button). |
| **Today tabs** | Tabs below the divider. New tabs go on top. They're archived after 12 hours unused (you can change this from the archive button). |
| **Command bar** | ⌘T or ⌘L. One box for URLs, searches, open tabs, history, archived tabs and app commands. |
| **Little window** | ⌥⌘N, or a page's popup. A small throwaway window. "Open in Main Window" moves the live page into the sidebar. |

Drag tabs between Favorites, Pinned and Today to reorganise them. Right-click a tab or Space for more options. Swipe sideways with two fingers on the sidebar to switch Spaces.

### Importing from Arc

**File → Import from Arc…** (also offered automatically on first launch) reads Arc's local data in `~/Library/Application Support/Arc`, read-only:

- **Brought across:** Spaces (with name, emoji and colour), pinned tabs including nested folders, Favorites, Today tabs, your last 5,000 history entries (used for ⌘T suggestions) and site icons.
- **Not brought across:** logins and cookies, which are encrypted with Arc's own key, so you'll sign in again. Extensions don't come across either: Qt 6.11's extension support crashes when one is enabled. Revisit this when PyQt6 updates.
- **Importing again** replaces the Spaces you imported last time instead of duplicating them.

**Folders** only live in Pinned:
- Click a folder to open or close it.
- Drop a tab onto the middle of a folder to put it inside.
- Right-click for Rename, Ungroup or Delete.
- Right-click a tab and choose *Move to Folder* to file it.

### More Arc features

| Feature | How to use it |
|---|---|
| **Space themes** | Right-click a Space and choose *Edit Theme…*. Pick up to 3 colours for a gradient, set the intensity, choose light/dark/auto, and add optional grain. Changes preview live. |
| **Split View** | Choose *Tabs → Split View…* (⌃⇧=), drag a tab from the sidebar onto the page, or right-click a tab and choose *Open in Split View*. Up to 4 panes. Click a pane to focus it; hover a pane and click × to take it out, or press ⌃⇧- to separate. |
| **Peek** | Links that open a new tab from a pinned tab or favorite float in a card instead. Press Esc or click outside to close it, or use ⤢ to keep it as a tab. Turn it off with the "Turn Peek On / Off" command in ⌘T. |
| **Hidden sidebar** | After ⌘S hides it, touch the left edge of the window and the sidebar slides out over the page. |
| **Boosts** | Use the *Boosts* menu, then *Boost This Site…*, to add your own CSS and JavaScript for a site. *Zap Elements* lets you click things on the page to hide them for good; press Esc when done. |
| **Notes** | ⇧⌘N (*File → New Note*). A note is a tab with a text editor, saved as Markdown in your data folder. ⌘B / ⌘I / ⌘U format text, and typing "* " starts a list. |
| **Profiles per Space** | Right-click a Space and choose *Profile* to pick one, or *New Profile…* to create one. Each profile has its own cookies and logins, so Work and Personal can be signed into different accounts. Favorites use the default profile. |

### Ad blocking

Built in, because Qt can't run extensions yet. It works in three layers:
- **Network:** third-party requests to ad and tracker domains are cancelled before they leave the browser. The domains come from Peter Lowe's list and the StevenBlack hosts list (about 70,000 domains), refreshed weekly. Requests to the site you're on are never blocked, which keeps sites working.
- **Cosmetic:** a few CSS rules hide the empty boxes ads leave behind.
- **YouTube:** its ads come from the same servers as the videos, so a small script skips them instead (it clicks Skip, or jumps to the end).

The shield in the URL pill shows how many requests were blocked on the current site. Click it to allow ads on that site or turn blocking off.

### Downloads & yt-dlp

**File → Downloads** (⌥⌘L) lists every download.

**Download Video…** (⇧⌘D, or right-click a tab) works like this:
1. Paste a link; it's prefilled with the current page.
2. yt-dlp describes it: title, available qualities, subtitles, or playlist size.
3. Choose video or audio, the format, quality, subtitles and folder.

**Download Settings** (the gear) sets your defaults: save folder, file-name template, format and quality, subtitle languages, cookies from another browser, and a speed limit. Regular browser downloads use the same save folder.

It needs `brew install yt-dlp ffmpeg`. The format choices are ported from Custom-Mac-Media-Player: MP4 prefers H.264 + AAC so it plays everywhere.

### Claude features (Arc Max, without an API key)

These use the `claude` command-line tool (Claude Code) that's already logged in on this Mac. There's no API key and no per-call billing, but each use counts toward your Claude plan's usage limits.

| Feature | How to use it |
|---|---|
| **Tidy Tab Titles** | Pin a tab and Claude renames it to something short, like "Arc Browser". To redo it, pick *Tabs → Tidy Tab Title*. |
| **Ask on Page** | ⌘F, type a question ("who owns this?" or "summarize"), then press Return. The answer is based on the page's text. |
| **Command bar answers** | ⌘T, type a question. Ending it with "?" makes "Ask Claude" the top result. The answer streams in right there. |
| **Browse for Me** | In ⌘T, type a question and press ⌘↩ (or pick *Browse for Me*). It searches DuckDuckGo, reads the top results one at a time in the background, and opens a tab with one answer that cites its sources as numbered links. The answer is saved, so it survives restarts. |
| **5-second Previews** | Hover a link and tap Shift. A card summarizes the linked page; click the card to open it. |

To turn them all off, use *View → Claude Features*. When they're off, nothing is ever sent to Claude.

How it works (`browser/ai.py`):
- Starting the CLI takes about 8 seconds. To hide that, one `claude -p` process is kept **warm**, already started and waiting for input. Answers begin in under a second.
- Each process answers one request (a fresh context every time) and is then replaced.
- Web pages are untrusted, so the CLI runs with **all tools disabled** (`--tools ""`), no MCP servers, no settings or CLAUDE.md files, and from a neutral folder. The worst a malicious page can do is make an answer wrong.

### Keyboard shortcuts

| Keys | Action |
|---|---|
| ⌘T / ⌘L | Command bar: new tab / edit current URL |
| ⌘W, ⇧⌘T | Close tab, reopen closed tab |
| ⌘S | Toggle sidebar |
| ⌘D | Pin / unpin tab |
| ⇧⌘C | Copy URL |
| ⌘F, ⌘G, ⇧⌘G | Find, next, previous |
| ⌘1–⌘9 | Jump to tab (⌘9 = last) |
| ⌃Tab, ⌥⌘↑/↓ | Next / previous tab |
| ⌃1–⌃9, ⌥⌘←/→ | Jump to Space / cycle Spaces |
| ⇧⌘K | Clear (archive) Today tabs |
| ⌘= ⌘- ⌘0 | Zoom |
| ⌥⌘I | Developer Tools |

---

## Testing

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/python -m pytest tests
```

The tests cover:
- the tab, folder and Split View model, plus save/load
- URL detection
- ad-block matching
- yt-dlp arguments
- the Arc importer, against a fake Arc folder (never your real one)
- Boosts, Notes, themes
- Browse for Me's Markdown rendering, including an HTML-injection check

They run headless and use a throwaway data folder.

## Code layout (`browser/`)

| File | Responsibility |
|---|---|
| `app.py` | Creates the `QApplication`, profile, `Store` and window. `run()` is the entry point. |
| `models.py` | Pure data: `Tab`, `Space` and `Store` (load/save, move/archive tabs, history). It contains no widgets. |
| `storage.py` | Disk paths, atomic JSON writes, favicon cache keyed by host. |
| `theme.py` | Space palette, light/dark tinting (follows macOS), the app-wide stylesheet, letter/dot icons. |
| `window.py` | `MainWindow`: the controller. It owns the web views and every user action (tabs, spaces, menus, shortcuts, downloads, fullscreen, devtools). |
| `sidebar.py` | The sidebar widgets: tab rows, favorites grid, drag-and-drop zones, space switcher. |
| `command_bar.py` | The ⌘T overlay and its result ranking (`to_url()` decides URL vs search). |
| `content.py` | The rounded page card, plus overlays: progress line, link-hover label, find bar, toasts. |
| `little.py` | Little windows. |
| `webview.py` | Profile setup (one per name: persistent cookies, user agent), `WebPage` and `WebView`. |
| `theme_editor.py` | The Space theme editor. |
| `split.py` | Split View panes. |
| `peek.py` | The Peek overlay. |
| `boosts.py` | Boosts (per-site CSS/JS) and the Zap tool. |
| `notes.py` | Note tabs (Markdown editor). |
| `adblock.py` | The request interceptor, block lists, and cosmetic/YouTube scripts. |
| `downloads.py` | The Downloads panel, yt-dlp integration and download settings. |
| `ai.py` | The Claude CLI worker pool. |
| `browse.py` | Browse for Me, plus `PageReader` (reads a page's text off-screen without waiting for its ads). |
| `arc_import.py` | Importing from Arc. |
| `macos.py` | Native macOS tweaks via the Objective-C runtime (hides the title text). |

### How the pieces talk

- Widgets never mutate data. They call `MainWindow` methods (`activate_tab`, `move_tab`, `close_tab` …).
- `MainWindow` changes the `Store`. The Store emits `structure_changed`, and the sidebar is rebuilt once per event-loop pass.
- Per-tab updates (title, favicon, URL) go out through `Tab.changed`. Each sidebar row listens to its own tab, so nothing is rebuilt.
- Web views are **lazy**. A restored or pinned tab gets no Chromium page until you click it.

### Ideas for next steps

- **Extensions**, once a PyQt6 update fixes enabling them (uBlock Origin, password managers)
- **Tidy Tabs / Tidy Downloads** with Claude
- **A real Mac app bundle** with its own Dock icon
- **Tests** for `models.py` (moves, folders, splits)
