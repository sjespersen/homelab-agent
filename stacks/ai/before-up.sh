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
media=$(sed -n 's/^AI_MEDIA_DIR="\(.*\)"$/\1/p' .env)
for sub in studio studio/logs switcher/logs swarm/Output comfyui/input comfyui/output logs/victoria logs/vector; do
  [[ -d $media/$sub ]] || mkdir -p "$media/$sub" || {
    echo "Can't create $media/$sub: run bootstrap.sh (./setup.sh) first, it prepares $media." >&2
    exit 1
  }
done
mkdir -p data/switcher data/swarm/{Data,CustomWorkflows} data/webchat data/studio
for sub in llama.cpp strata ollama swarm/Models swarm/dlbackend swarm/DLNodes swarm/Extensions; do
  [[ -d $models/$sub ]] || mkdir -p "$models/$sub" || {
    echo "Can't create $models/$sub: run bootstrap.sh (./setup.sh) first, it prepares $models." >&2
    exit 1
  }
done

# The Studio's ComfyUI node packs, at the commits its image pins (comfy-nodes.txt in it). ComfyUI
# exists once SwarmUI's installer ran; until then there's nothing to put them in.
nodes=$models/swarm/dlbackend/ComfyUI/custom_nodes
if [[ -d $nodes ]]; then
  studio_image=$(docker compose config --images studio)
  node_list=$(docker run --rm --entrypoint cat "$studio_image" /app/comfy-nodes.txt) || {
    echo "Can't read the node packs from $studio_image" >&2
    exit 1
  }
  while read -r name repo commit; do
    [[ -n $name && $name != \#* ]] || continue
    [[ $(git -C "$nodes/$name" rev-parse HEAD 2>/dev/null) != "$commit" ]] || continue
    echo "ComfyUI node pack $name at ${commit:0:7}"
    [[ -d $nodes/$name/.git ]] || git clone -q "$repo" "$nodes/$name"
    git -C "$nodes/$name" fetch -q origin "$commit"
    git -C "$nodes/$name" -c advice.detachedHead=false checkout -q "$commit"
  done <<<"$node_list"
fi

# MiniMax's H3 prompt-writing guide, which the Studio gives Qwen when it writes motion prompts:
# the Studio image's guide.py fetches it (pinned to a commit, checked by hash; not kept in any repo,
# since MiniMax's has no license file). Without it the Studio writes motion prompts all the same.
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data/studio:/data" --entrypoint python \
  "$(docker compose config --images studio)" /app/guide.py /data >/dev/null || true

# Open WebUI reaches the 27B and Strata through the switcher, with the API key.
key=$(sed -n 's/^API_KEY=//p' data/.env)
(umask 077; printf 'OPENAI_API_KEYS=%s\n' "$key" >data/webchat.env)

# Which services get the GPUs: the last one picked in the switcher (or ./ai-model.sh), or the
# services of the mode picked there ("scenario:<id>", data/switcher/scenarios.json), else
# AI_MODEL from the config; "none" after Stop. compose up starts only those
# (COMPOSE_PROFILES, comma-separated; empty: just the switcher).
services=$(docker compose --profile '*' config --services)
active=$(cat data/switcher/active 2>/dev/null || true)
if [[ $active == scenario:* ]]; then
  active=$(python3 -c 'import json, sys
for m in json.load(open(sys.argv[1])).get("scenarios", []):
    if m.get("id") == sys.argv[2]:
        print(",".join(m.get("services", [])))' data/switcher/scenarios.json "${active#scenario:}" 2>/dev/null)
fi
known=yes
for one in ${active//,/ }; do
  [[ $'\n'$services$'\n' == *$'\n'$one$'\n'* ]] || known=""
done
if [[ $active != none && ( -z $active || -z $known ) ]]; then
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
# active one, freeing the GPU and port 8080. Only the switcher's services (those with a profile):
# the rest (Studio, logs, web chat) runs all the time and compose up keeps it running.
docker compose --profile '*' create || exit 1
always=$(docker compose --profile __none__ config --services)
for service in $services; do
  [[ ,$active, == *,"$service",* || $'\n'$always$'\n' == *$'\n'$service$'\n'* ]] && continue
  if [[ -n $(docker compose --profile '*' ps -q "$service" 2>/dev/null) ]]; then
    echo "stopping $service ($active gets the GPUs)"
    docker compose --profile '*' stop -t 60 "$service"
  fi
done
