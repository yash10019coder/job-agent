"""One JSONL line per step of an application run: what we saw, what we decided, what we did. Text only; secrets are dropped."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_SECRET_KEYS = ("password", "passwd", "secret", "token", "otp", "code", "credential")


class Trace:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **fields) -> None:
        if not self.path:
            return
        clean = {k: ("<hidden>" if any(s in k.lower() for s in _SECRET_KEYS) else v) for k, v in fields.items()}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **clean}, default=str) + "\n")
