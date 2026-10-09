# Run by install.sh after the stack is up. $1: the stack's directory.
# With WAKE_TARGETS set ("name=mac ..."), Hermes can wake those devices: it writes a name to
# data/wake/request, and hermes-wake.path (systemd --user, no root) sends the magic packet from
# the host. Also gives Hermes the wake-device skill that explains this.
dir=$1
src=$(dirname "${BASH_SOURCE[0]}")/wake
targets=$(sed -n 's/^WAKE_TARGETS="\(.*\)"$/\1/p' "$dir/.env")
units=~/.config/systemd/user
if [[ -n $targets ]]; then
  mkdir -p "$dir/data/wake" ~/.local/bin "$units" "$dir/data/skills/homelab/wake-device"
  install -m 755 "$src/hermes-wake.sh" ~/.local/bin/hermes-wake.sh
  for unit in hermes-wake.service hermes-wake.path; do
    sed "s|@STACK@|$dir|g; s|@TARGETS@|$targets|g" "$src/$unit" >"$units/$unit"
  done
  names=$(for t in $targets; do printf '%s, ' "${t%%=*}"; done)
  sed "s|@NAMES@|${names%, }|" "$src/SKILL.md" >"$dir/data/skills/homelab/wake-device/SKILL.md"
  systemctl --user daemon-reload
  systemctl --user enable --now hermes-wake.path
elif [[ -f $units/hermes-wake.path ]]; then
  systemctl --user disable --now hermes-wake.path
  rm -f "$units"/hermes-wake.{path,service} ~/.local/bin/hermes-wake.sh
  rm -rf "$dir/data/skills/homelab/wake-device"
  systemctl --user daemon-reload
fi
