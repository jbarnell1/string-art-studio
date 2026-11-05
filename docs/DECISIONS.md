# Decision log (ADRs) and open questions

Short, dated records of why we chose something. Newest first. Reversals get a new entry.

## Decisions

### ADR-017 - Colour: one continuous thread per colour, layers composited in build order, CIELAB error
**2026-11-05.** A colour plan is a list of layers, each a thread colour with its own pin sequence,
laid lightest first and darkest last on a light board (the reverse on a dark one), because a dark
thread must sit on top to stay dark. The simulation gives each layer its own saturating coverage
and composites them in that order in linear light (what is above covers what is below in
proportion to its coverage); after the viewing-distance blur the picture is compared with the
photo in CIELAB scaled by 1/100, so a wrong hue costs as much as a wrong lightness and the single
thread engine's numbers stay comparable. Layers are blurred before compositing, which treats
threads of different colours as overlapping at random within the blur radius. The gradient of
one colour is the chain rule through the Lab Jacobian and the layer's "below" and "above"
composites, so the greedy walk keeps one current pin per colour and takes the best (colour, line)
at every step; each colour starts at the pin with its best first line rather than at pin 0, all
colours share the line budget, the local search runs per layer, and engine B
is not offered for colour (its set-then-repair would need one repair per colour). Palettes are a
preset set (CMYK, warm skin, earth, primary), a custom list of up to six, or k-means over the
framed photo's Lab pixels with the darkest centre forced to black and any centre near the board
colour dropped. A colour plan costs several times a single-thread plan (K gradients and K fans per step; about
210 s for 4,000 lines at 800 px against 37 s), so a colour run gets three times the configured time budget. Build mode shows
the colour and stops at each change ("tie off at pin 12, change to red thread, start at pin 40"),
exports carry a layer and colour per step, and the run stores `layers.json` beside the flat
sequence so older readers keep working.

### ADR-016 - Per-line work is a band, not a frame; fans are cached per pin
**2026-11-05.** Profiling the v2 engine: scoring the 210 candidate lines from a pin took 5.8 ms
(1.9 ms sampling the pixels, 3.2 ms gathering three images and forming d), and adding a long line
took 7.7 ms because the gradient image was recomputed on the line's whole bounding box, nearly the
full frame for a diagonal, with two power functions per pixel. Now: the blur pad matches OpenCV's
real kernel radius (4 sigma, not 6), a line's update runs in tiles of about five pads along the
line so only the band around it is blurred and re-derived, lookup tables replace the powers,
every image lives in a flat buffer with a spare zero so padded sample slots gather nothing and
masks disappear, two per-pixel products (H1 = (1 - C) G, H2 = K (1 - C)^2) make an add-score two
gathers, and the sampled fan of each pin is cached on first use (about 190 MB at 800 px and 240
pins; skipped above 1100 px). Result: scoring 1.3 ms, a long line 1.7 ms, a plan of 4,000 lines in
about half the time. ADR-007 said no line cache because a cache of every chord's pixels was 260 MB
and bought a millisecond; per-pin fans are smaller and buy most of a step, so it is reversed for
work resolutions up to 1100 px.

### ADR-015 - Local search prices every move from one gain matrix, then verifies exactly
**2026-11-05.** The v2 refinement walked the sequence and spent four line operations at every
position whether or not anything could improve there. `engine/polish.py` instead computes the add
gain of every allowed chord once per round (about 0.3 s), prices four moves at every position
from it (replace the middle pin, insert a pin, delete a pin, unfold a back-and-forth pair), picks
the best non-overlapping ones and verifies each exactly in the state it lands in, keeping only
real improvements; then the greedy walk continues from the end and the two alternate until
nothing changes. Honest result: on the v2 objective it converged to the same error as the old
refinement (0.1665 against 0.1663) and lookahead in the walk bought nothing either, which is what
told us the objective, not the optimiser, was the limit (ADR-014). It stays because it converges in
a fraction of the time, uses fewer lines for the same error, and its insert and delete moves are
what let the line count settle itself under the new objective.

### ADR-014 - Light adds linearly; the error is measured in lightness
**2026-11-05.** The v1 and v2 objective compared the photo's grey value, which is an encoded
lightness, with thread coverage, which is a linear quantity: a 50% grey asked for 50% coverage,
but half-covered white board reflects half the light and looks like 73% grey. Every mid-tone was
asked for about half the thread it needed, faces came out washed, and the gamma and
light-protection knobs were compensating for the wrong model. The engine now simulates light:
luminance y = Y_board + (Y_thread - Y_board) C from the board and thread colours, blurred for the
viewing distance, then encoded (exponent 1/2.2, close to CIE L*) and compared with the photo's
lightness; the gradient carries the encoding slope, so dark regions count as the eye counts them.
Renders and comparison images blend in linear light and encode for the screen, so they look like
the real piece will. Bench on the man sample, same tone settings, measured for both on the new
lightness yardstick: board error 0.187 (old) against 0.159 (new), face 0.236 against 0.204, and
the plan uses its full 4,000 lines instead of stopping near 2,700. Under the honest model the
strict light protection (1.0) is best (0.75: 0.163; 0.5: 0.174) and gamma 1.0 beats the old 0.78
darkening, so both defaults change. More than 4,000 lines buys weighted gains only and adds
background haze, so the budget stays. Objective "coverage" remains selectable for comparison.

### ADR-013 - Piper runs as its own container; Studio caches every clip on disk
**2026-11-05.** Build mode's voice in v2 is Piper (piper1-gpl) behind its small HTTP server
(`POST /synthesize`, `GET /voices`) in a second container, `deploy/piper/Dockerfile`, with three
English voices baked in so it works offline. It is GPL-3.0, so it stays a separate process and image
and the studio keeps its MIT licence; the studio only speaks HTTP to it. Studio keeps a clip cache
under `data/voice/<voice>/<hash>.wav` keyed by voice, speed and text, pre-renders every pin phrase of
a plan when voice is switched on ("Preparing voice 120 of 244"), and the phone plays cached WAVs
through one audio element with the next phrase prefetched. The compose profile `voice` and
`SAS_PIPER_URL` turn it on; without them, or when Piper is down, the browser's own speech is used,
so nothing breaks. The per-pin phrase ("Pin 37. C 14.") is defined once on the server
(`voice.pin_phrase`) and sent with the build steps so the cache always hits.

### ADR-012 - Engine A regrows after refinement, and refines worst lines first
**2026-11-05.** On portraits the greedy walk ends with "no improving line" at 2,000 to 2,400 lines
while the line budget is 4,000. Refinement then moves hundreds of lines, which frees error the walk
could not reach before. Engine A now walks again from the sequence's last pin after refinement; on
the samples that adds 300 to 500 lines and lowers the error further (bench: 2,925 lines instead of
about 2,400, error 0.153 instead of 0.165 at the same settings). Refinement's first pass also visits
the middle pins in order of how much removing their two lines would help, so the lines doing the
most harm get the first chance at a better pin; later passes stay random. `regrown_lines` is
reported in the run metrics.

### ADR-011 - Faces are detected locally and weighed up; edges count more
**2026-11-05.** The painted importance mask works but needs the user to know to paint the eyes. v2
bundles YuNet (OpenCV's 227 KB face detector, MIT) and does three things with it: a "Frame the face"
button that sets the crop so the face fills the circle (the framing that strings best), an automatic
importance map (face ellipse about twice the weight, eyes, nose and mouth up to three times, scaled
by `face_emphasis`, default 1.0), and a "Show importance" overlay on the preview so what the
optimiser cares about is visible. A second weight, `structure_weight` (default 0.5), multiplies the
weights along edges of the target so eyes, jaw and hair line stay crisp instead of dissolving into
tone. Both are plain multipliers on `W`, so the objective is unchanged and the painted mask still
composes with them. Bench on the man sample at the same tone settings: face-region error 0.248
without, 0.245 with face emphasis; the visible difference (eye sockets, nose shadow) is larger than
the number suggests because the error is dominated by the hair mass. Detection never leaves the
server and is cached beside the photo in `faces.json`.

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
