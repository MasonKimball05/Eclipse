# Cookie banners, answered for you: "Reject all" (or "Only necessary"), never "Accept".
#
# The common consent tools (OneTrust, Cookiebot, Didomi, Usercentrics, Google's, Quantcast,
# Sourcepoint, TrustArc, CookieYes, Osano, Complianz…) get their own reject button clicked. Anything
# else gets a careful fallback: a button whose whole label is a known "reject" phrase, inside
# something that's plainly a cookie banner. It keeps looking for a while after the page loads
# (banners arrive late), clicks at most a few times, and never follows a link to another page.
#
# It runs in the application world (the page can't see or tamper with it), in frames too, since
# several consent tools live in an iframe. Sites you've allowed ads on are left alone, and it can be
# turned off in Settings → Privacy.

import json

from PyQt6.QtWebEngineCore import QWebEngineScript

SCRIPT_NAME = "eclipse-cookie-banners"
SETTING = "reject_cookie_banners"

# Each consent tool's own "reject" button
KNOWN_BUTTONS = [
    "#onetrust-reject-all-handler", ".ot-pc-refuse-all-handler",
    "#CybotCookiebotDialogBodyButtonDecline", "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll",
    "#didomi-notice-disagree-button", ".didomi-continue-without-agreeing",
    "button[data-testid='uc-deny-all-button']",
    ".fc-cta-do-not-consent", ".fc-button.fc-cta-do-not-consent",
    ".sp_choice_type_REJECT_ALL", "button[title='Reject All']", "button[title='Reject all']",
    "#truste-consent-required", ".truste-button2",
    ".cky-btn-reject", ".osano-cm-denyAll", ".cmplz-deny", "#cmpbntnotxt", ".cm-btn-decline",
    "[data-cookiefirst-action='reject']", ".iubenda-cs-reject-btn", "#tarteaucitronAllDenied2",
    ".cc-deny", ".js-cookie-consent-reject", "[data-testid='cookie-policy-manage-dialog-btn-reject-all']",
    "button[aria-label='Reject all']", "button[aria-label='Decline optional cookies']",
]
# Whole button labels that mean "no" (lower case, no punctuation)
REJECT_LABELS = [
    "reject all", "reject all cookies", "reject", "reject cookies", "reject optional cookies",
    "decline", "decline all", "decline optional cookies", "decline cookies", "deny", "deny all",
    "only necessary", "necessary only", "only necessary cookies", "use necessary cookies only",
    "only essential", "essential only", "only essential cookies", "accept only essential cookies",
    "accept necessary cookies only", "allow essential cookies only", "refuse", "refuse all",
    "do not consent", "dont consent", "disagree", "i do not agree", "continue without accepting",
    "no thanks", "alle ablehnen", "ablehnen", "nur notwendige", "nur notwendige cookies",
    "nur erforderliche cookies", "tout refuser", "refuser", "continuer sans accepter",
    "rechazar todo", "rechazar todas", "rechazar", "rifiuta tutto", "rifiuta", "alles weigeren",
    "weigeren", "rejeitar todos", "rejeitar", "odrzuć wszystkie", "avvisa alla", "afvis alle",
]

SOURCE = r"""
(() => {
  if (window.__pbCookies || location.protocol === 'pbext:') return;
  window.__pbCookies = true;
  const site = location.hostname.replace(/^www\./, '');
  if (%(allowed)s.some((s) => site === s || site.endsWith('.' + s))) return;
  const KNOWN = %(known)s, LABELS = new Set(%(labels)s);
  // Names consent tools give their banners. Not "privacy" (privacy settings pages) or a bare "cmp"
  // (Adobe AEM calls every component cmp-…): "cmp" only as a whole name, like #cmpbox or .cmp
  const BANNER = /cookie|consent|gdpr|didomi|onetrust|usercentrics|truste|qc-cmp|sp_message|fc-consent|cmpbox|cmpwrapper|(^|[\s_])cmp($|[\s_])/i;
  const ABOUT_COOKIES = /cookie|consent|gdpr|tracking technolog|datenschutz|données|rastreo/i;
  let clicks = 0;
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 &&
    getComputedStyle(el).visibility !== 'hidden'; };
  const label = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '')
    .toLowerCase().replace(/[’']/g, '').replace(/[^\p{L}\p{N} ]+/gu, ' ').replace(/\s+/g, ' ').trim();
  const safe = (el) => {
    if (el.tagName !== 'A') return true;
    const href = el.getAttribute('href') || '';
    return !href || href === '#' || href.startsWith('#') || href.startsWith('javascript:');
  };
  const inBanner = (el) => {
    for (let e = el, i = 0; e && i < 12; e = e.parentElement || (e.getRootNode && e.getRootNode().host), i++) {
      // A consent tool's name on the container, and it actually talks about cookies
      if (BANNER.test((e.id || '') + ' ' + (typeof e.className === 'string' ? e.className : '') + ' ' +
                      (e.getAttribute && (e.getAttribute('aria-label') || ''))) &&
          ABOUT_COOKIES.test((e.textContent || '').slice(0, 4000))) return true;
      if (e.getAttribute && e.getAttribute('role') === 'dialog' && /cookie/i.test(e.textContent.slice(0, 3000))) return true;
    }
    return /cookie/i.test(document.title) && window !== top;  // a consent tool's own frame
  };
  const roots = () => {
    const out = [document];
    for (const host of document.querySelectorAll('#usercentrics-root, #cmpwrapper, [id^=sp_message], aside[id*=usercentrics]'))
      if (host.shadowRoot) out.push(host.shadowRoot);
    return out;
  };
  const press = (el) => { clicks++; el.click(); };
  const attempt = () => {
    if (clicks >= 3) return true;
    for (const root of roots()) {
      for (const selector of KNOWN) {
        const el = root.querySelector(selector);
        if (el && visible(el) && safe(el)) { press(el); return true; }
      }
    }
    for (const root of roots()) {
      // (never a submit button: that could send a form)
      for (const el of root.querySelectorAll('button:not([type=submit]), [role=button], a, input[type=button]')) {
        if (LABELS.has(label(el)) && visible(el) && safe(el) && inBanner(el)) { press(el); return true; }
      }
    }
    return false;
  };
  const start = () => {
    let tries = 0;
    const tick = () => { if (!attempt() && ++tries < 30) setTimeout(tick, tries < 10 ? 500 : 1500); };
    tick();
    // A banner that shows up later (after scrolling, say) still gets answered
    new MutationObserver(() => { if (clicks < 3) { clearTimeout(window.__pbCookiesLater);
      window.__pbCookiesLater = setTimeout(attempt, 300); } }).observe(document.documentElement, {childList: true, subtree: true});
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', start) : start();
})();
"""


def script(allowed_sites) -> QWebEngineScript:
    source = SOURCE % {"allowed": json.dumps(sorted(allowed_sites)), "known": json.dumps(KNOWN_BUTTONS),
                       "labels": json.dumps(REJECT_LABELS)}
    s = QWebEngineScript()
    s.setName(SCRIPT_NAME)
    s.setSourceCode(source)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    s.setRunsOnSubFrames(True)  # Sourcepoint, TrustArc and others show theirs in a frame
    return s


def install(profile, enabled, allowed_sites):
    scripts = profile.scripts()
    for old in scripts.find(SCRIPT_NAME):
        scripts.remove(old)
    if enabled:
        scripts.insert(script(allowed_sites))
