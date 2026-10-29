# Arc Max's "Tidy Downloads": when a download finishes, Claude gives the file a name a person
# would choose ("IMG_3847.pdf" → "Acme Invoice March 2026.pdf"), from its original name, where it
# came from and, for PDFs and text files, how it starts. The extension never changes, nothing is
# ever overwritten or made into something that runs, and Claude can leave a name alone if it's already clear.

import re
import unicodedata
from pathlib import Path

from PyQt6.QtPdf import QPdfDocument

from .page_actions import not_reserved

TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".log", ".tsv"}
EXCERPT_CHARS = 1500
MAX_NAME = 80


def excerpt(path: Path) -> str:
    """The start of the file's text, if it has any we can read cheaply."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            doc = QPdfDocument(None)
            if doc.load(str(path)) != QPdfDocument.Error.None_ or doc.pageCount() == 0:
                return ""
            text = " ".join(doc.getAllText(page).text() for page in range(min(doc.pageCount(), 2)))
            doc.close()
            return text[:EXCERPT_CHARS]
        if suffix in TEXT_TYPES and path.stat().st_size < 5_000_000:
            with path.open(encoding="utf-8", errors="replace") as f:
                return f.read(EXCERPT_CHARS)
    except OSError:
        pass
    return ""


def build_prompt(name: str, source_url: str, page_title: str, text: str) -> str:
    parts = [f"A file just finished downloading in a web browser.\nFile name: {name}\nDownloaded from: {source_url}"]
    if page_title:
        parts.append(f"From the page: {page_title}")
    if text.strip():
        parts.append(f"It starts:\n<<<\n{' '.join(text.split())[:EXCERPT_CHARS]}\n>>>")
    parts.append("Suggest a clear, specific file name a person would choose, 2-8 words, in Title Case, like "
                 "'Acme Invoice March 2026' or 'Python Packaging Guide'. No extension, no dates unless they're "
                 "meaningful, no words like 'download' or 'file'. If the current name is already clear and "
                 "readable, reply KEEP. Reply with the name only.")
    return "\n\n".join(parts)


def clean_name(reply: str) -> str | None:
    """The suggested name made safe for a file name, or None to keep the current one."""
    lines = reply.strip().splitlines()
    name = lines[0].strip(" \"'“”‘’`*.") if lines else ""
    if not name or name.upper() == "KEEP":
        return None
    name = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)          # drop an extension Claude added anyway
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', " ", name)        # characters macOS or other systems won't take
    # Nor ones that change how the rest reads: a right-to-left override makes "Invoice\u202efdp.command" show as
    # "Invoicedmmoc.pdf" (and zero-width ones hide things)
    name = "".join(c for c in name if unicodedata.category(c) not in ("Cf", "Cc"))
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:MAX_NAME].strip() or None


def unique_path(folder: Path, stem: str, suffix: str, current: Path | None = None) -> Path:
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists() and candidate != current:
        candidate = folder / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


def new_path(path: Path, reply: str) -> Path | None:
    """Where Claude's name for the file puts it, or None to leave it. Never a name that makes it something that
    runs when opened: the reply can be steered by the page and the file's own text, and a file with no extension
    ("README", a script inside) named "Read Me.command" would become one, without the question a download of one
    asks."""
    from .downloads import risky
    stem = clean_name(reply)
    if stem is None or stem == path.stem:
        return None
    target = unique_path(path.parent, not_reserved(stem), path.suffix, current=path)
    return None if risky(target.name) and not risky(path.name) else target


def tidy(browser, item, request):
    """Ask Claude for a better name for a finished BrowserDownload, then rename it."""
    path = Path(item.path)
    if not path.exists() or not browser.feature("claude").claude.available:
        return
    page = request.page()
    title = page.title() if page is not None else ""
    ask = browser.feature("claude").claude.ask(build_prompt(path.name, request.url().toString(), title, excerpt(path)))

    def rename(reply):
        target = new_path(path, reply)
        if target is None or not path.exists():
            return
        try:
            path.rename(target)
        except OSError:
            return
        item.path, item.title = str(target), target.name
        item.original_name = path.name
        item.changed.emit()
        browser.toast(f"Renamed “{path.name}” to “{target.name}”")
    ask.finished.connect(rename)
