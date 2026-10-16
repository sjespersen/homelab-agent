#!/usr/bin/env bash
# Writes the CPU package's power draw in watts every 2 seconds, for the Local AI page:
# RAPL's energy counter (/sys/class/powercap/intel-rapl:0, AMD and Intel) is readable by root
# only, because fine-grained readings can leak secrets (Platypus). Whole watts every 2 s don't.
#   homelab-cpu-power.sh <dir>   (writes <dir>/cpu-power: "<watts> <unix time>")
set -uo pipefail
dir=$1
rapl=/sys/class/powercap/intel-rapl:0
[[ -r $rapl/energy_uj ]] || { echo "no RAPL counter at $rapl" >&2; exit 0; }
max=$(cat "$rapl/max_energy_range_uj")
prev=$(cat "$rapl/energy_uj") prev_t=$(date +%s%N)
while sleep 2; do
  e=$(cat "$rapl/energy_uj") t=$(date +%s%N)
  d=$((e - prev)); ((d >= 0)) || d=$((d + max))  # the counter wraps around
  # microjoules per microsecond = watts
  w=$(( (d * 1000 + (t - prev_t) / 2) / (t - prev_t) ))
  printf '%s %s\n' "$w" "$((t / 1000000000))" >"$dir/cpu-power.tmp" && mv "$dir/cpu-power.tmp" "$dir/cpu-power"
  prev=$e prev_t=$t
done
