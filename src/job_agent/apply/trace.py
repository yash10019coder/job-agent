"""One JSONL line per step of an application run: what we saw, what we decided, what we did. Text only; secrets are dropped."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_SECRET_KEYS = ("password", "passwd", "secret", "token", "otp", "code", "credential")


_ECHO_KEYS = ("state", "action", "gate", "result", "final", "status", "note", "detail")


class Trace:
    """`echo=True` also prints one short line per event to stderr, so a run is not silent while it works."""

    def __init__(self, path: Path | None, echo: bool = False, label: str = "") -> None:
        self.path, self.echo, self.label = path, echo, label
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **fields) -> None:
        if self.echo:
            import sys
            bits = [f"{k}={str(fields[k])[:90]}" for k in _ECHO_KEYS if fields.get(k) not in (None, "")]
            print(f"[{self.label or 'run'}] step {fields.get('step', '?')}: " + "  ".join(bits), file=sys.stderr, flush=True)
        if not self.path:
            return
        clean = {k: ("<hidden>" if any(s in k.lower() for s in _SECRET_KEYS) else v) for k, v in fields.items()}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **clean}, default=str) + "\n")
