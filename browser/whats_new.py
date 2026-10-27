# What's New: a short tour of what's been added, shown once after an update brings something new
# (and any time from ⌘T → "What's New"). Bump EDITION when there's a new section at the top.

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from . import brand
from .motion import FadesIn
from .theme import quiet_scrollbars, stylesheet

EDITION = 9
SETTING = "whats_new_seen"

NOTES = """
## Lately

- **Arc's type**: the sidebar and ⌘T are set in SF Pro Medium, as in Arc.
- **Notes** have checklists (type "[] ", click a box to tick it, or ⇧⌘L) and headings ("# ").
- **A fresh start**: Eclipse opens on your Space's colours, like Arc, not on the last page you had open.
- **In your Applications folder**: Eclipse.app now installs where Finder shows it (it was in your home folder's).
- **Sidebar polish**: tabs line up with the address bar, the list fades out at the bottom instead of cutting a
  row in half, the scrollbar floats over it like macOS's, and a tab you switch to scrolls into view. In a narrow
  sidebar the DEV chip shrinks to a dot so the address (or just its port) still shows.
- **Easels**: text has corner handles too (drag one to change its size), ⌘B / ⌘I / ⌘U style it, and a knob above
  whatever's selected turns it (hold ⇧ for steps of 15°). Right-click for Duplicate, Bring to Front and Send to Back.
- **Nothing open** is just your Space's colours now, like Arc, rather than an empty page.
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
  where you're going; Spaces slide their tabs while the colours blend.

## Safer links

- **Clean links**: tracking tags such as `utm_source`, `fbclid` and `gclid` come off links before they open and when you
  copy them. Off in Settings → Privacy.
- **Certificate warnings**: a site with a bad certificate shows *This connection isn't private*, with Go Back up
  front and *Visit Anyway* tucked behind Details.
- **Right-click selected text** to ask about it, translate it, or look it up in Dictionary.
- **Spell check** in text fields (Settings → General): corrections at the top of the right-click menu.

## Your apps, in the browser

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
- ⌘T now **finds pages by what they said**, does **sums, conversions, time zones and colours**, and
  remembers **search shortcuts** you add (Settings → General).
- **Recently Closed** in the History menu; pinned tabs that wander show "/" and click home.

## How it feels

- The sidebar, command bar and toasts move instead of jumping (off with macOS's Reduce Motion).
- Switches instead of checkboxes, scrollbars that stay out of the way, and Settings has search.
"""


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
        text.setMarkdown(NOTES)
        quiet_scrollbars(text)  # (like every other list here: out of sight until you scroll)
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)


def show_if_new(browser):
    """After an update with something new: once."""
    settings = browser.store.settings
    if settings.get(SETTING, 0) >= EDITION:
        return None
    settings[SETTING] = EDITION
    browser.store.schedule_save()
    if browser.store.is_new:
        return None  # a first launch has the Arc import to deal with; this can wait for ⌘T
    window = WhatsNew(browser)
    window.show()
    return window
