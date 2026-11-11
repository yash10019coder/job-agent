"""Perception: turn a live page into a compact semantic PageModel (no selectors, no pixels).

The JS side (perceive.js) reads roles/labels/states and stamps `data-agent-id` on elements; the
model only ever refers to those ids. This module runs it in every frame, merges the results, and
provides diffing and a settle detector so the agent reacts to fields that appear or change.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from playwright.async_api import Frame, Page
from pydantic import BaseModel, Field

_PERCEIVE_JS = (Path(__file__).with_name("perceive.js")).read_text(encoding="utf-8")

_MUTATION_JS = """() => {
  if (window.__agentMut) return;
  window.__agentMut = { last: Date.now() };
  new MutationObserver(() => { window.__agentMut.last = Date.now(); })
    .observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
}"""


class Option(BaseModel):
    id: str = ""
    label: str
    value: str | None = None


class FieldModel(BaseModel):
    id: str
    kind: str  # text|textarea|email|tel|number|date|url|password|select|combobox|radio_group|checkbox|checkbox_group|file
    label: str = ""
    help: str = ""
    required: bool = False
    value: str = ""
    options: list[Option] | None = None
    async_options: bool = False
    placeholder: str = ""
    maxlength: int | None = None
    pattern: str | None = None
    accept: str | None = None
    invalid: bool = False
    error: str = ""
    section: str = ""
    frame: int = 0

    @property
    def key(self) -> str:
        """Stable identity across re-perceives (ids are re-stamped each pass)."""
        return f"{self.kind}|{re.sub(r'[^a-z0-9]+', ' ', self.label.lower()).strip()}"

    @property
    def is_trigger(self) -> bool:
        """Answering these can reveal/remove other fields, so they are applied one at a time."""
        return self.kind in {"radio_group", "checkbox", "checkbox_group", "select", "combobox"}

    @property
    def empty(self) -> bool:
        return not self.value.strip()


class ActionModel(BaseModel):
    id: str
    kind: str
    label: str
    primary: bool = False
    disabled: bool = False
    frame: int = 0


class PageModel(BaseModel):
    url: str
    title: str = ""
    headings: list[str] = Field(default_factory=list)
    alerts: list[str] = Field(default_factory=list)
    fields: list[FieldModel] = Field(default_factory=list)
    actions: list[ActionModel] = Field(default_factory=list)
    text_digest: str = ""

    def field_by_key(self, key: str) -> FieldModel | None:
        return next((f for f in self.fields if f.key == key), None)


class PageDiff(BaseModel):
    added: list[str] = Field(default_factory=list)  # field labels that appeared
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)  # new errors / options / values
    url_changed: bool = False

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.url_changed)

    def __str__(self) -> str:
        parts = []
        if self.url_changed:
            parts.append("url changed")
        for name, items in (("added", self.added), ("removed", self.removed), ("changed", self.changed)):
            if items:
                parts.append(f"{name}: {', '.join(items)}")
        return "; ".join(parts) or "no change"


def diff(old: PageModel, new: PageModel) -> PageDiff:
    old_by, new_by = {f.key: f for f in old.fields}, {f.key: f for f in new.fields}
    changed = []
    for key, f in new_by.items():
        prev = old_by.get(key)
        if prev and ((f.error and f.error != prev.error) or (f.options or []) != (prev.options or []) and f.options):
            changed.append(f.label or key)
    return PageDiff(
        added=[f.label or k for k, f in new_by.items() if k not in old_by],
        removed=[f.label or k for k, f in old_by.items() if k not in new_by],
        changed=changed,
        url_changed=old.url != new.url,
    )


class Perception:
    """A page model plus the frames its element ids belong to (needed to resolve ids)."""

    def __init__(self, model: PageModel, frames: list[Frame]) -> None:
        self.model, self.frames = model, frames

    def locator(self, element_id: str, frame: int = 0):
        return self.frames[frame].locator(f'[data-agent-id="{element_id}"]').first


async def perceive(page: Page) -> Perception:
    merged = PageModel(url=page.url, title=await page.title())
    frames: list[Frame] = []
    for frame in page.frames:
        if frame.url.startswith(("about:", "chrome-extension:")) and frame != page.main_frame:
            continue
        index = len(frames)
        try:
            raw = await asyncio.wait_for(frame.evaluate(_PERCEIVE_JS, {"prefix": f"f{index}-"}), 5.0)
        except Exception:
            continue  # detached/cross-origin-blocked frame
        frames.append(frame)
        if index == 0:
            merged = PageModel(**{**raw, "fields": [], "actions": []})
        for f in raw["fields"]:
            merged.fields.append(FieldModel(**{**f, "frame": index}))
        for a in raw["actions"]:
            merged.actions.append(ActionModel(**{**a, "frame": index}))
    return Perception(merged, frames or [page.main_frame])


class Settler:
    """Waits until the page stops reacting: DOM quiet + no XHR/fetch in flight. No fixed sleeps."""

    def __init__(self, page: Page, quiet_ms: int = 300, timeout_s: float = 5.0) -> None:
        self.page, self.quiet_ms, self.timeout_s = page, quiet_ms, timeout_s
        self.inflight = 0
        page.on("request", self._start)
        page.on("requestfinished", self._end)
        page.on("requestfailed", self._end)

    @staticmethod
    def _tracked(request) -> bool:
        return request.resource_type in {"xhr", "fetch"}

    def _start(self, request) -> None:
        if self._tracked(request):
            self.inflight += 1

    def _end(self, request) -> None:
        if self._tracked(request):
            self.inflight = max(0, self.inflight - 1)

    async def settle(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            quiet = True
            for frame in self.page.frames:
                try:  # a frame that never gets an execution context (e.g. a captcha iframe) must not stall the page
                    await asyncio.wait_for(frame.evaluate(_MUTATION_JS), 1.0)
                    idle = await asyncio.wait_for(frame.evaluate("() => Date.now() - window.__agentMut.last"), 1.0)
                    quiet = quiet and idle >= self.quiet_ms
                except Exception:
                    continue
            if quiet and self.inflight == 0:
                return
            await asyncio.sleep(0.05)
