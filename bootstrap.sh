#!/usr/bin/env bash
# Brings a fresh Ubuntu Server install to the configured state (the root-level parts).
# Safe to re-run. setup.sh runs it for you; by hand, on the server in a real terminal:
#   sudo bash ~/homelab-agent/bootstrap.sh
# Run it again after changing MODULES: the firewall and some modules' host settings depend on it.
# Not covered: netplan/Wi-Fi, hostname, router settings.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
repo="$(cd "$(dirname "$0")" && pwd)"
source "$repo/lib.sh"
load_config
read -ra TRUSTED <<<"$TRUSTED_NETS"

log() { printf '\n== %s\n' "$*"; }

log "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 avahi-daemon libnss-mdns restic rsync ufw curl
usermod -aG docker "$SERVER_USER"

log "SSH: keys only, no root login"
if [[ ! -s /home/$SERVER_USER/.ssh/authorized_keys ]]; then
  echo "No SSH key for $SERVER_USER yet; run ssh-copy-id first, or you'd be locked out." >&2
  exit 1
fi
cat >/etc/ssh/sshd_config.d/00-keys-only.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
systemctl reload ssh

for m in $(enabled_modules); do
  if [[ -f $repo/stacks/$m/root-setup.sh ]]; then
    log "Host settings for $m"
    source "$repo/stacks/$m/root-setup.sh"
  fi
done

log "Disk: grow the root volume to fill the disk"
# Ubuntu's installer leaves part of the disk unallocated by default.
if vgs ubuntu-vg >/dev/null 2>&1; then
  free_bytes=$(vgs --noheadings --units b --nosuffix -o vg_free ubuntu-vg | tr -d ' ')
  if (( free_bytes > 1024 * 1024 * 1024 )); then
    lvextend -r -l +100%FREE /dev/ubuntu-vg/ubuntu-lv
  else
    echo "already full size"
  fi
else
  echo "no ubuntu-vg volume group, skipping"
fi

log "Containers and user timers run without anyone logged in"
loginctl enable-linger "$SERVER_USER"
install -d -o "$SERVER_USER" -g "$SERVER_USER" /opt/stacks

if [[ -n ${IT87_FAN_MIN_PWM:-} ]]; then
  log "Quieter fan: load the IT87 driver and lower the fan's minimum speed at boot"
  apt-get install -y -q lm-sensors
  echo it87 >/etc/modules-load.d/it87.conf
  modprobe it87
  install -m 755 "$repo/extras/it87-fan/it87-fan-min.sh" /usr/local/sbin/it87-fan-min.sh
  sed "s|@MIN_PWM@|$IT87_FAN_MIN_PWM|" "$repo/extras/it87-fan/it87-fan-min.service" \
    >/etc/systemd/system/it87-fan-min.service
  systemctl daemon-reload
  systemctl enable it87-fan-min.service
  systemctl restart it87-fan-min.service
fi

log "Tailscale"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "Open the login link below to add this server to your tailnet:"
  # The server keeps using the router for its own DNS; Pi-hole serves the tailnet.
  tailscale up --accept-dns=false
fi
echo "Tailscale IP: $(tailscale ip -4)"

log "Firewall"
# SSH and Caddy, plus what the enabled modules need: their own ports (FIREWALL_PORTS) and,
# without Pi-hole's names, the port Caddy serves each one on (PORT).
open_ports=" 22 80 443 "
for m in $(enabled_modules); do
  for port in $(module_var "$m" FIREWALL_PORTS); do open_ports+="$port "; done
  if ! has_module pihole; then
    for port in $(module_var "$m" PORT); do open_ports+="$port "; done
  fi
done
# Ports of disabled modules, or of a mode no longer in use, get closed again.
closed_ports=()
for m in $(all_modules); do
  for port in $(module_var "$m" FIREWALL_PORTS) $(module_var "$m" PORT); do
    [[ $open_ports == *" $port "* ]] || closed_ports+=("$port")
  done
done
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for src in "${TRUSTED[@]}"; do
  for port in $open_ports; do
    ufw allow from "$src" to any port "$port" >/dev/null
  done
  ufw allow from "$src" to any port 5353 proto udp >/dev/null
  for port in "${closed_ports[@]}"; do
    ufw delete allow from "$src" to any port "$port" >/dev/null 2>&1 || true
  done
done
ufw allow in on tailscale0 >/dev/null
# SSH only from TRUSTED_NETS and Tailscale (added above); older versions allowed it from anywhere.
ufw delete allow 22/tcp >/dev/null 2>&1 || true

# Ports that Docker publishes (Hermes' 8642 and 9119) skip ufw's rules for IPv4: Docker forwards
# them before ufw sees them. Docker sends that traffic through its DOCKER-USER chain first, so
# give that chain the same limits. IPv6 to published ports goes through docker-proxy, which ufw
# already covers.
docker_user="# BEGIN homelab-agent DOCKER-USER
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -i docker0 -j RETURN
-A DOCKER-USER -i br-+ -j RETURN
-A DOCKER-USER -i tailscale0 -j RETURN"
for src in "${TRUSTED[@]}"; do
  [[ $src == *:* ]] || docker_user+=$'\n'"-A DOCKER-USER -s $src -j RETURN"
done
docker_user+=$'\n'"-A DOCKER-USER -j DROP
COMMIT
# END homelab-agent DOCKER-USER"
sed -i '/^# BEGIN homelab-agent DOCKER-USER$/,/^# END homelab-agent DOCKER-USER$/d' /etc/ufw/after.rules
printf '%s\n' "$docker_user" >>/etc/ufw/after.rules

ufw --force enable
ufw reload >/dev/null
ufw status | head -3

# setup.sh compares this with its own copy to see whether bootstrap needs to run again.
install -d /var/lib/homelab-agent
bootstrap_fingerprint >/var/lib/homelab-agent/bootstrap-fingerprint

log "Done. Next: ./setup.sh continues by itself; by hand, run ./deploy.sh from your computer."
