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

EDITION = 31
SETTING = "whats_new_seen"

NOTES = """
## This week

- **Site Settings**: click the shield in the address pill (or ⌘T → Site Settings, or right-click a tab) for
  everything about the site you're on in one place: what it may use (camera, microphone, location,
  notifications), its ads, zoom and dark page, the apps it may open without asking, and Clear Cookies & Site Data
  to sign out of just that site.
- **Highlights**: select text, right-click → Highlight, and it's marked there each time you come back. Each site's
  highlights are kept in a note of their own ("Highlights from example.com"), linked to their pages, and ⌘T finds
  them by what they say. Right-click highlighted text to remove it.

## Lately

- **Edit a Live Folder**: right-click one and choose Edit Live Folder… to change what it follows, its name, how
  often it refreshes (every 5 minutes to every 4 hours) and how many items it shows (10 to 100).
- **Pages get their own shortcuts**: a web app's keys work as it means them, as in Chrome: ⌘S saves in Google
  Docs, ⌘K searches in Slack or Notion. Keys like ⌘T, ⌘W, ⌘L and switching tabs are always Eclipse's.
- **More from the keyboard and VoiceOver**: ⌥⇧⌘A goes to a card a page put up (a permission, say) or a toast's
  Undo; the mini player has a View › Mini Player menu; Settings lists and theme colors work with the arrow keys;
  and VoiceOver reads out cards, toasts and the highlighted row in ⌘T and ⌃Tab. Private and Little windows keep
  their own menu shortcuts.
- **Better in full screen and on a second display**: the sidebar's buttons stay at the top in full screen; leaving
  a full-screen video puts the window back as it was; Settings and the Library open on the screen you're using; and
  the mini player follows you to every desktop.
- **Drag tabs in the sidebar**: the tabs part to make room where yours will go, a closed folder opens when you hold
  a tab over it, and you can drag out of Pinned into Today or past the end of an open folder.
- **Arc's live folders, live again**: a GitHub live folder brought over from Arc keeps itself up to date here.
- **Hard swipes, one Space at a time**: a hard swipe across the sidebar lands on the next Space, without flying
  past it or slipping back.
- **Private windows keep more to themselves**: extensions get nothing from them, zoom stays as you set it, and
  saving or filling a password there leaves nothing behind.
- **Safer passwords and extensions**: passwords and passkeys aren't offered on a page whose security warning you
  clicked past, hidden form fields can't collect your address, and extensions can't reach your files or see your
  tabs' addresses without permission.
- **Fixes all over**: sync keeps pinned notes and sketches; ⇧⌘T puts a tab back in its folder; a backup that
  fails never replaces the one before; a note or your tabs that can't be saved (a full disk) say so instead of
  losing your changes; and many smaller fixes.

## Spaces and the sidebar

- **Swipe between Spaces** on the sidebar: it follows your fingers at your display's full rate, the Spaces either
  side are ready before you get there, and the window takes the new Space's colors as you land. Two fingers on a
  page go back and forward.
- **A native sidebar**: your tabs are drawn by macOS itself (on macOS 14 or later). Settings → General → Native
  sidebar turns it off for the classic one.
- **Collapse the sidebar to icons** (⌥⌘S), like Zen's: rest on the strip and the whole sidebar spreads out over the
  page. The sidebar can go on the right too (Settings → General).
- **Pick a Space's colors on a field of color**: click the Space's name, then Edit Theme…, and drag each color's dot
  across hues; double-click the field for a gradient of two or three.
- **Drop a tab on a Space's button** to move it there, as in Arc. With more Spaces than fit, an arrow shows which
  side the hidden ones are on.
- **Space Rules**: right-click a Space → Space Rules… to bring it forward on a schedule or when a display's
  connected, and to block distracting sites while you're in it.
- **Folders in color**: right-click a folder → Color or Icon.
- **A start view** with no tab open: the sites you visit most and the tabs you closed last, on your Space's colors,
  or just the background (right-click the page area, per Space).
- **Now Playing** at the bottom of the sidebar for music in a tab you've left: right-click for play, next, mute or
  Dismiss.

## Tabs

- **Tab previews**: rest on a tab to see its page and how much memory it's using; a sleeping one starts waking.
- **Several tabs at once**: ⌘-click or ⇧-click tabs, then drag them together, or right-click to split, pin, file,
  move or close them. Undo after closing several puts them back inside their folders, and Clear has Undo too.
- **Web panels**: right-click a tab → Open in Web Panel keeps its site open beside whatever you're on, as in Zen
  (⌥⌘B shows or hides it).
- **The mini player**: a video keeps playing in a rounded floating window when you leave its tab, with controls
  when you point at it. Drag an edge to resize it; it glides to the nearest corner.
- **Peek** grows out of the link you clicked, and **Tab Recipes** reopen a routine's tabs from ⌘T (Save Tabs as
  Recipe…).
- **Memory Saver**: when your Mac runs short of memory, the tabs you've gone longest without using sleep, and wake
  where you left them. In Low Power Mode, background tabs pause too.
- **Keep Tab Awake**, **Mute Other Tabs** and a folder's **Reload Tabs** in the right-click menus; hold the back or
  forward button to jump several pages at once.

## ⌘T

- **Instant Links**: type and press ⇧↩ to go straight to the best page for it.
- **Ask about pages you've read**: ask a question and choose Ask Claude About Pages You've Read, for an answer
  linked to the pages it came from. Banks, health records and mail are never kept (Settings → Privacy → Excluded
  Sites…).
- **At a glance**: rest on Gmail for your newest unread mail, Google Calendar for what's next, GitHub
  notifications, Slack's unread channels or YouTube's Watch Later.

## Pages, notes and easels

- **Ask About This Page** (⇧⌘A): a conversation about the page beside it, or popped out into a small window.
- **Watch a page for changes**: right-click a tab → Watch for Changes…, or select part of a page and choose Watch
  This, and Eclipse tells you when it really changes ("Price dropped to $249").
- **Link previews**: tap ⇧ on a link for its summary and picture.
- **Notes** with checklists and headings, and **Easels** to sketch, resize, rotate and export as an image or PDF.
- **Screenshots** (⌥⇧⌘4, or ⌥⇧⌘3 for the whole page), **Share…** to AirDrop, Messages or Mail, and **Dark
  websites** for pages that only come in light.
- **Downloads fly home** to the Downloads button, pause and resume, count down in the Dock, and drag straight into
  other apps.

## Passwords and privacy

- **Passwords** saved and filled after Touch ID, encrypted with a key only Eclipse can read. Signing up, it offers a
  strong password, and Check looks yours up in known breaches without ever sending them.
- **Passkeys**, security keys and **verification codes**: sign in with Touch ID; a recovery PIN puts passkeys in your
  backups.
- **Addresses** (Settings → Addresses): saved once, offered on checkouts and sign-up forms.
- **Privacy Report**: which trackers each site tried to load over the last 30 days, from the shield in the address
  pill.
- **Safer browsing**: pages can't open pop-ups by themselves, full screen always says so, links that open apps ask
  first, downloads get macOS's quarantine check, and videos can wait for a click (Settings → Privacy).

## A Mac app all the way

- **Native windows**: Settings (like System Settings, with search), the Library, Downloads, What's New and the
  update window are drawn by macOS, and the editors open as sheets and popovers on the window.
- **Spotlight** finds your Spaces, pinned tabs and Favorites; **AppleScript and Shortcuts** can list tabs, open pages
  and switch Space (`tell application "Eclipse" to list tabs`).
- **Closing the window keeps Eclipse running**, like other Mac apps; a Window menu, and the Dock's menu for a new tab
  or switching Space.
- **Your own keyboard shortcuts** in Settings → Keyboard Shortcuts.
- **Updates** check every few hours and come in a window of their own; macOS checks each is signed by Eclipse's maker
  and notarized by Apple. It runs on macOS 13 and later.
- **App icon** (Settings → General): twelve looks of the eclipse.

## Your things, everywhere

- **Sync** through iCloud Drive (Settings → General): Spaces, pinned tabs and Favorites stay the same on each Mac and
  PC you use.
- **Handoff**: the page you're on shows on your iPhone and iPad to carry on there, and the other way round.
- **Share a Space**: right-click a Space → Share Space… to AirDrop, message or mail it.
- **Bring your things over** from Arc (Spaces, settings, archive, Air Traffic rules, your Arc card) or from Chrome,
  Brave, Edge or Safari (File → Import from Another Browser…). Backups (File menu) hold everything you've set up.
- **Live Folders** follow a feed, a GitHub repo's releases, a subreddit, a YouTube channel, a Bluesky or Mastodon
  account and more; Settings → Live Folders shows every kind. The **GitHub folder** (⌘T → Add GitHub Folder) keeps
  your reviews, PRs, issues and notifications, and a PR's checks show in the address bar.
- **On Windows** too: eclipse-browser:// links and Battery saver.
- [mine] **Homebase is a Live Folder now**: add the Homebase folder (⌘T → Add Live Folders for My Apps) for your
  apps and how each is doing; ⌘T → Homebase Apps restarts, stops and shows logs.

## [mine] Your apps, in the browser

- **Live Folders for your apps** (⌘T → *Add Live Folders for My Apps…*): Parliament notifications and chapter
  events, Job Radar and follow-ups, gradtrack deadlines, the Away queue, recently pushed repos, and **Repo Radar**
  (repos with uncommitted or unpushed work).
- **Send To** (⌘T, or right-click a page): Job Tracker, Daybook, a Sentinel security grade, Open in Loom, and **Give
  Issue to the Away Queue** on GitHub issues. A *Save Job* chip appears on job postings.
- **Ask Qwen**: questions in ⌘T can go to the model on homebase as well as Claude (Settings → My Apps).
- **Up Next** from Daybook at the bottom of the sidebar, with **Join** for meeting links.
- ⌘T knows your apps: "restart job tracker", "logs shelf". Search Parliament with `parl budget`.

## Browsing

- **Private windows** (⇧⌘P): nothing saved, everything forgotten when you close the last one.
- **Reader view** (⌥⌘R) and **Translate Page** (⌘T), **Developer Mode** for localhost (⌥⌘J).
- **Clean links**: tracking tags such as `utm_source`, `fbclid` and `gclid` come off links before they open and
  when you copy them. **Cookie banners** are answered "Reject" for you.
- **Certificate warnings** say *This connection isn't private*, with Go Back up front.
- **Right-click selected text** to ask about it, translate it, or look it up in Dictionary; **Spell check** in text
  fields puts corrections at the top of the right-click menu.
- ⌘T **finds pages by what they said**, does **sums, conversions, time zones and colors**, and remembers **search
  shortcuts** you add. **Recently Closed** and **Recently Visited** are in the History menu.

## How it feels

- The sidebar, command bar, Peek and toasts move instead of jumping, with a touch of spring (off with macOS's
  Reduce Motion), in SF Pro, as in Arc.
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
