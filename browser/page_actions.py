# Small things you do with a page: print it, save it as a PDF or a web archive, copy a link to it
# formatted for Markdown. The window wires these to menus and shortcuts.

import re

# Names Windows keeps for devices, whatever follows a dot ("CON.pdf" is still the console): never a file's
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def not_reserved(name: str) -> str:
    """A file name Windows can make: one of its device names ("Con", "nul.tar") gets a "_" after it. Everywhere,
    not only there: a shared Space or a saved page may end up on a PC."""
    stem, dot, rest = name.partition(".")
    return f"{stem}_{dot}{rest}" if stem.rstrip(" ").upper() in RESERVED else name


def safe_filename(title: str, fallback: str = "Page") -> str:
    """A page title made safe to use as a file name (no slashes, colons or control characters, nor a name Windows
    keeps for a device)."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return not_reserved(name[:120].strip()) or fallback


def markdown_link(title: str, url: str) -> str:
    """[Title](url), with the brackets and parentheses that would break it escaped."""
    text = re.sub(r"([\[\]\\])", r"\\\1", title or url)
    target = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
    return f"[{text}]({target})"
