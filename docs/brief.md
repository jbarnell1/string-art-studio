# String Art Studio: product brief

The owner's design document (November 2026), condensed. Decisions taken while building it are in
[DECISIONS.md](DECISIONS.md).

## Overview and goals

A self-hosted web app that turns a photo into a pin-and-thread build plan, replacing a $139 retail
kit with about $45 to $60 of materials. It runs on the homelab beside the other self-hosted apps and
ships as an open source repo.

- Output that beats free web generators: clean light areas, readable faces, no gray wash over
  backgrounds.
- Full control over image prep and generation settings on one page before committing to a build.
- Two engines (proven greedy and an experimental advanced one) with side-by-side comparison.
- Exports that make board prep trivial: true-size pin template, SVG/DXF for laser or CNC.
- Phone-friendly build mode with Piper voice guidance.
- Monochrome first, palette-agnostic engine so colour is an add-on.

Non-goals for v1: user accounts, multi-tenant hosting, native mobile apps.

## Users and use cases

The household making portraits and gifts; anyone deploying the repo on their own homelab. Make a
piece (upload, tune, generate, export, build with voice); reuse (projects saved, re-run with new
settings, every run kept as a version); share through the repo plus a project bundle. No accounts;
the instance relies on the homelab's network and auth layer.

## Core workflow

1. Upload a photo (JPEG, PNG, HEIC).
2. Prep: crop and zoom to the frame, adjust tone, optionally paint an importance mask. A live preview
   shows the target exactly as the optimiser will see it.
3. Configure board, pins, thread and engine, or load a preset.
4. Generate as a background job with streamed progress and a live preview; cancellable.
5. Compare target against simulated result, and A against B when "Run both" is on, with error, line
   count and thread length.
6. Tweak and re-run; each run is a version.
7. Export the pin template, fab files and sequence.
8. Build on a phone with voice guidance.

## Image editor controls

Crop, zoom, rotate; brightness, contrast, gamma; levels (black and white point); local contrast
(CLAHE); grayscale channel mix; edge emphasis; optional background removal with a local model;
invert; importance mask brush; viewing-distance preview.

## Board and thread configuration

| Setting | Default | Range or options |
|---|---|---|
| Frame shape | Circle | Circle (square and rectangle later) |
| Diameter | 610 mm (24 in) | 200 to 1000 mm |
| Pin count | 240 | 100 to 400 |
| Pin inset from edge | 10 mm | 5 to 30 mm |
| Pin diameter | 1.2 mm | Matches brad gauge |
| Thread width | 0.25 mm | Calibrated to the actual thread |
| Thread colour | Black | Any colour; palette in colour mode |
| Board colour | White | Any colour |
| Minimum pin skip | 15 | Excludes near-neighbour lines |
| Max lines | 4000 | Upper cap; engines usually stop earlier |
| Viewing distance | 1.5 m | Drives the comparison blur |
| Sector labels | A to H | Groups pins for faster finding |

Units toggle between inches and millimetres; any configuration can be saved as a preset.

## Generation algorithm

Both engines optimise the same objective: make a realistic simulation of the thread look like the
target from a normal distance. Scoring by error rather than raw darkness is the key fix over free
generators: lines that cross areas meant to stay light are penalised.

Shared model: thread-accurate rendering; saturating opacity `1 - (1 - a)^n`; viewing-distance
Gaussian blur on both target and render; weighted squared error; pins as points in v1.

Engine A, greedy with error scoring: from the current pin score every valid next pin by error
reduction, take the best, repeat; stop when no line improves or at max lines; refinement passes
replace middle pins or remove back-and-forth pairs while the error drops.

Engine B, unconstrained set plus path repair: choose the best multiset of chords with add and remove
passes; pair odd-degree pins with the lowest-cost connecting chords; build one continuous path with
Hierholzer's algorithm; report the repair cost.

Performance target: under 60 seconds for 240 pins and 4000 lines on homelab hardware.

## Colour mode (v3)

Palettes (CMY plus black, custom, auto by k-means always including black); one coverage layer per
colour composited in build order; CIELAB error after the blur; one continuous thread per colour
sharing the line budget; lightest first, black last on a white board. Roughly two to three times the
thread and build time of monochrome.

## Build mode and voice guidance (v2 for voice)

Huge current and next pin numbers, sector letter, step N of M, clockwise direction hint; large next
and back buttons, tap anywhere, auto-advance with pause, Bluetooth clicker or volume keys; Piper as a
local TTS service with pre-rendered clips per pin and phrase; progress saved server-side with a local
fallback; screen wake lock; colour mode announces thread changes.

## Outputs and exports

| Export | Format | Use |
|---|---|---|
| Pin template | PDF, true size | Tiled across Letter or A4 with alignment marks and overlap, or one large sheet; numbered pins, sector labels, centre mark, calibration ruler |
| Fab file | SVG, DXF | Board outline plus pin holes, or a notched ring |
| Sequence | TXT, CSV, JSON | Pin order for any player or manual build |
| Preview | PNG | Simulated render and side-by-side comparison |
| Thread estimate | In the UI | Total length plus 20% margin, spool count |
| Project bundle | ZIP | Move a project to another instance |

## Architecture and deployment

Follow the homelab conventions for stack, containers, reverse proxy and auth. Generation runs
server-side. Components: web UI, API, generation worker, Piper service (v2), storage (small database
plus files on disk). The repo ships a single compose file, defaults via environment variables and a
README for a generic homelab.

## Milestones

1. v1, generate and compare.
2. Pilot build to calibrate thread width, opacity and viewing distance.
3. v2, build mode with Piper voice.
4. v3, colour.

## Materials for one 24 inch piece (about $45 to $60)

Pre-cut 24 inch round (MDF or edge-glued pine, 3/4 in) $15 to $25; white primer or paint $8;
18-gauge brads, 1 to 1.25 in, box of 500, $8; black heavy-duty polyester thread about 0.25 mm, two
to three 1,000 yd spools, $10 to $15; hanger $3. Tools on hand: hammer, needle-nose pliers, awl,
tape, printer. Buy thread to the app's estimate plus 20%.
