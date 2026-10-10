# Run by install.sh before the stack starts. $1: the stack's directory.
dir=$1
cd "$dir" || exit 1

# Right after bootstrap installed the driver, the old one (nouveau) still holds the card.
if ! nvidia-smi >/dev/null 2>&1; then
  echo "The NVIDIA driver isn't loaded: reboot the server (sudo reboot), then ./deploy.sh." >&2
  exit 1
fi

# One API key for the chat models and the switcher's login, created once.
if [[ ! -s data/.env ]]; then
  key=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | cut -c1-40)
  (umask 077; printf 'API_KEY=%s\nLLAMA_API_KEY=%s\n' "$key" "$key" >data/.env)
fi

# Docker would create missing bind-mount folders as root; the containers run as you.
models=$(sed -n 's/^AI_MODELS_DIR="\(.*\)"$/\1/p' .env)
mkdir -p data/switcher data/swarm/{Data,Output,CustomWorkflows}
for sub in llama.cpp strata swarm/Models swarm/dlbackend swarm/DLNodes swarm/Extensions; do
  [[ -d $models/$sub ]] || mkdir -p "$models/$sub" || {
    echo "Can't create $models/$sub: run bootstrap.sh (./setup.sh) first, it prepares $models." >&2
    exit 1
  }
done

# Which service gets the GPU: the last one picked in the switcher (or ./ai-model.sh), else
# AI_MODEL from the config; "none" after Stop. compose up starts only that one
# (COMPOSE_PROFILES; empty: just the switcher).
services=$(docker compose --profile '*' config --services)
active=$(cat data/switcher/active 2>/dev/null || true)
if [[ $active != none && ( -z $active || $'\n'$services$'\n' != *$'\n'$active$'\n'* ) ]]; then
  active=$(sed -n 's/^COMPOSE_PROFILES="\(.*\)"$/\1/p' .env)
fi
profiles=$active
[[ $active != none ]] || profiles=""
sed -i "s/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=\"$profiles\"/" .env

# Strata: setup writes its settings into data/config/strata-<quant>.json on the first start and
# only reruns with REINSTALL=1. Rerun it when AI_STRATA_CONTEXT changed; otherwise make sure the
# config has fit_max_tokens (agents such as Hermes ask for more output than the context has room
# for; Strata then shortens max_tokens instead of refusing). The file belongs to root, so a
# throwaway container of the Strata image edits it.
strata_cfg=$models/strata/config/strata-$(sed -n 's/^AI_STRATA_QUANT="\(.*\)"$/\1/p' .env | tr '[:upper:]' '[:lower:]').json
want_ctx=$(sed -n 's/^AI_STRATA_CONTEXT="\(.*\)"$/\1/p' .env)
want_stream=$(sed -n 's/^AI_STRATA_KV_STREAMING="\(.*\)"$/\1/p' .env)
reinstall=0
if [[ -f $strata_cfg ]]; then
  have_ctx=$(python3 -c 'import json, sys; a = json.load(open(sys.argv[1]))["args"]; print(a[a.index("--max-context") + 1])' "$strata_cfg" 2>/dev/null)
  have_stream=off
  grep -q '"--kv-resident"' "$strata_cfg" && have_stream=on
  strata_image=$(docker compose --profile '*' config --images strata)
  if [[ $have_ctx != "$want_ctx" ]]; then
    echo "Strata: context $have_ctx -> $want_ctx, rerunning its setup on the next start"
    reinstall=1
  elif [[ ( $want_stream == on || $want_stream == off ) && $have_stream != "$want_stream" ]]; then
    echo "Strata: KV streaming $have_stream -> $want_stream, rerunning its setup on the next start"
    reinstall=1
  elif ! grep -q '"fit_max_tokens": true' "$strata_cfg" && docker image inspect "$strata_image" >/dev/null 2>&1; then
    echo "Strata: turning on fit_max_tokens"
    docker run --rm --entrypoint python3 -v "$models/strata/config:/c" "$strata_image" -c '
import json, sys
p = sys.argv[1]
c = json.load(open(p))
c["fit_max_tokens"] = True
json.dump(c, open(p, "w"), indent=1)' "/c/${strata_cfg##*/}"
    # A running Strata reads it at its next start. Restart it only if compose won't recreate it
    # anyway: one still started with REINSTALL=1 would rerun setup, which drops the key again.
    # A stopped one must stay stopped (the GPU).
    if [[ $(docker inspect -f '{{.State.Running}}' ai-strata 2>/dev/null) == true ]] \
      && docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' ai-strata | grep -qx 'REINSTALL=0'; then
      docker restart -t 60 ai-strata >/dev/null
    fi
  fi
fi
if grep -q '^STRATA_REINSTALL=' .env; then
  sed -i "s/^STRATA_REINSTALL=.*/STRATA_REINSTALL=\"$reinstall\"/" .env
else
  echo "STRATA_REINSTALL=\"$reinstall\"" >>.env
fi

# Build Strata and SwarmUI if their image is missing (the first time: Strata takes 15-30 min),
# one at a time and without bake: Compose 2.40's bake builds both at once, and on Docker's
# containerd image store that fails with 'image ... already exists' after a good build.
for service in $services; do
  image=$(docker compose --profile '*' config --images "$service")
  if [[ $image == local/* ]] && ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "building $image"
    COMPOSE_BAKE=false docker compose --profile '*' build "$service" || exit 1
  fi
done

# Create all of them, so the switcher can start them; then stop any that still run besides the
# active one, freeing the GPU and port 8080.
docker compose --profile '*' create || exit 1
for service in $services; do
  [[ $service == "$active" || $service == switcher || $service == socket-proxy ]] && continue
  if [[ -n $(docker compose --profile '*' ps -q "$service" 2>/dev/null) ]]; then
    echo "stopping $service ($active gets the GPU)"
    docker compose --profile '*' stop -t 60 "$service"
  fi
done
