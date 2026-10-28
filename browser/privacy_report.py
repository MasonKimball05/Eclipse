# Privacy Report: what each site tried, and what Eclipse stopped. From the shield in the address pill ("Privacy
# Report for <site>…") or ⌘T ("Privacy Report"): the trackers blocked there over the last 30 days, by the
# company behind them where it's a well-known one, and the links whose tracking was taken off. The whole report
# shows the same across every site, with the sites that tried hardest.
#
# The blocker (adblock.py) says what it blocks; this keeps a count per day, in a small file in your data folder,
# for 30 days. Nothing from private windows (their blocker reports nothing), and it's cleared with history.

import datetime
import html

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWidgets import QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

from . import storage
from .adblock import _site
from .features import Feature
from .models import display_host
from .motion import FadesIn
from .theme import quiet_scrollbars, stylesheet

DAYS = 30
SAVE_MS = 60 * 1000
FILE = "privacy_report.json"
# The companies behind the trackers seen most (by the tracker's site): anything else is named by its site
OWNERS = {
    "google-analytics.com": "Google", "googletagmanager.com": "Google", "doubleclick.net": "Google",
    "googlesyndication.com": "Google", "googleadservices.com": "Google", "googletagservices.com": "Google",
    "2mdn.net": "Google", "adservice.google.com": "Google", "app-measurement.com": "Google",
    "facebook.net": "Meta", "facebook.com": "Meta", "fbcdn.net": "Meta", "instagram.com": "Meta",
    "amazon-adsystem.com": "Amazon", "bing.com": "Microsoft", "clarity.ms": "Microsoft",
    "adnxs.com": "Microsoft (Xandr)", "linkedin.com": "LinkedIn", "licdn.com": "LinkedIn",
    "ads-twitter.com": "X", "twitter.com": "X", "tiktok.com": "TikTok", "tiktokv.com": "TikTok",
    "pinterest.com": "Pinterest", "snapchat.com": "Snap", "sc-static.net": "Snap", "reddit.com": "Reddit",
    "redditstatic.com": "Reddit", "criteo.com": "Criteo", "criteo.net": "Criteo", "taboola.com": "Taboola",
    "outbrain.com": "Outbrain", "hotjar.com": "Hotjar", "hotjar.io": "Hotjar", "segment.io": "Segment",
    "segment.com": "Segment", "mixpanel.com": "Mixpanel", "amplitude.com": "Amplitude",
    "fullstory.com": "FullStory", "quantserve.com": "Quantcast", "scorecardresearch.com": "Comscore",
    "chartbeat.com": "Chartbeat", "chartbeat.net": "Chartbeat", "newrelic.com": "New Relic",
    "nr-data.net": "New Relic", "rubiconproject.com": "Magnite", "pubmatic.com": "PubMatic",
    "openx.net": "OpenX", "casalemedia.com": "Index Exchange", "adsrvr.org": "The Trade Desk",
    "moatads.com": "Oracle (Moat)", "bluekai.com": "Oracle", "yahoo.com": "Yahoo", "yieldmo.com": "Yieldmo",
    "hubspot.com": "HubSpot", "hs-analytics.net": "HubSpot", "optimizely.com": "Optimizely",
}


def owner(tracker_site: str) -> str:
    return OWNERS.get(tracker_site, tracker_site)


class PrivacyLog:
    """Counts per day: {"2026-10-28": {site: {"trackers": {tracker site: n}, "cleaned": n}}}."""

    def __init__(self, path):
        self.path = path
        self.days: dict = storage.read_json(path) or {}
        self.dirty = False
        self.prune()

    @staticmethod
    def today() -> str:
        return datetime.date.today().isoformat()

    def _day(self, site):
        return self.days.setdefault(self.today(), {}).setdefault(site, {"trackers": {}, "cleaned": 0})

    def tracker(self, site, tracker):
        trackers = self._day(site)["trackers"]
        trackers[tracker] = trackers.get(tracker, 0) + 1
        self.dirty = True

    def cleaned(self, site):
        self._day(site)["cleaned"] += 1
        self.dirty = True

    def prune(self):
        oldest = (datetime.date.today() - datetime.timedelta(days=DAYS - 1)).isoformat()
        for day in [d for d in self.days if d < oldest]:
            del self.days[day]
            self.dirty = True

    def clear(self):
        self.days, self.dirty = {}, True
        self.save()

    def save(self):
        if self.dirty:
            self.dirty = False
            storage.write_json(self.path, self.days, private=True)

    def report(self, site=None) -> dict:
        """The last 30 days, for one site (None: every site): {"trackers": {tracker: n}, "cleaned": n,
        "sites": {site: trackers blocked there}, "days": days with anything}."""
        trackers, sites, cleaned, days = {}, {}, 0, set()
        for day, by_site in self.days.items():
            for where, counts in by_site.items():
                if site is not None and where != site:
                    continue
                days.add(day)
                cleaned += counts.get("cleaned", 0)
                for tracker, n in counts.get("trackers", {}).items():
                    trackers[tracker] = trackers.get(tracker, 0) + n
                    sites[where] = sites.get(where, 0) + n
        return {"trackers": trackers, "cleaned": cleaned, "sites": sites, "days": len(days)}


def report_html(report: dict, site: str | None) -> str:
    """The report as a page: what was blocked, by company."""
    e = html.escape
    total = sum(report["trackers"].values())
    companies: dict[str, dict] = {}
    for tracker, n in report["trackers"].items():
        company = companies.setdefault(owner(tracker), {"count": 0, "sites": set()})
        company["count"] += n
        company["sites"].add(tracker)
    where = f"on {e(site)}" if site else "across every site"
    parts = [f"<h2>Privacy Report</h2><p style='opacity:.7'>The last {DAYS} days, {where}.</p>"]
    if not total and not report["cleaned"]:
        parts.append("<p>Nothing blocked yet. Trackers this site tries to load will show here.</p>" if site else
                     "<p>Nothing blocked yet.</p>")
        return "".join(parts)
    noun = "tracker" if total == 1 else "trackers"
    parts.append(f"<p><b style='font-size:22px'>{total:,}</b> {noun} blocked"
                 + (f" from <b>{len(companies)}</b> compan{'y' if len(companies) == 1 else 'ies'}" if total else "")
                 + "</p>")
    if report["cleaned"]:
        parts.append(f"<p><b>{report['cleaned']:,}</b> link{'s' if report['cleaned'] != 1 else ''} had tracking "
                     "taken off before they opened.</p>")
    if companies:
        parts.append("<h3>Who tried to track you</h3><table width='100%' cellpadding='4'>")
        for company, info in sorted(companies.items(), key=lambda c: -c[1]["count"])[:25]:
            hosts = ", ".join(sorted(info["sites"])[:4])
            parts.append(f"<tr><td><b>{e(company)}</b><br><span style='opacity:.6'>{e(hosts)}</span></td>"
                         f"<td align='right'>{info['count']:,}</td></tr>")
        parts.append("</table>")
    if site is None and report["sites"]:
        parts.append("<h3>Sites that tried hardest</h3><table width='100%' cellpadding='4'>")
        for where_site, n in sorted(report["sites"].items(), key=lambda s: -s[1])[:15]:
            parts.append(f"<tr><td>{e(where_site)}</td><td align='right'>{n:,}</td></tr>")
        parts.append("</table>")
    return "".join(parts)


class ReportWindow(FadesIn, QWidget):
    def __init__(self, browser, site):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"Privacy Report · {site}" if site else "Privacy Report")
        self.setProperty("panel", True)
        self.setStyleSheet(stylesheet(browser.theme))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(520, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 14)
        self.text = QTextBrowser()
        self.text.setObjectName("Answer")
        self.text.setOpenLinks(False)
        self.text.setHtml(report_html(browser.feature("privacy_report").log.report(site), site))
        quiet_scrollbars(self.text)
        layout.addWidget(self.text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)


class PrivacyReportFeature(Feature):
    name = "privacy_report"

    def __init__(self, window):
        super().__init__(window)
        self.log = PrivacyLog(storage.data_dir() / FILE)
        window.adblock.tracker_blocked.connect(self.log.tracker)
        window.adblock.link_cleaned.connect(self.log.cleaned)
        self.timer = QTimer(window, interval=SAVE_MS, timeout=lambda: (self.log.prune(), self.log.save()))
        self.timer.start()
        self.shown = None

    def show(self, site=None):
        """The report for a site (None: every site)."""
        self.shown = ReportWindow(self.window, site)
        self.shown.show()
        return self.shown

    def site_menu(self, menu, tab):
        host = QUrl(tab.url).host() if tab and tab.url else ""
        if host:
            site = _site(host)
            menu.addAction(f"Privacy Report for {display_host(tab.url)}…").triggered.connect(lambda: self.show(site))

    def commands(self):
        return [("Privacy Report", "ph.shield-check", self.show)]

    def history_cleared(self):
        self.log.clear()

    def shutdown(self):
        self.log.prune()
        self.log.save()


FEATURE = PrivacyReportFeature
