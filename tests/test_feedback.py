"""Send Feedback: what goes in a report (nothing is sent from here)."""

import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser import feedback


def test_details_hold_versions_recent_logs_and_not_your_account_name(tmp_path):
    (tmp_path / "errors.log").write_text("\n".join(f"line {i}" for i in range(100)) +
                                         f"\nFile \"{Path.home()}/projects/x.py\", line 3")
    (tmp_path / "native-crash-20261001-090000.log").write_text("Fatal Python error: Segmentation fault")
    old = tmp_path / "native-crash-20250101-090000.log"
    old.write_text("an old crash")
    os.utime(old, (time.time() - 60 * 86400,) * 2)
    text = feedback.details(tmp_path)
    assert text.startswith("Eclipse from source\nmacOS ") and "Qt 6." in text
    assert "line 99" in text and "line 30" not in text  # (the last lines of the log only)
    assert 'File "~/projects/x.py"' in text and str(Path.home()) not in text
    assert "Segmentation fault" in text and "an old crash" not in text


def test_nothing_recent_means_just_versions(tmp_path):
    assert feedback.details(tmp_path).count("\n") == 3


def test_a_long_report_is_trimmed_to_fit_an_address():
    body = "It crashed when I opened Settings.\n\n---\n" + "x" * 20000
    url = feedback.issue_url("Crash: It crashed", body)
    assert len(url) <= feedback.MAX_URL and url.startswith(feedback.ISSUES + "?")
    sent = parse_qs(urlparse(url).query)
    assert sent["title"] == ["Crash: It crashed"]
    assert sent["body"][0].startswith("It crashed when I opened Settings.") and "trimmed" in sent["body"][0]
    short = parse_qs(urlparse(feedback.issue_url("t", "short")).query)["body"]
    assert short == ["short"]


def test_the_report_is_what_you_wrote_then_the_details(tmp_path):
    assert feedback.report("  Tabs vanished  ", False) == "Tabs vanished"
    assert feedback.report("", True, tmp_path).startswith("(nothing written)\n\n---\nEclipse ")


def test_web_addresses_lose_their_queries_and_emails_are_hidden(tmp_path):
    (tmp_path / "errors.log").write_text("loading https://bank.example/account?token=s3cret#x failed for me@example.com\n"
                                         "and https://plain.example/page was fine")
    text = feedback.details(tmp_path)
    assert "https://bank.example/account?…" in text and "s3cret" not in text
    assert "[email]" in text and "me@example.com" not in text and "https://plain.example/page" in text
