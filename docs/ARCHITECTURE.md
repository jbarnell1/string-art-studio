# Architecture

What the system is and how the parts fit. Rationale is in [DECISIONS.md](DECISIONS.md).

## One paragraph

One Python process serves a JSON API and a small PWA. The browser edits settings and sees a live
preview rendered by the same prep pipeline the engines use. Generation runs server-side in a worker
pool; progress and a live render stream to the page over server-sent events. Results, exports and
build progress are files and SQLite rows under one data folder.

```
 Phone or desktop browser (PWA, tailnet)
   |  settings, preview requests, SSE
   v
 uvicorn / FastAPI  (stringart/web/app.py)  ── /api/...  ── static shell (web/static)
   |                      |
   | prep.prepare_target  | JobManager (jobs.py): ThreadPoolExecutor, cancel flags, SSE fan-out
   v                      v
 prep.py (crop, tone, CLAHE, edges, mask, rim weights)
   |
   v
 engine/state.py  RenderState: target T, weights W, coverage C, gradient G
   |            \
   | greedy.py   \ setcover.py
   v              v
 sequence of pins ──> engine/render.py (thread-accurate render, comparison) ──> exports/
                                                                                |
 data/<id>/source.jpg, mask.png, runs/<run>/{sequence.json, render.png, compare*.jpg, live.jpg}
 data/studio.sqlite3 (projects, runs, presets, build_progress)
```

## The objective both engines share

The target `T` is the prepared grayscale turned into wanted thread coverage (darkness on a light
board, lightness on a dark one). The simulated coverage `C` saturates: a pixel crossed by `n` passes
of per-pass coverage `a` has `C = 1 - (1 - a)^n`, where `a = thread_width / pixel_size * opacity`
(times sqrt(2) on diagonals). Both are blurred with a Gaussian sized from the viewing distance and
compared with importance weights `W`:

```
E = sum W * (blur(T) - blur(C))^2
```

Adding a line with per-pixel deltas `d` changes `E` by `sum W blur(d)^2 - 2 sum d * G` with
`G = blur(W * (blur(T) - blur(C)))`, because the Gaussian is self-adjoint. `G` is kept current after
every line with blurs on a crop around it, so scoring a candidate is one gather along its pixels.
The light-protection factor scales the negative residual inside `G` (ADR-006). Reported error is
the symmetric weighted RMS in [0, 1].

## Engines

Engine A (`greedy.py`): from the current pin score every allowed next pin, take the best, repeat.
When nothing from the current pin helps, look one line ahead and take the pair if it still lowers
the error (a bridge, capped at 5% of the line budget). Then refinement passes within a time budget:
replace a middle pin with the best alternative (worst lines first, then random order), drop
back-and-forth pairs that hurt. Then the walk continues from the last pin, because refinement frees
error it could not reach before (ADR-012).

Weights `W` (`prep.board_weight`): one inside the board, fading to zero in the band at the pins
(ADR-005), times the painted mask, times the automatic face map from `faces.py` (YuNet, ADR-011),
times `1 + structure_weight * edges(T)`.

Engine B (`setcover.py`): score all chords, add the best batch, prune chords whose removal helps,
alternate; then pair odd-degree pins, join components with doubled chords, and trace an Eulerian
path (ADR-003). Repair lines and the error they add are reported.

## Data model

| Table | Fields |
|---|---|
| projects | id, name, notes, timestamps, has_image, image size, has_mask, prep JSON, board JSON, engine JSON |
| runs | id, project_id, status, engine, label, prep/board/params snapshots, metrics JSON, group_id (Run both), error_message |
| presets | id, name, board JSON, engine JSON |
| build_progress | run_id, step, updated_at, device |

## API

All under `/api`. `GET /api/docs` is the live OpenAPI page.

| Route | Purpose |
|---|---|
| `GET /health`, `GET /defaults` | Liveness, feature flags (background removal), default settings |
| `GET/POST /projects`, `GET/PATCH/DELETE /projects/{id}` | Project CRUD; PATCH saves prep, board, engine |
| `POST /projects/{id}/image`, `GET .../image?size=` | Upload (JPEG, PNG, HEIC...), thumbnails |
| `PUT/GET /projects/{id}/mask` | Painted importance mask (512 px grayscale PNG) |
| `POST /projects/{id}/preview` | Live target preview JPEG for given prep and board |
| `GET /projects/{id}/faces` | Detected faces (normalised source coordinates) and a suggested frame |
| `POST /projects/{id}/weightmap` | The importance weights as a PNG overlay (red more, blue less) |
| `POST /projects/{id}/runs` | Start engine a, b or both (both share a group_id) |
| `GET /runs/{id}`, `GET .../events` (SSE), `POST .../cancel`, `PATCH`, `DELETE` | Run status and control |
| `GET /runs/{id}/{live.jpg,render.png,target.jpg,compare.jpg?blur=1}` | Pictures |
| `GET /runs/{id}/sequence.{txt,csv,json}`, `.../steps` | Pin order; `steps` is the build-mode payload |
| `GET /runs/{id}/template.pdf?paper=letter|a4|single` | True-size pin template |
| `GET /runs/{id}/fab.{svg,dxf}?variant=holes|ring&hole=` | Fab files |
| `GET/PUT /runs/{id}/progress` | Build progress (step, device) |
| `GET/POST/DELETE /presets` | Board and engine presets |
| `GET /voice`, `GET /voice/clip.wav?text=&voice=&speed=` | Piper status and cached clips (503 without Piper) |
| `POST /runs/{id}/voice/prewarm`, `GET /voice/prewarm/{job}` | Pre-render every pin phrase of a plan |
| `GET /projects/{id}/bundle.zip`, `POST /projects/import` | Move a project between instances |

## Path prefix and PWA scope

Routes are mounted at `/`. Behind Tailscale Serve (`--set-path /stringart`) the prefix is stripped
before the request arrives, so uvicorn's `--root-path` (env `SAS_ROOT_PATH`) is the only place the
prefix exists. The shell injects it into `<base href>`, the manifest's `id`, `scope` and `start_url`,
and the service worker derives it from its own URL. Every client URL is relative.

## Storage layout

```
data/
  studio.sqlite3
  projects/<project id>/
    source.jpg              re-encoded upload (EXIF applied, max 2400 px)
    mask.png                importance mask, optional
    subject_alpha.npy       cached background-removal alpha, optional
    faces.json              cached face detection for source.jpg
    runs/<run id>/
      target.jpg  live.jpg  sequence.json  render.png  compare.jpg  compare_blur.jpg
```

  voice/<voice>/<hash>.wav  Piper clips, keyed by voice, speed and text (safe to delete)

Back up `data/` and nothing else.
