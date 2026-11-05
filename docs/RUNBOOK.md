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
string-art-studio generate photo.jpg --out plan --engine a --pins 240 --lines 4000
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
   studio backdrop), keep gamma 0.75 to 0.8.
3. Press "Show importance" on the preview: faces and edges should be red. Paint the mask for anything
   else that matters (a ring, a logo on a shirt).
4. Generate engine A. Expect 2,200 to 3,000 lines; the "regrown" number in the metrics table shows the
   lines added after refinement.

## Calibration after the pilot build

1. Measure the thread: wrap ten turns around a ruler, divide by ten, enter it as thread width.
2. String the piece and photograph it from the viewing distance beside the simulated render.
3. If the real piece is darker than the render, lower `thread_opacity`; lighter, raise it. Re-run the
   same project (every run is kept) until the simulation matches, then save the board as a preset.
4. If light areas are too washed, raise light-area protection; if faces are too faint, lower it or
   lower gamma in the Tone panel.
