"""Site credentials for accounts the agent creates or reuses (login/signup gates).

Passwords live ONLY in the OS keyring (never in files, traces, logs or the run folders). A signup saves the new credential
BEFORE the account is created, so an account can never exist without its password being stored. If no usable keyring exists,
`default_store()` returns None and the controller will not create accounts at all.
"""
from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

SERVICE = "job-agent"
_INDEX = "job-agent-index"
_SYMBOLS = "!@#$%^&*-_+="


@dataclass(frozen=True)
class Credential:
    username: str
    password: str

    def __repr__(self) -> str:  # never let a password reach a log line or a traceback
        return f"Credential(username={self.username!r}, password='***')"


def site_key(url: str) -> str:
    """The account scope: the host (each Workday/iCIMS tenant is its own account)."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host or "local"


def generate_password(length: int = 18) -> str:
    """A strong random password that satisfies the usual rules (lower, upper, digit, symbol)."""
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits, _SYMBOLS]
    chars = [secrets.choice(p) for p in pools] + [secrets.choice("".join(pools)) for _ in range(max(length, 12) - 4)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


class CredentialStore(Protocol):
    def get(self, site: str) -> Credential | None: ...
    def save(self, site: str, credential: Credential) -> None: ...
    def forget(self, site: str) -> None: ...
    def sites(self) -> list[str]: ...


class MemoryStore:
    """In-process store for tests."""

    def __init__(self) -> None:
        self._data: dict[str, Credential] = {}

    def get(self, site: str) -> Credential | None:
        return self._data.get(site)

    def save(self, site: str, credential: Credential) -> None:
        self._data[site] = credential

    def forget(self, site: str) -> None:
        self._data.pop(site, None)

    def sites(self) -> list[str]:
        return sorted(self._data)


class KeyringStore:
    def __init__(self) -> None:
        import keyring

        self._kr = keyring

    def get(self, site: str) -> Credential | None:
        raw = self._kr.get_password(SERVICE, site)
        if not raw:
            return None
        data = json.loads(raw)
        return Credential(data["u"], data["p"])

    def _index(self) -> list[str]:
        raw = self._kr.get_password(_INDEX, "sites")
        return json.loads(raw) if raw else []

    def save(self, site: str, credential: Credential) -> None:
        self._kr.set_password(SERVICE, site, json.dumps({"u": credential.username, "p": credential.password}))
        sites = self._index()
        if site not in sites:
            self._kr.set_password(_INDEX, "sites", json.dumps(sorted([*sites, site])))

    def forget(self, site: str) -> None:
        try:
            self._kr.delete_password(SERVICE, site)
        except Exception:
            pass
        self._kr.set_password(_INDEX, "sites", json.dumps([s for s in self._index() if s != site]))

    def sites(self) -> list[str]:
        return self._index()


def default_store() -> CredentialStore | None:
    """The OS keyring if it works here; None otherwise (then no accounts are created: a password we cannot keep is a lockout)."""
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        keyring.get_password(SERVICE, "__probe__")
        return KeyringStore()
    except Exception:
        return None
