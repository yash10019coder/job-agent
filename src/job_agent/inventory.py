from __future__ import annotations

import json
from pathlib import Path

from job_agent.models import ResumeInventory


def data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def load_resume_inventory(path: Path | None = None) -> ResumeInventory:
    inventory_path = path or data_dir() / "resume_inventory.json"
    with inventory_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return ResumeInventory.model_validate(payload)
