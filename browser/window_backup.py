# Back Up Everything and Restore from Backup (backup.py does the work).
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

import time

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtWidgets import QApplication, QFileDialog
from pathlib import Path

from . import backup, brand, prompts
from .timefmt import portable


class Backups:
    def back_up(self):
        from .window import _save_editor  # (window imports this module)
        name = time.strftime(f"{brand.NAME} Backup %Y-%m-%d.zip")
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _ = QFileDialog.getSaveFileName(self, f"Back Up {brand.NAME}", f"{folder}/{name}", "Backups (*.zip)")
        if not path:
            return
        # Everything on disk first: the session (and history), open notes, extensions' storage
        self.store.save()
        self.store.save_history()
        for tab in self.store.all_tabs():
            _save_editor(tab.view)
        self.emit("before_backup")  # (extensions' storage, say)
        try:
            count = backup.create(Path(path))
        except (OSError, ValueError) as error:
            prompts.tell(self, "Backup Failed", str(error), warning=True)
            return
        self.toast(f"Backed up {count} file{'' if count == 1 else 's'} to {Path(path).name}")

    def restore_backup(self):
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _ = QFileDialog.getOpenFileName(self, "Restore from Backup", folder, "Backups (*.zip)")
        if not path:
            return
        try:
            info = backup.describe(Path(path))
        except ValueError as error:
            prompts.tell(self, "Can't Restore", str(error), warning=True)
            return
        made = time.strftime(portable("%-d %B %Y at %-I:%M %p"), time.localtime(info["created"]))
        code = [f"{n} {what}{'s' if n != 1 else ''}" for n, what in
                ((info["extensions"], "extension"), (info["userscripts"], "userscript"),
                 (info["native"], "app extensions can talk to")) if n]
        detail = (
            f"It has {len(info['spaces'])} Space(s) ({', '.join(info['spaces'][:5])}) and {info['tabs']} tabs. "
            + (f"It also has {', '.join(code)}: code that can read and change the sites you visit. Only restore "
               "those from a backup you made yourself. " if code else "")
            + "What you have now is kept in a folder, not deleted. Sign-ins and cookies aren't affected.\n\n"
            f"{brand.NAME} will quit; open it again to finish.")
        answers = ["Restore Without Extensions", "Restore Everything"] if code else ["Restore"]  # (the first, the default)
        prompts.choose(self, "Restore from Backup",
                       f"Replace your Spaces, tabs, settings and notes with this backup from {made}?", answers,
                       lambda index: self._restore_from(path, with_code=answers[index] != "Restore Without Extensions"),
                       detail=detail)

    def _restore_from(self, path, with_code):
        try:
            backup.stage(Path(path), with_code=with_code)
        except (OSError, ValueError) as error:
            prompts.tell(self, "Can't Restore", str(error), warning=True)
            return
        QApplication.instance().quit()
