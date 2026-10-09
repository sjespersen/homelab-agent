#!/usr/bin/env bash
# Applies the LED settings from the Local AI page with OpenRGB, reads the result back and writes
# it next to them for the page. Runs as root (OpenRGB needs the USB/I2C devices) from
# homelab-leds.service: at boot, and whenever the settings file changes (homelab-leds.path).
#   homelab-leds.sh <state dir> <default effect for "on"> [default speed, 0-100]
# The settings file (<state dir>/leds) is written by the switcher, so it's checked strictly:
#   state=on|off   effect=<one of the device's modes>   speed=0-100   color=RRGGBB
# A file with just "on" or "off" (older switchers) works too.
set -uo pipefail
dir=$1
default_effect=$2
default_speed=${3:-}
export QT_QPA_PLATFORM=offscreen

# Through the OpenRGB server (homelab-openrgb.service) when it runs: it scanned the hardware
# once at start, so a command takes about a second. OpenRGB connects to a server on
# 127.0.0.1:6742 by itself; --nodetect skips its own scan. (Not --client: with it this version
# exits before it has the server's device list.) Without the server, OpenRGB scans (~8 s a run).
server=yes
openrgb() {
  if [[ -n $server ]]; then
    command openrgb --nodetect "$@" 2>/dev/null | grep -v -i -E '^qt\.|^Client:|^Connected to server|listener started'
  else
    command openrgb --noautoconnect "$@" 2>/dev/null | grep -v -i '^qt\.'
  fi
}

# Prints "<index>|<name>|<current mode>|<all modes>" per device. OpenRGB lists the modes on one
# line with the current one in brackets, and exits 0 even on errors, so this is the only check.
devices() {
  openrgb --list-devices | awk '
    /^[0-9]+: / { if (name != "") print idx "|" name "|" cur "|" modes
                  idx = $1; sub(":", "", idx); name = substr($0, index($0, " ") + 1); cur = ""; modes = "" }
    /^ *Modes:/ { modes = $0; sub(/^ *Modes: */, "", modes)
                  if (match(modes, /\[[^]]*\]/)) cur = substr(modes, RSTART + 1, RLENGTH - 2) }
    END { if (name != "") print idx "|" name "|" cur "|" modes }'
}

# has_mode <modes line> <mode>: whether the device lists that mode (any case; names with spaces
# are quoted, the current one is in brackets).
has_mode() {
  local modes=${1,,} mode=${2,,}
  [[ " $modes " == *" $mode "* || $modes == *"'$mode'"* || $modes == *"[$mode]"* ]]
}

# Reads the settings into state, effect, speed, color; anything malformed falls back.
read_settings() {
  # White unless a color is picked: a color effect (Static, Breathing ...) without one keeps the
  # board's last color, which after "off" is black.
  state="" effect=$default_effect speed=$default_speed color=FFFFFF
  local line key value
  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}
    case $line in
      on | off) state=$line ;;
      *=*)
        key=${line%%=*} value=${line#*=}
        case $key in
          state) [[ $value == on || $value == off ]] && state=$value ;;
          effect) [[ $value =~ ^[A-Za-z0-9\ ]{1,40}$ ]] && effect=$value ;;
          speed) [[ $value =~ ^[0-9]{1,3}$ ]] && ((value <= 100)) && speed=$value ;;
          color) [[ $value =~ ^[0-9A-Fa-f]{6}$ ]] && color=$value ;;
        esac
        ;;
    esac
  done 2>/dev/null <"$dir/leds"
}

# The arguments that set device <index> (modes: its mode list) to the current settings.
device_args() {
  local idx=$1 modes=$2 mode=$effect
  if [[ $state == off ]]; then
    # A real "Off" mode where there is one, otherwise static black.
    if has_mode "$modes" off; then
      printf '%s\n' -d "$idx" -m Off
    else
      printf '%s\n' -d "$idx" -m Static -c 000000
    fi
    return
  fi
  has_mode "$modes" "$mode" || mode=$default_effect
  printf '%s\n' -d "$idx" -m "$mode"
  [[ -z $speed ]] || printf '%s\n' -s "$speed"
  printf '%s\n' -c "$color"  # effects without a color (Rainbow wave ...) ignore it
}

# At boot the USB controller, and the server's own scan, can take a few seconds. If the server
# doesn't answer or knows no devices, scan without it.
for _ in 1 2 3 4 5 6; do
  list=$(devices)
  [[ -n $list ]] && break
  sleep 5
done
if [[ -z $list ]]; then
  server=""
  list=$(devices)
fi

read_settings
[[ -n $state ]] || exit 0  # nothing chosen yet: leave the LEDs as they are

# All devices go in one OpenRGB run. There's no check that it worked: some boards (MSI's
# Mystic Light ones) always report "Static" when asked.
# systemd ignores changes to the file while this runs, so apply again until the file still
# says what was applied.
while :; do
  applied=$(cat "$dir/leds" 2>/dev/null)
  args=()
  while IFS='|' read -r idx _ _ modes; do
    [[ -n $idx ]] || continue
    mapfile -t -O "${#args[@]}" args < <(device_args "$idx" "$modes")
  done <<<"$list"
  ((${#args[@]} == 0)) || openrgb "${args[@]}" >/dev/null

  # What was applied, and each device's modes for the page's effect list.
  {
    printf '{"state": "%s", "effect": "%s", "speed": "%s", "color": "%s", "at": "%s", "devices": [' \
      "$state" "$effect" "$speed" "$color" "$(date -Is)"
    sep=""
    while IFS='|' read -r idx name cur modes; do
      [[ -n $idx ]] || continue
      name=${name//\\/}; name=${name//\"/}; modes=${modes//\\/}; modes=${modes//\"/}
      printf '%s{"name": "%s", "mode": "%s", "modes": "%s"}' "$sep" "$name" "$cur" "$modes"
      sep=", "
    done <<<"$list"
    printf ']}\n'
  } >"$dir/leds.status.tmp"
  chmod 644 "$dir/leds.status.tmp"
  mv "$dir/leds.status.tmp" "$dir/leds.status"

  [[ $(cat "$dir/leds" 2>/dev/null) != "$applied" ]] || break
  read_settings
  [[ -n $state ]] || break
done
