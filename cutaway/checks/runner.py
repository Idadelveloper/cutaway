"""Run every code check on a rendered version -> findings."""
from __future__ import annotations

import json
from pathlib import Path

from ..schemas import Finding, Spec, Trace
from . import common
from .lawkit import controls_affect_probes
from .registry import CHECKS
from .common import Ids


def load_traces(run_dir: Path, version: int) -> dict[tuple[str, str], Trace]:
    out = {}
    for p in sorted((run_dir / "traces" / f"v{version}").glob("*.json")):
        tr = Trace.model_validate_json(p.read_text())
        out[(tr.figure, tr.sweep)] = tr
    return out


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def run_checks(spec: Spec, run_dir: Path, version: int, palette: dict[str, str] | None = None) -> list[Finding]:
    traces = load_traces(run_dir, version)
    rdir = run_dir / "render" / f"v{version}"
    ids = Ids(f"v{version}-X")
    fs: list[Finding] = []
    fs += common.check_console(_load(rdir / "console.json", []), ids)
    fs += common.check_finite(traces, ids)
    fs += common.check_visibility(spec, traces, ids)
    fs += common.check_color_words(spec, _load(rdir / "ent_words.json", []), ids, palette)
    fs += common.check_frame_time(traces, ids)
    if (rdir / "page_text.txt").exists():
        fs += common.check_raw_markup((rdir / "page_text.txt").read_text(), ids)

    laws = {l.id: l for l in spec.laws}
    lids = Ids(f"v{version}-L")
    for f in spec.figures:
        by_sweep = {sweep: tr for (fig, sweep), tr in traces.items() if fig == f.id}
        if not by_sweep:
            continue
        for lid in f.laws:
            law = laws.get(lid)
            check = CHECKS.get(law.check) if law else None
            if check is None:
                continue
            try:
                fs += check(by_sweep, law, f.id, lids)
            except Exception as e:  # a crashing check is itself a finding, never a crash
                fs.append(Finding(id=lids(), layer="law", severity="low", figure=f.id, time=None,
                                  what=f"[{lid}] check {law.check} crashed", evidence=repr(e)[:300],
                                  fix_hint="check the probes this law needs"))
        # every figure needs at least one control that changes the physics (view-only controls are fine too)
        if f.controls and spec.laws and "controls_affect_probes" not in {laws[l].check for l in f.laws if l in laws}:
            pseudo = spec.laws[0].model_copy(update={"id": "auto", "check": "controls_affect_probes", "params": {}})
            per = controls_affect_probes(by_sweep, pseudo, f.id, Ids("tmp"))
            if len(per) == len(f.controls):
                fs.append(Finding(id=lids(), layer="interaction", severity="high", figure=f.id, time=None,
                                  what="no control changes the physics (only camera/view controls)",
                                  evidence="; ".join(x.what for x in per),
                                  fix_hint="add or wire a physics control (frequency, delay, amplitude, mic on/off)"))
    return fs
