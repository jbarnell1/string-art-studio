# Owner step on the media PC (desktop-4l95il9): publish String Art Studio on the tailnet.
# On this box the command ran over SSH as the homelab user without elevation (2026-11-05). If it
# refuses, run it from an admin PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File C:\homelab\string-art-studio\deploy\media-pc\tailscale-serve.ps1
#
# This mounts the app beside Immich (/) and Highlights (/highlights) on the same host, at
#   https://desktop-4l95il9.taildc59f0.ts.net/stringart
# Tailscale strips the /stringart prefix before forwarding; the container knows its prefix from
# SAS_ROOT_PATH=/stringart in .env, so the PWA scope and every URL come out right.
$ErrorActionPreference = 'Stop'
tailscale serve --bg --set-path /stringart http://127.0.0.1:8050
tailscale serve status
