# Simple Chrome extensions, run by our own runtime instead of Qt's (which crashes on macOS).
#
# What works: content scripts and CSS, chrome.storage, runtime/tabs messaging, i18n, toolbar
# popups with badges, options pages, background scripts and service workers (run as hidden
# pages), alarms and notifications. What doesn't: anything that needs Chromium's internals,
# like webRequest, declarativeNetRequest, scripting, offscreen documents or native messaging.
# Extensions that rely on those are flagged as "may not work" when installed.
#
# Extension files are served from pbext://<id>/…, a private URL scheme registered at startup.

import fnmatch
import io
import json
import mimetypes
import re
import shutil
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtWebEngineCore import (QWebEngineScript, QWebEngineUrlRequestJob, QWebEngineUrlScheme,
                                   QWebEngineUrlSchemeHandler)

from .. import storage
from .patterns import match_pattern, matches

SCHEME = b"pbext"
BACKGROUND_PAGE = "__background.html"
META_FILE = "__pb_meta.json"
RUN_AT = {
    "document_start": QWebEngineScript.InjectionPoint.DocumentCreation,
    "document_end": QWebEngineScript.InjectionPoint.DocumentReady,
    "document_idle": QWebEngineScript.InjectionPoint.Deferred,
}
UNSUPPORTED_PERMISSIONS = {
    "webRequestBlocking", "debugger", "sidePanel", "cookies", "management",
    "privacy", "proxy", "tabGroups", "declarativeContent", "history", "bookmarks", "identity", "tabCapture",
    "desktopCapture", "devtools",
}


def register_scheme():
    """Must run before the QApplication is created."""
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalAccessAllowed |
                    QWebEngineUrlScheme.Flag.CorsEnabled | QWebEngineUrlScheme.Flag.FetchApiAllowed |
                    QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored)
    QWebEngineUrlScheme.registerScheme(scheme)


def extensions_dir() -> Path:
    folder = storage.data_dir() / "extensions"
    folder.mkdir(exist_ok=True)
    return folder


@dataclass
class ChromeExtension:
    id: str
    path: Path
    manifest: dict
    enabled: bool = True
    messages: dict = field(default_factory=dict)
    badge: str = ""

    @classmethod
    def load(cls, ext_id: str, path: Path, enabled=True) -> "ChromeExtension":
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8-sig"))
        ext = cls(ext_id, path, manifest, enabled)
        ext.messages = ext._load_messages()
        return ext

    def _load_messages(self) -> dict:
        locales = self.path / "_locales"
        for locale in ("en", "en_US", self.manifest.get("default_locale", "")):
            file = locales / locale / "messages.json" if locale else None
            if file and file.exists():
                try:
                    return {k.lower(): v for k, v in json.loads(file.read_text(encoding="utf-8-sig")).items()}
                except ValueError:
                    pass
        return {}

    def text(self, value: str) -> str:
        """Resolve __MSG_name__ placeholders."""
        return re.sub(r"__MSG_(\w+?)__", lambda m: self.messages.get(m.group(1).lower(), {}).get("message", m.group(0)),
                      value or "")

    @property
    def name(self) -> str:
        return self.text(self.manifest.get("name", self.id))

    @property
    def version(self) -> str:
        return self.manifest.get("version", "")

    @property
    def description(self) -> str:
        return self.text(self.manifest.get("description", ""))

    @property
    def action(self) -> dict:
        return self.manifest.get("action") or self.manifest.get("browser_action") or self.manifest.get("page_action") or {}

    @property
    def popup(self) -> str | None:
        return self.action.get("default_popup") or None

    @property
    def options_page(self) -> str | None:
        return (self.manifest.get("options_ui") or {}).get("page") or self.manifest.get("options_page")

    @property
    def background(self) -> dict:
        return self.manifest.get("background") or {}

    @property
    def unsupported(self) -> list[str]:
        perms = self.manifest.get("permissions", []) + self.manifest.get("optional_permissions", [])
        return sorted({p for p in perms if isinstance(p, str) and p in UNSUPPORTED_PERMISSIONS})

    def has_permission(self, *names) -> bool:
        perms = self.manifest.get("permissions", [])
        return any(name in perms for name in names)

    @property
    def host_patterns(self) -> list[str]:
        """Regexes for the sites this extension may read and change: host permissions (and, as Chrome
        treats them, the sites its content scripts run on)."""
        patterns = list(self.manifest.get("host_permissions", []))
        patterns += [p for p in self.manifest.get("permissions", []) if isinstance(p, str) and ("://" in p or p == "<all_urls>")]
        patterns += [m for cs in self.manifest.get("content_scripts", []) for m in cs.get("matches", [])]
        return [r for r in (match_pattern(p) for p in patterns if isinstance(p, str)) if r]

    def has_host_access(self, url: str) -> bool:
        return matches(url, self.host_patterns)

    def web_accessible(self, path: str, initiator: str = "") -> bool:
        """Whether a website (at `initiator`, its origin) may load this file: web_accessible_resources, MV2 or
        MV3 form, where an entry's "matches" say which sites. The runtime's own files never are, whatever a
        pattern like "*" says (they'd tell any site which extensions you have, and all their messages)."""
        if path in (BACKGROUND_PAGE, META_FILE):
            return False
        entries = self.manifest.get("web_accessible_resources") or []
        globs = []
        for entry in entries:
            if isinstance(entry, str):
                globs.append(entry)
            elif isinstance(entry, dict):
                sites = [r for r in (match_pattern(p) for p in entry.get("matches", []) if isinstance(p, str)) if r]
                if "matches" in entry and not (initiator and matches(initiator.rstrip("/") + "/", sites)):
                    continue  # (exposed to other sites, not this one)
                globs += [g for g in entry.get("resources", []) if isinstance(g, str)]
        return any(fnmatch.fnmatchcase(path, g.lstrip("/")) for g in globs)

    @property
    def icon_file(self) -> Path | None:
        icons = self.manifest.get("icons") or {}
        default = self.action.get("default_icon")
        if isinstance(default, str):
            icons = {"16": default, **icons}
        elif isinstance(default, dict):
            icons = {**default, **icons}
        for size in sorted(icons, key=lambda s: -int(s) if str(s).isdigit() else 0):
            file = self.path / str(icons[size]).lstrip("/")
            if file.exists():
                return file
        return None

    @property
    def commands(self) -> list[tuple[str, str, str]]:
        """(name, Qt key sequence, description) for each keyboard shortcut the manifest suggests."""
        out = []
        for name, spec in (self.manifest.get("commands") or {}).items():
            keys = (spec.get("suggested_key") or {})
            # This system's suggestion, as Chrome picks it (a Mac's can use ⌘ and the Control key, Windows' can't)
            system = {"darwin": "mac", "win32": "windows"}.get(sys.platform, "linux")
            key = keys.get(system) or keys.get("default") if isinstance(keys, dict) else None
            if not key:
                continue
            # Chrome's names → Qt's (on a Mac, Qt's "Ctrl" is ⌘ and "Meta" is the Control key)
            parts = [{"Command": "Ctrl", "MacCtrl": "Meta", "Comma": ",", "Period": ".", "Space": "Space"}.get(p, p)
                     for p in key.split("+")]
            description = self.text(spec.get("description", "")) or ("Open " + self.name if name == "_execute_action" else name)
            out.append((name, "+".join(parts), description))
        return out

    def url(self, path: str) -> str:
        return f"pbext://{self.id}/{path.lstrip('/')}"

    def meta(self) -> dict:
        """What the in-page chrome.* object needs to know about this extension."""
        return {"manifest": self.manifest, "messages": self.messages}

    # --- Scripts ------------------------------------------------------------------------------

    def _read(self, relative: str) -> str:
        file = (self.path / relative.lstrip("/")).resolve()
        if not file.is_relative_to(self.path.resolve()) or not file.exists():
            return ""
        return file.read_text(encoding="utf-8", errors="replace")

    def content_scripts(self, world=QWebEngineScript.ScriptWorldId.ApplicationWorld) -> list[QWebEngineScript]:
        """The manifest's content scripts; they run in `world`, the extension's own isolated world."""
        scripts = []
        for i, entry in enumerate(self.manifest.get("content_scripts", [])):
            includes = [r for r in (match_pattern(p) for p in entry.get("matches", [])) if r]
            excludes = [r for r in (match_pattern(p) for p in entry.get("exclude_matches", [])) if r]
            js = "\n;\n".join(self._read(f) for f in entry.get("js", []))
            css = "\n".join(self._read(f) for f in entry.get("css", []))
            main_world = entry.get("world") == "MAIN"
            source = f"""(() => {{
  const href = location.href;
  const test = (r) => {{ try {{ return new RegExp(r).test(href); }} catch (e) {{ return false; }} }};
  if (!{json.dumps(includes)}.some(test) || {json.dumps(excludes)}.some(test)) return;
  {"" if entry.get("all_frames") else "if (window.top !== window) return;"}
  {"" if not css else f"const style = document.createElement('style'); style.textContent = {json.dumps(css)}; "
                       "(document.head || document.documentElement).appendChild(style);"}
  {"const chrome = undefined, browser = undefined;" if main_world else
   # One chrome object per world (it's this extension's world alone), also set as the global,
   # since libraries like webextension-polyfill look for globalThis.chrome.runtime.id
   "if (!window.__pbApi) { Object.defineProperty(window, '__pbApi', { value: window.__pbChrome("
   f"{json.dumps(self.id)}, {json.dumps(self.meta())}) }}); "
   "try { Object.defineProperty(window, 'chrome', { value: window.__pbApi, configurable: true, writable: true }); "
   "window.browser = window.__pbApi; } catch (e) {} } "
   "const chrome = window.__pbApi, browser = chrome;"}
  try {{
    (function (chrome, browser) {{
{js}
    }}).call(window, chrome, browser);
  }} catch (e) {{ console.error({json.dumps("[" + self.id + "]")}, e); }}
}})();"""
            script = QWebEngineScript()
            script.setName(f"pbx-ext-{self.id}-{i}")
            script.setSourceCode(source)
            script.setInjectionPoint(RUN_AT.get(entry.get("run_at", "document_idle"),
                                                QWebEngineScript.InjectionPoint.Deferred))
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld if main_world else world)
            script.setRunsOnSubFrames(bool(entry.get("all_frames")))
            scripts.append(script)
        return scripts

    def background_html(self) -> str:
        """A page that runs the background scripts (MV2) or service worker (MV3)."""
        bg = self.background
        files = bg.get("scripts") or ([bg["service_worker"]] if bg.get("service_worker") else [])
        module = ' type="module"' if bg.get("type") == "module" else ""
        tags = "".join(f'<script src="/{f.lstrip("/")}"{module}></script>' for f in files)
        # Service workers can call importScripts(); give them a synchronous stand-in
        shim = ("<script>self.importScripts = (...urls) => urls.forEach((u) => { const x = new XMLHttpRequest(); "
                "x.open('GET', new URL(u, location.href).href, false); x.send(); (0, eval)(x.responseText); });</script>")
        return f"<!doctype html><meta charset=utf-8><title>{self.name}</title>{shim}{tags}"


# --- Serving extension files ----------------------------------------------------------------------

class ExtensionScheme(QWebEngineUrlSchemeHandler):
    def __init__(self, host):
        super().__init__(host)
        self.host = host

    def requestStarted(self, job: QWebEngineUrlRequestJob):
        url = job.requestUrl()
        ext = self.host.extensions.get(url.host())
        if ext is None or not ext.enabled:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        path = url.path().lstrip("/") or "index.html"
        # Websites (and other extensions) only get the files this one chose to expose; its own pages,
        # and pages you open yourself, get everything
        initiator = job.initiator()
        own = initiator.scheme() == "pbext" and initiator.host() == ext.id
        from_browser = initiator.isEmpty()  # typed or opened by us; an opaque origin ("null") is a website
        if not own and not from_browser and not ext.web_accessible(path, initiator.toString()):
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return
        if path == BACKGROUND_PAGE:
            data, mime = ext.background_html().encode(), "text/html"
        elif path == META_FILE:  # read by the runtime as an extension page starts
            data, mime = json.dumps(ext.meta()).encode(), "application/json"
        else:
            file = (ext.path / path).resolve()
            if not file.is_relative_to(ext.path.resolve()) or not file.is_file():  # no escaping the folder
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            data = file.read_bytes()
            mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
            if file.suffix in (".js", ".mjs"):
                mime = "text/javascript"
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime.encode(), buffer)


# --- Installing -------------------------------------------------------------------------------------

def _extension_id(source: Path, store_id: str) -> str:
    """A Chrome Web Store id, but only from a route that vouches for it (a download from the store by
    that id, our own update check, Arc's own folder). Anything else is a local extension: a file name
    or folder name proves nothing, and must never take over an installed extension or its data."""
    if store_id:
        if not re.fullmatch(r"[a-p]{32}", store_id):
            raise ValueError(f"not a Chrome Web Store id: {store_id}")
        return store_id
    return local_id(ChromeExtension.load("", source).name)  # localized: "__MSG_appName__" becomes the name


def local_id(name: str) -> str:
    """The id of an extension installed from a file or folder ("-" keeps it apart from store ids)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return "local-" + (slug or "extension")[:40]


def install_folder(source: Path, store_id: str = "") -> str:
    """Copy an unpacked extension into our data folder; returns its id."""
    ext_id = _extension_id(source, store_id)
    dest = extensions_dir() / ext_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("_metadata", ".DS_Store"))
    return ext_id


def crx_zip_bytes(data: bytes) -> bytes:
    """The zip inside a .crx (v2 or v3), or the data itself if it's already a zip."""
    if data[:4] != b"Cr24":
        return data
    version = struct.unpack("<I", data[4:8])[0]
    if version == 2:
        key_len, sig_len = struct.unpack("<II", data[8:16])
        return data[16 + key_len + sig_len:]
    header_len = struct.unpack("<I", data[8:12])[0]
    return data[12 + header_len:]


def _archive_root(names) -> str:
    return "" if "manifest.json" in names else next((n[:-len("manifest.json")] for n in names
                                                     if n.endswith("/manifest.json")), "")


def archive_preview(data: bytes) -> ChromeExtension:
    """Read a .crx/.zip's manifest (and its localized name) without installing it."""
    archive = zipfile.ZipFile(io.BytesIO(crx_zip_bytes(data)))
    root = _archive_root(archive.namelist())
    manifest = json.loads(archive.read(root + "manifest.json").decode("utf-8-sig"))
    ext = ChromeExtension("", Path(), manifest)
    for locale in ("en", "en_US", manifest.get("default_locale", "")):
        try:
            messages = json.loads(archive.read(f"{root}_locales/{locale}/messages.json").decode("utf-8-sig"))
            ext.messages = {k.lower(): v for k, v in messages.items()}
            break
        except (KeyError, ValueError):
            continue
    return ext


def install_archive(data: bytes, store_id: str = "") -> str:
    """Install from a .crx or .zip; returns the extension id."""
    archive = zipfile.ZipFile(io.BytesIO(crx_zip_bytes(data)))
    names = archive.namelist()
    root = _archive_root(names)
    incoming = extensions_dir() / ".incoming"
    if incoming.exists():
        shutil.rmtree(incoming)
    for name in names:
        if name.startswith(root) and not name.endswith("/"):
            target = (incoming / name[len(root):]).resolve()
            if target.is_relative_to(incoming.resolve()):  # no "../" tricks in the archive
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
    try:
        return install_folder(incoming, store_id)
    finally:
        shutil.rmtree(incoming, ignore_errors=True)


def web_store_id(text: str) -> str | None:
    m = re.search(r"\b([a-p]{32})\b", text or "")
    return m.group(1) if m else None


def web_store_download_url(ext_id: str) -> str:
    return ("https://clients2.google.com/service/update2/crx?response=redirect&prodversion=140.0"
            f"&acceptformat=crx2,crx3&x=id%3D{ext_id}%26uc")


def arc_extensions() -> list[tuple[str, Path]]:
    """(id, newest version folder) for each extension installed in Arc."""
    root = Path.home() / "Library" / "Application Support" / "Arc" / "User Data" / "Default" / "Extensions"
    found = []
    for folder in sorted(root.glob("*")) if root.exists() else []:
        versions = sorted((v for v in folder.iterdir() if (v / "manifest.json").exists()), key=lambda v: v.name)
        if versions:
            found.append((folder.name, versions[-1]))
    return found
