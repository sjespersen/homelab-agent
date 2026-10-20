#!/bin/bash
# Usage: aiwake-watchdog.sh [interface]
# Runs every minute (aiwake-watchdog.timer). The Pi's Wi-Fi sometimes doesn't come back after the
# router resets its Wi-Fi, and nothing on the Pi notices; then nobody can reach /api/wake. This
# pings the router and, if it stays unreachable, reconnects: after 3 failed checks NetworkManager
# reconnects, after 3 more the Wi-Fi radio is switched off and on, after 5 more the Pi reboots
# (at most every 30 minutes, so a router that's down for hours doesn't keep it rebooting).
set -u
iface=${1:-wlan0}
state=/run/aiwake-watchdog
last_reboot=/var/lib/aiwake-watchdog/last-reboot
mkdir -p "$state" "${last_reboot%/*}"
fails=$(cat "$state/fails" 2>/dev/null || echo 0)
level=$(cat "$state/level" 2>/dev/null || echo 0)

gw=$(ip -4 route show default dev "$iface" 2>/dev/null | awk '{ print $3; exit }')
if [[ -n $gw ]] && ping -c 2 -W 2 -I "$iface" "$gw" >/dev/null 2>&1; then
  [[ $fails -gt 0 || $level -gt 0 ]] && echo "$iface works again"
  echo 0 >"$state/fails"
  echo 0 >"$state/level"
  exit 0
fi

fails=$((fails + 1))
echo "$fails" >"$state/fails"
limit=3
[[ $level -ge 2 ]] && limit=5
if [[ $fails -lt $limit ]]; then
  echo "check $fails/$limit failed (router ${gw:-unknown} unreachable on $iface)"
  exit 0
fi

echo 0 >"$state/fails"
case $level in
  0)
    echo "router unreachable: reconnecting $iface"
    nmcli device disconnect "$iface" >/dev/null 2>&1
    nmcli device connect "$iface" >/dev/null 2>&1 || true
    ;;
  1)
    echo "router unreachable: switching the Wi-Fi radio off and on"
    nmcli radio wifi off
    sleep 5
    nmcli radio wifi on
    ;;
  *)
    now=$(date +%s)
    if [[ $((now - $(cat "$last_reboot" 2>/dev/null || echo 0))) -ge 1800 ]]; then
      echo "router still unreachable: rebooting"
      echo "$now" >"$last_reboot"
      systemctl reboot
    else
      echo "router still unreachable; rebooted less than 30 minutes ago, waiting"
    fi
    exit 0
    ;;
esac
echo $((level + 1)) >"$state/level"
