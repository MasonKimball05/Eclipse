# Secrets only Eclipse can read: keys kept in the macOS Keychain through Apple's Security framework.
#
# The difference from keychain.py (which uses the `security` command) is who the Keychain lets read them.
# An item made by `security` trusts `security`, so any program on the Mac can run it and read the item
# without a word. An item made here trusts the app that made it: Eclipse reads it silently, and anything else
# asking for it gets macOS's "wants to use your confidential information" prompt, which you can refuse. That's
# what keeps a password stealer from simply copying the keys that unlock your passwords and passkeys (the
# trouble Chrome had: its key was there for any program to take).
#
# (While Eclipse runs from source, "Eclipse" is the Python that runs it; a rebuilt app is a new app to the
# Keychain, so the first read after a rebuild asks once. A signed release is recognised across updates.)

import ctypes
import ctypes.util
import sys

from . import brand

SERVICE = f"{brand.NAME} Vault"
OLD_SERVICE = f"{brand.OLD_INTERNAL_NAME} Vault"  # (before the rename: an item found here is moved over when read)
NOT_FOUND, DUPLICATE = -25300, -25299

_sec = _cf = None
if sys.platform == "darwin":
    _sec = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Security.framework/Security")
    _cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    for name, res, args in (
            ("CFDictionaryCreate", ctypes.c_void_p, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                                     ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p,
                                                     ctypes.c_void_p]),
            ("CFArrayCreate", ctypes.c_void_p, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                                                ctypes.c_void_p]),
            ("CFDataCreate", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long]),
            ("CFDataGetLength", ctypes.c_long, [ctypes.c_void_p]),
            ("CFDataGetBytePtr", ctypes.c_void_p, [ctypes.c_void_p]),
            ("CFStringCreateWithCString", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]),
            ("CFRelease", None, [ctypes.c_void_p])):
        getattr(_cf, name).restype, getattr(_cf, name).argtypes = res, args
    for name, args in (("SecItemAdd", [ctypes.c_void_p, ctypes.c_void_p]),
                       ("SecItemUpdate", [ctypes.c_void_p, ctypes.c_void_p]),
                       ("SecItemCopyMatching", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]),
                       ("SecItemDelete", [ctypes.c_void_p]),
                       ("SecKeychainOpen", [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)])):
        getattr(_sec, name).restype, getattr(_sec, name).argtypes = ctypes.c_int32, args


def available() -> bool:
    return _sec is not None


def _const(lib, name):
    return ctypes.c_void_p.in_dll(lib, name).value


class _Refs:
    """CoreFoundation objects made for one call, all released at the end of it."""

    def __init__(self):
        self.owned = []

    def own(self, ref):
        self.owned.append(ref)
        return ref

    def string(self, text: str):
        return self.own(_cf.CFStringCreateWithCString(None, text.encode(), 0x08000100))  # (UTF-8)

    def data(self, value: bytes):
        return self.own(_cf.CFDataCreate(None, value, len(value)))

    def dictionary(self, pairs):
        keys = (ctypes.c_void_p * len(pairs))(*[_const(_sec, k) if isinstance(k, str) else k for k, _ in pairs])
        values = (ctypes.c_void_p * len(pairs))(*[v for _, v in pairs])
        return self.own(_cf.CFDictionaryCreate(None, keys, values, len(pairs),
                                               _const(_cf, "kCFTypeDictionaryKeyCallBacks"),
                                               _const(_cf, "kCFTypeDictionaryValueCallBacks")))

    def item(self, account, keychain=None, searching=True, service=None, **extra):
        """The pairs that pick out this item (in a keychain file instead of the login one, for tests)."""
        pairs = [("kSecClass", _const(_sec, "kSecClassGenericPassword")),
                 ("kSecAttrService", self.string(service or SERVICE)), ("kSecAttrAccount", self.string(account))]
        if keychain is not None:
            if searching:
                listed = (ctypes.c_void_p * 1)(keychain)
                pairs.append(("kSecMatchSearchList", self.own(_cf.CFArrayCreate(
                    None, listed, 1, _const(_cf, "kCFTypeArrayCallBacks")))))
            else:
                pairs.append(("kSecUseKeychain", keychain))
        return self.dictionary(pairs + list(extra.items()))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for ref in reversed(self.owned):
            if ref:
                _cf.CFRelease(ref)


def open_keychain(path: str):
    """A keychain file (made with `security create-keychain`), for tests: never your login keychain."""
    ref = ctypes.c_void_p()
    return ref.value if _sec.SecKeychainOpen(path.encode(), ctypes.byref(ref)) == 0 else None


def store(account: str, secret: bytes, keychain=None) -> bool:
    if not available() or not secret:
        return False
    with _Refs() as refs:
        status = _sec.SecItemUpdate(refs.item(account, keychain),
                                    refs.dictionary([("kSecValueData", refs.data(secret))]))
        if status == 0:
            return True
        if status != NOT_FOUND:
            return False
        return _sec.SecItemAdd(refs.item(account, keychain, searching=False, kSecValueData=refs.data(secret),
                                         kSecAttrLabel=refs.string(f"{brand.NAME}: {account}")), None) == 0


def load(account: str, keychain=None) -> bytes | None:
    if not available():
        return None
    found = _read(account, keychain, SERVICE)
    if found is None:
        found = _read(account, keychain, OLD_SERVICE)
        if found is not None and store(account, found, keychain):
            _delete(account, keychain, OLD_SERVICE)  # (moved: one item, under the new name)
    return found


def _read(account, keychain, service) -> bytes | None:
    with _Refs() as refs:
        result = ctypes.c_void_p()
        query = refs.item(account, keychain, service=service, kSecReturnData=_const(_cf, "kCFBooleanTrue"),
                          kSecMatchLimit=_const(_sec, "kSecMatchLimitOne"))
        if _sec.SecItemCopyMatching(query, ctypes.byref(result)) != 0 or not result.value:
            return None
        refs.own(result.value)
        return ctypes.string_at(_cf.CFDataGetBytePtr(result.value), _cf.CFDataGetLength(result.value))


def delete(account: str, keychain=None) -> bool:
    return _delete(account, keychain, SERVICE)


def _delete(account, keychain, service) -> bool:
    if not available():
        return False
    with _Refs() as refs:
        return _sec.SecItemDelete(refs.item(account, keychain, service=service)) in (0, NOT_FOUND)
