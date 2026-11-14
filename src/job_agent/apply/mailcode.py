"""Read emailed verification codes/links from Gmail (read-only), for signup and login gates.

Only mail that arrived after `since` and that mentions one of the site's hint words (company/vendor name) is considered, and only
the code or link is extracted: message content is never stored or logged. Uses Gmail's REST API over httpx with the
gmail.readonly scope; `gmail_authorize` runs the one-time consent (loopback OAuth with PKCE).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html as htmllib
import json
import os
import re
import secrets
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

CONFIG_DIR = Path.home() / ".config" / "job-agent"
TOKEN_PATH = CONFIG_DIR / "gmail_token.json"
CLIENT_PATH = CONFIG_DIR / "gmail_client_secret.json"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

_CODE_PATTERNS = [
    re.compile(r"(?:verification|security|confirmation|one[- ]time|login|sign[- ]in|access|authentication)\s*(?:code|pin|passcode)\D{0,40}?(\d{4,8})\b", re.I),
    re.compile(r"\b(\d{4,8})\b\s*(?:is|as)\s*your\s*(?:\w+\s*){0,3}code", re.I),
    re.compile(r"\b(?:code|otp|pin)\s*(?:is|:)?\s*(\d{4,8})\b", re.I),
    re.compile(r"\b(\d{6})\b"),  # last resort: a lone 6-digit number
]
_LINK = re.compile(r"""https?://[^\s"'<>)]+""", re.I)
_LINK_HINT = re.compile(r"verify|confirm|activate|validate|magic|token|email-?verification", re.I)
_LINK_BAD = re.compile(r"unsubscribe|privacy|terms|preferences|\.png|\.jpg|\.gif|pixel|track(ing)?\.", re.I)


@dataclass
class MailCode:
    code: str | None = None
    link: str | None = None


def extract_code(text: str) -> str | None:
    text = re.sub(r"\s+", " ", text)
    for pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def extract_link(text: str) -> str | None:
    for url in _LINK.findall(text):
        url = htmllib.unescape(url).rstrip(".,;")
        if _LINK_HINT.search(url) and not _LINK_BAD.search(url):
            return url
    return None


def site_hints(url: str) -> list[str]:
    """Words that identify the sender: company and vendor names taken from the site's host."""
    host = urlparse(url).netloc.lower()
    skip = {"www", "com", "net", "org", "io", "co", "careers", "jobs", "apply", "career", "recruiting", "wd1", "wd3", "wd5", "wd103", "global", "en"}
    words = [w for w in re.split(r"[.\-]", host) if len(w) >= 3 and w not in skip and not w.isdigit()]
    vendor = {"myworkdayjobs": ["workday", "myworkday"], "taleo": ["taleo", "oracle"], "icims": ["icims"], "successfactors": ["successfactors", "sap"],
              "smartrecruiters": ["smartrecruiters"], "greenhouse": ["greenhouse"], "lever": ["lever"], "ashbyhq": ["ashby"], "oraclecloud": ["oracle"]}
    extra = [v for key, vs in vendor.items() if key in host for v in vs]
    return list(dict.fromkeys(words + extra))


class CodeProvider(Protocol):
    async def fetch(self, hints: list[str], since: datetime, timeout_s: float = 90.0) -> MailCode | None: ...


class StaticCodeProvider:
    """Returns queued codes (tests)."""

    def __init__(self, *codes: MailCode) -> None:
        self.queue, self.calls = list(codes), []

    async def fetch(self, hints, since, timeout_s=90.0):
        self.calls.append((list(hints), since))
        return self.queue.pop(0) if self.queue else None


def _body_text(payload: dict) -> str:
    parts, out = [payload], []
    while parts:
        part = parts.pop()
        parts += part.get("parts", []) or []
        data = (part.get("body") or {}).get("data")
        if data and part.get("mimeType", "").startswith("text/"):
            raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            out.append(re.sub(r"<[^>]+>", " ", raw) if "html" in part.get("mimeType", "") else raw)
    return " ".join(out)


class GmailProvider:
    def __init__(self, token_path: Path = TOKEN_PATH, client: httpx.AsyncClient | None = None) -> None:
        self.token_path, self._client = token_path, client

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=20)
        return self._client

    async def _access_token(self) -> str:
        data = json.loads(self.token_path.read_text(encoding="utf-8"))
        if data.get("expires_at", 0) > time.time() + 60:
            return data["access_token"]
        client = await self._http()
        r = await client.post(_TOKEN_URL, data={"client_id": data["client_id"], "client_secret": data["client_secret"],
                                                "refresh_token": data["refresh_token"], "grant_type": "refresh_token"})
        r.raise_for_status()
        fresh = r.json()
        data.update(access_token=fresh["access_token"], expires_at=time.time() + int(fresh.get("expires_in", 3600)))
        _write_private(self.token_path, json.dumps(data))
        return data["access_token"]

    async def fetch(self, hints: list[str], since: datetime, timeout_s: float = 90.0) -> MailCode | None:
        client, deadline = await self._http(), time.monotonic() + timeout_s
        while True:
            headers = {"Authorization": f"Bearer {await self._access_token()}"}
            q = f"after:{int(since.timestamp())} (verification OR verify OR code OR confirm OR OTP OR \"one-time\" OR \"sign in\")"
            listing = (await client.get(f"{_API}/messages", params={"q": q, "maxResults": 10}, headers=headers)).json()
            for ref in listing.get("messages", []):
                msg = (await client.get(f"{_API}/messages/{ref['id']}", params={"format": "full"}, headers=headers)).json()
                if int(msg.get("internalDate", 0)) / 1000 < since.timestamp():
                    continue
                head = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
                text = _body_text(msg.get("payload", {}))
                haystack = f"{head.get('from', '')} {head.get('subject', '')} {text}".lower()
                if hints and not any(h.lower() in haystack for h in hints):
                    continue
                found = MailCode(extract_code(f"{head.get('subject', '')} {text}"), extract_link(text))
                if found.code or found.link:
                    return found
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(5)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def default_code_provider() -> CodeProvider | None:
    return GmailProvider() if TOKEN_PATH.exists() else None


def gmail_authorize(client_secret_path: Path = CLIENT_PATH, port: int = 0) -> Path:
    """One-time consent (read-only): opens the browser, receives the redirect on localhost, stores the refresh token (0600)."""
    info = json.loads(Path(client_secret_path).read_text(encoding="utf-8"))
    cfg = info.get("installed") or info.get("web")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            received.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Gmail read-only access granted. You can close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = cfg["auth_uri"] + "?" + urlencode({"client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
                                             "access_type": "offline", "prompt": "consent", "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Open this URL if the browser does not open:\n" + url)
    webbrowser.open(url)
    server.handle_request()
    if "code" not in received:
        raise RuntimeError(f"no authorization code received: {received}")
    r = httpx.post(cfg["token_uri"], data={"code": received["code"], "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                            "redirect_uri": redirect, "grant_type": "authorization_code", "code_verifier": verifier})
    r.raise_for_status()
    tok = r.json()
    _write_private(TOKEN_PATH, json.dumps({"access_token": tok["access_token"], "refresh_token": tok["refresh_token"], "client_id": cfg["client_id"],
                                           "client_secret": cfg["client_secret"], "expires_at": time.time() + int(tok.get("expires_in", 3600))}))
    return TOKEN_PATH
