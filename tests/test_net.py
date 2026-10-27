"""HTTPS from Eclipse's own Python trusts what macOS trusts (no connection made here)."""

import ssl
import sys
import urllib.error

import pytest

from browser import net


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's keychains")
def test_macos_trusted_certificates_are_loaded(monkeypatch):
    monkeypatch.setattr(net, "_context", None)
    pems = net.macos_certificates()
    assert len(pems) > 50 and all(p.startswith("-----BEGIN CERTIFICATE-----") for p in pems)
    assert net.context().cert_store_stats()["x509_ca"] > 50
    assert net.context() is net.context()  # (once)


def test_failures_are_said_plainly():
    bad = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] nope"))
    assert net.describe(bad) == "the secure connection couldn't be checked"
    assert net.describe(urllib.error.URLError(TimeoutError("timed out"))) == "it took too long to answer"
    assert net.describe(urllib.error.URLError(OSError(8, "nodename nor servname provided"))) == "you seem to be offline"
