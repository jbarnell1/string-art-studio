# Runbook

## Run it anywhere (Docker)

```bash
git clone https://github.com/jbarnell1/string-art-studio.git
cd string-art-studio
cp .env.example .env     # optional
docker compose up -d --build
open http://127.0.0.1:8050
```

Everything it stores is in `./data`. Upgrade with `git pull && docker compose up -d --build`.
Put Tailscale Serve, Caddy or nginx in front for HTTPS. Under a stripped path prefix set
`SAS_ROOT_PATH` to that prefix.

Background removal is optional and heavy (onnxruntime plus a 170 MB model on first use). To enable it
build with the extra: add `RUN pip install ".[bgremove]"` after the install line in the Dockerfile, or
install `rembg` in a local venv; the Subject panel lights up when it is importable.

## Voice (Piper)

Build mode reads pin numbers aloud. With the `voice` compose profile a second container runs Piper
(`deploy/piper/Dockerfile`, three English voices baked in; add more with the `PIPER_VOICES` build
argument). Set in `.env`:

```
COMPOSE_PROFILES=voice
SAS_PIPER_URL=http://piper:5000
SAS_PIPER_VOICE=en_US-lessac-medium
```

Then `docker compose up -d --build`. `GET /api/voice` reports `available: true` when Studio can reach
it. Clips are cached under `data/voice/`; delete that folder to re-render after a voice change.
Without Piper the Voice button uses the browser's own speech.

## Run it without Docker

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
string-art-studio serve --port 8050               # or: python -m stringart.cli serve
pytest -q
```

Command-line generation without the web app:

```bash
string-art-studio generate photo.jpg --out plan --engine a --pins 240 --lines 8000
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SAS_DATA_DIR` | `data` | Where photos, plans and the database live |
| `SAS_HOST`, `SAS_PORT` | `127.0.0.1`, `8050` | Bind address (the container uses 0.0.0.0 inside, compose binds 127.0.0.1 outside) |
| `SAS_ROOT_PATH` | empty | External path prefix when a proxy strips it |
| `SAS_WORKERS` | `2` | Concurrent generation jobs |
| `SAS_RENDER_RES` | `1600` | Final render size in px |
| `SAS_PREVIEW_RES` | `640` | Editor preview size |

## The Barnell homelab install (media PC)

Checkout: `C:\homelab\string-art-studio` (`/mnt/c/homelab/string-art-studio` in the `homelab` WSL
distro). Container `string-art-studio` on 127.0.0.1:8050. Data in `./data` (covered by Backrest
through `C:\homelab`).

Install or update, from jon-desktop:

```bash
ssh homelab@100.101.123.33 'wsl -d homelab -- bash -lc "cd /mnt/c/homelab/string-art-studio && git pull && bash deploy/media-pc/install.sh"'
```

Tailscale serve was set on 2026-11-05 (`deploy/media-pc/tailscale-serve.ps1` holds the command; it
ran fine over SSH as `homelab`, no elevation needed on that box). Result:
`https://desktop-4l95il9.taildc59f0.ts.net/stringart/`.

Owner step, once: Gatus. Paste `deploy/homelab/gatus-endpoint.yaml` into
`/mnt/c/homelab/monitoring/gatus.yaml` and restart the monitoring stack.

The Homepage tile on jon-desktop is in `C:\pwa-autostart\homepage\config\services.yaml` (Creator
tools) with the icon in `images/icons/string-art-studio.png` (added 2026-11-05).

Logs: `docker compose logs -f` in the checkout. Health: `curl http://127.0.0.1:8050/healthz`.

Weekly auto-update: add `string-art-studio` to the loop in `/mnt/c/homelab/update-containers.sh`
(it is built from source, so use the same `git fetch` pattern as Watchman).

## Backups and restore

Back up the `data/` folder (SQLite in WAL mode: stop the container or copy `studio.sqlite3` with
its `-wal` file). Restore by putting the folder back and starting the container. A single project
can also travel as a bundle ZIP from the results page.

## Portraits that string well

1. Press "Frame the face" in the Subject panel, or crop so the face fills the circle.
2. Pick the Portrait tone preset. Lower the white point until the backdrop goes white (0.8 for a grey
   studio backdrop) and leave gamma at 1.0: since v3 the engine models light properly, so the
   faithful target is the best target.
3. Press "Show importance" on the preview: faces and edges should be red. Paint the mask for anything
   else that matters (a ring, a logo on a shirt).
4. Generate engine A. Maximum lines (8,000 by default) is a ceiling; the results page says when a plan hit it. The "regrown"
   number in the metrics table shows the lines added after the local search.

## Colour plans

Pick a palette in the Board panel (or Auto to take colours from the photo); the preview turns to
colour and the swatches show the build order, lightest first. Colour plans run on engine A and take
about twice as long as a single thread (about one minute for 4,000 lines on a desktop, two on the media PC).
Colour plans are line-hungry (each colour needs its own coverage): on the colour sample the error
was 0.254 at 4,000 lines and 0.197 at 6,000, which is why the default ceiling is now 8,000; budget the thread
accordingly. More search time changes nothing; both engines converge inside the default budget.
In build mode the current colour sits in the top bar and the player stops at each change: tie off,
change thread, tie on at the pin shown, press Next. Buy one spool per colour; the metrics table
gives the thread per colour.

## Building the board with a 3D printer

1. Cut or buy the round (¾" plywood holds pins better than MDF), prime and paint it. Measure its
   diameter in three directions and enter the real figure as Diameter in the Board panel, and the
   thickness as Board thickness. Save the board as a preset and keep it: every plan with these
   settings fits the same board.
2. Generate the plan you want to build. On its results page set your printer's bed size and drill
   bit in "3D-print drill guide" and download the ZIP. Untick Skirt if the round is not true.
3. Follow README.txt in the ZIP: print the hole test strip and pick the clearance, print the
   segments, lock them round the board from A at twelve o'clock, drill every pilot hole, glue the
   pins in using the height block, let the glue cure, and load-test one offcut pin overnight.

## Rings in dense areas

Plans made before v0.3.3 use evenly spaced pins and can show concentric rings in dark areas. Open
the project, check that "Uneven pin spacing" in the Board panel reads 0.30, and generate again; the
new template prints the uneven positions. Never mix a template and a plan from different runs.

## Calibration after the pilot build

1. Measure the thread: wrap ten turns around a ruler, divide by ten, enter it as thread width.
   Wrap twenty turns round a spare brad, measure the height they take, divide by twenty, and enter
   it as "Brad height per wrap"; set "Board thickness" too. The build estimate then gives the right
   brad length. Time a few hundred lines and pick that pace on the results page.
2. String the piece and photograph it from the viewing distance beside the simulated render.
3. If the real piece is darker than the render, lower `thread_opacity`; lighter, raise it. Re-run the
   same project (every run is kept) until the simulation matches, then save the board as a preset.
4. If the real piece is lighter or darker than the render overall, `thread_opacity` is the knob;
   if only the mid-tones differ, gamma in the Tone panel. Light-area protection stays at 1.0 unless
   you want a moodier, hazier piece.
