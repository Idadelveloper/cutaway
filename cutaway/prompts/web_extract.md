Convert the research report below into structured items. Each URL cited in the report becomes a source with id W1, W2, ... Keep only facts the report attributes to a URL.

Report:
{{report}}

Output JSON:
{
  "source": "W",
  "title": "Deep Research report",
  "summary": "...",
  "sources": [{"id": "W1", "title": "...", "url": "..."}],
  "items": [{"id": "W1-1", "kind": "number|claim|equation|entity", "text": "...", "where": "url section or quote location", "value": null, "unit": null, "source": "W1"}]
}
Output a single JSON object only.
