# ai: local models on an NVIDIA GPU

Three services that take turns on the GPU, and a page to switch between them
([gpu-switcher](https://github.com/sjespersen/gpu-switcher), its own project):

| Service | What | Engine | Needs |
|---|---|---|---|
| `qwen27b` | Chat: Qwen3.8-27B (dense) | [llama.cpp](https://github.com/ggml-org/llama.cpp) server, pinned image | 16 GB VRAM, 13 GB disk |
| `strata` | Chat: Qwen3.8-Flash-Next (125B MoE, 6B active) | [Strata](https://github.com/Niko1221/Strata), built on the box from a release tag | 12 GB+ VRAM, 32 GB+ RAM (64 GB for every quant), ~75 GB NVMe |
| `swarmui` | Images: Krea 2, SDXL, Flux, SD 3.5 … | [SwarmUI](https://github.com/mcmonkeyprojects/SwarmUI) with ComfyUI inside, built from a pinned commit | 8 GB+ VRAM, ~10 GB for ComfyUI plus your models |

Only one runs at a time: each wants the whole GPU. "Stop" leaves the GPU empty; the page can
also suspend, restart or shut down the box.

| Address (no Pi-hole) | With Pi-hole | What |
|---|---|---|
| `http://<name>.local` | `<name>.<DOMAIN>` | The Local AI page, which is the box's landing page with this module: GPU and RAM, the models and buttons to switch, links to the other services, the API, power and lighting (login: the API key) |
| `http://<name>.local:8005` | `ai.<DOMAIN>` | The same page; the API lives here |
| `http://<name>.local:8005/v1` | `ai.<DOMAIN>/v1` | OpenAI-compatible API of the chat models; it starts the one a request names (Strata also has `/v1/messages`) |
| `http://<name>.local:8006` | `swarm.<DOMAIN>` | SwarmUI |
| `http://<name>.local:8007` | `chat.<DOMAIN>` | Open WebUI: web chat for all models, downloads Ollama models |
| `http://<name>.local:8008` | `studio.<DOMAIN>` | The Studio: a brief to video shots, see [Studio](#studio) |

The switcher and SwarmUI ask for the AI API key once per browser; the API takes it as a Bearer
token. It's `API_KEY` in `/opt/stacks/ai/data/.env` on the server.

## Set up

1. Put `ai` in `MODULES` and set the `AI_*` settings (see `config.example.env`). `setup.sh`
   asks for the models folder and the partition to mount there, and shows `lsblk`.
2. A new, empty disk needs a filesystem first. Check the name with `lsblk`, then (this erases
   it): `sudo parted -s /dev/nvme0n1 mklabel gpt mkpart models ext4 0% 100%` and
   `sudo mkfs.ext4 -L models /dev/nvme0n1p1`.
3. `./setup.sh`. Bootstrap installs the NVIDIA driver (Ubuntu's recommended one), the NVIDIA
   Container Toolkit and BuildKit, mounts the disk by UUID in `/etc/fstab`, and opens ports
   8005 and 8006. If it installed the driver: `sudo reboot`, then `./deploy.sh`.
4. The first deploy builds Strata (15–30 minutes) and SwarmUI. `AI_MODEL` starts first; its
   first start downloads the model (13 GB for `qwen27b`, ~70 GB for `strata`). Follow it with
   `docker logs -f ai-<service>` on the server.
5. The first time you open SwarmUI it shows its installer: pick the ComfyUI backend and let it
   install (several minutes). Then add models in its Models tab or with the model downloader
   (paste a Hugging Face or Civitai link).

## The page

`http://<name>.local` (and `:8005`). The header has suspend, restart and shut down (each asks
again on the button itself); the tab's icon gets a green dot while something has the GPU and
an amber one while switching. Below, two columns:

- **GPU**: what has it now (ready, answering, loading; up for how long; when the idle stop
  comes), or while switching the steps (free the GPU, start the container, load the model)
  with a timer. Below, a card per service with a Use button: chat models (API and web
  chat), SwarmUI (images) and Ollama (web chat, always running; "Make room" stops whatever
  has the GPU so its models load right away). Then the other services, each with its own
  icon, and the API (connect).
- **Box**: GPU, CPU, memory and power, each kind of number with its own look: memory as a
  bar (video memory per GB), load as a graph of the last 3 minutes, temperature on a
  cool-to-hot scale, power split into GPU, CPU and the rest. Then the case lighting.

The total power is an estimate: GPU and CPU as measured, plus about 35 W for the board, RAM,
SSDs, fans and power supply losses (`REST_WATTS` in [gpu-switcher](https://github.com/sjespersen/gpu-switcher)'s `app.py`); only a smart plug
measures it exactly. The CPU's power comes from `homelab-cpu-power.service` (RAPL is readable
by root only), which `root-setup.sh` installs: on a box set up before, rerun `./setup.sh`
once. On a phone it's one column, the box's numbers right after the GPU.

## Switch

On the switcher page, or from your computer:

```sh
./ai-model.sh            # what runs, GPU memory
./ai-model.sh strata     # Qwen3.8-Flash-Next
./ai-model.sh qwen27b    # Qwen3.8-27B
./ai-model.sh swarmui    # image generation
```

The API switches too: a request whose `model` is `qwen3.8-27b` or `qwen3.8-flash-next` (or
Strata's own `qwen3.8-flash-next-<quant>`, or the service names `qwen27b` and `strata`) first
gives the GPU to that model, after requests still running on the other one finish, and waits
until it has loaded. Any other name goes to the chat model that runs, or the last one used.
`/v1/models` lists both. While SwarmUI has the GPU the API answers 503 instead of stopping it.

Switching stops the running service and starts the other one; models already downloaded load
in under a minute (SwarmUI to Strata, measured: 44 s). Before the start the switcher drops the
stopped service's model files from the page cache: the kernel would free that cache by itself,
but Strata's 47 GB of huge pages waited ~80 s for it after SwarmUI. Strata's load is then bound
by the drive (~1.7 GB/s on this box's QLC NVMe: ~30 s of the 44). The choice is
saved on the server (`data/switcher/active`): it survives reboots and deploys. `AI_MODEL` in the
config is only what a new box starts with. With several servers, put the AI box's config
first: `CONFIG=config.ai.env ./ai-model.sh strata`.

## Stop, suspend, shut down

- **Idle unload:** with no API requests and no GPU load for `AI_IDLE_MINUTES` (default 30) the
  chat model stops, as with Stop; the next API request starts it again (1-2 minutes, which that
  request waits). `0` turns it off. It never stops SwarmUI or Ollama (Ollama unloads its own
  models after 30 minutes, `OLLAMA_KEEP_ALIVE`).
- **Stop** on the running service's card (or `./ai-model.sh stop`) leaves the GPU empty and frees
  Strata's ~50 GB of RAM. An idle loaded model costs little power (a 16 GB RTX 50 card idles at
  ~6 W with Strata in its memory), so this is mostly about RAM.
- The **Power** card suspends, restarts or shuts the box down, after a confirmation. A root
  service (`homelab-power.path`/`.service`) runs it and accepts nothing but those three actions.
- Before any suspend (page, power button, `systemctl suspend`) `homelab-ai-sleep.service` stops
  the GPU service, and starts it again after waking. It runs before NVIDIA's own suspend step:
  with a model still loaded, that step copies its GPU memory to RAM for ~90 s, and a model
  process ended afterwards hangs in the driver, so the kernel can't freeze it and the suspend
  fails (seen on an RTX 50 card with Strata).
- Waking up: the power button, or Wake-on-LAN with `./ai-model.sh wake` (set `WAKE_ON_LAN_*`;
  see [extras/wake-on-lan](../../extras/wake-on-lan/README.md), also for the BIOS settings
  that waking from off needs). On Wi-Fi, through a Pi cabled to the Ethernet port
  ([extras/wake-relay](../../extras/wake-relay/README.md), `WAKE_RELAY_*`).

## Case LEDs

With `AI_LEDS=yes`, the switcher page has a "Case lighting" card: Off and On, the effect (the
board's own list), its speed and its color; `./ai-model.sh leds off` (or `on`) switches too.
`AI_LEDS_ON_MODE` and `AI_LEDS_ON_SPEED` are only the defaults until you change them there. Bootstrap installs [OpenRGB](https://openrgb.org) and
three systemd units, all root (OpenRGB needs the USB and I2C devices): `homelab-openrgb.service`
keeps OpenRGB running as a server on 127.0.0.1:6742, so it scans the hardware once instead of on
every change (~1 s instead of ~17 s); `homelab-leds.path` notices the switch; and
`homelab-leds.service` applies it through the server, and again at every boot, since many
boards turn their RGB back on at power-up. "Off" uses a device's Off mode, or static black; "On" sets
`AI_LEDS_ON_MODE` (default `Rainbow wave`) at `AI_LEDS_ON_SPEED` (0 = slowest). The card shows each device's mode as read back.

Only LEDs OpenRGB can reach switch: the board's own and whatever is plugged into its RGB
headers, plus supported USB devices (e.g. an AMD Wraith Prism cooler on an internal USB header).
Fans on a case hub or the case's LED button, and power supplies with their own controller, don't.
List what it finds: `sudo QT_QPA_PLATFORM=offscreen openrgb --list-devices`.

## Web chat (Open WebUI) and Ollama

[Open WebUI](https://github.com/open-webui/open-webui) runs all the time (CPU only) at
`http://<name>.local:8007` (`chat.<DOMAIN>` with Pi-hole): a ChatGPT-like chat for every model
on the box. It talks to [Ollama](https://github.com/ollama/ollama) (the `ollama` service, for
models you download yourself) and to the 27B and Strata, all through the switcher, so every
model is always listed and picking one in the chat gives it the GPU.

- **Ollama runs all the time** and only holds the GPU while it has a model loaded (it unloads
  after 30 minutes unused). A chat with one of its models first stops the 27B or Strata, after
  their requests finish; switching to another service, or asking the API for the 27B or
  Strata, unloads Ollama's model instead of stopping Ollama. While SwarmUI has the GPU, Ollama
  chats get an error instead.
- **Set up before this change:** Open WebUI keeps its connections in its own database, so a box
  that ran it before still talks to the models directly. Admin Panel → Settings → Connections:
  the OpenAI URL is `http://switcher:8079/v1` (one URL, with the API key), and the Ollama URL
  `http://switcher:8079/ollama` (no key).
- **Not for Hermes:** the Ollama models aren't in the API (`/v1`), only in Open WebUI: their
  16K context is too small for agents.

- **Accounts:** the first account you create becomes the admin. Later sign-ups wait as
  "pending" until the admin approves them (Admin Panel → Users); you can also turn sign-ups off
  there (Admin Panel → Settings → General).
- **New models:** in Open WebUI, Admin Panel → Settings → Models → pull a model by name, e.g. `qwen2.5:14b`, or any GGUF on Hugging Face as
  `hf.co/<user>/<repo>:<quant>`. They land in `AI_MODELS_DIR/ollama` (not backed up).
- **What fits:** 8B-14B models run fully on a 16 GB GPU (`qwen2.5:14b`: 10 GB, ~29 tok/s on an
  RTX 5060 Ti); bigger ones spill into RAM and get slow (a 70B on DDR4: ~1-2 tok/s). For hard
  problems Strata is the faster and stronger choice.
- Ollama's KV cache is 8-bit and a model's default context 16K (`OLLAMA_CONTEXT_LENGTH`).
- Chats, accounts, settings and uploads are in `/opt/stacks/ai/data/webchat`, and **not** in the
  backups (one database holds all of them). After a rebuild, create the account again.

## Image models

Add models in SwarmUI's **Models → Download** tab (paste a model's download link), or save
them under `AI_MODELS_DIR/swarm/Models/` (`diffusion_models/` for Krea 2 and Flux-style
models). SwarmUI fetches text encoders and VAEs itself on first use.

- **Krea 2 Turbo** (fp8, 13 GB, [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2)) runs
  on a 16 GB card: 8 steps, CFG 1, about 14 s per 1024x1024 image on an RTX 50 card with 16 GB.
  It needs SwarmUI newer than 0.9.8-Beta (the pinned commit has it).

## Studio

`http://<name>.local:8008` (`studio.<DOMAIN>`): from a brief to finished video shots, on this
box. It's its own project, [studio](https://github.com/sjespersen/studio): see its README for
the steps (shot list, Krea 2 stills, MiniMax H3 previews and finals, upscale, export for
Resolve), timings, the models it needs and their licenses. Here it's set up like this:

- It asks the switcher for the GPU (the chosen chat model for the shot list and rewrites,
  SwarmUI for the rest) and holds it while it works: switching on the Local AI page is off
  until its queue is empty or you press Stop. Switching between Qwen and SwarmUI adds under a
  minute per job kind.
- Its chat models are the switcher's (Qwen3.8-27B, Flash-Next) and whatever you download into
  Ollama in Open WebUI; `AI_STUDIO_LLM` is the default for new projects.
- **Models** go into SwarmUI's `Models` folder (`AI_MODELS_DIR/swarm/Models`); the page lists
  what's missing. Hugging Face's CDN stalls over IPv6 on some networks: `curl -4 -L -C -`.
- Deploys put its two ComfyUI node packs into SwarmUI's ComfyUI, at the commits the pinned
  Studio image lists, and fetch MiniMax's H3 prompt guide into `data/studio/` (pinned, checked
  by hash).
- Projects live in `data/studio` (backed up), renders in `AI_MEDIA_DIR/studio` (not backed up).
- **Your own instructions**: a file `data/studio/prompts/<NAME>.txt` replaces one of its
  default instructions to the models (`SHOT_PROMPT`, `REWRITE_PROMPT`, `MOTION_FORMAT`,
  `PICTURE_PROMPT`), so your own versions stay on the box (and in the backup), out of git.

## Use the chat models

```sh
key=$(ssh <server> "sed -n 's/^API_KEY=//p' /opt/stacks/ai/data/.env")
curl http://<name>.local:8005/v1/chat/completions -H "Authorization: Bearer $key" \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"Hi"}]}'
```

- OpenAI clients: base URL `http://<name>.local:8005/v1`, the key above.
- Hermes or n8n on another server: use the AI box's Tailscale name, e.g. `http://<name>:8005/v1`.
- The first request after a switch or an idle stop waits while the model loads (1-2 minutes):
  give clients a timeout longer than that.
- While SwarmUI has the GPU, the API answers 503.

## Tuning

- `AI_QWEN27B_REPO` / `AI_QWEN27B_FILE`: the 27B GGUF. Measured on an RTX 5060 Ti 16 GB, with
  K at 8 bits, V at 5 and a 4-bit draft cache. Speculative decoding uses the DFlash2 draft
  (z-lab, 1.1 GB, downloaded on the first start); the MTP row is for comparison:

  | File | Draft | Context | VRAM | Output | Coding answer |
  |---|---|---|---|---|---|
  | unsloth `Qwen3.8-27B-UD-Q3_K_XL.gguf` (default, 13.1 GB) | DFlash2 | 32K | 15.6 GB | 50 tok/s | |
  | ukisai `Swift-1.5-Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf` (10.4 GB) | DFlash2 | 96K | 15.5 GB | 73 tok/s (40 with 90K in it) | 35 s (bench median) |
  | the same Swift 1.5 | MTP | 128K | 15.7 GB | 58 tok/s (26 with 126K in it) | 44 s (bench median) |

  Output speeds of the Swift rows are coding-bench medians (same score with both drafts).
  Swift 1.5 is a fine-tune that thinks shorter; set `AI_CONTEXT=98304` with it (112K runs out
  of memory on the first long prompt). For 128K, switch the draft back to MTP in compose.yaml.
  A 90K-token prompt takes ~150 s to read (Strata: 57 s). Without any draft, 144K fits with an
  8-bit cache, but output drops to ~33 tok/s.
- KV cache: keys at 8 bits, values at 5 (`q5_1`), the draft's cache at 4. Don't go lower for
  the main cache: an all-4-bit KV cache makes Qwen 3.8 loop. The draft cache only proposes
  tokens, so its bits can't change the answer.
- `AI_STRATA_QUANT`: `IQ2_XS` (Strata's default, least RAM), `IQ3_XXS`, `IQ3_S` (default
  here), and larger ones; Strata's docs list RAM per quant. A new quant downloads again.
- `AI_IDLE_MINUTES`: minutes without API requests or GPU load before the chat model stops (30;
  `0`: never).
- Changing these needs `./deploy.sh` (the switcher only starts and stops).

## Files

- `/opt/stacks/ai/data/`: the API key, the switcher's choice, SwarmUI's settings and workflows.
  In the backups, except the web chat (`webchat`), which stays out of them on purpose
  (`backup-excludes.txt`).
- `AI_MODELS_DIR`: `llama.cpp/`, `strata/`, and `swarm/` (image models, ComfyUI, extensions).
  Not backed up; models download again. Keep copies of models you made or changed yourself elsewhere.
- `AI_MEDIA_DIR` (`/srv/media`): the generated media, on its own disk with `AI_MEDIA_DEVICE`
  (mounted like the models disk): SwarmUI's images (`swarm/Output/`), ComfyUI's uploads and
  renders (`comfyui/input/`, `comfyui/output/`) and the Studio's renders (`studio/`). Not backed
  up: copy what you keep. The Local AI page shows how full the models, system and media disks are.
- The Studio: projects (briefs, shot lists, photos, the picture) in `data/studio/` (backed up),
  renders in `AI_MEDIA_DIR/studio/` (not backed up: export what you keep). ComfyUI keeps a copy
  of every upload and render; the Studio deletes them after each job. Deleting a shot or a
  project deletes its renders and photos too.
- The Studio's API log: every request to the chat models and ComfyUI with what came back
  (pictures as their size), in `AI_MEDIA_DIR/studio/logs/api.jsonl`, rotated at 10 MB with 5
  old files kept. Not backed up. `tail -f /srv/media/studio/logs/api.jsonl | jq`
- Logs (port 8009 / `logs.<DOMAIN>`, the switcher's login): VictoriaLogs with every container's
  log and the Studio's API log, searchable for 30 days (10 GB at most), in `AI_MEDIA_DIR/logs/`.
  Vector collects them (`logs/vector.yaml`). In the search page, `source:studio` shows the
  Studio's calls (the whole request and response in their `request` and `response` fields),
  `container_name:ai-switcher` one container. Opening port 8009 on the LAN takes a setup run.

## Updates

The llama.cpp, switcher, Studio and proxy images are pinned like every other image
(`./deploy.sh --update`). The switcher and the Studio come from their own repos: a new version
there is a tag, and `--update` moves the pin to it. Strata and SwarmUI are built from the git tags in `compose.yaml`: change the tag
(and the `image:` name next to it) and deploy. SwarmUI also updates ComfyUI and extensions
from its own UI; those live on the models disk.
