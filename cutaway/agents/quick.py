"""Quick change: a change request verified in about 2-3 minutes by scoping everything to the affected figures.

  scope (Flash) -> Fixer (Argon medium, only the scoped files) -> render only those figures ->
  code checks + fidelity + watched review of those figures (Argon medium), in parallel ->
  verdict. A passing version is marked "ready" and waits for a human to approve it;
  nothing is published automatically.

Findings for the new version = the base version's findings for untouched figures + fresh findings
for the touched ones, so the per-version table stays comparable. Clips/traces of untouched figures
are copied from the base version.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from pydantic import BaseModel

from .. import config, events, llm, scoring
from ..checks.runner import run_checks
from ..harness.render import render_version
from ..schemas import BuildOutput, Finding, RunState
from .base import run_dir, save_json, save_state
from .builder import write_files
from .fixer import read_files, version_findings
from .review import fidelity_checker, visual_reviewer

MAX_FIGURES = 4


class _Scope(BaseModel):
    figures: list[str]
    files: list[str]
    why: str = ""


def _copy_untouched(d: Path, base: int, new: int, touched: set[str]) -> None:
    for kind in ("clips", "traces", "keyframes"):
        src, dst = d / kind / f"v{base}", d / kind / f"v{new}"
        dst.mkdir(parents=True, exist_ok=True)
        for p in src.glob("*") if src.exists() else []:
            if p.name.split("_")[0] not in touched and not (dst / p.name).exists():
                shutil.copy(p, dst / p.name)


async def scope_change(state: RunState, text: str, files: list[str]) -> _Scope:
    figs = [{"id": f.id, "section": f.section, "controls": [c["name"] for c in f.controls], "entities": f.entities}
            for f in state.spec.figures]
    sc, _ = await llm.call_model(_Scope, stage="scope", model=config.FLASH, thinking="low", run_id=state.run_id,
                                 input=config.load_prompt("scope", request=text, figures=json.dumps(figs, indent=1),
                                                          files=", ".join(files)))
    known = {f.id for f in state.spec.figures}
    sc.figures = [f for f in sc.figures if f in known][:MAX_FIGURES] or [state.spec.figures[0].id]
    sc.files = [f for f in sc.files if f in files] or [f"figures/{sc.figures[0]}.js"]
    return sc


async def quick_change(state: RunState, text: str) -> dict:
    t0 = time.time()
    rid = state.run_id
    d = run_dir(state)
    base = state.best_version or state.version
    new = max(state.versions) + 1
    files = read_files(Path(state.versions[base]["dir"]))

    def step(msg: str) -> None:
        events.emit(rid, "stage", stage="change", msg=f"[{time.time() - t0:5.1f}s] {msg}")

    step(f"change request on v{base} -> v{new}: {text}")
    sc = await scope_change(state, text, list(files))
    touched = set(sc.figures)
    step(f"scope: figures {', '.join(sc.figures)}; files {', '.join(sc.files)} ({sc.why})")

    figs = [f for f in state.spec.figures if f.id in touched]
    ent_ids = {e for f in figs for e in f.entities}
    law_ids = {l for f in figs for l in f.laws}
    prompt = config.load_prompt(
        "fix_quick", request=text, figures=json.dumps([f.model_dump() for f in figs], indent=1),
        entities=json.dumps([e.model_dump() for e in state.spec.entities if e.id in ent_ids], indent=1),
        laws=json.dumps([l.model_dump() for l in state.spec.laws if l.id in law_ids], indent=1),
        model_notes=state.topic.get("model_notes", ""), contract=config.CONTRACT_PATH.read_text(),
        palette=config.palette_table(state.topic),
        files="\n\n".join(f"=== {n} ===\n{files[n]}" for n in sc.files))
    out, _ = await llm.call_model(BuildOutput, stage=f"quickfix_v{new}", model=config.ARGON, thinking="medium",
                                  background=True, max_output_tokens=config.LONG_OUTPUT_TOKENS,
                                  input=prompt, run_id=rid)
    changed = {k: v for k, v in out.files.items() if k in sc.files}
    step(f"fixer changed {', '.join(changed) or 'nothing'}")

    vdir = d / f"v{new}"
    if vdir.exists():
        shutil.rmtree(vdir)
    shutil.copytree(Path(state.versions[base]["dir"]), vdir)
    write_files(vdir, changed)
    (d / "fixes").mkdir(exist_ok=True)
    (d / "fixes" / f"v{new}.md").write_text(f"# v{base} -> v{new} (change request)\n\n{text}\n\n"
                                            f"Scope: {sc.figures} / {sc.files}\n\n{out.plan}\n")
    state.versions[new] = {"dir": str(vdir), "from": base, "changed": sorted(changed), "change_request": text,
                           "figures": sc.figures, "status": "checking"}
    save_state(state)

    timing = await render_version(vdir, d, new, state.spec, run_id=rid, figures=sc.figures, scroll_video=False)
    step(f"rendered {timing['sweeps']} clips of {', '.join(sc.figures)} in {timing['total_s']}s")
    _copy_untouched(d, base, new, touched)

    sub = state.spec.model_copy(update={"figures": figs})
    jobs = [asyncio.to_thread(run_checks, sub, d, new, config.palette(state.topic)), fidelity_checker(state, new)] + \
           [visual_reviewer(state, f, new, thinking="medium") for f in figs]
    results = await asyncio.gather(*jobs, return_exceptions=True)
    fresh: list[Finding] = []
    for r in results:
        if isinstance(r, Exception):
            events.emit(rid, "error", stage="change", msg=f"review job failed: {r!r}"[:300])
        else:
            fresh += r
    before = version_findings(state, base)
    kept = [f for f in before if f.figure and f.figure not in touched and f.layer not in ("fidelity", "citation")]
    findings = kept + [f for f in fresh if f.figure is None or f.figure in touched or f.layer == "fidelity"]
    save_json(d / "findings" / f"v{new}.json", findings)
    step("checked: code checks + fidelity + watched review of " + ", ".join(sc.figures))

    hb, ha = scoring.high_count(before), scoring.high_count(findings)
    blocking = [f for f in findings if f.severity == "high" and (f.figure in touched or f.figure is None)]
    passed = ha <= hb and not blocking
    state.versions[new].update({
        "status": "ready" if passed else "rejected", "weighted": scoring.weighted(findings), "high": ha,
        "seconds": round(time.time() - t0, 1),
        "new_findings": [f.model_dump() for f in findings if f.figure in touched or f.figure is None]})
    state.version = base   # the published/best version doesn't change until a human approves
    save_state(state)
    verdict = (f"v{new} passed the checks ({ha} high, was {hb}); waiting for approval" if passed else
               f"v{new} rejected: {len(blocking)} high findings in {', '.join(sc.figures)} "
               f"(e.g. {blocking[0].what[:120] if blocking else ''}); v{base} stays published")
    step(verdict)
    events.emit(rid, "change", msg=verdict, version=new, base=base, status=state.versions[new]["status"],
                seconds=state.versions[new]["seconds"], figures=sc.figures)
    return {"version": new, "base": base, **state.versions[new]}
