Write a complete, original, interactive 3D explainer web page about this topic, in one pass.

Topic: {{title}}
Brief: {{prompt}}
Audience: {{audience}}

Use exactly these figure ids, controls, entity ids (with color names) and probes, so an automated harness can drive the page:
{{skeleton}}

## Style rules
{{style}}

## Page contract (must be satisfied exactly)
{{contract}}

## Files to produce
index.html, style.css, model.js, parts.js, figures/<id>.js for every figure, contract.js. Classic scripts, Three.js r128 from cdnjs only.

Output a single JSON object, no prose, no code fences:
{"plan": "...", "files": {"index.html": "...", ...}}
