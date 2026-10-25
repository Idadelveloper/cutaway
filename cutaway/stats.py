"""Research stats for the proof section -> runs/<id>/research_stats.json."""
from __future__ import annotations

import json
from collections import Counter

from . import config
from .schemas import RunState


def call_tokens(run_id: str, include=None, exclude=None) -> dict:
    """Sum token usage over logged calls in runs/<id>/calls, filtered by stage prefix."""
    tot = Counter()
    d = config.RUNS_DIR / run_id / "calls"
    for p in d.glob("*.json") if d.exists() else []:
        rec = json.loads(p.read_text())
        st = rec.get("stage", "")
        if include and not st.startswith(include):
            continue
        if exclude and st.startswith(exclude):
            continue
        for k, v in (rec.get("usage") or {}).items():
            tot[k] += v or 0
        tot["calls"] += 1
    return dict(tot)


def save_research_tokens(state: RunState) -> dict:
    """Called at the end of research (before the cache copy) so cached runs keep the count."""
    tok = call_tokens(state.run_id, exclude=("spec",))
    p = config.RUNS_DIR / state.run_id / "research" / "tokens.json"
    p.write_text(json.dumps(tok, indent=2))
    return tok


def research_stats(state: RunState) -> dict:
    rdir = config.RUNS_DIR / state.run_id / "research"
    ok = [s for s in state.sources if s.get("status") == "ok"]
    research_tok = json.loads((rdir / "tokens.json").read_text()) if (rdir / "tokens.json").exists() else {}
    spec_tok = call_tokens(state.run_id, include=("spec",))
    cit_path = config.RUNS_DIR / state.run_id / "findings" / "citations_stats.json"
    cit = json.loads(cit_path.read_text()) if cit_path.exists() else {}
    index = json.loads((rdir / "sources.json").read_text()) if (rdir / "sources.json").exists() else {}
    video_s = sum((v.get("duration_s") or 0) for k, v in state.research.items() if k.startswith("video_"))
    st = {
        "sources_by_type": dict(Counter(s["type"] for s in ok)),
        "sources_total": len(ok),
        "sources_skipped": len([s for s in state.sources if s.get("status") != "ok"]) + len(index.get("skipped", [])),
        "found_by": dict(Counter(w for s in ok for w in s.get("found_by", []))),
        "source_chars": sum(s.get("chars", 0) for s in ok),
        "tokens_read": research_tok.get("total_input_tokens", 0) + spec_tok.get("total_input_tokens", 0),
        "tokens_read_research": research_tok.get("total_input_tokens", 0),
        "tokens_read_spec": spec_tok.get("total_input_tokens", 0),
        "video_hours": round(video_s / 3600, 2),
        "videos_watched": len([k for k in state.research if k.startswith("video_")]),
        "conflicts_resolved": len(state.spec.conflicts) if state.spec else 0,
        "citations_total": cit.get("total", 0),
        "citations_verified": cit.get("verified", 0),
        "citations_verified_loose": cit.get("verified_loose", 0),
        "citations_missing": cit.get("missing", 0),
        "research_cached": (rdir / ".from_cache").exists(),
    }
    (config.RUNS_DIR / state.run_id / "research_stats.json").write_text(json.dumps(st, indent=2))
    return st
