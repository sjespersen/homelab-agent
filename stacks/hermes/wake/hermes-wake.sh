#!/usr/bin/env bash
# Wakes a device by Wake-on-LAN when Hermes asks: Hermes writes a device name to
# data/wake/request; this sends the magic packet as a broadcast from the host (a container's
# broadcasts stay in its Docker network) and writes the outcome to data/wake/result. Only the
# devices in WAKE_TARGETS ("name=mac ...", from the config) can be woken.
# Runs as your user from hermes-wake.service (systemd --user), started by hermes-wake.path.
#   hermes-wake.sh <hermes stack dir> <WAKE_TARGETS>
set -uo pipefail
dir=$1/data/wake
targets=$2
name=$(tr -d '[:space:]' <"$dir/request" 2>/dev/null)
rm -f "$dir/request"
[[ -n $name ]] || exit 0

mac=""
for t in $targets; do
  [[ ${t%%=*} == "$name" ]] && mac=${t#*=}
done
if [[ ! $mac =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
  known=$(for t in $targets; do printf '%s ' "${t%%=*}"; done)
  echo "$(date -Is) unknown device '$name' (known: ${known% })" >"$dir/result"
  exit 0
fi
python3 - "$mac" <<'PY'
import socket, sys
packet = b"\xff" * 6 + bytes.fromhex(sys.argv[1].replace(":", "")) * 16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for port in (9, 7):
    s.sendto(packet, ("255.255.255.255", port))
PY
echo "$(date -Is) sent the wake packet to $name ($mac)" >"$dir/result"
