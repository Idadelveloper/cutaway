PY := .venv/bin/python
TOPIC ?= anc

smoke:
	$(PY) scripts/smoke_api.py
research:
	$(PY) scripts/run_topic.py $(TOPIC) --until spec
research-fresh:
	$(PY) scripts/run_topic.py $(TOPIC) --until spec --fresh-research
build:
	$(PY) scripts/run_topic.py $(TOPIC) --until build
loop:
	$(PY) scripts/run_topic.py $(TOPIC)
serve:
	$(PY) -m uvicorn server.main:app --port 8000 --reload
baselines:
	$(PY) scripts/baselines.py $(TOPIC)
test:
	$(PY) -m pytest -q tests

.PHONY: smoke research research-fresh build loop serve baselines test
