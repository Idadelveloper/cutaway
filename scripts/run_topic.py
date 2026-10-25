"""Run the Cutaway pipeline for a topic (under the ADK workflow in cutaway/agents/root.py).

  python scripts/run_topic.py anc --until spec|build|render|review|loop|publish [--fresh-research] [--run-id ID]

Research is cached per topic in cache/<topic>/research/ and reused unless --fresh-research.
With --run-id of an existing run, finished stages are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cutaway import config, scoring, stats  # noqa: E402
from cutaway.agents.pipeline import TARGETS, stages_for  # noqa: E402
from cutaway.agents.root import run_pipeline  # noqa: E402
from cutaway.schemas import RunState  # noqa: E402


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


def load_or_new(run_id: str, topic: str) -> RunState:
    p = config.RUNS_DIR / run_id / "state.json"
    if p.exists():
        st = RunState.model_validate_json(p.read_text())
        print(f"resuming {run_id}: spec={'yes' if st.spec else 'no'} version=v{st.version}")
        return st
    return RunState(run_id=run_id, topic=config.load_topic(topic))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--until", choices=TARGETS, default="publish")
    ap.add_argument("--run-id")
    ap.add_argument("--fresh-research", action="store_true", help="ignore cache/<topic>/research and redo it")
    a = ap.parse_args()

    if not config.FAKE and not config.api_key():
        sys.exit("GEMINI_API_KEY missing: add it to .env (or run with CUTAWAY_FAKE=1)")
    run_id = a.run_id or time.strftime(f"%Y%m%d-%H%M%S-{a.topic}")
    state = load_or_new(run_id, a.topic)
    stages = stages_for(state, a.until, a.fresh_research)
    print(f"run {run_id} (fake={config.FAKE}): {' -> '.join(s.name for s in stages)}")
    t0 = time.time()
    state = await run_pipeline(state, stages)
    run = config.RUNS_DIR / run_id
    print(f"\ndone in {time.time() - t0:.1f}s -> {run}")
    if state.spec:
        print_stats(stats.research_stats(state))
        print("\nFigures:\n" + "\n".join(f"  {f.id}: {f.section}" for f in state.spec.figures))
    if state.version:
        rows = scoring.version_table(run)
        if rows:
            print("\nFindings per version\n" + scoring.format_table(rows))
        best = state.best_version or state.version
        print(f"\nexplainer: http://localhost:8000/runs/{run_id}/v{best}/  (make serve)")


if __name__ == "__main__":
    asyncio.run(main())
