# What's New: a short tour of what's been added, shown once after an update brings something new
# (and any time from ⌘T → "What's New"). Bump EDITION when there's a new section at the top.
# A bullet starting "[mine]", or a section whose heading does, is about the maker's own apps: only the maker's
# copy shows it (see integrations.py).

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from . import brand, native
from .shortcuts import for_here
from .motion import FadesIn
from .theme import quiet_scrollbars, stylesheet

EDITION = 30
SETTING = "whats_new_seen"

NOTES = """
## This week

- **Edit a Live Folder**: right-click one and choose Edit Live Folder… to change what it follows, its name, how
  often it refreshes (every 5 minutes to every 4 hours) and how many items it shows (10 to 100).
- **Pages get their own shortcuts**: a web app's keys work as it means them, as in Chrome: ⌘S saves in Google
  Docs, ⌘K searches in Slack or Notion. Keys like ⌘T, ⌘W, ⌘L and switching tabs are always Eclipse's.
- **Private and Little windows keep their keys**: a menu shortcut there works in that window, not on the main
  window behind it.
- **More from the keyboard and VoiceOver**: ⌥⇧⌘A goes to a card a page put up (a permission, say) or a toast's
  Undo; the mini player has a View › Mini Player menu; theme colors move with the arrow keys or open the color
  panel; Settings lists work with the arrows; and VoiceOver says the cards, toasts and the highlighted row in the
  command bar and ⌃Tab.
- **Better in full screen and on a second display**: the sidebar's buttons stay at the top in full screen; leaving
  a full-screen video puts the window back as it was; Settings and the Library open on the screen you're using; and
  the mini player follows you to every desktop.

## Lately

- **Drag tabs in the sidebar**: rearranging tabs by dragging them works again, and feels like Arc's. The tabs part
  to make room where yours will go, a closed folder opens when you hold a tab over it, and you can drag a tab out
  of Pinned into Today, or past the end of an open folder. Live Folders show a small mark, and keep out tabs that
  aren't from their feed.
- **Arc's live folders, live again**: a GitHub live folder brought over from Arc is a GitHub Live Folder here,
  keeping itself up to date. Ones brought over before are offered the same, once.
- **Right-click menus that stay open**: a page's right-click menu (on a search result, say) no longer closes by
  itself, and right-clicking a link no longer highlights its text.
- **Hard swipes, one Space at a time**: a hard swipe across the sidebar lands on the next Space, smoothly, without
  flying past it or slipping back, and the window switches the moment the sidebar settles.
- **Private windows keep more to themselves**: extensions get nothing from them, a page there can't be watched for
  changes, zoom stays as you set it, and saving or filling a password there leaves nothing behind.
- **Safer passwords and extensions**: passwords and passkeys aren't offered on a page whose security warning you
  clicked past, hidden form fields can't collect your address, and extensions can't reach the files on your Mac
  or see your tabs' addresses without permission.
- **Fixes all over**: sync keeps pinned notes and sketches, and Keep Both keeps both Macs' Favorites; backups
  include your saved addresses; ⌘Q quits with a Little window open; Clear Cookies & Site Data clears sites'
  stored data too; updating a saved password no longer adds a second one; and many smaller fixes.

- **Opens again**: Eclipse 26.1.3.0 and 26.1.3.1 quit as soon as they were opened, so they couldn't update
  themselves either. If yours won't open, download this version from the releases page and drag it over the old
  one: your Spaces, tabs and passwords are all kept.
- **Smoother Space swipes**: swiping between Spaces no longer stutters partway through, and a quick swipe goes one
  Space over, not two. The window takes the new Space's colors once you've stopped swiping, so swiping across
  several in a row stays smooth too, and the Spaces either side have their pages ready, so even the first switch
  after opening Eclipse shows the page at once.
- **A better mini player**: the floating window a video keeps playing in when you leave its tab has rounded
  corners and a soft edge, like Arc's, and player controls along the bottom when you point at it: back or ahead
  10 seconds, play/pause, and the next video where the site has one. Drag an edge to resize it; let go and it
  glides to the nearest corner of the screen, and it remembers both.
- **More at a glance**: rest on GitHub's notifications page for your unread notifications, on Slack for your
  unread channels and mentions, or on YouTube's Watch Later list for what's next, as with Gmail and Calendar.
- **Small windows**: opening the web panel or Ask About This Page in a small window widens it rather than
  squeezing the page, and ⌘T fits a short window, scrolling its results.
- **Native windows**: on a Mac, Settings, the Library and Downloads are drawn by macOS itself. Settings is like
  System Settings: a sidebar with search, real switches and menus, every page there (Classic Settings… at the
  bottom of the sidebar opens the old window). The Library lists your history by day, with search and a right-click
  menu; in Downloads a finished file drags out into any app, opens with a double-click, and shows in Quick Look
  with the space bar. What's New and the update window are native too. The Space editor opens as a native popover
  from the Space's name, and the theme editor, Space Rules, recipes, Boosts, New Live Folder, Download Settings,
  Watch for Changes, Excluded Sites, addresses, passwords and screen sharing open as sheets on the window. Naming or
  deleting things (a new Space, Rename Folder, Delete Space…) asks with macOS's own alerts.
- **Pick a Space's colors on a field of color**: in the theme editor (click the Space's name, then Edit Theme…), each
  color is a dot you drag across hues, from soft to vivid to deep, and the window follows as you go. Double-click
  the field to add a second or third color for a gradient, and double-click a dot to take it out.
- **Native sidebar**: your Spaces' tabs are drawn by macOS itself (on macOS 14 or later). Switching Spaces follows
  your fingers on a swipe and runs at your display's full rate (120 frames a second on a ProMotion Mac, twice what
  it was). Settings → General → Native sidebar turns it off for the classic one.
- **Right-click Now Playing**: the card at the bottom of the sidebar for music in a tab you've left has a menu: go to
  the tab, play or pause, next track, mute, close the tab, or Dismiss to put the card away while it keeps playing.
- **Google sign-in works again**: signing in to Google no longer says "This browser or app may not be secure".
- **Favorites stay themselves**: a favorite whose tab you've taken to another site keeps its own icon (YouTube's stays
  YouTube's after you go to google.com in it), and its tooltip says where it is now.
- **No jump on the first tab**: the first tab you open after starting Eclipse (after an update, say) no longer makes the
  whole window flicker and jump.
- **Readable addresses**: a long address in the sidebar drops whole parts from the front (dash.cloudflare.com shows as
  cloudflare.com) and shrinks a little before anything's cut, and the traffic lights stay gray until you point at them.
- **Polish**: the mini player comes back in its corner on the screen you're on, and leaves with the window when you
  close it; the web panel makes room for Ask About This Page and takes it back after; a site with no icon gets its
  name's letter (W for en.wikipedia.org); tidier Settings menus, Send Feedback box and update notes.
- **Switching to or from a Space with no tab open**: the page you leave fades into the start view, and the start
  view into the page you arrive at, instead of a blank moment between.
- **Swipes start quicker**: the first frame of a swipe between Spaces shows at once.
- **Drop a tab on a Space**: drag a tab (or several) onto a Space's button at the bottom of the sidebar to move it
  there, as in Arc.
- **Collapse the sidebar to icons**: ⌥⌘S (or View → Collapse Sidebar to Icons) shrinks it to a strip of your tabs'
  icons, like Zen's; rest on one for its preview. Rest on the strip itself and the whole sidebar spreads out over
  the page, folding back when you move away (⌘T → Turn Sidebar Expand on Hover On / Off). ⌥⌘S again brings it back
  as it was.
- **Mail and calendar at a glance**: rest on a Gmail tab to see your newest unread mail, or on Google Calendar for
  what's coming up, instead of a picture of the page.
- **Folders in color**: right-click a folder → Color or Icon to tell it apart at a glance.
- **Web panels**: right-click a tab → Open in Web Panel keeps its site (a chat, music, a to-do list) open beside
  whatever tab you're on. Add a few and flip between them with the icons down the panel's side, as in Zen; each
  stays loaded. ⌥⌘B shows or hides it.
- **Smoother all round**: Ask About This Page fades in and out beside the page, the mini player's and Split View's
  buttons fade as you point at them, and the Up Next card no longer makes the sidebar jump.
- **Swiping between Spaces, smoother still**: a swipe goes on to the next Space without needing to come halfway,
  it never stalls partway, fingers down again catch a swipe still settling, and with a page open the page you
  leave fades into the next instead of flashing.
- **⌘T glides**: the highlight moves smoothly from row to row with the arrow keys, without a flicker.
- **Tab previews show memory**: rest on a tab to see how much memory its page is using. Resting on a sleeping tab
  starts waking it, so it's ready by the time you click.
- **More card shapes and colors**: eight new colors and twelve new shapes for your Eclipse card, from a crescent
  to a sunburst.
- **Watch a page for changes**: right-click a tab → *Watch for Changes…*, or select part of a page, right-click
  and choose *Watch This*. Eclipse checks it in the background and tells you when it really changes ("Price
  dropped to $249"), not when it only says it's later. Settings → Watched Pages pauses them or changes how often.
- **Ask about pages you've read**: in ⌘T, ask a question and choose *Ask Claude About Pages You've Read*.
  The answer comes from pages you've visited, with each point linked to its page. Page search results say when
  you read them, and banks, health records and mail are never kept (Settings → Privacy → Excluded Sites…).
- **Instant Links**: type in ⌘T and press ⇧↩ to go straight to the best page for it, instead of a page of results.
- **Back and forward remember the way**: hold down (or right-click) the back or forward button to jump several pages
  at once.
- **Downloads fly home**: a download's icon arcs from where you clicked to the Downloads button.
- **Peek grows out of the link** you clicked, and shrinks back into it when you close it.
- **Link previews show the page's picture**: tap ⇧ on a link for its summary, now with the image the page offers.
- **A slow page shows it's still working**: a highlight sweeps along the loading bar until it's done.
- **Softer motion**: the command bar, Peek, Little windows and cards settle in with a touch of spring.
- **Traffic lights drawn like Arc's**: they stay put as they light up (no more jump), and only light up once
  the pointer's right by them.
- **Live Folders guide**: Settings → Live Folders shows every kind, what it follows and how to set it up, each
  with a button to make one.
- **Just the background**: right-click the page area with no tab open → Show Just the Background, for an empty
  page like Arc's, in that Space (right-click again, or edit the Space, to bring the search and recents back).
- [mine] **Homebase is a Live Folder now**: its button's gone from the sidebar, making room for more Spaces. Add the
  Homebase folder (⌘T → Add Live Folders for My Apps) for your apps and how each is doing; ⌘T → Homebase Apps
  still restarts, stops and shows logs, and a toast still says when one goes down.
- **Smoother Space swipes**: the Spaces either side are made ready ahead, so a swipe moves the moment your
  fingers do, and the colors turn over once you're past halfway. Three Spaces show at a time, a little bigger.
- **Faster with lots of tabs**: switching Spaces no longer slows down as tabs pile up (about three times faster
  with 80 tabs).
- **Memory Saver**: when your Mac runs short of memory, the tabs you've gone longest without using go to sleep,
  a few at a time, so the rest stays quick. They wake where you left them. Settings → Tabs turns it off.
- **Lighter on memory**: older pages kept for swiping back are compressed, and Macs with 8 GB keep fewer pictures.
- **Just the background, per Space**: each Space can show its plain background with no tab open (edit the Space).
- **The theme's Intensity slider** follows your pointer smoothly.
- **More Spaces than fit**: a small arrow beside the Space buttons shows which side the hidden ones are on;
  click it (or swipe) to see them.
- **Fixed**: closing a tab you'd just opened from the hidden sidebar could leave the empty page with no
  sidebar at all.
- **On Windows**: eclipse-browser:// links work there too (from the Run box, a shortcut or AutoHotkey), and
  Eclipse saves energy while Battery saver's on, as it does in a Mac's Low Power Mode.
- **Privacy Report**: see which trackers each site tried to load and the companies behind them, over the last
  30 days. It's in the shield's menu in the address pill, and the whole report is in Settings → Privacy or ⌘T.
- **Space Rules**: right-click a Space → Space Rules… to bring it forward on a schedule (Work on weekdays, 9 to 5)
  or when a display's connected, and to block distracting sites while you're in it.
- **Tab Recipes**: open the tabs for a routine, then ⌘T → Save Tabs as Recipe… Run it again with one click from
  ⌘T, side by side if you like, finishing with a Browse for Me question if you want.
- **AppleScript and Shortcuts**: other apps can list your tabs and Spaces, open pages, switch Space and close
  tabs (`tell application "Eclipse" to list tabs`). Script Editor shows them all (File → Open Dictionary).
  Shortcuts can use them through "Run AppleScript", or open links like
  eclipse-browser://open?url=https://example.com&space=Work with "Open URLs".
- **Spotlight**: your Spaces, pinned tabs and Favorites show up in ⌘Space, and picking one takes you there
  (Settings → General turns it off).
- **Low Power Mode**: while your Mac saves its battery, Eclipse does too: background tabs pause, idle ones sleep
  sooner, and things appear instead of moving (Settings → General turns it off).
- **The traffic lights** no longer twitch as they light up when the pointer comes near.
- **Signing in to Google works**: no more "This browser or app may not be secure".
- **Folders open and close smoothly**, without the hitch at the start or the jiggle at the end.
- **Switching Spaces slides** with the sidebar hidden or no tab open, too (it used to jump).
- **A narrow sidebar fits**: Space buttons shrink instead of piling on top of each other (swipe over them to see
  the rest when they don't all fit), and the search pill says "Search…" when there's no room for more.
- **More from Arc**: importing now brings your Arc settings (how long before tabs archive, links from other apps),
  your archived tabs, Air Traffic Control rules and profiles too. If you'd opened Eclipse before and never saw
  the welcome, it asks about Arc once.
- **Fixes from testing**: clicking a tab quickly while switching could leave the page (and folders) not taking
  clicks until Eclipse restarted; folders now open and close in one smooth motion, at any size; switching to a
  Space for the first time no longer flashes white on a dark Space; and fewer YouTube ads get through (their
  ads are taken out before the player plans them, and feed and banner ads are hidden).
- **Sync**: turn it on in Settings → General, and your Spaces, pinned tabs and Favorites stay the same on each Mac
  (and PC) you use, through iCloud Drive. Today's tabs stay on each computer.
- **Handoff**: the page you're on shows on your iPhone and iPad, to carry on reading there (Settings → General
  turns it off). And with Eclipse as your default browser, a page from Safari on your phone continues here.
- **Share a Space**: right-click a Space → Share Space… and AirDrop, message or mail it. Whoever opens it in
  Eclipse gets the Space, with its look and pinned tabs, after saying yes.
- **Closing the window keeps Eclipse running**, like other Mac apps: click it in the Dock and your window comes
  back as you left it (Settings → General turns this off). ⌘Q quits.
- **Downloads in the Dock**: the Dock icon counts the downloads still coming (↓2), and bounces once when one finishes
  while you're elsewhere. In Downloads, click a finished file and press space for Quick Look, or right-click it.
- **Recently Visited** pages at the bottom of the History menu.
- **Your own keyboard shortcuts**: in Settings → Keyboard Shortcuts, click a shortcut and type a new one, or
  search for any menu item to give it one. Restore Defaults puts them all back.
- **Safer browsing**: pages can't open pop-ups by themselves, full screen always says so (Esc leaves), long
  addresses keep the site's name in view, apps and scripts ask before they download, and addresses are only
  filled into fields you can see. Restoring someone else's backup asks before adding their extensions.
- **A Window menu**: Minimize (⌘M), Zoom, Full Screen, and a list of your windows. Right-click Eclipse in the
  Dock for a new tab or window, or to switch Space.
- **A start view**: with no tab open, the sites you visit most and the tabs you closed last, on your Space's
  colors (Settings → Tabs → Start view turns it off).
- **Videos wait for a click** (Settings → Privacy): nothing plays by itself.
- **Pause downloads**: a button beside Cancel in Downloads pauses and resumes.
- **Tidier Settings**: Downloads, Profiles, Air Traffic Control, Extensions and My Apps are laid out like the
  rest, and the Saved passwords buttons have a line of their own.
- **Updates** come in a window of their own, with what's new, a progress bar, and Skip This Version. Eclipse
  looks every few hours.
- **More room** in the sidebar's top row, and the traffic lights stay put when you point at them.
- **The hidden sidebar** comes out by itself when no tab is open, and hiding the sidebar no longer leaves a box
  behind for a moment.
- **Bring your things over**: File → Import from Another Browser… brings bookmarks, history and passwords from
  Chrome, Brave, Edge or Safari (and Arc, as before). New people see it when they first open Eclipse.
- **Send Feedback** (Help menu): tell Eclipse's maker what went wrong, with the details that help fix it. After a
  crash, the note when Eclipse opens again has Report It.
- **Breached passwords**: Settings → Passwords & Passkeys → Check looks your passwords up in known data breaches
  without ever sending them, and flags the ones worth changing.
- **Addresses** (Settings → Addresses): save your name, email, phone and address once, and a card offers to
  fill them into checkouts and sign-up forms.
- **Opens a little quicker**.
- **Verification codes**: Eclipse can keep a login's two-factor setup key and fill in the 6-digit code after
  Touch ID. Exported from Apple's Passwords, they come along when you import.
- **Import from Chrome, Brave or Edge** (Settings → Passwords & Passkeys → Import), and **Export** your passwords
  to a file Apple's Passwords and Chrome can import.
- **Updates in Settings**: General shows the version you're on, what's new when there's an update, and the
  download as it comes.
- **More Live Folders**: paste a Bluesky, Mastodon or Medium account, a subreddit, a YouTube channel, a GitLab
  project, a PyPI package or a Stack Overflow tag, and it follows it.
- **Runs on older Macs**: the downloadable Eclipse now works on macOS 13 and later.
- **Eclipse all the way down**: the app's internal name is Eclipse now too. Your data moves over by itself; set
  Eclipse as your default browser again, and sites will ask again for the camera and microphone.
- **Updates for the app you download**: the signed Eclipse checks for a new release once a day and offers it.
  Before anything changes, macOS checks the download is signed by Eclipse's maker and notarized by Apple.
- **Sign-in boxes inside a page** (in a frame) get your saved passwords too, when they're the same site as the
  page. A frame from anywhere else, like an ad, never does.
- **A smaller download**: the parts of Qt Eclipse never uses are left out.
- **Passwords**: Eclipse saves and fills your passwords (after Touch ID), and imports them from Arc or from the
  file Apple's Passwords app exports. They're encrypted, with the key in the Keychain where only Eclipse can read
  it, so a password stealer can't just copy them. Signing up, it offers a strong password; the list in
  Settings → Passwords & Passkeys points out reused and easy-to-guess ones.
- **Passkeys**: when a site offers to make one, Eclipse can save it, and you sign in with Touch ID or your Mac's
  password, no password to type. Set a recovery PIN in Settings → Passkeys and they go into your backups, locked
  with it, so you can get them back on another Mac. Security keys (a YubiKey, say) work too.
- **Dropdowns match the menus**: in Settings, the Space editor and everywhere else, a dropdown's list opens
  just under its box, as wide as it, rounded and in your Space's colors.
- **Verified builds**: Settings → General says whether this copy of Eclipse is a signed release, unchanged
  since, or built from source. macOS does the checking.
- **Your cards** fit Settings at its usual size, and a new Eclipse card starts in its own colors. Your Arc card
  is copied out of Arc as soon as Eclipse starts, so it's safe when Arc's data goes.
- **Your cards** (Settings, the first page): a fresh Eclipse membership card, and your Arc card kept as it was.
- **Ask About This Page** (⇧⌘A): a conversation about the page in a panel beside it, with follow-ups. Drag its
  edge to resize it, or pop it out into a small window that can stay on top.
- **Livelier hover**: buttons and tiles everywhere light up under the pointer, tinted with your Space's color.
- **Arc's type**: the sidebar and ⌘T are set in SF Pro Medium, as in Arc.
- **Notes** have checklists (type "[] ", click a box to tick it, or ⇧⌘L) and headings ("# ").
- **A fresh start**: Eclipse opens on your Space's colors, like Arc, not on the last page you had open.
- **In your Applications folder**: Eclipse.app now installs where Finder shows it (it was in your home folder's).
- **Sidebar polish**: tabs line up with the address bar, the list fades out at the bottom instead of cutting a
  row in half, the scrollbar floats over it like macOS's, and a tab you switch to scrolls into view. In a narrow
  sidebar the DEV chip shrinks to a dot so the address (or just its port) still shows.
- **Easels**: text has corner handles too (drag one to change its size), ⌘B / ⌘I / ⌘U style it, and a knob above
  whatever's selected turns it (hold ⇧ for steps of 15°). Right-click for Duplicate, Bring to Front and Send to Back.
- **Nothing open** is just your Space's colors now, like Arc, rather than an empty page.
- **Links that open apps** (Zoom, VS Code, Mail…) ask first, like Chrome's "Open zoom.us?".
- **Security**: downloads get macOS's quarantine check, a downloaded page can't read your files, permission
  cards can't be swapped under your click, Zap only hears itself, and extensions can tell a page from its iframes.
- **Traffic lights** go with the sidebar, like Arc: hidden with it, out with it, dim until you point at them.
- **Site icons** for pinned tabs and Favorites before you've opened them.
- Fixes: Favorites fill their row, ⌘T works straight after launch, the lock lines up with the address,
  and the sidebar's scrollbar no longer shows when you aren't scrolling. Light Spaces stay readable
  (highlighted rows, menus, buttons and letter icons), Easels have no scrollbars, and Settings lost some gaps.
- **Easels**: drag a corner to resize what's selected, and copy or save the whole easel as an image or PDF.
- **GitHub folder** (⌘T → "Add GitHub Folder"): reviews asked of you, your PRs, your issues, notifications, and a
  sub-folder for each of your repos with open issues or pull requests, kept up to date.
- **Pull request status** in the address bar on a PR: its checks (passing, failing, running) and review. Click for each check.
- **Your GitHub repos in ⌘T**: type part of a repo's name to open it.
- **Follow a GitHub repo's releases**: ⌘T on any page of the repo, or give a New Live Folder its address.
- **Undo after closing several tabs** puts them back inside their folders.
- **Tab previews**: rest the pointer on a tab to see its page.
- **Sidebar on the right** (Settings → General), like Arc's Sidebar Position.
- **Keep Tab Awake**, **Mute Other Tabs** and a folder's **Reload Tabs** in the right-click menus.
- **Several tabs at once**: ⌘-click or ⇧-click tabs, then drag them anywhere together, or right-click to split, pin, file, move or close them.
- **Screenshots**: ⌥⇧⌘4 copies what's on screen; ⌥⇧⌘3 saves the whole page, top to bottom.
- **Share…** (File menu, or right-click a tab): AirDrop a page to your phone, or send it to Messages or Mail.
- **Copy Links** of a folder or Today's tabs as a Markdown list (right-click the folder, or the archive button).
- **Dark websites** (Settings → General): pages that only come in light are darkened. Leave a site light
  from the shield in the address bar.
- **Tabs keep their place**: a tab that sleeps, or comes back after a restart, reopens where you'd scrolled to.
- **Undo Clear**: the message after Clear has an Undo button for a few seconds.
- **Drag tabs** and the rows part to show where they'll land; tabs slide in and out as they open and close.
- **App icon** (Settings → General): twelve looks of the eclipse.
- Drag finished downloads straight into other apps; the hidden sidebar slides out more reliably.

## Meet Eclipse

The browser has a proper name and icon now. Your Spaces, tabs, sign-ins and settings are all where
they were. Rebuild the app (`./run.sh --build-app`) for the new icon in the Dock and Spotlight.

## Swipes that follow your fingers

- Swipe on a page or the sidebar and it moves with you, as far as you like and back. Let go past
  the middle (or flick) to go, anywhere short of it to spring back. Pages slide over a picture of
  where you're going; Spaces slide their tabs while the colors blend.

## Safer links

- **Clean links**: tracking tags such as `utm_source`, `fbclid` and `gclid` come off links before they open and when you
  copy them. Off in Settings → Privacy.
- **Certificate warnings**: a site with a bad certificate shows *This connection isn't private*, with Go Back up
  front and *Visit Anyway* tucked behind Details.
- **Right-click selected text** to ask about it, translate it, or look it up in Dictionary.
- **Spell check** in text fields (Settings → General): corrections at the top of the right-click menu.

## [mine] Your apps, in the browser

- **Homebase** in the sidebar's bottom bar: a dot says whether your desktop's apps are up. Its menu opens,
  restarts, stops and shows logs for each, lists how am-parliament.org and masonkimball.dev are doing, and
  a toast tells you when something goes down.
- **Live Folders for your apps** (homebase menu → *Add Live Folders for My Apps…*): Parliament notifications
  and chapter events, Job Radar and follow-ups, gradtrack deadlines, the Away queue, recently pushed repos,
  and **Repo Radar** (repos with uncommitted or unpushed work).
- **Send To** (⌘T, or right-click a page): Job Tracker, Daybook, a Sentinel security grade, Open in Loom,
  and **Give Issue to the Away Queue** on GitHub issues. A *Save Job* chip appears on job postings.
- **Ask Qwen**: questions in ⌘T can go to the model on homebase as well as Claude (Settings → My Apps).
- **Up Next** from Daybook at the bottom of the sidebar, with **Join** for meeting links.
- ⌘T knows your apps: "restart job tracker", "logs shelf". Search Parliament with `parl budget`.

## Browsing

- **Private windows** (⇧⌘P): nothing saved, everything forgotten when you close the last one.
- **Reader view** (⌥⌘R) and **Translate Page** (⌘T), **Developer Mode** for localhost (⌥⌘J),
  **Now Playing** in the sidebar, two-finger **swipes** for back and forward.
- **Cookie banners** are answered "Reject" for you. **Copied links** are the first thing ⌘T offers.
- ⌘T now **finds pages by what they said**, does **sums, conversions, time zones and colors**, and
  remembers **search shortcuts** you add (Settings → General).
- **Recently Closed** in the History menu; pinned tabs that wander show "/" and click home.

## How it feels

- The sidebar, command bar and toasts move instead of jumping (off with macOS's Reduce Motion).
- Switches instead of checkboxes, scrollbars that stay out of the way, and Settings has search.
"""


MINE = "[mine] "


def notes(maker: bool) -> str:
    """NOTES as this copy shows them: the maker's own parts only on the maker's copy."""
    out, section_hidden, bullet_hidden = [], False, False
    for line in NOTES.splitlines():
        if line.startswith("## "):
            section_hidden, bullet_hidden = line.startswith("## " + MINE) and not maker, False
        elif line.startswith("- "):
            bullet_hidden = line.startswith("- " + MINE) and not maker
        elif not line.startswith("  "):  # (a bullet's own lines carry on indented)
            bullet_hidden = False
        if not (section_hidden or bullet_hidden):
            out.append(line.replace(MINE, ""))
    return "\n".join(out)


class WhatsNew(FadesIn, QWidget):
    def __init__(self, browser):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"What's New in {brand.NAME}")
        self.setProperty("panel", True)
        self.setStyleSheet(stylesheet(browser.theme))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(620, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 14)
        text = QTextBrowser()
        text.setObjectName("Answer")
        text.setOpenLinks(False)
        from .member_card import is_maker
        text.setMarkdown(for_here(notes(is_maker())))  # (the maker's own parts only on theirs; Windows' keys, off a Mac)
        quiet_scrollbars(text)  # (like every other list here: out of sight until you scroll)
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)


class NativeWhatsNew(QObject):
    """The same page drawn natively (SwiftUI, native/Sources/Pages.swift), where it's available."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.page = None

    def show(self):
        from .member_card import is_maker
        self.page = native.NativePage({
            "page": "whats_new", "title": f"What's New in {brand.NAME}", "heading": f"What's New in {brand.NAME}",
            "sections": native.note_sections(for_here(notes(is_maker()))), "width": 620, "height": 640,
            **native.theme_state(self.browser.theme)}, self)
        self.page.action.connect(lambda name, _: self.close() if name == "close" else None)

    def raise_(self):
        if self.page is not None:
            self.page.front()

    def close(self):
        if self.page is not None:
            self.page.close()

    def isVisible(self) -> bool:
        return self.page is not None and self.page.is_open()


def window_for(browser):
    """What's New, natively where that's built, else Qt's window."""
    return NativeWhatsNew(browser) if native.available() else WhatsNew(browser)


def show_if_new(browser):
    """After an update with something new: once."""
    settings = browser.store.settings
    if settings.get(SETTING, 0) >= EDITION:
        return None
    settings[SETTING] = EDITION
    browser.store.schedule_save()
    if browser.store.is_new:
        return None  # a first launch has the Arc import to deal with; this can wait for ⌘T
    window = window_for(browser)
    window.show()
    return window
