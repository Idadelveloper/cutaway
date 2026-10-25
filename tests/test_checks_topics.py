"""Heart and BST law checks: one passing and one failing synthetic trace each."""
from __future__ import annotations

import pytest

from cutaway.checks import cardio, trees
from cutaway.checks.common import Ids
from cutaway.schemas import Law, Trace

N, DT = 120, 1 / 30


def heart_frames(hr=72.0, hr_probe=None, sv_bias=0.0, co_bias=0.0, leak=0.0, wrong_valve=False, both_open=False):
    """Left heart over 4 s: filling -> isovolumetric contraction -> ejection -> isovolumetric relaxation."""
    T, edv, esv = 60.0 / hr, 120.0, 50.0
    out = []
    for i in range(N):
        t = (i + 1) * DT
        ph = (t % T) / T
        la, ao = 10.0, 90.0
        if ph < 0.5:                       # filling: mitral open, LV pressure below atrium
            v, lv, mo, av = esv + (edv - esv) * ph / 0.5, 5.0, 1, 0
        elif ph < 0.55:                    # isovolumetric contraction
            v, lv, mo, av = edv, 60.0, 0, 0
        elif ph < 0.85:                    # ejection: aortic open, LV above aorta
            v, lv, mo, av = edv - (edv - esv) * (ph - 0.55) / 0.3, 120.0, 0, 1
            ao = 118.0
        else:                              # isovolumetric relaxation
            v, lv, mo, av = esv + leak * (ph - 0.85) * 100, 40.0, 0, 0   # relaxation spans ~4 frames
        if wrong_valve and 0.55 <= ph < 0.85:
            mo = 1                         # mitral open while LV pressure (120) >> atrium (10)
        if both_open and 0.55 <= ph < 0.85:
            mo = 1
        sv = edv - esv + sv_bias
        hrp = hr_probe or hr
        probes = {"heart_rate_bpm": hrp, "lv_volume_ml": v, "lv_pressure_mmhg": lv, "la_pressure_mmhg": la,
                  "aortic_pressure_mmhg": ao, "mitral_open": mo, "aortic_open": av, "stroke_volume_ml": sv,
                  "edv_ml": edv, "esv_ml": esv, "cardiac_output_l_min": hrp * sv / 1000 + co_bias, "t_phys_s": t}
        out.append({"t": t, "controls": {}, "probes": probes, "entities": {}})
    return out


KEYS, LEFT, RIGHT = [50, 30, 70, 20, 40], [1, 3, -1, -1, -1], [2, 4, -1, -1, -1]


def tree_frames(keys=KEYS, left=LEFT, right=RIGHT, height=3, inorder=None, path=(50, 30, 40), key=40,
                found=1, balanced=0, count=None):
    probes = {"node_keys": list(keys), "left_index": list(left), "right_index": list(right), "root_index": 0,
              "inorder_keys": inorder if inorder is not None else sorted(keys), "tree_height": height,
              "node_count": count if count is not None else len(keys), "search_key": key,
              "search_path": list(path), "search_found": found, "balanced": balanced}
    return [{"t": (i + 1) * DT, "controls": {}, "probes": probes, "entities": {}} for i in range(N)]


def tr(frames) -> dict[str, Trace]:
    return {"default": Trace(figure="F1", sweep="default", frames=frames)}


def run(mod, check, trs, **params):
    law = Law(id="L1", text="", check=check, params=params, cite=[{"source": "P1", "where": "p.1", "quote": "q"}])
    return mod.CHECKS[check](trs, law, "F1", Ids("T"))


@pytest.mark.parametrize("check,bad", [
    ("valve_pressure_logic", dict(wrong_valve=True)),
    ("valves_not_both_open", dict(both_open=True)),
    ("isovolumetric_volume", dict(leak=1.0)),
    ("volume_flow_direction", dict(wrong_valve=True)),   # mitral "open" while volume falls
    ("stroke_volume", dict(sv_bias=12.0)),
    ("stroke_volume", dict(co_bias=1.5)),
    ("cycle_period", dict(hr_probe=100.0)),
])
def test_heart_checks(check, bad):
    assert run(cardio, check, tr(heart_frames())) == [], check
    fs = run(cardio, check, tr(heart_frames(**bad)))
    assert fs and fs[0].severity == "high" and fs[0].layer == "law", (check, fs)


@pytest.mark.parametrize("check,bad", [
    ("bst_order", dict(keys=[50, 30, 70, 20, 60])),          # 60 in 30's right subtree but > 50
    ("bst_order", dict(inorder=[20, 30, 50, 40, 70])),
    ("tree_height", dict(height=2)),
    ("tree_height", dict(count=7)),
    ("search_path", dict(path=(50, 70))),                      # went right for a smaller key
    ("search_path", dict(key=45, path=(50, 30, 40), found=1)),   # 45 isn't in the tree
    ("avl_balance", dict(keys=[10, 20, 30], left=[-1, -1, -1], right=[1, 2, -1], height=3,
                          path=(10, 20, 30), key=30, balanced=1)),
])
def test_tree_checks(check, bad):
    assert run(trees, check, tr(tree_frames(balanced=1))) == [], check
    fs = run(trees, check, tr(tree_frames(**bad)))
    assert fs and fs[0].severity == "high", (check, fs)


def test_tree_missing_probes_is_medium():
    frames = [{"t": 0.1, "controls": {}, "probes": {"node_count": 3}, "entities": {}}]
    fs = run(trees, "bst_order", tr(frames))
    assert fs and fs[0].severity == "medium" and "cannot check" in fs[0].what
