# homelab-agent

Turn a cheap used mini PC into an always-on AI agent server. The reference box is a €70
Shuttle DS61 (Intel i5-3475S, 8 GB RAM) from a classifieds site.

The agent's model runs in the cloud (your OpenAI/Anthropic/OpenRouter account, or a local model
if you have the hardware). The box hosts the agent, its memory, its tools and everything around
it, 24/7.

| Service | Address | What it does |
|---|---|---|
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `hermes.home.arpa`, `<server>:9119` | The agent. Chat with it on Telegram/WhatsApp, let it code and push to GitHub |
| [n8n](https://n8n.io) | `n8n.home.arpa` | Workflow automation; can call Hermes' OpenAI-compatible API |
| [Pi-hole](https://pi-hole.net) | `pihole.home.arpa` | DNS with ad and tracker blocking; also serves the `*.home.arpa` names |
| [Uptime Kuma](https://github.com/louislam/uptime-kuma) | `status.home.arpa` | Uptime checks and alerts |
| [Beszel](https://beszel.dev) | `server.home.arpa` | CPU, RAM, disk, temperatures, fans, per-container stats |
| [Caddy](https://caddyserver.com) | port 80 | Gives every service a name; landing page at `http://<name>.local` |
| [Tailscale](https://tailscale.com) | — | Reach everything from anywhere, no open ports |
| [restic](https://restic.net) | — | Nightly encrypted backups, pulled to your Mac |

Everything is code: a fresh Ubuntu Server becomes this setup with two scripts, and a dead box
is rebuilt from the repo plus the latest backup.

## What you need

- Any x86-64 PC with 8 GB RAM and an SSD. Tested on Ubuntu Server 26.04.
- A computer to deploy from. The deploy scripts work from macOS or Linux; the backup pull in
  `mac/` is macOS-only.
- A free [Tailscale](https://tailscale.com) account.
- An LLM provider account for Hermes.

## Set up

1. Install Ubuntu Server with OpenSSH. Reserve the server's IP in your router.
2. From your computer, copy your SSH key to it: `ssh-copy-id <user>@<server-ip>`
3. `cp config.example.env config.env` and fill it in (about 2 minutes).
4. `./deploy.sh`. This copies the repo to `~/homelab-agent` on the server. The stacks fail
   until Docker exists; that's expected.
5. On the server, in a real terminal: `sudo bash ~/homelab-agent/bootstrap.sh`
   (installs Docker, restic, Tailscale and the firewall; switches SSH to keys only).
   Open the Tailscale login link it prints.
6. Put the Tailscale IP it prints into `config.env` as `TAILSCALE_IP`, then run `./deploy.sh` again.
7. Set up Hermes (model, login, chat apps):
   `ssh -t <server> 'cd /opt/stacks/hermes && docker compose run --rm -it gateway setup'`,
   then `./deploy.sh` once more to start it.
8. In the [Tailscale admin console](https://login.tailscale.com/admin/dns), add the server's
   Tailscale IP as a nameserver. All your Tailscale devices now resolve `*.home.arpa` and get
   ad blocking.
9. Open `http://<name>.local` and create the owner accounts for n8n, Uptime Kuma and Beszel
   (in Beszel, add the system with host `127.0.0.1`, port `45876`).
10. On a Mac: `mac/setup-mac.sh` starts the daily backup pull.

Total time: about an hour, most of it waiting for downloads.

## Change something

Edit a compose file in `stacks/` or `stacks/caddy/conf/Caddyfile`, then `./deploy.sh`.
Compose files read `${TZ}`, `${DOMAIN}` and friends from `config.env`.

## Security model

- SSH is keys only. Port 22 is the only port open to everyone on the LAN.
- DNS, the web UIs and the Hermes ports (8642, 9119) are reachable from `TRUSTED_NETS` and
  Tailscale only (ufw). Nothing is exposed to the internet.
- Traffic is plain HTTP: it stays on your LAN or inside Tailscale's encrypted tunnel.
- Hermes' dashboard uses basic auth and its API uses a key, both stored in
  `/opt/stacks/hermes/data/.env`.
- The Beszel agent can read the Docker socket, which is root-equivalent on the host.
- Hermes can run code on the box. Give it its own keys with the smallest access that works
  (see below), never your personal ones.

## Backups

- Every night at `BACKUP_TIME` the server stops the containers that use SQLite (~15 s), takes
  a restic snapshot of `/opt/stacks` into `~/backups/restic`, and keeps 7 daily, 4 weekly and
  6 monthly snapshots. Caches are excluded (`backup/excludes.txt`).
- The Mac copies that repo to `~/Backups/<name>/restic` once a day while awake. Its SSH key is
  limited to read-only rsync of that one folder, so a stolen Mac can't touch the server.
- The repo password is in `~/.config/restic/password` on the server and in the Mac Keychain
  (item `<name>-restic`). **Save it in a password manager.** Without it the backups can't be opened.

Check them:

```sh
ssh <server> systemctl --user list-timers           # next run on the server
cat ~/Backups/<name>/last-pull.txt                  # last pull on the Mac
RESTIC_PASSWORD_COMMAND='security find-generic-password -s <name>-restic -w' \
  restic -r ~/Backups/<name>/restic snapshots
```

## Rebuild from scratch

1. Follow **Set up** steps 1–6.
2. Stop the containers, then restore the app data from the Mac copy:
   ```sh
   restic -r ~/Backups/<name>/restic restore latest --target /tmp/restore
   rsync -a /tmp/restore/opt/stacks/ <user>@<server>:/opt/stacks/
   ```
3. `./deploy.sh`, then `mac/setup-mac.sh` to re-authorise the pull key.
4. If the Tailscale IP changed: update `config.env` and the Tailscale DNS settings.

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

## Extras

- `extras/it87-fan/`: makes the fan quieter on boards whose BIOS sets a loud minimum fan speed
  (many ITE IT87xx Super I/O chips). See its README.

## Not covered

Wi-Fi/ethernet config (netplan), hostname, and router settings: they depend on your hardware
and network. Local models: an 8 GB box without a GPU can't run useful ones; point Hermes at a
cloud provider or at a bigger machine on your tailnet.

## License

MIT
