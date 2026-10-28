# Tab recipes: a routine you do often, saved to replay with a click. "Morning dashboards" opens your five
# dashboards in the Work Space, side by side if you like, and can finish by asking Claude to Browse for Me
# ("what's on Hacker News about Qt today?").
#
#   Save one: ⌘T → "Save Tabs as Recipe…" (the Split View on screen, or the Space's Today tabs), or right-click
#   a Space → Save Tabs as Recipe….
#   Run one:  ⌘T → "Run Recipe: Morning dashboards", an eclipse-browser://recipe?name=… link (Shortcuts), or
#   AppleScript's `run recipe "Morning dashboards"`.
#   Change one: ⌘T → "Edit Recipe: …".
# Running one reuses a tab already open on the same page in that Space rather than opening it twice. Recipes
# are kept in the settings.

import re
import uuid
from urllib.parse import parse_qs

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QInputDialog, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton)

from .external_links import OWN_SCHEME
from .features import Feature
from .models import TODAY, Tab, walk_tabs
from .split import MAX_PANES
from .theme import stylesheet

SETTING = "recipes"
MOST_TABS = 12


def web_urls(text_or_list) -> list[str]:
    """The web addresses in a recipe (one per line when typed): anything else is left out."""
    lines = text_or_list.splitlines() if isinstance(text_or_list, str) else text_or_list
    found = []
    for line in lines:
        line = line.strip()
        if line and "://" not in line:
            if re.match(r"[a-zA-Z][\w+.-]*:(?!\d)", line):
                continue  # (javascript:, mailto: and the like: not a page; "localhost:8000" is one)
            line = "https://" + line
        if QUrl(line).scheme() in ("http", "https") and QUrl(line).host() and line not in found:
            found.append(line)
    return found[:MOST_TABS]


def _same_page(a: str, b: str) -> bool:
    strip = lambda u: QUrl(u).adjusted(QUrl.UrlFormattingOption.StripTrailingSlash
                                       | QUrl.UrlFormattingOption.RemoveFragment).toString()
    return strip(a) == strip(b)


class RecipesFeature(Feature):
    name = "recipes"

    def recipes(self) -> list[dict]:
        return self.window.store.settings.setdefault(SETTING, [])

    def find(self, name: str) -> dict | None:
        recipes = self.recipes()
        return next((r for r in recipes if r["name"] == name), None) or \
            next((r for r in recipes if r["name"].casefold() == (name or "").casefold()), None)

    # --- Saving ---------------------------------------------------------------------------------

    def current_tabs(self) -> tuple[list[str], bool]:
        """What "Save Tabs as Recipe" would keep: the Split View on screen, or the Space's Today tabs."""
        store, tab = self.window.store, self.window.current_tab
        split = store.split_of(tab) if tab is not None else None
        if split:
            return web_urls([t.url for t in split]), True
        return web_urls([t.url for t in walk_tabs(store.active_space.today)]), False

    def save_current(self, name=None):
        urls, split = self.current_tabs()
        if not urls:
            self.window.toast("Open the tabs for your recipe first (in Today, or side by side)")
            return None
        if name is None:
            name, ok = QInputDialog.getText(self.window, "Save Tabs as Recipe",
                                            f"Name for these {len(urls)} tab{'s' if len(urls) != 1 else ''}:")
            if not ok or not name.strip():
                return None
        recipe = {"id": uuid.uuid4().hex[:12], "name": name.strip(), "urls": urls, "split": split,
                  "space": self.window.store.active_space.id, "ask": ""}
        old = self.find(recipe["name"])
        if old is not None:
            self.recipes().remove(old)
        self.recipes().append(recipe)
        self.window.store.schedule_save()
        self.window.toast(f"Saved the recipe “{recipe['name']}” · run it from ⌘T")
        return recipe

    # --- Running --------------------------------------------------------------------------------

    def run(self, recipe: dict):
        window, store = self.window, self.window.store
        space = store.space(recipe.get("space")) or store.active_space
        if space is not store.active_space:
            window.switch_space(space)
        open_tabs = list(walk_tabs(space.pinned)) + list(walk_tabs(space.today)) + list(store.favorites)
        tabs, new = [], []
        for url in web_urls(recipe.get("urls", [])):
            tab = next((t for t in open_tabs if _same_page(t.url, url)), None)
            if tab is None:
                tab = Tab(url)
                new.append(tab)
            tabs.append(tab)
        for tab in reversed(new):  # (at the top of Today, in the recipe's order)
            store.add_tab(tab, TODAY, space, 0)
        if not tabs:
            window.toast(f"“{recipe['name']}” has no pages to open")
            return []
        if recipe.get("split") and len(tabs) >= 2:
            for tab in tabs[1:MAX_PANES]:
                store.add_to_split(tabs[0], tab, MAX_PANES)
        else:
            for tab in tabs[1:]:
                window._ensure_view(tab)  # (loading in the background, ready as you get to them)
        window.activate_tab(tabs[0])
        claude = window.feature("claude")
        if recipe.get("ask", "").strip() and claude is not None:
            claude.browse_for_me(recipe["ask"])
        return tabs

    def run_named(self, name) -> dict | None:
        recipe = self.find(name)
        if recipe is not None:
            self.run(recipe)
        return recipe

    # --- Where it's offered -----------------------------------------------------------------------

    def commands(self):
        found = [("Save Tabs as Recipe…", "ph.list-plus", self.save_current)]
        for recipe in self.recipes():
            found.append((f"Run Recipe: {recipe['name']}", "ph.play", lambda r=recipe: self.run(r)))
            found.append((f"Edit Recipe: {recipe['name']}", "ph.pencil-simple", lambda r=recipe: self.edit(r)))
        return found

    def space_actions(self, space):
        if space is not self.window.store.active_space:
            return []
        return [("ph.list-plus", "Save Tabs as Recipe…", self.save_current)]

    def app_link(self, url) -> bool:
        if url.scheme().lower() != OWN_SCHEME or (url.host() or url.path().strip("/")).lower() != "recipe":
            return False
        query = parse_qs(url.query(QUrl.ComponentFormattingOption.FullyEncoded))
        name = (query.get("name") or [""])[-1]
        self.window.bring_to_front()
        if self.run_named(name) is None:
            self.window.toast(f"There's no recipe called “{name}”")
        return True

    def edit(self, recipe):
        dialog = RecipeDialog(self.window, recipe)
        answer = dialog.exec()
        if dialog.deleted:
            self.recipes().remove(recipe)
        elif answer == QDialog.DialogCode.Accepted:
            recipe.update(dialog.result_recipe())
        self.window.store.schedule_save()
        return dialog


class RecipeDialog(QDialog):
    def __init__(self, browser, recipe):
        super().__init__(browser)
        self.deleted = False
        self.setWindowTitle(f"Recipe · {recipe['name']}")
        self.setStyleSheet(stylesheet(browser.theme))
        self.setProperty("panel", True)
        self.resize(480, 0)
        form = QFormLayout(self)
        self.name = QLineEdit(recipe["name"])
        self.space = QComboBox()
        for space in browser.store.spaces:
            self.space.addItem(space.name, space.id)
        self.space.setCurrentIndex(max(0, self.space.findData(recipe.get("space"))))
        self.urls = QPlainTextEdit("\n".join(recipe.get("urls", [])))
        self.urls.setFixedHeight(120)
        self.split = QCheckBox(f"Side by side (the first {MAX_PANES})")
        self.split.setChecked(bool(recipe.get("split")))
        self.ask = QLineEdit(recipe.get("ask", ""))
        self.ask.setPlaceholderText("Optional: then ask Claude to Browse for Me…")
        form.addRow("Name", self.name)
        form.addRow("Space", self.space)
        form.addRow("Pages", self.urls)
        form.addRow("", self.split)
        form.addRow("Then", self.ask)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        delete = QPushButton("Delete Recipe")
        buttons.addButton(delete, QDialogButtonBox.ButtonRole.DestructiveRole)
        delete.clicked.connect(self._delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _delete(self):
        if QMessageBox.question(self, "Delete Recipe", f"Delete “{self.name.text()}”?") == \
                QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.reject()

    def result_recipe(self) -> dict:
        return {"name": self.name.text().strip() or "Recipe", "space": self.space.currentData(),
                "urls": web_urls(self.urls.toPlainText()), "split": self.split.isChecked(),
                "ask": self.ask.text().strip()}


FEATURE = RecipesFeature
