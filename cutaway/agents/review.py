"""Review (all in parallel): code checks + FidelityChecker (Flash) + VisualReviewer (Argon high, per figure).

Output: findings/v<N>.json (all layers merged).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from pydantic import BaseModel

from .. import config, events, llm
from ..checks.runner import load_traces, run_checks
from ..schemas import Finding, RunState
from .base import Stage, run_dir, save_json, stage_event

SEGMENT_BEFORE_S, SEGMENT_AFTER_S = 10, 20


class _Answer(BaseModel):
    q: int
    answer: str
    clip: str | None = None
    time: str | None = None
    evidence: str = ""
    severity: str = "medium"
    fix_hint: str = ""


class _Review(BaseModel):
    answers: list[_Answer]


class _FidFinding(BaseModel):
    figure: str | None = None
    what: str
    evidence: str = ""
    severity: str = "medium"
    fix_hint: str = ""


class _Fidelity(BaseModel):
    findings: list[_FidFinding]


RUBRIC = {1: "motion doesn't match the cited video facts", 2: "a discussed entity is hidden or not visible",
          3: "colors don't match the spec palette", 4: "step isn't understandable on its own",
          5: "introduces more or less than the outline says"}


def _seconds(ts: str) -> float | None:
    m = re.match(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$", ts.strip())
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + float(s)


def _sev(s: str) -> str:
    return s if s in ("high", "medium", "low") else "medium"


def reference_parts(state: RunState, fig) -> tuple[list[dict], str]:
    """Video segments (uri + offsets) and diagram images for a figure's compare_with refs."""
    spec = state.spec
    vfs = {v.id: v for v in spec.video_facts}
    srcs = {s["id"]: s for s in state.sources} | {s.get("id"): s for s in spec.sources}
    parts, lines = [], []
    for ref in fig.compare_with:
        vf = vfs.get(ref)
        if vf:
            src = srcs.get(vf.source, {})
            url, sec = src.get("url_or_file", ""), _seconds(vf.time)
            lines.append(f"- {vf.id} [{vf.source} {vf.time}] ({vf.kind}): {vf.text}")
            if url.startswith("http") and sec is not None:
                parts.append({"type": "video", "uri": url, "processing": {
                    "start_offset": f"{max(0, sec - SEGMENT_BEFORE_S):.0f}s", "end_offset": f"{sec + SEGMENT_AFTER_S:.0f}s"}})
            continue
        sid = ref.split()[0]
        src = srcs.get(sid)
        if src and src.get("type") == "diagram" and src.get("local") and Path(src["url_or_file"]).exists():
            parts.append(llm.file_part(src["url_or_file"]))
            lines.append(f"- {ref}: diagram {src.get('title')}")
    return parts, "\n".join(lines) or "(none: judge against the spec only)"


async def visual_reviewer(state: RunState, fig, version: int) -> list[Finding]:
    clips_dir = run_dir(state) / "clips" / f"v{version}"
    clips = sorted(clips_dir.glob(f"{fig.id}_*.mp4"))
    if not clips:
        return []
    ref_parts, vf_text = reference_parts(state, fig)
    ents = [e.model_dump() for e in state.spec.entities if e.id in fig.entities]
    prompt = config.load_prompt("review", figure_id=fig.id, figure=json.dumps(fig.model_dump(), indent=1),
                                entities=json.dumps(ents, indent=1), video_facts=vf_text,
                                clips="\n".join(f"- {c.stem}" for c in clips))
    parts = ref_parts + [p for c in clips for p in (llm.text_part(f"Clip {c.stem}:"), llm.file_part(c))]
    out, _ = await llm.call_model(_Review, stage=f"review_v{version}_{fig.id}", model=config.ARGON, thinking="high",
                                  input=parts + [llm.text_part(prompt)], run_id=state.run_id)
    fs = []
    for a in out.answers:
        if a.answer.strip().lower().startswith("no"):
            fs.append(Finding(id=f"v{version}-W{fig.id}-{a.q}", layer="watched", severity=_sev(a.severity),
                              figure=fig.id, time=" ".join(x for x in (a.clip, a.time) if x) or None,
                              what=f"Q{a.q}: {RUBRIC.get(a.q, '')}", evidence=a.evidence, fix_hint=a.fix_hint))
    return fs


async def fidelity_checker(state: RunState, version: int) -> list[Finding]:
    traces = load_traces(run_dir(state), version)
    built = {}
    for (fig, sweep), tr in traces.items():
        if sweep == "default" and tr.frames and tr.frames[0]:
            fr = tr.frames[0]
            built[fig] = {"controls": fr.get("controls"),
                          "entities": {k: {kk: vv for kk, vv in (v or {}).items() if kk not in ("visible", "position")}
                                       for k, v in (fr.get("entities") or {}).items()}}
    spec = state.spec
    prompt = config.load_prompt(
        "fidelity", spec_entities=json.dumps([e.model_dump() for e in spec.entities], indent=1),
        spec_controls=json.dumps({f.id: f.controls for f in spec.figures}, indent=1),
        built=json.dumps(built, indent=1))
    out, _ = await llm.call_model(_Fidelity, stage=f"fidelity_v{version}", model=config.FLASH, thinking="low",
                                  input=prompt, run_id=state.run_id)
    return [Finding(id=f"v{version}-F{i + 1}", layer="fidelity", severity=_sev(f.severity), figure=f.figure,
                    time=None, what=f.what, evidence=f.evidence, fix_hint=f.fix_hint)
            for i, f in enumerate(out.findings)]


async def review_version(state: RunState, version: int) -> list[Finding]:
    async def guarded(name, coro):
        try:
            fs = await coro
            events.emit(state.run_id, "agent", agent=name, status="done", msg=f"{name}: {len(fs)} findings")
            for f in fs:
                if f.severity == "high":
                    events.emit(state.run_id, "finding", stage="review", msg=f"{f.figure or 'page'}: {f.what}",
                                finding=f.model_dump())
            return fs
        except Exception as e:
            events.emit(state.run_id, "error", agent=name, msg=f"{name} failed: {e!r}"[:500])
            return []

    jobs = {"checks": asyncio.to_thread(run_checks, state.spec, run_dir(state), version),
            "fidelity": fidelity_checker(state, version)}
    for f in state.spec.figures:
        jobs[f"watch_{f.id}"] = visual_reviewer(state, f, version)
    results = await asyncio.gather(*(guarded(n, c) for n, c in jobs.items()))
    contract = [f for f in state.findings if f.layer == "contract"] if version == 1 else []
    return contract + [f for fs in results for f in fs]


class Review(Stage):
    name = "review"

    async def run(self, state: RunState) -> RunState:
        n = state.version
        stage_event(state, "review", f"v{n}: code checks + fidelity + {len(state.spec.figures)} watched reviews")
        fs = await review_version(state, n)
        save_json(run_dir(state) / "findings" / f"v{n}.json", fs)
        by = {}
        for f in fs:
            by.setdefault(f.layer, {"high": 0, "medium": 0, "low": 0})[f.severity] += 1
        state.versions[n]["findings"] = by
        state.findings = [f for f in state.findings if f.layer == "citation"] + fs
        stage_event(state, "review", f"v{n}: {len(fs)} findings " +
                    ", ".join(f"{k} {sum(v.values())} ({v['high']} high)" for k, v in by.items()))
        return state
