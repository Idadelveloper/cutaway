"""Builder (Argon, long decode, background): spec + STYLE.md + contract -> the full explainer.

Attempts are written to runs/<id>/build/attempt<N>/. Contract failures go straight back to
Argon (same interaction thread) for up to MAX_CONTRACT_FIXES rounds; the result becomes v1/.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath

from .. import config, events, llm
from ..harness.contract_test import contract_test
from ..schemas import BuildOutput, Finding, RunState
from .base import Stage, run_dir, save_json, stage_event

MAX_CONTRACT_FIXES = 2
BUILD_CALL = dict(model=config.ARGON, thinking="high", background=True, max_output_tokens=config.LONG_OUTPUT_TOKENS)


def required_files(state: RunState) -> list[str]:
    return ["index.html", "style.css", "model.js", "parts.js", "contract.js"] + \
           [f"figures/{f.id}.js" for f in state.spec.figures]


def write_files(dst: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        rel = PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"unsafe path from model: {name}")
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def build_prompt(state: RunState) -> str:
    return config.load_prompt(
        "build", style=config.STYLE_PATH.read_text(), contract=config.CONTRACT_PATH.read_text(),
        hero=state.topic.get("hero", ""), palette=config.palette_table(state.topic),
        model_notes=state.topic.get("model_notes", ""),
        spec=json.dumps(state.spec.model_dump(mode="json"), indent=1))


def findings_text(fs: list[Finding]) -> str:
    return "\n".join(f"- [{f.severity}] {f.figure or 'page'}: {f.what} | evidence: {f.evidence} | hint: {f.fix_hint}"
                     for f in fs)


async def contract_round(state: RunState, files: dict[str, str], attempt: int) -> tuple[Path, list[Finding]]:
    d = run_dir(state) / "build" / f"attempt{attempt}"
    if d.exists():
        shutil.rmtree(d)
    write_files(d, files)
    missing = [f for f in required_files(state) if f not in files]
    fs, raw = await contract_test(d, state.spec, shot=run_dir(state) / "build" / f"attempt{attempt}.png")
    fs = [Finding(id=f"K0{i}", layer="contract", severity="high", figure=None, what=f"file missing: {m}",
                  evidence="", fix_hint="write this file") for i, m in enumerate(missing)] + fs
    save_json(run_dir(state) / "build" / f"attempt{attempt}_contract.json", {"findings": fs, **raw})
    return d, fs


class Builder(Stage):
    name = "build"

    def __init__(self, max_contract_fixes: int = MAX_CONTRACT_FIXES, prompt: str | None = None):
        self.max_fixes = max_contract_fixes
        self.prompt = prompt   # baselines pass their own prompt

    async def run(self, state: RunState) -> RunState:
        stage_event(state, "build", f"one-pass build of {len(state.spec.figures)} figures (Argon long decode)")
        out, res = await llm.call_model(BuildOutput, stage="build", input=self.prompt or build_prompt(state),
                                        run_id=state.run_id, **BUILD_CALL)
        files = dict(out.files)
        (run_dir(state) / "build").mkdir(parents=True, exist_ok=True)
        (run_dir(state) / "build" / "plan.md").write_text(out.plan)
        stage_event(state, "build", f"{len(files)} files, {sum(map(len, files.values())):,} chars; contract test")

        d, fs = await contract_round(state, files, 0)
        attempt = 0
        while [f for f in fs if f.severity == "high"] and attempt < self.max_fixes and not config.FAKE:
            attempt += 1
            events.emit(state.run_id, "info", stage="build",
                        msg=f"contract: {len(fs)} findings ({sum(f.severity == 'high' for f in fs)} high); fix round {attempt}")
            for f in fs[:5]:
                events.emit(state.run_id, "finding", stage="build", msg=f"{f.figure or 'page'}: {f.what}", finding=f.model_dump())
            fix, res = await llm.call_model(
                BuildOutput, stage="build_contract_fix", previous_interaction_id=res.id, run_id=state.run_id,
                input=config.load_prompt("fix_contract", findings=findings_text(fs)), **BUILD_CALL)
            files.update(fix.files)
            d, fs = await contract_round(state, files, attempt)

        v1 = run_dir(state) / "v1"
        if v1.exists():
            shutil.rmtree(v1)
        shutil.copytree(d, v1)
        save_json(run_dir(state) / "findings" / "contract_v1.json", fs)
        state.version = 1
        state.versions[1] = {"dir": str(v1), "contract_findings": len(fs), "contract_rounds": attempt}
        state.findings = [f for f in state.findings if f.layer != "contract"] + fs
        stage_event(state, "build", f"v1 written: {len(fs)} contract findings "
                                    f"({sum(f.severity == 'high' for f in fs)} high) after {attempt} fix rounds")
        return state
