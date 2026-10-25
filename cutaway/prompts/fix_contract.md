The page you wrote failed the automated contract test. Findings:
{{findings}}

Fix the cause of every finding. Change only what's needed; keep the contract intact; return complete files for every file you change (unchanged files may be omitted).

Output a single JSON object, no prose, no code fences:
{"plan": "what you changed and why", "files": {"<path>": "<complete new content>", ...}}
