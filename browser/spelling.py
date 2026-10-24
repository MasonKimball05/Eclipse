# Spell check while you type: misspelled words in text fields get the red underline, and right-clicking
# one offers corrections.
#
# Qt WebEngine checks spelling with Chromium's Hunspell dictionaries (it can't use the Mac's own
# spell checker), and doesn't come with any. Turning spell check on downloads your language's
# dictionary once (a few hundred KB) from the same place Chrome gets its own, into the data folder.
# Nothing is sent anywhere: the checking happens on this Mac.

import os
from pathlib import Path

from PyQt6.QtCore import QLocale, QObject, QUrl, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from . import storage

SETTING = "spell_check"
SOURCE = "https://redirector.gvt1.com/edgedl/chrome/dict/"
# Chrome's dictionary for each language (name and version, as Chrome publishes them)
DICTIONARIES = {
    "en-US": "en-US-10-1", "en-GB": "en-GB-10-1", "en-AU": "en-AU-10-1", "en-CA": "en-CA-10-1",
    "es-ES": "es-ES-3-0", "fr-FR": "fr-FR-3-0", "de-DE": "de-DE-3-0", "it-IT": "it-IT-3-0",
    "pt-BR": "pt-BR-3-0", "nl-NL": "nl-NL-3-0",
}
# A language without its own dictionary uses its closest relative
FALLBACK = {"en": "en-US", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT", "pt": "pt-BR", "nl": "nl-NL"}
MAGIC = b"BDic"


def folder() -> Path:
    # Wherever Qt WebEngine was told to look (prepare() sets it), so the two always agree
    configured = os.environ.get("QTWEBENGINE_DICTIONARIES_PATH")
    return Path(configured) if configured else storage.data_dir() / "dictionaries"


def prepare():
    """Before the QApplication exists: tell Qt WebEngine where dictionaries live."""
    path = storage.data_dir() / "dictionaries"
    path.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("QTWEBENGINE_DICTIONARIES_PATH", str(path))


def language(locale_name: str | None = None) -> str | None:
    """The dictionary for a locale like "en_US" (this Mac's, by default), or None if there isn't one."""
    name = (locale_name or QLocale.system().name()).replace("_", "-")
    if name in DICTIONARIES:
        return name
    return FALLBACK.get(name.split("-")[0])


def path_for(lang: str):
    return folder() / f"{lang}.bdic"  # the name Qt looks for


def installed(lang: str) -> bool:
    try:
        with open(path_for(lang), "rb") as f:
            return f.read(4) == MAGIC
    except OSError:
        return False


def apply(profiles, settings) -> bool:
    """Turn checking on (or off) in every profile. True if it's on."""
    lang = language(settings.get("spell_check_language"))
    on = bool(settings.get(SETTING, False) and lang and installed(lang))
    for profile in profiles:
        if on:
            profile.setSpellCheckLanguages([lang])
        profile.setSpellCheckEnabled(on)
    return on


class DictionaryDownload(QObject):
    """Fetches one language's dictionary. finished(ok, message)."""

    finished = pyqtSignal(bool, str)

    def __init__(self, lang, parent=None, source=None):
        super().__init__(parent)
        self.lang = lang
        self._network = QNetworkAccessManager(self)
        request = QNetworkRequest(QUrl((source or SOURCE) + DICTIONARIES[lang].lower() + ".bdic"))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        request.setTransferTimeout(60_000)
        self._reply = self._network.get(request)
        self._reply.finished.connect(self._done)

    def _done(self):
        reply = self._reply
        reply.deleteLater()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self.finished.emit(False, "Couldn't download the dictionary. Check your connection and try again.")
            return
        data = bytes(reply.readAll())
        if not data.startswith(MAGIC) or len(data) < 10_000:
            self.finished.emit(False, "The dictionary that came back wasn't a dictionary.")
            return
        target = path_for(self.lang)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".part")
        part.write_bytes(data)
        part.replace(target)
        self.finished.emit(True, f"Downloaded the {QLocale(self.lang.replace('-', '_')).nativeLanguageName()} "
                                 "dictionary")
