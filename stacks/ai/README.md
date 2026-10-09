# ai: local models on an NVIDIA GPU

Three services that take turns on the GPU, and a page to switch between them:

| Service | What | Engine | Needs |
|---|---|---|---|
| `qwen27b` | Chat: Qwen3.8-27B (dense) | [llama.cpp](https://github.com/ggml-org/llama.cpp) server, pinned image | 16 GB VRAM, 13 GB disk |
| `strata` | Chat: Qwen3.8-Flash-Next (125B MoE, 6B active) | [Strata](https://github.com/Niko1221/Strata), built on the box from a release tag | 12 GB+ VRAM, 32 GB+ RAM (64 GB for every quant), ~75 GB NVMe |
| `swarmui` | Images: SDXL, Flux, SD 3.5 … | [SwarmUI](https://github.com/mcmonkeyprojects/SwarmUI) with ComfyUI inside, built from a release tag | 8 GB+ VRAM, ~10 GB for ComfyUI plus your models |

Only one runs at a time: each wants the whole GPU. "Stop" leaves the GPU empty; the page can
also suspend, restart or shut down the box.

| Address (no Pi-hole) | With Pi-hole | What |
|---|---|---|
| `http://<name>.local:8005` | `ai.<DOMAIN>` | The switcher: what runs, GPU memory, buttons to switch |
| `http://<name>.local:8005/v1` | `ai.<DOMAIN>/v1` | OpenAI-compatible API of whichever chat model runs (Strata also has `/v1/messages`) |
| `http://<name>.local:8006` | `swarm.<DOMAIN>` | SwarmUI |

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

## Switch

On the switcher page, or from your computer:

```sh
./ai-model.sh            # what runs, GPU memory
./ai-model.sh strata     # Qwen3.8-Flash-Next
./ai-model.sh qwen27b    # Qwen3.8-27B
./ai-model.sh swarmui    # image generation
```

Switching stops the running service (Strata takes up to a minute to release its locked RAM)
and starts the other one; models already downloaded load in a minute or two. The choice is
saved on the server (`data/switcher/active`): it survives reboots and deploys. `AI_MODEL` in the
config is only what a new box starts with. With several servers, put the AI box's config
first: `CONFIG=config.ai.env ./ai-model.sh strata`.

## Stop, suspend, shut down

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
  that waking from off needs).

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

## Use the chat models

```sh
key=$(ssh <server> "sed -n 's/^API_KEY=//p' /opt/stacks/ai/data/.env")
curl http://<name>.local:8005/v1/chat/completions -H "Authorization: Bearer $key" \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"Hi"}]}'
```

- OpenAI clients: base URL `http://<name>.local:8005/v1`, the key above.
- Hermes or n8n on another server: use the AI box's Tailscale name, e.g. `http://<name>:8005/v1`.
- While SwarmUI has the GPU, the API answers 502.

## Tuning

- `AI_QWEN27B_QUANT` (Unsloth GGUF files): `UD-Q3_K_XL` 13.2 GB (default, fits a 32k context
  on 16 GB), `UD-IQ4_XS` 14.3 GB (better; set `AI_CONTEXT=16384`), `UD-IQ3_S` 12.0 GB (room
  for a longer context).
- `AI_STRATA_QUANT`: `IQ2_XS` (Strata's default, least RAM), `IQ3_XXS`, `IQ3_S` (default
  here), and larger ones; Strata's docs list RAM per quant. A new quant downloads again.
- Changing these needs `./deploy.sh` (the switcher only starts and stops).

## Files

- `/opt/stacks/ai/data/`: the API key, the switcher's choice, SwarmUI's settings, workflows
  and your images (`swarm/Output`). In the backups.
- `AI_MODELS_DIR`: `llama.cpp/`, `strata/`, and `swarm/` (image models, ComfyUI, extensions).
  Not backed up; models download again. Keep your own LoRAs or checkpoints elsewhere too.

## Updates

The llama.cpp, Python and proxy images are pinned like every other image (`./deploy.sh
--update`). Strata and SwarmUI are built from the git tags in `compose.yaml`: change the tag
(and the `image:` name next to it) and deploy. SwarmUI also updates ComfyUI and extensions
from its own UI; those live on the models disk.
