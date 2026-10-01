#!/usr/bin/env bash
# Runs on the server as your user: what's running, where to find it, how the backups are doing.
# From your computer: ./deploy.sh --status
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
source "$repo/lib.sh"
load_config
if [[ -z ${LAN_IP:-} ]]; then
  LAN_IP=$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')
fi

printf '\nLanding page: http://%s.local  (or http://%s)\n\n' "$NAME" "$LAN_IP"
for m in $(enabled_modules); do
  host=$(module_var "$m" HOST)
  link=$(module_var "$m" LINK)
  if [[ -n $link ]]; then url=http://$NAME.local$link
  elif [[ -z $host ]]; then url=""
  elif has_module pihole; then url=http://$host.$DOMAIN
  else url=http://$NAME.local:$(module_var "$m" PORT)
  fi
  printf '%-12s %s\n' "$m" "$url"
  if [[ -f /opt/stacks/$m/compose.yaml ]]; then
    (cd "/opt/stacks/$m" && docker compose ps -a --format '{{.Name}}: {{.Status}}') | sed 's/^/  /'
  else
    echo "  not installed yet (run ./deploy.sh)"
  fi
done

printf '\nBackups\n'
if systemctl --user is-failed --quiet homelab-backup.service; then
  echo "  LAST RUN FAILED: journalctl --user -u homelab-backup.service"
fi
echo "  next: $(systemctl --user show homelab-backup.timer -p NextElapseUSecRealtime --value)"
if command -v restic >/dev/null && [[ -f ~/.config/restic/password ]]; then
  RESTIC_REPOSITORY=~/backups/restic RESTIC_PASSWORD_FILE=~/.config/restic/password \
    restic snapshots --latest 1 --compact 2>/dev/null | sed -n '3p' | sed 's/^/  latest: /'
fi

printf '\nDisk\n'
df -h /opt/stacks | sed -n '2p' | awk '{ print "  " $3 " used of " $2 " (" $5 ")" }'
