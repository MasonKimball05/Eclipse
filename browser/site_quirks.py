# Sites that need to see a different browser to work.
#
# Google's sign-in refuses browsers built on an engine inside an app ("This browser or app may not be secure"),
# going by small signs that Eclipse isn't Chrome itself, though it says it's Chrome. It signs in Firefox
# without fuss, so on its sign-in pages (only those) Eclipse says it's Firefox: in what it sends (the
# User-Agent header, set by the request filter: adblock.py) and in what the page's own scripts can ask
# (navigator's details), so the two agree. qutebrowser, another Qt browser, does the same.

FIREFOX_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0"
GOOGLE_SIGN_IN = ("accounts.google.com", "accounts.youtube.com")


def user_agent_for(host: str) -> str | None:
    """The browser this host should be told about, or None for Eclipse's own."""
    host = host.lower()
    return FIREFOX_UA if host in GOOGLE_SIGN_IN else None


# In the page's own world, before its scripts: the same browser the header said
SCRIPT = r"""
(() => {
  if (!%(hosts)s.includes(location.hostname)) return;
  const nav = Navigator.prototype, ua = %(ua)s;
  const set = (name, value) => Object.defineProperty(nav, name, {get: () => value, configurable: true});
  set('userAgent', ua);
  set('appVersion', ua.slice(ua.indexOf('/') + 1));
  set('vendor', '');
  set('platform', 'MacIntel');
  set('userAgentData', undefined);  // (Firefox has none)
  set('webdriver', false);
  try { delete window.chrome; } catch (error) {}
})();
"""


def script_source() -> str:
    import json
    return SCRIPT % {"hosts": json.dumps(list(GOOGLE_SIGN_IN)), "ua": json.dumps(FIREFOX_UA)}
