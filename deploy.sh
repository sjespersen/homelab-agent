#!/usr/bin/env bash
# From your computer: copy this repo to the server and apply it (stacks + backup timer).
# ./deploy.sh --update also takes a backup first, then pulls newer images for every stack.
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.env ]] || { echo "Copy config.example.env to config.env and fill it in first." >&2; exit 1; }
source config.env
host="$SERVER_USER@$SERVER_HOST"

pull=missing
if [[ ${1:-} == --update ]]; then
  pull=always
  echo "== backup before updating"
  ssh "$host" 'systemctl --user start homelab-backup.service'
fi

rsync -a --delete --exclude .git --exclude mac ./ "$host:homelab-agent/"
ssh "$host" "PULL=$pull bash ~/homelab-agent/install.sh"
