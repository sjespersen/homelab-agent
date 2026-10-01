#!/usr/bin/env bash
# Brings a fresh Ubuntu Server install to the configured state (the root-level parts).
# Safe to re-run. On the server, in a real terminal:  sudo bash ~/homelab-agent/bootstrap.sh
# Not covered: netplan/Wi-Fi, hostname, router settings.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
repo="$(cd "$(dirname "$0")" && pwd)"
source "$repo/config.env"
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

log "systemd-resolved: free port 53 for Pi-hole"
mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nDNSStubListener=no\n' >/etc/systemd/resolved.conf.d/90-pihole.conf
ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
systemctl restart systemd-resolved

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
echo "Tailscale IP (put it in config.env as TAILSCALE_IP): $(tailscale ip -4)"

log "Firewall"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for src in "${TRUSTED[@]}"; do
  for port in 22 53 80 443 8642 9119; do
    ufw allow from "$src" to any port "$port" >/dev/null
  done
  ufw allow from "$src" to any port 5353 proto udp >/dev/null
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

log "Done. Next, from your computer: ./deploy.sh"
