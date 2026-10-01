#!/usr/bin/env bash
# Nightly snapshot of the app data in /opt/stacks into a local restic repo.
# Your Mac pulls a copy of the repo whenever it's awake (mac/pull-backup.sh).
set -euo pipefail

export RESTIC_REPOSITORY="$HOME/backups/restic"
export RESTIC_PASSWORD_FILE="$HOME/.config/restic/password"
repo="$(cd "$(dirname "$0")/.." && pwd)"
source "$repo/lib.sh"

# Containers with SQLite databases are stopped so the snapshot is consistent (~15s).
# Each module lists its own (BACKUP_STOP) and its caches (backup-excludes.txt).
stop=()
excludes=()
for m in $(all_modules); do
  stop+=($(module_var "$m" BACKUP_STOP))
  f=$repo/stacks/$m/backup-excludes.txt
  [[ ! -f $f ]] || excludes+=(--exclude-file "$f")
done
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
restic backup --quiet "${excludes[@]}" /opt/stacks
restart
trap - EXIT

restic forget --quiet --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
