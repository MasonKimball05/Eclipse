# A Space's name, color, theme and menu, deleting one, and sharing one (space_share.py).
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

import shutil
from PyQt6.QtCore import QPoint, QRect, QSize, QStandardPaths, QUrl
from PyQt6.QtWidgets import QColorDialog, QFileDialog
from pathlib import Path

from . import brand, macos, native, prompts, space_share, storage
from .models import walk_folders, walk_tabs
from .space_editor import SpaceEditor, native_space_editor
from .theme import PALETTE
from .theme_editor import edit_theme


class SpaceEditing:
    def share_space(self, space=None):
        """The Space as a file, in macOS's share menu (AirDrop, Messages, Mail…), or saved where you choose."""
        space = space or self.store.active_space
        if not list(walk_tabs(space.pinned)) and not any(f.live for f in walk_folders(space.pinned)):
            self.toast(f"“{space.name}” has no pinned tabs to share")
            return
        folder = storage.data_dir() / "shared spaces"
        folder.mkdir(exist_ok=True)
        for old in folder.glob("*" + space_share.SUFFIX):
            old.unlink(missing_ok=True)  # (only the latest is kept: it's there to be sent)
        path = space_share.write(space, folder)
        title, native = self.sidebar.space_title, self.sidebar.native
        if native is not None and native.container.isVisible():  # (the native sidebar's Space name)
            anchor = QRect(native.container.mapTo(self, QPoint(4, 2)), QSize(max(40, native.container.width() // 2), 26))
        elif title.isVisible():
            anchor = QRect(title.mapTo(self, QPoint(0, 0)), title.size())
        else:
            anchor = QRect(self.content.mapTo(self, QPoint(self.content.width() // 2, 0)), QSize(1, 1))
        if macos.share(self, bytes(QUrl.fromLocalFile(str(path)).toEncoded()).decode(), anchor):
            return
        start = str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)) / path.name)
        dest, _ = QFileDialog.getSaveFileName(self, "Share Space", start, f"Shared Spaces (*{space_share.SUFFIX})")
        if dest:
            shutil.copyfile(path, dest)
            self.toast(f"Saved “{space.name}” as {Path(dest).name}: send it to anyone with {brand.NAME}")

    def import_space(self):
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        path, _ = QFileDialog.getOpenFileName(self, "Import Space", folder, f"Shared Spaces (*{space_share.SUFFIX})")
        if path:
            self.import_space_file(path)

    def import_space_file(self, path):
        """A shared Space someone sent: added as a new Space, after asking."""
        self.bring_to_front()
        try:
            shared = space_share.read(Path(path))
        except ValueError as error:
            prompts.tell(self, "Can't Import Space", str(error), warning=True)
            return
        prompts.choose(self, "Import Space", f"Add the Space “{shared['name']}”, with {space_share.summary(shared)}?",
                       ["Add Space"], lambda _: self._add_shared_space(shared),
                       detail="It brings only addresses and names: you sign in to its sites yourself.")

    def _add_shared_space(self, shared):
        used = {s.color for s in self.store.spaces}
        color = next((c for _, c in PALETTE if c not in used), PALETTE[len(self.store.spaces) % len(PALETTE)][1])
        space = space_share.build(shared, color)
        self.store.spaces.append(space)
        self.store._changed()
        self.switch_space(space)
        live = self.feature("live_folders")
        for folder in walk_folders(space.pinned):
            if folder.live and live is not None:
                live.folders.refresh(folder)
        self.toast(f"Added “{space.name}”")

    def rename_space(self, space):
        def named(name):
            if name.strip():
                space.name = name.strip()
                self.store.schedule_save()
                self.sidebar.rebuild()
        prompts.ask_text(self, "Rename Space", "Space name:", named, text=space.name, action="Rename")

    def set_space_color(self, space, color=None):
        if color is None:
            picked = QColorDialog.getColor(parent=self, title="Space Color")
            if not picked.isValid():
                return
            color = picked.name()
        space.color = color
        if space.theme.get("colors"):
            space.theme["colors"][0] = color
        self.store.schedule_save()
        if space is self.store.active_space:
            self.apply_theme()
        else:
            self.sidebar.rebuild()

    def edit_space_theme(self, space=None):
        space = space or self.store.active_space
        if space is not self.store.active_space:
            self.switch_space(space)  # so the editor previews on the real window
        edit_theme(self, space)

    def delete_space(self, space):
        if len(self.store.spaces) == 1:
            self.toast("You need at least one Space")
            return
        prompts.confirm(self, "Delete Space",
                        f"Delete “{space.name}” and its {len(list(walk_tabs(space.pinned + space.today)))} tabs?",
                        lambda: self._delete_space_now(space) if space in self.store.spaces else None,
                        action="Delete", destructive=True)

    def _delete_space_now(self, space):
        if len(self.store.spaces) == 1:
            return
        was_active = space is self.store.active_space
        for tab in list(walk_tabs(space.pinned + space.today)):
            self.store.remove_from_split(tab)
            self._remove_view(tab)
        self.store.remove_space(space)
        if was_active:
            self.apply_theme()
            self._restore_space_tab()

    def show_space_menu(self, space, pos, beside=None):
        """`beside`: the Space name's place (global), for the native popover to open beside it, pointing at it."""
        if native_space_editor(self, space, pos, beside) is None:
            SpaceEditor(self, space).popup(pos)

    def edit_space(self, space=None):
        """Open the Space editor next to the Space's name in the sidebar."""
        space = space or self.store.active_space
        if space is not self.store.active_space:
            self.switch_space(space)
        self.show_space_menu(space, self.sidebar.space_title_anchor(), self.sidebar.space_title_rect())

    def space_edited(self, space, retheme=False):
        self.store.schedule_save()
        if retheme and space is self.store.active_space:
            self.apply_theme()
        else:
            self.sidebar.rebuild()
