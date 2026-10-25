"""All law checks by name, across topics. A topic's spec may only use names listed in its topic.yaml."""
from __future__ import annotations

from . import acoustics, cardio, trees
from .lawkit import controls_affect_probes

CHECKS = {**acoustics.CHECKS, **cardio.CHECKS, **trees.CHECKS, "controls_affect_probes": controls_affect_probes}
