#!/usr/bin/env bash
# Nightly snapshot of the app data in /opt/stacks into a local restic repo.
# Your Mac pulls a copy of the repo whenever it's awake (mac/pull-backup.sh).
set -euo pipefail

export RESTIC_REPOSITORY="$HOME/backups/restic"
export RESTIC_PASSWORD_FILE="$HOME/.config/restic/password"
repo="$(cd "$(dirname "$0")/.." && pwd)"
source "$repo/lib.sh"

# A live SQLite database can be caught half-written, so each module either lists containers
# to stop for the snapshot (BACKUP_STOP, ~15s) or databases to copy while it keeps running
# (BACKUP_SQLITE: globs under /opt/stacks/<module>, copied to its .sqlite-backup/ folder,
# the live files left out). Caches are listed in backup-excludes.txt.
stop=()
excludes=()
copies=()
for m in $(all_modules); do
  stop+=($(module_var "$m" BACKUP_STOP))
  f=$repo/stacks/$m/backup-excludes.txt
  [[ ! -f $f ]] || excludes+=(--exclude-file "$f")
  read -ra globs <<<"$(module_var "$m" BACKUP_SQLITE)"
  for db in $(cd "/opt/stacks/$m" 2>/dev/null && for g in "${globs[@]}"; do compgen -G "$g" || true; done); do
    copies+=("/opt/stacks/$m" "$db")
    for suffix in "" -wal -shm -journal; do excludes+=(--exclude "/opt/stacks/$m/$db$suffix"); done
  done
done
running=()
for c in "${stop[@]}"; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == true ]]; then
    running+=("$c")
  fi
done

restart() {
  if [[ ${#running[@]} -gt 0 ]]; then docker start "${running[@]}" >/dev/null; fi
  rm -rf /opt/stacks/*/.sqlite-backup
}
trap restart EXIT

for ((i = 0; i < ${#copies[@]}; i += 2)); do
  dir=${copies[i]} db=${copies[i + 1]}
  mkdir -p "$dir/.sqlite-backup/$(dirname "$db")"
  python3 -c 'import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=60)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()' "$dir/$db" "$dir/.sqlite-backup/$db"
done
if [[ ${#running[@]} -gt 0 ]]; then docker stop "${running[@]}" >/dev/null; fi
restic backup --quiet "${excludes[@]}" /opt/stacks
restart
trap - EXIT

restic forget --quiet --prune --keep-daily 7 --keep-weekly 4 --keep-monthly 6
