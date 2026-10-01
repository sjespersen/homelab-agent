#!/bin/bash
# Usage: it87-fan-min.sh <0-255>
# Lowers pwm1's minimum (auto start) speed on an ITE IT87xx Super I/O chip. Many BIOSes set a
# loud minimum on every boot; the chip's own temperature curve still ramps the fan up when hot.
set -eu
min_pwm=$1

for _ in $(seq 20); do
  name=$(grep -l '^it8' /sys/class/hwmon/hwmon*/name 2>/dev/null | head -1 || true)
  [[ -n $name ]] && break
  sleep 1
done
[[ -n $name ]] || { echo "IT87 sensor not found (is the it87 module loaded?)" >&2; exit 1; }
h=$(dirname "$name")

echo "$min_pwm" >"$h/pwm1_auto_start"
echo "fan minimum set to $min_pwm/255 on $(cat "$name")"
