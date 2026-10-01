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

## Security

This is a home lab, not a hardened production setup. The defaults keep everything off the
internet, but anyone or anything that gets **inside** (onto your LAN, your tailnet, or into the
agent) gets a lot. Read this before you put anything valuable on the box.

### What the defaults protect

- SSH accepts keys only; passwords and root login are off.
- ufw blocks DNS, the web UIs and the Hermes ports (8642, 9119) from everywhere except
  `TRUSTED_NETS` and Tailscale. Pi-hole, n8n, Uptime Kuma and Beszel listen on `127.0.0.1` only;
  outside the box they are reachable only through Caddy.
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

**2. The agent pushes straight to production.** If you let Hermes push to `main` of a repo that
deploys automatically, one successful injection ships attacker code to your live site. A
pre-push hook checks that the code builds, not what it does.
*Reduce it:* let Hermes push to a branch and merge pull requests yourself. Use branch
protection on `main`.

**3. No isolation between containers.** Every stack uses host networking, so every container
can reach every other service's `127.0.0.1` port (Pi-hole admin, n8n, Uptime Kuma, Beszel and
its agent) without going through Caddy. A compromised Hermes or n8n can attack the rest
directly; each service's own login is the only barrier.
*Reduce it:* use strong, unique passwords for every web UI. For real isolation, move the stacks
to Docker bridge networks (more setup; note that `ports:` mappings bypass ufw).

**4. Several paths to root.** Anything that controls Docker controls the host:
- The Beszel agent mounts the Docker socket. `:ro` does not limit the Docker API, so a
  compromised Beszel image or agent is root on the host.
- Your user is in the `docker` group, so your SSH key is effectively a root key, and the sudo
  password doesn't protect anything.
- n8n workflows can run code (Code node, Execute Command node) on the host network.
*Reduce it:* protect your SSH key with a passphrase; drop the socket mount from
`stacks/beszel/compose.yaml` if you don't need per-container stats; make sure n8n's Execute
Command node is off (`NODES_EXCLUDE`; newer n8n versions turn it off by default).

**5. Ransomware can reach the backups.** Anyone who controls the server can delete or encrypt
`~/backups/restic` (the password sits next to it in `~/.config/restic/password`). The Mac
pulls with `rsync --delete`, so the next pull mirrors that damage onto the Mac copy.
*Reduce it:* keep Time Machine (or any versioned backup) running on the Mac so older copies
survive; check `restic snapshots` now and then.

**6. Plain HTTP on the LAN.** Logins for Hermes (basic auth), n8n, Uptime Kuma, Beszel and
Pi-hole travel unencrypted when you use them over the LAN. Anyone on the same network can
capture them: a guest on your Wi-Fi, a compromised smart TV or IoT device. `TRUSTED_NETS`
usually means "your whole LAN".
*Reduce it:* use the services over Tailscale (encrypted) even at home, and narrow
`TRUSTED_NETS` or drop the LAN entries entirely. Put IoT and guest devices on a guest network.

**7. Everyone on your tailnet gets everything.** ufw allows all traffic on `tailscale0`. Any
device you add, any node you share with someone, and anyone who takes over your Tailscale
account can reach every port on the box.
*Reduce it:* turn on 2FA for the account behind Tailscale; use
[Tailscale ACLs](https://tailscale.com/kb/1018/acls) to limit who reaches the server; remove
old devices.

**8. SSH is open to everyone, not just the LAN.** `ufw allow 22/tcp` has no source limit. On
IPv4 that means your LAN. On IPv6 the server has a public address, so port 22 is reachable from
the internet unless your router's IPv6 firewall blocks incoming connections (most do by
default; check yours). Keys-only login stops password guessing, not a future SSH vulnerability.
*Reduce it:* limit SSH to `TRUSTED_NETS` and `tailscale0` once Tailscale works, or check
that your router blocks incoming IPv6.

**9. Unpinned, unupdated images.** Most images use `:latest`, so you trust whatever the
maintainers publish next. `--pull missing` never updates them on its own, so known holes stay
until you update. Ubuntu installs its own security updates (unattended-upgrades); containers
don't.
*Reduce it:* update monthly (`docker compose pull && docker compose up -d` in each stack);
pin versions where you care; follow the release notes for Hermes and n8n.

**10. Secrets at rest.** Ubuntu's default install has no disk encryption, so whoever takes the
box gets every key and session on it. Secrets also sit in plain files under `/opt/stacks`
(Hermes `data/.env` and `auth.json`, n8n's encryption key) and in every backup.
*Reduce it:* choose disk encryption when you install Ubuntu (you then type the passphrase at
every boot); keep the box somewhere it won't walk off.

### Smaller items

- Hermes' API on port 8642 gives full agent access to anyone with the key on `TRUSTED_NETS` or
  the tailnet. Treat that key like a password.
- n8n webhooks are open by design to anyone who can reach n8n. Add authentication in the
  webhook node.
- Tailscale is installed with `curl | sh`, and `bootstrap.sh` runs as root: read scripts before
  you run them, including these.
- The Mac pulls with rsync from the server; rsync clients have had bugs a malicious server
  could exploit. Keep Homebrew's rsync updated.
- Avahi announces the server's name and services on the LAN.

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
