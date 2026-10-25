"""Run the Cutaway pipeline for a topic.

  python scripts/run_topic.py anc --until spec [--fresh-research] [--run-id ID]

Research is cached per topic in cache/<topic>/research/ and reused unless --fresh-research.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cutaway import config, stats  # noqa: E402
from cutaway.agents.base import save_state  # noqa: E402
from cutaway.agents.intake import Intake  # noqa: E402
from cutaway.agents.research import Research, load_cache, save_cache  # noqa: E402
from cutaway.agents.spec_writer import SpecWriter  # noqa: E402
from cutaway.schemas import RunState  # noqa: E402

UNTIL = ["research", "spec"]


def print_stats(st: dict) -> None:
    rows = [
        ("sources (with text)", st["sources_total"]),
        ("  by type", ", ".join(f"{k} {v}" for k, v in sorted(st["sources_by_type"].items(), key=lambda x: -x[1]))),
        ("  found by", ", ".join(f"{k} {v}" for k, v in st["found_by"].items())),
        ("sources skipped", st["sources_skipped"]),
        ("tokens read", f"{st['tokens_read']:,} (research {st['tokens_read_research']:,} + spec {st['tokens_read_spec']:,})"),
        ("video watched", f"{st['video_hours']} h over {st['videos_watched']} videos"),
        ("conflicts resolved", st["conflicts_resolved"]),
        ("citations verified", f"{st['citations_verified']}/{st['citations_total']}"
                               f" ({st['citations_verified_loose']} punctuation-insensitive)"),
        ("research cached", st["research_cached"]),
    ]
    w = max(len(r[0]) for r in rows)
    print("\nResearch stats")
    for k, v in rows:
        print(f"  {k:<{w}}  {v}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--until", choices=UNTIL, default="spec")
    ap.add_argument("--run-id")
    ap.add_argument("--fresh-research", action="store_true", help="ignore cache/<topic>/research and redo it")
    a = ap.parse_args()

    if not config.FAKE and not config.api_key():
        sys.exit("GEMINI_API_KEY missing: add it to .env (or run with CUTAWAY_FAKE=1)")
    run_id = a.run_id or time.strftime(f"%Y%m%d-%H%M%S-{a.topic}")
    state = RunState(run_id=run_id, topic=config.load_topic(a.topic))
    print(f"run {run_id} (fake={config.FAKE}) -> {config.RUNS_DIR / run_id}")
    t0 = time.time()

    async def step(name, coro):
        ts = time.time()
        out = await coro
        save_state(out)
        print(f"  {name} done in {time.time() - ts:.1f}s")
        return out

    if a.fresh_research or not load_cache(state):
        state = await step("intake", Intake().run(state))
        state = await step("research", Research().run(state))
        save_cache(state)
    if a.until == "spec":
        state = await step("spec", SpecWriter().run(state))
    print(f"done in {time.time() - t0:.1f}s -> {config.RUNS_DIR / run_id}")
    if state.spec:
        print_stats(stats.research_stats(state))
        print("\nFigures:\n" + "\n".join(f"  {f.id}: {f.section}" for f in state.spec.figures))
    else:
        print(json.dumps([{k: s.get(k) for k in ("id", "type", "status", "title")} for s in state.sources], indent=1))


if __name__ == "__main__":
    asyncio.run(main())
