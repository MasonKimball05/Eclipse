# Features: the parts of Eclipse that plug into its core (the window, sidebar, pages and command bar)
# rather than being built into it: Claude, Live Folders, extensions, Easels and Notes, Boosts, and the rest.
#
# The core never imports a feature by name. At start-up the window asks this module to load the features;
# each one hooks in where it needs to (⌘T commands, menus, Settings pages, a tab's menu, what happens as tabs
# are shown and closed…), and anything that isn't there simply doesn't appear. So the core runs on its own
# as a plain browser, and a feature can be added or taken out without touching it. ECLIPSE_CORE_ONLY=1
# starts the core alone.
#
# A feature module has FEATURE = its Feature subclass. Every hook below is optional: the base class does
# nothing, so a feature only writes the ones it uses.

import importlib
import os
import sys
import traceback

# The feature modules, in the order they're loaded (later ones can find earlier ones)
FEATURE_MODULES = [
    "self_update", "arc_import", "welcome", "addresses", "member_card", "notes", "easel", "gestures", "now_playing", "upnext",
    "integrations_ui", "github_status", "live_folder_dialog", "boosts", "claude_feature", "page_chat",
    "handoff", "sync", "scripting", "spotlight", "power_saver", "memory_saver", "privacy_report", "space_rules", "recipes",
    "page_watch", "download_arc", "ext.ui",
]
CORE_ONLY = "ECLIPSE_CORE_ONLY"


def before_app():
    """Anything the features need done before the QApplication is made: the pbext:// scheme, for extension
    files, has to be known by then."""
    if os.environ.get(CORE_ONLY):
        return
    try:
        from .ext.chrome import register_scheme
    except ImportError:  # (no extensions here)
        return
    register_scheme()


def after_app():
    """The app has quit: compile what changed while it ran (an update, say), so the next launch is quick."""
    if os.environ.get(CORE_ONLY):
        return
    try:
        from .self_update import precompile
    except ImportError:
        return
    precompile()


class Feature:
    """What a feature can add to the core."""

    name = ""

    def __init__(self, window):
        self.window = window

    # --- Its life -------------------------------------------------------------------------------------

    def ready(self):
        """The window's built (sidebar, page area, command bar): make what needs them."""

    def shutdown(self):
        """The window's closing."""

    def theme_changed(self):
        """The Space's colours changed."""

    # --- Pages ----------------------------------------------------------------------------------------

    def profile_ready(self, profile):
        """A browser profile was made (the default one, or a Space's; not a private window's, which keeps
        features such as extensions out)."""

    def view_created(self, view, tab):
        """A web page was made for a tab."""

    def url_changed(self, tab, url):
        """A tab's page went somewhere (tab.url is already the new address)."""

    def load_finished(self, tab, ok):
        """A tab's page finished loading."""

    def console_message(self, tab, message) -> bool:
        """A page said something on the console meant for the browser (from a script of the feature's own, in
        its own isolated world). True: it was for this feature."""
        return False

    def view_for(self, tab):
        """A view of a tab that isn't a web page (a note's editor, an easel), or None."""
        return None

    # --- Tabs -----------------------------------------------------------------------------------------

    def tab_shown(self, tab):
        """A tab came on screen (None: nothing's on screen)."""

    def tab_left(self, tab):
        """You switched away from a tab (it's still open)."""

    def tab_closed(self, tab):
        """A tab's going for good (closed, archived, deleted)."""

    def view_destroyed(self, tab):
        """A tab's page was let go (closed, or put to sleep)."""

    def audible_changed(self, tab):
        """A tab started or stopped playing sound."""

    def tab_menu(self, menu, tab, kind):
        """A tab's right-click menu (kind: where the tab is: Favorites, pinned, Today), for adding to."""

    def folder_deleted(self, folder):
        """A folder went (with its tabs)."""

    def own_folder_menu(self, folder, pos):
        """True if this feature shows a folder's right-click menu itself (one of its own folders)."""
        return None

    def folder_menu(self, menu, folder):
        """A folder's right-click menu, for adding to."""

    def page_menu(self, menu, request, view, tab):
        """A page's right-click menu (request: Qt's context-menu request: what was right-clicked), for adding
        to. tab: the page's tab, if it has one."""

    def selection_menu(self, menu, view, text) -> list:
        """Right-clicking selected text on a page: actions for the top of the menu."""
        return []

    def tab_pinned(self, tab):
        """A Today tab was pinned."""

    def preview_link(self, url):
        """You tapped ⇧ with the pointer on a link: a preview of it, if this feature makes them."""

    def download_started(self, item, download):
        """A download began (accepted, and in the Downloads list)."""

    def download_done(self, item, download):
        """A download finished."""

    # --- What it offers ---------------------------------------------------------------------------------

    def commands(self) -> list:
        """⌘T commands: (title, icon, action)."""
        return []

    def command_bar_opened(self):
        """⌘T just opened (for refreshing something in the background, for next time)."""

    def query_rows(self, bar, query: str, results: list, url) -> None:
        """⌘T rows that go among the search rows themselves (asking Claude): inserted into `results`.
        url: what the query would open as an address, if it's one."""

    def command_results(self, bar, query: str, seen: set) -> list:
        """Rows for ⌘T as you type (command_bar.Result), after its open tabs. seen: the links already shown
        (add yours, so nothing comes twice)."""
        return []

    def menus(self, add, menus, bar) -> None:
        """Items for the menu bar: add(menus["File"], title, shortcut, action). bar: the menu bar itself, for a
        menu of the feature's own."""

    def space_actions(self, space) -> list:
        """Rows for a Space's editor (right-click a Space), beside New Folder and Share Space: (icon, text, action)."""
        return []

    def site_menu(self, menu, tab):
        """The shield's menu in the address pill (Site Controls, ad blocking), for the site on screen: for adding to."""

    def history_cleared(self):
        """You cleared your history: forget what goes with it."""

    def app_link(self, url) -> bool:
        """A link to Eclipse itself (eclipse-browser://…) from another app or Shortcuts. True: it was this feature's."""
        return False

    def put_away(self) -> None:
        """The window was closed while Eclipse keeps running (hidden: it comes back from the Dock)."""

    def shortcuts_changed(self) -> None:
        """The menu items' shortcuts changed (yours, from Settings → Keyboard Shortcuts, are in place)."""

    def pill_buttons(self) -> list:
        """Chips for the address pill (a pull request's checks, Save Job), shown or hidden by update_pill."""
        return []

    def update_pill(self, tab):
        """The tab on screen, or what's on it, changed: show or hide this feature's chips for it."""

    def sidebar_buttons(self) -> list:
        """Buttons for the sidebar's bottom row, beside Archive and Downloads."""
        return []

    def sidebar_cards(self) -> list:
        """Widgets for the bottom of the sidebar, above its buttons (Up Next, Now Playing)."""
        return []

    def settings_pages(self) -> list:
        """Settings pages: (key, icon, title, build(settings_window) -> QWidget, the core page it goes before or
        None for the end)."""
        return []

    def settings_shown(self, settings):
        """Settings opened (or changed page): bring this feature's pages up to date."""

    def before_backup(self):
        """A backup's about to be made: anything this feature keeps in memory goes to disk first."""

    def backup_paths(self) -> list:
        """Folders in the data folder that Back Up Everything should take."""
        return []


def load(window) -> dict[str, Feature]:
    """The features that are here, made for `window` (none with ECLIPSE_CORE_ONLY set)."""
    found = window.features = {}  # (filled as they load, so a feature can find the ones before it)
    if os.environ.get(CORE_ONLY):
        return found
    for module in FEATURE_MODULES:
        try:
            feature = getattr(importlib.import_module(f"{__package__}.{module}"), "FEATURE", None)
        except ImportError:
            continue  # (not here: the core goes on without it)
        except Exception:  # (here, but broken: said so, and the core goes on without it)
            traceback.print_exc(file=sys.stderr)
            continue
        if feature is None:
            continue
        try:
            made = feature(window)
            found[made.name] = made
        except Exception:  # (one feature failing mustn't take the browser with it)
            traceback.print_exc(file=sys.stderr)
    return found
