# Roadmap

Milestones follow the brief: prove output quality first, then voice, then colour.

## v1, generate and compare (built 2026-11-05)

Done:
- Upload (JPEG, PNG, HEIC), one settings page with live preview: crop, zoom, rotate, brightness,
  contrast, gamma, levels, CLAHE, channel mix, edge emphasis, tone presets, painted importance mask,
  viewing-distance preview, optional background removal (rembg extra).
- Board configuration with mm and inch display, presets.
- Engine A (greedy with error scoring, bridge, refinement) and engine B (chord set, prune, parity
  repair, Euler path), Run both, side-by-side comparison with error, lines, thread, time, repair cost.
- Exports: tiled or single-sheet true-size pin template PDF, SVG and DXF fab files (holes or notched
  ring), sequence TXT/CSV/JSON, render and comparison pictures, project bundle export and import.
- Step player (build mode v1) with browser voice, auto-advance, clicker keys, wake lock, server-side
  progress with local fallback.
- Docker image, compose file, media PC deploy script, Homepage and Gatus snippets, tests.

Not done in v1:
- Square and rectangular frames.
- Printable thread-width test pattern.
- A proper minimum-weight matching in engine B's repair (greedy today).

## Pilot build

One physical 24 inch piece from a v1 plan to calibrate: thread width and `thread_opacity`, the
viewing-distance blur, the light-protection default, and how the rim pile-up really looks. Record the
results in DECISIONS.md and adjust defaults.

## v2, voice and portraits (built 2026-11-05)

Done:
- Piper in its own container (compose profile `voice`), three voices baked in, clip cache under
  `data/voice`, pre-rendering of every pin phrase when voice is switched on, voice and speed choice
  in build mode, browser speech as the fallback (ADR-013).
- Bluetooth clicker mapping in build mode (press the clicker's buttons once; stored per device),
  optional background music from a local file, ducked under the voice.
- Portraits: local face detection, "Frame the face", automatic face emphasis, structure weight,
  "Show importance" overlay (ADR-011); engine A regrows after refinement (ADR-012).

Not done in v2:
- Colour-change pauses (they belong with colour, v3).
- A separate clicker settings page; the mapping lives in the build-mode bar.

## v3, colour

Palette presets (CMY plus black), custom palettes, auto palette by k-means always including black;
one coverage layer per colour composited in build order; CIELAB error after the blur; one continuous
thread per colour sharing the line budget; lightest first, black last on a light board. The engine
state is already palette-agnostic in the sense that coverage, weights and gradient are per layer;
the scoring loop needs a per-colour `d` and a Lab comparison.
