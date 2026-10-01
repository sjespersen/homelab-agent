#!/usr/bin/env bash
# Nightly snapshot of the app data in /opt/stacks into a local restic repo.
# Your Mac pulls a copy of the repo whenever it's awake (mac/pull-backup.sh).
set -euo pipefail

export RESTIC_REPOSITORY="$HOME/backups/restic"
export RESTIC_PASSWORD_FILE="$HOME/.config/restic/password"
here="$(cd "$(dirname "$0")" && pwd)"

# Containers with SQLite databases are stopped so the snapshot is consistent (~15s).
stop=(hermes n8n uptime-kuma beszel)
running=()
for c in "${stop[@]}"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == true ]]; then
    running+=("$c")
  fi
done

restart() {
  if [[ ${#running[@]} -gt 0 ]]; then docker start "${running[@]}" >/dev/null; fi
}
trap restart EXIT

if [[ ${#running[@]} -gt 0 ]]; then docker stop "${running[@]}" >/dev/null; fi
restic backup --quiet --exclude-file "$here/excludes.txt" /opt/stacks
restart
trap - EXIT

restic forget --quiet --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
