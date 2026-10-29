# Eclipse from other apps: AppleScript (Script Editor, Shortcuts' "Run AppleScript", Raycast, Alfred, Keyboard
# Maestro), and links to Eclipse itself (eclipse-browser://…) for Shortcuts' "Open URLs" and anything else that
# can open a link.
#
# AppleScript, once the app's been built with its dictionary (Eclipse.sdef, written from COMMANDS below):
#
#     tell application "Eclipse"
#         list tabs                         -- {{id:"…", title:"…", URL:"…", space:"Work", section:"pinned"}, …}
#         list tabs in space "Work"
#         current tab                       -- the tab on screen, or missing value
#         list spaces                       -- {"Personal", "Work"}
#         open tab "https://example.com" in space "Work"
#         focus tab "<id>"                  -- switch to it (and its Space)
#         close tab "<id>"
#         switch to space "Work"
#         run recipe "Morning dashboards"   -- recipes.py
#     end tell
#
# macOS asks you the first time each app wants to control Eclipse. Private windows' tabs are never listed.
#
# Links: eclipse-browser://open?url=https://example.com&space=Work, eclipse-browser://space?name=Work,
# eclipse-browser://search?q=…, eclipse-browser://new-tab. They only open pages and move between Spaces (as a
# click on a link can), and pages can't use them (webview.py refuses them). On Windows (no AppleScript there),
# these links are the way in: Eclipse registers them on start (windows_system.py), for the Run box (Win+R),
# shortcuts, PowerToys or AutoHotkey.

from html import escape
from urllib.parse import parse_qs

from PyQt6.QtCore import QUrl

from . import macos
from .command_bar import search_url
from .external_links import OWN_SCHEME
from .features import Feature
from .models import FAVORITES, Tab, TODAY

SUITE = "Eclp"
TAB_RECORD = {"id": "ID  ", "title": "Ettl", "URL": "pURL", "space": "Espn", "section": "Esec"}
IN_SPACE = "Eisp"
# (AppleScript's words, code, what it does, its direct parameter (or None), whether it has "in space", result)
COMMANDS = [
    ("list tabs", "LsTb", "Your tabs: Favorites, then each Space's pinned and Today tabs.", None, True, "tab list"),
    ("current tab", "CrTb", "The tab on screen (missing value if there's none).", None, False, "tab"),
    ("list spaces", "LsSp", "The names of your Spaces, in order.", None, False, "text list"),
    ("open tab", "OpTb", "Open a web page in a new tab.", "the page's address", True, "tab"),
    ("focus tab", "FcTb", "Bring a tab on screen, switching to its Space.", "the tab's id", False, None),
    ("close tab", "ClTb", "Close a tab (a pinned one goes back to where it's pinned).", "the tab's id", False, None),
    ("switch to space", "SwSp", "Switch to a Space.", "the Space's name", False, None),
    ("run recipe", "RnRc", "Run one of your tab recipes (⌘T → Save Tabs as Recipe…).", "the recipe's name",
     False, None),
]


def sdef() -> str:
    """The scripting dictionary: what Script Editor shows, and what lets a script use these words."""
    def result(kind):
        if kind is None:
            return ""
        many = ' list="yes"' if kind.endswith(" list") else ""
        name = {"tab": "tab info", "text": "text"}[kind.removesuffix(" list")]
        return f'      <result><type type="{name}"{many}/></result>\n'
    commands = []
    for words, code, says, direct, in_space, kind in COMMANDS:
        commands.append(
            f'    <command name="{words}" code="{SUITE}{code}" description="{escape(says)}">\n'
            + (f'      <direct-parameter type="text" description="{escape(direct)}"/>\n' if direct else "")
            + (f'      <parameter name="in space" code="{IN_SPACE}" type="text" optional="yes" '
               f'description="A Space, by name."/>\n' if in_space else "")
            + result(kind) + "    </command>\n")
    properties = "".join(f'      <property name="{name}" code="{code}" type="text"/>\n' for name, code in TAB_RECORD.items())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE dictionary SYSTEM "file://localhost/System/Library/DTDs/sdef.dtd">\n'
            '<dictionary title="Eclipse Terminology">\n'
            f'  <suite name="Eclipse Suite" code="{SUITE}" description="Your tabs and Spaces.">\n'
            f'    <record-type name="tab info" code="Etab" description="A tab: section is favorites, pinned or today.">\n'
            f'{properties}    </record-type>\n'
            + "".join(commands) + "  </suite>\n</dictionary>\n")


class ScriptingFeature(Feature):
    name = "scripting"

    def __init__(self, window):
        super().__init__(window)
        self.handlers = {"LsTb": self.list_tabs, "CrTb": self.current_tab, "LsSp": self.list_spaces,
                         "OpTb": self.open_tab, "FcTb": self.focus_tab, "ClTb": self.close_tab,
                         "SwSp": self.switch_space, "RnRc": self.run_recipe}
        macos.listen_for_commands(SUITE, list(self.handlers), self.answer)

    def answer(self, command, param):
        return self.handlers[command](param)

    # --- AppleScript ---------------------------------------------------------------------------------

    def _space(self, name):
        if name is None:
            return None
        found = next((s for s in self.window.store.spaces if s.name == name), None) or \
            next((s for s in self.window.store.spaces if s.name.casefold() == name.casefold()), None)
        if found is None:
            raise macos.ScriptError(f'There\'s no Space called "{name}".')
        return found

    def _tab(self, tab_id):
        tab = self.window.store.find_tab(tab_id or "")
        if tab is None:
            raise macos.ScriptError(f'There\'s no tab with the id "{tab_id}".')
        return tab

    def record(self, tab) -> dict:
        section, space = self.window.store.locate(tab) or (TODAY, None)
        return {TAB_RECORD["id"]: tab.id, TAB_RECORD["title"]: tab.label, TAB_RECORD["URL"]: tab.url,
                TAB_RECORD["space"]: space.name if space else "", TAB_RECORD["section"]: section}

    def list_tabs(self, param):
        space = self._space(param(IN_SPACE))
        store = self.window.store
        tabs = [t for t in store.all_tabs() if space is None or (store.locate(t) or (None, None))[1] is space]
        return [self.record(t) for t in tabs]

    def current_tab(self, _param):
        tab = self.window.current_tab
        return self.record(tab) if tab is not None and self.window.store.locate(tab) else None

    def list_spaces(self, _param):
        return [s.name for s in self.window.store.spaces]

    def open_tab(self, param):
        url = param("----") or ""
        if QUrl(url).scheme() not in ("http", "https"):
            raise macos.ScriptError("open tab takes a web page's address (https://…).")
        return self.record(self._open(url, self._space(param(IN_SPACE))))

    def focus_tab(self, param):
        self._show(self._tab(param("----")))

    def close_tab(self, param):
        self.window.close_tab(self._tab(param("----")))

    def switch_space(self, param):
        self.window.switch_space(self._space(param("----")))
        self.window.bring_to_front()

    def run_recipe(self, param):
        recipes = self.window.feature("recipes")
        if recipes is None or recipes.run_named(param("----") or "") is None:
            raise macos.ScriptError(f'There\'s no recipe called "{param("----")}".')
        self.window.bring_to_front()

    def _open(self, url, space=None):
        tab = Tab(url)
        self.window.store.add_tab(tab, TODAY, space or self.window.store.active_space, 0)
        self._show(tab)
        return tab

    def _show(self, tab):
        window = self.window
        _section, space = window.store.locate(tab) or (FAVORITES, None)
        if space is not None and space is not window.store.active_space:
            window.switch_space(space)
        window.activate_tab(tab)
        window.bring_to_front()

    # --- Links to Eclipse ----------------------------------------------------------------------------

    def app_link(self, url: QUrl) -> bool:
        if url.scheme().lower() != OWN_SCHEME:
            return False
        action = (url.host() or url.path().strip("/")).lower()
        if action not in ("open", "search", "space", "new-tab"):
            return False  # (another feature's, perhaps: a recipe. app.py says if it's nobody's)
        query = {k: v[-1] for k, v in parse_qs(url.query(QUrl.ComponentFormattingOption.FullyEncoded)).items()}
        try:
            space = self._space(query.get("space") or query.get("name"))
            if action == "open" and QUrl(query.get("url", "")).scheme() in ("http", "https"):
                self._open(query["url"], space)
            elif action == "search" and query.get("q", "").strip():
                engine = self.window.store.settings.get("search_engine", "google")
                self._open(search_url(query["q"], engine), space)
            elif action == "space" and space is not None:
                self.window.switch_space(space)
                self.window.bring_to_front()
            elif action == "new-tab":
                if space is not None:
                    self.window.switch_space(space)
                self.window.bring_to_front()
                self.window.open_command_bar()
            else:  # (open with no web page, search with nothing to search for…)
                self.window.bring_to_front()
                self.window.toast(f"Eclipse can't open the link {url.toString()[:80]}")
        except macos.ScriptError as problem:
            self.window.bring_to_front()
            self.window.toast(str(problem))
        return True


FEATURE = ScriptingFeature
