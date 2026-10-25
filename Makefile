PY := .venv/bin/python
TOPIC ?= anc

smoke:
	$(PY) scripts/smoke_api.py
research:
	$(PY) scripts/run_topic.py $(TOPIC) --until spec $(ARGS)
research-fresh:
	$(PY) scripts/run_topic.py $(TOPIC) --until spec --fresh-research
build:
	$(PY) scripts/run_topic.py $(TOPIC) --until build $(ARGS)
render:
	$(PY) scripts/run_topic.py $(TOPIC) --until review $(ARGS)
loop:
	$(PY) scripts/run_topic.py $(TOPIC) --until loop $(ARGS)
all:
	$(PY) scripts/run_topic.py $(TOPIC) --until publish $(ARGS)
score:
	$(PY) -m cutaway.scoring $(RUN)
serve:
	$(PY) -m uvicorn server.main:app --port 8000 --reload --reload-dir cutaway --reload-dir server --reload-dir web
baselines:
	$(PY) scripts/baselines.py $(TOPIC)
test:
	$(PY) -m pytest -q tests

.PHONY: smoke research research-fresh build render all score loop serve baselines test
