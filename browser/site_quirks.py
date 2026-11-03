# Google's sign-in, and the browser it's told it is.
#
# It refuses browsers it takes for an engine inside an app ("This browser or app may not be secure"), going by
# whether what a browser says about itself hangs together: the User-Agent header, what the page's scripts can
# read (navigator's userAgent and userAgentData), and the client hints (sec-ch-ua) Chromium sends on every
# secure request, which name "Chromium" and can't be taken off. Eclipse elsewhere leaves "QtWebEngine" out of
# its User-Agent (webview.look_like_chrome); here it says what the engine itself says, so all three agree.
#
# (It used to say it was Firefox here. That stopped working: a Firefox User-Agent beside Chromium's client hints
# doesn't hang together. Orca, an Electron browser, found the same: told the engine's own identity, Google's
# sign-in let it through where Firefox's or a cleaned Chrome's was refused.)

GOOGLE_SIGN_IN = ("accounts.google.com", "accounts.youtube.com")
_engine_agent = None  # (the engine's own User-Agent, before Eclipse cleans it: webview.look_like_chrome)


def remember_engine_agent(agent: str):
    global _engine_agent
    if _engine_agent is None and "QtWebEngine" in agent:
        _engine_agent = agent


def user_agent_for(host: str) -> str | None:
    """The User-Agent this host should be told, or None for Eclipse's own."""
    return _engine_agent if host.lower() in GOOGLE_SIGN_IN else None


# In the page's own world, before its scripts: the same browser the header said
SCRIPT = r"""
(() => {
  const ua = %(ua)s;
  if (!ua || !%(hosts)s.includes(location.hostname)) return;
  const nav = Navigator.prototype;
  const set = (name, value) => Object.defineProperty(nav, name, {get: () => value, configurable: true});
  set('userAgent', ua);
  set('appVersion', ua.slice(ua.indexOf('/') + 1));
})();
"""


def script_source() -> str:
    import json
    return SCRIPT % {"hosts": json.dumps(list(GOOGLE_SIGN_IN)), "ua": json.dumps(_engine_agent or "")}
