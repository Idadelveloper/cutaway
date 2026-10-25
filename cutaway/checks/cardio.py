"""Cardiac cycle law checks (left heart). Probes per frame:
heart_rate_bpm, lv_volume_ml, lv_pressure_mmhg, la_pressure_mmhg, aortic_pressure_mmhg,
mitral_open (0/1), aortic_open (0/1), stroke_volume_ml, edv_ml, esv_ml, cardiac_output_l_min, t_phys_s.
"""
from __future__ import annotations

import numpy as np

from ..schemas import Finding, Law, Trace
from .common import Ids
from .lawkit import _f, _missing, series


def _get(tr: Trace, *names):
    return [series(tr, n) for n in names]


def _too_many(bad: np.ndarray, n: int, frac: float) -> bool:
    return len(bad) > max(1, int(frac * n))


def valve_pressure_logic(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """A valve is open only when the upstream pressure exceeds the downstream pressure (one-way valves)."""
    tol = float(law.params.get("tol_mmhg", 2.0))
    frac = float(law.params.get("max_violation_frac", 0.05))
    checked = 0
    for sweep, tr in trs.items():
        la, lv, ao, mo, av = _get(tr, "la_pressure_mmhg", "lv_pressure_mmhg", "aortic_pressure_mmhg",
                                  "mitral_open", "aortic_open")
        if any(x is None for x in (la, lv, ao, mo, av)):
            continue
        checked += len(lv)
        mo, av = mo > 0.5, av > 0.5
        bad_m = np.flatnonzero((mo & (la < lv - tol)) | (~mo & (la > lv + tol)))
        bad_a = np.flatnonzero((av & (lv < ao - tol)) | (~av & (lv > ao + tol)))
        for name, bad, up, down, op in (("mitral", bad_m, la, lv, mo), ("aortic", bad_a, lv, ao, av)):
            if _too_many(bad, len(lv), frac):
                i = int(bad[0])
                return [_f(ids, fig, law, "high",
                           f"{name} valve state contradicts pressures in {len(bad)}/{len(lv)} frames",
                           f"{sweep} frame {i}: upstream {up[i]:.1f} mmHg, downstream {down[i]:.1f} mmHg, "
                           f"{name}_open={int(op[i])}", "open a valve only when upstream pressure > downstream",
                           time=f"frame {i}")]
    if not checked:
        return _missing(ids, fig, law, ["la_pressure_mmhg", "lv_pressure_mmhg", "aortic_pressure_mmhg",
                                        "mitral_open", "aortic_open"])
    return []


def valves_not_both_open(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    for sweep, tr in trs.items():
        mo, av = _get(tr, "mitral_open", "aortic_open")
        if mo is None or av is None:
            return _missing(ids, fig, law, ["mitral_open", "aortic_open"])
        both = np.flatnonzero((mo > 0.5) & (av > 0.5))
        if len(both):
            return [_f(ids, fig, law, "high", f"mitral and aortic valves open at the same time ({len(both)} frames)",
                       f"{sweep} frame {int(both[0])}", "the inlet and outlet valves never open together",
                       time=f"frame {int(both[0])}")]
    return []


def isovolumetric_volume(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """With both valves closed, ventricular volume can't change."""
    tol = float(law.params.get("tol_ml", 0.5))
    for sweep, tr in trs.items():
        v, mo, av = _get(tr, "lv_volume_ml", "mitral_open", "aortic_open")
        if v is None or mo is None or av is None:
            return _missing(ids, fig, law, ["lv_volume_ml", "mitral_open", "aortic_open"])
        closed = (mo < 0.5) & (av < 0.5)
        both = closed[1:] & closed[:-1]
        dv = np.abs(np.diff(v))
        bad = np.flatnonzero(both & (dv > tol))
        if len(bad):
            i = int(bad[0]) + 1
            return [_f(ids, fig, law, "high", f"volume changes by {dv[i - 1]:.2f} ml with both valves closed",
                       f"{sweep} frames {i - 1}-{i}: {v[i - 1]:.1f} -> {v[i]:.1f} ml",
                       "hold volume constant during isovolumetric contraction/relaxation", time=f"frame {i}")]
    return []


def volume_flow_direction(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """Filling (mitral open) can't shrink the ventricle; ejection (aortic open) can't grow it."""
    tol = float(law.params.get("tol_ml", 0.5))
    for sweep, tr in trs.items():
        v, mo, av = _get(tr, "lv_volume_ml", "mitral_open", "aortic_open")
        if v is None or mo is None or av is None:
            return _missing(ids, fig, law, ["lv_volume_ml", "mitral_open", "aortic_open"])
        dv = np.diff(v)
        fill = (mo[1:] > 0.5) & (mo[:-1] > 0.5)
        eject = (av[1:] > 0.5) & (av[:-1] > 0.5)
        bad = np.flatnonzero((fill & (dv < -tol)) | (eject & (dv > tol)))
        if len(bad):
            i = int(bad[0]) + 1
            return [_f(ids, fig, law, "high", "volume moves the wrong way for the open valve",
                       f"{sweep} frame {i}: dV={dv[i - 1]:+.2f} ml, mitral_open={int(mo[i])}, aortic_open={int(av[i])}",
                       "blood enters through the mitral valve and leaves through the aortic valve", time=f"frame {i}")]
    return []


def stroke_volume(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """SV = EDV - ESV, CO = HR x SV, and LV volume stays within [ESV, EDV]."""
    tol = float(law.params.get("tolerance", 0.02))
    checked = 0
    for sweep, tr in trs.items():
        sv, edv, esv, hr, co, v = _get(tr, "stroke_volume_ml", "edv_ml", "esv_ml", "heart_rate_bpm",
                                       "cardiac_output_l_min", "lv_volume_ml")
        if sv is None or edv is None or esv is None:
            continue
        checked += 1
        err = np.abs(sv - (edv - esv)) / np.maximum(np.abs(edv - esv), 1e-9)
        if np.max(err) > tol:
            i = int(np.argmax(err))
            return [_f(ids, fig, law, "high", "stroke volume != EDV - ESV",
                       f"{sweep} frame {i}: SV={sv[i]:.1f}, EDV={edv[i]:.1f}, ESV={esv[i]:.1f} ml",
                       "compute SV = EDV - ESV")]
        if hr is not None and co is not None:
            exp = hr * sv / 1000.0
            err = np.abs(co - exp) / np.maximum(exp, 1e-9)
            if np.max(err) > tol:
                i = int(np.argmax(err))
                return [_f(ids, fig, law, "high", "cardiac output != heart rate x stroke volume",
                           f"{sweep} frame {i}: CO={co[i]:.2f} L/min, HR={hr[i]:.0f} bpm, SV={sv[i]:.1f} ml (expected {exp[i]:.2f})",
                           "CO [L/min] = HR [bpm] x SV [ml] / 1000")]
        if v is not None:
            out = np.flatnonzero((v < esv - 1.0) | (v > edv + 1.0))
            if len(out):
                i = int(out[0])
                return [_f(ids, fig, law, "high", "ventricular volume outside [ESV, EDV]",
                           f"{sweep} frame {i}: V={v[i]:.1f}, ESV={esv[i]:.1f}, EDV={edv[i]:.1f} ml",
                           "volume should swing between end-systolic and end-diastolic volume")]
    if not checked:
        return _missing(ids, fig, law, ["stroke_volume_ml", "edv_ml", "esv_ml"])
    return []


def cycle_period(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    """Beats per minute measured from mitral valve openings matches heart_rate_bpm."""
    tol = float(law.params.get("tolerance", 0.10))
    tr = trs.get("default")
    if tr is None:
        return []
    mo, hr, tp = _get(tr, "mitral_open", "heart_rate_bpm", "t_phys_s")
    if mo is None or hr is None:
        return _missing(ids, fig, law, ["mitral_open", "heart_rate_bpm"])
    t = tp if tp is not None else np.asarray([fr["t"] for fr in tr.frames], dtype=float)
    opens = np.flatnonzero((mo[1:] > 0.5) & (mo[:-1] < 0.5)) + 1
    if len(opens) < 2:
        return []   # clip shorter than two beats: can't measure
    measured = 60.0 / float(np.mean(np.diff(t[opens])))
    want = float(np.median(hr))
    if abs(measured - want) / want > tol:
        return [_f(ids, fig, law, "high", f"beats come every {60 / measured:.2f} s ({measured:.0f} bpm), "
                   f"but heart_rate_bpm is {want:.0f}", f"default sweep: {len(opens)} mitral openings",
                   "the cycle period must be 60 / heart rate")]
    return []


CHECKS = {f.__name__: f for f in (valve_pressure_logic, valves_not_both_open, isovolumetric_volume,
                                  volume_flow_direction, stroke_volume, cycle_period)}
