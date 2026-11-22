"""Turn the user's hand labels (data/verification_sheet.xlsx) + saved destination pages into classifier fixtures.

Each labeled row becomes tests/fixtures/states/labeled_<idx>_<vendor>.json with the expected PageState of the page that
was saved after the Apply click. 'Extra checks: email code' is NOT a property of the saved page (it is a later step),
so the expected state comes from Gate + Form-fields-visible + Entry type only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from job_agent.apply.states import PageState

_GATE_STATE = {
    "login": PageState.LOGIN, "signup": PageState.SIGNUP, "sso": PageState.SSO_ONLY,
    "login+signup": PageState.ACCOUNT_CHOICE, "login+sso": PageState.ACCOUNT_CHOICE, "signup+sso": PageState.ACCOUNT_CHOICE,
    "login+signup+sso (all)": PageState.ACCOUNT_CHOICE, "guest option available": PageState.ACCOUNT_CHOICE,
}


def expected_state(entry: str | None, gate: str | None, extra: str | None, form_visible: str | None) -> PageState | None:
    entry, gate, extra, form_visible = (x or "" for x in (entry, gate, extra, form_visible))
    if entry.startswith("Dead") or form_visible.startswith("closed"):
        return PageState.CLOSED
    if extra.startswith("bot block"):
        return PageState.BOT_BLOCK
    if gate in _GATE_STATE:
        return _GATE_STATE[gate]
    if form_visible.startswith("yes"):
        return PageState.FORM_STEP
    if form_visible.startswith(("no", "decoy")):
        return PageState.JOB_PAGE
    return None


def apply_page_labels(out_dir: Path) -> int:
    """Set expect_state on every fixture from page_labels.json (labels read off each saved page's own text)."""
    labels_path = out_dir / "page_labels.json"
    labels = {k: v for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    n = 0
    for name, label in labels.items():
        path = out_dir / f"{name}.json"
        if not path.exists():
            continue
        fixture = json.loads(path.read_text(encoding="utf-8"))
        fixture["expect_state"] = label["state"]
        fixture["acceptable"] = label.get("acceptable", [label["state"]])
        fixture["expect_application_form"] = label["state"] == "form_step"
        fixture["expect_listing"] = label["state"] == "listing"
        fixture["label_source"] = "page_text_reviewed"
        path.write_text(json.dumps(fixture, indent=1, default=str), encoding="utf-8")
        n += 1
    return n


def build(sheet_path: Path, out_dir: Path, tab: str = "1 Reveal + gates (60)") -> list[dict]:
    from openpyxl import load_workbook

    ws = load_workbook(sheet_path, data_only=True)[tab]
    col = {c.value: c.column for c in ws[1]}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for r in range(2, ws.max_row + 1):
        v = lambda name: ws.cell(r, col[name]).value  # noqa: E731
        labels = {n: v(n) for n in ("1 Entry type", "2 Gate (account)", "3 Extra checks", "4 Form fields visible", "5 Steps", "6 Vendor")}
        state = expected_state(labels["1 Entry type"], labels["2 Gate (account)"], labels["3 Extra checks"], labels["4 Form fields visible"])
        page_path = v("Saved page model (json)")
        if not any(labels.values()) or state is None or not page_path or not Path(page_path).exists():
            continue
        model = json.loads(Path(page_path).read_text(encoding="utf-8"))
        model["text_digest"] = model.get("text_digest", "")[:900]
        model["actions"] = model.get("actions", [])[:70]
        idx, vendor = v("#"), re.sub(r"[^a-z0-9]+", "-", str(labels["6 Vendor"] or "unknown").lower()).strip("-")
        fixture = {
            "name": f"labeled_{idx:02d}_{vendor}", "url": v("Destination URL (run 2)") or model.get("url", ""), "status": 200,
            "frame_hosts": [], "from_url": v("Landing URL"), "expect_state": state.value, "labels": labels,
            "expect_application_form": state == PageState.FORM_STEP, "expect_listing": False, "model": model,
        }
        (out_dir / f"{fixture['name']}.json").write_text(json.dumps(fixture, indent=1, default=str), encoding="utf-8")
        written.append(fixture)
    if (out_dir / "page_labels.json").exists():
        apply_page_labels(out_dir)
    return written
