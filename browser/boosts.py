# Arc's Boosts: your own tweaks for a website, re-applied every time it loads.
#
# A Boost targets one site (and its subdomains) and can carry:
#   - CSS        restyle the site (fonts, colours, hide clutter)
#   - JavaScript run your own code on the page
#   - Zapped     elements you clicked away with the Zap tool, hidden for good
#
# Boosts are code you write for your own browser, so they run in the page itself
# (main world) where they can see and change everything, like Arc's.

import json
from dataclasses import asdict, dataclass, field

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWebEngineCore import QWebEngineScript
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout)

from .models import new_id
from .theme import stylesheet

SCRIPT_PREFIX = "pybrowser-boost-"
ZAP_MESSAGE = "PYBROWSER_ZAP:"
ZAP_DONE = "PYBROWSER_ZAP_DONE"


def site_of(url: str) -> str:
    return QUrl(url).host().lower().removeprefix("www.")


@dataclass
class Boost:
    site: str
    name: str = ""
    css: str = ""
    js: str = ""
    zapped: list[str] = field(default_factory=list)
    enabled: bool = True
    id: str = field(default_factory=new_id)

    @property
    def label(self) -> str:
        return self.name or self.site


# Runs inside the page while zapping: highlights what's under the mouse, and a click
# hides that element and reports a CSS selector for it back to the browser.
ZAP_SCRIPT = r"""
(() => {
  if (window.__pbZap) return;
  window.__pbZap = true;
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #f43f5e;' +
                      'background:rgba(244,63,94,.15);border-radius:4px;transition:all .05s';
  const banner = document.createElement('div');
  banner.textContent = '⚡ Zap: click anything to hide it · Esc when done';
  banner.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;' +
                         'background:#18181b;color:#fff;font:600 13px -apple-system,sans-serif;padding:8px 14px;' +
                         'border-radius:999px;box-shadow:0 6px 24px rgba(0,0,0,.3);pointer-events:none';
  document.documentElement.append(box, banner);
  const selector = (el) => {
    if (el.id && !/\d{3,}/.test(el.id)) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node !== document.body && parts.length < 5; node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      const classes = [...node.classList].filter(c => !/\d{3,}|^(is|has)-/.test(c)).slice(0, 2);
      if (classes.length) part += '.' + classes.map(c => CSS.escape(c)).join('.');
      const siblings = node.parentElement ? [...node.parentElement.children].filter(s => s.tagName === node.tagName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      parts.unshift(part);
      if (node.id && !/\d{3,}/.test(node.id)) { parts[0] = '#' + CSS.escape(node.id); break; }
    }
    return parts.join(' > ');
  };
  const move = (e) => {
    const r = e.target.getBoundingClientRect();
    Object.assign(box.style, {left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px'});
  };
  const click = (e) => {
    e.preventDefault(); e.stopPropagation();
    const sel = selector(e.target);
    e.target.style.setProperty('display', 'none', 'important');
    console.log('PYBROWSER_ZAP:' + sel);
  };
  const stop = (e) => {
    if (e.key !== 'Escape') return;
    document.removeEventListener('mousemove', move, true);
    document.removeEventListener('click', click, true);
    document.removeEventListener('keydown', stop, true);
    box.remove(); banner.remove(); window.__pbZap = false;
    console.log('PYBROWSER_ZAP_DONE');
  };
  document.addEventListener('mousemove', move, true);
  document.addEventListener('click', click, true);
  document.addEventListener('keydown', stop, true);
})();
"""


class BoostManager(QObject):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.profiles = []
        self.boosts = [Boost(**b) for b in store.settings.get("boosts", [])]

    def attach(self, profile):
        self.profiles.append(profile)
        self._install(profile)

    def for_site(self, site) -> list[Boost]:
        return [b for b in self.boosts if site == b.site or site.endswith("." + b.site)]

    def save(self, boost: Boost):
        if boost not in self.boosts:
            self.boosts.append(boost)
        self._changed()

    def delete(self, boost: Boost):
        self.boosts.remove(boost)
        self._changed()

    def add_zapped(self, site, selector) -> Boost:
        boost = next((b for b in self.boosts if b.site == site), None)
        if boost is None:
            boost = Boost(site, name=f"{site} (Zapped)")
            self.boosts.append(boost)
        if selector not in boost.zapped:
            boost.zapped.append(selector)
        self._changed()
        return boost

    def _changed(self):
        self.store.settings["boosts"] = [asdict(b) for b in self.boosts]
        self.store.schedule_save()
        for profile in self.profiles:
            self._install(profile)

    def _install(self, profile):
        scripts = profile.scripts()
        for script in scripts.toList():
            if script.name().startswith(SCRIPT_PREFIX):
                scripts.remove(script)
        for boost in self.boosts:
            if boost.enabled:
                scripts.insert(self._script(boost))

    @staticmethod
    def _script(boost: Boost) -> QWebEngineScript:
        css = boost.css
        if boost.zapped:
            css += "\n" + ",\n".join(boost.zapped) + " { display: none !important; }"
        source = f"""
        (() => {{
          const site = location.hostname.replace(/^www\\./, '');
          const target = {json.dumps(boost.site)};
          if (site !== target && !site.endsWith('.' + target)) return;
          const style = document.createElement('style');
          style.dataset.boost = {json.dumps(boost.id)};
          style.textContent = {json.dumps(css)};
          const add = () => (document.head || document.documentElement).appendChild(style);
          document.documentElement ? add() : document.addEventListener('DOMContentLoaded', add);
          // new Function() keeps a syntax error in your JS from also breaking the CSS above
          const run = () => {{ try {{ new Function({json.dumps(boost.js)})(); }} catch (e) {{ console.error('Boost error:', e); }} }};
          document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', run) : run();
        }})();
        """
        script = QWebEngineScript()
        script.setName(SCRIPT_PREFIX + boost.id)
        script.setSourceCode(source)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        return script


def _code_editor(text, placeholder):
    editor = QPlainTextEdit(text)
    editor.setPlaceholderText(placeholder)
    font = QFont("Menlo")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(12)
    editor.setFont(font)
    editor.setTabStopDistance(editor.fontMetrics().horizontalAdvance(" ") * 2)
    editor.setMinimumHeight(140)
    return editor


class BoostEditor(QDialog):
    def __init__(self, manager: BoostManager, boost: Boost, theme, parent):
        super().__init__(parent)
        self.manager = manager
        self.boost = boost
        self.setObjectName("BoostEditor")
        self.setProperty("panel", True)  # the shared panel styling (theme.py)
        self.setWindowTitle(f"Boost · {boost.site}")
        self.setStyleSheet(stylesheet(theme))
        self.resize(640, 680)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(boost.name)
        self.name.setPlaceholderText(boost.site)
        self.site = QLineEdit(boost.site)
        self.enabled = QCheckBox("Boost is on")
        self.enabled.setChecked(boost.enabled)
        form.addRow("Name", self.name)
        form.addRow("Website", self.site)
        form.addRow("", self.enabled)
        layout.addLayout(form)

        layout.addWidget(QLabel("CSS"))
        self.css = _code_editor(boost.css, "body { font-family: Georgia, serif; }\n.sidebar { display: none; }")
        layout.addWidget(self.css)
        layout.addWidget(QLabel("JavaScript (runs once the page is ready)"))
        self.js = _code_editor(boost.js, "document.querySelectorAll('video').forEach(v => v.playbackRate = 1.5);")
        layout.addWidget(self.js)

        layout.addWidget(QLabel("Zapped elements (hidden)"))
        self.zapped = QListWidget()
        self.zapped.addItems(boost.zapped)
        self.zapped.setMaximumHeight(110)
        layout.addWidget(self.zapped)
        unzap = QPushButton("Un-zap Selected")
        unzap.clicked.connect(lambda: [self.zapped.takeItem(self.zapped.row(i)) for i in self.zapped.selectedItems()])

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        delete = buttons.addButton("Delete Boost", QDialogButtonBox.ButtonRole.DestructiveRole)
        delete.setVisible(boost in manager.boosts)
        delete.clicked.connect(self._delete)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(unzap)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

    def _save(self):
        b = self.boost
        b.name = self.name.text().strip()
        b.site = site_of("https://" + self.site.text().strip()) or b.site
        b.enabled = self.enabled.isChecked()
        b.css = self.css.toPlainText()
        b.js = self.js.toPlainText()
        b.zapped = [self.zapped.item(i).text() for i in range(self.zapped.count())]
        self.manager.save(b)
        self.accept()

    def _delete(self):
        self.manager.delete(self.boost)
        self.accept()
