#!/usr/bin/env bash
# Runs on the server as your user (no sudo); setup.sh and deploy.sh call it after copying the repo.
# Syncs the enabled modules' compose stacks into /opt/stacks, starts them, stops disabled ones,
# and installs the backup timer.
# install.sh --render <dir>: only write the stack files into <dir> (for tests; needs no Docker).
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
source "$repo/lib.sh"
load_config

stacks_dir=/opt/stacks
render_only=
if [[ ${1:-} == --render ]]; then
  stacks_dir=$2
  render_only=1
fi

# Settings the server can tell us itself; config.env can still set them.
if [[ -z $render_only ]]; then
  if [[ -z ${LAN_IP:-} ]]; then
    LAN_IP=$(ip -4 route get 1.1.1.1 | sed -n 's/.* src \([0-9.]*\).*/\1/p')
  fi
  if [[ -z ${TAILSCALE_IP:-} ]] && command -v tailscale >/dev/null; then
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || true)
  fi
fi
PUID=${PUID:-$(id -u)}
PGID=${PGID:-$(id -g)}

modules=($(enabled_modules))

# Each module with a web UI gets a Caddy site: <host>.<DOMAIN> when Pi-hole gives out the
# names, otherwise its own port on the server.
names="$NAME.$DOMAIN"
sites=""
landing=""
links=""
if has_module pihole; then
  links+="  <p>*.$DOMAIN names need Pi-hole as DNS (Tailscale on).</p>"$'\n'
fi
for m in "${modules[@]}"; do
  [[ -f $repo/stacks/$m/landing.caddy ]] && landing+="$(<"$repo/stacks/$m/landing.caddy")"$'\n'
  while read -r site host port file; do
    [[ $port != - ]] || port=""
    if has_module pihole; then
      names+=" $host.$DOMAIN"
      address="http://$host.$DOMAIN"
      url="http://$host.$DOMAIN"
    elif [[ -n $port ]]; then
      address="http://:$port"
      url="http://{{.Host}}:$port"  # a Caddy template: whatever address the page was opened at
    else
      continue
    fi
    sites+="$address {"$'\n'"$(sed 's/^/\t/' "$repo/stacks/$m/$file")"$'\n}\n\n'
    link=$(module_site_var "$m" "$site" LINK)
    links+="  <a href=\"${link:-$url}\">$(module_site_var "$m" "$site" TITLE) <span>— $(module_site_var "$m" "$site" DESCRIPTION)</span></a>"$'\n'
  done < <(module_sites "$m")
done

# Pi-hole answers these names with the server's LAN or Tailscale address; dnsmasq's
# localise-queries picks the one matching the interface the query came in on.
PIHOLE_HOSTS="$LAN_IP $names"
[[ -z ${TAILSCALE_IP:-} ]] || PIHOLE_HOSTS+="; $TAILSCALE_IP $names"

mkdir -p "$stacks_dir"
for m in "${modules[@]}"; do
  dir=$stacks_dir/$m
  rsync -a --exclude module.env --exclude '*.caddy' --exclude '*.sh' --exclude backup-excludes.txt \
    --exclude landing.html --exclude README.md --exclude /host/ "$repo/stacks/$m/" "$dir/"
  # Create data dirs ourselves; Docker would create them owned by root.
  if grep -q '\./data' "$dir/compose.yaml"; then mkdir -p "$dir/data"; fi

  host=$(module_var "$m" HOST)
  SITE_HOST="" SITE_URL=""
  if [[ -n $host ]] && has_module pihole; then
    SITE_HOST=$host.$DOMAIN SITE_URL=http://$host.$DOMAIN/
  elif [[ -n $host ]]; then
    SITE_HOST=$NAME.local SITE_URL=http://$NAME.local:$(module_var "$m" PORT)/
  fi
  # Optional parts that only make sense together with another module.
  COMPOSE_FILE=compose.yaml
  for f in "$repo/stacks/$m"/compose.with-*.yaml; do
    [[ -f $f ]] || continue
    other=${f##*/compose.with-}
    if has_module "${other%.yaml}"; then COMPOSE_FILE+=":${f##*/}"; fi
  done
  # Compose reads these from .env, so a plain `docker compose ...` in the directory works too.
  {
    for v in NAME DOMAIN LAN_IP TZ PUID PGID PIHOLE_HOSTS SITE_HOST SITE_URL COMPOSE_FILE; do
      printf '%s="%s"\n' "$v" "${!v}"
    done
    # The module's own settings (ENV in module.env), with the defaults module.env gives them.
    for v in $(module_var "$m" ENV); do
      printf '%s="%s"\n' "$v" "$(module_var "$m" "$v")"
    done
  } >"$dir/.env"
done

caddy=$stacks_dir/caddy
printf '%s' "$sites" >"$caddy/conf/sites.caddy"
printf '%s' "$landing" >"$caddy/conf/landing.caddy"
mkdir -p "$caddy/site"
LINKS=$links awk '/<!-- LINKS/ { printf "%s", ENVIRON["LINKS"]; next } { print }' \
  "$repo/stacks/caddy/landing.html" >"$caddy/site/index.html"

[[ -z $render_only ]] || exit 0

# Modules switched off in config.env: stop them. Their data stays in /opt/stacks.
for dir in "$stacks_dir"/*/; do
  m=$(basename "$dir")
  if [[ -f $repo/stacks/$m/module.env && " ${modules[*]} " != *" $m "* && -f $dir/compose.yaml ]]; then
    echo "== $m (disabled: stopping, data kept)"
    (cd "$dir" && docker compose down --remove-orphans)
  fi
done

for m in "${modules[@]}"; do
  echo "== $m"
  if [[ -f $repo/stacks/$m/before-up.sh ]]; then
    bash "$repo/stacks/$m/before-up.sh" "$stacks_dir/$m"
  fi
  # From inside the directory: compose resolves COMPOSE_FILE in .env against the current one.
  (cd "$stacks_dir/$m" && docker compose up -d --pull "${PULL:-missing}" --remove-orphans)
  if [[ -f $repo/stacks/$m/after-up.sh ]]; then
    bash "$repo/stacks/$m/after-up.sh" "$stacks_dir/$m"
  fi
done

[[ ${PULL:-} != always ]] || docker image prune -f  # images the update replaced

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
