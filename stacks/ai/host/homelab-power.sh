#!/usr/bin/env bash
# Runs the power action asked for on the Local AI page: suspend, reboot or poweroff, nothing else.
# Runs as root from homelab-power.service when the switcher writes <state dir>/power.
#   homelab-power.sh <state dir>
set -uo pipefail
file=$1/power
action=$(tr -d '[:space:]' <"$file" 2>/dev/null)
# Empty: the switcher is still writing it (the timer can look at any moment); its close runs us again.
[[ -n $action ]] || exit 0
rm -f "$file"  # first: the action must not repeat (after the reboot, say)
case $action in
  suspend) exec systemctl suspend ;;  # homelab-ai-sleep stops the model first
  reboot) exec systemctl reboot ;;
  poweroff) exec systemctl poweroff ;;
esac
