# The window side of My Apps (see integrations.py): the homebase menu and its logs, Send To, the
# security grade card, adding your apps' Live Folders, and the Settings page.

import html
import json
import re

from PyQt6.QtCore import QObject, Qt, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from . import keychain
from .integrations import (DEFAULTS, ISSUE_TEXT, SETTING, STATE_DOTS, away_task_text, base, config, daybook_url,
                           github_issue, hop_quicklinks, job_tracker_url, live_folder_presets, local_clone,
                           matching_projects, open_external, open_in_app, search_shortcut_presets, summarize)
from .live_folders import token_account
from .models import display_host
from .theme import dot_icon, stylesheet


def _dialog(browser, title, width=520):
    dialog = QDialog(browser)
    dialog.setWindowTitle(title)
    dialog.setStyleSheet(stylesheet(browser.theme))
    dialog.setProperty("panel", True)
    dialog.resize(width, 0)
    return dialog


class AppsUI(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.net = QNetworkAccessManager(self)

    @property
    def cfg(self):
        return config(self.browser.store.settings)

    # --- Homebase menu ---------------------------------------------------------------------------

    def homebase_menu(self, pos):
        b = self.browser
        hb = b.homebase
        menu = b._menu()
        if hb.status is None:
            menu.addAction(f"Homebase isn't reachable · {hb.error or 'offline'}").setEnabled(False)
            menu.addAction("Is Tailscale on, and the desktop awake?").setEnabled(False)
        else:
            up, total, apps = summarize(hb.status)
            menu.addAction(f"{hb.status.get('host', 'Homebase')} · {up} of {total} apps up").setEnabled(False)
            menu.addSeparator()
            for app in apps:
                state = app.get("state", "")
                healthy = (app.get("health") or {}).get("ok", True)
                label = app.get("title") or app.get("name")
                note = "" if state in ("running", "external") and healthy else f"  ·  {state if healthy else 'unhealthy'}"
                sub = b._submenu(menu, "ph.app-window", label + note)
                sub.setIcon(dot_icon(STATE_DOTS.get(state, "#8e8e93") if healthy else "#ff9f0a", 10))
                name = app.get("name")
                if app.get("url"):
                    b._act(sub, "ph.arrow-square-out", "Open", lambda u=app["url"]: b.open_url(u))
                if state in ("stopped", "crashed", "backoff"):
                    b._act(sub, "ph.play", "Start", lambda n=name: self._act(n, "start"))
                elif state != "external":
                    b._act(sub, "ph.arrows-clockwise", "Restart", lambda n=name: self._act(n, "restart"))
                    b._act(sub, "ph.stop", "Stop", lambda n=name: self._act(n, "stop"))
                b._act(sub, "ph.file-text", "Show Logs", lambda n=name, t=label: self.show_logs(n, t))
        if hb.sites:
            menu.addSeparator()
            menu.addAction("Your Sites (go-sentinel)").setEnabled(False)
            for site in hb.sites:
                status = site.get("status", "")
                problems = [r.get("detail", "") for r in site.get("results") or [] if r.get("status") != "ok"]
                host = QUrl(site.get("url", "")).host() or site.get("name", "")
                tls = next((r.get("detail", "") for r in site.get("results") or [] if r.get("check") == "tls"), "")
                until = re.search(r"\((\d{4})-(\d{2})-(\d{2})\)", tls)
                if problems:
                    label = f"{host}  ·  {problems[0]}"
                else:
                    label = f"{host}  ·  all checks pass"
                    if until:
                        month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[int(until.group(2)) - 1]
                        label += f"  ·  certificate until {month} {int(until.group(3))}"
                action = menu.addAction(dot_icon("#30d158" if status == "ok" else "#ff9f0a" if status == "warn"
                                                 else "#ff453a", 10), label[:80],
                                        lambda u=site.get("url", ""): u and b.open_url(u))
                action.setToolTip("\n".join(f"{r.get('check')}: {r.get('detail')}" for r in site.get("results") or []))
        menu.addSeparator()
        b._act(menu, "ph.gauge", "Open Dashboard", lambda: b.open_url(hb.url + "/"))
        b._act(menu, "ph.chat-circle-dots", "Local AI Chat", lambda: b.open_url(hb.url + "/chat.html"))
        b._act(menu, "ph.folders", "Add Live Folders for My Apps…", self.add_live_folders)
        b._act(menu, "ph.arrows-clockwise", "Refresh", hb.refresh)
        menu.exec(pos)

    def _act(self, name, action):
        self.browser.toast(f"{action.capitalize()}ing {name}…" if action != "stop" else f"Stopping {name}…")
        self.browser.homebase.act(name, action, lambda error: error and self.browser.toast(f"Couldn't {action} "
                                                                                           f"{name}: {error}"))

    def show_logs(self, name, title):
        dialog = _dialog(self.browser, f"{title} · Logs", 760)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Menlo", 11))
        text.setPlainText("Loading…")
        layout.addWidget(text)
        refresh = QPushButton("Refresh")
        layout.addWidget(refresh, 0, Qt.AlignmentFlag.AlignRight)

        def load():
            def got(lines, error):
                if error:
                    text.setPlainText(f"Couldn't read the logs: {error}")
                    return
                text.setPlainText("\n".join(f"{(l.get('time') or '')[11:19]}  {'!' if l.get('stream') == 'err' else ' '} "
                                            f"{l.get('text', '')}" for l in lines[-400:]) or "(nothing logged yet)")
                text.verticalScrollBar().setValue(text.verticalScrollBar().maximum())
            self.browser.homebase.logs(name, got)
        refresh.clicked.connect(load)
        dialog.resize(760, 520)
        load()
        dialog.show()

    # --- Send To -----------------------------------------------------------------------------------

    def send_to_actions(self, tab) -> list[tuple[str, str, object]]:
        """(label, icon, action) for the current page, for ⌘T and the page's right-click menu."""
        if tab is None or not tab.url.startswith(("http://", "https://")):
            return []
        cfg, out = self.cfg, []
        if base(cfg["jobtracker"]):
            out.append(("Save Job Posting to Job Tracker", "ph.briefcase",
                        lambda: self.browser.open_url(job_tracker_url(cfg, tab.url))))
        out.append(("Add Page to Daybook", "ph.calendar-check",
                    lambda: open_external(daybook_url(tab.label, tab.url)) and self.browser.toast("Added to Daybook")))
        if base(cfg["checkup"]):
            out.append(("Security Grade for This Site", "ph.shield-check", lambda: self.security_grade(tab.url)))
        clone = local_clone(tab.url, cfg.get("repos_root"))
        if clone:
            out.append(("Open Repo in Loom", "ph.code", lambda: open_in_app("Loom", clone)))
        if github_issue(tab.url) and base(cfg["homebase"]):
            out.append(("Give Issue to the Away Queue", "ph.robot", lambda: self.issue_to_away(tab)))
        return out

    def issue_to_away(self, tab):
        """Queue a GitHub issue for homebase's Away runner (the local model), after you've checked it."""
        owner, repo, number = github_issue(tab.url)
        hb = self.browser.homebase

        def got_projects(projects, error):
            if error:
                self.browser.toast(f"Couldn't reach homebase: {error}")
                return
            choices = matching_projects(repo, projects) or projects
            if not choices:
                self.browser.toast("homebase has no projects")
                return
            tab.view.page().runJavaScript(ISSUE_TEXT, 1, lambda found: self._confirm_away(
                tab, owner, repo, number, found if isinstance(found, dict) else {}, choices, projects))
        hb.projects(got_projects)

    def _confirm_away(self, tab, owner, repo, number, found, choices, projects):
        dialog = _dialog(self.browser, "Give Issue to the Away Queue", 560)
        form = QFormLayout(dialog)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        project = QComboBox()
        for name in choices + [p for p in projects if p not in choices]:
            project.addItem(name)
        task = QPlainTextEdit(away_task_text(owner, repo, number, found.get("title") or tab.label,
                                             found.get("body", ""), tab.url))
        task.setMinimumHeight(220)
        note = QLabel("The local model works on it in its own branch next time the desktop is idle; you "
                      "review and accept it from homebase.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        form.addRow("Project", project)
        form.addRow("Task", task)
        form.addRow("", note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Queue It", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or not task.toPlainText().strip():
            return
        name = project.currentText()
        self.browser.homebase.add_away_task(name, task.toPlainText().strip(), lambda error: self.browser.toast(
            f"Couldn't queue it: {error}" if error else f"Queued for {name} on homebase"))

    def security_grade(self, url):
        q = QUrl(url)
        site = f"{q.scheme()}://{q.host()}"
        self.browser.toast(f"Checking {display_host(url)}…")
        endpoint = QUrl(base(self.cfg["checkup"]) + "/api/check?url=" + QUrl.toPercentEncoding(site).data().decode())
        request = QNetworkRequest(endpoint)
        request.setTransferTimeout(45_000)
        reply = self.net.get(request)

        def finished():
            data, ok = bytes(reply.readAll()), reply.error() == QNetworkReply.NetworkError.NoError
            reply.deleteLater()
            try:
                report = json.loads(data)
            except ValueError:
                report = None
            if not ok or not isinstance(report, dict) or "grade" not in report:
                self.browser.toast(f"Couldn't check {display_host(url)}: {reply.errorString() if not ok else 'odd answer'}")
                return
            self._show_grade(display_host(url), report)
        reply.finished.connect(finished)

    def _show_grade(self, host, report):
        dialog = _dialog(self.browser, f"Security · {host}", 520)
        layout = QVBoxLayout(dialog)
        grade = str(report.get("grade", "?"))[:3]
        color = {"A": "#30d158", "B": "#64d2ff", "C": "#ffd60a", "D": "#ff9f0a"}.get(grade[:1], "#ff453a")
        score = report.get("score", 0)
        score = score if isinstance(score, (int, float)) else 0
        big = QLabel(f"<span style='font-size:44px; font-weight:700; color:{color}'>{html.escape(grade)}</span>"
                     f"&nbsp;&nbsp;<span style='font-size:15px'>{score:g}/100 · {html.escape(host)}</span>")
        big.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(big)
        for finding in report.get("results") or []:
            status = str(finding.get("status", ""))
            mark = {"pass": "✓", "ok": "✓", "warn": "!", "fail": "✗"}.get(status.lower(), "·")
            line = QLabel(f"{mark}  {finding.get('check', '')}: {finding.get('detail', '')}"
                          + (f"\n     {finding['tip']}" if finding.get("tip") else ""))
            line.setTextFormat(Qt.TextFormat.PlainText)  # the site's own headers end up in these
            line.setWordWrap(True)
            layout.addWidget(line)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.show()

    # --- Live Folders and search shortcuts ----------------------------------------------------------

    def add_live_folders(self):
        b = self.browser
        presets = live_folder_presets(self.cfg)
        dialog = _dialog(b, "Live Folders for My Apps")
        layout = QVBoxLayout(dialog)
        intro = QLabel(f"Add to the “{b.store.active_space.name}” Space:")
        layout.addWidget(intro)
        existing = {(f.live.get("kind"), f.live.get("url"), f.live.get("root")) for f in b.live_folders.folders()}
        boxes = []
        for preset in presets:
            box = QCheckBox(f"{preset['name']} — {preset['about']}")
            have = (preset["kind"], preset.get("url"), preset.get("root")) in existing
            box.setEnabled(not have)
            box.setChecked(False)
            if have:
                box.setText(box.text() + "  (added)")
            boxes.append((box, preset))
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Add", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        added = 0
        for box, preset in boxes:
            if box.isChecked() and box.isEnabled() and preset.get("subscribe"):
                # Its address is personal: open where you get it, and the dialog to paste it into
                b.open_url(preset["subscribe"])
                b.new_live_folder({k: v for k, v in preset.items() if k not in ("id", "about", "subscribe")})
                continue
            if box.isChecked() and box.isEnabled():
                live = {k: v for k, v in preset.items() if k not in ("id", "name", "about")}
                live["named"] = True
                b.create_live_folder(live, preset["name"])
                added += 1
        if added:
            b.toast(f"Added {added} Live Folder{'s' if added != 1 else ''}")

    def add_search_shortcuts(self, entries=None):
        from .command_bar import CUSTOM_SETTING, use_custom_sites
        settings = self.browser.store.settings
        mine = settings.setdefault(CUSTOM_SETTING, [])
        have = {e["keyword"] for e in mine}
        new = [e for e in (entries if entries is not None else search_shortcut_presets(self.cfg))
               if e["keyword"] not in have]
        mine.extend(new)
        use_custom_sites(mine)
        self.browser.store.schedule_save()
        self.browser.toast(f"Added {', '.join(e['keyword'] for e in new)}" if new else "You have those already")

    # --- Settings → My Apps ----------------------------------------------------------------------

    def settings_page(self, settings_window):
        """The My Apps page: where your apps live, who answers questions, and quick setup."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        store = self.browser.store
        cfg = self.cfg
        form_card = QFrame()
        form_card.setObjectName("OptionsCard")
        form = QFormLayout(form_card)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        fields = {}
        for key, label in (("homebase", "Homebase"), ("parliament", "Parliament"), ("jobtracker", "Job Tracker"),
                           ("gradtrack", "gradtrack"), ("sentinel", "go-sentinel"), ("checkup", "Sentinel checkup"),
                           ("portfolio", "Portfolio"),
                           ("github_user", "GitHub user"), ("repos_root", "Repos folder")):
            edit = QLineEdit(cfg.get(key, ""))
            edit.setPlaceholderText(DEFAULTS.get(key, ""))
            edit.editingFinished.connect(lambda k=key, e=edit: self._save(k, e.text().strip()))
            fields[key] = edit
            form.addRow(label, edit)
        hint = QLabel("Clear an address to leave that app out. The desktop ones are reached over Tailscale.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        form.addRow("", hint)
        layout.addWidget(form_card)

        ai_card = QFrame()
        ai_card.setObjectName("OptionsCard")
        ai = QFormLayout(ai_card)
        ai.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        who = QComboBox()
        who.addItem("Claude", "claude")
        who.addItem("Local AI on homebase", "local")
        who.setCurrentIndex(1 if cfg["ai"] == "local" else 0)
        who.currentIndexChanged.connect(lambda: self._save("ai", who.currentData()))
        model = QComboBox()
        model.setEditable(True)
        model.addItem(cfg["local_model"])
        model.currentTextChanged.connect(lambda text: text.strip() and self._save("local_model", text.strip()))
        self.browser.local_ai.models(lambda names: [model.addItem(n) for n in names if n != cfg["local_model"]])
        ai.addRow("Questions answered by", who)
        ai.addRow("Local model", model)
        note = QLabel("For ⌘T questions and Ask on Page (⌘F, then a question). The other one is always a row "
                      "below in ⌘T. Tidy, Translate and Browse for Me stay with Claude.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        ai.addRow("", note)
        layout.addWidget(ai_card)

        setup = QFrame()
        setup.setObjectName("OptionsCard")
        sv = QVBoxLayout(setup)
        add_folders = QPushButton("Add Live Folders for My Apps…")
        add_folders.clicked.connect(self.add_live_folders)
        add_search = QPushButton("Add Search Shortcuts (parl, mygh)")
        add_search.clicked.connect(self.add_search_shortcuts)
        widgets = [add_folders, add_search]
        hop = hop_quicklinks()
        if hop:
            add_hop = QPushButton(f"Import Hop's Quicklinks ({', '.join(e['keyword'] for e in hop[:6])}"
                                  f"{'…' if len(hop) > 6 else ''})")
            add_hop.clicked.connect(lambda: self.add_search_shortcuts(hop_quicklinks()))
            widgets.append(add_hop)
        for widget in widgets:
            sv.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(setup)
        layout.addStretch(1)
        return page

    def _save(self, key, value):
        settings = self.browser.store.settings
        mine = settings.setdefault(SETTING, {})
        if value == DEFAULTS.get(key):
            mine.pop(key, None)
        else:
            mine[key] = value
        self.browser.store.schedule_save()
        if key == "homebase":
            self.browser.homebase.refresh()
            self.browser.sidebar.update_homebase()

    @staticmethod
    def set_token(folder, token) -> bool:
        return bool(token) and keychain.store(token_account(folder), token)

    @staticmethod
    def forget_token(folder):
        keychain.delete(token_account(folder))
