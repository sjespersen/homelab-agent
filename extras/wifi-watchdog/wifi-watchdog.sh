#!/bin/bash
# Usage: wifi-watchdog.sh <interface>
# Runs every minute (wifi-watchdog.timer). Wi-Fi can stay "connected" while no replies arrive;
# nothing on the box notices, so this checks that traffic really flows and reconnects if not.
# Router unreachable for 3 checks in a row: reconnect. Router fine but the internet gone for 10
# (probably the provider): reconnect anyway, in case it's us. First a light reassociate, then a
# full restart of the Wi-Fi client; after that at most every 30 minutes.
set -u
iface=$1
state=/run/wifi-watchdog
mkdir -p "$state"
fails=$(cat "$state/fails" 2>/dev/null || echo 0)
level=$(cat "$state/level" 2>/dev/null || echo 0)

reachable() { ping -c 2 -W 2 -I "$iface" "$@" >/dev/null 2>&1; }

gw=$(ip -4 route show default dev "$iface" 2>/dev/null | awk '{ print $3; exit }')
gw_ok=""
[[ -n $gw ]] && reachable "$gw" && gw_ok=yes
net_ok=""
if reachable 9.9.9.9 || reachable 1.1.1.1 || reachable -6 2620:fe::fe; then net_ok=yes; fi

if [[ -n $gw_ok && -n $net_ok ]]; then
  [[ $fails -gt 0 || $level -gt 0 ]] && echo "$iface works again"
  echo 0 >"$state/fails"
  echo 0 >"$state/level"
  exit 0
fi

fails=$((fails + 1))
echo "$fails" >"$state/fails"
limit=10
[[ -z $gw_ok ]] && limit=3
[[ $level -ge 2 ]] && limit=30
if [[ $fails -lt $limit ]]; then
  echo "check $fails/$limit failed (router ${gw_ok:-unreachable}, internet ${net_ok:-unreachable})"
  exit 0
fi

echo 0 >"$state/fails"
echo $((level + 1)) >"$state/level"
if [[ $level -eq 0 ]] && wpa_cli -i "$iface" reassociate >/dev/null 2>&1; then
  echo "no traffic on $iface: reassociating with the access point"
else
  echo "no traffic on $iface: restarting the Wi-Fi client"
  if systemctl cat "netplan-wpa-$iface.service" >/dev/null 2>&1; then
    systemctl restart "netplan-wpa-$iface.service"
  else
    ip link set "$iface" down
    ip link set "$iface" up
  fi
  networkctl reconfigure "$iface" 2>/dev/null || true
fi
