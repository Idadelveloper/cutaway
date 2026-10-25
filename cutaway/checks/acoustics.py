"""ANC law checks on rendered traces. Each check: (traces_by_sweep, law, figure_id, ids) -> findings.

Traces come from the render harness: per figure, a "default" sweep (controls fixed, time advancing)
and one sweep per control (control min->max while time advances). Probes used:
p_noise_ear, p_anti_ear, p_total_ear, frequency_hz, wavelength_m, delay_s, residual_ratio,
particle_disp_max, particle_mean_offset.
"""
from __future__ import annotations

import numpy as np

from ..schemas import Finding, Law, Trace
from .common import Ids

ZERO_DELAY = 1e-9


def series(tr: Trace, probe: str) -> np.ndarray | None:
    vals = [(fr.get("probes") or {}).get(probe) if fr else None for fr in tr.frames]
    if any(v is None or isinstance(v, list) for v in vals):
        return None
    return np.asarray(vals, dtype=float)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def _f(ids: Ids, fig: str, law: Law, sev: str, what: str, evidence: str, hint: str, time: str | None = None) -> Finding:
    return Finding(id=ids(), layer="law", severity=sev, figure=fig, time=time,
                   what=f"[{law.id} {law.check}] {what}", evidence=evidence, fix_hint=hint)


def _missing(ids, fig, law, probes) -> list[Finding]:
    return [_f(ids, fig, law, "medium", f"cannot check: probes {probes} not exposed",
               "", f"expose {probes} in getState().probes")]


def wave_speed(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    c = float(law.params.get("speed_of_sound_m_s", 343))
    tol = float(law.params.get("tolerance", 0.02))
    checked, worst = 0, (0.0, None)
    for sweep, tr in trs.items():
        f, lam = series(tr, "frequency_hz"), series(tr, "wavelength_m")
        if f is None or lam is None:
            continue
        err = np.abs(f * lam - c) / c
        checked += len(err)
        i = int(np.argmax(err))
        if err[i] > worst[0]:
            worst = (float(err[i]), f"{sweep} frame {i}: f={f[i]:.4g} Hz, wavelength={lam[i]:.4g} m, f*wavelength={f[i] * lam[i]:.4g} m/s")
    if not checked:
        return _missing(ids, fig, law, ["frequency_hz", "wavelength_m"])
    if worst[0] > tol:
        return [_f(ids, fig, law, "high", f"f x wavelength off by {worst[0]:.1%} (tolerance {tol:.0%})",
                   worst[1], f"wavelength_m must be {c}/frequency_hz")]
    return []


def superposition_add(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    tol = float(law.params.get("rel_tolerance", 1e-6))
    for sweep, tr in trs.items():
        n, a, t = (series(tr, p) for p in ("p_noise_ear", "p_anti_ear", "p_total_ear"))
        if n is None or a is None or t is None:
            return _missing(ids, fig, law, ["p_noise_ear", "p_anti_ear", "p_total_ear"])
        amp = max(float(np.max(np.abs(n))), 1e-12)
        err = np.abs(t - (n + a)) / amp
        i = int(np.argmax(err))
        if err[i] > tol:
            return [_f(ids, fig, law, "high", f"p_total_ear != p_noise_ear + p_anti_ear (rel err {err[i]:.2e})",
                       f"{sweep} frame {i}: noise={n[i]:.6g} anti={a[i]:.6g} total={t[i]:.6g}",
                       "compute p_total_ear as the exact sum", time=f"frame {i}")]
    return []


def _anc_on(tr: Trace) -> bool:
    a = series(tr, "p_anti_ear")
    return a is not None and float(np.max(np.abs(a))) > 0


def superposition_cancel(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    max_ratio = float(law.params.get("max_ratio", 0.05))
    cutoff = float(law.params.get("cutoff_hz", 1000))
    tr = trs.get("default")
    if tr is None:
        return []
    n, t, d, f = (series(tr, p) for p in ("p_noise_ear", "p_total_ear", "delay_s", "frequency_hz"))
    if n is None or t is None:
        return _missing(ids, fig, law, ["p_noise_ear", "p_total_ear"])
    if not _anc_on(tr) or (d is not None and np.any(np.abs(d) > ZERO_DELAY)) or (f is not None and np.any(f >= cutoff)):
        # default isn't the zero-delay/ANC-on/low-f case: look for those frames in a delay sweep
        for sweep, s in trs.items():
            rr, d2, f2 = series(s, "residual_ratio"), series(s, "delay_s"), series(s, "frequency_hz")
            if rr is None or d2 is None or not _anc_on(s):
                continue
            mask = np.abs(d2) <= ZERO_DELAY
            if f2 is not None:
                mask &= f2 < cutoff
            if mask.any() and float(np.max(rr[mask])) > max_ratio:
                i = int(np.flatnonzero(mask)[np.argmax(rr[mask])])
                return [_f(ids, fig, law, "high", f"residual {rr[i]:.3f} > {max_ratio} at zero delay",
                           f"{sweep} frame {i}", "anti-noise must be the exact inverse at zero delay")]
        return []
    ratio = rms(t) / max(rms(n), 1e-12)
    if ratio > max_ratio:
        return [_f(ids, fig, law, "high", f"RMS(total)/RMS(noise) = {ratio:.3f} > {max_ratio} with zero delay, ANC on",
                   f"default sweep, {len(t)} frames", "anti-noise must be the exact inverse at zero delay")]
    return []


def delay_residual(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    tol = float(law.params.get("tolerance", 0.10))
    checked = 0
    for sweep, tr in trs.items():
        d, f, rr = series(tr, "delay_s"), series(tr, "frequency_hz"), series(tr, "residual_ratio")
        if d is None or f is None or rr is None or not _anc_on(tr):
            continue
        if np.ptp(d) == 0 and np.ptp(f) == 0 and sweep != "default":
            continue
        expected = 2 * np.abs(np.sin(np.pi * f * d))
        err = np.abs(rr - expected) / np.maximum(expected, 0.05)
        checked += len(err)
        bad = np.flatnonzero(err > tol)
        if len(bad) > max(2, len(err) // 20):
            i = int(bad[np.argmax(err[bad])])
            return [_f(ids, fig, law, "high",
                       f"residual/noise deviates from 2|sin(pi f tau)| by up to {err[i]:.0%} in {len(bad)}/{len(err)} frames",
                       f"{sweep} frame {i}: f={f[i]:.4g} Hz, tau={d[i]:.4g} s, measured {rr[i]:.4f}, expected {expected[i]:.4f}",
                       "anti-noise = -noise delayed by tau; residual_ratio from the same model", time=f"frame {i}")]
    if not checked:
        return _missing(ids, fig, law, ["delay_s", "frequency_hz", "residual_ratio"])
    return []


def phase_flip(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    max_corr = float(law.params.get("max_corr", -0.95))
    tr = trs.get("default")
    if tr is None:
        return []
    n, a, d = (series(tr, p) for p in ("p_noise_ear", "p_anti_ear", "delay_s"))
    if n is None or a is None:
        return _missing(ids, fig, law, ["p_noise_ear", "p_anti_ear"])
    if d is not None and np.any(np.abs(d) > ZERO_DELAY):
        return []  # default delay isn't zero; covered by delay_residual
    if np.std(n) < 1e-12 or np.std(a) < 1e-12:
        return [_f(ids, fig, law, "medium", "noise or anti-noise is constant; can't measure phase",
                   "default sweep", "animate the waves over time")]
    corr = float(np.corrcoef(n, a)[0, 1])
    if corr > max_corr:
        return [_f(ids, fig, law, "high", f"corr(anti, noise) = {corr:.3f} > {max_corr} at zero delay",
                   "default sweep", "anti-noise must be the inverted noise")]
    return []


def particles_oscillate(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    max_disp = float(law.params.get("max_disp", 1.0))
    max_drift = float(law.params.get("max_drift", 0.05))
    out = []
    for sweep, tr in trs.items():
        dm, mo = series(tr, "particle_disp_max"), series(tr, "particle_mean_offset")
        if dm is None:
            return _missing(ids, fig, law, ["particle_disp_max", "particle_mean_offset"])
        if float(np.max(dm)) > max_disp * 1.0001:
            i = int(np.argmax(dm))
            out.append(_f(ids, fig, law, "high", f"particle displacement {dm[i]:.3g} exceeds max {max_disp}",
                          f"{sweep} frame {i}", "bound displacement by d_max", time=f"frame {i}"))
            break
        if sweep == "default":
            if np.max(dm) < 1e-9:
                out.append(_f(ids, fig, law, "high", "particles don't move", "default sweep", "animate particles"))
            if mo is not None and abs(float(np.mean(mo))) > max_drift * max_disp:
                out.append(_f(ids, fig, law, "high", f"particles drift: mean offset {np.mean(mo):.3g}",
                              "default sweep", "particles oscillate about fixed rest positions; no net travel"))
    return out


def controls_affect_probes(trs: dict[str, Trace], law: Law, fig: str, ids: Ids) -> list[Finding]:
    base = trs.get("default")
    if base is None:
        return []
    out = []
    probes = set((base.frames[0] or {}).get("probes", {}))
    for sweep, tr in trs.items():
        if sweep == "default":
            continue
        changed = []
        for p in probes:
            a, b = series(base, p), series(tr, p)
            if a is not None and b is not None and len(a) == len(b) and float(np.max(np.abs(a - b))) > 1e-9:
                changed.append(p)
        if not changed:
            out.append(_f(ids, fig, law, "high", f"control '{sweep}' changes no probe",
                          f"compared {sorted(probes)} against the default sweep frame by frame",
                          "make the control change the physics in model.js"))
    return out


CHECKS = {f.__name__: f for f in (wave_speed, superposition_add, superposition_cancel, delay_residual,
                                  phase_flip, particles_oscillate, controls_affect_probes)}
