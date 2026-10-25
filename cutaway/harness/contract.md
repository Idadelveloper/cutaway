# Page contract

Every generated explainer exposes this test API. The render harness and all checks use only this API.

```js
window.__cutaway = {
  figures: ["F1", "F2", ...],
  figure(id) -> {
    controls: { [name]: { min, max, default, unit } },
    set(name, value),              // apply a control value
    reset(),                       // default controls, t = 0
    advance(dt),                   // step simulated time by dt seconds AND render one frame synchronously
    getState() -> {
      t,
      controls: { [name]: value },
      entities: { [id]: { kind, visible, color, position, ...params } },
      probes:   { [name]: number | number[] }   // measured quantities, e.g. "p_noise_ear",
                                                // "p_anti_ear", "p_total_ear", "particle_disp_max"
    },
    highlight(entityId),           // what hovering a colored word does
  },
  pauseAll(), playAll()
}
```

Rules:
- Simulated time only moves in `advance(dt)` while under test. A normal animation loop is fine for human viewers, but it must call the same model function. `pauseAll()` stops that loop completely, so only `advance` moves time.
- One shared model (`model.js`, a pure function of `(t, controls)`) is used by every figure. Probes in `getState()` are computed by the model, not read back from the drawing.
- Geometry is built in code from spec params; no external 3D files.
- Three.js from cdnjs at a pinned version (`https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js`).
- The WebGL renderer is created with `preserveDrawingBuffer: true`.
- `window.__cutaway` exists as soon as the `load` event has fired. `figures` lists exactly the spec's figure ids, in order.
- `controls` lists exactly the spec's controls for that figure (same names, min, max, default, unit). `set` clamps to [min, max].
- `getState().entities` has an entry for every entity the spec lists for that figure, with `color` = the palette color name and `visible` = whether it is currently drawn and not hidden by other geometry.
- `getState().probes` has every probe the spec lists for that figure. All numbers are finite.
- `advance(dt)` renders the figure's region synchronously, so a screenshot taken right after shows that frame. The harness scrolls the figure into view before calling it.
- Each figure's placeholder element is `<figure id="fig-F1" data-figure="F1">`, so the harness can find and screenshot it.
- No console errors.
