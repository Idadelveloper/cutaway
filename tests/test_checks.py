"""M5: every check gets one passing and one failing synthetic trace."""
from __future__ import annotations

import math

import pytest

from cutaway.checks import acoustics, common
from cutaway.checks.common import Ids
from cutaway.schemas import Law, Spec, Trace
from tests.test_spec_m2 import GOOD

N, DT, SCALE = 120, 1 / 30, 200.0   # 120 frames; physical time = t / SCALE


def frames(f=100.0, delay=0.0, gain=1.0, anc=True, c=343.0, broken_sum=False, sweep_delay=None, sweep_f=None,
           disp=0.5, drift=0.0, residual_bias=0.0, nan_at=None, visible=True, particles=True):
    out = []
    for i in range(N):
        t = (i + 1) * DT
        tp = t / SCALE
        fi = sweep_f[0] + (sweep_f[1] - sweep_f[0]) * i / (N - 1) if sweep_f else f
        di = sweep_delay[0] + (sweep_delay[1] - sweep_delay[0]) * i / (N - 1) if sweep_delay else delay
        noise = math.sin(2 * math.pi * fi * tp)
        anti = -gain * math.sin(2 * math.pi * fi * (tp - di)) if anc else 0.0
        total = noise + anti + (0.1 if broken_sum else 0.0)
        probes = {"p_noise_ear": noise, "p_anti_ear": anti, "p_total_ear": total, "frequency_hz": fi,
                  "wavelength_m": c / fi, "delay_s": di,
                  "residual_ratio": (2 * abs(math.sin(math.pi * fi * di)) if anc else 1.0) + residual_bias}
        if particles:
            probes |= {"particle_disp_max": disp * abs(math.sin(2 * math.pi * t)) + (drift * t),
                       "particle_mean_offset": drift * t}
        if nan_at == i:
            probes["p_total_ear"] = None
        out.append({"t": t, "controls": {}, "probes": probes,
                    "entities": {"outer_mic": {"kind": "microphone", "visible": visible, "color": "mic"}}})
    return out


def tr(sweep="default", **kw) -> Trace:
    return Trace(figure="F1", sweep=sweep, frames=frames(**kw), frame_ms=[3.0] * N)


def law(check, **params) -> Law:
    return Law(id="L1", text="", check=check, params=params, cite=[{"source": "P1", "where": "p.1", "quote": "q"}])


def run(check, trs, **params):
    return acoustics.CHECKS[check](trs, law(check, **params), "F1", Ids("T"))


# ---------- acoustics ----------

@pytest.mark.parametrize("check,good,bad,params", [
    ("wave_speed", {"default": tr()}, {"default": tr(c=300.0)}, {"speed_of_sound_m_s": 343, "tolerance": 0.02}),
    ("superposition_add", {"default": tr()}, {"default": tr(broken_sum=True)}, {"rel_tolerance": 1e-6}),
    ("superposition_cancel", {"default": tr()}, {"default": tr(gain=0.5)}, {"max_ratio": 0.05, "cutoff_hz": 1000}),
    ("delay_residual", {"delay_s": tr("delay_s", f=500, sweep_delay=(0, 0.002))},
     {"delay_s": tr("delay_s", f=500, sweep_delay=(0, 0.002), residual_bias=0.3)}, {"tolerance": 0.1}),
    ("phase_flip", {"default": tr()}, {"default": tr(delay=0.0, gain=-1.0)}, {"max_corr": -0.95}),
    ("particles_oscillate", {"default": tr()}, {"default": tr(drift=0.05)}, {"max_disp": 1.0, "max_drift": 0.05}),
    ("controls_affect_probes", {"default": tr(), "frequency_hz": tr("frequency_hz", sweep_f=(50, 2000))},
     {"default": tr(), "frequency_hz": tr("frequency_hz")}, {}),
])
def test_law_checks(check, good, bad, params):
    assert run(check, good, **params) == [], check
    fs = run(check, bad, **params)
    assert fs and all(f.layer == "law" and f.severity == "high" for f in fs), (check, fs)


def test_superposition_cancel_uses_delay_sweep_when_default_has_delay():
    trs = {"default": tr(delay=0.001), "delay_s": tr("delay_s", sweep_delay=(0, 0.002), residual_bias=0.2)}
    assert run("superposition_cancel", trs, max_ratio=0.05, cutoff_hz=1000)


def test_missing_probes_is_medium():
    fs = run("particles_oscillate", {"default": tr(particles=False)})
    assert fs and fs[0].severity == "medium" and "cannot check" in fs[0].what


# ---------- common ----------

def test_finite():
    assert common.check_finite({("F1", "default"): tr()}, Ids("C")) == []
    fs = common.check_finite({("F1", "default"): tr(nan_at=7)}, Ids("C"))
    assert fs and "frame 7" in fs[0].time


def test_console():
    assert common.check_console([{"type": "log", "text": "hi"}], Ids("C")) == []
    fs = common.check_console([{"type": "pageerror", "text": "x is not defined"}] * 3, Ids("C"))
    assert len(fs) == 1 and fs[0].severity == "high"


def test_visibility():
    spec = Spec.model_validate(GOOD)
    assert common.check_visibility(spec, {("F1", "default"): tr()}, Ids("C")) == []
    fs = common.check_visibility(spec, {("F1", "default"): tr(visible=False)}, Ids("C"))
    assert fs and "never visible" in fs[0].what


def test_color_words():
    spec = Spec.model_validate(GOOD)
    ok = [{"entity": "outer_mic", "figure": "F1", "text": "outer mic", "color": "rgb(31, 163, 122)"}]
    assert common.check_color_words(spec, ok, Ids("C")) == []
    bad = [{"entity": "outer_mic", "figure": "F1", "text": "outer mic", "color": "rgb(255, 0, 0)"},
           {"entity": "ghost", "figure": "F1", "text": "ghost", "color": "rgb(0, 0, 0)"}]
    whats = [f.what for f in common.check_color_words(spec, bad, Ids("C"))]
    assert any("is rgb(255, 0, 0)" in w for w in whats) and any("unknown entity" in w for w in whats)


def test_frame_time():
    assert common.check_frame_time({("F1", "default"): tr()}, Ids("C")) == []
    slow = tr()
    slow.frame_ms = [80.0] * N
    assert common.check_frame_time({("F1", "default"): slow}, Ids("C"))


def test_raw_markup():
    assert common.check_raw_markup("residual = 2|sin(pi f t)| costs $5", Ids("C")) == []
    fs = common.check_raw_markup(r"error: $\phi = 360^\circ \times f$ and \frac{a}{b}", Ids("C"))
    assert fs and "unrendered LaTeX" in fs[0].what
