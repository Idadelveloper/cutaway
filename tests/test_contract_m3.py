"""M3: the Playwright contract test on a tiny hand-written page (one passing, one failing)."""
from __future__ import annotations

import asyncio

from cutaway.harness.contract_test import contract_test
from cutaway.schemas import Spec
from tests.test_spec_m2 import GOOD

PAGE = """<!doctype html><html><body>
<p>The <span class="ent" data-entity="outer_mic" data-figure="F1">outer mic</span> listens.</p>
<figure id="fig-F1" data-figure="F1"><canvas width=200 height=100></canvas></figure>
<script>
const M = (t, c) => { const n = Math.sin(2*Math.PI*c.frequency_hz*t);
  return {p_noise_ear: n, p_anti_ear: -n, p_total_ear: __TOTAL__}; };
let t = 0, c = {frequency_hz: 200};
window.__cutaway = {
  figures: ["F1"],
  figure(id) { return {
    controls: {frequency_hz: {min: 50, max: 2000, default: 200, unit: "Hz"}},
    set(n, v) { c[n] = Math.min(2000, Math.max(50, v)); },
    reset() { t = 0; c = {frequency_hz: 200}; },
    advance(dt) { t += dt; },
    getState() { return {t, controls: {...c},
      entities: {outer_mic: {kind: "microphone", visible: true, color: "mic", position: [0,0,0]}},
      probes: __PROBES__}; },
    highlight(e) {},
  }; },
  pauseAll() {}, playAll() {},
};
__EXTRA__
</script></body></html>"""


def _page(tmp_path, total="0", probes="M(t, c)", extra=""):
    (tmp_path / "index.html").write_text(
        PAGE.replace("__TOTAL__", total).replace("__PROBES__", probes).replace("__EXTRA__", extra))
    return tmp_path


def _run(d):
    import os
    os.environ["CUTAWAY_HEADLESS"] = "1"
    return asyncio.run(contract_test(d, Spec.model_validate(GOOD)))


def test_contract_passes(tmp_path):
    fs, raw = _run(_page(tmp_path))
    assert fs == [], [f.what for f in fs]
    assert raw["report"]["figures"]["F1"]["probes"] == ["p_noise_ear", "p_anti_ear", "p_total_ear"]


def test_contract_fails(tmp_path):
    fs, _ = _run(_page(tmp_path, total="NaN", probes="{p_total_ear: NaN}", extra="undefinedFn();"))
    whats = " | ".join(f.what for f in fs)
    assert "console pageerror" in whats
    assert "non-finite probes ['p_total_ear']" in whats
    assert all(f.layer == "contract" for f in fs)
