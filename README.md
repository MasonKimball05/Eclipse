# Eclipse

An Arc-style web browser for the Mac: tabs down the side, Spaces for different parts of your life, a command bar
instead of a new-tab page, and tabs that tidy themselves away. Arc is winding down, so Eclipse carries its ideas
forward. It's a personal project, open source, and free.

**[Download Eclipse for Mac](https://github.com/MasonKimball05/Eclipse/releases/latest/download/Eclipse.dmg)** · Macs with Apple silicon, macOS 13 or later

Eclipse renders pages with Chromium (through Qt WebEngine), so sites work as they do in Chrome. It's signed with a
Developer ID and notarized by Apple, and **Settings → General** shows whether your copy is verified: exactly as
it was signed.

## Getting started

1. Download **Eclipse.dmg** from the [latest release](https://github.com/MasonKimball05/Eclipse/releases/latest),
   open it, and drag **Eclipse** onto the **Applications** folder beside it.
2. Open it. The welcome offers to bring over your bookmarks, history and passwords from Chrome, Brave, Edge,
   Safari or Arc, and to make Eclipse your default browser.
3. Press **⌘T** for anything: a site, a search, an open tab, something from your history, or a command.

Eclipse updates itself: once a day it checks for a new release and offers it, after macOS has confirmed the
download is signed by Eclipse's maker and notarized.

## What's in it

- **The sidebar:** Favorites at the top, pinned tabs that remember their home page, and Today's tabs, which are
  archived after 12 hours unused (you can bring them back).
- **Spaces:** separate sets of tabs, each with its own name, colour and icon, and its own sign-ins if you like.
  Swipe with two fingers to switch.
- **Command bar (⌘T):** sites, searches, tabs, history, sums and conversions, and every command.
- **Little windows:** links from other apps open in a small window you can glance at, or keep with ⌘O.
- **Split View, Peek, Reader view, Notes and Easels** (a whiteboard you can drop pages onto).
- **Built-in ad and tracker blocking**, cookie banners answered "Reject", and tracking tags taken off links.
- **Passwords and passkeys**, kept by Eclipse (more below), with two-factor codes and a breached-password check.
- **Address AutoFill:** your name, email, phone and address, filled into checkouts from a card.
- **Extensions and userscripts:** a lightweight runtime that runs many Chrome extensions.
- **Live Folders:** a folder that fills itself from a feed, a GitHub repo, a subreddit, a YouTube channel and more.

## Your data

Everything is kept on your Mac, in `~/Library/Application Support/MasonKimball05/Eclipse`. There are no accounts,
no sync servers and no analytics. Besides the sites you visit, Eclipse only goes online to check for updates and
to refresh its ad-blocking lists (and, if you use them, for the Claude features, through the `claude` tool you've
signed in to yourself).

- **Passwords** are encrypted (AES-256-GCM) with a key kept in the macOS Keychain, which only Eclipse can read
  without macOS asking you first. Filling one asks for Touch ID or your Mac's password.
- **Passkeys** are kept the same way, and signing in with one asks for Touch ID.
- **A recovery PIN** (Settings → Passwords & Passkeys) puts a locked copy of both into your backups, so you can get
  them back on another Mac.
- **The breached-password check** runs only when you click it, and sends only the first 5 characters of each
  password's SHA-1 hash to [Have I Been Pwned](https://haveibeenpwned.com/Passwords), never a password.
- **Feedback and crash reports** are only sent when you send them (Help → Send Feedback), and you see everything
  in them first.

## Feedback

Something broken, or something you'd like? Use **Help → Send Feedback…** in Eclipse, or
[open an issue](https://github.com/MasonKimball05/Eclipse/issues/new).

## Building it yourself

You need a Mac with Apple silicon and Python 3.14 (from [python.org](https://www.python.org/downloads/macos/) or
Homebrew).

```bash
git clone https://github.com/MasonKimball05/Eclipse.git
cd Eclipse
./run.sh               # sets up a virtualenv the first time, then opens Eclipse
./run.sh --build-app   # an Eclipse.app in /Applications that runs this checkout
./run.sh --tests       # the unit tests
```

A copy built from source works fully; Settings just says "Built from source" instead of "Verified". How it all
fits together is in [claude_docs.md](claude_docs.md).

## On Windows (early)

Eclipse runs on Windows 10 and 11 from source, with the basics working: tabs, Spaces, the command bar, ad blocking,
passwords and passkeys (their keys kept in Windows Credential Manager), Notes, Easels and the rest. There's no
installer yet, and the Mac-only touches aren't there: Touch ID before filling a password, importing from other
browsers, and updating itself.

1. Install [Python 3.14](https://www.python.org/downloads/windows/) (tick **Add python.exe to PATH**).
2. Download this repository (Code → Download ZIP, or `git clone`), and double-click **run.bat** in it. The first
   time it sets itself up, which takes a few minutes.

Every push is checked on a Windows machine on GitHub (the **Windows** workflow): Eclipse starts, opens its window,
loads a page and keeps a secret in Credential Manager.

## License

[MIT](LICENSE)
