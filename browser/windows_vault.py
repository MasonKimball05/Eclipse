# secure_store.py's Windows side: the keys that lock your passwords and passkeys, kept in Windows Credential
# Manager (Control Panel → Credential Manager → Windows Credentials, as "Eclipse Vault/…").
#
# Windows encrypts them with your Windows sign-in (DPAPI), so they're no use copied to another account or PC.
# Unlike macOS's Keychain, though, Windows doesn't ask before another program you run reads them: anything
# running as you could. (Chrome's own key on Windows is kept the same way.)

import ctypes
import sys

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2  # (this PC, for this Windows account)
ERROR_NOT_FOUND = 1168
MAX_BLOB = 5 * 512  # (Credential Manager's limit per item: the keys kept here are far smaller)

_api = None
if sys.platform == "win32":
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", _FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    _api = ctypes.WinDLL("advapi32", use_last_error=True)
    _api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    _api.CredWriteW.restype = wintypes.BOOL
    _api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    _api.CredReadW.restype = wintypes.BOOL
    _api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _api.CredDeleteW.restype = wintypes.BOOL
    _api.CredFree.argtypes = [ctypes.c_void_p]
    _api.CredFree.restype = None


def available() -> bool:
    return _api is not None


def store(target: str, secret: bytes, label: str = "") -> bool:
    if _api is None or not secret or len(secret) > MAX_BLOB:
        return False
    blob = (ctypes.c_ubyte * len(secret)).from_buffer_copy(secret)
    credential = _CREDENTIAL()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = label or None
    credential.CredentialBlobSize = len(secret)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = target
    return bool(_api.CredWriteW(ctypes.byref(credential), 0))


def load(target: str) -> bytes | None:
    if _api is None:
        return None
    found = ctypes.POINTER(_CREDENTIAL)()
    if not _api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        return None
    try:
        item = found.contents
        return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize) if item.CredentialBlobSize else None
    finally:
        _api.CredFree(found)


def delete(target: str) -> bool:
    if _api is None:
        return False
    return bool(_api.CredDeleteW(target, CRED_TYPE_GENERIC, 0)) or ctypes.get_last_error() == ERROR_NOT_FOUND
