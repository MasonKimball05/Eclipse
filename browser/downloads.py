# The Downloads panel: everything the browser downloads, plus "Download from URL",
# which hands a link to yt-dlp (videos, audio, playlists from YouTube and ~1,800 other sites).
#
# Ported from Custom-Mac-Media-Player's yt-dlp integration: same two-step flow (paste a
# link, yt-dlp describes it, then pick options) and the same format choices. yt-dlp and
# ffmpeg come from Homebrew rather than being bundled, since sites change constantly and
# `brew upgrade` keeps yt-dlp current.

import json
import os
import shutil
import sys
from pathlib import Path

import qtawesome as qta
from PyQt6.QtCore import (QFileInfo, QMimeData, QObject, QProcess, QProcessEnvironment, QSize, QStandardPaths, Qt,
                          QUrl, pyqtSignal)
from PyQt6.QtGui import QColor, QDesktopServices, QDrag
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFileIconProvider, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                             QMenu, QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import macos
from .motion import FadesIn
from .theme import FittedLabel, quiet_scrollbars, stylesheet

HOMEBREW_BIN = "/opt/homebrew/bin"
PROGRESS_MARKER = "MPDL|"
FILE_MARKER = "MPDLFILE|"
PROGRESS_TEMPLATE = (PROGRESS_MARKER + "%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
                     "%(progress.total_bytes_estimate)s|%(info.vcodec)s|%(info.acodec)s")

VIDEO_FORMATS = [("mp4", "MP4 (plays everywhere)"), ("mkv", "MKV (any codec)")]
AUDIO_FORMATS = [("m4a", "M4A (AAC)"), ("mp3", "MP3"), ("opus", "Opus"), ("flac", "FLAC"), ("wav", "WAV")]
QUALITIES = [0, 2160, 1440, 1080, 720, 480, 360]  # 0 = best available
COOKIE_BROWSERS = [("", "Don't use cookies"), ("safari", "Safari"), ("chrome", "Chrome"), ("firefox", "Firefox"),
                   ("brave", "Brave"), ("edge", "Edge"), ("vivaldi", "Vivaldi")]

DEFAULT_SETTINGS = {
    "folder": QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation),
    "template": "%(title).180B [%(id)s].%(ext)s",
    "kind": "video",
    "video_format": "mp4",
    "max_height": 0,
    "audio_format": "m4a",
    "subtitle_langs": "",
    "embed_subs": False,
    "embed_metadata": True,
    "embed_thumbnail": True,
    "cookies_from": "",
    "rate_limit": "",
}


# Files that can run something on a Mac when opened (an app, a script, an installer, a link that opens one):
# a page's download of one asks first
RISKY = {".app", ".command", ".tool", ".terminal", ".pkg", ".mpkg", ".webloc", ".inetloc", ".fileloc",
         ".scpt", ".scptd", ".applescript", ".workflow", ".jar", ".sh", ".zsh", ".bash",
         ".prefpane", ".mobileconfig", ".exe", ".msi", ".bat", ".cmd", ".ps1", ".lnk",
         ".saver", ".xip",  # (a screen saver is a plug-in that runs; a .xip unpacks itself, usually into an app)
         # What Windows runs (or installs, or opens a page of commands for) when it's double-clicked
         ".scr", ".com", ".pif", ".cpl", ".vbs", ".vbe", ".hta", ".reg", ".msix", ".msixbundle", ".appx",
         ".appxbundle", ".appinstaller", ".application", ".appref-ms", ".url", ".scf", ".settingcontent-ms"}
# A script Windows itself runs when it's opened, and a Mac only shows
WINDOWS_RISKY = {".js", ".jse", ".wsf", ".wsh"}
# Archives that unpack when opened ("Tool.app.zip" is an app): what's inside them is what counts
ARCHIVES = {".zip", ".gz", ".tgz", ".tbz", ".tbz2", ".txz", ".bz2", ".xz", ".7z", ".rar", ".cpgz", ".cpio", ".tar"}


def risky(name: str) -> bool:
    """Whether a downloaded file of this name could run something when opened (".app.zip" counts: opening the
    zip unpacks the app)."""
    parts = [f".{p.lower()}" for p in name.split(".")[1:]]
    while len(parts) > 1 and parts[-1] in ARCHIVES:  # ("x.app.tar.gz": the .tar, then the app)
        parts = parts[:-1]
    found = RISKY | (WINDOWS_RISKY if sys.platform == "win32" else set())
    return bool(parts) and parts[-1] in found


def mark_downloaded(path: str) -> bool:
    """Mark a finished download as from the internet, the way Safari, Chrome and Edge do, so opening it goes through
    the system's check first: Gatekeeper's on a Mac, SmartScreen's (and Office's Protected View) on Windows, which
    looks for the file's Zone.Identifier stream. Qt WebEngine does neither, and nor does yt-dlp."""
    if sys.platform != "win32":
        return macos.quarantine(path)
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as stream:  # (ZoneId 3: the internet)
            stream.write("[ZoneTransfer]\r\nZoneId=3\r\n")
        return True
    except OSError:  # (a drive that isn't NTFS keeps no streams: nothing to mark it with)
        return False


def ytdlp_settings(store) -> dict:
    return {**DEFAULT_SETTINGS, **store.settings.get("ytdlp", {})}


def find_tool(name) -> str | None:
    for path in (shutil.which(name), f"{HOMEBREW_BIN}/{name}", f"/usr/local/bin/{name}"):
        if path and os.access(path, os.X_OK):
            return path
    return None


# What Windows' own installer (winget) calls the tools Homebrew installs on a Mac
WINGET = {"yt-dlp": "yt-dlp.yt-dlp", "ffmpeg": "Gyan.FFmpeg", "gh": "GitHub.cli"}


def install_hint(*tools) -> str:
    """How to install them here: "brew install …" on a Mac, "winget install …" on Windows."""
    if sys.platform == "win32":
        return "winget install " + " ".join(WINGET.get(t, t) for t in tools)
    return "brew install " + " ".join(tools)


def _environment() -> QProcessEnvironment:
    # An app started from Finder gets a minimal PATH; yt-dlp looks there for ffmpeg
    # and for the JavaScript runtime it needs for YouTube's player challenges.
    env = QProcessEnvironment.systemEnvironment()
    env.insert("PATH", f"{HOMEBREW_BIN}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    env.insert("PYTHONUNBUFFERED", "1")
    return env


def _error_message(stderr: str) -> str | None:
    """The last "ERROR:" line yt-dlp printed, without its prefixes."""
    lines = [l for l in stderr.splitlines() if l.startswith("ERROR:")]
    if not lines:
        return None
    message = lines[-1][len("ERROR:"):].strip()
    if message.startswith("[") and ": " in message:  # "[youtube] abc123: Video unavailable"
        message = message.split(": ", 1)[1]
    return message


def build_arguments(opts: dict, url: str, playlist: bool) -> list[str]:
    height = f"[height<=?{opts['max_height']}]" if opts["max_height"] else ""
    args = [
        "--ignore-config",  # your personal yt-dlp config shouldn't override what this panel promised
        "--yes-playlist" if playlist else "--no-playlist",
        "--newline", "--progress", "--progress-template", "download:" + PROGRESS_TEMPLATE,
        "--no-simulate", "--print", "after_move:" + FILE_MARKER + "%(filepath)s",
        "--no-mtime",  # file date = when you downloaded it, so new downloads sort first in Finder
        "-P", opts["folder"], "-o", opts["template"],
    ]
    if opts["kind"] == "audio":
        args += ["-f", "ba/b", "-x", "--audio-format", opts["audio_format"], "--audio-quality", "0"]
        if opts["embed_thumbnail"] and opts["audio_format"] != "wav":  # WAV has nowhere to put cover art
            args.append("--embed-thumbnail")
    elif opts["video_format"] == "mp4":
        # H.264 + AAC first: what every Mac app can play, even if a higher resolution exists
        # in another codec. Sites without H.264 fall back to the best they have, and
        # "mp4/mkv" then uses MKV for codecs MP4 can't hold.
        args += ["-f", f"bv*{height}[vcodec^=avc]+ba[ext=m4a]/bv*{height}[vcodec^=avc]+ba/b{height}[ext=mp4]/"
                       f"bv*{height}+ba/b{height}/bv*+ba/b",
                 "--merge-output-format", "mp4/mkv"]
    else:
        args += ["-f", f"bv*{height}+ba/b{height}/bv*+ba/b", "--merge-output-format", "mkv"]
    if opts["kind"] == "video" and opts["embed_thumbnail"]:
        args.append("--embed-thumbnail")
    if opts["embed_metadata"]:
        args.append("--embed-metadata")

    langs = [l.strip() for l in opts["subtitle_langs"].replace(";", ",").split(",") if l.strip()]
    if langs:
        # Prefer subtitles the uploader wrote; fall back to automatic captions in that language
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", ",".join(langs),
                 "--sub-format", "srt/vtt/best", "--convert-subs", "srt"]
        if opts["kind"] == "video" and opts["embed_subs"]:
            args.append("--embed-subs")
    if opts["cookies_from"]:
        args += ["--cookies-from-browser", opts["cookies_from"]]
    if opts["rate_limit"].strip():
        args += ["-r", opts["rate_limit"].strip()]
    return args + ["--", url]


def _duration(seconds) -> str:
    if not seconds:
        return ""
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def quick_look(path, running=None):
    """Quick Look, as Finder's space bar does (macOS only): a preview of the file in its own panel. Pass back
    what it returned to close the panel instead, as pressing space again does in Finder."""
    if running is not None and running.state() != QProcess.ProcessState.NotRunning:
        running.kill()
        return None
    if sys.platform != "darwin" or not path or not os.path.exists(path):
        return None
    process = QProcess()
    process.setStandardOutputFile(QProcess.nullDevice())  # (it talks a lot)
    process.setStandardErrorFile(QProcess.nullDevice())
    process.start("/usr/bin/qlmanage", ["-p", path])
    return process


def reveal_in_finder(path):
    if sys.platform == "darwin":
        QProcess.startDetached("open", ["-R", path])
    elif sys.platform == "win32":
        QProcess.startDetached("explorer", [f"/select,{Path(path)}"])  # (File Explorer, the file picked)
    else:
        QProcess.startDetached("xdg-open", [str(Path(path).parent)])


# --- What yt-dlp says is at a link ------------------------------------------------------------

class InfoFetch(QObject):
    """Runs `yt-dlp -J` (describe, don't download) and reports the parsed result."""

    done = pyqtSignal(object, str)  # info dict or None, error message

    def __init__(self, exe, url, cookies_from, parent):
        super().__init__(parent)
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(_environment())
        args = ["--ignore-config", "--flat-playlist", "--no-warnings", "-J"]
        if cookies_from:
            args += ["--cookies-from-browser", cookies_from]
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart
                                        and self.done.emit(None, "Couldn't start yt-dlp"))
        self.proc.start(exe, args + ["--", url])

    def cancel(self):
        self.proc.kill()

    def _finished(self, code, status):
        stdout = bytes(self.proc.readAllStandardOutput())
        stderr = bytes(self.proc.readAllStandardError()).decode(errors="replace")
        if status != QProcess.ExitStatus.NormalExit:
            return  # cancelled
        if code != 0:
            self.done.emit(None, _error_message(stderr) or "yt-dlp couldn't read that link.")
            return
        try:
            data = json.loads(stdout)
        except ValueError:
            self.done.emit(None, "yt-dlp returned something this browser couldn't read.")
            return
        self.done.emit(self._summarize(data), "")

    @staticmethod
    def _summarize(data) -> dict:
        if data.get("_type") in ("playlist", "multi_video"):
            entries = [e for e in data.get("entries") or []
                       if e.get("_type", "url") != "playlist" and e.get("ie_key") != "YoutubeTab"]
            return {"playlist": True, "title": data.get("title") or "Playlist", "count": len(entries),
                    "uploader": data.get("uploader") or data.get("channel"), "url": data.get("webpage_url")}
        formats = data.get("formats") or [data]
        heights = sorted({f["height"] for f in formats
                          if isinstance(f.get("height"), int) and f.get("vcodec", "x") != "none"}, reverse=True)
        subs = sorted(set(data.get("subtitles") or {}) - {"live_chat"})
        auto = sorted(c for c in (data.get("automatic_captions") or {}) if c.endswith("-orig"))
        return {"playlist": False, "title": data.get("title") or "Untitled",
                "uploader": data.get("uploader") or data.get("channel"), "duration": data.get("duration"),
                "heights": heights, "has_video": bool(heights), "subtitles": subs + auto,
                "url": data.get("webpage_url")}


# --- Download items (one row each in the panel) ------------------------------------------------

class DownloadItem(QObject):
    changed = pyqtSignal()

    def __init__(self, title, icon):
        super().__init__()
        self.title = title
        self.icon = icon
        self.status = "Starting…"
        self.fraction: float | None = None  # None = indeterminate
        self.state = "running"              # running, done, failed, cancelled
        self.path: str | None = None

    can_pause = False  # (a page's download can; yt-dlp's can't)
    paused = False

    def cancel(self):
        pass

    def toggle_pause(self):
        pass


class YtDlpDownload(DownloadItem):
    def __init__(self, exe, opts, url, title, playlist_count=0):
        super().__init__(title, "ph.music-note" if opts["kind"] == "audio" else "ph.film-strip")
        self.playlist_count = playlist_count
        self.files_done = 0
        self._stream = None
        self._stdout = b""
        self._stderr = ""
        self._cancelled = False
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(_environment())
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(
            lambda: setattr(self, "_stderr", self._stderr + bytes(self.proc.readAllStandardError()).decode(errors="replace")))
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)
        Path(opts["folder"]).mkdir(parents=True, exist_ok=True)
        self.proc.start(exe, build_arguments(opts, url, playlist=playlist_count > 0))

    def cancel(self):
        if self.state == "running":
            self._cancelled = True
            self.proc.kill()

    def _prefix(self):
        if self.playlist_count:
            return f"Video {min(self.files_done + 1, self.playlist_count)} of {self.playlist_count} · "
        return ""

    def _read(self):
        self._stdout += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._stdout:
            raw, self._stdout = self._stdout.split(b"\n", 1)
            line = raw.decode(errors="replace").strip()
            if line.startswith(FILE_MARKER):
                self.path = line[len(FILE_MARKER):]
                mark_downloaded(self.path)  # (from the internet: Gatekeeper or SmartScreen checks it before it opens)
                self.files_done += 1
                self.status = self._prefix() + "Starting next…" if self.playlist_count else "Finishing…"
                self.fraction = None
            elif line.startswith(PROGRESS_MARKER):
                self._progress(line[len(PROGRESS_MARKER):].split("|"))
            self.changed.emit()

    def _progress(self, fields):
        if len(fields) < 6:
            return
        status, done, total, estimate, vcodec, acodec = fields[:6]
        stream = "video" if vcodec not in ("none", "NA") else "audio" if acodec not in ("none", "NA") else ""
        try:
            total_bytes = float(total if total != "NA" else estimate)
            self.fraction = min(1.0, float(done) / total_bytes) if total_bytes > 0 else None
        except ValueError:
            self.fraction = None
        if status == "finished":
            self.fraction = 1.0
            self.status = self._prefix() + "Processing…"
        else:
            what = {"video": "Downloading video", "audio": "Downloading audio"}.get(stream, "Downloading")
            pct = f" {round(self.fraction * 100)}%" if self.fraction is not None else ""
            self.status = self._prefix() + what + pct

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.state, self.status = "failed", "Couldn't start yt-dlp"
            self.changed.emit()

    def _finished(self, code, status):
        if self._cancelled:
            self.state, self.status = "cancelled", "Cancelled"
        elif code == 0:
            self.state, self.fraction = "done", 1.0
            self.status = f"Done · {self.files_done} files" if self.playlist_count else "Done"
        else:
            self.state = "failed"
            self.status = _error_message(self._stderr) or f"yt-dlp stopped (exit code {code})"
        self.changed.emit()


class BrowserDownload(DownloadItem):
    """A normal download started by a web page, wrapped to look like the yt-dlp ones."""

    def __init__(self, request):
        super().__init__(request.downloadFileName(), "ph.download-simple")
        self.request = request
        self.path = str(Path(request.downloadDirectory()) / request.downloadFileName())
        request.receivedBytesChanged.connect(self._update)
        request.totalBytesChanged.connect(self._update)
        request.isFinishedChanged.connect(self._update)
        request.isPausedChanged.connect(self._update)
        self._update()

    can_pause = True

    @property
    def paused(self):
        return self.state == "running" and self.request.isPaused()

    def cancel(self):
        if self.state == "running":
            self.request.cancel()

    def toggle_pause(self):
        if self.state == "running":
            self.request.resume() if self.request.isPaused() else self.request.pause()

    def _update(self):
        r = self.request
        states = r.DownloadState
        if r.state() == states.DownloadCompleted:
            self.state, self.status, self.fraction = "done", "Done", 1.0
        elif r.state() == states.DownloadCancelled:
            self.state, self.status = "cancelled", "Cancelled"
        elif r.state() == states.DownloadInterrupted:
            self.state, self.status = "failed", r.interruptReasonString() or "Interrupted"
        else:
            total = r.totalBytes()
            self.fraction = r.receivedBytes() / total if total > 0 else None
            mb = r.receivedBytes() / 1_000_000
            self.status = ("Paused · " if r.isPaused() else "") + f"{mb:.1f} MB" + (
                f" of {total / 1_000_000:.1f} MB" if total > 0 else "")
        self.changed.emit()


def _file_mime(path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path)])  # (what Finder puts on the clipboard, or drags, for a file)
    return mime


class DownloadRow(QFrame):
    removed = pyqtSignal(object)

    def __init__(self, item: DownloadItem, theme_color):
        super().__init__()
        self.item = item
        self.color = theme_color
        self.setObjectName("DownloadRow")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # (clicked, it takes the space bar: Quick Look)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(qta.icon(item.icon, color=theme_color).pixmap(18, 18))
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        middle = QVBoxLayout()
        middle.setSpacing(3)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)  # (a video's title, from the site)
        self.title.setObjectName("DownloadTitle")
        self.status = QLabel()
        self.status.setObjectName("DownloadStatus")
        self.status.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.setRange(0, 1000)
        middle.addWidget(self.title)
        middle.addWidget(self.bar)
        middle.addWidget(self.status)
        layout.addLayout(middle, 1)
        self.pause = QToolButton()  # (pause and resume, beside Cancel, while a page's download comes)
        self.pause.setObjectName("IconButton")
        self.pause.setIconSize(QSize(16, 16))
        self.pause.clicked.connect(item.toggle_pause)
        layout.addWidget(self.pause, 0, Qt.AlignmentFlag.AlignTop)
        self.action = QToolButton()
        self.action.setObjectName("IconButton")
        self.action.setIconSize(QSize(16, 16))
        self.action.clicked.connect(self._act)
        layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignTop)
        item.changed.connect(self.refresh)
        self._press = None
        self.refresh()

    def _file(self):
        """The finished file, if there is one on disk."""
        path = self.item.path
        return path if self.item.state == "done" and path and os.path.exists(path) else None

    # A finished download can be dragged straight into Finder, Mail, Messages… and double-clicked open
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        path = self._file()
        if (self._press is None or path is None or not event.buttons() & Qt.MouseButton.LeftButton
                or (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance()):
            return
        self._press = None
        drag = QDrag(self)
        drag.setMimeData(_file_mime(path))
        drag.setPixmap(QFileIconProvider().icon(QFileInfo(path)).pixmap(48, 48))
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event):
        self._press = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        path = self._file()
        if path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # Click a finished one and press space for Quick Look (again to close it), as in Finder; right-click for more
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and self._file() is not None:
            self.look()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._file() is not None:
            self.mouseDoubleClickEvent(None)
            return
        super().keyPressEvent(event)

    def look(self):
        self._looking = quick_look(self._file(), getattr(self, "_looking", None))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        path = self._file()
        if path is not None:
            menu.addAction("Open", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
            if sys.platform == "darwin":
                menu.addAction("Quick Look", self.look)
            menu.addAction("Show in Finder" if sys.platform == "darwin" else "Show in Folder",
                           lambda: reveal_in_finder(path))
            menu.addAction("Copy", lambda: QApplication.clipboard().setMimeData(_file_mime(path)))
            menu.addSeparator()
        if self.item.state == "running":
            if self.item.can_pause:
                menu.addAction("Resume" if self.item.paused else "Pause", self.item.toggle_pause)
            menu.addAction("Cancel", self.item.cancel)
        else:
            menu.addAction("Remove from List", lambda: self.removed.emit(self))
        menu.exec(event.globalPos())

    def refresh(self):
        item = self.item
        self.title.setText(self.title.fontMetrics().elidedText(item.title, Qt.TextElideMode.ElideMiddle, 380))
        self.title.setToolTip(item.path or item.title)
        if self._file():
            self.setToolTip("Drag it into another app, double-click to open it, or press space for Quick Look"
                            if sys.platform == "darwin" else "Drag it into another app, or double-click to open it")
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.status.setText(item.status)
        self.bar.setVisible(item.state == "running")
        if item.fraction is None:
            self.bar.setRange(0, 0)  # busy animation
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(item.fraction * 1000))
        running = item.state == "running"
        self.pause.setVisible(running and item.can_pause)
        self.pause.setIcon(qta.icon("ph.play-circle" if item.paused else "ph.pause-circle", color=self.color))
        self.pause.setToolTip("Resume" if item.paused else "Pause")
        self.pause.setAccessibleName(self.pause.toolTip())
        if item.paused:
            self.bar.setRange(0, 1000)  # (still: no busy animation while it waits)
        self.action.setVisible(running or (item.state == "done" and bool(item.path)))
        self.action.setIcon(qta.icon("ph.x-circle" if running else "ph.magnifying-glass", color=self.color))
        self.action.setToolTip("Cancel" if running else "Show in Finder" if sys.platform == "darwin" else "Show in Folder")

    def _act(self):
        if self.item.state == "running":
            self.item.cancel()
        elif self.item.path:
            reveal_in_finder(self.item.path)


# --- Settings ----------------------------------------------------------------------------------

def _combo(options, current):
    box = QComboBox()
    for value, label in options:
        box.addItem(label, value)
    box.setCurrentIndex(max(0, box.findData(current)))
    return box


def _quality_label(h):
    return "Best available" if not h else f"Up to {h}p" + (" (4K)" if h == 2160 else "")


class FolderButton(QPushButton):
    changed = pyqtSignal(str)

    def __init__(self, folder):
        super().__init__()
        self.setObjectName("FolderButton")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(200)
        self.set_folder(folder)
        self.clicked.connect(self._choose)

    def set_folder(self, folder):
        self.folder = folder
        self.setToolTip(folder)
        self._refresh_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self):
        # Long paths shrink in the middle instead of stretching the window
        path = self.folder.replace(str(Path.home()), "~")
        self.setText("📁  " + self.fontMetrics().elidedText(path, Qt.TextElideMode.ElideMiddle, max(self.width() - 50, 120)))

    def _choose(self):
        folder = QFileDialog.getExistingDirectory(self, "Save Downloads To", self.folder)
        if folder:
            self.set_folder(folder)
            self.changed.emit(folder)


class DownloadSettingsForm(QWidget):
    """Your defaults: where downloads go, how files are named, and the usual format choices.

    Used inside the Settings window (saving as you change things) and in the standalone
    Download Settings dialog (saving on Save).
    """

    def __init__(self, store, autosave=False, parent=None):
        super().__init__(parent)
        self.store = store
        opts = ytdlp_settings(store)
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.folder = FolderButton(opts["folder"])
        self.template = QLineEdit(opts["template"])
        template_help = FittedLabel("yt-dlp output template. For example <code>%(uploader)s/%(title)s.%(ext)s</code> "
                               "makes a folder per channel.")
        template_help.setObjectName("Hint")
        template_help.setWordWrap(True)
        # Without this a wrapped hint in a form takes its narrowest width, a word or two per line
        template_help.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.kind = _combo([("video", "Video"), ("audio", "Audio only")], opts["kind"])
        self.video_format = _combo(VIDEO_FORMATS, opts["video_format"])
        self.max_height = _combo([(h, _quality_label(h)) for h in QUALITIES], opts["max_height"])
        self.audio_format = _combo(AUDIO_FORMATS, opts["audio_format"])
        self.subs = QLineEdit(opts["subtitle_langs"])
        self.subs.setPlaceholderText("Language codes, e.g. en, es (blank for none)")
        self.embed_subs = QCheckBox("Embed subtitles in the video file")
        self.embed_subs.setChecked(opts["embed_subs"])
        self.embed_metadata = QCheckBox("Add title, channel and date to the file")
        self.embed_metadata.setChecked(opts["embed_metadata"])
        self.embed_thumbnail = QCheckBox("Add the thumbnail as cover art")
        self.embed_thumbnail.setChecked(opts["embed_thumbnail"])
        self.cookies = _combo(COOKIE_BROWSERS, opts["cookies_from"])
        cookies_help = FittedLabel("Lets yt-dlp use that browser's logins for age-restricted or members-only "
                              "videos. Safari needs Full Disk Access for this.")
        cookies_help.setObjectName("Hint")
        cookies_help.setWordWrap(True)
        cookies_help.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.rate = QLineEdit(opts["rate_limit"])
        self.rate.setPlaceholderText("e.g. 5M (blank for no limit)")

        form.addRow("Save to", self.folder)
        form.addRow("File names", self.template)
        form.addRow("", template_help)
        form.addRow("Download", self.kind)
        form.addRow("Video format", self.video_format)
        form.addRow("Video quality", self.max_height)
        form.addRow("Audio format", self.audio_format)
        form.addRow("Subtitles", self.subs)
        form.addRow("", self.embed_subs)
        form.addRow("", self.embed_metadata)
        form.addRow("", self.embed_thumbnail)
        form.addRow("Cookies from", self.cookies)
        form.addRow("", cookies_help)
        form.addRow("Speed limit", self.rate)

        if autosave:
            self.folder.changed.connect(self.save)
            for edit in (self.template, self.subs, self.rate):
                edit.editingFinished.connect(self.save)
            for combo in (self.kind, self.video_format, self.max_height, self.audio_format, self.cookies):
                combo.currentIndexChanged.connect(self.save)
            for box in (self.embed_subs, self.embed_metadata, self.embed_thumbnail):
                box.toggled.connect(self.save)

    def save(self, *_):
        self.store.settings["ytdlp"] = {
            "folder": self.folder.folder,
            "template": self.template.text().strip() or DEFAULT_SETTINGS["template"],
            "kind": self.kind.currentData(),
            "video_format": self.video_format.currentData(),
            "max_height": self.max_height.currentData(),
            "audio_format": self.audio_format.currentData(),
            "subtitle_langs": self.subs.text().strip(),
            "embed_subs": self.embed_subs.isChecked(),
            "embed_metadata": self.embed_metadata.isChecked(),
            "embed_thumbnail": self.embed_thumbnail.isChecked(),
            "cookies_from": self.cookies.currentData(),
            "rate_limit": self.rate.text().strip(),
        }
        self.store.schedule_save()


class DownloadSettings(QDialog):
    def __init__(self, store, theme, parent):
        super().__init__(parent)
        self.setObjectName("DownloadSettings")
        self.setProperty("panel", True)
        self.setWindowTitle("Download Settings")
        self.setStyleSheet(stylesheet(theme))
        layout = QVBoxLayout(self)
        self.form = DownloadSettingsForm(store)
        layout.addWidget(self.form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda: (self.form.save(), self.accept()))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setFixedWidth(600)


# --- The panel ---------------------------------------------------------------------------------

class DownloadsWindow(FadesIn, QWidget):
    def __init__(self, browser):
        super().__init__(None, Qt.WindowType.Window)
        self.browser = browser
        self.store = browser.store
        self.exe = find_tool("yt-dlp")
        self.items: list[DownloadItem] = []
        self.info: dict | None = None
        self._fetch: InfoFetch | None = None
        self.setObjectName("DownloadsWindow")
        self.setProperty("panel", True)
        self.setWindowTitle("Downloads")
        self.resize(560, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Download from URL")
        title.setObjectName("PanelTitle")
        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("IconButton")
        self.settings_btn.setToolTip("Download settings: folder, file names, default formats")
        self.settings_btn.clicked.connect(self.open_settings)
        header.addWidget(title, 1)
        header.addWidget(self.settings_btn)
        layout.addLayout(header)

        link_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a video or playlist link (YouTube, Vimeo, SoundCloud, …)")
        self.url.returnPressed.connect(self.fetch)
        self.fetch_btn = QPushButton("Get Info")
        self.fetch_btn.setObjectName("Secondary")
        self.fetch_btn.clicked.connect(self.fetch)
        link_row.addWidget(self.url, 1)
        link_row.addWidget(self.fetch_btn)
        layout.addLayout(link_row)

        self.message = QLabel()
        self.message.setObjectName("Hint")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.message)

        self.options = self._build_options()
        layout.addWidget(self.options)

        list_header = QHBoxLayout()
        downloads_title = QLabel("Downloads")
        downloads_title.setObjectName("PanelTitle")
        self.clear_btn = QPushButton("Clear Finished")
        self.clear_btn.setObjectName("Link")
        self.clear_btn.clicked.connect(self.clear_finished)
        list_header.addWidget(downloads_title, 1)
        list_header.addWidget(self.clear_btn)
        layout.addSpacing(6)
        layout.addLayout(list_header)

        self.list_body = QWidget()
        self.list_body.setObjectName("DownloadList")
        self.list_layout = QVBoxLayout(self.list_body)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        # Empty: a quiet picture and a line of help, centred, instead of a bare sentence
        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.setContentsMargins(0, 48, 0, 0)
        ev.setSpacing(6)
        art = QLabel()
        faded = QColor(browser.theme.panel_text)
        faded.setAlphaF(0.3)
        art.setPixmap(qta.icon("ph.download-simple", color=faded).pixmap(44, 44))
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        headline = QLabel("No downloads yet")
        headline.setObjectName("PanelEmptyTitle")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("Files you download appear here. Paste a video link above to save it with yt-dlp.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for widget in (art, headline, hint):
            ev.addWidget(widget)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(self.list_body)
        quiet_scrollbars(scroll)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)

        self.apply_theme()
        self._check_tools()

    def _build_options(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("OptionsCard")
        frame.hide()
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)
        self.info_title = QLabel()
        self.info_title.setTextFormat(Qt.TextFormat.PlainText)
        self.info_title.setObjectName("InfoTitle")
        self.info_title.setWordWrap(True)
        self.info_meta = QLabel()
        self.info_meta.setObjectName("Hint")
        v.addWidget(self.info_title)
        v.addWidget(self.info_meta)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        kind_row = QHBoxLayout()
        self.video_btn = QPushButton("Video")
        self.audio_btn = QPushButton("Audio only")
        self.kind_group = QButtonGroup(self)
        for btn in (self.video_btn, self.audio_btn):
            btn.setObjectName("Segment")
            btn.setCheckable(True)
            self.kind_group.addButton(btn)
            kind_row.addWidget(btn)
        kind_row.addStretch(1)
        self.kind_group.buttonToggled.connect(lambda *_: self._update_kind())
        self.video_format = QComboBox()
        self.quality = QComboBox()
        self.audio_format = QComboBox()
        self.subs = QLineEdit()
        self.subs.setPlaceholderText("Language codes, e.g. en, es (blank for none)")
        self.embed_subs = QCheckBox("Embed in the video")
        self.folder = FolderButton("")
        form.addRow("Type", kind_row)
        form.addRow("Format", self.video_format)
        form.addRow("Quality", self.quality)
        form.addRow("Format", self.audio_format)
        form.addRow("Subtitles", self.subs)
        form.addRow("", self.embed_subs)
        form.addRow("Save to", self.folder)
        self._form = form
        v.addLayout(form)

        self.download_btn = QPushButton("Download")
        self.download_btn.setObjectName("Primary")
        self.download_btn.clicked.connect(self.start_download)
        v.addWidget(self.download_btn, 0, Qt.AlignmentFlag.AlignRight)
        return frame

    def apply_theme(self):
        theme = self.browser.theme
        self.setStyleSheet(stylesheet(theme))
        self.settings_btn.setIcon(qta.icon("ph.gear-six", color=theme.panel_text))
        for row in self.findChildren(DownloadRow):
            row.color = theme.panel_text
            row.refresh()

    def _check_tools(self):
        if not self.exe:
            self.message.setText(f"yt-dlp isn't installed. Install it ({install_hint('yt-dlp', 'ffmpeg')}), "
                                 "then reopen this window.")
            self.fetch_btn.setEnabled(False)
        elif not find_tool("ffmpeg"):
            self.message.setText("ffmpeg isn't installed, so video and audio can't be combined or converted. "
                                 f"Install it with: {install_hint('ffmpeg')}")

    # --- Opening ---------------------------------------------------------------------------

    def open(self, url=""):
        self.show()
        self.raise_()
        self.activateWindow()
        if url and url.startswith(("http://", "https://")):
            self.url.setText(url)
            self.fetch()
        else:
            self.url.setFocus()

    def open_settings(self):
        DownloadSettings(self.store, self.browser.theme, self).exec()
        if self.info is not None:
            self._show_options(self.info)  # re-apply the new defaults to the open form

    # --- Step 1: describe the link -----------------------------------------------------------

    def fetch(self):
        url = self.url.text().strip()
        if not url or not self.exe:
            return
        if self._fetch is not None:
            self._fetch.cancel()
        self.options.hide()
        self.info = None
        self.message.setText("Looking at that link…")
        self.fetch_btn.setEnabled(False)
        self._fetch = InfoFetch(self.exe, url, ytdlp_settings(self.store)["cookies_from"], self)
        self._fetch.done.connect(self._fetched)

    def _fetched(self, info, error):
        self.fetch_btn.setEnabled(True)
        self._fetch.deleteLater()
        self._fetch = None
        if info is None:
            self.message.setText(error)
            return
        if info["playlist"] and not info["count"]:
            self.message.setText("That's a channel's main page. Open its Videos tab (or a playlist) and paste that link.")
            return
        self.message.setText("")
        info["url"] = info.get("url") or self.url.text().strip()
        self.info = info
        self._show_options(info)

    # --- Step 2: options ----------------------------------------------------------------------

    def _show_options(self, info):
        opts = ytdlp_settings(self.store)
        self.info_title.setText(info["title"])
        if info["playlist"]:
            meta = [f"Playlist · {info['count']} videos", info.get("uploader") or ""]
        else:
            meta = [info.get("uploader") or "", _duration(info.get("duration"))]
            if info.get("subtitles"):
                meta.append("Subtitles: " + ", ".join(info["subtitles"][:8]) + ("…" if len(info["subtitles"]) > 8 else ""))
        self.info_meta.setText("  ·  ".join(m for m in meta if m))

        has_video = info["playlist"] or info["has_video"]
        self.video_btn.setEnabled(has_video)
        (self.video_btn if opts["kind"] == "video" and has_video else self.audio_btn).setChecked(True)

        self.video_format.clear()
        for value, label in VIDEO_FORMATS:
            self.video_format.addItem(label, value)
        self.video_format.setCurrentIndex(max(0, self.video_format.findData(opts["video_format"])))
        self.quality.clear()
        heights = [h for h in QUALITIES[1:] if info["playlist"] or any(x >= h for x in info.get("heights", []))]
        for h in [0] + heights:
            self.quality.addItem(_quality_label(h), h)
        self.quality.setCurrentIndex(max(0, self.quality.findData(opts["max_height"])))
        self.audio_format.clear()
        for value, label in AUDIO_FORMATS:
            self.audio_format.addItem(label, value)
        self.audio_format.setCurrentIndex(max(0, self.audio_format.findData(opts["audio_format"])))
        self.subs.setText(opts["subtitle_langs"])
        self.embed_subs.setChecked(opts["embed_subs"])
        self.folder.set_folder(opts["folder"])
        self.download_btn.setText(f"Download {info['count']} Videos" if info["playlist"] else "Download")
        self._update_kind()
        self.options.show()

    def _update_kind(self):
        video = self.video_btn.isChecked()
        for widget, visible in ((self.video_format, video), (self.quality, video), (self.embed_subs, video),
                                (self.audio_format, not video)):
            self._form.setRowVisible(widget, visible)

    def start_download(self):
        info = self.info
        if info is None:
            return
        opts = {**ytdlp_settings(self.store),
                "kind": "video" if self.video_btn.isChecked() else "audio",
                "video_format": self.video_format.currentData(),
                "max_height": self.quality.currentData() or 0,
                "audio_format": self.audio_format.currentData(),
                "subtitle_langs": self.subs.text().strip(),
                "embed_subs": self.embed_subs.isChecked(),
                "folder": self.folder.folder}
        self.add(YtDlpDownload(self.exe, opts, info["url"], info["title"],
                               playlist_count=info["count"] if info["playlist"] else 0))
        self.options.hide()
        self.info = None
        self.url.clear()

    # --- The list ------------------------------------------------------------------------------

    def add(self, item: DownloadItem):
        item.setParent(self)
        self.items.insert(0, item)
        self.empty.hide()
        row = DownloadRow(item, self.browser.theme.panel_text)
        row.removed.connect(self._remove)
        self.list_layout.insertWidget(0, row)
        item.changed.connect(lambda: self._announce(item))
        self._badge()

    def _announce(self, item):
        self._badge()
        if item.state == "done" and not getattr(item, "_announced", False):
            item._announced = True
            self.browser.toast(f"Downloaded {item.title}")
            if QApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
                QApplication.alert(self.browser, 0)  # (one bounce of the Dock icon: it's ready, while you're elsewhere)

    def _badge(self):
        """The Dock icon counts the downloads still coming ("↓2"; unread messages from your sites come first)."""
        count = self.active_count
        if count != getattr(self, "_badged", 0):
            self._badged = count
            macos.set_dock_badge(f"↓{count}" if count else "", "downloads")

    def _remove(self, row):
        if row.item in self.items:
            self.items.remove(row.item)
        row.hide()
        row.deleteLater()
        self.empty.setVisible(not self.items)

    def clear_finished(self):
        for row in self.findChildren(DownloadRow):
            if row.item.state != "running":
                self._remove(row)

    @property
    def active_count(self) -> int:
        return sum(1 for i in self.items if i.state == "running")

    def shutdown(self):
        macos.set_dock_badge("", "downloads")
        for item in self.items:
            if isinstance(item, YtDlpDownload):
                item.cancel()
                item.proc.waitForFinished(2000)
