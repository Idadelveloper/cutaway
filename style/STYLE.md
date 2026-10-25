# Cutaway explainer style

Reproduce the *qualities* of a great long-form interactive engineering article, never its content. All prose is original; every fact comes from the spec and is cited.

## Structure
- **Hero figure** at the top: the whole object (here: the headphone ear cup). Drag to orbit. A slider peels it open into a cutaway and exploded view. Play/pause.
- **8–12 figures, one idea each**, in the spec's order. Each figure builds on the previous one and introduces only what its `teaches` line says.
- Short paragraphs before and after every figure: what to look at, what to try ("drag the delay slider to the right and watch the purple wave grow back").
- **Every figure has at least one control that changes the physics** (frequency, delay, amplitude, mic on/off, mode), not only the camera.
- A **Sources** section at the end listing every cited source with its id, title and link. Inline citations as small superscript ids (e.g. `[P1]`) linking to that list.

## Color-linked words
- When prose names an entity, wrap it: `<span class="ent" data-entity="outer_mic" data-figure="F5">outer microphone</span>`.
- The span's text color is the entity's palette color. Hovering it calls `__cutaway.figure(fig).highlight(entityId)` for the nearest figure that shows the entity; leaving it clears the highlight (`highlight(null)`).
- The same color is used for that entity everywhere: 3D material, plot line, legend, label.

## Palette (spec color name → hex)
| name | hex | use |
|---|---|---|
| grey | #c9ccd1 | passive parts (shell, headband, cushions) |
| ink | #22252a | outlines, text, axes |
| noise | #e0533d | incoming noise wave |
| anti | #2f7de1 | anti-noise wave |
| total | #7b4fd6 | what reaches the ear (sum) |
| mic | #1fa37a | microphones |
| chip | #e3a21a | processing chip |
| speaker | #d64f9a | speaker / driver |
| air | #8fb8de | air particles |
| padding | #9c7a5b | passive padding / foam |

## Illustration
- White background. Light grey passive parts, **dark outlines** on every mesh (`THREE.EdgesGeometry` + `LineBasicMaterial` in ink), flat/toon shading (`MeshToonMaterial` or `MeshLambertMaterial` with `flatShading`), one accent color per discussed entity; everything else stays grey.
- Geometry is built in code from spec params (lathe/cylinder/torus/box), no external model files or textures.
- Waves in 3D: tubes or line strips; 2D plots (pressure vs time at the ear) drawn on a 2D canvas under the 3D view, same colors, with a zero line and labeled axes.
- Air particles: small spheres in a grid that move back and forth along the wave direction around fixed rest positions.

## Typography and layout
- Text column max ~680px, centered; figures slightly wider (up to ~880px). Generous whitespace (figures have ~48px vertical margin).
- Body: system sans-serif (`-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, sans-serif`), 18px, line-height 1.6, color ink.
- Controls under each figure: labeled range sliders with live value + unit, toggle buttons for on/off, a per-figure play/pause button. Controls are plain HTML, large enough to use on a phone.
- A global "pause all animations" link fixed in a corner.

## Performance
- **One shared WebGL renderer** (`preserveDrawingBuffer: true`) on a fixed full-window canvas behind the page; each visible figure draws into its own viewport/scissor rect matching its placeholder element.
- `IntersectionObserver`: only on-screen figures animate.
- The animation loop for humans uses the same model function as `advance(dt)`; nothing physical is computed anywhere else.
- Three.js from cdnjs at a pinned version: `https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js` (global `THREE`). No other external scripts. Orbit dragging is implemented by hand (pointer events → spherical camera angles); do not load OrbitControls.
