"""SpecWriter (Argon high, background): all source texts + video facts -> cited spec.json.

Validated in code twice: structure/references (validate_spec) and the citation layer
(every quote must appear in the stored source text). One repair round fixes both.
"""
from __future__ import annotations

import json

from .. import config, events, llm, stats
from ..checks.citations import check_citations
from ..schemas import RunState, Spec
from .base import Stage, run_dir, save_json, stage_event
from .research import texts_dir

MAX_CHARS_PER_SOURCE = 120_000
MAX_TOTAL_CHARS = 2_800_000   # ~700k tokens; Argon has a 1M context


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
            if not c.quote.strip():
                errs.append(f"{kind} {oid}: citation of {c.source} has empty 'quote'")

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


def source_texts_block(state: RunState) -> tuple[str, list[dict]]:
    """All stored source texts, best rank first, within the context budget."""
    d = texts_dir(state)
    ok = sorted((s for s in state.sources if s.get("status") == "ok"), key=lambda s: (s.get("rank", 3), s["id"]))
    parts, listed, total = [], [], 0
    for s in ok:
        p = d / f"{s['id']}.txt"
        if not p.exists():
            continue
        text = p.read_text(errors="ignore")[:MAX_CHARS_PER_SOURCE]
        if total + len(text) > MAX_TOTAL_CHARS:
            events.emit(state.run_id, "info", stage="spec", msg=f"spec: context budget reached, left out {s['id']}")
            continue
        total += len(text)
        parts.append(f"=== [{s['id']}] {s['type']} (rank {s.get('rank')}) | {s['title']} | {s['url_or_file']}"
                     f"{' | ' + s['date'] if s.get('date') else ''} ===\n{text}")
        listed.append({k: s.get(k) for k in ("id", "type", "title", "url_or_file", "date", "rank")})
    return "\n\n".join(parts), listed


class SpecWriter(Stage):
    name = "spec"

    async def run(self, state: RunState) -> RunState:
        t = state.topic
        d = run_dir(state)
        texts, listed = source_texts_block(state)
        videos = {k: {kk: v[kk] for kk in ("source", "title", "summary", "duration_s", "items") if kk in v}
                  for k, v in state.research.items() if k.startswith("video_")}
        stage_event(state, "spec", f"writing cited spec from {len(listed)} source texts "
                                   f"({len(texts):,} chars) + {len(videos)} video analyses (Argon high, background)")
        lo, hi = t.get("figure_count", [8, 12])
        prompt = config.load_prompt(
            "spec", title=t["title"], prompt=t["prompt"], audience=t["audience"],
            figure_hints="; ".join(f"({i + 1}) {h}" for i, h in enumerate(t.get("figure_hints", []))),
            hero=t.get("hero", ""), fig_min=lo, fig_max=hi,
            sources=json.dumps(listed, indent=1),
            law_checks=json.dumps(t.get("law_checks", {}), indent=1),
            probes=", ".join(t.get("probes", [])), palette=", ".join(config.palette(t)),
            spec_notes=t.get("spec_notes", ""),
            video_research=json.dumps(videos, indent=1),
            diagram_research=json.dumps(state.research.get("diagrams", {}), indent=1),
            source_texts=texts)  # last, so placeholders inside source texts are never substituted
        call = dict(model=config.ARGON, thinking="high", background=True, max_output_tokens=131072,
                    run_id=state.run_id)
        spec, res = await llm.call_model(Spec, stage="spec", input=prompt, **call)
        events.emit(state.run_id, "info", stage="spec",
                    msg=f"spec: input tokens {res.usage.get('total_input_tokens')}", usage=res.usage)

        checks, palette = set(t.get("law_checks", {})), set(config.palette(t))
        errs = validate_spec(spec, checks, palette)
        cit_findings, cit_stats = check_citations(spec, texts_dir(state))
        high = [f for f in cit_findings if f.severity == "high"]
        stage_event(state, "spec", f"check: {len(errs)} structure errors, "
                                   f"{cit_stats['verified']}/{cit_stats['total']} quotes verified")
        if (errs or high) and not config.FAKE:
            lines = [f"- {e}" for e in errs[:150]] + [f"- {f.what}: {f.evidence}" for f in cit_findings[:150]]
            fix = ("The spec failed validation:\n" + "\n".join(lines) +
                   "\n\nReturn the complete corrected spec JSON. Every quote must be copied verbatim (<= 20 words) "
                   "from the cited source's text; if no source supports a fact, remove it. Output JSON only.")
            spec, res = await llm.call_model(Spec, stage="spec_repair", input=fix,
                                             previous_interaction_id=res.id, **call)
            errs = validate_spec(spec, checks, palette)
            cit_findings, cit_stats = check_citations(spec, texts_dir(state))
            stage_event(state, "spec", f"after repair: {len(errs)} structure errors, "
                                       f"{cit_stats['verified']}/{cit_stats['total']} quotes verified")

        save_json(d / "spec.json", spec)
        save_json(d / "spec_errors.json", errs)
        save_json(d / "findings" / "citations.json", cit_findings)
        save_json(d / "findings" / "citations_stats.json", cit_stats)
        if errs:
            raise RuntimeError(f"spec has {len(errs)} validation errors (see {d / 'spec_errors.json'}): {errs[:5]}")
        state.spec = spec
        state.findings = [f for f in state.findings if f.layer != "citation"] + cit_findings
        st = stats.research_stats(state)
        stage_event(state, "spec", f"valid: {len(spec.entities)} entities, {len(spec.laws)} laws, "
                                   f"{len(spec.figures)} figures, {len(spec.video_facts)} video facts, "
                                   f"{len(spec.conflicts)} conflicts; "
                                   f"{sum(f.severity == 'high' for f in cit_findings)} high citation findings",
                    stats=st)
        return state
