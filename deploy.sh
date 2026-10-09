#!/usr/bin/env bash
# From your computer: copy this repo to the server and apply it (stacks + backup timer).
# For a new server, or after changing MODULES, use ./setup.sh: it also runs bootstrap.sh.
#   ./deploy.sh            apply config.env and the stacks
#   ./deploy.sh --update   take a backup, move the image pins to the newest builds
#                          (update-images.sh), apply; then commit the changed compose files
#   ./deploy.sh --status   what's running, where, and how the backups are doing
# Another server: CONFIG=config.<name>.env ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"
config=${CONFIG:-config.env}
[[ -f $config ]] || { echo "No $config yet: run ./setup.sh first." >&2; exit 1; }
source "$config"
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
  ./update-images.sh
fi

# The server gets its own config as config.env, and no other server's.
rsync -a --delete --exclude .git --exclude mac --include config.example.env --exclude 'config*.env' \
  ./ "$host:homelab-agent/"
rsync -a "$config" "$host:homelab-agent/config.env"
ssh "$host" "PULL=$pull bash ~/homelab-agent/install.sh"

if [[ $pull == always ]] && ! git diff --quiet -- stacks; then
  echo
  echo "Running the new versions. If they work, keep them:  git commit -am 'Update images'"
  echo "If not, go back:  git checkout -- stacks && ./deploy.sh"
fi
