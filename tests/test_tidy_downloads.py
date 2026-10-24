from pathlib import Path

from PyQt6.QtGui import QPageSize, QPainter, QPdfWriter

from browser.tidy_downloads import build_prompt, clean_name, excerpt, unique_path


def test_clean_name():
    assert clean_name("Acme Invoice March 2026") == "Acme Invoice March 2026"
    assert clean_name('"Acme Invoice.pdf"\nbecause…') == "Acme Invoice"
    assert clean_name("KEEP") is None and clean_name("  ") is None
    assert clean_name("Q3: Budget / Forecast?") == "Q3 Budget Forecast"
    assert len(clean_name("word " * 40)) <= 80


def test_unique_path(tmp_path):
    (tmp_path / "Report.pdf").write_text("x")
    assert unique_path(tmp_path, "Report", ".pdf") == tmp_path / "Report (2).pdf"
    assert unique_path(tmp_path, "Report", ".pdf", current=tmp_path / "Report.pdf") == tmp_path / "Report.pdf"


def test_excerpt_reads_pdfs_and_text(tmp_path):
    pdf = tmp_path / "scan_0001.pdf"
    writer = QPdfWriter(str(pdf))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    painter = QPainter(writer)
    painter.drawText(200, 400, "Invoice 1042 from Acme Corporation")
    painter.end()
    assert "Acme Corporation" in excerpt(pdf)
    notes = tmp_path / "x.txt"
    notes.write_text("Meeting notes for the launch")
    assert excerpt(notes).startswith("Meeting notes")
    assert excerpt(tmp_path / "missing.pdf") == ""


def test_prompt_mentions_what_claude_needs():
    prompt = build_prompt("dl_83jd.pdf", "https://acme.com/billing/83jd", "Billing · Acme", "Invoice 1042")
    assert "dl_83jd.pdf" in prompt and "acme.com" in prompt and "Invoice 1042" in prompt and "KEEP" in prompt
