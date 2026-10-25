"""Run the Cutaway pipeline for a topic.

  python scripts/run_topic.py anc --until spec [--with-deep-research] [--with-scout] [--run-id ID]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cutaway import config  # noqa: E402
from cutaway.agents.base import save_state  # noqa: E402
from cutaway.agents.intake import Intake  # noqa: E402
from cutaway.agents.research import Research  # noqa: E402
from cutaway.agents.spec_writer import SpecWriter  # noqa: E402
from cutaway.schemas import RunState  # noqa: E402

STAGES = [("intake", Intake), ("research", Research), ("spec", SpecWriter)]
UNTIL = ["research", "spec"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--until", choices=UNTIL, default="spec")
    ap.add_argument("--run-id")
    ap.add_argument("--reuse-research", action="store_true",
                    help="with --run-id: skip intake/research and reuse runs/<id>/state.json")
    ap.add_argument("--with-deep-research", action="store_true")
    ap.add_argument("--with-scout", action="store_true")
    a = ap.parse_args()

    if not config.FAKE and not config.api_key():
        sys.exit("GEMINI_API_KEY missing: add it to .env (or run with CUTAWAY_FAKE=1)")
    run_id = a.run_id or time.strftime(f"%Y%m%d-%H%M%S-{a.topic}")
    state_path = config.RUNS_DIR / run_id / "state.json"
    if a.reuse_research and state_path.exists():
        state = RunState.model_validate_json(state_path.read_text())
    else:
        state = RunState(run_id=run_id, topic=config.load_topic(a.topic),
                         flags={"with_deep_research": a.with_deep_research, "with_scout": a.with_scout})
    print(f"run {run_id} (fake={config.FAKE}) -> {config.RUNS_DIR / run_id}")

    t0 = time.time()
    stop = STAGES.index(next(s for s in STAGES if s[0] == a.until)) + 1
    for name, cls in STAGES[:stop]:
        if a.reuse_research and name in ("intake", "research") and state.research:
            continue
        ts = time.time()
        state = await cls().run(state)
        save_state(state)
        print(f"  {name} done in {time.time() - ts:.1f}s")
    print(f"done in {time.time() - t0:.1f}s")
    if state.spec:
        print(json.dumps({"spec": str(config.RUNS_DIR / run_id / "spec.json"),
                          "figures": [f"{f.id}: {f.section}" for f in state.spec.figures]}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
