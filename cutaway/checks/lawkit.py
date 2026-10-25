"""Shared helpers for topic law checks: probe series, law findings, and the generic
controls_affect_probes check. Each topic's check module registers CHECKS = {name: fn};
fn(traces_by_sweep, law, figure_id, ids) -> list[Finding]."""
from __future__ import annotations

import numpy as np

from ..schemas import Finding, Law, Trace
from .common import Ids


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
