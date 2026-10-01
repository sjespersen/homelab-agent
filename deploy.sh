#!/usr/bin/env bash
# From your computer: copy this repo to the server and apply it (stacks + backup timer).
# For a new server, or after changing MODULES, use ./setup.sh: it also runs bootstrap.sh.
#   ./deploy.sh            apply config.env and the stacks
#   ./deploy.sh --update   take a backup first, then pull newer images for every stack
#   ./deploy.sh --status   what's running, where, and how the backups are doing
set -euo pipefail
cd "$(dirname "$0")"
[[ -f config.env ]] || { echo "No config.env yet: run ./setup.sh first." >&2; exit 1; }
source config.env
host="$SERVER_USER@$SERVER_HOST"

if [[ ${1:-} == --status ]]; then
  ssh "$host" 'bash ~/homelab-agent/status.sh'
  if [[ -f ~/Backups/$NAME/last-pull.txt ]]; then
    echo "  this Mac: $(cat ~/Backups/"$NAME"/last-pull.txt)"
  fi
  exit
fi

pull=missing
if [[ ${1:-} == --update ]]; then
  pull=always
  echo "== backup before updating"
  ssh "$host" 'systemctl --user start homelab-backup.service'
fi

rsync -a --delete --exclude .git --exclude mac ./ "$host:homelab-agent/"
ssh "$host" "PULL=$pull bash ~/homelab-agent/install.sh"
