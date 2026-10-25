"""M2: spec validation + the intake -> research -> spec pipeline in fake mode.

Fixtures here are synthetic (written by the test into a temp dir), only to exercise the plumbing.
"""
from __future__ import annotations

import asyncio
import copy
import json

import pytest

from cutaway import config
from cutaway.agents.spec_writer import validate_spec
from cutaway.schemas import Spec

C = [{"source": "V1", "where": "01:02"}]
GOOD = {
    "topic": "ANC", "audience": "adults",
    "entities": [{"id": "outer_mic", "kind": "microphone", "description": "feedforward mic",
                  "params": {"diameter_mm": 4}, "color": "mic", "cite": C}],
    "laws": [{"id": "L1", "text": "p_total = p_noise + p_anti", "check": "superposition_add",
              "params": {"rel_tolerance": 1e-6}, "cite": C}],
    "video_facts": [{"id": "VF1", "text": "wave inverted", "source": "V1", "time": "01:02", "kind": "motion"}],
    "figures": [{"id": "F1", "section": "Hero", "teaches": "the ear cup", "entities": ["outer_mic"],
                 "controls": [{"name": "frequency_hz", "label": "Frequency", "min": 50, "max": 2000,
                               "default": 200, "unit": "Hz"}],
                 "probes": ["p_total_ear"], "laws": ["L1"], "compare_with": ["VF1"], "cite": C}],
    "sources": [{"id": "V1", "type": "video", "title": "ANC explained", "url_or_file": "x.mp4"}],
    "conflicts": [],
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
])
def test_invalid_spec_fails(mutate, expect):
    s = copy.deepcopy(GOOD)
    mutate(s)
    errs = validate_spec(Spec.model_validate(s), CHECKS, PALETTE)
    assert any(expect in e for e in errs), errs


def test_pipeline_fake(tmp_path, monkeypatch):
    fx = tmp_path / "fixtures"
    for stage, data in {
        "intake": {"questions": ["q"], "focus": {"V1": "motion"}, "gaps": []},
        "video_V1": {"source": "V1", "title": "t", "summary": "s",
                     "items": [{"id": "V1-1", "kind": "motion", "text": "wave flips", "where": "01:02"}]},
        "spec": GOOD,
    }.items():
        (fx / stage).mkdir(parents=True)
        (fx / stage / "x.json").write_text(json.dumps({"text": json.dumps(data)}))
    src = tmp_path / "sources" / "anc"
    src.mkdir(parents=True)
    (src / "urls.txt").write_text("ANC explained | https://www.youtube.com/watch?v=abc\n")

    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "FIXTURES_DIR", fx)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SOURCES_DIR", tmp_path / "sources")

    from cutaway.agents.intake import Intake
    from cutaway.agents.research import Research
    from cutaway.agents.spec_writer import SpecWriter
    from cutaway.schemas import RunState

    async def go():
        st = RunState(run_id="t", topic=config.load_topic("anc"))
        for stage in (Intake(), Research(), SpecWriter()):
            st = await stage.run(st)
        return st

    st = asyncio.run(go())
    assert st.sources[0]["id"] == "V1" and st.sources[0]["type"] == "video"
    assert "video_V1" in st.research
    assert st.spec and st.spec.figures[0].id == "F1"
    assert (tmp_path / "runs" / "t" / "spec.json").exists()
    assert list((tmp_path / "runs" / "t" / "calls").glob("*_spec.json"))
