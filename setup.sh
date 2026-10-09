#!/usr/bin/env bash
# Sets up a fresh Ubuntu server, or brings an existing one in line with config.env.
# Safe to re-run: every step checks what's already done, so it also resumes after an error.
#   ./setup.sh           from your computer, over SSH (macOS or Linux)
#   ./setup.sh --local   on the server itself
# Another server: CONFIG=config.<name>.env ./setup.sh (one config file per server).
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
cd "$repo"
source "$repo/lib.sh"

local_mode=
[[ ${1:-} == --local ]] && local_mode=1

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

# ask <variable> <question> [default]
ask() {
  local answer
  read -r -p "$2${3:+ [$3]}: " answer </dev/tty
  printf -v "$1" '%s' "${answer:-${3:-}}"
}

# confirm <question> <Y|N>: Y or N is the answer when you just press Enter.
confirm() {
  local answer hint=y/N
  [[ $2 == Y ]] && hint=Y/n
  read -r -p "$1 [$hint] " answer </dev/tty
  answer=${answer:-$2}
  [[ $answer == [Yy]* ]]
}

# on_server <command>: runs a shell command on the server. on_server_tty: same, interactive.
on_server() {
  if [[ -n $local_mode ]]; then bash -c "$1"; else ssh "$SERVER_USER@$SERVER_HOST" "$1"; fi
}
on_server_tty() {
  if [[ -n $local_mode ]]; then bash -c "$1"; else ssh -t "$SERVER_USER@$SERVER_HOST" "$1"; fi
}

# Runs on the server: prints what it can find out about itself as shell assignments.
detect() {
  local dev
  . /etc/os-release 2>/dev/null || true
  printf 'D_OS=%q\n' "${PRETTY_NAME:-unknown}"
  printf 'D_UBUNTU=%q\n' "$([[ ${ID:-} == ubuntu ]] && echo yes)"
  printf 'D_HOSTNAME=%q\n' "$(hostname)"
  printf 'D_ARCH=%q\n' "$(uname -m)"
  printf 'D_RAM_MB=%q\n' "$(awk '/MemTotal/ { print int($2 / 1024) }' /proc/meminfo)"
  printf 'D_TZ=%q\n' "$(timedatectl show -p Timezone --value 2>/dev/null)"
  printf 'D_LAN_IP=%q\n' "$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
  dev=$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')
  printf 'D_NET4=%q\n' "$(ip -4 route show dev "$dev" scope link proto kernel 2>/dev/null | awk '{ print $1; exit }')"
  # The global IPv6 /64 your router hands out, if any.
  printf 'D_NET6=%q\n' "$(ip -6 route show dev "$dev" 2>/dev/null | awk '$1 ~ /^[23][0-9a-f]*:.*\/64$/ { print $1; exit }')"
  printf 'D_WIFI=%q\n' "$([[ -d /sys/class/net/$dev/wireless ]] && echo "$dev")"
  printf 'D_IT87=%q\n' "$(grep -qs '^it8' /sys/class/hwmon/hwmon*/name && echo yes)"
  printf 'D_KEYS=%q\n' "$([[ -s ~/.ssh/authorized_keys ]] && echo yes)"
  printf 'D_NVIDIA=%q\n' "$(grep -qs 0x10de /sys/bus/pci/devices/*/vendor && echo yes)"
}

# Settings from an earlier run are the defaults this time.
if [[ -f $config_file ]]; then
  source "$config_file"
  say "Using your earlier answers ($config_file) as defaults"
else
  say "Setting up a new server"
fi

# 1. Reach the server ---------------------------------------------------------------------
if [[ -n $local_mode ]]; then
  SERVER_USER=$(id -un)
  SERVER_HOST=${SERVER_HOST:-$(hostname).local}
  rdir=$(printf %q "$repo")
else
  SERVER_HOST=${SERVER_HOST:-}
  ask SERVER_HOST "Server address (e.g. homelab.local or its IP)" "$SERVER_HOST"
  while [[ -z $SERVER_HOST ]]; do ask SERVER_HOST "Server address"; done
  ask SERVER_USER "Your user on the server" "${SERVER_USER:-$(id -un)}"
  rdir=homelab-agent  # in the user's home on the server
  if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$SERVER_USER@$SERVER_HOST" true 2>/dev/null; then
    say "SSH key"
    note "The server will only accept SSH keys, so this copies yours over (type the server password once)."
    if ! ls ~/.ssh/id_*.pub >/dev/null 2>&1; then
      note "You have no SSH key yet; creating one. A passphrase protects it if your computer is stolen."
      ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519
    fi
    ssh-copy-id "$SERVER_USER@$SERVER_HOST"
  fi
fi

# 2. Look at the server --------------------------------------------------------------------
say "Checking the server"
if [[ -n $local_mode ]]; then
  eval "$(detect)"
else
  eval "$(ssh "$SERVER_USER@$SERVER_HOST" bash -s <<<"$(declare -f detect); detect")"
fi
note "$D_OS, $D_ARCH, $D_RAM_MB MB RAM, LAN IP ${D_LAN_IP:-unknown}${D_NVIDIA:+, NVIDIA GPU}"
if [[ $D_UBUNTU != yes ]]; then
  note "This was written for Ubuntu Server; other systems will probably fail in bootstrap."
  confirm "   Continue anyway?" N || exit 1
fi
if [[ $D_ARCH != x86_64 && $D_ARCH != aarch64 ]]; then
  note "Some images may not exist for $D_ARCH."
fi
if (( ${D_RAM_MB:-0} < 3500 )); then
  note "Less than 4 GB RAM: consider leaving out n8n and Beszel."
fi
if [[ -n $local_mode && $D_KEYS != yes ]]; then
  say "SSH key"
  note "Bootstrap turns off SSH password logins, so add your computer's key first."
  ask gh_user "   GitHub username to import your keys from (empty: paste a public key instead)"
  if [[ -n $gh_user ]]; then
    ssh-import-id "gh:$gh_user"
  else
    pubkey=""
    while [[ $pubkey != ssh-* && $pubkey != ecdsa-* && $pubkey != sk-* ]]; do
      ask pubkey "   Public key (one line: ssh-ed25519 AAAA...; on your computer: cat ~/.ssh/id_ed25519.pub)"
    done
    install -d -m 700 ~/.ssh
    printf '%s\n' "$pubkey" >>~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
  fi
fi

# 3. Questions -----------------------------------------------------------------------------
say "Your setup"
ask NAME "Short name for the box (landing page at http://<name>.local)" "${NAME:-$D_HOSTNAME}"
if [[ -z ${MODULES+set} ]]; then
  MODULES=$(echo $(default_modules))
  [[ -z $D_NVIDIA ]] || MODULES+=" ai"
fi
echo "Which services? (Caddy, firewall, Tailscale and backups are always on)"
chosen=""
for m in $(optional_modules); do
  default=N
  has_module "$m" && default=Y
  if confirm "   $(module_var "$m" TITLE): $(module_var "$m" DESCRIPTION)?" "$default"; then
    chosen+="$m "
  fi
done
MODULES=${chosen% }
if [[ -n $D_IT87 || -n ${IT87_FAN_MIN_PWM:-} ]]; then
  ask IT87_FAN_MIN_PWM "Fan minimum speed, 0-255 (empty: leave as is; see extras/it87-fan)" "${IT87_FAN_MIN_PWM:-}"
fi

if [[ -n $D_WIFI || -n ${WIFI_WATCHDOG_IFACE:-} ]]; then
  ask WIFI_WATCHDOG_IFACE "Wi-Fi interface to watch and reconnect when stuck (empty: off; see extras/wifi-watchdog)" "${WIFI_WATCHDOG_IFACE-$D_WIFI}"
fi

if has_module ai; then
  if (( ${D_RAM_MB:-0} < 30000 )); then
    note "Strata (Qwen3.8-Flash-Next) needs 32 GB RAM or more; the 27B model runs on the GPU alone."
  fi
  ask AI_MODELS_DIR "Folder for the AI models (~100 GB and up)" "${AI_MODELS_DIR:-/srv/models}"
  note "Partitions on the server (lsblk):"
  on_server "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS" | sed 's/^/     /'
  ask AI_MODELS_DEVICE "Partition to mount there, e.g. /dev/nvme0n1p1 (empty: leave as is; never formatted)" "${AI_MODELS_DEVICE:-}"
fi

# Everything else is detected or has a sensible default; the config file is yours to edit.
LAN_IP=${LAN_IP:-$D_LAN_IP}
TAILSCALE_IP=${TAILSCALE_IP:-}
TRUSTED_NETS=${TRUSTED_NETS:-$(echo $D_NET4 $D_NET6 fe80::/10)}
# Ubuntu installs default to UTC; your computer's zone is the better guess then.
if [[ -z ${TZ:-} && ( -z $D_TZ || $D_TZ == *UTC ) && -z $local_mode ]]; then
  D_TZ=$(readlink /etc/localtime 2>/dev/null | sed -n 's|.*zoneinfo/||p')
fi
TZ=${TZ:-${D_TZ:-UTC}}
DOMAIN=${DOMAIN:-home.arpa}
BACKUP_TIME=${BACKUP_TIME:-04:00}
IT87_FAN_MIN_PWM=${IT87_FAN_MIN_PWM:-}
WIFI_WATCHDOG_IFACE=${WIFI_WATCHDOG_IFACE:-}
MDNS_INTERFACES=${MDNS_INTERFACES:-}
WAKE_ON_LAN_IFACE=${WAKE_ON_LAN_IFACE:-}
WAKE_TARGETS=${WAKE_TARGETS:-}
WAKE_ON_LAN_VIA=${WAKE_ON_LAN_VIA:-}
if [[ -n $WAKE_ON_LAN_IFACE && -z ${WAKE_ON_LAN_MAC:-} ]]; then
  WAKE_ON_LAN_MAC=$(on_server "cat /sys/class/net/$WAKE_ON_LAN_IFACE/address" 2>/dev/null || true)
fi
WAKE_ON_LAN_MAC=${WAKE_ON_LAN_MAC:-}
AI_MODEL=${AI_MODEL:-qwen27b}
AI_MODELS_DIR=${AI_MODELS_DIR:-/srv/models}
AI_MODELS_DEVICE=${AI_MODELS_DEVICE:-}
AI_CONTEXT=${AI_CONTEXT:-32768}
AI_STRATA_CONTEXT=${AI_STRATA_CONTEXT:-131072}
AI_QWEN27B_QUANT=${AI_QWEN27B_QUANT:-UD-Q3_K_XL}
AI_STRATA_QUANT=${AI_STRATA_QUANT:-IQ3_S}
AI_LEDS=${AI_LEDS:-}
AI_LEDS_ON_MODE=${AI_LEDS_ON_MODE:-Rainbow wave}
AI_LEDS_ON_SPEED=${AI_LEDS_ON_SPEED:-}

# config.example.env with your values: same order, same comments.
tmp=$(mktemp)
while IFS= read -r line; do
  if [[ $line =~ ^([A-Z0-9_]+)= ]]; then
    key=${BASH_REMATCH[1]}
    eval "value=\${$key-}"
    printf '%s="%s"\n' "$key" "$value"
  else
    printf '%s\n' "$line"
  fi
done <config.example.env >"$tmp"
mv "$tmp" "$config_file"

say "$config_file"
grep -E '^[A-Z]' "$config_file" | sed 's/^/   /'
if confirm "Edit it before going on?" N; then
  "${EDITOR:-vi}" "$config_file" </dev/tty >/dev/tty
fi
load_config

# 4. Copy the repo -------------------------------------------------------------------------
if [[ -z $local_mode ]]; then
  say "Copying the repo to ~/homelab-agent on the server"
  rsync -a --delete --exclude .git --exclude mac --include config.example.env --exclude 'config*.env' \
    ./ "$SERVER_USER@$SERVER_HOST:homelab-agent/"
  rsync -a "$config_file" "$SERVER_USER@$SERVER_HOST:homelab-agent/config.env"
fi

# 5. Bootstrap (root) ----------------------------------------------------------------------
# Only when something it depends on changed since its last run (see bootstrap_fingerprint).
check="repo=$rdir; source \$repo/lib.sh; [[ \$(bootstrap_fingerprint) == \$(cat /var/lib/homelab-agent/bootstrap-fingerprint 2>/dev/null) ]]"
if on_server "$check"; then
  say "Bootstrap: up to date"
else
  say "Bootstrap: packages, Docker, firewall, SSH keys only, Tailscale"
  note "Asks for your sudo password on the server. When Tailscale prints a login link, open it."
  on_server_tty "sudo bash $rdir/bootstrap.sh"
fi

# 6. Start the services --------------------------------------------------------------------
say "Starting the services"
# Right after bootstrap, a shell on the server may not be in the docker group yet.
on_server "if id -nG | grep -qw docker; then bash $rdir/install.sh; else sg docker -c 'bash $rdir/install.sh'; fi"

# 7. Hermes' own setup ---------------------------------------------------------------------
if has_module hermes; then
  default=Y
  on_server "test -s /opt/stacks/hermes/data/.env" && default=N
  say "Hermes"
  if confirm "Run Hermes' setup (model provider, login, chat apps)? Needed once." "$default"; then
    on_server_tty "cd /opt/stacks/hermes && docker compose stop && docker compose run --rm -it gateway setup; docker compose up -d"
  fi
fi

# 8. Backups to this Mac -------------------------------------------------------------------
if [[ -z $local_mode && $(uname) == Darwin ]]; then
  say "Backups"
  label="home.$NAME.backup-pull"
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    # Already set up here; re-run so a rebuilt server gets the pull key again.
    mac/setup-mac.sh
  elif ! command -v brew >/dev/null; then
    note "Install Homebrew, then run mac/setup-mac.sh to pull the backups to this Mac."
  elif confirm "Copy the server's backups to this Mac every day?" Y; then
    mac/setup-mac.sh
  fi
fi

# 9. Summary -------------------------------------------------------------------------------
say "Done"
on_server "bash $rdir/status.sh"
echo
echo "Left for you:"
note "Reserve $LAN_IP for this server in your router, so it never changes."
for m in n8n uptime-kuma beszel; do
  if has_module "$m"; then note "Open $(module_var "$m" TITLE) and create its owner account."; fi
done
if has_module beszel; then note "In Beszel, add the system with host 127.0.0.1, port 45876."; fi
if has_module ai; then
  note "If bootstrap installed the NVIDIA driver: sudo reboot the server once."
  note "Local AI: the first start downloads the model (watch: ssh $SERVER_USER@$SERVER_HOST docker logs -f ai-$AI_MODEL)."
  note "  Switch models and open SwarmUI on the Local AI page; sign in with the API key:"
  note "  ssh $SERVER_USER@$SERVER_HOST cat /opt/stacks/ai/data/.env"
fi
if has_module pihole; then
  note "Tailscale admin console > DNS > add nameserver $(on_server 'tailscale ip -4' 2>/dev/null || echo '<the server'"'"'s Tailscale IP>'),"
  note "so your devices resolve *.$DOMAIN and get ad blocking."
fi
note "Save the backup password in your password manager:"
note "  ssh $SERVER_USER@$SERVER_HOST cat .config/restic/password"
echo
prefix=${CONFIG:+CONFIG=$CONFIG }
echo "Run ${prefix}./setup.sh again after changing services; ${prefix}./deploy.sh --update once a month."
