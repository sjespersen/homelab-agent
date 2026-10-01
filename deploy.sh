#!/usr/bin/env bash
# From your computer: copy this repo to the server and apply it (stacks + backup timer).
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.env ]] || { echo "Copy config.example.env to config.env and fill it in first." >&2; exit 1; }
source config.env
host="$SERVER_USER@$SERVER_HOST"

rsync -a --delete --exclude .git --exclude mac ./ "$host:homelab-agent/"
ssh "$host" 'bash ~/homelab-agent/install.sh'
