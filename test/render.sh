#!/usr/bin/env bash
# Usage: test/render.sh <config.env> <out-dir>
# Renders what install.sh would put in /opt/stacks for that config, then the final compose
# config of every stack (<out-dir>/<module>.compose.yaml). Needs Docker's compose CLI, no daemon.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
config=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
out=$2
rm -rf "$out"
mkdir -p "$out"
out=$(cd "$out" && pwd)

# install.sh reads config.env from its own directory, so render from a copy of the repo.
copy=$(mktemp -d)
trap 'rm -rf "$copy"' EXIT
rsync -a --exclude .git "$repo/" "$copy/"
cp "$config" "$copy/config.env"
PUID=1000 PGID=1000 bash "$copy/install.sh" --render "$out/stacks"

for dir in "$out"/stacks/*/; do
  m=$(basename "$dir")
  # Compose reads .env (and COMPOSE_FILE in it) like it would on the server.
  (cd "$dir" && env -i PATH="$PATH" HOME="$HOME" docker compose config) \
    | sed "s|$out/stacks|/opt/stacks|g" >"$out/$m.compose.yaml"
done
