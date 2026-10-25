"""Findings per version by layer, weighted scores, and the baselines table."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config
from .schemas import Finding

WEIGHT = {"high": 5, "medium": 2, "low": 1}
LAYERS = ["contract", "law", "interaction", "fidelity", "watched"]   # citation is scored separately (spec stage)


def weighted(findings: list[Finding]) -> int:
    return sum(WEIGHT[f.severity] for f in findings if f.layer != "citation")


def high_count(findings: list[Finding]) -> int:
    return sum(f.severity == "high" for f in findings if f.layer != "citation")


def load_findings(path: Path) -> list[Finding]:
    return [Finding.model_validate(f) for f in json.loads(path.read_text())]


def by_layer(findings: list[Finding]) -> dict[str, dict[str, int]]:
    out = {l: {"high": 0, "medium": 0, "low": 0} for l in LAYERS}
    for f in findings:
        if f.layer in out:
            out[f.layer][f.severity] += 1
    return out


def version_table(run_dir: Path) -> list[dict]:
    rows = []
    for p in sorted((run_dir / "findings").glob("v*.json"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0):
        if not p.stem[1:].isdigit():
            continue
        fs = load_findings(p)
        rows.append({"version": p.stem, "layers": by_layer(fs), "total": len(fs), "high": high_count(fs),
                     "weighted": weighted(fs)})
    return rows


def format_table(rows: list[dict], label: str = "version") -> str:
    head = f"{label:<10}" + "".join(f"{l:>14}" for l in LAYERS) + f"{'total':>8}{'high':>6}{'weighted':>10}"
    lines = [head, "-" * len(head)]
    for r in rows:
        cells = "".join(f"{sum(r['layers'][l].values())} ({r['layers'][l]['high']}H)".rjust(14) for l in LAYERS)
        lines.append(f"{r.get('version', r.get('name', '')):<10}{cells}{r['total']:>8}{r['high']:>6}{r['weighted']:>10}")
    lines.append("cells: findings in layer (high-severity count); weighted = 5*high + 2*medium + 1*low")
    return "\n".join(lines)


if __name__ == "__main__":
    run = config.RUNS_DIR / sys.argv[1]
    print(format_table(version_table(run)))
