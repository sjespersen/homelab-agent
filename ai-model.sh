#!/usr/bin/env bash
# From your computer: which ai service has the GPU, and switching it (like the Local AI page).
#   ./ai-model.sh            show what runs
#   ./ai-model.sh qwen27b    Qwen3.8-27B on llama.cpp
#   ./ai-model.sh strata     Qwen3.8-Flash-Next on Strata
#   ./ai-model.sh swarmui    SwarmUI (image generation)
#   ./ai-model.sh stop       no model on the GPU (frees Strata's RAM too)
#   ./ai-model.sh leds off   case LEDs off (or on; needs AI_LEDS=yes)
#   ./ai-model.sh wake       wake the box after suspend or shutdown (WAKE_ON_LAN_*)
# With several servers: CONFIG=config.ai.env ./ai-model.sh strata
set -euo pipefail
repo="$(cd "$(dirname "$0")" && pwd)"
cd "$repo"
source lib.sh
load_config
has_module ai || { echo "The ai module isn't in MODULES in $config_file." >&2; exit 1; }

# Asks the switcher on the server, with the key from the server's data/.env.
call() {
  ssh "$SERVER_USER@$SERVER_HOST" "key=\$(sed -n 's/^API_KEY=//p' /opt/stacks/ai/data/.env)
    curl -fsS -H \"Authorization: Bearer \$key\" $1"
}
show() {
  call http://127.0.0.1:8079/_ai/api/status | python3 -c '
import json, sys
d = json.load(sys.stdin)
for s in d["services"]:
    state = s["state"]
    if state == "running" and s["health"]:
        state += " (" + s["health"] + ")"
    print("%-10s %-20s %s" % (s["id"], s["title"], state))
g = d["gpu"]
if g:
    print("GPU: %.1f of %.1f GB in use" % (g["used"] / 1024, g["total"] / 1024))
if d["switching"]:
    print("switching to", d["switching"])
if d["error"]:
    print("error:", d["error"])'
}

if [[ ${1:-} == wake ]]; then
  [[ -n ${WAKE_ON_LAN_MAC:-} ]] || { echo "Set WAKE_ON_LAN_MAC in $config_file." >&2; exit 1; }
  # The magic packet: 6 x FF, then the MAC 16 times, as a UDP broadcast to port 9.
  send='import socket, sys
mac = bytes.fromhex(sys.argv[1].replace(":", ""))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.sendto(b"\xff" * 6 + mac * 16, ("255.255.255.255", 9))'
  if [[ -n ${WAKE_ON_LAN_VIA:-} ]]; then
    ssh "$WAKE_ON_LAN_VIA" "python3 -c '$send' $WAKE_ON_LAN_MAC"
  else
    python3 -c "$send" "$WAKE_ON_LAN_MAC"
  fi
  echo "Wake packet sent to $WAKE_ON_LAN_MAC${WAKE_ON_LAN_VIA:+ from $WAKE_ON_LAN_VIA}; the box needs ~30 s (suspend) to ~2 min (off)."
  exit
fi
[[ ${1:-} != stop ]] || set -- none
if [[ ${1:-} == leds ]]; then
  [[ ${2:-} == on || ${2:-} == off ]] || { echo "Usage: $0 leds on|off" >&2; exit 1; }
  call "-X POST -H 'Content-Type: application/json' -d '{\"state\":\"$2\"}' http://127.0.0.1:8079/_ai/api/leds" >/dev/null
  echo "LEDs $2: applying (OpenRGB takes a few seconds)..."
  sleep 8
  call http://127.0.0.1:8079/_ai/api/status | python3 -c '
import json, sys
l = json.load(sys.stdin)["leds"]
s = l["settings"]
print("wanted:", s["state"], "" if s["state"] == "off" else "/ %s, speed %s" % (s["effect"], s["speed"] or "default"),
      "| applied:", l["applied"])
for d in l["devices"]:
    print(" ", d["name"] + ":", d["mode"])'
  exit
fi
if [[ -n ${1:-} ]]; then
  call "-X POST -H 'Content-Type: application/json' -d '{\"service\":\"$1\"}' http://127.0.0.1:8079/_ai/api/switch" >/dev/null
  echo "Switching to $1 (stopping the other one can take up to a minute)..."
  sleep 5
fi
show
