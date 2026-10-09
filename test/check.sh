#!/usr/bin/env bash
# Renders config.example.env with several MODULES combinations and checks that every compose
# file parses and (when Docker is running) that Caddy accepts its config. Also checks that
# every image is pinned by digest, and that the AI switcher page scripts parse. CI runs this.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Rebuilds must install exactly what the repo says: every image pinned by digest.
# Images under local/ are built on the server from a pinned source instead.
if grep -n '^ *image:' "$repo"/stacks/*/compose.yaml | grep -v '@sha256:[0-9a-f]\{64\}' \
  | grep -v 'image: *local/'; then
  echo "The images above aren't pinned by digest; run ./update-images.sh." >&2
  exit 1
fi

# The switcher's pages are JavaScript inside a Python file: check that their scripts parse.
if docker info >/dev/null 2>&1; then
  API_KEY=x python3 -B - "$repo/stacks/ai/switcher/app.py" "$tmp" <<'PY'
import importlib.util, re, sys
spec = importlib.util.spec_from_file_location("app", sys.argv[1])
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
for name in ("PAGE", "LOGIN"):
    with open(f"{sys.argv[2]}/{name}.js", "w") as f:
        f.write("\n".join(re.findall(r"<script>(.*?)</script>", getattr(app, name), re.S)))
PY
  for page in PAGE LOGIN; do
    docker run --rm -v "$tmp:/s:ro" node:22-alpine node --check "/s/$page.js" \
      || { echo "The switcher's $page script doesn't parse." >&2; exit 1; }
  done
fi

combos=(
  "pihole hermes n8n uptime-kuma beszel"
  "hermes n8n uptime-kuma beszel ai"
  "ai"
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
