# Spotlight: your Spaces, pinned tabs and Favorites, found with ⌘Space like an app or a document. Picking one
# brings Eclipse up at it (switching to its Space). Today's tabs come and go too often to be worth it, and
# private windows' never are. The work is macOS's (macos.spotlight_add / on_spotlight_open).
#
# Only the built app (Eclipse, or Eclipse Dev) puts things in Spotlight: Spotlight files them under the app,
# which `python main.py` isn't. Settings → General → "Show Spaces and pinned tabs in Spotlight" turns it off,
# taking them out again.

from PyQt6.QtCore import QTimer, QUrl

from . import macos
from .features import Feature
from .models import FAVORITES, walk_tabs

SETTING = "spotlight"
INDEXED = "spotlight_ids"  # what's in Spotlight now, so what's gone since can be taken out (even after a restart)
SETTLE_MS = 3000            # after a change, the wait before Spotlight's told (a burst of changes, told once)
RECHECK_MS = 2 * 60 * 1000  # titles change without the sidebar's structure changing: looked at now and then


class SpotlightFeature(Feature):
    name = "spotlight"

    def __init__(self, window):
        super().__init__(window)
        self._told = None  # (what Spotlight was last told, this run)
        self.timer = QTimer(window, singleShot=True, interval=SETTLE_MS, timeout=self.refresh)
        self.recheck = QTimer(window, interval=RECHECK_MS, timeout=self.refresh)
        window.store.structure_changed.connect(self.timer.start)
        macos.on_spotlight_open(self.open)

    def able(self) -> bool:
        return macos.is_built_app()

    def on(self) -> bool:
        return self.window.store.settings.get(SETTING, True)

    def ready(self):
        if self.able():
            self.timer.start()
            self.recheck.start()

    def items(self) -> list[tuple]:
        """What goes in Spotlight: (id, title, description, keywords)."""
        store, found = self.window.store, []
        for space in store.spaces:
            found.append((f"space:{space.id}", space.name, "Space in Eclipse", ["Eclipse", "Space"]))
        tabs = [(t, "Favorite in Eclipse") for t in store.favorites] + \
            [(t, f"Pinned in {space.name}") for space in store.spaces for t in walk_tabs(space.pinned)]
        for tab, where in tabs:
            host = QUrl(tab.url).host().removeprefix("www.")
            if not host:
                continue  # (a note or an easel: nothing to look for it by)
            found.append((f"tab:{tab.id}", tab.label or host, f"{where} · {host}", [host, "Eclipse"]))
        return found

    def refresh(self):
        """Tell Spotlight what's changed (or take everything out, with the setting off)."""
        if not self.able():
            return
        settings = self.window.store.settings
        before = set(settings.get(INDEXED, []))
        items = self.items() if self.on() else []
        if items == self._told:
            return
        ids = {item[0] for item in items}
        if items:
            macos.spotlight_add(items)
        if not self.on():
            macos.spotlight_remove(None)
        elif before - ids:
            macos.spotlight_remove(sorted(before - ids))
        self._told = items
        if before != ids:
            settings[INDEXED] = sorted(ids)
            self.window.store.schedule_save()

    def open(self, item_id: str):
        """You picked one of Eclipse's items in Spotlight."""
        window, store = self.window, self.window.store
        kind, _, found_id = item_id.partition(":")
        if kind == "space" and (space := store.space(found_id)) is not None:
            window.switch_space(space)
        elif kind == "tab" and (tab := store.find_tab(found_id)) is not None:
            section, space = store.locate(tab) or (FAVORITES, None)
            if space is not None and space is not store.active_space:
                window.switch_space(space)
            window.activate_tab(tab)
        else:
            self.timer.start()  # (it's gone since: Spotlight's told)
        window.bring_to_front()

    def setting_changed(self):
        self._told = None
        self.refresh()


FEATURE = SpotlightFeature
