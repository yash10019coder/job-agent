"""Launch Brave with remote debugging and attach Playwright over CDP.

Brave runs as its own detached process with a dedicated, FRESH profile (default name "job-agent")
that is never derived from any of your real Brave profiles: nothing is copied from them, so cookies,
sync state and logins of your normal browser are never read or duplicated. Log in once inside this
profile and it persists between runs; closing the script never kills the browser.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

_BRAVE_CANDIDATES = (
    "brave-browser",
    "brave-browser-stable",
    "brave",
    "/opt/brave.com/brave/brave",
    "/usr/bin/brave-browser",
)


@dataclass
class BraveConfig:
    port: int = 9222
    profile_name: str = "job-agent"  # separate from every real Brave profile
    executable: str | None = None
    startup_timeout: float = 20.0

    @property
    def profile_dir(self) -> Path:
        return Path.home() / ".config" / "job-agent" / "browser" / self.profile_name

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def find_brave(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for candidate in _BRAVE_CANDIDATES:
        path = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if path:
            return path
    raise RuntimeError("Brave not found. Pass --brave-path or install brave-browser.")


async def _cdp_ready(config: BraveConfig) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            return (await client.get(f"{config.cdp_url}/json/version")).status_code == 200
    except httpx.HTTPError:
        return False


async def ensure_brave_running(config: BraveConfig) -> bool:
    """Start Brave with a CDP port if none is listening. Returns True if newly launched."""
    if await _cdp_ready(config):
        return False
    config.profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            find_brave(config.executable),
            f"--remote-debugging-port={config.port}",
            f"--user-data-dir={config.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # survive this process exiting
    )
    deadline = asyncio.get_running_loop().time() + config.startup_timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _cdp_ready(config):
            return True
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Brave did not expose CDP on {config.cdp_url} within {config.startup_timeout}s")


class BraveSession:
    """Async context manager: launches/attaches Brave and exposes new tabs."""

    def __init__(self, config: BraveConfig | None = None) -> None:
        self.config = config or BraveConfig()
        self.launched = False
        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def __aenter__(self) -> "BraveSession":
        self.launched = await ensure_brave_running(self.config)
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.connect_over_cdp(self.config.cdp_url)
        self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
        return self

    async def __aexit__(self, *exc) -> None:
        # Stop the Playwright driver only; Brave itself keeps running.
        if self._pw:
            await self._pw.stop()

    async def new_tab(self, url: str | None = None) -> Page:
        assert self.context is not None, "use inside 'async with'"
        page = await self.context.new_page()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        await page.bring_to_front()
        return page
