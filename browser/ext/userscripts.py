# Userscripts (the Greasemonkey / Tampermonkey format): a single .user.js file with a
# metadata block describing which sites it runs on and which GM_* APIs it needs.
#
# Each script is turned into a QWebEngineScript. Scripts that use GM_* APIs run in an
# isolated world of their own (see host.py), so pages and other scripts can't reach them. Scripts with "@grant none" run in the page itself, like
# Tampermonkey does, so they can see the page's own JavaScript objects.

import json
import re
from dataclasses import asdict, dataclass, field

from PyQt6.QtWebEngineCore import QWebEngineScript

from .patterns import glob_pattern, match_pattern

RUN_AT = {
    "document-start": QWebEngineScript.InjectionPoint.DocumentCreation,
    "document-body": QWebEngineScript.InjectionPoint.DocumentReady,
    "document-end": QWebEngineScript.InjectionPoint.DocumentReady,
    "document-idle": QWebEngineScript.InjectionPoint.Deferred,
}
# Grants we provide. Anything else is listed as unsupported in the install dialog.
SUPPORTED_GRANTS = {
    "none", "GM_info", "GM.info", "GM_getValue", "GM.getValue", "GM_setValue", "GM.setValue",
    "GM_deleteValue", "GM.deleteValue", "GM_listValues", "GM.listValues", "GM_addStyle", "GM.addStyle",
    "GM_xmlhttpRequest", "GM.xmlHttpRequest", "GM_openInTab", "GM.openInTab", "GM_setClipboard",
    "GM.setClipboard", "GM_notification", "GM.notification", "unsafeWindow", "window.close", "window.focus",
}

_BLOCK = re.compile(r"//\s*==UserScript==(.*?)//\s*==/UserScript==", re.S)
_LINE = re.compile(r"^\s*//\s*@([\w:.-]+)(?:\s+(.*?))?\s*$")


def parse_metadata(source: str) -> dict[str, list[str]]:
    block = _BLOCK.search(source)
    meta: dict[str, list[str]] = {}
    for line in (block.group(1).splitlines() if block else []):
        m = _LINE.match(line)
        if m:
            meta.setdefault(m.group(1), []).append((m.group(2) or "").strip())
    return meta


@dataclass
class Userscript:
    id: str
    name: str
    source: str                                   # the whole .user.js file
    namespace: str = ""
    version: str = ""
    description: str = ""
    matches: list[str] = field(default_factory=list)   # @match + @include
    excludes: list[str] = field(default_factory=list)  # @exclude + @exclude-match
    run_at: str = "document-end"
    grants: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    noframes: bool = False
    origin: str = ""                              # where it was installed from (for updates)
    enabled: bool = True

    @property
    def uses_gm(self) -> bool:
        return any(g not in ("none", "") for g in self.grants)

    @property
    def unsupported_grants(self) -> list[str]:
        return [g for g in self.grants if g not in SUPPORTED_GRANTS]

    @property
    def include_regexes(self) -> list[str]:
        out = []
        for pattern in self.matches:
            out.append(match_pattern(pattern) or glob_pattern(pattern))
        return out or [r"^(https?|file)://.*$"]  # no @match/@include: every page

    @property
    def exclude_regexes(self) -> list[str]:
        return [match_pattern(p) or glob_pattern(p) for p in self.excludes]

    def to_record(self) -> dict:
        record = asdict(self)
        del record["source"]  # the source lives in its own .user.js file
        return record


def script_id(name: str, namespace: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{namespace} {name}".lower()).strip("-")
    return slug[:80] or "userscript"


def from_source(source: str, origin: str = "") -> Userscript:
    meta = parse_metadata(source)
    first = lambda key, default="": (meta.get(key) or [default])[0]
    name = first("name") or "Untitled script"
    return Userscript(
        id=script_id(name, first("namespace")),
        name=name, source=source, namespace=first("namespace"), version=first("version"),
        description=first("description"),
        matches=meta.get("match", []) + meta.get("include", []),
        excludes=meta.get("exclude", []) + meta.get("exclude-match", []),
        run_at=first("run-at", "document-end") if first("run-at", "document-end") in RUN_AT else "document-end",
        grants=meta.get("grant", []) or ["none"],
        requires=meta.get("require", []),
        noframes="noframes" in meta,
        origin=origin or first("downloadURL") or first("updateURL"),
    )


def build_script(script: Userscript, required_code: str, values: dict, world: int | None = None) -> QWebEngineScript:
    """The QWebEngineScript that runs `script` on matching pages. Scripts using GM_* APIs run in
    `world`, their own isolated world (where the host installed the runtime)."""
    # The metadata block is removed: Qt would otherwise apply its own reading of @match/@run-at
    body = _BLOCK.sub("", script.source)
    info = {"script": {"name": script.name, "namespace": script.namespace, "version": script.version,
                       "description": script.description, "matches": script.matches},
            "scriptHandler": "Halo", "version": "1.0"}
    if script.uses_gm:
        setup = (f"const {{GM, GM_info, GM_getValue, GM_setValue, GM_deleteValue, GM_listValues, GM_addStyle, "
                 f"GM_xmlhttpRequest, GM_openInTab, GM_setClipboard, GM_notification, unsafeWindow}} = "
                 f"window.__pbGM({json.dumps(script.id)}, {json.dumps(info)}, {json.dumps(values)});")
    else:
        setup = f"const GM_info = {json.dumps(info)}; const GM = {{info: GM_info}};"
    source = f"""(() => {{
  const href = location.href;
  const includes = {json.dumps(script.include_regexes)}, excludes = {json.dumps(script.exclude_regexes)};
  const test = (r) => {{ try {{ return new RegExp(r).test(href); }} catch (e) {{ return false; }} }};
  if (!includes.some(test) || excludes.some(test)) return;
  {"if (window.top !== window) return;" if script.noframes else ""}
  {setup}
  try {{
{required_code}
{body}
  }} catch (e) {{ console.error({json.dumps("[" + script.name + "]")}, e); }}
}})();"""
    qscript = QWebEngineScript()
    qscript.setName("pbx-userscript-" + script.id)
    qscript.setSourceCode(source)
    qscript.setInjectionPoint(RUN_AT[script.run_at])
    if script.uses_gm:
        qscript.setWorldId(world if world is not None else QWebEngineScript.ScriptWorldId.ApplicationWorld)
    else:
        qscript.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    qscript.setRunsOnSubFrames(not script.noframes)
    return qscript
