"""Replay of recorded real applications: every control the person used must be one the engine's reader lists.

Fixtures come from `tools/record_apply.py promote` (sanitised page copies + what the person interacted with). A control the reader missed when
it was recorded is marked a known gap (xfail, not strict): fixing the reader turns it into XPASS, which is the signal to drop the marker.
"""
import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from job_agent.apply.perceive import perceive

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests/fixtures/recorded").glob("*/*.json"))
BRAVE = shutil.which("brave-browser") and "/opt/brave.com/brave/brave"


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def seen(model, exp: dict) -> bool:
    """Did the reader list a field of a fitting kind (or an action) named after the control the person used?"""
    wanted = {w for w in (_norm(exp["label"]), _norm(exp.get("group", ""))) if len(w) >= 2}
    match = lambda label: any(w in _norm(label) or (_norm(label) and _norm(label) in w) for w in wanted)
    if exp["as"] == "field":
        return any(f.kind in exp["kinds"] and match(f.label) for f in model.fields)
    return any(match(a.label) for a in model.actions)


async def read_fixture(html: str):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=BRAVE, headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.set_content(html)
        model = (await perceive(page)).model
        await browser.close()
        return model


CASES = []
for meta_path in FIXTURES:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for exp in meta["interactions"]:
        marks = [] if exp["seen_when_recorded"] else [pytest.mark.xfail(reason="the reader missed this control when it was recorded", strict=False)]
        CASES.append(pytest.param(meta_path, exp, id=f"{meta['site']}-{meta['state']}-step{exp['step']}-{exp['label'][:25]}", marks=marks))


@pytest.mark.skipif(not BRAVE or not Path(BRAVE).exists() or not CASES, reason="Brave not installed or no recordings promoted yet")
@pytest.mark.parametrize("meta_path,exp", CASES)
def test_the_reader_lists_the_control_the_person_used(meta_path, exp):
    model = asyncio.run(read_fixture(meta_path.with_suffix(".html").read_text(encoding="utf-8")))
    assert seen(model, exp), f"the reader lists no {exp['as']} for '{exp['label']}' ({exp['role']}); it found fields {[f.label for f in model.fields][:12]}"
