# Decision log (ADRs) and open questions

Short, dated records of why we chose something. Newest first. Reversals get a new entry.

## Decisions

### ADR-021 - A 3D-printable drilling jig; pins are glued into drilled holes, not hammered
**2026-11-05.** The owner worried that a hammered brad that works loose would ruin a build hours in,
and has a 3D printer (Elegoo Neptune 4 Pro, 225 mm bed). The thread loads every pin sideways towards
the centre, so the failure is a brad leaning or wallowing its hole, not pulling out; a pin pressed
into a slightly undersized drilled hole with glue does not lean. PLA creeps under steady load, so it
never carries thread: it only guides the drill. `stringart/exports/jig.py` builds, with manifold3d
for watertight booleans, a ring of segments (one per sector, split further when a sector will not
fit the bed) with a guide hole over every pin, a dovetail into the next segment, a skirt over the
board's edge (optional, for boards that are not quite round), and the letter and pin range debossed;
plus a height block set from the build estimate and a strip of holes at five clearances to check the
fit first. Parts are turned over for printing, a rotation, so the uneven hole pattern is never
mirrored. Checks in the tests: one through hole per pin (genus), a vertical ray through every pin
centre misses the plate and one 2 mm beside it hits, mating dovetails do not overlap, every part fits
the bed. On the default board: eight segments of 30 pins, 182 mm square on the bed, about 53 cm3 each.

### ADR-020 - A build estimate from the plan's own pin wraps
**2026-11-05.** The owner asked whether 8,000 lines is realistic, how big the brads must be and how
long a build takes. The results page now answers from the plan itself (`stringart/estimate.py`,
`GET /api/runs/{id}/estimate?pace=`): wraps per pin (every middle visit plus the knots where a colour
starts and ends), the brad height the busiest pin needs (wraps times `wrap_height_mm`, default
0.35 mm, plus 3 mm of headroom), the depth into the board (`board_thickness_mm` minus 5, within 8 to
15 mm), the shortest common brad that covers both, 18 gauge or 16 gauge above 60 wraps, and the time
at a pace the user picks (default 400 lines an hour) plus 10 s per brad and 5 minutes per thread
change. Wrap height, board thickness and pace are guesses until the pilot build and are labelled
as such; the first two are board settings so a household can calibrate them. On the owner's
8,000-line plan: busiest pin 69 wraps, 1¾" 16 gauge brads, 20.7 hours, 5 spools.

### ADR-019 - Pins are spaced a little unevenly, so chords do not stack into rings
**2026-11-05.** The owner saw a bullseye of concentric circles in dark areas of an 8,000-line plan,
from both engines. It is geometry, not rendering: a chord that skips d pins on a ring of radius R
passes R cos(pi d / N) from the centre, so with evenly spaced pins every chord of the same skip
touches the same circle and the chords stack into envelopes 3.86 mm apart on a 610 mm, 240-pin
board. The owner's plan showed peaks in its radial density at exactly those radii, and a real piece
would show them too. The engine cannot avoid it, because the distances themselves are quantised.
The fix is in the board: each pin moves along the rim by a fixed pseudo-random fraction of the pin
pitch (`pin_jitter`, default 0.3, capped so neighbours keep the pin diameter plus 1.6 mm, about
3.2 mm at 240 pins), which spreads each skip's chords over most of the gap to the next. The offsets
come from a fixed integer hash of the pin index and `jitter_seed`, not from numpy's generator, so
a template can be regenerated identically by any later version; the editor's overlay uses the same
hash in JavaScript. Boards stored before this setting have no key and keep even pins, so old
templates and plans are unchanged. Measured on the owner's plan, regenerated with the same
settings: ring score (radial comb 15 to 115 mm) 0.0258 to 0.0134, peak 0.079 to 0.035, error 0.1036
to 0.1031, the same 8,000 lines and 3.7 km of thread.

### ADR-018 - The two inner loops are Numba kernels, with numpy fallbacks
**2026-11-05.** The owner asked whether the GPU could speed the engine up. Both machines have
NVIDIA cards (the media PC a GTX 1060, the desktop an RTX 4070 SUPER) and CUDA reaches WSL2, but
the greedy walk is strictly sequential and each step is a few small array operations, so a GPU
port would be launch-latency bound, need a CUDA runtime in the image and the container toolkit
in the homelab distro, and still leave the colour engine's per-tile maths as the bottleneck. The
CPU route was taken first: `engine/kernels.py` holds two fused loops compiled by Numba on first
use and cached (`NUMBA_CACHE_DIR` in the container): `score_add`, the gains of all candidate lines
from the H1 and H2 products (1.3 ms to 0.14 ms for a fan of 210 lines), and `color_terms`, the
whole per-pixel colour model on a crop (composite, Lab, Jacobian, one integrand and one
second-order weight per layer; the colour line update went from 35 ms to 7 ms). Each has a plain
numpy twin that runs when Numba is missing and that the kernels are checked against (equal to
float precision). Result on this desktop: a four-colour plan of the colour sample in 55 s instead
of 209 s with a lower error (0.230 against 0.250) because the local search gets many more rounds in
its budget; a single-thread plan in 30 s instead of 37 s at the same error (0.154), since it is now bound by the tiled line update and the exact verification of local-search moves rather than by scoring. Numba threads are capped at two per worker in the container
so two concurrent plans share the i3's four cores with OpenCV. Building the local search's gain matrix from the cached fans was tried and dropped: 240 separate calls were slower than one pass over all chord pairs.

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
