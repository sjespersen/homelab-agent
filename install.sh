#!/usr/bin/env bash
# Runs on the server as your user (no sudo); deploy.sh calls it after copying the repo.
# Syncs compose stacks into /opt/stacks, starts them, and installs the backup timer.
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"

# Compose files and the Caddyfile read these from the environment.
set -a
source "$repo/config.env"
PUID=$(id -u)
PGID=$(id -g)
set +a
# Names for the services behind Caddy. Each name gets a LAN and a Tailscale address;
# dnsmasq's localise-queries answers with the one matching the interface asked on.
names="$NAME.$DOMAIN"
for n in pihole hermes n8n status server; do names+=" $n.$DOMAIN"; done
PIHOLE_HOSTS="$LAN_IP $names"
[[ -z $TAILSCALE_IP ]] || PIHOLE_HOSTS+="; $TAILSCALE_IP $names"
export PIHOLE_HOSTS

# Order matters: Pi-hole must release port 80 before Caddy takes it.
stacks=(pihole hermes uptime-kuma n8n beszel caddy)

for s in "${stacks[@]}"; do
  rsync -a "$repo/stacks/$s/" "/opt/stacks/$s/"
  # Create data dirs ourselves; Docker would create them owned by root.
  mkdir -p "/opt/stacks/$s/data"
done
rmdir /opt/stacks/pihole/data 2>/dev/null || true  # Pi-hole uses etc-pihole/

for s in "${stacks[@]}"; do
  echo "== $s"
  docker compose --project-directory "/opt/stacks/$s" up -d --pull missing --remove-orphans
done

# Containers don't notice edited config files; reload Caddy so Caddyfile changes apply.
docker exec caddy caddy reload --config /etc/caddy/Caddyfile

# The Beszel hub only writes its private key (on first start); the agent needs the public half.
beszel=/opt/stacks/beszel/data
for _ in $(seq 30); do [[ -f $beszel/id_ed25519 ]] && break; sleep 1; done
if [[ -f $beszel/id_ed25519 && ! -f $beszel/id_ed25519.pub ]]; then
  ssh-keygen -y -f "$beszel/id_ed25519" >"$beszel/id_ed25519.pub"
fi

echo "== backup timer"
if ! command -v restic >/dev/null; then
  echo "restic not installed yet: run bootstrap.sh first. Skipping." >&2
  exit 0
fi
export RESTIC_REPOSITORY="$HOME/backups/restic" RESTIC_PASSWORD_FILE="$HOME/.config/restic/password"
if [[ ! -f $RESTIC_PASSWORD_FILE ]]; then
  install -d -m 700 "$(dirname "$RESTIC_PASSWORD_FILE")"
  (umask 077; head -c 32 /dev/urandom | base64 >"$RESTIC_PASSWORD_FILE")
fi
if [[ ! -f $RESTIC_REPOSITORY/config ]]; then
  restic init --quiet
fi
units=~/.config/systemd/user
mkdir -p "$units"
sed "s|@REPO@|$repo|" "$repo/backup/homelab-backup.service" >"$units/homelab-backup.service"
sed "s|@TIME@|$BACKUP_TIME|; s|@TZ@|$TZ|" "$repo/backup/homelab-backup.timer" >"$units/homelab-backup.timer"
systemctl --user daemon-reload
systemctl --user enable --now homelab-backup.timer
systemctl --user list-timers homelab-backup.timer --no-pager
