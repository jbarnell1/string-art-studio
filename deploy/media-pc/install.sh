#!/usr/bin/env bash
# Example: install or update String Art Studio on a home server that runs Docker (written for a
# Windows PC with Docker inside WSL, works on any Linux host). Run it from the checkout:
#
#   bash deploy/media-pc/install.sh
#
# Data stays in ./data inside the checkout, so back that folder up. The container binds
# 127.0.0.1:8050; Tailscale Serve (see tailscale-serve.ps1) publishes it on the tailnet at
# https://<host>.<tailnet>.ts.net/stringart. Set SAS_ROOT_PATH to another prefix, or to nothing,
# in .env if you publish it differently.
set -euo pipefail
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$APP"
if [ ! -f .env ]; then
    cat > .env <<ENV
SAS_ROOT_PATH=/stringart
SAS_BIND=127.0.0.1
SAS_PORT=8050
SAS_DATA=./data
SAS_WORKERS=2
TZ=${TZ:-UTC}
COMPOSE_PROFILES=voice
SAS_PIPER_URL=http://piper:5000
SAS_PIPER_VOICE=en_US-lessac-medium
ENV
    echo "wrote .env"
fi
# v2 added the Piper voice container; older .env files get the lines that switch it on.
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
