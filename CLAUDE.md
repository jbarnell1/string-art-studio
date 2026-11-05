# CLAUDE.md: String Art Studio

Read this first every session.

## What this is
A self-hosted web app that turns a photo into a pin-and-thread build plan: an image editor and board
settings on one page with a live preview, two generation engines with side-by-side comparison, true-size
pin templates and SVG/DXF fab files, and a phone-first build mode. Python (FastAPI + numpy/OpenCV) with
a vanilla-JS PWA, one container. MIT. Public repo intended; the first install is the Barnell homelab
(media PC, desktop-4l95il9) but nothing homelab-specific is in the code.

## Canonical documents (link to these, never duplicate them)
| Topic | Source of truth |
|---|---|
| The product brief (owner's) | `docs/brief.md` |
| Why we chose X | `docs/DECISIONS.md` (ADRs, newest first) |
| Components, data flow, the objective both engines optimise | `docs/ARCHITECTURE.md` |
| What is built, what is next | `docs/ROADMAP.md` |
| Running, deploying, backing up, calibrating | `docs/RUNBOOK.md` |

## Layout
```
stringart/
  board.py        Board geometry: pins, chords, sectors, thread length (all millimetres).
  prep.py         Image prep pipeline (crop, tone, CLAHE, edges, mask, face and structure weights). Preview and engine share it.
  faces.py        Local face detection (YuNet in models/), portrait framing, automatic importance map.
  voice.py        Piper over HTTP with the on-disk clip cache and prewarm jobs; pin_phrase is the one phrase template.
  raster.py       Vectorised line sampling and the anti-aliased renderer's line.
  engine/state.py The shared objective (RenderState): target, weights, coverage, gradient image G.
  engine/greedy.py   Engine A: greedy with error scoring, two-line bridge, refinement passes, regrow.
  engine/setcover.py Engine B: chord multiset, prune, parity repair, Euler path.
  engine/render.py   Final thread-accurate render and the comparison image.
  exports/        Pin template PDF, SVG/DXF fab files, sequence TXT/CSV/JSON, project bundle ZIP.
  db.py, jobs.py  SQLite metadata; worker pool, progress, cancellation, files on disk.
  web/app.py      FastAPI routes (/api/...) and the PWA shell; web/static/ is the client.
  cli.py          `string-art-studio serve` and `string-art-studio generate`.
tests/            pytest; the API test runs a real tiny generation end to end.
scripts/          bench.py (engine benchmarks), make_icons.py (logo to icons).
deploy/           Media PC install script, Tailscale serve owner step, Homepage and Gatus snippets, piper/ container.
```

## Working agreement
1. Decisions go into `docs/DECISIONS.md` as a new ADR at the top; other docs link to the ADR.
2. Tests ship with the code: `.venv/Scripts/python.exe -m pytest -q` must pass. Engine changes get a
   `scripts/bench.py` run on `samples/portrait-man.jpg` and the numbers go in the PR or commit body.
3. The prep pipeline is one function for preview and generation (`prep.prepare_target`); never let the
   preview and the engine see different images.
4. Settings, not constants: anything a household would change (board, thread, engine knobs) is a field on
   `BoardConfig`, `PrepSettings` or `EngineParams` with a default and validation.
5. The app runs at the root and learns its external prefix from `--root-path` / `SAS_ROOT_PATH`; the
   client only uses relative URLs. Never hard-code a path prefix.
6. Prose style: no em dashes in docs, PRs, commits, comments or UI copy. Plain, specific language.

## Environment notes
- Windows dev box: `.venv` (uv, Python 3.11). Run `uv pip install --python .venv/Scripts/python.exe -e ".[dev]"`
  after dependency changes. The Bash tool mangles backslashes in heredocs: write files with the Write
  tool or use forward slashes.
- Deployment target: media PC WSL distro `homelab`, Docker Compose, `/mnt/c/homelab/string-art-studio`,
  loopback 8050, Tailscale serve path `/stringart` (owner step). See `docs/RUNBOOK.md`.
- Sample photos in `samples/` are AI-generated fictional people and a dog; they are safe to publish.
- Local voice testing: `.env.dev` (ignored) holds `SAS_PIPER_URL=http://127.0.0.1:5000`; build the
  Piper image from `deploy/piper` in WSL and run it with `-p 127.0.0.1:5000:5000`.
