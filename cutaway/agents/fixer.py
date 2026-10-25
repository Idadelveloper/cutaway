"""Triage (Argon medium) + Fixer (Argon high, long decode) + the fix loop.

Loop: triage -> fix -> v<N+1> -> render -> review. Stop at zero high-severity findings or after
MAX_ROUNDS fix rounds. If a version scores worse (weighted findings) than the previous one, revert to
the best version and retry once with the regression as feedback.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from pydantic import BaseModel

from .. import config, events, llm, scoring
from ..schemas import BuildOutput, Finding, RunState
from .base import Stage, run_dir, save_json, stage_event
from .builder import write_files
from .render import Render
from .review import Review

MAX_ROUNDS = 3
MAX_FINDINGS_TO_FIX = 40


class _Ranked(BaseModel):
    id: str
    duplicates: list[str] = []
    files: list[str] = []
    note: str = ""


class _Triage(BaseModel):
    ranked: list[_Ranked]


def read_files(vdir: Path) -> dict[str, str]:
    return {str(p.relative_to(vdir)): p.read_text() for p in sorted(vdir.rglob("*"))
            if p.is_file() and p.suffix in (".html", ".css", ".js")}


def findings_block(fs: list[Finding]) -> str:
    return "\n".join(json.dumps(f.model_dump(), ensure_ascii=False) for f in fs)


async def triage(state: RunState, fs: list[Finding], files: list[str], version: int) -> list[tuple[Finding, _Ranked]]:
    out, _ = await llm.call_model(_Triage, stage=f"triage_v{version}", model=config.ARGON, thinking="medium",
                                  input=config.load_prompt("triage", version=version, files=", ".join(files),
                                                           findings=findings_block(fs)), run_id=state.run_id)
    by_id = {f.id: f for f in fs}
    ranked = [(by_id[r.id], r) for r in out.ranked if r.id in by_id]
    seen = {r.id for _, r in ranked} | {d for _, r in ranked for d in r.duplicates}
    # anything triage forgot stays in, after the ranked ones (high first)
    rest = sorted((f for f in fs if f.id not in seen), key=lambda f: scoring.WEIGHT[f.severity], reverse=True)
    return ranked + [(f, _Ranked(id=f.id)) for f in rest]


async def fix_version(state: RunState, src_version: int, new_version: int, fs: list[Finding],
                      extra: str = "", requirement: str | None = None) -> Path:
    """Write v<new_version> = v<src_version> + Fixer's changed files."""
    src = Path(state.versions[src_version]["dir"])
    files = read_files(src)
    if fs:
        ranked = await triage(state, fs, list(files), src_version)
        ranked = ranked[:MAX_FINDINGS_TO_FIX]
        save_json(run_dir(state) / "findings" / f"triage_v{src_version}.json",
                  [{"finding": f.model_dump(), **r.model_dump()} for f, r in ranked])
        block = "\n".join(json.dumps({**f.model_dump(), "files": r.files, "root_cause": r.note,
                                      "duplicates": r.duplicates}, ensure_ascii=False) for f, r in ranked)
    else:
        block = "(no findings)"
    if requirement:
        extra = f"## Change request (a requirement; implement it exactly)\n{requirement}\n\n{extra}"
    prompt = config.load_prompt(
        "fix", findings=block, extra=extra, spec=json.dumps(state.spec.model_dump(mode="json"), indent=1),
        model_notes=state.topic.get("model_notes", ""), contract=config.CONTRACT_PATH.read_text(),
        palette=config.palette_table(state.topic),
        files="\n\n".join(f"=== {name} ===\n{text}" for name, text in files.items()))
    out, _ = await llm.call_model(BuildOutput, stage=f"fix_v{new_version}", model=config.ARGON, thinking="high",
                                  background=True, max_output_tokens=config.LONG_OUTPUT_TOKENS,
                                  input=prompt, run_id=state.run_id)
    dst = run_dir(state) / f"v{new_version}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    write_files(dst, out.files)
    (dst.parent / "fixes").mkdir(exist_ok=True)
    (dst.parent / "fixes" / f"v{new_version}.md").write_text(
        f"# v{src_version} -> v{new_version}\n\nChanged: {', '.join(out.files)}\n\n{out.plan}\n")
    state.versions[new_version] = {"dir": str(dst), "from": src_version, "changed": sorted(out.files)}
    return dst


def version_findings(state: RunState, n: int) -> list[Finding]:
    p = run_dir(state) / "findings" / f"v{n}.json"
    return scoring.load_findings(p) if p.exists() else []


async def render_and_review(state: RunState, n: int) -> RunState:
    state.version = n
    state = await Render().run(state)
    return await Review().run(state)


class FixLoop(Stage):
    name = "fix"

    async def run(self, state: RunState) -> RunState:
        current = state.version
        best = state.best_version or current
        retried = False
        rounds = 0
        while rounds < MAX_ROUNDS:
            fs = version_findings(state, current)
            score, high = scoring.weighted(fs), scoring.high_count(fs)
            if high == 0:
                stage_event(state, "fix", f"v{current}: no high-severity findings; stopping")
                break
            rounds += 1
            new = max(state.versions) + 1
            stage_event(state, "fix", f"round {rounds}: v{current} ({len(fs)} findings, {high} high, "
                                      f"weighted {score}) -> v{new}")
            extra = state.versions[current].get("regression_note", "")
            await fix_version(state, current, new, [f for f in fs if f.layer != "citation"], extra=extra)
            state = await render_and_review(state, new)
            new_fs = version_findings(state, new)
            new_score = scoring.weighted(new_fs)
            state.versions[new]["weighted"] = new_score
            state.versions[current]["weighted"] = score
            if new_score < scoring.weighted(version_findings(state, best)):
                best = new
            if new_score > score and not retried:
                retried = True
                gone = {f.what for f in fs} - {f.what for f in new_fs}
                appeared = [f for f in new_fs if f.what not in {x.what for x in fs}]
                note = (f"## Regression feedback\nA previous fix attempt from v{best} made things worse "
                        f"(weighted findings {score} -> {new_score}). It introduced:\n" +
                        findings_block(appeared[:20]) + f"\nIt fixed {len(gone)} findings. Avoid those regressions.")
                state.versions[best]["regression_note"] = note
                events.emit(state.run_id, "info", stage="fix",
                            msg=f"v{new} regressed ({score} -> {new_score}); reverting to v{best} and retrying once")
                current = best
                continue
            current = new
        state.best_version = best
        state.version = best
        save_json(run_dir(state) / "versions.json", {"best": best, "versions": state.versions})
        stage_event(state, "fix", f"done: best version v{best}")
        return state
