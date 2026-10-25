"""Baselines, scored with the same checks as the full run.

  A: prompt only -> one-pass build (no research, no spec facts, no contract-fix rounds, no loop).
     Gets the figure/control/entity/probe ids from the full run's spec so the harness can drive the page.
  B: research + spec + one-pass build, no loop = the full run's v1 (reused, not rebuilt).
  Full: the full run's best version after the fix loop.

  python scripts/baselines.py anc --run <full run id> [--rebuild-a]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cutaway import config, events, scoring  # noqa: E402
from cutaway.agents.base import save_state  # noqa: E402
from cutaway.agents.builder import Builder  # noqa: E402
from cutaway.agents.render import Render  # noqa: E402
from cutaway.agents.review import Review  # noqa: E402
from cutaway.agents.root import run_pipeline  # noqa: E402
from cutaway.schemas import RunState  # noqa: E402


def skeleton(spec) -> str:
    return json.dumps({
        "figures": [{"id": f.id, "section": f.section, "controls": [{k: c.get(k) for k in ("name", "min", "max", "default", "unit")}
                                                                     for c in f.controls],
                     "entities": f.entities, "probes": f.probes} for f in spec.figures],
        "entity_colors": {e.id: e.color for e in spec.entities}}, indent=1)


def latest_full_run(topic: str) -> str | None:
    for d in sorted(config.RUNS_DIR.glob(f"*-{topic}"), reverse=True):
        st = d / "state.json"
        if st.exists() and json.loads(st.read_text()).get("best_version"):
            return d.name
    return None


async def baseline_a(full: RunState, rebuild: bool) -> Path:
    run_id = f"{full.run_id}-baselineA"
    d = config.RUNS_DIR / run_id
    if (d / "findings" / "v1.json").exists() and not rebuild:
        return d
    if d.exists():
        shutil.rmtree(d)
    # same spec for scoring (laws, probes, compare_with) and same sources for the watched review references
    state = RunState(run_id=run_id, topic=full.topic, spec=full.spec, sources=full.sources, research=full.research)
    save_state(state)
    t = full.topic
    prompt = config.load_prompt("build_baseline", title=t["title"], prompt=t["prompt"], audience=t["audience"],
                                skeleton=skeleton(full.spec), style=config.STYLE_PATH.read_text(),
                                palette=config.palette_table(t),
                                contract=config.CONTRACT_PATH.read_text())
    async with events.lifecycle(run_id, "baseline"):
        await run_pipeline(state, [Builder(max_contract_fixes=0, prompt=prompt), Render(), Review()])
    return d


def row(name: str, findings_path: Path) -> dict:
    fs = scoring.load_findings(findings_path)
    return {"name": name, "layers": scoring.by_layer(fs), "total": len(fs), "high": scoring.high_count(fs),
            "weighted": scoring.weighted(fs)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--run", help="full run id (default: latest finished run of the topic)")
    ap.add_argument("--rebuild-a", action="store_true")
    a = ap.parse_args()
    run_id = a.run or latest_full_run(a.topic)
    if not run_id:
        sys.exit(f"no finished full run for {a.topic}; run `make all` first")
    full = RunState.model_validate_json((config.RUNS_DIR / run_id / "state.json").read_text())
    best = full.best_version or full.version
    da = await baseline_a(full, a.rebuild_a)
    rows = [row("A prompt", da / "findings" / "v1.json"),
            row("B no loop", config.RUNS_DIR / run_id / "findings" / "v1.json"),
            row(f"Full v{best}", config.RUNS_DIR / run_id / "findings" / f"v{best}.json")]
    out = scoring.format_table(rows, label="baseline")
    (config.RUNS_DIR / run_id / "baselines.txt").write_text(out + "\n")
    (config.RUNS_DIR / run_id / "baselines.json").write_text(json.dumps(rows, indent=1))
    print(f"full run {run_id}\n")
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
