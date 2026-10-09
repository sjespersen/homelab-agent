# Run by bootstrap.sh as root when the ai module is enabled: the NVIDIA driver, GPU access for
# containers, BuildKit (Strata is built on the box), and the disk for the models.
models=${AI_MODELS_DIR:-/srv/models}

apt-get install -y -q ubuntu-drivers-common docker-buildx gnupg
if ! nvidia-smi >/dev/null 2>&1; then
  # Ubuntu's recommended driver for the card (the open kernel modules on RTX 50).
  ubuntu-drivers install
  echo "NVIDIA driver installed: reboot the server (sudo reboot) before the models can start."
fi

if ! command -v nvidia-ctk >/dev/null; then
  keyring=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor --yes -o "$keyring"
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed "s#deb https://#deb [signed-by=$keyring] https://#" \
    >/etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -q
  apt-get install -y -q nvidia-container-toolkit
fi
# The containers get the GPU through CDI (see stacks/ai/compose.yaml). Toolkit 1.18+ keeps the
# spec in /var/run/cdi current by itself; older ones need it written once.
if [[ ! -f /var/run/cdi/nvidia.yaml && ! -f /etc/cdi/nvidia.yaml ]]; then
  nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
fi
if ! grep -qs nvidia /etc/docker/daemon.json; then
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
fi

# The Power card on the Local AI page: the switcher writes suspend/reboot/poweroff to
# data/switcher/power, homelab-power.path runs homelab-power.sh as root. homelab-ai-sleep stops
# the GPU service before any suspend (before NVIDIA's own step) and starts it again after.
install -d -o "$SERVER_USER" -g "$SERVER_USER" /opt/stacks/ai/data/switcher
install -m 755 "$repo/stacks/ai/host/homelab-power.sh" /usr/local/sbin/homelab-power.sh
install -m 755 "$repo/stacks/ai/host/homelab-ai-sleep.sh" /usr/local/sbin/homelab-ai-sleep.sh
for unit in homelab-power.service homelab-power.path homelab-ai-sleep.service; do
  sed "s|@DIR@|/opt/stacks/ai/data/switcher|" "$repo/stacks/ai/host/$unit" >/etc/systemd/system/$unit
done
rm -f /usr/lib/systemd/system-sleep/homelab-ai-sleep  # the earlier hook ran too late
systemctl daemon-reload
systemctl enable --now homelab-power.path
systemctl enable homelab-ai-sleep.service

# AI_LEDS=yes: the LED switch on the Local AI page. The switcher only writes "on" or "off" to
# data/switcher/leds; homelab-leds.path notices and runs homelab-leds.sh as root with OpenRGB,
# which also reapplies it at boot (many boards turn their RGB back on at power-up).
leds_dir=/opt/stacks/ai/data/switcher
if [[ ${AI_LEDS:-} == yes ]]; then
  apt-get install -y -q openrgb
  echo i2c-dev >/etc/modules-load.d/i2c-dev.conf  # RGB RAM and graphics cards are on I2C
  modprobe i2c-dev
  install -d -o "$SERVER_USER" -g "$SERVER_USER" "$leds_dir"
  install -m 755 "$repo/stacks/ai/host/homelab-leds.sh" /usr/local/sbin/homelab-leds.sh
  for unit in homelab-openrgb.service homelab-leds.service homelab-leds.path; do
    sed "s|@DIR@|$leds_dir|; s|@ON_MODE@|${AI_LEDS_ON_MODE:-Rainbow wave}|; s|@ON_SPEED@|${AI_LEDS_ON_SPEED:-}|" \
      "$repo/stacks/ai/host/$unit" >/etc/systemd/system/$unit
  done
  systemctl daemon-reload
  systemctl enable --now homelab-openrgb.service
  systemctl enable homelab-leds.service
  systemctl enable --now homelab-leds.path
elif [[ -f /etc/systemd/system/homelab-leds.path ]]; then
  systemctl disable --now homelab-leds.path homelab-leds.service homelab-openrgb.service
  rm -f /etc/systemd/system/homelab-leds.{path,service} /etc/systemd/system/homelab-openrgb.service \
    /usr/local/sbin/homelab-leds.sh
  systemctl daemon-reload
fi

# AI_MODELS_DEVICE: mount that partition at AI_MODELS_DIR at every boot (by UUID; nofail, so a
# missing disk doesn't stop the boot). Formatting is up to you; this never erases anything.
if [[ -n ${AI_MODELS_DEVICE:-} ]]; then
  uuid=$(blkid -s UUID -o value "$AI_MODELS_DEVICE" || true)
  fstype=$(blkid -s TYPE -o value "$AI_MODELS_DEVICE" || true)
  if [[ -z $uuid || -z $fstype ]]; then
    echo "$AI_MODELS_DEVICE has no filesystem yet. Check it's the right disk (lsblk), then" >&2
    echo "format it (erases it): sudo mkfs.ext4 -L models $AI_MODELS_DEVICE" >&2
    exit 1
  fi
  mounted_at=$(findmnt -nro TARGET -S "UUID=$uuid" 2>/dev/null | head -1 || true)
  if [[ -n $mounted_at && $mounted_at != "$models" ]]; then
    echo "$AI_MODELS_DEVICE is already mounted at $mounted_at; not touching it." >&2
    exit 1
  fi
  mkdir -p "$models"
  sed -i "\\|^[^#][^[:space:]]*[[:space:]]\\+${models}[[:space:]]|d" /etc/fstab
  printf 'UUID=%s %s %s defaults,noatime,nofail 0 2\n' "$uuid" "$models" "$fstype" >>/etc/fstab
  systemctl daemon-reload
  mountpoint -q "$models" || mount "$models"
fi
mkdir -p "$models"
if ! mountpoint -q "$models"; then
  echo "Note: $models is on the system disk. Set AI_MODELS_DEVICE to keep the models on their own disk."
fi
chown "$SERVER_USER:$SERVER_USER" "$models"
df -h "$models" | sed -n 2p
