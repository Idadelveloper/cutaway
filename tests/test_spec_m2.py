"""M2: spec validation, citation layer, source merge/rank, and the pipeline in fake mode.

Fixtures here are synthetic (written by the test into a temp dir), only to exercise the plumbing.
"""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from cutaway import config
from cutaway.agents.research import merge, norm_url
from cutaway.agents.spec_writer import validate_spec
from cutaway.checks.citations import check_citations
from cutaway.schemas import DiscoveryOutput, Spec

PAPER_TEXT = ("[p.3]\nThe feedforward microphone picks up ambient noise before it reaches the ear.\n"
              "Latency of the anti-noise path limits cancellation at   higher frequencies.")
VIDEO_TEXT = "[01:02] Here you can see the inverted wave cancelling the original."
C = [{"source": "P1", "where": "p.3", "quote": "Latency of the anti-noise path limits cancellation"}]
GOOD = {
    "topic": "ANC", "audience": "adults",
    "entities": [{"id": "outer_mic", "kind": "microphone", "description": "feedforward mic",
                  "params": {"diameter_mm": 4}, "color": "mic",
                  "cite": [{"source": "P1", "where": "p.3", "quote": "The FEEDFORWARD microphone picks up ambient noise"}]}],
    "laws": [{"id": "L1", "text": "p_total = p_noise + p_anti", "check": "superposition_add",
              "params": {"rel_tolerance": 1e-6},
              "cite": [{"source": "V1", "where": "01:02", "quote": "the inverted wave cancelling the original"}]}],
    "video_facts": [{"id": "VF1", "text": "wave inverted", "source": "V1", "time": "01:02", "kind": "motion"}],
    "figures": [{"id": "F1", "section": "Hero", "teaches": "the ear cup", "entities": ["outer_mic"],
                 "controls": [{"name": "frequency_hz", "label": "Frequency", "min": 50, "max": 2000,
                               "default": 200, "unit": "Hz"}],
                 "probes": ["p_total_ear"], "laws": ["L1"], "compare_with": ["VF1"], "cite": C}],
    "sources": [{"id": "P1", "type": "paper", "title": "ANC paper", "url_or_file": "https://x.org/a.pdf"},
                {"id": "V1", "type": "video", "title": "ANC explained", "url_or_file": "https://youtube.com/watch?v=abc"}],
    "conflicts": [{"fact": "cutoff", "values": ["1 kHz (P1)", "800 Hz (A1)"], "chosen": "1 kHz", "why": "rank 1"}],
}
CHECKS = set(config.load_topic("anc")["law_checks"])
PALETTE = set(config.PALETTE)


def test_valid_spec_passes():
    assert validate_spec(Spec.model_validate(GOOD), CHECKS, PALETTE) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda s: s["entities"][0].update(cite=[]), "no citation"),
    (lambda s: s["laws"][0]["cite"][0].update(source="S9"), "unknown source"),
    (lambda s: s["figures"][0].update(cite=[]), "figure F1: no citation"),
    (lambda s: s["laws"][0].update(check="made_up"), "not in"),
    (lambda s: s["entities"][0].update(color="chartreuse"), "not in palette"),
    (lambda s: s["figures"][0].update(entities=["ghost"]), "unknown entity"),
    (lambda s: s["video_facts"][0].update(time=""), "no timestamp"),
    (lambda s: s["figures"][0]["controls"][0].update(default=5000), "default outside"),
    (lambda s: s["entities"][0]["cite"][0].update(quote=" "), "empty 'quote'"),
])
def test_invalid_spec_fails(mutate, expect):
    s = copy.deepcopy(GOOD)
    mutate(s)
    errs = validate_spec(Spec.model_validate(s), CHECKS, PALETTE)
    assert any(expect in e for e in errs), errs


def _texts(tmp_path):
    d = tmp_path / "texts"
    d.mkdir()
    (d / "P1.txt").write_text(PAPER_TEXT)
    (d / "V1.txt").write_text(VIDEO_TEXT)
    return d


def test_citations_all_verified(tmp_path):
    findings, st = check_citations(Spec.model_validate(GOOD), _texts(tmp_path))
    assert findings == [] and st["verified"] == st["total"] == 3


@pytest.mark.parametrize("mutate,severity,expect", [
    (lambda s: s["entities"][0]["cite"][0].update(quote="microphones are made of cheese"), "high", "not found"),
    (lambda s: s["laws"][0]["cite"][0].update(source="P2"), "high", "no stored text"),
    (lambda s: s["figures"][0]["cite"][0].update(quote=" ".join(["word"] * 25)), "medium", "max 20"),
])
def test_citations_fail(tmp_path, mutate, severity, expect):
    s = copy.deepcopy(GOOD)
    mutate(s)
    findings, _ = check_citations(Spec.model_validate(s), _texts(tmp_path))
    assert any(f.severity == severity and expect in f.what and f.layer == "citation" for f in findings), findings


def test_merge_dedupes_and_ranks():
    a = DiscoveryOutput.model_validate({"sources": [
        {"title": "Blog", "url": "https://www.blog.com/anc/?utm_source=x", "type": "blog"},
        {"title": "Paper", "url": "https://arxiv.org/pdf/1", "type": "paper"},
        {"title": "Vid", "url": "https://www.youtube.com/watch?v=abc&t=10", "type": "article"}]})
    b = DiscoveryOutput.model_validate({"sources": [
        {"title": "Paper again", "url": "http://arxiv.org/pdf/1/", "type": "paper", "text": "full text"},
        {"title": "Mfr", "url": "https://chipmaker.com/appnote", "type": "manufacturer"}],
        "skipped": [{"url": "https://paywalled.com", "reason": "paywall"}]})
    srcs, texts, skipped = merge({"search": a, "scout": b}, [])
    ids = [s.id for s in srcs]
    assert len(srcs) == 4                                     # arxiv deduped
    assert [s.rank for s in srcs] == sorted(s.rank for s in srcs)
    paper = next(s for s in srcs if s.type == "paper")
    assert paper.found_by == ["search", "scout"] and texts[paper.id] == "full text"
    assert "V1" in ids and "B1" in ids and "M1" in ids        # youtube link typed as video
    assert skipped[0]["reason"] == "paywall"
    assert norm_url("http://www.x.com/a/?utm_medium=y&q=1#frag") == "https://x.com/a?q=1"


def test_pipeline_fake(tmp_path, monkeypatch):
    fx = tmp_path / "fixtures"
    fixtures = {
        "intake": {"questions": ["q"], "focus": {}, "gaps": []},
        "discover_search": {"sources": [
            {"title": "ANC explained", "url": "https://www.youtube.com/watch?v=abc", "type": "video"},
            {"title": "Paywalled", "url": "https://journal.com/paywalled", "type": "paper"}]},
        "deep_research": "report citing https://x.org/a.pdf",
        "deep_research_extract": {"sources": [{"title": "ANC paper", "url": "https://x.org/a.pdf", "type": "paper"}]},
        "scout": {"sources": [{"title": "ANC paper", "url": "https://x.org/a.pdf", "type": "paper",
                               "text": PAPER_TEXT * 400}], "skipped": [], "checks": []},
        "video_V1": {"source": "V1", "title": "t", "summary": "s", "duration_s": 600, "transcript": VIDEO_TEXT,
                     "items": [{"id": "V1-1", "kind": "motion", "text": "wave flips", "where": "01:02"}]},
        "spec": GOOD,
    }
    for stage, data in fixtures.items():
        (fx / stage).mkdir(parents=True)
        text = data if isinstance(data, str) else json.dumps(data)
        (fx / stage / "x.json").write_text(json.dumps({"text": text, "usage": {"total_input_tokens": 1000}}))
    (tmp_path / "sources" / "anc").mkdir(parents=True)

    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "FIXTURES_DIR", fx)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(config, "ROOT", tmp_path)

    from cutaway import stats
    from cutaway.agents.intake import Intake
    from cutaway.agents.research import Research, load_cache, save_cache
    from cutaway.agents.spec_writer import SpecWriter
    from cutaway.schemas import RunState

    async def go(run_id):
        st = RunState(run_id=run_id, topic=config.load_topic("anc"))
        if not load_cache(st):
            st = await Intake().run(st)
            st = await Research().run(st)
            save_cache(st)
        return await SpecWriter().run(st)

    st = asyncio.run(go("t1"))
    by_id = {s["id"]: s for s in st.sources}
    assert by_id["P1"]["status"] == "ok" and by_id["P1"]["found_by"] == ["deep_research", "scout"]
    assert by_id["P2"]["status"] == "skipped"                 # no fetch fixture -> skipped and logged
    assert by_id["V1"]["status"] == "ok"                      # transcript stored as text
    run = tmp_path / "runs" / "t1"
    assert (run / "research" / "sources" / "P1.txt").exists()
    assert json.loads((run / "research" / "sources.json").read_text())["sources"][0]["rank"] == 1
    assert json.loads((run / "findings" / "citations.json").read_text()) == []
    s = stats.research_stats(st)
    assert s["citations_verified"] == 3 and s["video_hours"] == 0.17 and s["conflicts_resolved"] == 1
    assert s["tokens_read"] > 0 and not s["research_cached"]

    st2 = asyncio.run(go("t2"))                                # second run reuses cache/anc/research
    assert stats.research_stats(st2)["research_cached"]
    assert not list((tmp_path / "runs" / "t2" / "calls").glob("*_discover_search.json"))
