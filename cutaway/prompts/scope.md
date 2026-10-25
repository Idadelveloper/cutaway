You route a change request for a generated interactive explainer to the smallest set of figures and files that must change.

Change request: {{request}}

Figures (id, section, controls, entities):
{{figures}}

Files in the page: {{files}}
(model.js = shared physics model used by every figure; parts.js = shared renderer/helpers; figures/<id>.js = one figure; index.html = prose, controls markup; style.css = styles.)

Rules: pick only figures whose behavior or appearance the request changes. If the change is in model.js or parts.js, list every figure that visibly depends on it, but at most 4 (the most affected). Prefer editing a figure file over shared files.

Output a single JSON object only:
{"figures": ["F6"], "files": ["figures/F6.js"], "why": "one line"}
