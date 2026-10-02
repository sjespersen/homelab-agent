#!/usr/bin/env bash
# Renders config.example.env with several MODULES combinations and checks that every compose
# file parses and (when Docker is running) that Caddy accepts its config. Also checks that
# every image is pinned by digest. CI runs this.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Rebuilds must install exactly what the repo says: every image pinned by digest.
if grep -n '^ *image:' "$repo"/stacks/*/compose.yaml | grep -v '@sha256:[0-9a-f]\{64\}'; then
  echo "The images above aren't pinned by digest; run ./update-images.sh." >&2
  exit 1
fi

combos=(
  "pihole hermes n8n uptime-kuma beszel"
  "hermes n8n uptime-kuma beszel"
  "hermes"
  "n8n"
  "pihole"
  ""
)
for modules in "${combos[@]}"; do
  echo "== MODULES=\"$modules\""
  { grep -v '^MODULES=' "$repo/config.example.env"; echo "MODULES=\"$modules\""; } >"$tmp/config.env"
  "$repo/test/render.sh" "$tmp/config.env" "$tmp/out"
  if docker info >/dev/null 2>&1; then
    docker run --rm -e NAME=homelab -e DOMAIN=home.arpa -e LAN_IP=192.168.1.10 \
      -v "$tmp/out/stacks/caddy/conf:/etc/caddy:ro" caddy:2 \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
      || { echo "Caddy rejects the config for MODULES=\"$modules\"" >&2; exit 1; }
  fi
done
echo "All combinations render."
