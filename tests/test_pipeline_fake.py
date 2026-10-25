"""M3-M7 plumbing in fake mode, under the ADK workflow: research (cached) -> spec -> build -> render ->
review -> fix loop -> publish. Fixtures are synthetic (the tiny contract page), written by the test."""
from __future__ import annotations

import asyncio
import json
import os

from cutaway import config, scoring
from cutaway.harness import render as render_mod
from tests.test_contract_m3 import PAGE
from tests.test_spec_m2 import GOOD, PAPER_TEXT, VIDEO_TEXT

GOOD_PAGE = PAGE.replace("__TOTAL__", "0").replace("__PROBES__", "M(t, c)").replace("__EXTRA__", "")
FIXED_EXTRA = {"style.css": "body{}", "model.js": "", "parts.js": "", "figures/F1.js": "", "contract.js": ""}


def test_full_pipeline_fake(tmp_path, monkeypatch):
    os.environ["CUTAWAY_HEADLESS"] = "1"
    fx = tmp_path / "fixtures"

    def fixture(stage, data):
        (fx / stage).mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        (fx / stage / "x.json").write_text(json.dumps({"text": text, "usage": {"total_input_tokens": 10}}))

    fixture("spec", GOOD)
    fixture("build", {"plan": "p", "files": {"index.html": GOOD_PAGE}})            # missing files -> contract findings
    fixture("fidelity_v1", {"findings": [{"figure": "F1", "what": "diameter 5 mm vs cited 4 mm", "severity": "medium"}]})
    fixture("fidelity_v2", {"findings": []})
    fixture("review_v1_F1", {"answers": [{"q": 1, "answer": "no", "clip": "F1_default", "time": "0-2s",
                                          "evidence": "wave moves the wrong way vs V1 01:02", "severity": "high"}]})
    fixture("review_v2_F1", {"answers": [{"q": i, "answer": "yes"} for i in range(1, 6)]})
    fixture("triage_v1", {"ranked": []})
    fixture("fix_v2", {"plan": "added missing files", "files": FIXED_EXTRA})

    # cached research so no discoverer runs
    cache = tmp_path / "cache" / "anc" / "research"
    (cache / "sources").mkdir(parents=True)
    (cache / "sources" / "P1.txt").write_text(PAPER_TEXT)
    (cache / "sources" / "V1.txt").write_text(VIDEO_TEXT)
    srcs = [{"id": "P1", "type": "paper", "title": "ANC paper", "url_or_file": "https://x.org/a.pdf", "status": "ok",
             "rank": 1, "found_by": ["scout"], "chars": len(PAPER_TEXT)},
            {"id": "V1", "type": "video", "title": "ANC explained", "url_or_file": "https://www.youtube.com/watch?v=abcdefg",
             "status": "ok", "rank": 2, "found_by": ["search"], "chars": len(VIDEO_TEXT)}]
    (cache / "research_state.json").write_text(json.dumps({"sources": srcs, "research": {
        "video_V1": {"source": "V1", "title": "t", "summary": "", "duration_s": 300, "items": []}}, "plan": {}}))
    (cache / "sources.json").write_text(json.dumps({"sources": srcs, "skipped": []}))

    monkeypatch.setattr(config, "FAKE", True)
    monkeypatch.setattr(config, "FIXTURES_DIR", fx)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(render_mod, "FRAMES", 20)
    monkeypatch.setattr(render_mod, "SCROLL_FRAMES", 5)

    from cutaway.agents.pipeline import stages_for
    from cutaway.agents.root import run_pipeline
    from cutaway.schemas import RunState

    st = RunState(run_id="e2e", topic=config.load_topic("anc"))
    st = asyncio.run(run_pipeline(st, stages_for(st, "publish")))

    run = tmp_path / "runs" / "e2e"
    rows = {r["version"]: r for r in scoring.version_table(run)}
    assert set(rows) == {"v1", "v2"}
    v1 = rows["v1"]["layers"]
    assert v1["contract"]["high"] >= 4 and v1["watched"]["high"] == 1 and v1["fidelity"]["medium"] == 1
    assert rows["v2"]["high"] == 0 and rows["v2"]["weighted"] < rows["v1"]["weighted"]
    assert st.best_version == 2
    for name in ("F1_default.mp4", "F1_frequency_hz.mp4"):
        assert (run / "clips" / "v2" / name).exists()
    page = (run / "final" / "index.html").read_text()
    assert 'id="proof"' in page and "Findings per version" in page and "Cited sources" in page
    assert "youtube.com/embed/abcdefg" in page                       # reference moment next to the fix
    assert (run / "final" / "proof" / "fix0_before.mp4").exists()
    assert json.loads((run / "published.json").read_text())["version"] == 2
