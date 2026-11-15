# Example: publish String Art Studio on your tailnet with Tailscale Serve (Windows host; the
# same two commands work in a Linux shell). Run it from an admin PowerShell if Tailscale refuses:
#
#   powershell -ExecutionPolicy Bypass -File deploy\media-pc\tailscale-serve.ps1
#
# This mounts the app under a path on the host's tailnet name, beside whatever else it serves:
#   https://<host>.<tailnet>.ts.net/stringart
# Tailscale strips the /stringart prefix before forwarding; the container knows its prefix from
# SAS_ROOT_PATH=/stringart in .env, so the PWA scope and every URL come out right.
$ErrorActionPreference = 'Stop'
tailscale serve --bg --set-path /stringart http://127.0.0.1:8050
tailscale serve status
