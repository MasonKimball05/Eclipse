# Verified builds: whether this copy of Eclipse is exactly as its maker signed it.
#
# Eclipse is open source, so anyone can build and change their own copy. A *verified* one is a release
# build: the app, with all of its code inside it, signed with the maker's Developer ID, notarized by Apple,
# and unchanged since. macOS's own tools say so (codesign checks every file against the signature and who
# signed it; spctl checks that Apple notarized it), so the answer comes from the Mac, not from Eclipse.
#
# A copy running from a folder of source (a checkout, or the app ./run.sh --build-app makes, which runs the
# checkout's code) is "built from source": nothing wrong with it, just not a signed release.
#
# Anyone can check a copy from Terminal too:  spctl -a -vv /Applications/Eclipse.app
# (a verified one says "source=Notarized Developer ID" and names the maker).

from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

TEAM_ID = "F4P3T9JYP2"  # the maker's Apple Developer team (as in "Developer ID Application: Mason Kimball (F4P3T9JYP2)")
# Signed with a Developer ID Application certificate (that's the OID) issued to that team
REQUIREMENT = ('anchor apple generic and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
               'and certificate leaf[subject.OU] = "{team}"')

CHECKING, VERIFIED, SOURCE, UNSIGNED, CHANGED, OTHER = ("checking", "verified", "source", "unsigned", "changed",
                                                        "other")
WORDS = {  # (what Settings says: a heading, and a line under it)
    CHECKING: ("Checking…", "Asking macOS whether this copy is as it was signed."),
    VERIFIED: ("Verified", "Signed by Eclipse's maker, notarized by Apple, and unchanged since."),
    SOURCE: ("Built from source", "This copy runs from source code, not a signed release, so it can't be verified."),
    UNSIGNED: ("Not verified", "This copy isn't signed, so there's no telling whether it's been changed."),
    CHANGED: ("Not verified", "This copy has been changed since it was signed."),
    OTHER: ("Not verified", "This copy wasn't signed and notarized as an Eclipse release."),
}


def app_bundle(path=None) -> Path | None:
    """The .app this code runs from, or None when it runs from a folder of source."""
    for parent in Path(path or __file__).resolve().parents:
        if parent.suffix == ".app" and (parent / "Contents" / "Info.plist").is_file():
            return parent
    return None


def outcome(signed: tuple[int, str], by_maker: tuple[int, str] | None, notarized: tuple[int, str] | None) -> str:
    """What the checks found, from each one's (exit code, output): codesign's own check of the files, then
    codesign against the maker's signature, then spctl (notarized). A check that didn't run is None."""
    code, output = signed
    if code != 0:
        return UNSIGNED if "not signed at all" in output else CHANGED
    if by_maker is None or by_maker[0] != 0 or notarized is None or notarized[0] != 0:
        return OTHER
    return VERIFIED


class Verification(QObject):
    """This copy's verification: started once, in the background (checking every file of a big app takes a
    few seconds). state is one of the states above; changed fires when it's known."""

    changed = pyqtSignal()

    def __init__(self, parent=None, bundle=None, team=None):
        super().__init__(parent)
        self.bundle = bundle if bundle is not None else app_bundle()
        self.team = TEAM_ID if team is None else team
        self.state = CHECKING if self.bundle is not None else SOURCE
        self._results = []
        if self.bundle is not None:
            # (not --strict: that also fails a copy over Finder's leftover metadata on a file, which isn't a change)
            self._run(["/usr/bin/codesign", "--verify", "--deep", str(self.bundle)])

    def _run(self, command):
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(lambda code, _status: self._step(process, code))
        process.errorOccurred.connect(lambda _error: process.state() == QProcess.ProcessState.NotRunning
                                      and self._step(process, -1))
        process.start(command[0], command[1:])

    def _step(self, process, code):
        if process.property("done"):
            return
        process.setProperty("done", True)
        self._results.append((code, bytes(process.readAll()).decode(errors="replace")))
        process.deleteLater()
        signed = self._results[0]
        if len(self._results) == 1 and signed[0] == 0 and self.team:
            self._run(["/usr/bin/codesign", "--verify", "--deep", f"-R={REQUIREMENT.format(team=self.team)}",
                       str(self.bundle)])
        elif len(self._results) == 2 and self._results[1][0] == 0:
            self._run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(self.bundle)])
        else:
            results = self._results + [None] * (3 - len(self._results))
            self.state = outcome(*results)
            self.changed.emit()


_this_copy = None


def this_copy() -> Verification:
    """This copy's verification (started the first time it's asked for)."""
    global _this_copy
    if _this_copy is None:
        from PyQt6.QtWidgets import QApplication
        _this_copy = Verification(QApplication.instance())
    return _this_copy
