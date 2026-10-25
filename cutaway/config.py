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
FIXTURES_DIR = ROOT / "fixtures"
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


def load_prompt(name: str, **values) -> str:
    """Load prompts/<name>.md and fill {{key}} placeholders."""
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text
