# HTTPS from Eclipse's own Python (the update check and download, the breached-password check), trusting what
# macOS trusts. (Pages don't come through here: Chromium checks their certificates itself.)
#
# The release app's Python is python.org's, whose OpenSSL looks for trusted certificates in a file that only
# exists where its installer has been run, so on its own it trusts nobody and every connection fails
# ("certificate verify failed"). Rather than ship a list of certificates that would need keeping up to date,
# the ones macOS trusts are read from its own keychains (the same list Safari uses), once, when first needed.

import os
import ssl
import subprocess
import threading

KEYCHAINS = ["/System/Library/Keychains/SystemRootCertificates.keychain",  # (Apple's list)
             "/Library/Keychains/System.keychain"]  # (ones added for the whole Mac, by you or your organisation)
_context = None
_lock = threading.Lock()


def macos_certificates() -> list[str]:
    """The certificates in macOS's system keychains, each as PEM text ([] off macOS)."""
    found = []
    for keychain in KEYCHAINS:
        if not os.path.exists(keychain):
            continue
        try:
            done = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p", keychain],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        marker = "-----END CERTIFICATE-----"
        found += [block.strip() + "\n" + marker + "\n" for block in done.stdout.split(marker) if "BEGIN" in block]
    return found


def context() -> ssl.SSLContext:
    """A context that checks certificates against Python's own list, if it has one, and macOS's."""
    global _context
    with _lock:
        if _context is None:
            made = ssl.create_default_context()
            for pem in macos_certificates():
                try:
                    made.load_verify_locations(cadata=pem)
                except (ssl.SSLError, ValueError):
                    pass  # (one odd certificate shouldn't cost the rest)
            _context = made
        return _context


def describe(error) -> str:
    """A failed connection in a few plain words (not OpenSSL's)."""
    reason = getattr(error, "reason", error)
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE" in str(reason).upper():
        return "the secure connection couldn't be checked"
    if isinstance(reason, TimeoutError) or "timed out" in str(reason):
        return "it took too long to answer"
    if isinstance(reason, OSError) and getattr(reason, "errno", None) in (8, 51, 61, 65):
        return "you seem to be offline"
    return str(getattr(error, "reason", "") or error)[:120]


def urlopen(request, timeout=30):
    import urllib.request  # (here, not at launch: it brings in the whole web stack)
    return urllib.request.urlopen(request, timeout=timeout, context=context())
