# Payment cards: kept once, filled into checkouts after Touch ID, the way Safari's AutoFill does. Kept in Settings →
# Payment Cards.
#
# Like addresses (addresses.py), a script in each page, in a world of its own the page can't see into, recognizes a
# form's card fields (by the autocomplete="cc-…" names sites are meant to use, or else by their names). Click into
# one and a card offers your saved cards; picking one asks for Touch ID (or the Mac's password) every time, then fills
# the number, the name on it and when it expires into the empty fields. The security code is never kept, nor filled:
# you type it. After you pay with a card that isn't saved, a card offers to keep it (never in a Private window).
#
# Checkouts often have their card fields in a frame of the payment company's (Stripe, Braintree, Adyen…): those are
# filled too, as are frames of the page's own site. A frame from anywhere else (an ad) never is. Only on https pages.
#
# Kept encrypted (cards.json), with the same key as your passwords, in the Keychain where only Eclipse can read it, so
# your recovery PIN and backups cover cards too. Messages from the script go straight to the browser, never the
# console, with a token made fresh each launch, so a page can't pretend to be the script.

import json
import os
import re
import secrets
import time

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import QPushButton

from . import macos, password_store, prompts, recovery, storage
from .features import Feature
from .passkeys import PageCard, _Answer, _escape, _gone
from .webview import CARD_CALL

SCRIPT_NAME = "eclipse-cards"
SETTING = "fill_cards"
SAVE_SETTING = "offer_to_save_cards"
SEAL = b"eclipse-cards-v1"
WORLD = 1  # (QWebEngineScript.ScriptWorldId.ApplicationWorld)
# The payment companies whose frames hold a checkout's card fields (as a site and the sites under it)
PAYMENT_SITES = ("stripe.com", "stripe.network", "braintreegateway.com", "braintree-api.com", "adyen.com",
                 "adyenpayments.com", "checkout.com", "squareup.com", "squarecdn.com", "paypal.com",
                 "recurly.com", "chargebee.com", "authorize.net", "shopifycs.com", "shopify.com", "klarna.com")
BRANDS = [("Visa", r"4"), ("Mastercard", r"5[1-5]|2[2-7]"), ("American Express", r"3[47]"),
          ("Discover", r"6011|65|64[4-9]"), ("JCB", r"35"), ("Diners Club", r"3(0[0-5]|[68])"),
          ("UnionPay", r"62")]

SOURCE = r"""
(() => {
  if (window.__eclipseCards) return;
  const TOKEN = %(token)s, MARK = %(mark)s, say = console.debug.bind(console);
  const framed = window.top !== window;
  const tell = data => say(MARK + JSON.stringify({token: TOKEN, ...data, ...(framed ? {frame: location.href} : {})}));
  // Only a field you can see is filled (as addresses.py: a page can't hide one beside the one you click)
  const shown = el => {
    const box = el.getBoundingClientRect();
    if (box.width < 4 || box.height < 4) return false;
    if (box.right + scrollX <= 0 || box.bottom + scrollY <= 0) return false;
    if (box.left >= document.documentElement.clientWidth) return false;
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) < 0.1) return false;
      if (style.clipPath && style.clipPath !== 'none' && node === el) return false;
      // (nor cut off by what it's in: a box no taller than nothing that hides what overflows it)
      if (node !== el && /(hidden|clip|scroll|auto)/.test(style.overflow + style.overflowX + style.overflowY)) {
        const around = node.getBoundingClientRect();
        const across = Math.min(box.right, around.right) - Math.max(box.left, around.left);
        const down = Math.min(box.bottom, around.bottom) - Math.max(box.top, around.top);
        if (across < 4 || down < 4) return false;
      }
    }
    return true;
  };
  const BY_AUTOCOMPLETE = {'cc-number': 'number', 'cc-name': 'name', 'cc-exp': 'exp', 'cc-exp-month': 'month',
    'cc-exp-year': 'year', 'cc-csc': 'csc', 'cc-given-name': 'given', 'cc-family-name': 'family'};
  const BY_NAME = [['csc', /cvc|cvv|csc|security.?code|card.?code|cid\b/i],
    ['number', /card.?(number|num|no)\b|cc.?(number|num)|cardnumber|ccnumber|^(cc|card)$|kreditkarte|numero.?(de.?)?tarjeta/i],
    ['exp', /mm\s*\/?\s*yy|exp(iry|iration)?.?date|valid.?thru/i],  // (one field for both, before either alone)
    ['month', /exp.*(month|mm\b)|(month|mm).*exp|cc.?month|card.?month/i],
    ['year', /exp.*(year|yy)|(year|yy).*exp|cc.?year|card.?year/i],
    ['exp', /expir|exp.?date|mm.?\/?.?yy|valid.?thru|^exp$|cc.?exp/i],
    ['name', /name.?on.?card|card.?holder|holder.?name|cc.?name|card.?name|nameoncard/i]];
  const kindOf = el => {
    if (el.disabled || el.readOnly || !shown(el)) return null;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['password', 'hidden', 'search', 'checkbox', 'radio', 'submit', 'button', 'file', 'email'].includes(type)) return null;
    const auto = (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).filter(Boolean);
    for (const word of auto) if (BY_AUTOCOMPLETE[word]) return BY_AUTOCOMPLETE[word];
    const called = [el.name, el.id, el.getAttribute('aria-label'), el.placeholder, el.getAttribute('data-elements-stable-field-name')]
      .filter(Boolean).join(' ');
    for (const [kind, pattern] of BY_NAME) if (pattern.test(called)) return kind;
    return null;
  };
  const fieldsOf = scope => [...scope.querySelectorAll('input, select')].map(el => [el, kindOf(el)]).filter(([, kind]) => kind);
  const scopeOf = el => el.form || el.closest('fieldset, section, [role=form], [class*=payment], [class*=card]') || document;
  // A part of a page with card fields: its number (or, in a payment company's frame, any of them: each of its
  // fields can be a frame of its own)
  const cardy = scope => fieldsOf(scope).some(([, kind]) => kind === 'number' || (framed && kind !== 'csc'));
  let scope = null;
  const offered = new WeakSet();
  const pressed = event => {
    const el = event.target;
    if (!el || !el.matches || !el.matches('input, select') || !kindOf(el) || kindOf(el) === 'csc') return;
    const here = scopeOf(el);
    if (!cardy(here)) return;
    scope = here;
    if (!offered.has(here)) { offered.add(here); tell({kind: 'form'}); }
  };
  document.addEventListener('focusin', pressed, true);
  document.addEventListener('pointerdown', pressed, true);
  const set = (el, value) => {
    if (el.tagName === 'SELECT') {
      const wanted = String(value).toLowerCase().replace(/^0/, '');
      const option = [...el.options].find(o => [o.value, o.text.trim()].some(t => {
        const v = t.toLowerCase();
        // (a month's "4" or "04"; a year's "2031", or "31" in a menu of two-digit years)
        return v === wanted || v.replace(/^0/, '') === wanted || (wanted.length === 4 && v.length === 2 && v === wanted.slice(-2));
      }));
      if (!option) return false;
      el.value = option.value;
    } else {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value);
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  };
  const valueFor = (el, kind, card) => {
    if (kind === 'number') return card.number;
    if (kind === 'name') return card.name;
    if (kind === 'given') return card.name.split(' ')[0] || '';
    if (kind === 'family') return card.name.split(' ').slice(1).join(' ');
    if ((kind === 'month' || kind === 'year' || kind === 'exp') && !(card.year && card.month)) return '';  // (not known)
    if (kind === 'month') return el.tagName === 'SELECT' ? String(card.month) : String(card.month).padStart(2, '0');
    if (kind === 'year') {
      const max = parseInt(el.getAttribute('maxlength') || '0', 10);
      return el.tagName === 'SELECT' || max !== 2 ? String(card.year) : String(card.year).slice(-2);
    }
    if (kind === 'exp') {
      const max = parseInt(el.getAttribute('maxlength') || '0', 10);
      const mm = String(card.month).padStart(2, '0');
      return max >= 7 ? mm + ' / ' + String(card.year).slice(-2) : mm + '/' + String(card.year).slice(-2);
    }
    return '';  // (the security code: never)
  };
  Object.defineProperty(window, '__eclipseCards', {value: (token, card) => {
    if (token !== TOKEN) return 0;
    // (the page's own document: only the form you clicked into, never every card field on it; a payment company's
    // frame holds just its card fields, one of them each, as often as not)
    const where = scope || (framed ? document : null);
    if (!where) return 0;
    let filled = 0;
    for (const [el, kind] of fieldsOf(where)) {
      const value = valueFor(el, kind, card);
      if (value && !el.value && set(el, value)) filled++;  // (never over something already typed)
    }
    return filled;
  }});
  // Paid with a card that isn't saved: said once (the browser offers to keep it), from the form's submit, or in a
  // payment company's frame, where there's no form to submit, once all of it's in and you've moved on
  const luhn = digits => {
    let sum = 0;
    for (let i = 0; i < digits.length; i++) {
      let d = +digits[digits.length - 1 - i];
      if (i %% 2) { d *= 2; if (d > 9) d -= 9; }
      sum += d;
    }
    return sum %% 10 === 0;
  };
  const told = new Set();
  const entered = here => {
    const card = {number: '', name: '', month: '', year: ''};
    for (const [el, kind] of fieldsOf(here)) {
      const value = (el.value || '').trim();
      if (kind === 'number') card.number = value.replace(/[\s-]/g, '');
      else if (kind === 'name') card.name = value;
      else if (kind === 'given') card.name = (value + ' ' + card.name).trim();
      else if (kind === 'family') card.name = (card.name + ' ' + value).trim();
      else if (kind === 'month') card.month = value;
      else if (kind === 'year') card.year = value;
      else if (kind === 'exp') { const m = value.match(/(\d{1,2})\s*\/?\s*-?\s*(\d{2,4})/); if (m) { card.month = m[1]; card.year = m[2]; } }
    }
    if (!/^\d{13,19}$/.test(card.number) || !luhn(card.number) || told.has(card.number)) return;
    told.add(card.number);
    tell({kind: 'entered', card});
  };
  document.addEventListener('submit', event => event.target && cardy(event.target) && entered(event.target), true);
  if (framed) document.addEventListener('focusout', event => {
    const el = event.target;
    if (el && el.matches && el.matches('input') && kindOf(el)) setTimeout(() => entered(scopeOf(el)), 600);
  }, true);
})();
"""


# --- Kept ---------------------------------------------------------------------------------------------------

class Locked(Exception):
    """The cards can't be opened: the passwords' key isn't here (another Mac's copy: the recovery PIN brings it)."""


def _path():
    return storage.data_dir() / "cards.json"


def _key(create=False) -> bytes:
    key = password_store._key(create=create)
    if key is None:
        raise Locked()
    return key


def all_cards() -> list[dict]:
    """Every saved card, opened: [{"id", "number", "name", "month", "year", "label", "created", "used"}]."""
    saved = storage.read_json(_path())
    if not saved:
        return []
    try:
        return json.loads(recovery.unseal(_key(), saved, SEAL))["cards"]
    except Locked:
        raise
    except Exception as error:  # (a key that isn't theirs)
        raise Locked() from error


def summaries() -> list[dict]:
    """The saved cards without their numbers (but the last four): what a card on a page, or Settings, shows."""
    return [{k: v for k, v in card.items() if k != "number"} | {"last4": card["number"][-4:],
                                                                "brand": brand(card["number"])}
            for card in all_cards()]


def _save(cards: list[dict]):
    storage.write_json(_path(), recovery.seal(_key(create=True), json.dumps({"cards": cards}).encode(), SEAL),
                       private=True)


def digits_of(number: str) -> str:
    return re.sub(r"[\s-]", "", str(number))


def valid_number(number: str) -> bool:
    digits = digits_of(number)
    if not re.fullmatch(r"\d{13,19}", digits):
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def brand(number: str) -> str:
    digits = digits_of(number)
    return next((name for name, prefix in BRANDS if re.match(prefix, digits)), "Card")


def normal_year(year) -> int:
    try:
        year = int(str(year).strip())
    except ValueError:
        return 0
    return year + 2000 if year < 100 else year


def save(fields: dict, card_id: str | None = None) -> dict:
    """Add a card, or change the one with that id. The number must be a real one (it passes the Luhn check)."""
    number = digits_of(fields.get("number", ""))
    if not valid_number(number):
        raise ValueError("That isn't a card number")
    cards = all_cards()
    found = next((c for c in cards if c["id"] == card_id), None)
    if found is None:
        found = next((c for c in cards if c["number"] == number), None)  # (the same card again: that one)
    if found is None:
        found = {"id": os.urandom(6).hex(), "created": time.time(), "used": 0}
        cards.append(found)
    try:
        month = int(str(fields.get("month", "")).strip() or 0)
    except ValueError:
        month = 0
    year = normal_year(fields.get("year", ""))
    if not (1 <= month <= 12 and year):
        month, year = 0, 0  # (not known: when it expires is left for you to type, never filled made up)
    found.update(number=number, name=str(fields.get("name", "")).strip()[:100], month=month, year=year,
                 label=str(fields.get("label", "")).strip()[:60], changed=time.time())
    _save(cards)
    return found


def delete(card_id: str):
    _save([c for c in all_cards() if c["id"] != card_id])


def mark_used(card_id: str):
    cards = all_cards()
    for card in cards:
        if card["id"] == card_id:
            card["used"] = time.time()
    _save(cards)


def known(number: str) -> bool:
    try:
        return any(c["number"] == digits_of(number) for c in all_cards())
    except Locked:
        return True  # (can't tell: never offered again over a locked store)


def summary(card: dict) -> str:
    """One line: "Work · Visa ••••4242, expires 04/28"."""
    last4 = card.get("last4") or digits_of(card.get("number", ""))[-4:]
    kind = card.get("brand") or brand(card.get("number", ""))
    expires = f", expires {int(card['month']):02d}/{str(card['year'])[-2:]}" if card.get("month") and card.get("year") else ""
    text = f"{kind} ••••{last4}{expires}"
    return f"{card['label']} · {text}" if card.get("label") else text


def for_page(card: dict) -> dict:
    return {"number": card["number"], "name": card.get("name", ""), "month": int(card.get("month") or 0),
            "year": int(card.get("year") or 0)}


def expired(card: dict, now=None) -> bool:
    now = time.localtime(now if now is not None else time.time())
    year, month = int(card.get("year") or 0), int(card.get("month") or 0)
    return bool(year) and (year, month) < (now.tm_year, now.tm_mon)


# --- On pages -----------------------------------------------------------------------------------------------

def script(token: str) -> QWebEngineScript:
    s = QWebEngineScript()
    s.setName(SCRIPT_NAME)
    s.setSourceCode(SOURCE % {"token": json.dumps(token), "mark": json.dumps(CARD_CALL)})
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    s.setRunsOnSubFrames(True)  # (a checkout's card fields are often in a payment company's frame)
    return s


def _secure(url: QUrl) -> bool:
    host = url.host().lower()
    return url.scheme() == "https" or (url.scheme() == "http" and (host == "localhost" or host.endswith(".localhost")))


def payment_frame(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in PAYMENT_SITES)


def frame_allowed(page_url: QUrl, frame_url: QUrl) -> bool:
    """A frame's card fields may be filled: an https frame of the page's own site, or of a payment company's."""
    return _secure(frame_url) and (password_store.same_site(frame_url.host(), page_url.host())
                                   or payment_frame(frame_url.host()))


def _frames(frame):
    yield frame
    for child in frame.children():
        yield from _frames(child)


class FillCard(PageCard):
    """A checkout's card fields: your saved cards, to fill it with one (after Touch ID)."""

    def __init__(self, manager, view, host, cards, frame=None):
        super().__init__(manager, view, host)
        self.frame = frame
        site = view.url().host().removeprefix("www.")
        self._set_icon("ph.credit-card")
        self._text(f"Pay with a saved card on <b>{_escape(site)}</b>? You'll type its security code yourself.")
        for card in cards[:4]:
            button = QPushButton(summary(card) + (" (expired)" if expired(card) else ""))
            button.setObjectName("PermissionAllow")
            button.clicked.connect(lambda _=False, chosen=card: manager.fill(self, chosen))
            self.body.addWidget(button)
        self._buttons(("Not Now", lambda: manager.close(self), False))
        self.arm()
        self.place()


class SaveCard(PageCard):
    """Paid with a card that isn't saved: keep it for next time?"""

    def __init__(self, manager, view, host, card):
        super().__init__(manager, view, host)
        self.card = card
        self._set_icon("ph.credit-card")
        self._text(f"Save {_escape(summary(card))} for next time? Its security code isn't kept.")
        self._buttons(("Not Now", lambda: manager.close(self), False), ("Save", lambda: manager.keep(self), True))
        self.arm()
        self.place()


class Cards(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.token = secrets.token_hex(16)  # (new each launch: a page can't know it)
        self._cards = {}

    @property
    def settings(self):
        return self.browser.store.settings

    def attach_profile(self, profile):
        scripts = profile.scripts()
        for old in scripts.find(SCRIPT_NAME):
            scripts.remove(old)
        scripts.insert(script(self.token))

    def attach(self, view):
        view.page().card_messages = lambda message, v=view: self._said(v, message)
        view.page().loadStarted.connect(lambda v=view: self._left(v))
        view.destroyed.connect(lambda _=None, key=id(view): self._cards.pop(key, None))

    def _said(self, view, message, tries=0):
        try:
            said = json.loads(message[len(CARD_CALL):])
        except ValueError:
            return
        if not isinstance(said, dict) or not secrets.compare_digest(str(said.get("token", "")), self.token):
            return  # (not our script: a page making it up)
        if view.url().isEmpty() and tries < 20:
            QTimer.singleShot(50, lambda: not _gone(view) and self._said(view, message, tries + 1))
            return
        page_url = view.url()
        frame = str(said["frame"]) if said.get("frame") else None
        if not _secure(page_url) or (frame is not None and not frame_allowed(page_url, QUrl(frame))):
            return  # (an http page, or a frame from anywhere but the page's site or a payment company's)
        kind = said.get("kind")
        if kind == "form" and self.settings.get(SETTING, True) and id(view) not in self._cards:
            try:
                cards = summaries()
            except Locked:
                return
            if cards:
                cards.sort(key=lambda c: (expired(c), -(c.get("used") or 0)))
                self._cards[id(view)] = FillCard(self, view, self._host(view), cards, frame)
        elif kind == "entered" and isinstance(said.get("card"), dict) and self.settings.get(SAVE_SETTING, True):
            entered = said["card"]
            if view.page().profile().isOffTheRecord() or id(view) in self._cards:
                return  # (a Private window keeps nothing)
            number = digits_of(entered.get("number", ""))
            if not valid_number(number) or known(number):
                return
            card = {"number": number, "name": str(entered.get("name", ""))[:100],
                    "month": str(entered.get("month", "")), "year": str(entered.get("year", ""))}
            self._cards[id(view)] = SaveCard(self, view, self._host(view), card)

    def _host(self, view):
        return self.browser.permissions._host_for(view)

    def fill(self, card, chosen):
        """After Touch ID (or the Mac's password), every time: the card's number into the page (or its frames)."""
        if not macos.can_verify_owner():
            self._fill(card, chosen)
            return
        answer = _Answer(self)
        answer.answered.connect(lambda ok: ok and self._fill(card, chosen))
        site = card.view.url().host().removeprefix("www.")
        if not macos.verify_owner(f"fill your card on {site}", answer.answered.emit):
            self.browser.toast("This Mac couldn't check it's you")

    def _fill(self, card, chosen):
        if _gone(card) or self._cards.get(id(card.view)) is not card:
            return
        view = card.view
        if not _secure(view.url()):
            self.close(card)
            return
        try:
            full = next((c for c in all_cards() if c["id"] == chosen["id"]), None)
        except Locked:
            full = None
        if full is None:
            self.close(card)
            return
        call = (f"window.__eclipseCards && window.__eclipseCards({json.dumps(self.token)}, "
                f"{json.dumps(for_page(full))})")
        page_url = view.url()
        asked_in = QUrl(card.frame) if card.frame else None
        # Only where it was asked for: the page and the payment company's frames beside its form, or (asked from a
        # payment company's frame) that company's frames alone, never the merchant's page, which a card from there
        # is kept from. Looked at again now: the page may have moved on while you touched the sensor
        for frame in _frames(view.page().mainFrame()):
            if frame.isMainFrame():
                wanted = asked_in is None
            elif asked_in is not None:
                wanted = frame_allowed(page_url, frame.url()) and password_store.same_site(frame.url().host(),
                                                                                           asked_in.host())
            else:
                wanted = frame_allowed(page_url, frame.url())
            if wanted:
                frame.runJavaScript(call, WORLD)
        if not view.page().profile().isOffTheRecord():
            mark_used(full["id"])
        self.close(card)

    def keep(self, card):
        try:
            save(card.card)
        except (Locked, ValueError, OSError) as error:
            self.browser.toast(f"Couldn't save the card: {error}" if not isinstance(error, Locked) else
                               "Couldn't save the card: your passwords are locked on this Mac (enter your "
                               "recovery PIN in Passwords & Passkeys)")
        else:
            self.browser.toast(f"Saved {summary(card.card)}")
        self.close(card)

    def _left(self, view):
        card = self._cards.get(id(view))
        if card is not None:
            self.close(card)

    def close(self, card):
        if self._cards.get(id(card.view)) is card:
            del self._cards[id(card.view)]
        if not _gone(card):
            card.host_widget.removeEventFilter(card)
            card.deleteLater()

    def card_for(self, view):
        return self._cards.get(id(view))


# --- Settings → Payment Cards ------------------------------------------------------------------------------------

def _card_form(parent, browser, on_done, card=None):
    """Add a card, or change one: on_done(its fields). A native sheet where it can be, else Qt's dialog."""
    from . import forms
    this_year = time.localtime().tm_year
    years = sorted({*range(this_year, this_year + 15), *([int(card["year"])] if card and card.get("year") else [])})
    fields = [{"id": "number", "kind": "text", "label": "Card number", "value": (card or {}).get("number", ""),
               "placeholder": "1234 5678 9012 3456"},
              {"id": "name", "kind": "text", "label": "Name on card", "value": (card or {}).get("name", "")},
              {"id": "month", "kind": "menu", "label": "Expires",
               "value": str((card or {}).get("month", 1)),
               "options": [{"value": "0", "label": "—"}] + [{"value": str(m), "label": f"{m:02d}"} for m in range(1, 13)]},
              {"id": "year", "kind": "menu", "label": "", "value": str((card or {}).get("year", this_year)),
               "options": [{"value": "0", "label": "—"}] + [{"value": str(y), "label": str(y)} for y in years if y]},
              {"id": "label", "kind": "text", "label": "Label", "value": (card or {}).get("label", ""),
               "placeholder": "Personal, Work…"}]
    form = forms.open_form(browser, "Edit Card" if card else "Add Card",
                           [forms.section(*fields, footer="The security code isn't kept: you type it each time.")],
                           forms.save_buttons(), lambda _, values: on_done({k["id"]: str(values.get(k["id"], ""))
                                                                           for k in fields}),
                           width=440, height=420, on_page=forms.native_page_of(parent))
    if form is not None:
        return form
    from PyQt6.QtWidgets import QInputDialog
    number, ok = QInputDialog.getText(parent, "Add Card", "Card number:", text=(card or {}).get("number", ""))
    if ok:
        expires, ok = QInputDialog.getText(parent, "Add Card", "Expires (MM/YY):")
        month, _, year = expires.partition("/")
        on_done({"number": number, "month": month, "year": year, "name": "", "label": ""})
    return None


def settings_page(settings):
    from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget
    browser = settings.browser
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)

    def saved(fields, card_id=None):
        try:
            save(fields, card_id)
        except ValueError as error:
            prompts.tell(settings, "Card Not Saved", f"{error}: check the number and try again.", warning=True)
        except Locked:
            prompts.tell(settings, "Card Not Saved", "Your saved passwords are locked on this Mac: enter your "
                         "recovery PIN in Passwords & Passkeys first.", warning=True)
        fill()

    def add():
        _card_form(settings, browser, saved)

    def edit(card):
        # (its full number shown only after Touch ID, as filling it would)
        def open_it():
            try:
                full = next((c for c in all_cards() if c["id"] == card["id"]), None)
            except Locked:
                return
            if full is not None:
                _card_form(settings, browser, lambda fields: saved(fields, full["id"]), full)
        if not macos.can_verify_owner():
            open_it()
            return
        answer = _Answer(settings)
        answer.answered.connect(lambda ok: ok and open_it())
        macos.verify_owner("show your card's number", answer.answered.emit)

    def remove(card):
        def gone():
            delete(card["id"])
            fill()
        prompts.confirm(settings, "Remove Card", f"Remove {summary(card)}?", gone, action="Remove", destructive=True)

    def fill():
        while outer.count():
            item = outer.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        rows = []
        for key, title, detail in ((SETTING, "Offer to fill in your cards", "On a checkout, a card offers your "
                                    "saved cards; picking one asks for Touch ID, every time."),
                                   (SAVE_SETTING, "Offer to save cards", "After you pay with a card that isn't "
                                    "saved. Never in a Private window.")):
            switch = QCheckBox()
            switch.setChecked(browser.store.settings.get(key, True))
            switch.toggled.connect(lambda on, k=key: (browser.store.settings.__setitem__(k, on),
                                                      browser.store.schedule_save()))
            rows.append(settings._row(title, detail, switch))
        add_button = QPushButton("Add…")
        add_button.clicked.connect(add)
        rows.append(settings._row("Your cards", "Encrypted, with the key to them in the Keychain, as your passwords "
                                  "are. Security codes are never kept.", add_button))
        try:
            cards = summaries()
        except Locked:
            cards = None
            locked = QLabel("Locked on this Mac: enter your recovery PIN in Passwords & Passkeys to open them.")
            locked.setObjectName("Hint")
            rows.append(locked)
        for card in cards or []:
            actions = QWidget()
            a = QHBoxLayout(actions)
            a.setContentsMargins(0, 0, 0, 0)
            for label, act in (("Edit", lambda _=False, x=card: edit(x)), ("Remove", lambda _=False, x=card: remove(x))):
                button = QPushButton(label)
                button.clicked.connect(act)
                a.addWidget(button)
            rows.append(settings._row(summary(card) + (" (expired)" if expired(card) else ""),
                                      card.get("name") or None, actions))
        if cards == []:
            empty = QLabel("None yet. Add one, or save one after you pay, and checkouts are a Touch ID away.")
            empty.setObjectName("Hint")
            empty.setContentsMargins(0, 8, 0, 8)
            rows.append(empty)
        outer.addWidget(settings._card(rows))
        outer.addStretch(1)
    fill()
    return page


class CardsFeature(Feature):
    name = "cards"

    def __init__(self, window):
        super().__init__(window)
        self.cards = Cards(window)

    def profile_ready(self, profile):
        self.cards.attach_profile(profile)

    def view_created(self, view, tab):
        self.cards.attach(view)

    def settings_pages(self):
        return [("cards", "ph.credit-card", "Payment Cards", settings_page, "downloads")]


FEATURE = CardsFeature
