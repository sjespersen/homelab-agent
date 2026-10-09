#!/usr/bin/env bash
# Stops the GPU service before the box sleeps (pre) and starts it again after it wakes (post).
# Run by homelab-ai-sleep.service, which systemd starts before sleep.target and stops after
# waking. It has to come before nvidia-suspend.service: with a model still loaded, NVIDIA's step
# copies its GPU memory to RAM (~90 s), and a model process that ends after that hangs in the
# driver and keeps the kernel from freezing it, so the suspend fails. Strata also keeps ~50 GB
# of RAM locked.
#   homelab-ai-sleep.sh pre|post
set -uo pipefail
saved=/run/homelab-ai-sleep
case ${1:-} in
  pre)
    mapfile -t running < <(docker ps --filter label=homelab.ai.title --format '{{.Names}}')
    printf '%s\n' "${running[@]}" >"$saved"
    ((${#running[@]} == 0)) || docker stop -t 60 "${running[@]}" >/dev/null
    ;;
  post)
    [[ -s $saved ]] || exit 0
    mapfile -t running <"$saved"
    rm -f "$saved"
    # Right after waking, Docker or the GPU can need a moment.
    for _ in 1 2 3 4 5 6; do
      docker start "${running[@]}" >/dev/null 2>&1 && exit 0
      sleep 5
    done
    echo "Could not start ${running[*]} after waking" >&2
    exit 1
    ;;
esac
