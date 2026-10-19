# homelab-agent

It started as a cheap used mini PC turned into an always-on AI agent server: the reference
box is a €70 Shuttle DS61 (Intel i5-3475S, 8 GB RAM) from a classifieds site. It hosts the
agent, its memory, its tools and everything around it, 24/7.

Now a second stack runs next to it: a homelabbed AI server with an NVIDIA GPU (the reference
box: RTX 5060 Ti 16 GB, Ryzen 7 3700X, 64 GB RAM). It runs the models locally (Qwen3.8-27B,
Qwen3.8-Flash-Next, image generation, Ollama for the web chat) behind one OpenAI-compatible
API and a page to switch between them, so the agent can use a local model instead of the
cloud (your OpenAI/Anthropic/OpenRouter account still works). Both boxes come from this repo,
each with its own config (see [More than one server](#more-than-one-server)).

Every service except Caddy is optional (see [Modules](#modules)):

| Service | Address | What it does |
|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `hermes.home.arpa`, `<server>:9119` | The agent. Chat with it on Telegram/WhatsApp, let it code and push to GitHub |
| [n8n](https://n8n.io) | `n8n.home.arpa` | Workflow automation; can call Hermes' OpenAI-compatible API |
| [Pi-hole](https://pi-hole.net) | `pihole.home.arpa` | DNS with ad and tracker blocking; also serves the `*.home.arpa` names |
| [Uptime Kuma](https://github.com/louislam/uptime-kuma) | `status.home.arpa` | Uptime checks and alerts |
| [Beszel](https://beszel.dev) | `server.home.arpa` | CPU, RAM, disk, temperatures, fans, per-container stats |
| Local AI ([llama.cpp](https://github.com/ggml-org/llama.cpp), [Strata](https://github.com/Niko1221/Strata), [SwarmUI](https://github.com/mcmonkeyprojects/SwarmUI), [Open WebUI](https://github.com/open-webui/open-webui), [Ollama](https://github.com/ollama/ollama)) | `ai.home.arpa`, `<server>:8005`; `swarm.home.arpa`, `<server>:8006`; `chat.home.arpa`, `<server>:8007`; `studio.home.arpa`, `<server>:8008` | Qwen3.8-27B, Qwen3.8-Flash-Next or image generation on an NVIDIA GPU, a page to switch, OpenAI-compatible API, and a Studio that turns a brief into video shots (Krea 2, MiniMax H3, SeedVR2). Opt-in |
| [Caddy](https://caddyserver.com) | port 80 | Gives every service a name; landing page at `http://<name>.local` (with the ai module: the Local AI page, which links to the rest) |
| [Tailscale](https://tailscale.com) | — | Reach everything from anywhere, no open ports |
| [restic](https://restic.net) | — | Nightly encrypted backups, pulled to your Mac |

Everything is code: a fresh Ubuntu Server becomes this setup with one command, and a dead box
is rebuilt from the repo plus the latest backup.

## What you need

Two tracks: start with the agent box; the AI box is optional and can come later (or run on
its own).

**The agent box** (Hermes, n8n, Pi-hole, monitoring, backups):

- Any x86-64 PC with 8 GB RAM and an SSD, like the €70 Shuttle. Tested on Ubuntu Server 26.04.
- An LLM provider account for Hermes (OpenAI, Anthropic, OpenRouter, ...), or the AI box.

**The AI box** (local models; the [ai module](stacks/ai/README.md)):

- An x86-64 PC with an NVIDIA GPU: 16 GB VRAM for Qwen3.8-27B, 12 GB+ for Qwen3.8-Flash-Next
  on Strata, 8 GB+ for image generation. The reference box has an RTX 5060 Ti 16 GB.
- 32 GB RAM or more; 64 GB for every Strata quant (it keeps the experts in RAM).
- An NVMe disk for the models: about 100 GB for both chat models, plus your image models.
- Ubuntu 26.04, as for the agent box; `setup.sh` installs the NVIDIA driver.

**For both:**

- A computer to set it up from (macOS or Linux), or a keyboard and screen on the server itself.
  The backup pull in `mac/` is macOS-only.
- A free [Tailscale](https://tailscale.com) account.

## Set up

1. Install Ubuntu Server on the box, with OpenSSH (the installer offers it).
2. On your computer:
   ```sh
   git clone https://github.com/sjespersen/homelab-agent && cd homelab-agent
   ./setup.sh
   ```
   It asks for the server's address, a name and which services you want; everything else it
   reads from the server. Then it copies your SSH key, installs Docker, the firewall, Tailscale
   and the services, and starts Hermes' own setup (model, login, chat apps). You type the
   server password once, the sudo password once, and open the Tailscale login link it prints.
3. Do the few things it lists at the end: reserve the server's IP in your router, create the
   owner accounts in the web UIs, add the server as DNS server in the
   [Tailscale admin console](https://login.tailscale.com/admin/dns) (with Pi-hole), and save
   the backup password in your password manager.

No second computer? Clone the repo on the server and run `./setup.sh --local`; it can import
your SSH keys from GitHub.

`setup.sh` is safe to re-run: it skips what's done, resumes after an error, and applies
changed answers. Total time: about an hour, most of it waiting for downloads.

<details>
<summary>The same by hand</summary>

1. `cp config.example.env config.env` and fill it in; `ssh-copy-id <user>@<server>`.
2. `./deploy.sh` (copies the repo to `~/homelab-agent`; the stacks fail until Docker exists).
3. On the server, in a real terminal: `sudo bash ~/homelab-agent/bootstrap.sh`.
4. `./deploy.sh` again.
5. `ssh -t <server> 'cd /opt/stacks/hermes && docker compose run --rm -it gateway setup'`,
   then `./deploy.sh` once more.
6. On a Mac: `mac/setup-mac.sh` starts the daily backup pull.

</details>

## Modules

Pick the services in `config.env`:

```sh
MODULES="pihole hermes n8n uptime-kuma beszel"   # the default: everything
MODULES="hermes"                                 # just the agent
MODULES="ai"                                     # a GPU box serving local models
```

`ai` needs an NVIDIA GPU, so it's only in `MODULES` if you put it there (`setup.sh` suggests
it when it finds one). See [`stacks/ai/README.md`](stacks/ai/README.md).

Caddy, the firewall, Tailscale and backups are always on. Turning a module off stops its
containers and closes its ports; its data stays in `/opt/stacks/<module>` (and in the backups).

Without Pi-hole nobody hands out the `*.home.arpa` names, so Caddy serves each service on its
own port instead: Hermes `:8001`, n8n `:8002`, Uptime Kuma `:8003`, Beszel `:8004`, Local AI
`:8005`, SwarmUI `:8006`, web chat `:8007`, all linked from `http://<name>.local`.

A module is a folder in `stacks/`:

| File | What it's for |
|---|---|
| `compose.yaml` | The containers. Reads `${TZ}`, `${DOMAIN}`, `${PUID}`, `${SITE_URL}` and friends |
| `module.env` | Title, start order, name/port behind Caddy (more with `SITES`), firewall ports, containers to stop for backups, settings passed to compose (`ENV`) |
| `site.caddy`, `site.<site>.caddy` | What Caddy does for its name or port (usually one `reverse_proxy` line) |
| `compose.with-<module>.yaml` | Added only when that other module is on (n8n joins Hermes' network) |
| `backup-excludes.txt`, `before-up.sh`, `after-up.sh`, `root-setup.sh` | Optional: caches to skip, a step before or after start, host setup as root |

To add your own service, copy a module like `uptime-kuma`, give it a free `PORT`, and run
`./setup.sh`.

## Change something

Edit a compose file in `stacks/`, then `./deploy.sh`. To add or remove services, run
`./setup.sh` again (it also updates the firewall, which needs sudo).

See what's running, where, and when the last backup ran: `./deploy.sh --status`.

### More than one server

Give each server its own config file in the repo (`config.<name>.env`, gitignored like
`config.env`) and put `CONFIG=` in front of the commands:

```sh
CONFIG=config.ai.env ./setup.sh        # first time: writes config.ai.env
CONFIG=config.ai.env ./deploy.sh
CONFIG=config.ai.env ./deploy.sh --update
```

Each server gets only its own file (as `config.env` in `~/homelab-agent`), and
`update-images.sh` only moves the pins of the modules that server runs. Without `CONFIG` the
commands use `config.env`, as before.

### Versions and updates

Every image is pinned by digest in its compose file, so a deploy or a rebuild installs exactly
what the repo says, today or a year from now:

```yaml
image: caddy:2@sha256:0c99...  # v2.11.4   (the tag it follows, the exact build, the version)
```

Update about once a month with `./deploy.sh --update`: it takes a backup, moves each pin to the
newest build of its tag (`update-images.sh`), shows what changed and deploys. If everything
works, commit the compose files; if not, `git checkout -- stacks && ./deploy.sh` goes back to
the old images, and restic has the data from before the update.

Not pinned: Ubuntu's packages (Docker, restic, the NVIDIA driver; they get Ubuntu's security
updates), Tailscale, the NVIDIA Container Toolkit, and the tools Hermes installs into its data
folder at runtime (those are in the backups). Strata and SwarmUI have no published images: the server builds
them from the release tag or commit in `stacks/ai/compose.yaml`, so updating them means changing that.

## Security

This is a home lab, not a hardened production setup. The defaults keep everything off the
internet, but anyone or anything that gets **inside** (onto your LAN, your tailnet, or into the
agent) gets a lot. Read this before you put anything valuable on the box.

### What the defaults protect

- SSH accepts keys only, and only from `TRUSTED_NETS` and Tailscale; passwords and root login
  are off.
- ufw blocks DNS, the web UIs and the Hermes ports from everywhere except `TRUSTED_NETS` and
  Tailscale. That includes the ports Docker publishes, which normally skip ufw (`bootstrap.sh`
  adds matching rules to Docker's `DOCKER-USER` chain).
  Pi-hole, n8n, Uptime Kuma and Beszel listen on `127.0.0.1` only; outside the box they are
  reachable only through Caddy. Without Pi-hole, Caddy's ports for them (8001–8004) get the
  same `TRUSTED_NETS` and Tailscale limits.
- Only the modules you enable run, and only their ports are open.
- Hermes and n8n, the two containers that run code from outside, sit on Docker bridge networks.
  They can't reach the other services' ports or Caddy (ufw drops traffic from Docker networks).
- The Beszel agent talks to Docker through a read-only proxy, not the Docker socket.
- n8n's Execute Command and Local File Trigger nodes are off.
- The local AI API, switcher page and SwarmUI (ports 8005 and 8006, via Caddy) need the AI API
  key; the engines themselves listen on `127.0.0.1` only. The API (`/v1`) takes only the key
  itself, not the switcher page's login cookie, so another web page you open can't use your
  login to send requests to the models or start them. Ollama's own API passes through the
  switcher at `/ollama` without a login, for Open WebUI; Caddy doesn't pass `/ollama` on, so
  only the AI stack's network and the server itself reach it, as before. The CPU's energy
  counter (RAPL) stays root-only, since fine-grained readings can leak secrets; a root service
  passes only whole watts every 2 seconds to the page. The switcher reaches Docker through a
  proxy that only lists, starts and stops containers, on an internal network nothing else is on.
- The Studio (ai module, port 8008, via Caddy) checks the same login itself (the switcher's
  cookie or the key) and reaches ComfyUI only through SwarmUI on the stack's network. Reference
  photos must be PNG, JPEG or WebP (checked by their first bytes, 25 MB at most) and are stored
  under names the Studio makes up; it serves renders only by those names, so a request can't
  reach other files.
- The wake relay Pi (`extras/wake-relay`) answers `/api/wake` without a login to anyone on the
  LAN; all it can do is send the magic packet to the box.
- Hermes pushes to GitHub with per-repo deploy keys, not your account.
- The Mac's backup key can only read the backup folder (`rrsync -ro`).
- Backups are encrypted with restic.

### Attack vectors, most serious first

**1. Prompt injection into the agent.** Hermes reads web pages, emails, chat messages, issues
and code, and any of those can contain instructions written for it. It can run shell commands
in its container, and that container holds your LLM credentials, chat-app sessions (a stolen
WhatsApp session can read and send as you), API keys and GitHub deploy keys.
*Reduce it:* give it only the keys it needs; keep the chat apps on an allowlist of your own
accounts; don't connect it to inboxes or accounts you can't afford to leak.

**2. Partial isolation between containers.** Hermes and n8n are on bridge networks, but when
both are enabled they share one (so workflows can call `http://hermes:8642/v1`): a compromised
Hermes can reach n8n's login and webhooks, and the other way round. Pi-hole, Caddy, Uptime Kuma and Beszel still use
host networking and can reach each other's `127.0.0.1` ports, including the read-only Docker
proxy on port 2375 (see 3).
*Reduce it:* use strong, unique passwords for every web UI, and authentication on n8n
webhooks.

**3. Several paths to root.** Anything that controls Docker controls the host:
- Your user is in the `docker` group, so your SSH key is effectively a root key, and the sudo
  password doesn't protect anything.
- The socket proxy for Beszel mounts the Docker socket. It only passes on reads (container
  list, inspect, stats, logs; server info), so a compromised Beszel agent can't start
  containers. The proxy image itself is trusted with root. The reads still expose every
  container's environment variables and logs to anything on the host network (port 2375 on
  `127.0.0.1`), so keep secrets out of compose `environment:` (Hermes and n8n keep theirs in
  `data/`).
- The AI switcher's proxy (ai module) mounts the Docker socket too. It allows listing,
  inspecting, starting and stopping containers: a compromised switcher can stop any container
  on the box and read their environment variables, but can't create one or run commands.
*Reduce it:* protect your SSH key with a passphrase; remove the `socket-proxy` service and
`DOCKER_HOST` from `stacks/beszel/compose.yaml` if you don't need per-container stats.

**4. Ransomware can reach the backups.** Anyone who controls the server can delete or encrypt
`~/backups/restic` (the password sits next to it in `~/.config/restic/password`). The Mac
pulls with `rsync --delete`, so the next pull mirrors that damage onto the Mac copy.
*Reduce it:* keep Time Machine (or any versioned backup) running on the Mac so older copies
survive; check `restic snapshots` now and then.

**5. Plain HTTP on the LAN.** Logins for Hermes (basic auth), n8n, Uptime Kuma, Beszel and
Pi-hole travel unencrypted when you use them over the LAN. Anyone on the same network can
capture them: a guest on your Wi-Fi, a compromised smart TV or IoT device. `TRUSTED_NETS`
usually means "your whole LAN".
*Reduce it:* use the services over Tailscale (encrypted) even at home, and narrow
`TRUSTED_NETS` or drop the LAN entries entirely. Put IoT and guest devices on a guest network.

**6. Everyone on your tailnet gets everything.** ufw allows all traffic on `tailscale0`. Any
device you add, any node you share with someone, and anyone who takes over your Tailscale
account can reach every port on the box.
*Reduce it:* turn on 2FA for the account behind Tailscale; use
[Tailscale ACLs](https://tailscale.com/kb/1018/acls) to limit who reaches the server; remove
old devices.

**7. Images only update when you update them.** Every image is pinned by digest, so nothing
changes behind your back, but known holes also stay until you run `./deploy.sh --update`.
Ubuntu installs its own security updates (unattended-upgrades); containers don't. And when you
do update, you trust whatever the maintainers published since. Strata (ai module) is a young,
single-maintainer project built from a git tag on the box and run as root in its container
with page-locked memory: a tag that gets moved, or a bad release, runs on your GPU box.
SwarmUI is built the same way, and installs ComfyUI and Python packages at runtime, unpinned;
every ComfyUI custom node or SwarmUI extension you add runs code with access to its container
(your images and the models disk). The Studio adds two community node packs
(`stacks/ai/studio/comfy-nodes.txt`): pinned to a commit, read before pinning (no network
calls, subprocesses or installs at runtime), put in place by `before-up.sh`. It also fetches
MiniMax's H3 prompt guide, a text file that goes into Qwen's prompt: pinned to a commit and
checked against its SHA-256, so a changed file is refused rather than fed to the model.
*Reduce it:* update monthly; read the release notes for Hermes, n8n, Strata and SwarmUI before
committing new pins or tags, and the diff before moving a node pack's pin; install only custom
nodes and extensions from well-known authors.

**8. Secrets at rest.** Ubuntu's default install has no disk encryption, so whoever takes the
box gets every key and session on it. Secrets also sit in plain files under `/opt/stacks`
(Hermes `data/.env` and `auth.json`, n8n's encryption key) and in every backup.
*Reduce it:* choose disk encryption when you install Ubuntu (you then type the passphrase at
every boot); keep the box somewhere it won't walk off.

### Smaller items

- Hermes' API on port 8642 gives full agent access to anyone with the key on `TRUSTED_NETS` or
  the tailnet. Treat that key like a password.
- The local AI key (`/opt/stacks/ai/data/.env`) travels in plain HTTP on the LAN like the logins
  in 5. Whoever has it can use the chat models, switch services, use SwarmUI, including
  installing extensions there (see 7), and the Studio (its projects, photos and renders; it can
  also hold the GPU, which turns off switching on the Local AI page for up to 10 minutes at a
  time). It also signs the login cookie: change the key to sign
  everyone out.
- Model files from Hugging Face or Civitai can carry code: prefer `.safetensors` and `.gguf`
  over `.ckpt`/`.pt` files.
- The web chat (ai module, Open WebUI, port 8007) has its own accounts, and the first one is the
  admin. Create it right after the first deploy: until then anyone on `TRUSTED_NETS` or the
  tailnet could. Its admin can download models and add Open WebUI "functions" and "tools",
  which run Python inside its container (with the AI API key in its environment). Ollama isn't
  published; only Open WebUI reaches it.
- The local AI key can also suspend, restart or shut down the box (the Local AI page's power buttons).
  A root service runs those three actions and nothing else.
- With `WAKE_TARGETS` set, Hermes can wake those devices (and only those: the MACs and wake URLs
  come from the config). A prompt injection could wake them too; it can't do more than that through this.
- With `WAKE_ON_LAN_IFACE` set, anything on the LAN can wake the box with a magic packet. That's
  harmless by itself, but a box woken that way is up with all its services.
- The LED switch (ai module, `AI_LEDS=yes`) runs a root service whenever
  `/opt/stacks/ai/data/switcher/leds` changes, a file your user and the switcher can write. The
  service accepts only on/off, an effect the device itself lists (letters, digits, spaces), a
  speed of 0–100 and a 6-digit color from it, and passes them to OpenRGB as plain arguments.
  OpenRGB itself runs as a root server on 127.0.0.1:6742 without a password: any program on the
  box (including the host-network containers: Caddy, Uptime Kuma, Beszel) can use its whole SDK,
  which changes the lighting and saves or loads OpenRGB profiles in root's OpenRGB folder.
- With the ai module, `bootstrap.sh` adds NVIDIA's apt repository (signed with NVIDIA's key)
  for the Container Toolkit.
- n8n webhooks are open by design to anyone who can reach n8n. Add authentication in the
  webhook node.
- Tailscale is installed with `curl | sh`, and `bootstrap.sh` runs as root: read scripts before
  you run them, including these.
- `./setup.sh --local` can import your SSH keys from GitHub: whoever controls that GitHub account
  then has a key to the server. Remove keys you don't use from `~/.ssh/authorized_keys`.
- The Mac pulls with rsync from the server; rsync clients have had bugs a malicious server
  could exploit. Keep Homebrew's rsync updated.
- Avahi announces the server's name and services on the LAN.
- If your ISP changes your IPv6 prefix, update `TRUSTED_NETS` and re-run `bootstrap.sh`. Until
  then the server is reachable over IPv4 on the LAN and over Tailscale, including SSH.
- If you add a rule to `/etc/ufw/after.rules` yourself, keep it outside the
  `homelab-agent DOCKER-USER` block; `bootstrap.sh` rewrites that block.

## Backups

- Every night at `BACKUP_TIME` the server stops the containers that use SQLite (~15 s), takes
  a restic snapshot of `/opt/stacks` into `~/backups/restic`, and keeps 7 daily, 4 weekly and
  6 monthly snapshots. Caches are excluded (each module's `backup-excludes.txt`).
- The Mac copies that repo to `~/Backups/<name>/restic` once a day while awake. Its SSH key is
  limited to read-only rsync of that one folder, so a stolen Mac can't touch the server.
- The repo password is in `~/.config/restic/password` on the server and in the Mac Keychain
  (item `<name>-restic`). **Save it in a password manager.** Without it the backups can't be opened.

Check them:

```sh
./deploy.sh --status                                # next run, latest snapshot, last Mac pull
RESTIC_PASSWORD_COMMAND='security find-generic-password -s <name>-restic -w' \
  restic -r ~/Backups/<name>/restic snapshots
```

## Rebuild from scratch

1. Install Ubuntu and run `./setup.sh` with your existing `config.env`; skip Hermes' setup.
   You get the same image versions as before: they're pinned in the repo.
2. Stop the containers, then restore the app data from the Mac copy:
   ```sh
   restic -r ~/Backups/<name>/restic restore latest --target /tmp/restore
   rsync -a /tmp/restore/opt/stacks/ <user>@<server>:/opt/stacks/
   ```
3. `./deploy.sh`. On the Mac, `mac/setup-mac.sh` re-authorises the pull key (`setup.sh`
   already did if you ran it from the Mac).
4. If the Tailscale IP changed: update the Tailscale DNS settings (and `TAILSCALE_IP` in
   `config.env`, if you set it).

## Let Hermes code and push to GitHub

Hermes works in `/opt/data/workspace` inside its container (`/opt/stacks/hermes/data/workspace`
on the host). Give each repo its own deploy key, so Hermes can only push to that one repo:

1. On the server: `ssh-keygen -t ed25519 -N '' -C hermes -f /opt/stacks/hermes/data/.ssh/myrepo_deploy`
2. On GitHub: the repo → Settings → Deploy keys → add the `.pub` file, tick **Allow write access**.
3. Clone it inside the container and make the key stick:
   ```sh
   docker exec -u $(id -u) -w /opt/data/workspace hermes sh -c '
     export GIT_SSH_COMMAND="ssh -i /opt/data/.ssh/myrepo_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
     git clone git@github.com:you/myrepo.git && cd myrepo &&
     git config core.sshCommand "$GIT_SSH_COMMAND" &&
     git config user.name Hermes && git config user.email you@users.noreply.github.com'
   ```
4. Recommended: add a `.git/hooks/pre-push` that runs your lint, tests and build, so a broken
   commit never leaves the box.
5. Tell Hermes about the repo and your rules (branch or straight to `main`, what to test).
   It saves them in its memory.

Revoke access any time by deleting the deploy key on GitHub.

## Let Hermes wake other computers

Set `WAKE_TARGETS="gpubox=aa:bb:cc:dd:ee:ff"` (name=MAC, space-separated) and run `./deploy.sh`.
Hermes gets a `wake-device` skill: it writes a name to `/opt/data/wake/request`, and a small
user service on the host (`hermes-wake.path`, no root) sends the Wake-on-LAN packet, since
broadcasts from Hermes' container don't leave its Docker network. A device that wakes some other
way can have a URL instead of a MAC (`laptop=http://buddy.local/api/wake`): the host POSTs to it,
e.g. a Pi cabled to a Wi-Fi-only box's Ethernet port (`extras/wake-relay`), or an ESP32-S3
plugged in as a USB keyboard. Ask it on Telegram or WhatsApp
to "wake the gpubox". The other computer has to allow Wake-on-LAN (see `extras/wake-on-lan`).

## Extras

- `extras/it87-fan/`: makes the fan quieter on boards whose BIOS sets a loud minimum fan speed
  (many ITE IT87xx Super I/O chips). See its README.
- `extras/wifi-watchdog/`: reconnects Wi-Fi when it stays connected but stops passing traffic.
  See its README.
- `extras/wake-on-lan/`: wakes the box over the network from suspend or off (`WAKE_ON_LAN_*`).
  See its README.
- `extras/wake-relay/`: wakes the box while it's on Wi-Fi, through a Pi cabled to its Ethernet
  port (`WAKE_RELAY_*`). See its README.

## Not covered

Wi-Fi/ethernet config (netplan), hostname, and router settings: they depend on your hardware
and network. Local models need a GPU box (the `ai` module); an 8 GB box without one can't run
useful ones, so point its Hermes at a cloud provider or at the GPU box on your tailnet.

## License

MIT
