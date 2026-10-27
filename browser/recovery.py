# The recovery PIN: what brings your passwords and passkeys back on another Mac, or after reinstalling.
#
# Your passwords and passkeys are locked with keys kept in the Keychain (secure_store.py), which a backup
# can't carry. With a recovery PIN, each also keeps a copy of what it needs, sealed with a key made from the
# PIN (scrypt, then AES-256-GCM), and those copies go into your backups. On a new Mac, the PIN opens them.
#
# The key made from the PIN is kept in the Keychain too (only Eclipse can read it), so something new joins
# its copy without asking for the PIN each time. With no server limiting guesses, the PIN is at least 8
# characters, and each guess costs about half a second and 128 MB.

import hashlib
import os


from . import secure_store, storage

MIN_PIN = 8
SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}
KEY_ACCOUNT = "recovery-key"
CHECK = b"eclipse-recovery"  # (sealed with the key, so a wrong PIN is told apart from a damaged file)
_stores = []  # the stores with a copy: passwords, passkeys


def register(reseal, restore):
    """A store with a copy. reseal(new, old) makes its copy again with a new key (old: the previous key, if
    any, to keep what's in the copy but not on this Mac). restore(key) brings back what this Mac is missing
    from the copy, and says how many."""
    if (reseal, restore) not in _stores:
        _stores.append((reseal, restore))


def _path():
    return storage.data_dir() / "recovery.json"


def is_set() -> bool:
    return _path().exists()


def pin_problem(pin: str) -> str:
    if len(pin) < MIN_PIN:
        return f"Use at least {MIN_PIN} characters: letters, numbers or both."
    return ""


def _derive(pin: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=n, r=r, p=p, maxmem=256 * 1024 * 1024, dklen=32)


def seal(key: bytes, data: bytes, label: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # (here: it takes a moment to load)
    nonce = os.urandom(12)
    return {"nonce": nonce.hex(), "sealed": AESGCM(key).encrypt(nonce, data, label).hex()}


def unseal(key: bytes, sealed: dict, label: bytes) -> bytes:
    """What was sealed (cryptography's InvalidTag if it's the wrong key or the file was changed)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(bytes.fromhex(sealed["nonce"]), bytes.fromhex(sealed["sealed"]), label)


def key() -> bytes | None:
    """The recovery key, if a PIN is set and this Mac has the key (None: no PIN, or it needs entering here)."""
    return secure_store.load(KEY_ACCOUNT) if is_set() else None


def set_pin(pin: str) -> bool:
    """Set (or change) the PIN: every store seals its copy again with the new key."""
    if pin_problem(pin):
        return False
    old = key()
    salt = os.urandom(16)
    new = _derive(pin, salt, **SCRYPT)
    if not secure_store.store(KEY_ACCOUNT, new):
        return False
    storage.write_json(_path(), {"version": 1, "kdf": "scrypt", **SCRYPT, "salt": salt.hex(),
                                 "check": seal(new, CHECK, b"check")}, private=True)
    for reseal, _restore in _stores:
        reseal(new, old)
    return True


def unlock(pin: str) -> int | None:
    """Bring back what this Mac is missing, with the PIN: how many things came back, or None if it isn't the
    right PIN. (The key is kept on this Mac from then on.)"""
    saved = storage.read_json(_path())
    if not saved:
        return None
    found = _derive(pin, bytes.fromhex(saved["salt"]), saved["n"], saved["r"], saved["p"])
    try:
        if unseal(found, saved["check"], b"check") != CHECK:
            return None
    except Exception:  # (InvalidTag: the wrong PIN)
        return None
    secure_store.store(KEY_ACCOUNT, found)
    return sum(restore(found) for _reseal, restore in _stores)
