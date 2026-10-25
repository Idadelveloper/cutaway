"""M4: render harness on the tiny contract page: traces + clips + keyframes + scroll clip."""
from __future__ import annotations

import asyncio
import json
import os

from cutaway.harness.render import FRAMES, render_version
from cutaway.schemas import Spec
from tests.test_contract_m3 import _page
from tests.test_spec_m2 import GOOD


def test_render(tmp_path):
    os.environ["CUTAWAY_HEADLESS"] = "1"
    (tmp_path / "site").mkdir()
    site = _page(tmp_path / "site")
    run = tmp_path / "run"
    timing = asyncio.run(render_version(site, run, 1, Spec.model_validate(GOOD)))
    assert timing["errors"] == [] and timing["sweeps"] == 2           # default + frequency_hz
    tr = json.loads((run / "traces" / "v1" / "F1_frequency_hz.json").read_text())
    assert len(tr["frames"]) == FRAMES
    assert tr["frames"][0]["controls"]["frequency_hz"] == 50 and tr["frames"][-1]["controls"]["frequency_hz"] == 2000
    assert abs(tr["frames"][-1]["t"] - FRAMES / 30) < 1e-9
    for name in ("F1_default.mp4", "F1_frequency_hz.mp4"):
        assert (run / "clips" / "v1" / name).stat().st_size > 0
    assert (run / "render" / "v1" / "scroll.mp4").exists() and (run / "render" / "v1" / "page.png").exists()
    assert (run / "keyframes" / "v1" / "F1_default_mid.jpg").exists()
    from PIL import Image  # the clip must be the <figure>, not a colored word that shares data-figure
    w, h = Image.open(run / "keyframes" / "v1" / "F1_default_mid.jpg").size
    assert h >= 100, (w, h)
    assert not (run / "frames" / "v1").exists()                     # raw frames cleaned up
