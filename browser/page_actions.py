# Small things you do with a page: print it, save it as a PDF or a web archive, copy a link to it
# formatted for Markdown. The window wires these to menus and shortcuts.

import re


def safe_filename(title: str, fallback: str = "Page") -> str:
    """A page title made safe to use as a file name (no slashes, colons or control characters)."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120].strip() or fallback


def markdown_link(title: str, url: str) -> str:
    """[Title](url), with the brackets and parentheses that would break it escaped."""
    text = re.sub(r"([\[\]\\])", r"\\\1", title or url)
    target = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
    return f"[{text}]({target})"
