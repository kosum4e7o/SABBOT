"""Config + token storage.

* stream_activity_bot_config.json  - channels, accounts (id/name only), settings. No secrets.
* stream_activity_bot_tokens.json  - OAuth tokens and the Kick client secret. On Windows the
  payload is encrypted with DPAPI (bound to the current Windows user). On other systems
  (development only) it falls back to a plain file with restricted permissions.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from models import Account, Channel, Settings

CONFIG_NAME = "stream_activity_bot_config.json"
TOKENS_NAME = "stream_activity_bot_tokens.json"


def app_dir() -> Path:
    env = os.environ.get("STREAM_BOT_HOME")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or str(Path.home())
        p = Path(base) / "Stream Activity Bot"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent


def _atomic_write(path: Path, text: str, private: bool = False) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if private and sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ----------------------------------------------------------------- DPAPI ----
def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_call(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if encrypt:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "StreamActivityBot", None,
                                      None, None, 0, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None,
                                        None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


# ----------------------------------------------------------- TokenStore ----
class TokenStore:
    """Thread-safe token/secret storage. Never logs values."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (app_dir() / TOKENS_NAME)
        self._lock = threading.RLock()
        self._data = {"accounts": {}, "secrets": {}}
        self.load_warning = ""
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            raw = base64.b64decode(doc["data"])
            if doc.get("protection") == "dpapi":
                if not _dpapi_available():
                    raise OSError("Token file is DPAPI-protected but DPAPI is unavailable")
                raw = _dpapi_call(raw, encrypt=False)
            data = json.loads(raw.decode("utf-8"))
            self._data = {"accounts": data.get("accounts", {}), "secrets": data.get("secrets", {})}
        except Exception:
            self.load_warning = ("Token storage could not be read (different Windows user or "
                                 "corrupt file). Accounts must be reconnected.")
            self._data = {"accounts": {}, "secrets": {}}

    def _save(self) -> None:
        raw = json.dumps(self._data).encode("utf-8")
        if _dpapi_available():
            protection, blob = "dpapi", _dpapi_call(raw, encrypt=True)
        else:
            protection, blob = "plain", raw
        doc = {"version": 1, "protection": protection,
               "data": base64.b64encode(blob).decode("ascii")}
        _atomic_write(self.path, json.dumps(doc, indent=2), private=True)

    def get_tokens(self, account_id: str) -> Optional[dict]:
        with self._lock:
            t = self._data["accounts"].get(account_id)
            return dict(t) if t else None

    def set_tokens(self, account_id: str, tokens: dict) -> None:
        with self._lock:
            self._data["accounts"][account_id] = dict(tokens)
            self._save()

    def delete_tokens(self, account_id: str) -> None:
        with self._lock:
            if self._data["accounts"].pop(account_id, None) is not None:
                self._save()

    def get_secret(self, name: str) -> str:
        with self._lock:
            return self._data["secrets"].get(name, "")

    def set_secret(self, name: str, value: str) -> None:
        with self._lock:
            if value:
                self._data["secrets"][name] = value
            else:
                self._data["secrets"].pop(name, None)
            self._save()


# --------------------------------------------------------------- Config ----
class Config:
    """Persistent channels/accounts/settings. All access is lock-protected."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or (app_dir() / CONFIG_NAME)
        self.lock = threading.RLock()
        self.settings = Settings()
        self.accounts: list[Account] = []
        self.channels: list[Channel] = []
        self.load()

    # -- persistence
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            backup = self.path.with_suffix(".corrupt.json")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return
        with self.lock:
            s = doc.get("settings", {})
            self.settings = Settings(**{k: v for k, v in s.items() if k in Settings.__dataclass_fields__})
            self.accounts = [Account(**{k: v for k, v in a.items() if k in Account.__dataclass_fields__})
                             for a in doc.get("accounts", [])]
            self.channels = [Channel(**{k: v for k, v in c.items() if k in Channel.__dataclass_fields__})
                             for c in doc.get("channels", [])]
            # Backward compatibility: old channels only had account_id.
            for ch in self.channels:
                if not ch.account_ids and ch.account_id:
                    ch.account_ids = [ch.account_id]
                elif ch.account_ids and not ch.account_id:
                    ch.account_id = ch.account_ids[0]

    def save(self) -> None:
        with self.lock:
            doc = {"version": 4, "settings": asdict(self.settings),
                   "accounts": [asdict(a) for a in self.accounts],
                   "channels": [asdict(c) for c in self.channels]}
            _atomic_write(self.path, json.dumps(doc, indent=2, ensure_ascii=False))

    # -- accounts
    def get_account(self, account_id: str) -> Optional[Account]:
        with self.lock:
            return next((a for a in self.accounts if a.id == account_id), None)

    def accounts_for(self, platform: str) -> list[Account]:
        with self.lock:
            return [a for a in self.accounts if a.platform == platform]

    def find_account(self, platform: str, external_id: str) -> Optional[Account]:
        with self.lock:
            return next((a for a in self.accounts
                         if a.platform == platform and external_id and a.external_id == external_id), None)

    def add_account(self, acct: Account) -> None:
        with self.lock:
            self.accounts.append(acct)
            self.save()

    def remove_account(self, account_id: str) -> int:
        """Remove account; channels keep dangling references for compatibility."""
        with self.lock:
            self.accounts = [a for a in self.accounts if a.id != account_id]
            n = sum(1 for c in self.channels if account_id in c.connected_account_ids())
            self.save()
            return n

    # -- channels
    def get_channel(self, channel_id: str) -> Optional[Channel]:
        with self.lock:
            return next((c for c in self.channels if c.id == channel_id), None)

    def add_channel(self, ch: Channel) -> None:
        with self.lock:
            self.channels.append(ch)
            self.save()

    def replace_channel(self, ch: Channel) -> None:
        with self.lock:
            for i, c in enumerate(self.channels):
                if c.id == ch.id:
                    self.channels[i] = ch
                    break
            else:
                self.channels.append(ch)
            self.save()

    def remove_channel(self, channel_id: str) -> None:
        with self.lock:
            self.channels = [c for c in self.channels if c.id != channel_id]
            self.save()
