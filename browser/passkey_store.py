# Passkeys Eclipse keeps itself: a WebAuthn authenticator in the browser, like Chrome's passkeys in Google
# Password Manager.
#
# Each passkey is a P-256 key pair made for one site (its "relying party") and one account. The private key
# lives in the Keychain, where only Eclipse can read it (secure_store.py); what it's for (site, account name,
# when it was made) in passkeys/passkeys.json in the data folder. Signing happens here, in the browser; a page
# only ever gets the signature, and only for its own site, which is the address Qt says the page is at, never
# what the page says.
#
# With a recovery PIN (recovery.py), passkeys/vault.json is a copy of every private key, sealed with the key
# made from the PIN, and it goes into backups: the PIN brings your passkeys back on another Mac.

import base64
import hashlib
import json
import os
import struct
import time


from . import recovery, secure_store, storage

ES256 = -7  # (COSE's number for ECDSA P-256 with SHA-256: the one algorithm Eclipse's passkeys use)
AAGUID = bytes(16)  # (no attestation: a site can't tell which authenticator made it, as with most passkeys)
KEY_ACCOUNT = "passkey:{}"  # the Keychain entry for a passkey's private key
SEAL = b"eclipse-passkeys-v2"

UP, UV, BE, BS, AT = 0x01, 0x04, 0x08, 0x10, 0x40  # authenticator data flags: present, verified, backup
                                                    # eligible, backed up, credential included

# Domains under which anyone can register a name (so a site can't claim one as its own): the common two-part
# ones. (The full Public Suffix List isn't here; a site claiming "co.uk" is refused, one claiming the rare
# suffix missing from this list would still need you to approve the passkey on its own page.)
PUBLIC_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "com.au", "net.au", "org.au",
    "edu.au", "gov.au", "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "ac.jp", "co.kr", "or.kr", "com.br",
    "net.br", "org.br", "com.cn", "net.cn", "org.cn", "com.mx", "com.ar", "com.tr", "co.in", "net.in", "org.in",
    "co.za", "com.sg", "com.hk", "com.tw", "co.il", "github.io", "gitlab.io", "pages.dev", "netlify.app",
    "vercel.app", "herokuapp.com", "appspot.com", "web.app", "firebaseapp.com", "blogspot.com", "azurewebsites.net",
    "cloudfront.net", "workers.dev", "fly.dev", "onrender.com", "glitch.me", "repl.co",
}


# --- Encoding ------------------------------------------------------------------------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cbor(value) -> bytes:
    """CBOR, for the few kinds WebAuthn uses (canonical: maps keep the order given, which callers make so)."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for size, code in ((1, 24), (2, 25), (4, 26), (8, 27)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | code]) + n.to_bytes(size, "big")
        raise ValueError("too big")
    if isinstance(value, bool):
        return b"\xf5" if value else b"\xf4"
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        data = value.encode()
        return head(3, len(data)) + data
    if isinstance(value, (list, tuple)):
        return head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value))


def cose_key(public: "ec.EllipticCurvePublicKey") -> bytes:
    numbers = public.public_numbers()
    return cbor({1: 2, 3: ES256, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})


# --- Sites ---------------------------------------------------------------------------------------------

def secure(origin_url) -> bool:
    """Whether a page may use passkeys at all: https, or http on this Mac (as browsers allow)."""
    scheme, host = origin_url.scheme(), origin_url.host().lower()
    return scheme == "https" or (scheme == "http" and (host == "localhost" or host.endswith(".localhost")))


def rp_id_allowed(rp_id: str, host: str) -> bool:
    """Whether a page at `host` may make or use passkeys for `rp_id`: its own name, or a domain it's part of
    (accounts.example.com may use example.com), but never a suffix anyone can register under."""
    rp_id, host = (rp_id or "").lower().strip("."), host.lower().strip(".")
    if not rp_id or not host:
        return False
    if rp_id == host:
        return True
    return host.endswith("." + rp_id) and "." in rp_id and rp_id not in PUBLIC_SUFFIXES


def origin_of(url) -> str:
    port = url.port()
    default = {"https": 443, "http": 80}.get(url.scheme())
    return f"{url.scheme()}://{url.host()}" + (f":{port}" if port not in (-1, default) else "")


def client_data(kind: str, challenge: str, origin: str) -> bytes:
    """clientDataJSON, as browsers write it (that order, no spaces): made here, with the real origin."""
    return json.dumps({"type": f"webauthn.{kind}", "challenge": challenge, "origin": origin, "crossOrigin": False},
                      separators=(",", ":")).encode()


# --- The passkeys --------------------------------------------------------------------------------------

def _folder():
    return storage.private_folder(storage.data_dir() / "passkeys")


def all_passkeys() -> list[dict]:
    return (storage.read_json(storage.data_dir() / "passkeys" / "passkeys.json") or {}).get("passkeys", [])


def _save_list(passkeys):
    storage.write_json(_folder() / "passkeys.json", {"version": 1, "passkeys": passkeys}, private=True)


def for_site(rp_id: str, allowed: list[str] | None = None) -> list[dict]:
    """This site's passkeys (only the ones it listed, if it listed some), most recently used first."""
    found = [p for p in all_passkeys() if p["rp_id"] == rp_id and (not allowed or p["id"] in allowed)]
    return sorted(found, key=lambda p: p.get("last_used") or p.get("created", 0), reverse=True)


def _private_key(passkey) -> "ec.EllipticCurvePrivateKey | None":
    from cryptography.hazmat.primitives import serialization
    stored = secure_store.load(KEY_ACCOUNT.format(passkey["id"]))
    if not stored:
        return None
    try:
        return serialization.load_der_private_key(stored, None)
    except ValueError:
        return None


def has_key(passkey) -> bool:
    return secure_store.load(KEY_ACCOUNT.format(passkey["id"])) is not None


def create(rp_id: str, rp_name: str, user_id: str, user_name: str, display_name: str, challenge: str, origin: str,
           verified: bool) -> dict:
    """A new passkey for the site, saved; and what the site gets back (all base64url):
    {id, clientDataJSON, attestationObject, authenticatorData, publicKey}."""
    from cryptography.hazmat.primitives import serialization  # (here, not at launch: it takes a moment to load)
    from cryptography.hazmat.primitives.asymmetric import ec
    private = ec.generate_private_key(ec.SECP256R1())
    credential = os.urandom(16)
    passkey = {"id": b64url(credential), "rp_id": rp_id, "rp_name": rp_name[:100], "user_id": user_id,
               "user_name": user_name[:200], "display_name": display_name[:200], "created": time.time(),
               "last_used": None}
    der = private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    if not secure_store.store(KEY_ACCOUNT.format(passkey["id"]), der):
        raise OSError("Couldn't save the passkey in the Keychain")
    # A site saving a second passkey for the same account replaces the first (as Chrome and Safari do)
    kept = [p for p in all_passkeys() if not (p["rp_id"] == rp_id and p["user_id"] == user_id)]
    for old in all_passkeys():
        if old not in kept:
            secure_store.delete(KEY_ACCOUNT.format(old["id"]))
    _save_list(kept + [passkey])
    backed_up = add_to_vault(passkey["id"], b64url(der))
    flags = UP | (UV if verified else 0) | BE | (BS if backed_up else 0) | AT
    auth = (hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + AAGUID +
            struct.pack(">H", len(credential)) + credential + cose_key(private.public_key()))
    spki = private.public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"id": passkey["id"], "clientDataJSON": b64url(client_data("create", challenge, origin)),
            "attestationObject": b64url(cbor({"fmt": "none", "attStmt": {}, "authData": auth})),
            "authenticatorData": b64url(auth), "publicKey": b64url(spki)}


def sign_in(passkey_id: str, challenge: str, origin: str, verified: bool) -> dict:
    """A signature from one of the site's passkeys, and what the site gets back (all base64url):
    {id, clientDataJSON, authenticatorData, signature, userHandle}."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    passkeys = all_passkeys()
    passkey = next(p for p in passkeys if p["id"] == passkey_id)
    private = _private_key(passkey)
    if private is None:
        raise OSError("This passkey's key isn't on this Mac: recover your passkeys with your PIN")
    data = client_data("get", challenge, origin)
    flags = UP | (UV if verified else 0) | BE | (BS if recovery.is_set() else 0)
    # (the counter stays 0: a passkey that's backed up can't promise to count, and sites expect that)
    auth = hashlib.sha256(passkey["rp_id"].encode()).digest() + bytes([flags]) + struct.pack(">I", 0)
    signature = private.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
    passkey["last_used"] = time.time()
    _save_list(passkeys)
    return {"id": passkey_id, "clientDataJSON": b64url(data), "authenticatorData": b64url(auth),
            "signature": b64url(signature), "userHandle": passkey["user_id"]}


def delete(passkey_id: str):
    secure_store.delete(KEY_ACCOUNT.format(passkey_id))
    _save_list([p for p in all_passkeys() if p["id"] != passkey_id])
    remove_from_vault(passkey_id)


# --- The recovery copy -----------------------------------------------------------------------------------

def _vault_path():
    return storage.data_dir() / "passkeys" / "vault.json"


def _open_vault(key: bytes | None) -> dict:
    saved = storage.read_json(_vault_path())
    if not saved or key is None:
        return {}
    try:
        return json.loads(recovery.unseal(key, saved, SEAL))
    except Exception:  # (sealed with another key: nothing to keep from it)
        return {}


def _write_vault(key: bytes, keys: dict):
    storage.write_json(_folder() / "vault.json", {"version": 2, **recovery.seal(key, json.dumps(keys).encode(), SEAL)},
                       private=True)


def _reseal(new: bytes, old: bytes | None):
    """The copy, sealed with a new key: every passkey on this Mac, and any in the old copy that aren't here."""
    keys = _open_vault(old)
    for passkey in all_passkeys():
        if (der := secure_store.load(KEY_ACCOUNT.format(passkey["id"]))) is not None:
            keys[passkey["id"]] = b64url(der)
    _write_vault(new, {k: v for k, v in keys.items() if any(p["id"] == k for p in all_passkeys())})


def add_to_vault(passkey_id: str, der_b64: str) -> bool:
    key = recovery.key()
    if key is None:
        return False
    keys = _open_vault(key)
    keys[passkey_id] = der_b64
    _write_vault(key, keys)
    return True


def remove_from_vault(passkey_id: str):
    if (key := recovery.key()) is not None:
        keys = _open_vault(key)
        keys.pop(passkey_id, None)
        _write_vault(key, keys)


def missing() -> list[dict]:
    """Passkeys listed here whose key isn't on this Mac (restored from a backup): the PIN brings them back."""
    return [p for p in all_passkeys() if not has_key(p)]


def _restore(key: bytes) -> int:
    keys = _open_vault(key)
    brought = 0
    for passkey in missing():
        if keys.get(passkey["id"]):
            brought += bool(secure_store.store(KEY_ACCOUNT.format(passkey["id"]), unb64url(keys[passkey["id"]])))
    return brought


recovery.register(_reseal, _restore)
