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
    assert "acme corporation" in excerpt(pdf).lower()  # (offscreen on Windows, the stand-in font reads back in capitals)
    notes = tmp_path / "x.txt"
    notes.write_text("Meeting notes for the launch")
    assert excerpt(notes).startswith("Meeting notes")
    assert excerpt(tmp_path / "missing.pdf") == ""


def test_prompt_mentions_what_claude_needs():
    prompt = build_prompt("dl_83jd.pdf", "https://acme.com/billing/83jd", "Billing · Acme", "Invoice 1042")
    assert "dl_83jd.pdf" in prompt and "acme.com" in prompt and "Invoice 1042" in prompt and "KEEP" in prompt


def test_a_cancelled_video_download_leaves_nothing_of_its_own_behind(tmp_path):
    """Cancelled, yt-dlp left its partial files, resume notes and unmerged streams in your Downloads folder."""
    from browser.downloads import leftovers
    video = tmp_path / "Talk [abc123].f137.mp4"
    audio = tmp_path / "Talk [abc123].f140.m4a"
    final = tmp_path / "Talk [abc123].mkv"
    made = [video.with_name(video.name + ".part"), video.with_name(video.name + ".ytdl"),
            video.with_name(video.name + ".part-Frag3"), audio]
    yours = [tmp_path / "Talk [abc123].pdf", tmp_path / "Another video.mp4", final]
    for path in made + yours:
        path.write_text("x")
    found = leftovers([str(video), str(audio), str(final)])
    assert sorted(found) == sorted(made)
    assert all(p.exists() for p in yours)  # (its finished file, and anything else there, is left alone)


def test_a_tar_archive_keeps_its_whole_extension(tmp_path):
    """Only ".gz" was kept: "project-1.2.tar.gz" became "Project Source Archive.gz", which unpacked to a file
    with no extension where it was a folder."""
    from browser.tidy_downloads import new_path
    archive = tmp_path / "project-1.2.tar.gz"
    archive.write_text("x")
    assert new_path(archive, "Project Source Archive").name == "Project Source Archive.tar.gz"
    assert new_path(archive, "Project Source Archive.tar.gz").name == "Project Source Archive.tar.gz"
    plain = tmp_path / "scan 3.pdf"
    plain.write_text("x")
    assert new_path(plain, "Lease Agreement").name == "Lease Agreement.pdf"


def test_a_big_pdf_isnt_read_for_its_name(tmp_path, monkeypatch):
    """Read as it finished, on the window's own thread: a big or scanned PDF froze the window while it loaded."""
    from browser import tidy_downloads
    big = tmp_path / "scan.pdf"
    big.write_bytes(b"%PDF-1.4\n" + b"0" * 64)
    monkeypatch.setattr(tidy_downloads, "MAX_PDF_BYTES", 32)
    loaded = []
    monkeypatch.setattr(tidy_downloads, "QPdfDocument", lambda parent: loaded.append(1))
    assert tidy_downloads.excerpt(big) == "" and not loaded
