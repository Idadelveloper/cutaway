"""Pydantic schemas shared across the pipeline."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source: str          # e.g. "P1", "V2"
    where: str           # "p.4", "fig.2", "07:42"
    quote: str           # verbatim snippet (<= 20 words) that must appear in the stored source text


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
    layer: Literal["fidelity", "law", "interaction", "watched", "contract", "citation"]
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
    sources: list[dict] = Field(default_factory=list)   # Source dicts
    research: dict = Field(default_factory=dict)   # job name -> output (dict)
    plan: dict | None = None
    spec: Spec | None = None
    version: int = 0
    versions: dict[int, dict] = Field(default_factory=dict)  # n -> {dir, findings_path, score}
    findings: list[Finding] = Field(default_factory=list)
    best_version: int | None = None
    stage: str = "init"


# ---------- research ----------

class ResearchItem(BaseModel):
    id: str
    kind: str
    text: str
    where: str
    value: float | None = None
    unit: str | None = None
    source: str | None = None    # set when one job covers several sources


class ResearchOutput(BaseModel):
    source: str
    title: str
    summary: str = ""
    items: list[ResearchItem]
    duration_s: float | None = None     # videos: length watched
    transcript: str | None = None       # videos: verbatim speech, stored as the source text


class ResearchPlan(BaseModel):
    questions: list[str] = Field(default_factory=list)
    focus: dict[str, str] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)


SourceType = Literal["paper", "patent", "standard", "manufacturer", "article", "blog", "video", "diagram", "web"]

# Lower is better. Primary/technical sources first.
SOURCE_RANK = {"paper": 1, "patent": 1, "standard": 1, "manufacturer": 1, "diagram": 1,
               "article": 2, "video": 2, "web": 3, "blog": 3}
SOURCE_PREFIX = {"paper": "P", "patent": "T", "standard": "S", "manufacturer": "M", "article": "A",
                 "blog": "B", "video": "V", "diagram": "D", "web": "W"}


class Source(BaseModel):
    id: str = ""         # assigned after merge: P1, T1, S1, M1, A1, B1, V1, D1, W1
    type: SourceType
    title: str
    url_or_file: str
    local: bool = False  # True if url_or_file is a local path
    date: str | None = None
    publisher: str | None = None
    found_by: list[str] = Field(default_factory=list)   # seed | search | deep_research | scout
    rank: int = 3
    status: str = "pending"   # ok | skipped
    skip_reason: str | None = None
    chars: int = 0
    text_path: str | None = None


class DiscoveredSource(BaseModel):
    title: str
    url: str
    type: SourceType = "web"
    date: str | None = None
    publisher: str | None = None
    why: str | None = None
    text: str | None = None   # SourceScout returns extracted text


class DiscoveryOutput(BaseModel):
    sources: list[DiscoveredSource] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)   # {url, reason}
