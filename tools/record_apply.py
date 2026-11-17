"""Full-capture recorder for a manual job application, and what to do with the recording.

  record_apply.py record [--name N] [--seconds S] [--no-screenshots]   attach to the agent browser and record until Ctrl+C
  record_apply.py study DIR                                              gap report: which controls the engine's reader missed, answer differences, overlays
  record_apply.py promote DIR --site NAME [--only-gaps]                  write sanitised fixtures for tests/test_apply_recorded.py

What is saved, and the privacy limits, are described in tools/recorder_capture.py. Recordings stay in data/recordings/ (git-ignored).
"""
import argparse, asyncio, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _facts() -> dict | None:
    from job_agent.apply.prefill import build_facts
    profile, inventory = ROOT / "data" / "applicant_profile.json", ROOT / "data" / "resume_inventory.json"
    return build_facts(profile, inventory) if profile.is_file() and inventory.is_file() else None


async def record(name: str, seconds: float, screenshots: bool) -> None:
    from job_agent.apply.browser import BraveSession
    from recorder_capture import Recorder
    out = ROOT / "data" / "recordings" / f"{datetime.now():%Y%m%d-%H%M%S}-{name}"
    async with BraveSession() as s:
        rec = Recorder(s.context, out, facts=_facts(), screenshots=screenshots)
        await rec.start()
        print(f"recording to {out}: do the application in the agent browser; Ctrl+C to stop", flush=True)
        try:
            await asyncio.sleep(seconds if seconds else 86400)
        except asyncio.CancelledError:
            pass
        finally:
            await rec.stop()
            print(f"saved {rec.step} actions and {rec.nstate} page states to {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record"); r.add_argument("--name", default="apply"); r.add_argument("--seconds", type=float, default=0); r.add_argument("--no-screenshots", action="store_true")
    st = sub.add_parser("study"); st.add_argument("folder")
    pr = sub.add_parser("promote"); pr.add_argument("folder"); pr.add_argument("--site", required=True); pr.add_argument("--only-gaps", action="store_true")
    a = ap.parse_args()
    if a.cmd == "record":
        try:
            asyncio.run(record(a.name, a.seconds, not a.no_screenshots))
        except KeyboardInterrupt:
            pass
    elif a.cmd == "study":
        from recorder_study import study
        print(study(a.folder))
    else:
        from recorder_study import promote
        for p in promote(a.folder, a.site, only_gaps=a.only_gaps, facts=_facts()):
            print("wrote", p.relative_to(ROOT))
