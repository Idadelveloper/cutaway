"""Paths, model IDs, flags and topic loading."""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RUNS_DIR = ROOT / "runs"
TOPICS_DIR = ROOT / "topics"
SOURCES_DIR = ROOT / "sources"
PROMPTS_DIR = ROOT / "cutaway" / "prompts"
FIXTURES_DIR = Path(os.environ.get("CUTAWAY_FIXTURES", ROOT / "fixtures"))
STYLE_PATH = ROOT / "style" / "STYLE.md"
CONTRACT_PATH = ROOT / "cutaway" / "harness" / "contract.md"

# Models and agents
ARGON = "gemini-4-argon"
FLASH = "gemini-3.8-flash"
DEEP_RESEARCH = "deep-research-preview-04-2026"
ANTIGRAVITY = "antigravity-preview-09-2026"

# Timeouts (seconds)
TIMEOUT_DEFAULT = 300
TIMEOUT_HIGH = 20 * 60
POLL_SECONDS = 5
LONG_OUTPUT_TOKENS = 262144

FAKE = os.environ.get("CUTAWAY_FAKE") == "1"


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY")


def load_topic(name: str) -> dict:
    path = TOPICS_DIR / name / "topic.yaml"
    with open(path) as f:
        topic = yaml.safe_load(f)
    topic.setdefault("name", name)
    topic["sources_dir"] = str(SOURCES_DIR / name)
    return topic


def load_prompt(name: str, /, **values) -> str:
    """Load prompts/<name>.md and fill {{key}} placeholders."""
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text

# Shared palette (names used in spec entity.color; hex used by STYLE.md and the builder)
PALETTE = {
    "grey": "#c9ccd1",      # passive parts
    "ink": "#22252a",       # outlines, text
    "noise": "#e0533d",     # incoming noise (red-orange)
    "anti": "#2f7de1",      # anti-noise (blue)
    "total": "#7b4fd6",     # what reaches the ear (violet)
    "mic": "#1fa37a",       # microphones (green)
    "chip": "#e3a21a",      # processing chip (amber)
    "speaker": "#d64f9a",   # speaker/driver (magenta)
    "air": "#8fb8de",       # air particles (light blue)
    "padding": "#9c7a5b",   # passive padding (brown)
}
