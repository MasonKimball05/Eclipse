# Passkeys and security keys (WebAuthn): signing in to a site with a key instead of a password.
#
# Two kinds:
#   - Passkeys Eclipse keeps itself (passkey_store.py), like Chrome's in Google Password Manager: made and used
#     here, checked with Touch ID or your Mac's password, kept in the Keychain, and backed up with a recovery
#     PIN. When a site asks, a card offers to save one, or to sign in with one, or to use a security key.
#   - Security keys (YubiKey and the like, over USB). Chromium talks to the key; Qt hands each step of that
#     conversation to the browser as a request that moves through states, and each is a card: which account,
#     the key's PIN (or a new one), "touch your security key", what went wrong with Try Again.
#
# A small script in each page (in the page's own world, wrapping navigator.credentials) hands every passkey
# request to the browser first, which answers it: with one of Eclipse's passkeys, by passing it on to Chromium
# for a security key, or with an error. What a site gets is made here, for the address Qt says the page is at;
# the page script only describes the request. Qt only hands over a security key's steps once one is plugged
# in, so until then the card says to plug one in, with Cancel.
#
# Passkeys kept in iCloud Keychain or on a phone can't be used yet: macOS lets a browser reach those only with
# an entitlement Apple grants browser makers (with a Developer ID), and Qt WebEngine doesn't connect to them.

import hmac
import json
import secrets

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineScript, QWebEngineWebAuthUxRequest
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

import qtawesome as qta

from . import macos, motion, passkey_store, recovery
from .permissions import ARM_MS

R = QWebEngineWebAuthUxRequest
State, Failure = R.WebAuthUxState, R.RequestFailureReason
Pin, PinError = R.PinEntryReason, R.PinEntryError

FAILURES = {
    Failure.Timeout: "The security key didn't answer in time.",
    Failure.KeyNotRegistered: "This security key isn't registered with this site. Try another one.",
    Failure.KeyAlreadyRegistered: "This security key is already registered with this site. Try another one.",
    Failure.SoftPinBlock: "The security key is locked after too many wrong PINs. Unplug it and plug it back in to "
                          "try again.",
    Failure.HardPinBlock: "The security key is locked after too many wrong PINs. It needs to be reset.",
    Failure.AuthenticatorRemovedDuringPinEntry: "The security key was removed. Plug it back in and try again.",
    Failure.AuthenticatorMissingResidentKeys: "This security key can't keep a passkey for this site.",
    Failure.AuthenticatorMissingUserVerification: "This security key can't check it's you (it has no PIN or "
                                                  "fingerprint).",
    Failure.AuthenticatorMissingLargeBlob: "This security key can't keep what this site needs to store on it.",
    Failure.NoCommonAlgorithms: "This security key doesn't support what this site asks for.",
    Failure.StorageFull: "This security key is full. Remove a passkey from it to make room.",
    Failure.UserConsentDenied: "The security key said no.",
    Failure.WinUserCancelled: "Cancelled.",
}
PIN_ERRORS = {
    PinError.WrongPin: "That PIN isn't right.",
    PinError.TooShort: "That PIN is too short.",
    PinError.InvalidCharacters: "That PIN has characters a key can't use.",
    PinError.SameAsCurrentPin: "The new PIN has to be different from the current one.",
    PinError.InternalUvLocked: "Too many tries with the fingerprint: use the PIN instead.",
}


MARK = "__eclipse_passkey__:"  # (how the page script's messages start)
SCRIPT_NAME = "eclipse-passkeys"
TOKEN = secrets.token_hex(16)  # (this launch's: see SOURCE)
# In the page's own world, before the site's scripts: every passkey request goes to the browser first (described:
# challenge, site, account, which passkeys the site will take), and waits for its answer, through
# window.__eclipsePasskeys(id, answer): {credential} (one of Eclipse's: built into the objects a site expects),
# {use: "key"} (on to Chromium, for a security key; Cancel then aborts it), or {error, message}. Passkey
# autofill (mediation: "conditional": a sign-in page waiting in the background for you to pick a passkey)
# goes to the browser too, which only says anything if it has a passkey for the site. Sites ask whether this
# browser has passkeys of its own, and autofill, before offering them: it has both.
SOURCE = r"""
(() => {
  const credentials = navigator.credentials, MARK = %(mark)s, TOKEN = %(token)s, PKC = window.PublicKeyCredential;
  if (!credentials || !PKC || window.__eclipsePasskeys) return;
  const pending = new Map(), originals = {};
  const debug = console.debug.bind(console);  // (kept now: a site may silence its console later)
  // Every message carries this launch's token, which only this closure knows: a frame inside the page (an ad)
  // writes to the same console, but can't pass off a passkey request as the page's
  // (put together now, before the site's scripts run, and only ever added to: no method a site could replace
  // ever sees the token)
  const PREFIX = MARK + '{"token":"' + TOKEN + '",', SKIP = MARK.length + 1;
  const say = text => debug(PREFIX + text.slice(SKIP));
  let next = 0;
  const b64 = value => {
    if (value == null) return null;
    const bytes = value instanceof ArrayBuffer ? new Uint8Array(value)
      : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    let text = '';
    for (const byte of bytes) text += String.fromCharCode(byte);
    return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  const unb64 = text => {
    const raw = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes.buffer;
  };
  const describe = (kind, key) => {
    const chosen = key.authenticatorSelection || {};
    return kind === 'create' ? {
      challenge: b64(key.challenge), rp: {id: key.rp && key.rp.id, name: key.rp && key.rp.name},
      user: {id: b64(key.user.id), name: key.user.name, displayName: key.user.displayName},
      algs: (key.pubKeyCredParams || []).map(p => p.alg), exclude: (key.excludeCredentials || []).map(c => b64(c.id)),
      attachment: chosen.authenticatorAttachment || null, userVerification: chosen.userVerification || 'preferred',
      credProps: !!(key.extensions && key.extensions.credProps)
    } : {
      challenge: b64(key.challenge), rpId: key.rpId || null, allow: (key.allowCredentials || []).map(c => b64(c.id)),
      userVerification: key.userVerification || 'preferred'
    };
  };
  const own = (object, values) => {
    for (const [name, value] of Object.entries(values)) Object.defineProperty(object, name, {value, enumerable: true});
    return object;
  };
  const build = (kind, made) => {
    const data = unb64(made.clientDataJSON);
    let response, json;
    if (kind === 'create') {
      const auth = unb64(made.authenticatorData), spki = unb64(made.publicKey);
      response = own(Object.create(AuthenticatorAttestationResponse.prototype), {
        clientDataJSON: data, attestationObject: unb64(made.attestationObject), getTransports: () => ['internal'],
        getAuthenticatorData: () => auth.slice(0), getPublicKey: () => spki.slice(0), getPublicKeyAlgorithm: () => -7});
      json = {clientDataJSON: made.clientDataJSON, attestationObject: made.attestationObject,
              authenticatorData: made.authenticatorData, transports: ['internal'], publicKey: made.publicKey,
              publicKeyAlgorithm: -7};
    } else {
      response = own(Object.create(AuthenticatorAssertionResponse.prototype), {
        clientDataJSON: data, authenticatorData: unb64(made.authenticatorData), signature: unb64(made.signature),
        userHandle: made.userHandle ? unb64(made.userHandle) : null});
      json = {clientDataJSON: made.clientDataJSON, authenticatorData: made.authenticatorData,
              signature: made.signature, userHandle: made.userHandle || null};
    }
    const extensions = made.credProps ? {credProps: {rk: true}} : {};
    return own(Object.create(PKC.prototype), {
      id: made.id, rawId: unb64(made.id), type: 'public-key', authenticatorAttachment: 'platform', response,
      getClientExtensionResults: () => extensions,
      toJSON: () => ({id: made.id, rawId: made.id, type: 'public-key', authenticatorAttachment: 'platform',
                      clientExtensionResults: extensions, response: json})});
  };
  for (const kind of ['create', 'get']) {
    const original = originals[kind] = credentials[kind].bind(credentials);
    credentials[kind] = function (options) {
      if (!options || !options.publicKey) return original(options);
      const conditional = options.mediation === 'conditional';
      if (conditional && kind !== 'get') return original(options);
      const theirs = options.signal;
      if (theirs && theirs.aborted) return Promise.reject(theirs.reason);
      let described;
      try { described = describe(kind, options.publicKey); } catch (error) { return original(options); }
      const id = ++next;
      return new Promise((resolve, reject) => {
        const request = {kind, options, resolve, reject, controller: null};
        pending.set(id, request);
        if (theirs) theirs.addEventListener('abort', () => {
          if (request.controller) return;  // (with a key: Chromium hears it through our signal)
          pending.delete(id);
          say(MARK + JSON.stringify({id, done: true}));
          reject(theirs.reason);
        });
        say(MARK + JSON.stringify({id, kind, conditional, options: described}));
      });
    };
  }
  Object.defineProperty(window, '__eclipsePasskeys', {value: (id, answer) => {
    const request = pending.get(id);
    if (!request) return;
    if (answer.use === 'key' && !request.controller) {
      const controller = request.controller = new AbortController(), theirs = request.options.signal;
      if (theirs) theirs.addEventListener('abort', () => controller.abort(theirs.reason));
      originals[request.kind]({...request.options, signal: controller.signal})
        .then(request.resolve, request.reject)
        .finally(() => { pending.delete(id); say(MARK + JSON.stringify({id, done: true})); });
      return;
    }
    if (answer.credential) {
      pending.delete(id);
      request.resolve(build(request.kind, answer.credential));
      return;
    }
    const error = new DOMException(answer.message || 'Cancelled', answer.error || 'NotAllowedError');
    if (request.controller) { request.controller.abort(error); return; }
    pending.delete(id);
    request.reject(error);
  }});
  const yes = () => Promise.resolve(true);
  Object.defineProperty(PKC, 'isUserVerifyingPlatformAuthenticatorAvailable', {value: yes});
  Object.defineProperty(PKC, 'isConditionalMediationAvailable', {value: yes});
  Object.defineProperty(PKC, 'getClientCapabilities', {value: () => Promise.resolve({
    passkeyPlatformAuthenticator: true, userVerifyingPlatformAuthenticator: true, conditionalCreate: false,
    conditionalGet: true, hybridTransport: false, relatedOrigins: false})});
})();
""" % {"mark": json.dumps(MARK), "token": json.dumps(TOKEN)}


def script() -> QWebEngineScript:
    s = QWebEngineScript()
    s.setName(SCRIPT_NAME)
    s.setSourceCode(SOURCE)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)  # (before the site's own scripts)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    return s


def site_of(request) -> str:
    return (request.relyingPartyId() or "").removeprefix("www.") or "This site"


def pin_problem(pin: str, confirm: str | None, minimum: int) -> str:
    """What's wrong with a PIN before it's sent, or "" (confirm: the second copy, when setting a new one)."""
    if len(pin) < max(minimum, 1):
        return f"The PIN needs at least {max(minimum, 4)} characters."
    if confirm is not None and pin != confirm:
        return "The two PINs don't match."
    return ""


class PageCard(QFrame):
    """A card at the top of a page, like a permission's: an icon, what it says, and buttons."""

    def __init__(self, manager, view, host):
        super().__init__(host)
        self.manager, self.view, self.host_widget = manager, view, host
        self.setObjectName("PermissionPrompt")  # (looks like a permission's card)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 8, 10)
        outer.setSpacing(10)
        self.icon = QLabel()
        outer.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body, 1)
        host.installEventFilter(self)

    def _clear(self):
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()  # (gone now, not when Qt next gets round to deleting it)
                item.widget().deleteLater()
            elif item.layout() is not None:
                QWidget().setLayout(item.layout())  # (takes the layout's widgets with it)

    def _text(self, html, detail=False):
        label = QLabel(html)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        if detail:
            label.setObjectName("PromptDetail")
        self.body.addWidget(label)
        return label

    def _buttons(self, *buttons):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        for text, action, main in buttons:
            button = QPushButton(text)
            if main:
                button.setObjectName("PermissionAllow")
                button.setDefault(True)
            button.clicked.connect(action)
            row.addWidget(button)
        self.body.addLayout(row)

    def _set_icon(self, name):
        self.icon.setPixmap(qta.icon(name, color=self.manager.browser.theme.accent_text).pixmap(20, 20))

    def place(self):
        width = min(460, self.host_widget.width() - 40)
        self.setFixedWidth(max(width, 280))
        height = self.layout().heightForWidth(self.width())
        if height > 0:
            self.setFixedHeight(height)
        else:
            self.adjustSize()
        self.move((self.host_widget.width() - self.width()) // 2, 12)
        if self.view.isVisible() and not self.isVisible() and not getattr(self, "_shown_once", False):
            self._shown_once = True
            motion.fade(self, True, motion.NORMAL)
        else:
            self.setVisible(self.view.isVisible())
        self.raise_()

    def eventFilter(self, obj, event):
        # (getattr: the page area can still be resizing while this card is being taken down)
        if obj is getattr(self, "host_widget", None) and event.type() == QEvent.Type.Resize:
            self.place()
        return False

    def arm(self):
        """A moment before its main buttons can be clicked, so a page can't put it up under a click meant for
        the page."""
        buttons = [b for b in self.findChildren(QPushButton) if b.objectName() == "PermissionAllow"]
        for button in buttons:
            button.setEnabled(False)
        QTimer.singleShot(ARM_MS, lambda: [b.setEnabled(True) for b in buttons if not _gone(b)])


class PasskeyCard(PageCard):
    """The card for one request, at the top of its page."""

    def __init__(self, manager, view, host, request, waiting=None, offer=None):
        super().__init__(manager, view, host)
        self.request = request
        self.waiting = waiting  # (no request from Qt yet: the page script's id for the site's request)
        self.offer = offer  # (one of Eclipse's own passkeys can answer it: what the site asked)
        if offer is not None:
            self.show_offer()
            return
        if request is None:
            self._waiting_for_a_key()
            return
        request.stateChanged.connect(self.show_state)
        self.show_state()

    def show_offer(self, checking=False):
        """One of Eclipse's passkeys can answer: save one, or sign in with one (or use a security key)."""
        self._clear()
        offer = self.offer
        site = offer["rp_id"].removeprefix("www.")
        key = ("Use a Security Key", lambda: self.manager.use_key(self), False)
        cancel = ("Cancel", lambda: self.manager.refuse(self, "NotAllowedError"), False)
        if checking:
            self._set_icon("ph.fingerprint")
            self._text(f"<b>{site}</b>")
            self._text("Checking it's you: use Touch ID or your Mac's password.", detail=True)
        elif offer.get("already"):
            self._set_icon("ph.key")
            self._text(f"You already have a passkey for <b>{site}</b> in Eclipse")
            self._buttons(("OK", lambda: self.manager.refuse(self, "InvalidStateError",
                                                               "A passkey for this account is already saved"), True))
        elif offer["kind"] == "create":
            user = offer["options"].get("user") or {}
            self._set_icon("ph.key")
            self._text(f"Save a passkey for <b>{site}</b>?")
            where = "on this Mac, with your recovery PIN" if recovery.is_set() else "on this Mac"
            self._text(f"For {_escape(user.get('name') or user.get('displayName') or 'your account')}. Eclipse keeps "
                       f"it {where}, and you sign in with Touch ID or your Mac's password.", detail=True)
            self._buttons(key, cancel, ("Save Passkey", lambda: self.manager.use_eclipse(self), True))
        elif not offer["passkeys"]:
            self._set_icon("ph.key")
            self._text(f"<b>{site}</b> wants to sign you in with a passkey")
            self._text(f"You don't have a passkey for {site} in Eclipse yet. Sign in another way, then add a passkey "
                       f"in the site's settings and Eclipse will offer to save it. Or use a security key.", detail=True)
            self._buttons(key, cancel)
        else:
            self._set_icon("ph.key")
            self._text(f"Sign in to <b>{site}</b> with your passkey?" if offer.get("conditional")
                       else f"Sign in to <b>{site}</b> with a passkey")
            for passkey in offer["passkeys"]:
                button = QPushButton(passkey.get("user_name") or passkey.get("display_name") or "Your account")
                button.setObjectName("PermissionAllow")
                button.clicked.connect(lambda _=False, i=passkey["id"]: self.manager.use_eclipse(self, i))
                self.body.addWidget(button)
            if offer.get("conditional"):  # (the page keeps waiting: Not Now just puts the card away)
                self._buttons(("Not Now", lambda: self.manager.close(self), False))
            else:
                self._buttons(key, cancel)
        self.arm()
        self.place()

    def _waiting_for_a_key(self):
        site = self.view.url().host().removeprefix("www.") or "This site"
        self._set_icon("ph.key")
        self._text(f"<b>{site}</b> wants to use a passkey or security key")
        self._text("Plug in your security key, then touch it. Passkeys kept in iCloud Keychain or on your phone "
                   "can't be used here yet.", detail=True)
        self._buttons(("Cancel", lambda: self.manager.cancel_waiting(self), False))
        self.place()

    def show_state(self, *_):
        request = self.request
        state = request.state()
        if state in (State.Completed, State.Cancelled):
            self.manager.close(self)
            return
        self._clear()
        site = site_of(request)
        cancel = ("Cancel", request.cancel, False)
        if state == State.SelectAccount:
            self._set_icon("ph.user-circle")
            self._text(f"Sign in to <b>{site}</b> as:")
            for name in request.userNames():
                button = QPushButton(name)
                button.clicked.connect(lambda _=False, n=name: request.setSelectedAccount(n))
                self.body.addWidget(button)
            self._buttons(cancel)
        elif state == State.CollectPin:
            self._pin_form(site)
        elif state == State.FinishTokenCollection:
            self._set_icon("ph.fingerprint")
            self._text(f"<b>{site}</b> wants to use your security key")
            self._text("Touch your security key to continue.", detail=True)
            self._buttons(cancel)
        elif state == State.RequestFailed:
            self._set_icon("ph.warning-circle")
            self._text(f"Couldn't use your security key with <b>{site}</b>")
            self._text(FAILURES.get(request.requestFailureReason(), "Something went wrong."), detail=True)
            self._buttons(("Close", request.cancel, False), ("Try Again", request.retry, True))
        else:  # (NotStarted: nothing to ask yet)
            self.hide()
            return
        self.place()

    def _pin_form(self, site):
        pin = self.request.pinRequest()
        new = pin.reason in (Pin.Set, Pin.Change)
        self._set_icon("ph.key")
        self._text({Pin.Set: f"<b>{site}</b> needs your security key to have a PIN. Choose one:",
                    Pin.Change: f"<b>{site}</b> needs a new PIN for your security key:"}.get(
                        pin.reason, f"Enter your security key's PIN for <b>{site}</b>"))
        first = QLineEdit()
        first.setEchoMode(QLineEdit.EchoMode.Password)
        first.setPlaceholderText("New PIN" if new else "PIN")
        self.body.addWidget(first)
        second = None
        if new:
            second = QLineEdit()
            second.setEchoMode(QLineEdit.EchoMode.Password)
            second.setPlaceholderText("New PIN again")
            self.body.addWidget(second)
        problem = PIN_ERRORS.get(pin.error, "")
        if pin.error == PinError.WrongPin and pin.remainingAttempts > 0:
            problem += f" {pin.remainingAttempts} tr{'y' if pin.remainingAttempts == 1 else 'ies'} left."
        note = self._text(problem, detail=True)
        note.setVisible(bool(problem))

        def send():
            wrong = pin_problem(first.text(), second.text() if second is not None else None, pin.minPinLength)
            if wrong:
                note.setText(wrong)
                note.show()
                self.place()
                return
            self.request.setPin(first.text())
        first.returnPressed.connect(send)
        if second is not None:
            second.returnPressed.connect(send)
        self._buttons(("Cancel", self.request.cancel, False), ("Continue", send, True))
        first.setFocus()




class _Answer(QObject):
    """macOS's answer (Touch ID or the password), which comes on a thread of its own, brought back to Qt's."""
    answered = pyqtSignal(bool)


def _gone(widget) -> bool:
    from PyQt6 import sip
    return sip.isdeleted(widget)


def _escape(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Passkeys(QObject):
    """Every page's passkey and security key requests, each as a card over its page."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._cards: dict[int, PasskeyCard] = {}  # id(view) → its card

    def attach_profile(self, profile):
        scripts = profile.scripts()
        for old in scripts.find(SCRIPT_NAME):
            scripts.remove(old)
        scripts.insert(script())

    def attach(self, view):
        view.page().webAuthUxRequested.connect(lambda request, v=view: self._requested(v, request))
        view.page().console_message.connect(lambda message, v=view: self._said(v, message))
        view.page().loadStarted.connect(lambda v=view: self._left(v))
        view.destroyed.connect(lambda _=None, key=id(view): self._cards.pop(key, None))

    def _said(self, view, message, tries=0):
        """The page script: a site asked for a passkey ({id, kind, options}), or one that went to a security key is
        over ({id, done})."""
        if not message.startswith(MARK):
            return
        try:
            said = json.loads(message[len(MARK):])
            request_id = int(said["id"])
        except (ValueError, KeyError, TypeError):
            return
        if not hmac.compare_digest(str(said.get("token", "")), TOKEN):
            return  # (not the page's own script: a frame in it, say)
        if view.url().isEmpty() and tries < 20:
            # (a sign-in page asking as it arrives can speak before Qt's told the view its address: then which
            # site it is isn't known yet, so it's heard again in a moment)
            QTimer.singleShot(50, lambda: not _gone(view) and self._said(view, message, tries + 1))
            return
        card = self._cards.get(id(view))
        if said.get("done"):
            if card is not None and card.request is None and request_id in (card.waiting, (card.offer or {}).get("id")):
                self.close(card)
            return
        if said.get("kind") not in ("create", "get") or not isinstance(said.get("options"), dict):
            return
        conditional = bool(said.get("conditional"))
        if card is not None:
            if card.request is not None or conditional:
                return  # (Qt's already asking about a security key, or the site asked outright: that card stays)
            self.close(card)
        self._asked(view, request_id, said["kind"], said["options"], conditional)

    def _asked(self, view, request_id, kind, options, conditional=False):
        """A site asked: offer one of Eclipse's passkeys if one can answer, or else a security key. Autofill
        (conditional) only ever offers Eclipse's, and says nothing when there are none, as a sign-in page that
        waits in the background for a passkey should be left alone."""
        url = view.url()
        if conditional and not passkey_store.secure(url):
            return
        if not passkey_store.secure(url):
            self._answer(view, request_id, {"use": "key"})  # (Chromium refuses it, as it should)
            return
        host = url.host().lower()
        asked_for = (options.get("rp") or {}).get("id") if kind == "create" else options.get("rpId")
        rp_id = str(asked_for or host).lower()
        if not passkey_store.rp_id_allowed(rp_id, host):
            self._answer(view, request_id, {"error": "SecurityError", "message": "That site isn't this page's"})
            return
        if conditional and not passkey_store.rp_id_allowed(rp_id, host):
            return
        offer = {"id": request_id, "kind": kind, "rp_id": rp_id, "options": options,
                 "origin": passkey_store.origin_of(url), "conditional": conditional}
        if kind == "create":
            algs = [a for a in options.get("algs") or [] if isinstance(a, int)]
            if (algs and passkey_store.ES256 not in algs) or options.get("attachment") == "cross-platform":
                self._to_key(view, request_id)  # (the site wants a security key, or what Eclipse can't make)
                return
            excluded = set(options.get("exclude") or [])
            offer["already"] = any(p["id"] in excluded for p in passkey_store.for_site(rp_id))
        else:
            allowed = [str(i) for i in options.get("allow") or []]
            offer["passkeys"] = [p for p in passkey_store.for_site(rp_id, allowed or None) if passkey_store.has_key(p)]
            if not offer["passkeys"] and conditional:
                return  # (nothing to offer: the page carries on as it is)
        self._cards[id(view)] = PasskeyCard(self, view, self.browser.permissions._host_for(view), None, offer=offer)
        self._mention_if_hidden(view, "wants to use a passkey")

    def _answer(self, view, request_id, answer):
        view.page().runJavaScript(f"window.__eclipsePasskeys && window.__eclipsePasskeys({int(request_id)}, "
                                  f"{json.dumps(answer)})", 0)  # (the page's own world, where the script is)

    def _to_key(self, view, request_id):
        """On to Chromium, for a security key: the card says to plug one in."""
        self._answer(view, request_id, {"use": "key"})
        self._cards[id(view)] = PasskeyCard(self, view, self.browser.permissions._host_for(view), None, request_id)
        self._mention_if_hidden(view, "wants to use your security key")

    def use_key(self, card):
        self.close(card)
        self._to_key(card.view, card.offer["id"])

    def refuse(self, card, error, message="Cancelled"):
        self._answer(card.view, card.offer["id"], {"error": error, "message": message})
        self.close(card)

    def cancel_waiting(self, card):
        self._answer(card.view, card.waiting, {"error": "NotAllowedError", "message": "Cancelled"})
        self.close(card)

    def use_eclipse(self, card, passkey_id=None):
        """Save a passkey, or sign in with one, once macOS has checked it's you (unless the site said not to)."""
        offer = card.offer
        site = offer["rp_id"].removeprefix("www.")
        wanted = offer["options"].get("userVerification", "preferred")
        if wanted == "discouraged" or not macos.can_verify_owner():
            if wanted == "required":
                self.refuse(card, "NotAllowedError", "This Mac can't check it's you")
                self.browser.toast("This Mac can't check it's you (Touch ID or its password), so the passkey "
                                   "can't be used")
                return
            self._finish(card, passkey_id, verified=False)
            return
        answer = self._checking = _Answer(self)  # (kept: macOS answers later)
        answer.answered.connect(lambda ok: self._checked(card, passkey_id, ok))
        card.show_offer(checking=True)
        reason = f"save a passkey for {site}" if offer["kind"] == "create" else f"use your passkey for {site}"
        if not macos.verify_owner(reason, answer.answered.emit):
            self._checked(card, passkey_id, False)

    def _checked(self, card, passkey_id, ok):
        if _gone(card) or self._cards.get(id(card.view)) is not card:
            return  # (the card went meanwhile: the page left, say)
        if ok:
            self._finish(card, passkey_id, verified=True)
        else:
            card.show_offer()  # (back to the question: try again, or something else)

    def _finish(self, card, passkey_id, verified):
        offer, view = card.offer, card.view
        options = offer["options"]
        first = not passkey_store.all_passkeys()
        try:
            if offer["kind"] == "create":
                user, rp = options.get("user") or {}, options.get("rp") or {}
                made = passkey_store.create(offer["rp_id"], str(rp.get("name") or offer["rp_id"]),
                                            str(user.get("id") or ""), str(user.get("name") or ""),
                                            str(user.get("displayName") or ""), str(options.get("challenge") or ""),
                                            offer["origin"], verified)
                made["credProps"] = bool(options.get("credProps"))
            else:
                made = passkey_store.sign_in(passkey_id, str(options.get("challenge") or ""), offer["origin"],
                                             verified)
        except (OSError, StopIteration, ValueError) as error:
            self._answer(view, offer["id"], {"error": "NotAllowedError", "message": "The passkey couldn't be used"})
            self.close(card)
            self.browser.toast(str(error) if isinstance(error, OSError) else "The passkey couldn't be used")
            return
        self._answer(view, offer["id"], {"credential": made})
        self.close(card)
        if offer["kind"] == "create":
            site = offer["rp_id"].removeprefix("www.")
            if first and not recovery.is_set():
                self.browser.toast(f"Passkey saved for {site}. Set a recovery PIN to get it back on another Mac",
                                   lambda: self.browser.open_settings("passwords"), "Set PIN")
            else:
                self.browser.toast(f"Passkey saved for {site}")

    def _mention_if_hidden(self, view, what):
        if not view.isVisible():
            tab = next((t for t in self.browser.store.all_tabs() if t.view is view), None)
            if tab is not None:
                self.browser.toast(f"{tab.label} {what} — switch to it to continue")

    def _requested(self, view, request):
        old = self._cards.pop(id(view), None)
        if old is not None:
            old.deleteLater()
        self._cards[id(view)] = PasskeyCard(self, view, self.browser.permissions._host_for(view), request)
        self._mention_if_hidden(view, "wants to use your security key")

    def _left(self, view):
        """The page is going: its requests went with it (Qt's own security key request ends itself)."""
        card = self._cards.get(id(view))
        if card is not None and card.request is None:
            self.close(card)

    def view_shown(self):
        """A tab came on screen: show its card, if it has one, and hide the others'."""
        for card in self._cards.values():
            card.setVisible(card.view.isVisible())

    def close(self, card):
        if self._cards.get(id(card.view)) is card:
            del self._cards[id(card.view)]
        card.host_widget.removeEventFilter(card)
        motion.fade(card, False, motion.FAST, done=card.deleteLater) if card.isVisible() else card.deleteLater()

    def card_for(self, view) -> PasskeyCard | None:
        return self._cards.get(id(view))
