#!/bin/bash
# Usage: pull-backup.sh <name> <user@host>   (launchd runs it hourly; see setup-mac.sh)
# Copies the server's restic repo to this Mac at most once a day, whenever both are awake.
# The key only allows read-only rsync of ~/backups/restic (rrsync -ro).
set -euo pipefail
name=$1 host=$2

dir="$HOME/Backups/$name"
stamp="$dir/last-pull.txt"
mkdir -p "$dir/restic"

if [[ -f $stamp ]] && (( $(date +%s) - $(stat -f %m "$stamp") < 20 * 3600 )); then
  exit 0
fi

# IdentitiesOnly/IdentityAgent: never fall back to the agent's unrestricted keys.
/opt/homebrew/bin/rsync -a --delete \
  -e "ssh -i $HOME/.ssh/${name}_backup_pull -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes -o ConnectTimeout=10" \
  "$host:./" "$dir/restic/"

date '+%F %T pulled' >"$stamp"
