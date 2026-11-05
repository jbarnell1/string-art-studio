# Decision log (ADRs) and open questions

Short, dated records of why we chose something. Newest first. Reversals get a new entry.

## Decisions

### ADR-010 - Deploy on the media PC under /stringart, port 8050
**2026-11-05.** The owner asked for this app to run on the media PC (desktop-4l95il9), not jon-desktop.
Port 8040 there is taken by Highlights, so the container binds 127.0.0.1:8050. It is published by
Tailscale Serve as a path on the existing host (`/stringart`), beside Immich at `/` and Highlights at
`/highlights`, because Android treats every PWA on one host as the same app unless each has its own
path scope (the homelab rule learned with Doc Keeper and Night Shift). The app reads
`SAS_ROOT_PATH=/stringart` so the manifest scope, start URL and every link carry the prefix while the
routes stay at the root (Tailscale strips the prefix). Data is a bind mount at
`C:\homelab\string-art-studio\data`, inside the folder Backrest already backs up.

### ADR-009 - MIT licence, fictional sample photos
**2026-11-05.** The brief asked "MIT or similar"; MIT is the least friction for a hobby tool others
will copy code from. Sample photos are AI-generated fictional people (and a dog) made for the repo, so
the README and tests never carry a family photo.

### ADR-008 - Build mode in v1 is a step player with browser speech; Piper waits for v2
**2026-11-05.** The brief puts Piper voice guidance in v2. Build mode still ships in v1 with everything
that costs nothing: huge pin numbers with sector labels, clockwise direction hints, next and back,
tap to advance, auto-advance with pause, keyboard and clicker keys (PageUp/PageDown, arrows, Enter),
screen wake lock, progress saved to the server with a local fallback, and the browser's own speech
synthesis as a voice option. Piper will replace the voice behind the same toggle.

### ADR-007 - No line cache; lines are rasterised on the fly, vectorised
**2026-11-05.** The brief suggested caching pixel lists for all 28,700 chords. At 800 px that cache is
about 260 MB per board configuration and must be rebuilt whenever the working resolution or pin count
changes. Sampling 240 candidate lines per step with one vectorised numpy call costs about a
millisecond, so the cache buys nothing. Engine B scores all chords in chunks the same way (about
0.3 s per full pass).

### ADR-006 - Light-area protection: an asymmetric objective knob, default 0.5
**2026-11-05.** With the plain squared error the engines stop early on portraits: every chord that
darkens the hair or beard also crosses the face or background, so the first-order gain reaches zero
with mid-tones still light and the subject unreadable. Scaling the negative residual (areas already
too dark) by a factor `light_protection` in the gradient image lets lines through at the cost of some
wash. 1.0 is the strict objective; 0.5 gave readable faces on the samples while keeping the background
far cleaner than darkness-greedy generators. The reported error stays the symmetric RMS so runs are
comparable whatever the knob. The pilot build decides the default.

### ADR-005 - Rim weights fade to zero in the band near the pins
**2026-11-05.** Every chord ends at two pins, so coverage piles up in a ring just inside the pin
circle. Penalising that ring made the optimiser refuse lines the dark areas still needed. The weight
image fades from 1 to 0 across the outer 7% to 1.5% of the diameter (`prep.board_weight`); the eye
forgives the dark rim on every real piece, and the importance mask can still raise weights anywhere
else.

### ADR-004 - Default working resolution 800 px, not one thread width per pixel
**2026-11-05.** The brief suggested about 2400 px (one pixel per 0.25 mm thread). The incremental
objective update costs two Gaussian blurs on a crop per line; at 2400 px that is about 30 ms a line
and 4000 lines take two minutes, which misses the 60 s target on the media PC's i3. At 800 px (about
three thread widths per pixel on a 610 mm board) engine A does 4000 lines in about 20 s. Thread width
still enters the model as the coverage a pass adds to a pixel (`thread_width / pixel_size`, with the
diagonal factor), and the final render is drawn at 1600 px with anti-aliased lines. `work_res` is a
setting, so anyone with time can run 1600 or 2400.

### ADR-003 - Engine B adds chords in batches and repairs parity greedily
**2026-11-05.** Scoring all chords per added line would take 20 minutes. Engine B scores everything
once per round and adds the best batch (24 to 120 chords depending on how many remain), then prunes
chords whose removal lowers the error, and alternates. Odd-degree pins are paired by a greedy
minimum-cost matching (cost is the error the connecting chord adds; chords shorter than the minimum
skip are heavily discouraged, never impossible), components are joined with doubled chords, and
Hierholzer's algorithm reads off one continuous path. The repair cost is reported on the results page
so B can be judged honestly. A proper minimum-weight matching is a later refinement if B earns its keep.

### ADR-002 - One prep pipeline for preview and engine, mask painted in frame space
**2026-11-05.** `prep.prepare_target` builds the target from the source photo for both the live
preview (640 px) and the run (work_res). The importance mask is painted over the framed preview and
stored as a 512 px PNG in frame space, so it follows the crop the user painted on; moving the crop
afterwards means repainting, which is the simpler rule to explain.

### ADR-001 - Python + FastAPI + vanilla JS PWA, one container, SQLite and files on disk
**2026-11-05.** The owner asked for Python with a PWA front end and no native app. FastAPI matches
Doc Keeper and Scripture Studio, so the homelab conventions (uvicorn root path, path-scoped PWA,
loopback bind behind Tailscale) carry over. The client is plain JS with no build step so the repo is
easy to run and fork. Metadata lives in SQLite; images, renders and exports are files under
`data/projects/<id>/`, which keeps backups and bundles simple.

## Open questions (from the brief, with the current answer)

| Question | Current answer |
|---|---|
| Default viewing distance for the blur model | 1.5 m with 1.5 mm of blur per metre; both are settings. Tune after the pilot build. |
| Thread width calibration | Manual entry plus the `thread_opacity` knob. A printable test pattern is on the roadmap. |
| Local background removal in v1 | Optional: install the `bgremove` extra (rembg) and the toggle lights up. Not in the default image. |
| Square and rectangular frames | Later. `BoardConfig.shape` exists and is forced to circle. |
| Thread brand for colour | Open until v3. |
| Repo licence | MIT (ADR-009). |
