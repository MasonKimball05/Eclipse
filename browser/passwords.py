# Passwords on pages: filling them in, offering to save them, and Settings → Passwords & Passkeys.
#
# A small script in each page, in a world of its own the page can't see into, notices sign-in forms (a
# password field) and what's typed into them when they're sent, and fills them when asked. Its messages go
# straight to the browser, never to the console (webview.PASSWORD_CALL), and carry a token made fresh each
# launch, so a page can't pass itself off as the script.
#
# Nothing is filled by itself: a card offers your accounts for the site, and filling asks for Touch ID or
# the Mac's password (once each time Eclipse opens, or every time, as you choose). Only the page's own site,
# or its own subdomains, ever get a password, and never a page sent in the clear (http, other than this Mac's
# own). Saving: once a form is sent, a card offers to save (or update) what was typed, with Never for This Site.
#
# Sign-in forms in an embedded frame work too, but only a frame of the same site as the page around it (a
# sign-in frame from accounts.example.com on example.com): a frame from anywhere else, like an ad, is never
# offered a password, whatever the page. The frame's address comes from the script, which the page can't touch,
# and filling runs in that frame alone.

import json
import secrets
import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import QPushButton

from . import macos, password_store, passkey_store, recovery
from .passkeys import PageCard, _Answer, _escape, _gone
from .webview import PASSWORD_CALL

SCRIPT_NAME = "eclipse-passwords"
FILL_SETTING, SAVE_SETTING, NEVER_SETTING, UNLOCK_SETTING = ("fill_passwords", "save_passwords",
                                                             "never_save_passwords", "password_unlock")
WORLD = 1  # (QWebEngineScript.ScriptWorldId.ApplicationWorld: the script's own, which the page can't see)

SOURCE = r"""
(() => {
  if (window.__eclipsePasswords) return;
  const TOKEN = %(token)s, MARK = %(mark)s, say = console.debug.bind(console);
  const framed = window !== window.top;
  const tell = data => say(MARK + JSON.stringify({token: TOKEN, ...data, ...(framed ? {frame: location.href} : {})}));
  const shown = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const passwords = () => [...document.querySelectorAll('input[type=password]')].filter(shown);
  // A form making a new password: marked so, or asking for it twice (password and confirm)
  const newPasswords = () => {
    const fields = passwords();
    const marked = fields.filter(f => /new-password/.test(f.getAttribute('autocomplete') || ''));
    if (marked.length) return marked;
    const together = fields.filter(f => f.form && fields.filter(o => o.form === f.form).length === 2);
    return together;
  };
  const userFor = pass => {
    const scope = pass.form || document;
    let found = null;
    for (const input of scope.querySelectorAll('input')) {
      const type = (input.getAttribute('type') || 'text').toLowerCase();
      if (!['text', 'email', 'tel'].includes(type) || !shown(input)) continue;
      if (input.compareDocumentPosition(pass) & Node.DOCUMENT_POSITION_FOLLOWING) found = input;
    }
    return found || document.querySelector('input[autocomplete~=username]');
  };
  // A sign-in that asks for the username first, on a page of its own (Google's, Microsoft's)
  const loneUser = () => [...document.querySelectorAll('input')].find(input => shown(input) && (
    /(^|\s)username(\s|$)/.test(input.getAttribute('autocomplete') || '') ||
    (input.getAttribute('type') || '').toLowerCase() === 'email' ||
    /(user|email|login|identifier)/i.test((input.name || '') + ' ' + (input.id || ''))));
  let announced = false;
  const look = () => {
    const fields = passwords();
    if (fields.length && announced !== 'password') { announced = 'password'; tell({kind: 'form'}); }
    if (!fields.length && !announced) {
      const user = loneUser();
      if (user) {
        announced = 'username';
        tell({kind: 'form', step: 'username'});
        if (!user.__eclipseWatched) {
          user.__eclipseWatched = true;
          const sent = () => { if (user.value) tell({kind: 'username', username: user.value}); };
          user.addEventListener('keydown', event => { if (event.key === 'Enter') sent(); }, true);
          if (user.form) user.form.addEventListener('submit', sent, true);
          (user.form || document).addEventListener('click', event => {
            const button = event.target.closest && event.target.closest('button, input[type=submit]');
            if (button) sent();
          }, true);
        }
      }
    }
    for (const field of newPasswords()) {
      if (field.__eclipseOffered) continue;
      field.__eclipseOffered = true;
      const offer = () => tell({kind: 'new-password'});
      field.addEventListener('focus', offer, true);
      field.addEventListener('pointerdown', offer, true);  // (focus doesn't come while the window's in the back)
    }
    for (const field of fields) {
      if (field.__eclipseWatched) continue;
      field.__eclipseWatched = true;
      const sent = () => {
        if (!field.value) return;
        const user = userFor(field);
        tell({kind: 'sent', username: user ? user.value : '', password: field.value,
              created: (field.getAttribute('autocomplete') || '').includes('new-password')});
      };
      if (field.form) field.form.addEventListener('submit', sent, true);
      field.addEventListener('keydown', event => { if (event.key === 'Enter') sent(); }, true);
      (field.form || document).addEventListener('click', event => {
        const button = event.target.closest && event.target.closest('button, input[type=submit]');
        if (button && (button.type || 'submit').toLowerCase() === 'submit') sent();
      }, true);
    }
  };
  const set = (input, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  };
  Object.defineProperty(window, '__eclipsePasswords', {value: (token, username, password) => {
    if (token !== TOKEN) return false;
    const pass = passwords()[0];
    if (!pass) {  // (the username's own page)
      const user = loneUser();
      if (user && username) set(user, username);
      return !!user;
    }
    const user = userFor(pass);
    if (user && username) set(user, username);
    set(pass, password);
    return true;
  }});
  Object.defineProperty(window, '__eclipseNewPassword', {value: (token, password) => {
    if (token !== TOKEN) return false;
    const fields = newPasswords();
    for (const field of fields) set(field, password);
    return fields.length > 0;
  }});
  look();
  let later = null;
  new MutationObserver(() => { clearTimeout(later); later = setTimeout(look, 300); })
    .observe(document.documentElement, {childList: true, subtree: true});
})();
"""


def script(token: str) -> QWebEngineScript:
    s = QWebEngineScript()
    s.setName(SCRIPT_NAME)
    s.setSourceCode(SOURCE % {"token": json.dumps(token), "mark": json.dumps(PASSWORD_CALL)})
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    s.setRunsOnSubFrames(True)
    return s


class FillCard(PageCard):
    """Your accounts for the site: pick one to fill it in (on a username's own page, just the username)."""

    def __init__(self, manager, view, host, logins, username_only=False, frame=None):
        super().__init__(manager, view, host)
        self.username_only, self.frame = username_only, frame
        site = password_store.site_of(frame or view.url().toString())
        self._set_icon("ph.key")
        self._text(f"Sign in to <b>{_escape(site)}</b>?")
        for login in logins[:6]:
            other = password_store.site_of(login["origin"])
            label = (login["username"] or "No username") + (f"  ·  {other}" if other != site else "")
            button = QPushButton(label)
            button.setObjectName("PermissionAllow")
            button.clicked.connect(lambda _=False, entry=login: manager.fill(self, entry))
            self.body.addWidget(button)
        self._buttons(("Not Now", lambda: manager.close(self), False))
        self.arm()
        self.place()


def strong_password() -> str:
    """A strong password, in the style Apple's Passwords makes: three groups of six letters and digits, at least
    one capital and one digit (about 71 bits, and easy to read out if it ever has to be)."""
    alphabet = "abcdefghijkmnopqrstuvwxyz23456789"  # (no l, 0 or 1: nothing to mistake for another)
    while True:
        chars = [secrets.choice(alphabet) for _ in range(18)]
        chars[secrets.randbelow(18)] = secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ")
        text = "".join(chars)
        if any(c.isdigit() for c in text) and any(c.isupper() for c in text):
            return "-".join(text[i:i + 6] for i in (0, 6, 12))


class StrongCard(PageCard):
    """Making an account: a strong password, filled in for you, and saved when the form's sent."""

    def __init__(self, manager, view, host, password, frame=None):
        super().__init__(manager, view, host)
        self.password, self.frame = password, frame
        site = password_store.site_of(frame or view.url().toString())
        self._set_icon("ph.lock-key")
        self._text(f"Use a strong password for <b>{_escape(site)}</b>?")
        self._text(f"<span style='font-family: Menlo; font-size: 13px'>{_escape(password)}</span><br>"
                   f"Eclipse saves it when you sign up.", detail=True)
        self._buttons(("Not Now", lambda: manager.close(self), False),
                      ("Use Strong Password", lambda: manager.use_strong(self), True))
        self.arm()
        self.place()


class SaveCard(PageCard):
    """Save (or update) what was just typed into the site."""

    def __init__(self, manager, view, host, url, username, password, existing):
        super().__init__(manager, view, host)
        self.url, self.username, self.password = url, username, password
        self.carries_over = True  # (the form usually sends you to a new page: the card stays for that)
        site = password_store.site_of(url)
        self._set_icon("ph.key")
        who = _escape(username) if username else "this site"
        if existing is not None:
            self._text(f"Update your saved password for <b>{who}</b> on <b>{_escape(site)}</b>?")
            self._buttons(("Not Now", lambda: manager.close(self), False),
                          ("Update", lambda: manager.save(self), True))
        else:
            self._text(f"Save your password for <b>{_escape(site)}</b>?")
            self._text(f"For {who}. Eclipse keeps it encrypted on this Mac, and fills it in after Touch ID or "
                       f"your Mac's password.", detail=True)
            self._buttons(("Never for This Site", lambda: manager.never(self), False),
                          ("Not Now", lambda: manager.close(self), False), ("Save", lambda: manager.save(self), True))
        self.arm()
        self.place()


class Passwords(QObject):
    """Every page's sign-in forms: filling, and offering to save."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.token = secrets.token_hex(16)  # (new each launch: a page can't know it)
        self._cards = {}  # id(view) → its card
        self._unlocked = False  # (Touch ID passed since Eclipse opened)
        self._usernames = {}  # site → (username, when): typed on a sign-in's first page, for the password's
        self._made = {}  # site → a strong password Eclipse filled in: saved, without asking, once it's sent

    @property
    def settings(self):
        return self.browser.store.settings

    def attach_profile(self, profile):
        scripts = profile.scripts()
        for old in scripts.find(SCRIPT_NAME):
            scripts.remove(old)
        scripts.insert(script(self.token))

    def attach(self, view):
        view.page().password_messages = lambda message, v=view: self._said(v, message)
        view.page().loadStarted.connect(lambda v=view: self._left(v))
        view.destroyed.connect(lambda _=None, key=id(view): self._cards.pop(key, None))

    def _said(self, view, message):
        try:
            said = json.loads(message[len(PASSWORD_CALL):])
        except ValueError:
            return
        if not isinstance(said, dict) or not secrets.compare_digest(str(said.get("token", "")), self.token):
            return  # (not our script: a page making it up)
        url, frame = view.url().toString(), None
        if "frame" in said:  # (a form in an embedded frame: its own address, and only the page's own site's)
            frame = str(said["frame"])
            if not password_store.same_site(QUrl(frame).host(), QUrl(url).host()):
                return
            url = frame
        if said.get("kind") == "form":
            if self.settings.get(FILL_SETTING, True) and id(view) not in self._cards:
                found = password_store.for_page(url)
                if found:
                    self._show(view, FillCard(self, view, self._host(view), found,
                                              username_only=said.get("step") == "username", frame=frame))
        elif said.get("kind") == "new-password":
            card = self._cards.get(id(view))
            if _fillable(url) and self.settings.get(SAVE_SETTING, True) and not isinstance(card, StrongCard):
                self._show(view, StrongCard(self, view, self._host(view), strong_password(), frame))
        elif said.get("kind") == "username":
            if _fillable(url) and said.get("username"):
                self._usernames[password_store.site_of(url)] = (str(said["username"])[:200], time.time())
        elif said.get("kind") == "sent":
            username = str(said.get("username") or "")[:200] or self._remembered(url)
            self._sent(view, url, username, str(said.get("password") or "")[:500])

    def _remembered(self, url) -> str:
        """The username typed on this site's sign-in first page, in the last quarter of an hour."""
        site = password_store.site_of(url)
        for other, (username, when) in list(self._usernames.items()):
            if time.time() - when > 15 * 60:
                del self._usernames[other]
            elif password_store.related(other, site):
                return username
        return ""

    def _sent(self, view, url, username, password):
        site = password_store.site_of(url)
        if self._made.get(site) == password:  # (the strong one Eclipse made: saved, as Apple's and Chrome's do)
            del self._made[site]
            try:
                password_store.add(url, username, password, source="Eclipse")
                self.browser.toast(f"Strong password saved for {site}")
            except (password_store.Locked, OSError):
                self.browser.toast("Your saved passwords need your recovery PIN first: Settings → Passwords")
            return
        if (not password or not _fillable(url) or not self.settings.get(SAVE_SETTING, True)
                or site in self.settings.get(NEVER_SETTING, [])):
            return  # (nothing typed, a page sent in the clear, or you said not to)
        try:
            saved = [e for e in password_store.logins() if password_store.related(QUrl(e["origin"]).host(),
                                                                                  QUrl(url).host())]
        except password_store.Locked:
            return
        existing = next((e for e in saved if e["username"] == username), None)
        if existing is not None and existing["password"] == password:
            return  # (nothing new)
        self._show(view, SaveCard(self, view, self._host(view), url, username, password, existing))

    # --- Doing it --------------------------------------------------------------------------------------------

    def fill(self, card, login):
        site = password_store.site_of(card.frame or card.view.url().toString())
        if card.username_only:
            self._fill(card, login, with_password=False)  # (no password goes in: nothing to unlock)
            return
        self._unlock(f"fill your password for {site}", lambda: self._fill(card, login))

    def _fill(self, card, login, with_password=True):
        if _gone(card):
            return
        view = card.view
        if not with_password:
            self._usernames[password_store.site_of(card.frame or view.url().toString())] = (login["username"],
                                                                                            time.time())
        password = login["password"] if with_password else ""
        call = (f"window.__eclipsePasswords && window.__eclipsePasswords({json.dumps(self.token)}, "
                f"{json.dumps(login['username'])}, {json.dumps(password)})")
        if not _run_in(view, card.frame, call):
            self.browser.toast("That sign-in form isn't on the page any more")
            self.close(card)
            return
        password_store.update(login["id"], last_used=time.time())
        self.close(card)

    def _unlock(self, reason, then):
        """Touch ID (or the Mac's password) first: once each time Eclipse opens, or every time."""
        every = self.settings.get(UNLOCK_SETTING, "launch") == "every"
        if (self._unlocked and not every) or not macos.can_verify_owner():
            then()
            return
        answer = self._checking = _Answer(self)

        def answered(ok):
            if ok:
                self._unlocked = True
                then()
        answer.answered.connect(answered)
        if not macos.verify_owner(reason, answer.answered.emit):
            self.browser.toast("This Mac couldn't check it's you")

    def use_strong(self, card):
        view = card.view
        call = (f"window.__eclipseNewPassword && window.__eclipseNewPassword({json.dumps(self.token)}, "
                f"{json.dumps(card.password)})")
        _run_in(view, card.frame, call)
        self._made[password_store.site_of(card.frame or view.url().toString())] = card.password
        self.close(card)

    def save(self, card):
        try:
            password_store.add(card.url, card.username, card.password, source="Eclipse")
        except (password_store.Locked, OSError) as error:
            self.browser.toast("Your saved passwords need your recovery PIN first: Settings → Passwords"
                               if isinstance(error, password_store.Locked) else str(error))
            self.close(card)
            return
        self.close(card)
        self.browser.toast(f"Password saved for {password_store.site_of(card.url)}")

    def never(self, card):
        never = self.settings.setdefault(NEVER_SETTING, [])
        site = password_store.site_of(card.url)
        if site not in never:
            never.append(site)
            self.browser.store.schedule_save()
        self.close(card)

    # --- The cards -------------------------------------------------------------------------------------------

    def _host(self, view):
        return self.browser.permissions._host_for(view)

    def _show(self, view, card):
        old = self._cards.pop(id(view), None)
        if old is not None and not _gone(old):
            old.deleteLater()
        self._cards[id(view)] = card

    def _left(self, view):
        card = self._cards.get(id(view))
        if card is None:
            return
        if getattr(card, "carries_over", False):
            card.carries_over = False  # (once: the page the form sent you to)
            return
        self.close(card)

    def view_shown(self):
        for card in self._cards.values():
            if not _gone(card):
                card.setVisible(card.view.isVisible())

    def close(self, card):
        if self._cards.get(id(card.view)) is card:
            del self._cards[id(card.view)]
        if not _gone(card):
            card.host_widget.removeEventFilter(card)
            card.deleteLater()

    def card_for(self, view):
        return self._cards.get(id(view))


def _frames(frame):
    yield frame
    for child in frame.children():
        yield from _frames(child)


def _run_in(view, frame_url, call) -> bool:
    """Run the script's call in the page, or in the frame at frame_url (False when that frame's gone). The frame
    is looked up again now: it may have moved on since the card appeared, and then it isn't the one asked about."""
    page = view.page()
    if frame_url is None:
        page.runJavaScript(call, WORLD)
        return True
    wanted = QUrl(frame_url).adjusted(QUrl.UrlFormattingOption.RemoveFragment)
    frame = next((f for f in _frames(page.mainFrame())
                  if not f.isMainFrame() and f.url().adjusted(QUrl.UrlFormattingOption.RemoveFragment) == wanted), None)
    if frame is None:
        return False
    frame.runJavaScript(call, WORLD)
    return True


def _fillable(url: str) -> bool:
    q = QUrl(url)
    host = q.host().lower()
    return q.scheme() == "https" or (q.scheme() == "http" and (host == "localhost" or host.endswith(".localhost")))


# --- Settings → Passwords & Passkeys -------------------------------------------------------------------

class _Done(QObject):
    """Work done on another thread (asking macOS for Arc's key, which waits for you), brought back to Qt's."""
    done = pyqtSignal(object)


PIN_WIDTH = 400  # (the dialog's text, wrapped to this)


def _wrapped(text, hint=False):
    """A label that wraps at a set width (left to itself in a dialog, a wrapped label gets too little height and
    its last lines are cut off: at a set width, the dialog's layout knows how tall it is)."""
    from PyQt6.QtWidgets import QLabel
    label = QLabel(text)
    label.setWordWrap(True)
    if hint:
        label.setObjectName("Hint")
    label.setFixedWidth(PIN_WIDTH)
    return label


def _make_pin_dialog(parent, title, text, note, twice):
    """The recovery PIN dialog, and how to read it: (dialog, the first field)."""
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLayout, QLineEdit, QVBoxLayout
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    v = QVBoxLayout(dialog)
    v.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)  # (always just big enough for what it says)
    v.setContentsMargins(20, 18, 20, 16)
    v.setSpacing(10)
    v.addWidget(_wrapped(text))
    if note:
        v.addWidget(_wrapped(note, hint=True))
    fields = []
    for placeholder in (["Recovery PIN", "The same PIN again"] if twice else ["Recovery PIN"]):
        field = QLineEdit()
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setPlaceholderText(placeholder)
        v.addWidget(field)
        fields.append(field)
    problem = _wrapped("", hint=True)
    problem.hide()
    v.addWidget(problem)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    v.addWidget(buttons)

    def accept():
        pin = fields[0].text()
        wrong = recovery.pin_problem(pin) if twice else ("" if pin else "Enter your recovery PIN.")
        if not wrong and twice and fields[1].text() != pin:
            wrong = "The two PINs don't match."
        if wrong:
            problem.setText(wrong)
            problem.show()
            return
        dialog.accept()
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    return dialog, fields[0]


def _pin_dialog(parent, title, text, twice, note=""):
    """Asks for the recovery PIN (twice, for a new one). The PIN, or None if cancelled."""
    dialog, field = _make_pin_dialog(parent, title, text, note, twice)
    return field.text() if dialog.exec() else None


def _login_dialog(parent, login=None):
    """Add a login by hand, or change one: {url, username, password}, or None if cancelled."""
    from PyQt6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLayout, QLineEdit, QVBoxLayout
    dialog = QDialog(parent)
    dialog.setWindowTitle("Edit Password" if login else "Add Password")
    v = QVBoxLayout(dialog)
    v.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
    v.setContentsMargins(20, 18, 20, 16)
    form = QFormLayout()
    url = QLineEdit(login["origin"] if login else "")
    url.setPlaceholderText("https://example.com")
    username = QLineEdit(login["username"] if login else "")
    password = QLineEdit(login["password"] if login else "")
    password.setEchoMode(QLineEdit.EchoMode.Password)
    for field in (url, username, password):
        field.setMinimumWidth(320)
    show = QCheckBox("Show password")
    show.toggled.connect(lambda on: password.setEchoMode(QLineEdit.EchoMode.Normal if on
                                                         else QLineEdit.EchoMode.Password))
    suggest = QPushButton("Suggest a Strong One")
    suggest.clicked.connect(lambda: (password.setText(strong_password()), show.setChecked(True)))
    form.addRow("Website", url)
    form.addRow("Username", username)
    form.addRow("Password", password)
    form.addRow("", show)
    form.addRow("", suggest)
    v.addLayout(form)
    problem = _wrapped("", hint=True)
    problem.hide()
    v.addWidget(problem)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    v.addWidget(buttons)

    def accept():
        address = url.text().strip()
        if address and "://" not in address:
            address = "https://" + address
        wrong = ("Enter the website's address." if not QUrl(address).host() else
                 "Enter the password." if not password.text() else "")
        if wrong:
            problem.setText(wrong)
            problem.show()
            return
        url.setText(address)
        dialog.accept()
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    if not dialog.exec():
        return None
    return {"url": url.text(), "username": username.text().strip(), "password": password.text()}


def settings_page(settings):
    """Passwords & Passkeys: the recovery PIN, importing, your passwords (after Touch ID), and your passkeys."""
    from PyQt6.QtCore import QFile
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLineEdit, QMenu, QMessageBox,
                                 QVBoxLayout, QWidget)
    from .settings import _combo
    browser = settings.browser
    page = QWidget()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    state = {"shown": False, "search": ""}

    def busy(work):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return work()
        finally:
            QApplication.restoreOverrideCursor()

    def set_pin():
        pin = _pin_dialog(settings, "Change Recovery PIN" if recovery.is_set() else "Set a Recovery PIN",
                          "With a recovery PIN, a locked copy of your passwords and passkeys goes into your backups, "
                          "so you can get them back on another Mac or after reinstalling.", twice=True,
                          note=f"At least {recovery.MIN_PIN} characters. Keep it somewhere safe: nobody, not even "
                               f"Eclipse's maker, can open that copy without it.")
        if pin is not None:
            browser.toast("Recovery PIN set" if busy(lambda: recovery.set_pin(pin))
                          else "Couldn't keep the recovery PIN in the Keychain")
            fill()

    def recover():
        pin = _pin_dialog(settings, "Recover Passwords and Passkeys",
                          "Enter the recovery PIN you set on your other Mac.", twice=False)
        if pin is None:
            return
        brought = busy(lambda: recovery.unlock(pin))
        if brought is None:
            QMessageBox.warning(settings, "Recover", "That isn't the recovery PIN these were saved with.")
        else:
            browser.toast(f"Brought back {brought} password{'' if brought == 1 else 's'} and passkey"
                          f"{'' if brought == 1 else 's'}" if brought else "Everything's already here")
        fill()

    def imported(rows, source):
        try:
            added, changed, same = password_store.import_rows(rows, source)
        except password_store.Locked:
            QMessageBox.warning(settings, "Import", "Your saved passwords need your recovery PIN first.")
            return False
        QMessageBox.information(settings, "Import", f"From {source}: {added} new, {changed} updated"
                                                   f"{f', {same} you had already' if same else ''}.")
        fill()
        return True

    def from_arc():
        if QMessageBox.question(settings, "Import from Arc",
                                "macOS will ask whether Eclipse may use Arc's key for its saved passwords. Click "
                                "Allow (not Always Allow) to bring them over.") != QMessageBox.StandardButton.Yes:
            return
        finished = _Done(page)

        def got(secret):
            if secret is None:
                QMessageBox.warning(settings, "Import from Arc", "Arc's passwords couldn't be opened (macOS wasn't "
                                                                 "allowed to share Arc's key).")
                return
            try:
                rows = busy(lambda: password_store.read_arc(secret))
            except OSError as error:
                QMessageBox.warning(settings, "Import from Arc", f"Couldn't read Arc's passwords: {error}")
                return
            imported(rows, "Arc")
        finished.done.connect(got)
        threading.Thread(target=lambda: finished.done.emit(password_store.arc_secret()), daemon=True).start()

    def from_file():
        path, _ = QFileDialog.getOpenFileName(settings, "Import Passwords", "", "Passwords export (*.csv)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as f:
                rows = password_store.parse_csv(f.read())
        except (OSError, ValueError, UnicodeDecodeError) as error:
            QMessageBox.warning(settings, "Import Passwords", str(error))
            return
        if imported(rows, "a file") and QMessageBox.question(
                settings, "Import Passwords", "That file holds every password in it as plain text. Move it to the "
                                              "Trash?") == QMessageBox.StandardButton.Yes:
            QFile.moveToTrash(path)

    def show_passwords():
        if state["shown"]:
            state["shown"] = False
            fill()
            return
        answer = page._checking = _Answer(page)

        def answered(ok):
            if ok:
                state["shown"] = True
                fill()
        answer.answered.connect(answered)
        if not macos.can_verify_owner():
            answered(True)
        elif not macos.verify_owner("show your saved passwords", answer.answered.emit):
            browser.toast("This Mac couldn't check it's you")

    def copy(login):
        clipboard = QApplication.clipboard()
        clipboard.setText(login["password"])
        browser.toast(f"Copied the password for {password_store.site_of(login['origin'])}: it's cleared in a minute")
        QTimer.singleShot(60_000, lambda: clipboard.text() == login["password"] and clipboard.clear())

    def add_login():
        made = _login_dialog(settings)
        if made is not None:
            try:
                password_store.add(made["url"], made["username"], made["password"], source="Eclipse")
            except (password_store.Locked, OSError) as error:
                QMessageBox.warning(settings, "Add Password", "Your saved passwords need your recovery PIN first."
                                    if isinstance(error, password_store.Locked) else str(error))
            fill()

    def edit_login(login):
        changed = _login_dialog(settings, login)
        if changed is not None:
            password_store.update(login["id"], **changed)
            fill()

    def remove_login(login):
        if QMessageBox.question(settings, "Remove Password",
                                f"Remove your saved password for {password_store.site_of(login['origin'])} "
                                f"({login['username'] or 'no username'})?") == QMessageBox.StandardButton.Yes:
            password_store.delete(login["id"])
            fill()

    def remove_passkey(passkey):
        site = passkey["rp_id"]
        if QMessageBox.question(settings, "Remove Passkey",
                                f"Remove your passkey for {site} ({passkey.get('user_name') or 'your account'})? "
                                f"You'll need another way to sign in to {site}.") == QMessageBox.StandardButton.Yes:
            passkey_store.delete(passkey["id"])
            fill()

    def setting(key, value):
        browser.store.settings[key] = value
        browser.store.schedule_save()

    def fill():
        while outer.count():
            item = outer.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        store_settings = browser.store.settings
        # The recovery PIN
        pin_button = QPushButton("Change PIN…" if recovery.is_set() else "Set PIN…")
        pin_button.clicked.connect(set_pin)
        rows = [settings._row("Recovery PIN", "Set: your passwords and passkeys are in your backups, locked with it."
                              if recovery.is_set() else "Not set: your passwords and passkeys are only on this Mac.",
                              pin_button)]
        waiting = len(passkey_store.missing()) + (1 if password_store.needs_recovery() else 0)
        if waiting and recovery.is_set():
            back = QPushButton("Recover…")
            back.clicked.connect(recover)
            rows.append(settings._row("Bring back your passwords and passkeys",
                                      "They came from a backup or another Mac: your recovery PIN brings them back.",
                                      back))
        cards = [settings._card(rows)]
        # Passwords
        locked = password_store.needs_recovery()
        try:
            logins = [] if locked else password_store.logins()
        except password_store.Locked:
            logins, locked = [], True
        import_button = QPushButton("Import ▾")
        menu = QMenu(import_button)
        if password_store.arc_installed():
            menu.addAction("From Arc…", from_arc)
        menu.addAction("From a File (Passwords, Chrome, Firefox…)…", from_file)
        import_button.setMenu(menu)
        show_button = QPushButton("Hide" if state["shown"] else "Show…")
        show_button.clicked.connect(show_passwords)
        show_button.setEnabled(bool(logins))
        buttons = QWidget()
        h = QHBoxLayout(buttons)
        h.setContentsMargins(0, 0, 0, 0)
        add_button = QPushButton("Add…")
        add_button.clicked.connect(add_login)
        add_button.setEnabled(not locked)
        h.addWidget(import_button)
        h.addWidget(add_button)
        h.addWidget(show_button)
        count = len(logins)
        about = ("Locked: they came from a backup and need your recovery PIN." if locked else
                 f"{count} saved, encrypted on this Mac. Showing them asks for Touch ID or your Mac's password."
                 if count else "None yet. Import them from Arc, or from a file exported by Passwords (File → Export "
                               "All Passwords), Chrome or Firefox.")
        fill_box, save_box = QCheckBox(), QCheckBox()
        fill_box.setChecked(store_settings.get(FILL_SETTING, True))
        fill_box.toggled.connect(lambda on: setting(FILL_SETTING, on))
        save_box.setChecked(store_settings.get(SAVE_SETTING, True))
        save_box.toggled.connect(lambda on: setting(SAVE_SETTING, on))
        ask = _combo([("launch", "Once each time Eclipse opens"), ("every", "Every time")],
                     store_settings.get(UNLOCK_SETTING, "launch"))
        ask.currentIndexChanged.connect(lambda: setting(UNLOCK_SETTING, ask.currentData()))
        rows = [settings._row("Saved passwords", about, buttons),
                settings._row("Offer to fill passwords", "A card on a sign-in page offers your accounts for the site.",
                              fill_box),
                settings._row("Offer to save passwords", "After you sign in somewhere new.", save_box),
                settings._row("Ask for Touch ID before filling", None, ask)]
        never = store_settings.get(NEVER_SETTING, [])
        if never:
            clear = QPushButton("Clear")
            clear.clicked.connect(lambda: (setting(NEVER_SETTING, []), fill()))
            rows.append(settings._row("Never saved for", ", ".join(sorted(never)[:6]) + ("…" if len(never) > 6 else ""),
                                      clear))
        cards.append(settings._card(rows))
        if state["shown"] and logins:
            search = QLineEdit(state["search"])
            search.setPlaceholderText("Search")
            search.textChanged.connect(lambda text: (state.__setitem__("search", text), refill_later.start()))
            wanted = state["search"].lower()
            issues = password_store.health(logins)
            reused = sum("reused" in found for found in issues.values())
            weak = sum("weak" in found for found in issues.values())
            summary = (f"{reused} reused on more than one site, {weak} easy to guess. Worth changing on those sites "
                       f"(Edit, then Suggest a Strong One)." if issues else "None reused, none easy to guess.")
            listed = [settings._row("", summary, search)]
            for login in sorted(logins, key=lambda e: (password_store.site_of(e["origin"]), e["username"])):
                site = password_store.site_of(login["origin"])
                if wanted and wanted not in site and wanted not in login["username"].lower():
                    continue
                actions = QWidget()
                a = QHBoxLayout(actions)
                a.setContentsMargins(0, 0, 0, 0)
                for label, act in (("Copy", lambda _=False, e=login: copy(e)),
                                   ("Edit", lambda _=False, e=login: edit_login(e)),
                                   ("Remove", lambda _=False, e=login: remove_login(e))):
                    button = QPushButton(label)
                    button.clicked.connect(act)
                    a.addWidget(button)
                flags = {"reused": "⚠︎ reused", "weak": "⚠︎ easy to guess"}
                note = "".join(f" · {flags[i]}" for i in issues.get(login["id"], []))
                listed.append(settings._row(site, (login["username"] or "No username") + note, actions))
            cards.append(settings._card(listed))
        # iCloud Keychain
        icloud = QCheckBox()
        icloud.setEnabled(False)
        icloud.setToolTip("Needs a signed release of Eclipse")
        cards.append(settings._card([settings._row(
            "Use iCloud Keychain", "Coming once Eclipse is a signed release: macOS lets only signed browsers use "
                                   "iCloud Keychain. Passwords and passkeys saved here can then move over with "
                                   "macOS's import.", icloud)]))
        # Passkeys
        passkeys = sorted(passkey_store.all_passkeys(), key=lambda p: (p["rp_id"], p.get("user_name", "")))
        listed = []
        for passkey in passkeys:
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(lambda _=False, p=passkey: remove_passkey(p))
            when = time.strftime("%b %-d, %Y", time.localtime(passkey.get("created") or 0))
            note = ("" if passkey_store.has_key(passkey) else " · needs your recovery PIN" if recovery.is_set()
                    else " · not on this Mac (no recovery PIN was set)")
            listed.append(settings._row(f"Passkey · {passkey['rp_id']}",
                                        f"{passkey.get('user_name') or 'Your account'} · saved {when}{note}",
                                        remove_button))
        if not listed:
            listed = [settings._row("No passkeys yet", "When a site offers to make a passkey, Eclipse can save it: "
                                                       "you then sign in with Touch ID, no password to type.", None)]
        cards.append(settings._card(listed))
        outer.addWidget(settings._stack(*cards))

    refill_later = QTimer(page, singleShot=True, interval=250, timeout=fill)
    fill()
    return page
