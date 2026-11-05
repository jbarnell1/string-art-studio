#!/usr/bin/env bash
# Install or update String Art Studio on the media PC (homelab WSL distro, Docker under systemd).
# Run inside WSL:  bash /mnt/c/homelab/string-art-studio/deploy/media-pc/install.sh
#
# The checkout lives at /mnt/c/homelab/string-art-studio (C:\homelab\string-art-studio), next to
# the other apps. Data stays in ./data inside it, which Backrest already covers through C:\homelab.
# The container binds 127.0.0.1:8050; Tailscale Serve (owner step, see tailscale-serve.ps1)
# publishes it at https://desktop-4l95il9.taildc59f0.ts.net/stringart.
set -euo pipefail
APP=/mnt/c/homelab/string-art-studio
cd "$APP"
if [ ! -f .env ]; then
    cat > .env <<'ENV'
SAS_ROOT_PATH=/stringart
SAS_BIND=127.0.0.1
SAS_PORT=8050
SAS_DATA=./data
SAS_WORKERS=2
TZ=America/Chicago
COMPOSE_PROFILES=voice
SAS_PIPER_URL=http://piper:5000
SAS_PIPER_VOICE=en_US-lessac-medium
ENV
    echo "wrote .env"
fi
# v2 added the Piper voice container; older .env files get the two lines that switch it on.
if ! grep -q '^SAS_PIPER_URL=' .env; then
    printf 'COMPOSE_PROFILES=voice\nSAS_PIPER_URL=http://piper:5000\nSAS_PIPER_VOICE=en_US-lessac-medium\n' >> .env
    echo "enabled the voice profile in .env"
fi
mkdir -p data
docker compose up -d --build
for _ in $(seq 1 30); do
    if curl -fsS -m 3 http://127.0.0.1:8050/healthz >/dev/null 2>&1; then
        echo "String Art Studio is up on 127.0.0.1:8050"
        exit 0
    fi
    sleep 2
done
echo "container did not answer on :8050; docker compose logs follow"
docker compose logs --tail=50
exit 1
