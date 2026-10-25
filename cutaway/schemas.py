"""Pydantic schemas shared across the pipeline."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source: str          # e.g. "S1", "V2"
    where: str           # "p.4", "fig.2", "07:42"


class Entity(BaseModel):
    id: str
    kind: str
    description: str
    params: dict
    color: str
    cite: list[Citation]


class Law(BaseModel):
    id: str
    text: str
    check: str
    params: dict
    cite: list[Citation]


class VideoFact(BaseModel):
    id: str
    text: str
    source: str
    time: str
    kind: str


class FigureSpec(BaseModel):
    id: str
    section: str
    teaches: str
    entities: list[str]
    controls: list[dict]     # {name, label, min, max, default, unit}
    probes: list[str]
    laws: list[str]
    compare_with: list[str]
    cite: list[Citation] = Field(default_factory=list)


class Spec(BaseModel):
    topic: str
    audience: str
    entities: list[Entity]
    laws: list[Law]
    video_facts: list[VideoFact]
    figures: list[FigureSpec]
    sources: list[dict]      # {id, type, title, url_or_file}
    conflicts: list[dict]    # {fact, values, chosen, why}


class Finding(BaseModel):
    id: str
    layer: Literal["fidelity", "law", "interaction", "watched", "contract"]
    severity: Literal["high", "medium", "low"]
    figure: str | None = None
    time: str | None = None
    what: str
    evidence: str
    fix_hint: str


class BuildOutput(BaseModel):
    files: dict[str, str]


class Trace(BaseModel):
    figure: str
    sweep: str               # "default" or control name
    frames: list[dict]       # getState() per frame
    frame_ms: list[float] = Field(default_factory=list)
    console: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    run_id: str
    topic: dict
    flags: dict = Field(default_factory=dict)
    research: dict = Field(default_factory=dict)   # job name -> output (dict)
    plan: dict | None = None
    spec: Spec | None = None
    version: int = 0
    versions: dict[int, dict] = Field(default_factory=dict)  # n -> {dir, findings_path, score}
    findings: list[Finding] = Field(default_factory=list)
    best_version: int | None = None
    stage: str = "init"
