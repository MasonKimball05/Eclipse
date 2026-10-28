# Addresses: your name, email, phone and address, filled into forms (a checkout, a sign-up) from a card, the way
# Safari's and Chrome's AutoFill do. Kept in Settings → Addresses.
#
# A small script in each page, in a world of its own the page can't see into, recognises a form's contact and
# address fields (by the autocomplete="…" names sites are meant to use, or else by their names), and when you
# click into one of a form that asks for at least three such things, it tells Eclipse. A card then offers your
# saved addresses; picking one fills the form's empty fields. Nothing is filled by itself, a page never learns
# what's saved until you pick one, and its messages go straight to the browser (never the console) with a token
# made fresh each launch, so a page can't pretend to be the script.
#
# Kept in addresses.json in the data folder, readable only by your account.

import json
import os
import secrets
import time

from PyQt6.QtCore import QObject
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import QPushButton

from . import storage
from .features import Feature
from .passkeys import PageCard, _escape, _gone
from .webview import ADDRESS_CALL

SCRIPT_NAME = "eclipse-addresses"
SETTING = "fill_addresses"
WORLD = 1  # (QWebEngineScript.ScriptWorldId.ApplicationWorld)
FIELDS = [  # (what's kept, its label in Settings)
    ("label", "Label"), ("name", "Full name"), ("org", "Company"), ("email", "Email"), ("tel", "Phone"),
    ("street", "Street address"), ("city", "City"), ("region", "State or region"), ("postal", "ZIP or postcode"),
    ("country", "Country"),
]

SOURCE = r"""
(() => {
  if (window.__eclipseAddress) return;
  const TOKEN = %(token)s, MARK = %(mark)s, say = console.debug.bind(console);
  const tell = data => say(MARK + JSON.stringify({token: TOKEN, ...data}));
  const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  // What a field asks for: the autocomplete name sites are meant to use, or else what it's called
  const BY_AUTOCOMPLETE = {name: 'name', 'given-name': 'given', 'family-name': 'family', email: 'email',
    tel: 'tel', 'tel-national': 'tel', organization: 'org', 'street-address': 'street', 'address-line1': 'line1',
    'address-line2': 'line2', 'address-level2': 'city', 'address-level1': 'region', 'postal-code': 'postal',
    country: 'country', 'country-name': 'country'};
  const BY_NAME = [['given', /first.?name|fname|given/i], ['family', /last.?name|lname|surname|family/i],
    ['name', /full.?name|^name$|your.?name/i], ['email', /e-?mail/i], ['tel', /phone|mobile|^tel/i],
    ['org', /company|organi[sz]ation/i], ['line2', /address.?(line)?.?2|apartment|suite|addr2|unit/i],
    ['line1', /address.?(line)?.?1|street|addr1|^address$/i], ['city', /city|town|locality/i],
    ['region', /state|province|region|county/i], ['postal', /zip|postal|postcode/i], ['country', /country/i]];
  const kindOf = el => {
    if (el.disabled || el.readOnly || !shown(el)) return null;
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['password', 'hidden', 'search', 'checkbox', 'radio', 'submit', 'button', 'file'].includes(type)) return null;
    const auto = (el.getAttribute('autocomplete') || '').toLowerCase().split(/\s+/).filter(Boolean);
    for (const word of auto) if (BY_AUTOCOMPLETE[word]) return BY_AUTOCOMPLETE[word];
    if (type === 'email') return 'email';
    if (type === 'tel') return 'tel';
    const called = [el.name, el.id, el.getAttribute('aria-label'), el.placeholder].filter(Boolean).join(' ');
    for (const [kind, pattern] of BY_NAME) if (pattern.test(called)) return kind;
    return null;
  };
  const fieldsOf = scope => [...scope.querySelectorAll('input, select, textarea')]
    .map(el => [el, kindOf(el)]).filter(([, kind]) => kind);
  const scopeOf = el => el.form || document;
  // A form that asks for at least three such things (not a newsletter's lone email), and isn't a sign-in
  const worth = scope => !scope.querySelector('input[type=password]') &&
    new Set(fieldsOf(scope).map(([, kind]) => kind)).size >= 3;
  let scope = null;
  const offered = new WeakSet();
  const pressed = event => {
    const el = event.target;
    if (!el || !el.matches || !el.matches('input, select, textarea') || !kindOf(el)) return;
    const here = scopeOf(el);
    if (!worth(here)) return;
    scope = here;
    if (!offered.has(here)) { offered.add(here); tell({kind: 'form'}); }
  };
  document.addEventListener('focusin', pressed, true);
  document.addEventListener('pointerdown', pressed, true);  // (focus doesn't come while the window's in the back)
  const set = (el, value) => {
    if (el.tagName === 'SELECT') {
      const wanted = String(value).toLowerCase();
      const option = [...el.options].find(o => o.value.toLowerCase() === wanted || o.text.trim().toLowerCase() === wanted);
      if (!option) return;
      el.value = option.value;
    } else {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  Object.defineProperty(window, '__eclipseAddress', {value: (token, address) => {
    if (token !== TOKEN || !scope) return 0;
    let filled = 0;
    for (const [el, kind] of fieldsOf(scope)) {
      const value = address[kind];
      if (value && !el.value) { set(el, value); filled++; }  // (never over something already typed)
    }
    return filled;
  }});
})();
"""


# --- Kept ---------------------------------------------------------------------------------------------------

def _path():
    return storage.data_dir() / "addresses.json"


def all_addresses() -> list[dict]:
    return (storage.read_json(_path()) or {}).get("addresses", [])


def _save(addresses):
    storage.write_json(_path(), {"addresses": addresses}, private=True)


def save(fields: dict, address_id: str | None = None) -> dict:
    """Add an address, or change the one with that id."""
    addresses = all_addresses()
    kept = {key: str(fields.get(key, "")).strip()[:500] for key, _ in FIELDS}
    found = next((a for a in addresses if a["id"] == address_id), None)
    if found is None:
        found = {"id": os.urandom(6).hex(), "created": time.time()}
        addresses.append(found)
    found.update(kept, changed=time.time())
    _save(addresses)
    return found


def delete(address_id: str):
    _save([a for a in all_addresses() if a["id"] != address_id])


def for_page(address: dict) -> dict:
    """An address as the page script fills it: the name split for first and last name fields, and the street
    for one field or two lines."""
    name = address.get("name", "").split()
    street = [line.strip() for line in address.get("street", "").splitlines() if line.strip()]
    return {"name": address.get("name", ""), "given": name[0] if name else "",
            "family": " ".join(name[1:]), "email": address.get("email", ""), "tel": address.get("tel", ""),
            "org": address.get("org", ""), "street": ", ".join(street), "line1": street[0] if street else "",
            "line2": ", ".join(street[1:]), "city": address.get("city", ""), "region": address.get("region", ""),
            "postal": address.get("postal", ""), "country": address.get("country", "")}


def summary(address: dict) -> str:
    """One line for a button: "Home · Mason, 1 Infinite Loop"."""
    first_line = (address.get("street", "").splitlines() or [""])[0]
    parts = [p for p in (address.get("name", ""), first_line or address.get("city", "") or address.get("email", ""))
             if p]
    text = ", ".join(parts) or "Address"
    return f"{address['label']} · {text}" if address.get("label") else text


# --- On pages -----------------------------------------------------------------------------------------------

def script(token: str) -> QWebEngineScript:
    s = QWebEngineScript()
    s.setName(SCRIPT_NAME)
    s.setSourceCode(SOURCE % {"token": json.dumps(token), "mark": json.dumps(ADDRESS_CALL)})
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    s.setRunsOnSubFrames(False)
    return s


def _fillable(url) -> bool:
    host = url.host().lower()
    return url.scheme() == "https" or (url.scheme() == "http" and (host == "localhost" or host.endswith(".localhost")))


class AddressCard(PageCard):
    """A form asking for your details: your saved addresses, to fill it with one."""

    def __init__(self, manager, view, host, addresses):
        super().__init__(manager, view, host)
        site = view.url().host().removeprefix("www.")
        self._set_icon("ph.address-book")
        self._text(f"Fill in your details on <b>{_escape(site)}</b>?")
        for address in addresses[:5]:
            button = QPushButton(summary(address))
            button.setObjectName("PermissionAllow")
            button.clicked.connect(lambda _=False, chosen=address: manager.fill(self, chosen))
            self.body.addWidget(button)
        self._buttons(("Not Now", lambda: manager.close(self), False))
        self.arm()
        self.place()


class Addresses(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.token = secrets.token_hex(16)  # (new each launch: a page can't know it)
        self._cards = {}

    def attach_profile(self, profile):
        scripts = profile.scripts()
        for old in scripts.find(SCRIPT_NAME):
            scripts.remove(old)
        scripts.insert(script(self.token))

    def attach(self, view):
        view.page().address_messages = lambda message, v=view: self._said(v, message)
        view.page().loadStarted.connect(lambda v=view: self._left(v))
        view.destroyed.connect(lambda _=None, key=id(view): self._cards.pop(key, None))

    def _said(self, view, message):
        try:
            said = json.loads(message[len(ADDRESS_CALL):])
        except ValueError:
            return
        if not isinstance(said, dict) or not secrets.compare_digest(str(said.get("token", "")), self.token):
            return  # (not our script: a page making it up)
        if (said.get("kind") != "form" or not self.browser.store.settings.get(SETTING, True)
                or not _fillable(view.url()) or id(view) in self._cards):
            return
        saved = all_addresses()
        if saved:
            self._cards[id(view)] = AddressCard(self, view, self.browser.permissions._host_for(view), saved)

    def fill(self, card, address):
        if _gone(card):
            return
        call = (f"window.__eclipseAddress && window.__eclipseAddress({json.dumps(self.token)}, "
                f"{json.dumps(for_page(address))})")
        card.view.page().runJavaScript(call, WORLD)
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


# --- Settings → Addresses -------------------------------------------------------------------------------------

def _address_dialog(parent, address=None):
    """Add an address, or change one: its fields, or None if cancelled."""
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLayout, QLineEdit, QPlainTextEdit, QVBoxLayout
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Address" if address else "Add Address")
    v = QVBoxLayout(dialog)
    v.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
    v.setContentsMargins(20, 18, 20, 16)
    form = QFormLayout()
    edits = {}
    for key, label in FIELDS:
        if key == "street":
            edit = QPlainTextEdit((address or {}).get(key, ""))
            edit.setFixedHeight(56)
            edit.setPlaceholderText("Street, then apartment or suite on a second line")
        else:
            edit = QLineEdit((address or {}).get(key, ""))
            edit.setPlaceholderText({"label": "Home, Work…"}.get(key, ""))
        edit.setMinimumWidth(320)
        edits[key] = edit
        form.addRow(label, edit)
    v.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    v.addWidget(buttons)
    if not dialog.exec():
        return None
    return {key: (edit.toPlainText() if key == "street" else edit.text()) for key, edit in edits.items()}


def settings_page(settings):
    from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget
    browser = settings.browser
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)

    def add():
        made = _address_dialog(settings)
        if made is not None and any(made.values()):
            save(made)
            fill()

    def edit(address):
        changed = _address_dialog(settings, address)
        if changed is not None:
            save(changed, address["id"])
            fill()

    def remove(address):
        if QMessageBox.question(settings, "Remove Address", f"Remove {summary(address)}?") == \
                QMessageBox.StandardButton.Yes:
            delete(address["id"])
            fill()

    def fill():
        while outer.count():
            item = outer.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        switch = QCheckBox()
        switch.setChecked(browser.store.settings.get(SETTING, True))
        switch.toggled.connect(lambda on: (browser.store.settings.__setitem__(SETTING, on),
                                           browser.store.schedule_save()))
        add_button = QPushButton("Add…")
        add_button.clicked.connect(add)
        rows = [settings._row("Offer to fill in your details", "On a form asking for your name, address, email or "
                              "phone, a card offers your saved addresses. Only empty fields are filled.", switch),
                settings._row("Your addresses", "Kept on this Mac, readable only by your account.", add_button)]
        for address in all_addresses():
            actions = QWidget()
            a = QHBoxLayout(actions)
            a.setContentsMargins(0, 0, 0, 0)
            for label, act in (("Edit", lambda _=False, x=address: edit(x)),
                               ("Remove", lambda _=False, x=address: remove(x))):
                button = QPushButton(label)
                button.clicked.connect(act)
                a.addWidget(button)
            details = ", ".join(p for p in (address.get("email", ""), address.get("tel", ""),
                                            address.get("city", "")) if p)
            rows.append(settings._row(summary(address), details or None, actions))
        if not all_addresses():
            empty = QLabel("None yet. Add one, and checkout and sign-up forms can be filled in a click.")
            empty.setObjectName("Hint")
            empty.setContentsMargins(0, 8, 0, 8)
            rows.append(empty)
        outer.addWidget(settings._card(rows))
        outer.addStretch(1)
    fill()
    return page


class AddressesFeature(Feature):
    name = "addresses"

    def __init__(self, window):
        super().__init__(window)
        self.addresses = Addresses(window)

    def profile_ready(self, profile):
        self.addresses.attach_profile(profile)

    def view_created(self, view, tab):
        self.addresses.attach(view)

    def settings_pages(self):
        return [("addresses", "ph.address-book", "Addresses", settings_page, "downloads")]


FEATURE = AddressesFeature
