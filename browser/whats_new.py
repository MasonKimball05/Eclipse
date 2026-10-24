# What's New: a short tour of what's been added, shown once after an update brings something new
# (and any time from ⌘T → "What's New"). Bump EDITION when there's a new section at the top.

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from . import brand
from .theme import stylesheet

EDITION = 3
SETTING = "whats_new_seen"

NOTES = """
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


class WhatsNew(QWidget):
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
