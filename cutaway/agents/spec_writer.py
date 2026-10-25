"""SpecWriter (Argon high): all research in one context -> cited spec.json, validated in code."""
from __future__ import annotations

import json

from .. import config, events, llm
from ..schemas import RunState, Spec
from .base import Stage, run_dir, save_json, stage_event
from .research import research_digest


def validate_spec(spec: Spec, law_checks: set[str], palette: set[str]) -> list[str]:
    """Every fact cited, every citation points at a known source, references resolve."""
    errs: list[str] = []
    src_ids = {s.get("id") for s in spec.sources}
    entity_ids = {e.id for e in spec.entities}
    law_ids = {l.id for l in spec.laws}
    vf_ids = {v.id for v in spec.video_facts}

    def check_cites(kind: str, oid: str, cites) -> None:
        if not cites:
            errs.append(f"{kind} {oid}: no citation")
        for c in cites:
            if c.source not in src_ids:
                errs.append(f"{kind} {oid}: cites unknown source '{c.source}'")
            if not c.where.strip():
                errs.append(f"{kind} {oid}: citation of {c.source} has empty 'where'")

    for e in spec.entities:
        check_cites("entity", e.id, e.cite)
        if e.color not in palette:
            errs.append(f"entity {e.id}: color '{e.color}' not in palette {sorted(palette)}")
    for l in spec.laws:
        check_cites("law", l.id, l.cite)
        if l.check not in law_checks:
            errs.append(f"law {l.id}: check '{l.check}' not in {sorted(law_checks)}")
    for v in spec.video_facts:
        if v.source not in src_ids:
            errs.append(f"video_fact {v.id}: unknown source '{v.source}'")
        if not v.time.strip():
            errs.append(f"video_fact {v.id}: no timestamp")
    for f in spec.figures:
        check_cites("figure", f.id, f.cite)
        for eid in f.entities:
            if eid not in entity_ids:
                errs.append(f"figure {f.id}: unknown entity '{eid}'")
        for lid in f.laws:
            if lid not in law_ids:
                errs.append(f"figure {f.id}: unknown law '{lid}'")
        for ref in f.compare_with:
            if ref.startswith("VF") and ref not in vf_ids:
                errs.append(f"figure {f.id}: unknown video fact '{ref}'")
        for c in f.controls:
            missing = {"name", "min", "max", "default"} - set(c)
            if missing:
                errs.append(f"figure {f.id}: control {c.get('name')} missing {sorted(missing)}")
            elif not (c["min"] <= c["default"] <= c["max"]):
                errs.append(f"figure {f.id}: control {c['name']} default outside [min, max]")
    ids = [f.id for f in spec.figures]
    if len(ids) != len(set(ids)):
        errs.append("duplicate figure ids")
    return errs


class SpecWriter(Stage):
    name = "spec"

    async def run(self, state: RunState) -> RunState:
        t = state.topic
        stage_event(state, "spec", "writing cited spec (Argon high)")
        lo, hi = t.get("figure_count", [8, 12])
        prompt = config.load_prompt(
            "spec", title=t["title"], prompt=t["prompt"], audience=t["audience"],
            figure_hints="; ".join(f"({i + 1}) {h}" for i, h in enumerate(t.get("figure_hints", []))),
            hero=t.get("hero", ""), fig_min=lo, fig_max=hi,
            sources=json.dumps(state.sources, indent=1), research=research_digest(state),
            law_checks=json.dumps(t.get("law_checks", {}), indent=1),
            probes=", ".join(t.get("probes", [])), palette=", ".join(config.PALETTE))
        spec, res = await llm.call_model(Spec, stage="spec", model=config.ARGON, thinking="high",
                                         input=prompt, run_id=state.run_id)
        checks, palette = set(t.get("law_checks", {})), set(config.PALETTE)
        errs = validate_spec(spec, checks, palette)
        if errs and not config.FAKE:
            events.emit(state.run_id, "info", stage="spec", msg=f"spec: {len(errs)} citation/ref errors, repairing")
            fix = ("The spec failed validation:\n" + "\n".join(f"- {e}" for e in errs[:200]) +
                   "\n\nReturn the complete corrected spec JSON. Add missing citations from the research; "
                   "remove any fact you cannot cite. Output JSON only.")
            spec, res = await llm.call_model(Spec, stage="spec_repair", model=config.ARGON, thinking="high",
                                             input=fix, previous_interaction_id=res.id, run_id=state.run_id)
            errs = validate_spec(spec, checks, palette)
        d = run_dir(state)
        save_json(d / "spec.json", spec)
        save_json(d / "spec_errors.json", errs)
        if errs:
            raise RuntimeError(f"spec has {len(errs)} validation errors (see {d / 'spec_errors.json'}): {errs[:5]}")
        state.spec = spec
        stage_event(state, "spec", f"valid: {len(spec.entities)} entities, {len(spec.laws)} laws, "
                                   f"{len(spec.figures)} figures, {len(spec.video_facts)} video facts, "
                                   f"{len(spec.sources)} sources, {len(spec.conflicts)} conflicts")
        return state
