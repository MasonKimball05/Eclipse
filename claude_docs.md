# Halo — Documentation

**Author:** MasonKimball05
**Stack:** Python 3, PyQt6, QtWebEngine (Chromium), qtawesome

**Halo** is an Arc-inspired browser. It was called PythonBrowser at first, and that name lives on underneath: the data folder, the Keychain entries and the bundle ID. Arc is being wound down in favour of Dia, so this project carries its ideas forward: a vertical sidebar, Spaces, pinned and favorite tabs, a command bar in place of a new-tab page, and tabs that archive themselves.

---

## Running it

### As a Mac app (recommended)

```bash
./run.sh --build-app
```

This builds `~/Applications/Halo.app`, which you can open from Spotlight, Launchpad or the Dock like any other app. (An older `PythonBrowser.app` from before the rename goes to the Trash; it's only a launcher. It keeps the same bundle ID, so macOS still treats it as your default browser and remembers its camera and microphone permissions.) Then choose **Halo menu → Set as Default Browser…**, and macOS asks you to confirm.

- **Links from other apps** (Slack, Mail, Messages…) open in a Little window, the same as Arc.
- **No rebuild needed for code changes:** the app is a small native launcher that runs this checkout with its virtualenv, so your next launch picks them up. Rebuild only if you move the checkout or Homebrew moves to a new Python version (3.15+).
- **Only one copy runs.** Opening it again, or running `./run.sh https://…`, hands the link to the window that's already open.
- **Signing:** by default the app is signed ad hoc, which is all your own Mac needs. To sign it properly (needed for Apple's Passwords helper; see Extensions below):

  ```bash
  ./run.sh --build-app --sign "Developer ID Application: Your Name (TEAMID)"
  ```

  This uses Apple's hardened runtime, with the exceptions a Python/Qt browser needs (loading the virtualenv's libraries, and run-time code for JavaScript and ctypes). Tested with an Apple Development certificate: the app runs and loads pages normally. To also notarize, save your credentials once with `xcrun notarytool store-credentials PROFILE --apple-id you@example.com --team-id TEAMID`, then add `--notarize PROFILE`. `--output FOLDER` builds elsewhere for testing; only the copy in ~/Applications registers as a browser with macOS.

### From the terminal

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

Your session, favicons, cookies and cache normally live in `~/Library/Application Support/MasonKimball05/PythonBrowser/` (the internal name, so renaming the app to Halo didn't move anything; the Keychain entries for your app tokens keep it too).

---

## The Arc model

| Concept | What it does |
|---|---|
| **Favorites** | The icon grid at the top of the sidebar. Every Space shares them. |
| **Spaces** | Separate sets of tabs, each with its own name, colour and emoji. The whole window takes on the Space's colour. |
| **Pinned tabs** | Tabs kept above the divider. Each one remembers its home URL. Closing one only *unloads* it (the `—` button). |
| **Today tabs** | Tabs below the divider. New tabs go on top. They're archived after 12 hours unused (you can change this from the archive button). |
| **Command bar** | ⌘T or ⌘L. One box for URLs, searches, open tabs, history, archived tabs and app commands. |
| **Little window** | Arc's "Little Arc": links from other apps, page popups, or ⌥⌘N open in a compact window. It has a slim header tinted with your Space theme, the site's name (click it or press ⌘L to edit the address), back/reload/copy-link buttons, and **Open** (⌘O), which moves the live page into the main window as a Today tab. To send outside links to a new tab instead, use *File → Open Links from Other Apps In*. |

Drag tabs between Favorites, Pinned and Today to reorganise them. Right-click a tab or Space for more options. Swipe sideways with two fingers on the sidebar to switch Spaces.

### Importing from Arc

**File → Import from Arc…** (also offered automatically on first launch) reads Arc's local data in `~/Library/Application Support/Arc`, read-only:

- **Brought across:** Spaces (with name, emoji and colour), pinned tabs including nested folders, Favorites, Today tabs, your last 5,000 history entries (used for ⌘T suggestions) and site icons.
- **Not brought across:** logins and cookies, which are encrypted with Arc's own key, so you'll sign in again. Extensions are imported separately: *Settings → Extensions → Add Extension → Import from Arc* (see Extensions & userscripts below).
- **Importing again** replaces the Spaces you imported last time instead of duplicating them.

**Folders** only live in Pinned:
- Click a folder to open or close it.
- Drop a tab onto the middle of a folder to put it inside.
- Right-click for Rename, Ungroup or Delete.
- Right-click a tab and choose *Move to Folder* to file it.

### More Arc features

| Feature | How to use it |
|---|---|
| **Editing a Space** | Click the Space's name at the top of its tabs, or right-click it or its icon in the switcher. A popover lets you rename it, pick an icon (line icons or emoji), choose a colour, switch its profile, open the theme editor, add a folder or delete the Space. Every change applies immediately. |
| **Space themes** | Right-click a Space and choose *Edit Theme…*. Pick up to 3 colours for a gradient, set the intensity, choose light/dark/auto, and add optional grain. Changes preview live. |
| **Split View** | Choose *Tabs → Split View…* (⌃⇧=), drag a tab from the sidebar onto the page, or right-click a tab and choose *Open in Split View*. Up to 4 panes. Click a pane to focus it; hover a pane and click × to take it out, or press ⌃⇧- to separate. |
| **Peek** | Links that open a new tab from a pinned tab or favorite float in a card instead. Press Esc or click outside to close it, or use ⤢ to keep it as a tab. Turn it off with the "Turn Peek On / Off" command in ⌘T. |
| **Resizing the sidebar** | Drag its right edge, which highlights on hover. Double-click the edge to reset it. The width is kept between 190 and 420 pt and remembered. |
| **Hidden sidebar** | After ⌘S hides it, touch the left edge of the window and the sidebar slides out over the page. |
| **Boosts** | Use the *Boosts* menu, then *Boost This Site…*, to add your own CSS and JavaScript for a site. *Zap Elements* lets you click things on the page to hide them for good; press Esc when done. |
| **Notes** | ⇧⌘N (*File → New Note*). A note is a tab with a text editor, saved as Markdown in your data folder. ⌘B / ⌘I / ⌘U format text, and typing "* " starts a list. |
| **Easels** | *File → New Easel* (or ⌘T → "New Easel") opens a whiteboard tab: text, sticky notes, drawing, rectangles, ellipses and arrows in six colours (shortcuts V T S P R O A), plus images you paste or drop in. ⌘-scroll or pinch to zoom, ⌘0 fits everything, ⌘Z / ⇧⌘Z undo and redo, Backspace deletes. Click the name at the left of its toolbar to rename it. |
| **Capture** | ⇧⌘2 on any web page, then drag a box: that part of the page goes onto your most recently used easel (or a new one), captioned with the page's title. Double-click it on the easel to open the page again. |
| **Live Folders** | ⌘T → "New Live Folder": a pinned folder that fills itself from a feed (RSS/Atom, or just a site's address and its feed is found) or from GitHub (PRs to review, your open PRs, issues assigned to you, via the `gh` CLI you're signed in to). It refreshes every half hour; new items are bold, with a count on the folder, until you open them. Right-click it to refresh now or mark everything seen. |
| **Spell check** | Turn on *Check spelling while typing* in *Settings → General*. The first time, it downloads your language's dictionary (about 450 KB for English) from the server Chrome gets its own from; after that, checking happens on this Mac and nothing is sent anywhere. Misspelled words in text fields get underlined, and right-clicking one puts corrections at the top of the menu. English, Spanish, French, German, Italian, Portuguese and Dutch. |
| **Selected text** | Select some text and right-click: *Ask Claude About* (or the other assistant, such as Qwen) opens ⌘T with the answer about that passage, *Translate* shows it in your language in ⌘T, and for a word or short term *Look Up in Dictionary* opens macOS's Dictionary. |
| **Sound** | A speaker appears on tabs playing audio; click it to mute or unmute the tab (also in the tab's right-click menu). |
| **Mini player** | Switch away from a tab that's playing a video and the video keeps playing in a small floating window. Hover it for play/pause, back to the tab, and close (which pauses it). Drag the top strip to move it. Turn it off in *Settings → Tabs*. |
| **Site search** | In ⌘T, type a site's name or keyword and press Tab to search just that site (YouTube, GitHub, Wikipedia, Maps, Images, Amazon, Reddit, X, Stack Overflow, MDN, PyPI, npm, Gmail, Drive, Spotify, Claude). Or start with the keyword: "yt lofi", "gh pyqt6". Backspace on an empty box leaves site search. |
| **Air Traffic Control** | *Settings → Air Traffic Control*: rules like "links matching zoom.us open in Work". Matching links from other apps open as a tab in that Space instead of a Little window. A domain covers its subdomains; anything else matches text in the link. |
| **Backup & restore** | *File → Back Up Everything…* saves Spaces, tabs, history, settings (Boosts, rules, extension lists), notes, userscripts and extensions to one .zip. Sign-ins and cookies aren't included. *Restore from Backup…* quits, and the next launch swaps the backup in; what it replaced is kept in a `before-restore-…` folder in the data folder. |
| **Site permissions** | When a site asks for your camera, microphone, screen, location or notifications, a card appears at the top of the page: *Don't Allow*, *Allow*, or × to decide later. Camera and microphone answers are remembered per site; screen sharing asks every time and then lets you pick a screen or window. A tab that asks while you're elsewhere waits until you switch to it. Change any answer in Site Controls (the shield in the URL pill) or *Settings → Privacy*. |
| **Notifications & badges** | Sites you've allowed show banners in the top-right corner; click one to go to its tab. Pinned tabs and Favorites show the unread count from the site's title ("(3) Inbox"), and the Dock icon shows the total. Turn on Do Not Disturb in *Settings → Tabs* or from ⌘T. Tabs allowed to notify you don't go to sleep. |
| **Autocomplete** | In ⌘T and ⌘L, sites you visit often are filled in as you type ("gi" → github.com), ranked by how often and how recently you went there. → or End takes the suggestion; Esc or Backspace drops it. |
| **Recent-tab switching** | Hold ⌃ and tap Tab to go back through recently used tabs, shown as cards with a picture of each page; let go to switch. A quick ⌃Tab flips to the previous tab. Esc cancels. *Settings → Tabs* switches ⌃Tab back to sidebar order. |
| **Per-site zoom** | ⌘= / ⌘- / ⌘0 zoom the page and remember it for that site. A chip in the URL pill shows the level; click it to reset. |
| **Print, PDF & saving** | ⌘P prints, ⌥⌘P saves the page as a PDF, ⇧⌘S saves it as a web archive (.mhtml), and ⌥⇧⌘C copies the page as a Markdown link (`[Title](url)`). |
| **Swipe gestures** | Swipe right with two fingers on a page to go back, left to go forward; an arrow slides in from the edge and fills in once letting go will navigate. Pages that scroll sideways themselves (carousels, code blocks, maps) keep the swipe. Swiping on the sidebar slides to the next or previous Space. Turn page swipes off in *Settings → Tabs*. |
| **Developer Mode** | Pages you're building (localhost, 127.0.0.1, `*.localhost`, `*.test`, files) show their full address with port in the URL pill and a **DEV** chip that turns red with the page's error count. Click it (or ⌥⌘J) for the console drawer under the page: the page's log, a line to run JavaScript in it (↑ for history), DevTools, and **Reload on Save**: pick your project folder and the page reloads whenever a file in it changes (`node_modules`, `.git` and build folders are ignored). Pages opened from a file reload on save by themselves. |
| **Now Playing** | When a tab you've left is playing music or a video, a card at the bottom of the sidebar shows the track and artist (from the site's media info), with play/pause, next track (YouTube, Spotify, SoundCloud, YouTube Music) and × to stop. Click it to go back to the tab. Videos still go to the mini player first. |
| **Reader view** | ⌥⌘R (or ⌘T → "Reader View") shows just the article: a clean column with the site, title and reading time, in light, sepia or dark, with bigger or smaller text. It's rebuilt from safe tags and shown with JavaScript off. Esc closes it. |
| **Translate Page** | ⌘T → "Translate Page", or *Translate* in Reader view: the page's text is translated in place by the local Claude CLI, top of the page first, into your Mac's language. Run it again to show the original. Code and anything marked `translate="no"` is left alone. |
| **Crash recovery** | Tabs are saved as you go (with the previous copy kept as a spare), so after a crash everything comes back, with a note saying so. A page whose renderer crashes shows a card with Reload; one in the background reloads when you return to it. Errors and hard-crash stacks go to the `logs` folder (⌘T → "Show Crash Logs"). |
| **Updates** | Once a day Halo checks its GitHub branch and offers *Update & Restart* when there are new commits: it fast-forwards the checkout, installs new requirements, and relaunches with your tabs. A checkout with uncommitted changes or unpushed commits is never touched. *Settings → General → Updates* has Check Now. |
| **Private windows** | ⇧⌘P (*File → New Private Window*, ⌘T, or right-click a link → *Open Link in Private Window*). A dark window with its own tabs, sharing one in-memory profile: no history, suggestions, site icons, zoom levels, remembered permissions or page text are kept, and extensions don't run. Cookies and logins are forgotten when the last private window closes. Ads are still blocked. |
| **Clean links** | Tracking tags such as `utm_source`, `fbclid` and `gclid` are taken off a link before it opens, and off links you copy (⇧⌘C, *Copy Link*, the Markdown link). Everything else in the address stays exactly as it was. YouTube's and Spotify's `si` share tags go too. Off in *Settings → Privacy*. |
| **Certificate warnings** | A site with a bad security certificate (expired, for another site, or not from a trusted authority) shows *This connection isn't private* instead of loading. **Go Back** returns you to the previous page, or closes the tab if it was opened just for that site. *Details* says what's wrong and, where the site allows it, offers *Visit Anyway* for the rest of the session. Local development servers say that this is expected. |
| **Cookie banners** | On by default: consent pop-ups get "Reject all" / "Only necessary" clicked for you, never Accept. The big consent tools are recognised by their own buttons, others by a careful match on the button's label. Toggle it from the shield in the URL pill or *Settings → Privacy*. |
| **Copied links** | Copy a link anywhere, then press ⌘T: it's the first suggestion ("Copied link"), so Return opens it. |
| **Search what you've read** | ⌘T also finds pages by their text, not just their titles: type a few words you remember and matches appear marked *Read*, with the sentence around them. The text is kept in a local index (5,000 most recent pages), never from private windows or sign-in pages. Clear it or turn it off in *Settings → Privacy*; the shield menu can exclude a site. |
| **Instant answers** | ⌘T answers on the spot, with no search: sums (`23*4+7`, `15% of 80`, `sqrt(2)`), conversions (`5 km in miles`, `72 f in c`, `3 cups to ml`, `1 gb in mib`), the time anywhere (`time in tokyo`, `london time`) and colours (`#7c5cff` → swatch, RGB, HSL). Return copies the answer. |
| **Back to the pinned page** | A pinned tab or Favorite that's wandered off its pinned page shows a "/" after its icon, like Arc. Point at the icon and it becomes ↰; click it to go back. |
| **Recently Closed** | *History → Recently Closed* lists the last 15 tabs you closed; pick one to bring it back (⇧⌘T still reopens the latest). |
| **Search shortcuts** | *Settings → General → Search shortcuts*: add your own sites to search from ⌘T, e.g. keyword `parl` with `https://…/search?q=%s`. Type `parl budget` or `parl` + Tab. Yours win over a built-in keyword. |
| **Profiles per Space** | Right-click a Space and choose *Profile* to pick one, or *New Profile…* to create one. Each profile has its own cookies and logins, so Work and Personal can be signed into different accounts. Favorites use the default profile. |

### My Apps (your own apps and sites)

Set up in *Settings → My Apps* (addresses default to your setup: homebase and the desktop apps on `arkans-pc1` by its full Tailscale name, so nothing on another network can answer for it, and Parliament on am-parliament.org).

- **Homebase chip** (bottom of the sidebar, next to Downloads): a server icon with a dot, green when every app homebase supervises is up, amber when some aren't, grey when homebase can't be reached (Tailscale off). Its menu lists each app with Open, Restart, Stop/Start and Show Logs, plus the dashboard, Local AI Chat, and adding Live Folders.
- **Live Folders for your apps** (*Add Live Folders for My Apps…* in that menu, ⌘T, or Settings): Parliament notifications (with your login in this browser), Job Radar matches and follow-ups, gradtrack deadlines, homebase's Away queue, your recently pushed GitHub repos, and **Repo Radar** (local repos with uncommitted or unpushed work, built in). The *New Live Folder* dialog also takes any JSON API (signed in with your browser login or a token kept in the Keychain), the links on any page matching a CSS selector, and any `.ics` calendar.
- **Send To** (⌘T, or right-click a page → *Send Page To*): *Save Job Posting to Job Tracker* (opens its New Job form with the page filled in), *Add Page to Daybook* (as a task), *Security Grade for This Site* (Sentinel's checkup: A–F with what to fix), and *Open Repo in Loom* on GitHub pages of repos you have cloned.
- **Local AI (Qwen):** questions in ⌘T get an *Ask qwen3-coder (local)* row next to Ask Claude, answered by the model homebase serves through its Ollama proxy. Choose which one answers by default (and for Ask on Page) in Settings → My Apps.
- **Search shortcuts** for your sites: `parl` searches Parliament, `mygh` searches your own GitHub code (*Add Search Shortcuts* in Settings → My Apps). *Import Hop's Quicklinks* brings over the ones Hop has that the browser doesn't (go, g).
- **Up Next** (from Daybook): your next event today in a card at the bottom of the sidebar, "2:00 PM · in 12 min", turning your Space's colour when it's on or about to start. An event with a meeting link (Zoom, Meet, Teams, Webex) gets a **Join** button; click the card for Daybook. It reads the `hop.json` Daybook already writes for Hop.
- **Parliament's unread count** shows on your pinned Parliament tab or Favorite (and in the Dock badge), read from Parliament's own notifications every few minutes with your login, the way Gmail's "(3)" does.
- **Give Issue to the Away Queue** (⌘T or Send Page To, on a GitHub issue): picks the matching homebase project (Parliament → Parliament-Dev), shows the task it will queue for you to edit, and only then sends it to homebase's Away runner for the local model to work on.
- **Repos:** right-click a GitHub tab of a repo you have cloned (or a Repo Radar item) for *Open Repo in Loom* and *Show Repo in Finder*.
- **Tokens:** a Live Folder signed in with a token keeps it in the Keychain (passed to `security` privately, never on its command line); *Set Token…* in the folder's menu replaces it, and deleting the folder removes it. A token is never sent on to another site by a redirect.
- **Save Job chip:** on a job posting (LinkedIn, Greenhouse, Lever, Workday, Ashby, Indeed, Wellfound, Handshake…) a *Save Job* chip appears in the URL pill; one click opens Job Tracker's New Job form with the posting filled in.
- **Your sites:** the homebase menu also lists what go-sentinel sees of am-parliament.org and masonkimball.dev (all checks pass, the certificate's end date, or the first problem), and a toast says when a site's status changes.
- **⌘T knows your apps:** type "restart job tracker", "open gradtrack" or "logs shelf".
- **Chapter Events:** a Live Folder of Parliament's calendar; adding it opens Parliament's Subscribe page for your personal feed link.
- **Alerts:** when an app on homebase goes down (not when you stop it), a toast says so and the Dock icon bounces if the browser is in the background; another says when it's back.

### How it feels

- **Motion:** the sidebar slides away and back (⌘S) and slides out from the edge when hidden, the command bar settles into place as the page dims, the sidebar slides between Spaces, and toasts, notification banners and permission cards fade in and out. Everything is under a quarter of a second. With *Reduce motion* on in macOS's Accessibility settings, things just appear.
- **Controls:** on/off settings are macOS-style switches, and sliders match them, in your Space's colour (system blue for grey Spaces). Scrollbars stay hidden until you scroll or point at them.
- **Site icons:** an icon that would vanish against the sidebar (GitHub's all-white one on a light Space, an all-black one on a dark Space) gets a small contrasting tile.
- **Empty page:** with nothing open, the page area shows the few keys that get you anywhere (⌘T, ⌘L, ⇧⌘P, ⌘/).

### Settings, shortcuts & Library

- **Settings** (⌘,, or *Halo → Settings…*) has a search box at the top ("sleep", "cookie", "qwen"): pages without a match step aside and matching settings light up; Return scrolls to the first. It has one page each for:
  - **General:** default browser, where outside links open, search engine (Google, DuckDuckGo, Bing, Brave, Kagi or Ecosia), and spell check (off until you turn it on; the first time, it downloads your language's dictionary, a few hundred KB, from Chrome's dictionary server).
  - **General** also has Back Up / Restore, and Updates (automatic daily check, Check Now).
  - **Tabs:** archive timing, tab sleeping, Peek, the mini player, ⌃Tab order, swipe to go back, notification banners.
  - **Air Traffic Control:** which Space outside links open in.
  - **Privacy & Ad Blocking:** blocking on/off, allowed sites, block lists, clearing history.
  - **Downloads:** your yt-dlp defaults, and **Tidy Downloads** (Claude renames finished downloads, e.g. "dl_83jd92.pdf" → "Northwind Traders Invoice 1042.pdf", reading the start of PDFs and text files; on by default).
  - **Claude:** on/off, and Haiku or Sonnet.
  - **Profiles:** which Spaces use each profile, and clearing a profile's cookies.
  - **My Apps:** your apps' addresses, who answers questions (Claude or the local model), and quick setup for their Live Folders and search shortcuts.
- **Keyboard Shortcuts** (⌘/) is a searchable list, built from the menus so it's always current.
- **Library** (⌘Y) has History grouped by day, Archived Tabs, Notes, and Browse for Me answers, with search. Double-click to open; right-click for Little window, Copy Link or Remove. Opening an archived tab restores it.
- **Tab sleeping:** tabs you haven't looked at for an hour (adjustable in Settings) are discarded to free memory. They dim in the sidebar and reload when clicked. In testing, putting 3 of 4 open pages to sleep cut their memory from 407 MB to 104 MB. The current tab, visible split panes and anything playing audio stay awake. *Tabs → Put Other Tabs to Sleep* does it immediately.

### Ad blocking

Built in, so it works without an extension. It works in three layers:
- **Network:** third-party requests to ad and tracker domains are cancelled before they leave the browser. The domains come from Peter Lowe's list and the StevenBlack hosts list (about 70,000 domains), refreshed weekly. Requests to the site you're on are never blocked, which keeps sites working.
- **Cosmetic:** a few CSS rules hide the empty boxes ads leave behind.
- **YouTube:** its ads come from the same servers as the videos, so a small script skips them instead (it clicks Skip, or jumps to the end).

The shield in the URL pill shows how many requests were blocked on the current site. Click it to allow ads on that site or turn blocking off.

### Extensions & userscripts

Qt WebEngine's own extension support crashes on macOS, so Halo has its **own lightweight runtime** (`browser/ext/`). Manage everything in **Settings → Extensions**. The puzzle piece in the URL pill lists your extensions (click one for its popup); *Pin to URL Bar* in that menu gives an extension its own button, with its badge. Keyboard shortcuts from an extension's manifest work too (⌘⇧X opens Proton Pass), unless they'd take over one of the browser's.

**Installing:** on a Chrome Web Store extension page, click **Add to Halo** (bottom right). Before anything is installed you see what it can access and anything that won't work here. Extensions and userscripts installed from the web are **checked for updates daily** (or *Check for Updates* in Settings); an update that asks for more permissions asks you first.

**Userscripts** (Greasemonkey/Tampermonkey format):
- Open any `.user.js` link (e.g. Greasy Fork's Install button) and an install dialog shows where the script runs and what it can use. You can also add one from a link or file, or write a new one.
- Supported: `@match`/`@include`/`@exclude`, `@run-at`, `@require` (downloaded at install), `@noframes`, and `GM_getValue/setValue/deleteValue/listValues`, `GM_addStyle`, `GM_xmlhttpRequest` (cross-site, made from Python), `GM_openInTab`, `GM_setClipboard`, `GM_notification`, `GM.*` promise versions, `unsafeWindow`.
- Scripts with `@grant none` run in the page like Tampermonkey; scripts using GM APIs each run in an isolated world of their own, which neither the page nor other scripts can reach.

**Chrome extensions** — install from a Chrome Web Store link (or while on the store page), a `.crx`/`.zip`, an unpacked folder, or import from Arc. Files are served from `pbext://<id>/`.
- Works: content scripts and CSS (`run_at`, `all_frames`, `MAIN` world), messages from websites the extension lists in `externally_connectable` (`onMessageExternal`, which is how Proton Pass signs in), `chrome.storage` (local/sync/session, with `onChanged`), `runtime.sendMessage`/`onMessage` with replies, long-lived ports (`runtime.connect`, `tabs.connect`, `onConnect`), `tabs.*` (query, create, update, remove, sendMessage, events), `i18n`, toolbar popups with badges, options pages, background pages and MV3 service workers (run as hidden pages, with `importScripts`), `scripting.executeScript`/`insertCSS` (including `world: "MAIN"` and `allFrames`), offscreen documents, `contextMenus` (in the page's right-click menu), `commands`, `alarms`, `notifications`, `downloads.download`, and cross-site `fetch` from extension pages.
- **Native messaging** (`runtime.connectNative`, `sendNativeMessage`): extensions can talk to helper programs on this Mac, found the way Chrome finds them: registered in a `NativeMessagingHosts` folder (Chrome's, Chromium's, Arc's, Edge's, Brave's, or our own data folder), and only for extensions the helper lists in `allowed_origins`. Exits are noted on stderr (`[extensions] native helper … exited`).
- **Blocking rules (`declarativeNetRequest`)** run in the browser's request interceptor: static rulesets, dynamic and session rules, block / allow / allowAllRequests / redirect / upgradeScheme, and request-header changes. Rules are indexed by domain and keyword, so an ad blocker's 200,000 rules load in under a second (on a background thread) in about 20 MB and cost a fraction of a millisecond per request. When an extension's rule decides a request, the built-in blocker leaves it alone.
- Approximated: `webNavigation` (from tab updates, no per-frame detail). `webRequest` can **watch** requests (`onBeforeRequest`, `onBeforeSendHeaders`, `onSendHeaders`, with URL/type/tab filters) but not block or change them from a listener, because Qt won't pause a request; the response-side events never fire (Qt 6.11 has no response hook).
- Present but inert, so code that touches them doesn't crash: blocking `webRequest`, `sidePanel`, `tabGroups`, `cookies`, `identity`, `nativeMessaging` and similar. Extensions that rely on these are marked "may not work fully". Response-header rules can't be applied (Qt doesn't allow it).
- **Isolation:** each extension's content scripts run in their own JavaScript world, separate from the page and from other extensions. Qt only allows its web channel in one world per page, so these worlds call Python with console messages stamped with a secret per-world token, and get answers by Python running code in that world. Websites can't call the runtime at all.
- What this means for the Arc five:
  - **Pie Adblock blocks** with its own rules (ads blocked, trackers swapped for harmless stand-ins, cookie banners blocked).
  - **Proton Pass** loads cleanly: its popup, its autofill dropdown inside pages, and the account.proton.me sign-in handoff all work as far as can be tested without signing in. Try signing in.
  - **Video Downloader** can now see requests, but it decides what's a video from response headers, which Qt can't show it. The built-in yt-dlp downloader covers this.
  - **iCloud Passwords** starts Apple's Passwords helper over native messaging, but **macOS kills the helper on launch** ("Launch Constraint Violation", SIGKILL): Apple only lets it run for browsers that pass its signing rules. Tested 23 Oct 2026 by launching PythonBrowser.app normally: killed when signed ad hoc, signed with an Apple Development certificate, and signed with that certificate plus the hardened runtime. Arc, a notarized Developer ID app, gets through. Next to try once the Developer ID certificate exists: `./run.sh --build-app --sign "Developer ID Application: …"`, then with `--notarize` if that's not enough (or Apple may only allow certain browsers). When this happens the browser says so in a toast.
  - **Claude** needs the debugger and side panel. It can now reach Claude Code's own native host, but its panel can't appear.
- Changes apply to pages loaded afterwards.

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
| **Tidy Tabs** | Click **✦ Tidy** above Today, which appears once you have 4+ loose Today tabs, or use *Tabs → Tidy Today's Tabs*. Claude groups related tabs into topic folders such as "Rome Trip" or "React Docs", and leaves unrelated ones alone. For 15 seconds afterwards, **↶ Undo Tidy** puts everything back. Claude only sees tab titles and site names. |
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
| ⌃Tab | Recently used tabs (hold ⌃; Esc cancels) |
| ⌥⌘↑/↓ | Next / previous tab |
| ⌃1–⌃9, ⌥⌘←/→ | Jump to Space / cycle Spaces |
| ⇧⌘K | Clear (archive) Today tabs |
| ⌘= ⌘- ⌘0 | Zoom (remembered per site) |
| ⌘P, ⌥⌘P | Print, save as PDF |
| ⇧⌘S | Save page as a web archive |
| ⌥⇧⌘C | Copy as a Markdown link |
| ⌥⌘R | Reader view |
| ⇧⌘P | New private window |
| ⌥⌘I, ⌥⌘J | Developer Tools, Developer Mode console |

---

### Troubleshooting slow pages

- **Pages (GitHub especially) take 15–30 s or never finish**, while `curl` gets them in a second: the
  network has IPv6 routes that go nowhere (on 23 Oct 2026 the Mac's IPv6 default routes went through VPN
  tunnels, utun0–2, with no IPv6 on Wi-Fi). Chromium tries IPv6 first and waits on each attempt before
  falling back to IPv4. Check with `curl -6 https://github.githubassets.com` (it times out). Fix it in
  the VPN (Tailscale: turn off its IPv6 route / exit-node IPv6) or set Wi-Fi's IPv6 to *Link-local only*
  in System Settings → Network → Wi-Fi → Details → TCP/IP.
- **Pages load slowly while the browser is behind other windows**: macOS App Nap. The browser now holds
  off App Nap while anything is loading (`macos.busy`), which roughly halved load times in testing.

## Testing

```bash
./run.sh --tests
./run.sh --live-tests
```

The tests cover:
- the tab, folder and Split View model, plus save/load
- URL detection
- ad-block matching
- yt-dlp arguments
- the Arc importer, against a fake Arc folder (never your real one)
- Boosts, Notes, themes
- Browse for Me's Markdown rendering, including an HTML-injection check
- the extension runtime: match patterns, userscript metadata, manifests and i18n, content-script building, CRX/zip install (including path-traversal checks), using the demo extension and userscript in `tests/fixtures/`
- blocking rules: urlFilter syntax, domain and keyword indexing, priorities, conditions, redirects and header rules
- easels (saving, loading, undo, Retina captures), Live Folders (RSS/Atom parsing, feed discovery, keeping tabs and marking new ones), Tidy Downloads (name cleaning, PDF text)
- site search shortcuts, Air Traffic rules, and backup/restore (including that cookies stay out and archives can't write outside the data folder)
- update checks: version comparison, the Web Store's update answer, and spotting new permissions
- native messaging: the length-prefixed wire format, finding helpers and checking `allowed_origins`, and a real round trip through a helper process (`tests/fixtures/native_echo.py`)
- the runtime's security: forged tokens are ignored, a world can't act for another extension, and websites get nothing
- site permissions (origins, remembered answers, prompt wording), unread counts from titles, autocomplete and frecency, per-site zoom, file names for saving and Markdown links
- My Apps: presets against each app's real answer shapes, homebase's summary, Qwen's thinking stripped, Repo Radar on real git repos, calendar feeds
- instant answers (and what isn't one), custom search shortcuts, the pinned-tab "away" check
- cookie-banner rules (never Accept), the page-text index (adding, searching, replacing, skipping a site, clearing, safe queries)
- Developer Mode (which pages count, labels, watched files), Translate (chunking, answer checking, prompt), Reader's escaping, Now Playing text, crash detection and the session's spare copy, and self-updating against a real local git remote (fast-forward, never over your changes)

They run headless and use a throwaway data folder.

**Live tests** (`./run.sh --live-tests`, or `python -m pytest tests --live`) drive the real window against pages from a local server in `tests/live/site`, about 30 seconds with a window showing: swipes (and not over a carousel), cookie banners (and not other buttons), private windows keeping nothing, ⌘T completion, sums and page-text search, Reader, the Developer Mode console and Reload on Save, the crashed-page card, pinned tabs going home, Now Playing, menus showing "&", links losing their tracking tags, certificate warnings (against a local HTTPS server with a self-signed certificate), and spell check. The spell-check test needs a real dictionary: it uses yours once you've turned spell check on, or `PB_TEST_DICTIONARY=/path/to/en-US.bdic`, and is skipped otherwise. Plain `pytest` skips them.

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
| `app.py` | Startup, single-instance hand-off (local socket), and links arriving from macOS. |
| `tools/build_app.py` | Builds `Halo.app`: a native launcher, `Info.plist` (registered for http/https and HTML files) and the icon, and retires an old `PythonBrowser.app`. |
| `brand.py` | The name (Halo) and the icon, drawn in code (the built app and `./run.sh` both use it), plus the internal name that never changes. |
| `webview.py` | Profile setup (one per name: persistent cookies, user agent), `WebPage` and `WebView`. |
| `theme_editor.py` | The Space theme editor. |
| `space_editor.py` | The Space popover (name, icon, colour, profile). |
| `icons.py` | Space icons: line icons, emoji, or a colour dot. |
| `split.py` | Split View panes. |
| `peek.py` | The Peek overlay. |
| `boosts.py` | Boosts (per-site CSS/JS) and the Zap tool. |
| `notes.py` | Note tabs (Markdown editor). |
| `adblock.py` | The request interceptor, block lists, and cosmetic/YouTube scripts. |
| `downloads.py` | The Downloads panel, yt-dlp integration and download settings. |
| `ai.py` | The Claude CLI worker pool. |
| `settings.py` | The Settings window and the Keyboard Shortcuts page. |
| `library.py` | The Library window. |
| `browse.py` | Browse for Me, plus `PageReader` (reads a page's text off-screen without waiting for its ads). |
| `arc_import.py` | Importing from Arc. |
| `ext/host.py` | Extension runtime, Python side: installs scripts into profiles, `PageBridge` (extension pages, web channel) and `WorldBridge` (content scripts and userscripts, isolated worlds), and every `chrome.*`/`GM_*` call. |
| `ext/runtime.js` | Extension runtime, page side: the bridge client, `chrome.*` for an extension, `GM_*` for a userscript. |
| `ext/chrome.py` | Chrome extensions: manifests, content scripts, the `pbext://` scheme, CRX/zip/Web Store/Arc installs. |
| `ext/dnr.py` | Blocking rules (`declarativeNetRequest`): parsing, the domain/keyword index, matching, redirects. No Qt. |
| `ext/netrules.py` | Applies those rules in the request interceptor; each extension's rulesets, dynamic and session rules. |
| `ext/native.py` | Native messaging: finding registered helpers, access checks, running one (`NativePort`). |
| `ext/updates.py` | Daily update checks for Web Store extensions and userscripts. |
| `ext/userscripts.py`, `ext/patterns.py` | Userscript metadata and match-pattern → regex conversion. |
| `ext/ui.py` | Install dialogs, the toolbar popup and menu, Settings → Extensions. |
| `easel.py` | Easels (whiteboard tabs) and the Capture overlay (⇧⌘2). |
| `live_folders.py` | Live Folders: feed parsing and discovery, GitHub via `gh`, refreshing a folder in place. |
| `tidy_downloads.py` | Tidy Downloads: the prompt, PDF/text excerpts, and safe renaming. |
| `mini_player.py` | The floating mini player (moves a tab's live view into a small window, video only). |
| `air_traffic.py` | Air Traffic Control rules (which Space an outside link opens in). |
| `backup.py` | Backup to one .zip; restore by staging it and swapping it in at the next launch. |
| `permissions.py` | Site permissions: the prompt card, remembered camera/microphone answers, the screen picker, Site Controls rules. |
| `notify.py` | Web notifications (banners), unread counts from titles, and the Dock badge. |
| `tab_switcher.py` | The ⌃Tab recent-tabs switcher. |
| `zoom.py` | Per-site zoom levels. |
| `page_actions.py` | Helpers for printing and saving: safe file names, Markdown links. |
| `gestures.py` | Two-finger swipe back/forward (with the page asked whether it scrolls sideways itself) and the sliding Space transition. |
| `devmode.py` | Developer Mode: which pages count, the console drawer, Reload on Save (a polling folder watcher). |
| `now_playing.py` | The sidebar's Now Playing card and the page scripts that find and control what's playing. |
| `reader.py` | Reader view: article extraction (in the page's isolated world) and the script-free reader page. |
| `translate.py` | Translate Page via the Claude CLI: collecting text nodes, batching, writing translations back, Show Original. |
| `crash.py` | Crash recovery: the "did it quit properly" marker, error and native crash logs, the crashed-page card. |
| `self_update.py` | Updating Halo itself from its git upstream (fast-forward only) and relaunching. |
| `private.py` | Private windows and the shared off-the-record profile. |
| `spelling.py` | Spell check: which Chrome dictionary fits your language, downloading it once, turning checking on in every profile. |
| `clean_links.py` | Tracking-tag removal for links that open (via the request interceptor) and links you copy. |
| `cert_errors.py` | The "This connection isn't private" card for pages with bad certificates. |
| `cookies.py` | The cookie-banner rejecter (known consent tools, then a careful label match). |
| `page_index.py` | The local full-text index of pages you've read (SQLite FTS5, written on its own thread). |
| `answers.py` | The command bar's instant answers: safe arithmetic, unit conversion, world time, colours. |
| `motion.py` | Animation helpers (eased values, fades, the sidebar slide); respects Reduce Motion. |
| `integrations.py` | My Apps: addresses, presets, homebase status and control, the local AI client, Send To links. |
| `integrations_ui.py` | The homebase menu and logs, Send To actions, the security-grade card, the My Apps settings page. |
| `live_folder_dialog.py` | The New Live Folder dialog (feed, GitHub, JSON API, page links, calendar, Repo Radar). |
| `upnext.py` | The Up Next card: the next event from Daybook's hop.json, with Join for meeting links. |
| `keychain.py` | Tokens for your apps, kept in the macOS Keychain. |
| `whats_new.py` | The What's New window, shown once after an update with something new (⌘T → What's New). |
| `macos.py` | Native macOS tweaks via the Objective-C runtime (hides the title text, sets the Dock badge). |

### How the pieces talk

- Widgets never mutate data. They call `MainWindow` methods (`activate_tab`, `move_tab`, `close_tab` …).
- `MainWindow` changes the `Store`. The Store emits `structure_changed`, and the sidebar is rebuilt once per event-loop pass.
- Per-tab updates (title, favicon, URL) go out through `Tab.changed`. Each sidebar row listens to its own tab, so nothing is rebuilt.
- Web views are **lazy**. A restored or pinned tab gets no Chromium page until you click it.

### Ideas for next steps

- **Extensions, next steps:** Qt's native extension support still crashes (retested 23 Oct 2026, Qt 6.11.2 and 6.12.0, in `QtWebEngineCore::ExtensionManager`), hence our own runtime. Worth adding to it: response-side `webRequest` events if a future PyQt exposes Qt's response interceptor, and per-frame ids for `webNavigation`/`frameIds`.
- **Video codecs:** PyQt's Qt WebEngine is built without H.264/AAC (patent-encumbered), so MP4-only videos don't play; YouTube and most big sites use VP9/AV1 and work. Fixing it means building Qt WebEngine from source with `-webengine-proprietary-codecs` (hours of compiling).
- **Easels:** resizing items with handles, and sharing an easel as an image or PDF.
- **Live Folders:** more sources (a GitHub repo's releases, Reddit, YouTube channels — the last two already work as feeds).
