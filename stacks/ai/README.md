# ai: local models on an NVIDIA GPU

Three services that take turns on the GPU, and a page to switch between them:

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
SSDs, fans and power supply losses (`REST_WATTS` in `switcher/app.py`); only a smart plug
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

`http://<name>.local:8008`: from a brief to finished video shots, on this box.

1. **Brief and characters.** Describe the film, and give each recurring person one clear photo
   (showing the outfit, on a plain background: the photo's background leaks into the stills
   otherwise). **Write shot list** has a chat model turn it into
   shots: a still prompt (the first frame) and a motion-and-sound prompt per shot. Edit any of
   it, or add shots by hand. Motion prompts follow H3's own format (`integrated_multimodal_description`,
   `overall_soundscape`, `non_diegetic_music`; the Studio adds the first-frame line): the model gets
   MiniMax's [H3 prompt-writing guide](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)
   with the brief. **Rewrite for H3** on a shot (or **Rewrite prompts for H3** for all that
   aren't in the format yet) turns a prompt you wrote in plain words into that format.
   Each project picks its model under the brief, from the same list as Open WebUI: Qwen3.8-27B,
   Flash-Next, and whatever you download into Ollama there (`AI_STUDIO_LLM` is the default for
   new projects). Small Ollama models (8-14B) work but write weaker H3 prompts.
   **Or start from a picture**: drop one under the brief (the page cuts it to the format, at the
   final size) and say in the brief what should happen. A model that can see (one with vision
   in Ollama, like Qwen3.8 or Gemma 3; pick it there) reads the place, each person's outfit,
   position and box, and a prompt for the picture; the shot list starts with the picture as
   shot 1's still, and the people become characters. Then Krea makes each person a full-body
   reference photo on a plain background (the identity LoRA restages their crop in the outfit
   read from the picture) and an image of each place, so one picture builds the whole project.
   Rename "Person 1" and the prompts follow.
   The model also writes the film's **places and outfits** once (the room, its furniture and
   light; each garment and what isn't worn), and each shot names its place and, per person,
   an outfit and where they stand in the frame. Edit them under **Places and outfits** and in a
   shot's prompts.
2. **Stills**: Krea 2 Turbo makes each shot's first frame at the final size. Each still is made
   on its own, so the Studio adds the place's and the outfits' descriptions to every prompt:
   that keeps the room and the clothes the same from shot to shot. With characters in the shot
   it uses the [Krea 2 Identity Edit V2](https://huggingface.co/conradlocke/krea2-identity-edit)
   LoRA with their photos, one pass per person, each with its own prompt. Krea doesn't know
   names, so the Studio swaps them for positions: pass 1 has the first person's photo and
   stages the whole scene ("the person from the photo is on the left", the others as stand-ins
   at their places), and each later pass replaces one stand-in ("the person on the right")
   with the person from the next photo. Click a still to see it at full size.
3. **Previews**: MiniMax H3 animates the still at ~0.3 MP (736×416 for 16:9), with sound.
4. **Review**: approve, retry (a new seed) or reject each preview; only approved ones go on.
5. **Finals**: the approved preview again (same seed, frame and prompt, so the same motion),
   its latent upscaled by a learned H3 upscaler and refined at H3's full size (1344×768; H3
   doesn't go past 768×1344). "Gentle" in the brief's settings keeps closer to the preview.
6. **Upscale** to 1080p, set per project: **Soft** (the default: SeedVR2 3B with its chunks
   crossfaded, mixed half and half with a plain Lanczos upscale, so it invents less skin
   texture and doesn't shift from chunk to chunk), **SeedVR2** alone (the most detail, and the
   most invented), or **Clean** (Lanczos only, ~20 s; finish in Resolve). Clips over ~9 s are
   upscaled in ~7.7 s pieces and joined, so RAM stays under ~45 GB (a 15 s clip in one go
   needed over 60).
7. **Export for Resolve**: a zip with the clips in order, `timeline.edl` (File > Import >
   Timeline, then point it at the clips folder) and the prompts. Finish (4K, grade) in Resolve.

Measured on the RTX 5060 Ti 16 GB with 64 GB RAM, for one 6.6-second 16:9 shot (once the models
are loaded; switching the GPU between Qwen and SwarmUI adds under a minute):

| Step | Time | Peak VRAM / RAM |
|---|---|---|
| Still (Krea 2 + identity LoRA) | ~50 s | — |
| Preview (736×416) | 1½ min | 15.8 / 11 GB |
| Final (1344×768) | 5½ min | 15.6 / 18 GB |
| Upscale (1920×1080, SeedVR2) | 10 min | — |
| Rewrite for H3 (Strata) | ~1 min after a 44 s switch from SwarmUI | — / 55 GB |

So a finished minute of film is roughly 2½ hours of GPU time after you approve the previews.

Jobs run one at a time, grouped by kind so models load less often. The Studio asks the switcher
for the GPU (the chosen chat model for the shot list and rewrites, SwarmUI for the rest) and holds it while it works:
switching on the Local AI page is off until the queue is empty or you press Stop.

**Models** (in SwarmUI's `Models` folder; the page lists what's missing). Hugging Face's CDN
stalls over IPv6 on some networks, so download with `curl -4 -L -C -`:

| File | Folder | From |
|---|---|---|
| `krea2_turbo_fp8_scaled`, `qwen3vl_4b`, `QwenImage/qwen_image_vae` | `diffusion_models`, `text_encoders`, `VAE` | SwarmUI fetches the last two the first time you use Krea 2 in it; the first from [Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2) |
| `krea2_identity_edit_v2` | `Lora` | [conradlocke/krea2-identity-edit](https://huggingface.co/conradlocke/krea2-identity-edit) |
| `minimax_h3_fl2va_pruned_w6a8`, `qwen3vl_32b_minimax_h3_nvfp4_awq`, `minimax_h3_video_vae_int8_convrot`, `minimax_h3_audio_vae_fp32`, `minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16` | `diffusion_models`, `text_encoders`, `VAE`, `VAE`, `Lora` | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| `minimax_h3_latent_upscaler_3d_conv_v1_bf16` | `latent_upscale_models` | [LBH-123-AI/Minimax_h3_latent_Upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler) |
| `seedvr2_3b_int8_convrot`, `seedvr2_ema_vae_fp16` | `diffusion_models`, `VAE` | [Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2) |

Two ComfyUI node packs come with it, pinned in `studio/comfy-nodes.txt` (deploys put them in
place): [comfyui-krea2edit](https://github.com/lbouaraba/comfyui-krea2edit) for the identity
LoRA, and [the H3 latent upscaler](https://github.com/xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus). Deploys also fetch MiniMax's
prompt guide (`references/base-en.txt`, pinned to a commit and checked by hash) into
`data/studio/`; without it, Qwen still writes the format from a short summary.

**Licenses.** MiniMax H3's license excludes the US, UK, EU and South Korea unless MiniMax
authorizes you (apply on their H3 license page; keep their email). The identity LoRA is free
for personal, non-commercial use only. The H3 upscaler's repository and MiniMax's H3 repository (the prompt guide) have no license; that's why the guide is fetched, not kept here.

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
  MTP and an 8-bit KV cache:

  | File | Context | VRAM | Output | Coding task to a finished answer |
  |---|---|---|---|---|
  | unsloth `Qwen3.8-27B-UD-Q3_K_XL.gguf` (default, 13.1 GB) | 32K | 14.4 GB | 52 tok/s | 96 s |
  | ukisai `Swift-1.5-Qwen3.8-27B-GSQ-RCO-IQ3_XXS-mtp.gguf` (10.4 GB) | 100K | 15.2 GB | 60 tok/s | 67 s |

  Swift 1.5 is a fine-tune that thinks shorter; set `AI_CONTEXT=102400` with it. 128K doesn't
  fit 16 GB with either. Both found a fact at the start of a 90K-token prompt, but reading
  that much took ~150 s (Strata: 57 s).
- Keep the KV cache at 8 bits (the default here): 4-bit KV makes Qwen 3.8 loop.
- `AI_STRATA_QUANT`: `IQ2_XS` (Strata's default, least RAM), `IQ3_XXS`, `IQ3_S` (default
  here), and larger ones; Strata's docs list RAM per quant. A new quant downloads again.
- `AI_IDLE_MINUTES`: minutes without API requests or GPU load before the chat model stops (30;
  `0`: never).
- Changing these needs `./deploy.sh` (the switcher only starts and stops).

## Files

- `/opt/stacks/ai/data/`: the API key, the switcher's choice, SwarmUI's settings and workflows.
  In the backups, except the generated images (`swarm/Output`) and the web chat (`webchat`),
  which stay out of them on purpose (`backup-excludes.txt`); copy images you want to keep.
- `AI_MODELS_DIR`: `llama.cpp/`, `strata/`, and `swarm/` (image models, ComfyUI, extensions).
  Not backed up; models download again. Keep copies of models you made or changed yourself elsewhere.
- The Studio: projects (briefs, shot lists, photos, the picture) in `data/studio/` (backed up),
  renders in `AI_MODELS_DIR/studio/` (not backed up: export what you keep). ComfyUI keeps a copy
  of every upload and render; the Studio deletes them after each job. Deleting a shot or a
  project deletes its renders and photos too.

## Updates

The llama.cpp, Python and proxy images are pinned like every other image (`./deploy.sh
--update`). Strata and SwarmUI are built from the git tags in `compose.yaml`: change the tag
(and the `image:` name next to it) and deploy. SwarmUI also updates ComfyUI and extensions
from its own UI; those live on the models disk.
