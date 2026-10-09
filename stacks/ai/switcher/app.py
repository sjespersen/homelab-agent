"""Local AI switcher: shows which GPU service runs and switches between them.

Only one service can have the GPU, so switching stops the others first. Talks to Docker
through a proxy that allows listing, starting and stopping containers and nothing else.
The services are the containers labelled homelab.ai.title (see compose.yaml). Login is the
AI API key; Caddy also asks /_ai/auth before letting anyone into SwarmUI.
"""

import hashlib
import hmac
import http.client
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ["API_KEY"]
DOCKER = os.environ.get("DOCKER_PROXY", "socket-proxy:2375")
STATE = os.environ.get("STATE_FILE", "/state/active")
# The LED switch (AI_LEDS=yes): this only writes "on"/"off"; a root service on the host applies
# it with OpenRGB and writes leds.status (stacks/ai/host/homelab-leds.sh).
LEDS = os.environ.get("LEDS") == "yes"
LEDS_FILE = os.path.join(os.path.dirname(STATE), "leds")
LEDS_STATUS = LEDS_FILE + ".status"
# White unless a color is picked: "on" with a color effect (Static, Breathing ...) and no color
# would keep the board's last one, which after "off" is black.
# Suspend, restart, shut down: also only written to a file, for a root service on the host
# (stacks/ai/host/homelab-power.sh), which accepts nothing but these three words.
POWER_FILE = os.path.join(os.path.dirname(STATE), "power")
POWER_ACTIONS = ("suspend", "reboot", "poweroff")

LEDS_DEFAULTS = {"state": "", "effect": os.environ.get("LEDS_EFFECT", "Rainbow wave"),
                 "speed": os.environ.get("LEDS_SPEED", ""), "color": "FFFFFF"}
SESSION = hmac.new(API_KEY.encode(), b"ai-switcher-session", hashlib.sha256).hexdigest()
COOKIE = "ai_session"

switching = {"to": None, "error": None}
lock = threading.Lock()


def docker(method, path):
    conn = http.client.HTTPConnection(DOCKER, timeout=90)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status >= 400 and resp.status != 304:
        raise RuntimeError(f"docker {method} {path}: {resp.status} {body[:200]!r}")
    return json.loads(body) if body else None


def services():
    """The GPU services, in the order compose.yaml lists them (homelab.ai.order)."""
    flt = urllib.parse.quote(json.dumps({"label": ["homelab.ai.title"]}))
    found = []
    for c in docker("GET", f"/containers/json?all=1&filters={flt}"):
        labels = c["Labels"]
        detail = docker("GET", f"/containers/{c['Id']}/json")
        health = (detail["State"].get("Health") or {}).get("Status")
        found.append({
            "id": labels["com.docker.compose.service"],
            "container": c["Id"],
            "title": labels["homelab.ai.title"],
            "about": labels.get("homelab.ai.about", ""),
            "kind": labels.get("homelab.ai.kind", "chat"),
            "order": int(labels.get("homelab.ai.order", "9")),
            "state": c["State"],  # running, exited, created, ...
            "health": health,     # starting, healthy, unhealthy or None
            "started": detail["State"].get("StartedAt"),
        })
    return sorted(found, key=lambda s: s["order"])


def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        name, used, total, util, temp = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"name": name, "used": int(used), "total": int(total), "util": int(util), "temp": int(temp)}
    except Exception:
        return None


def leds_settings():
    """The settings file as the host's homelab-leds.sh reads it (same checks, same defaults)."""
    settings = dict(LEDS_DEFAULTS)
    try:
        with open(LEDS_FILE) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if not sep and key in ("on", "off"):
            settings["state"] = key
        elif key == "state" and value in ("on", "off"):
            settings["state"] = value
        elif key == "effect" and re.fullmatch(r"[A-Za-z0-9 ]{1,40}", value):
            settings["effect"] = value
        elif key == "speed" and re.fullmatch(r"[0-9]{1,3}", value) and int(value) <= 100:
            settings["speed"] = value
        elif key == "color" and re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            settings["color"] = value
    return settings


def effects(devices):
    """The effects all devices offer, in the first device's order (Direct needs a program
    streaming colors, and Off is the Off button)."""
    lists = []
    for d in devices:
        names = shlex.split(re.sub(r"\[([^]]*)\]", r"'\1'", d.get("modes", "")))
        lists.append([n for n in names if n.lower() not in ("direct", "off")])
    if not lists:
        return []
    common = set.intersection(*[{n.lower() for n in names} for names in lists])
    return [n for n in lists[0] if n.lower() in common]


def leds():
    if not LEDS:
        return None
    settings = leds_settings()
    try:
        with open(LEDS_STATUS) as f:
            status = json.load(f)
    except (OSError, ValueError):
        status = {}
    keys = ("state",) if settings["state"] == "off" else ("state", "effect", "speed", "color")
    applied = bool(settings["state"]) and all(
        str(status.get(k, "")).lower() == str(settings[k]).lower() for k in keys)
    devices = status.get("devices", [])
    return {"settings": settings, "applied": applied, "effects": effects(devices),
            "devices": [{"name": d.get("name"), "mode": d.get("mode")} for d in devices]}


def switch(target):
    """Gives the GPU to service <target>, or to none ("none": stop them all)."""
    try:
        all_services = services()
        if target != "none" and target not in {s["id"] for s in all_services}:
            raise RuntimeError(f"unknown service {target}")
        for s in all_services:
            if s["id"] != target and s["state"] in ("running", "restarting"):
                # Strata needs up to a minute to release its page-locked RAM.
                docker("POST", f"/containers/{s['container']}/stop?t=60")
        for s in all_services:
            if s["id"] == target and s["state"] != "running":
                docker("POST", f"/containers/{s['container']}/start")
        with open(STATE, "w") as f:
            f.write(target + "\n")
        switching["error"] = None
    except Exception as e:
        switching["error"] = str(e)
    finally:
        switching["to"] = None
        lock.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def authed(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), API_KEY):
            return True
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(value, SESSION):
                return True
        return False

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/_ai/auth":
            # Caddy's forward_auth for SwarmUI: 2xx lets the request through.
            if self.authed():
                return self.send(204)
            uri = self.headers.get("X-Forwarded-Uri", "/")
            return self.send(302, headers=[("Location", "/_ai/login?next=" + urllib.parse.quote(uri))])
        if url.path == "/_ai/login":
            return self.send(200, LOGIN, "text/html; charset=utf-8")
        if url.path == "/_ai/logout":
            return self.send(302, headers=[("Location", "/_ai/login"),
                                           ("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0")])
        if not self.authed():
            if url.path.startswith("/_ai/api/"):
                return self.json({"error": "login needed"}, 401)
            return self.send(302, headers=[("Location", "/_ai/login?next=/")])
        if url.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if url.path == "/_ai/api/status":
            try:
                svcs = services()
            except Exception as e:
                return self.json({"error": str(e)}, 502)
            return self.json({"services": svcs, "gpu": gpu(), "switching": switching["to"],
                              "error": switching["error"], "leds": leds()})
        self.send(404, "not found")

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 10000)).decode()
        if url.path == "/_ai/login":
            form = urllib.parse.parse_qs(raw)
            key = (form.get("key") or [""])[0].strip()
            nxt = (form.get("next") or ["/"])[0]
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = "/"
            if hmac.compare_digest(key, API_KEY):
                time.sleep(0.2)
                cookie = f"{COOKIE}={SESSION}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000"
                return self.send(303, headers=[("Location", nxt), ("Set-Cookie", cookie)])
            time.sleep(1)  # slows down guessing
            return self.send(303, headers=[("Location", "/_ai/login?wrong=1&next=" + urllib.parse.quote(nxt))])
        if not self.authed():
            return self.json({"error": "login needed"}, 401)
        if url.path == "/_ai/api/leds":
            if not LEDS:
                return self.json({"error": "the LED switch is off (AI_LEDS)"}, 404)
            try:
                body = json.loads(raw or "{}")
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return self.json({"error": "bad request"}, 400)
            settings = leds_settings()
            state = body.get("state", settings["state"] or "on")
            if state not in ("on", "off"):
                return self.json({"error": "state must be on or off"}, 400)
            settings["state"] = state
            if "effect" in body:
                known = {e.lower() for e in leds()["effects"]}
                if str(body["effect"]).lower() not in known:
                    return self.json({"error": "unknown effect"}, 400)
                settings["effect"] = str(body["effect"])
            if "speed" in body:
                speed = str(body["speed"])
                if not re.fullmatch(r"[0-9]{1,3}", speed) or int(speed) > 100:
                    return self.json({"error": "speed must be 0-100"}, 400)
                settings["speed"] = speed
            if "color" in body:
                color = str(body["color"]).lstrip("#")
                if not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
                    return self.json({"error": "color must be RRGGBB"}, 400)
                settings["color"] = color.upper()
            with open(LEDS_FILE, "w") as f:
                f.write("".join(f"{k}={settings[k]}\n" for k in ("state", "effect", "speed", "color")))
            return self.json({"leds": settings}, 202)
        if url.path == "/_ai/api/power":
            try:
                action = json.loads(raw or "{}").get("action")
            except (ValueError, AttributeError):
                action = None
            if action not in POWER_ACTIONS:
                return self.json({"error": "action must be suspend, reboot or poweroff"}, 400)
            with open(POWER_FILE, "w") as f:
                f.write(action + "\n")
            return self.json({"power": action}, 202)
        if url.path == "/_ai/api/switch":
            try:
                target = json.loads(raw or "{}").get("service", "")
            except ValueError:
                return self.json({"error": "bad request"}, 400)
            if not lock.acquire(blocking=False):
                return self.json({"error": f"already switching to {switching['to']}"}, 409)
            switching["to"] = target
            threading.Thread(target=switch, args=(target,), daemon=True).start()
            return self.json({"switching": target}, 202)
        self.send(404, "not found")


STYLE = """
:root { --bg:#f6f6f4; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --line:#e2e2dd; --accent:#2f6f4f;
  --warn:#9a6b00; --bad:#a33; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#151514; --card:#1f1f1d; --ink:#ecece8;
  --muted:#9a9a93; --line:#33332f; --accent:#6fbf93; --warn:#e0b04a; --bad:#e07070; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 system-ui, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:12px; }
.row { display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
.title { font-weight:600; }
.about { color:var(--muted); font-size:13px; }
.state { font-size:13px; }
.on { color:var(--accent); } .busy { color:var(--warn); } .bad { color:var(--bad); } .off { color:var(--muted); }
button, .btn { font:inherit; padding:7px 14px; border-radius:7px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); cursor:pointer; text-decoration:none; display:inline-block; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button:disabled { opacity:.5; cursor:default; }
.bar { height:8px; background:var(--line); border-radius:4px; overflow:hidden; margin-top:8px; }
.bar > div { height:100%; background:var(--accent); }
.actions { display:flex; gap:8px; }
code { font-size:13px; }
input { font:inherit; padding:8px; width:100%; border:1px solid var(--line); border-radius:7px;
  background:var(--bg); color:var(--ink); margin:8px 0 12px; }
.err { color:var(--bad); }
button.confirm { background:var(--bad); border-color:var(--bad); color:#fff; }
.controls { display:flex; gap:12px 20px; flex-wrap:wrap; margin-top:14px; padding-top:12px;
  border-top:1px solid var(--line); }
.controls label { display:flex; gap:8px; align-items:center; font-size:13px; color:var(--muted); }
.controls select { font:inherit; padding:6px 8px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--ink); }
.controls input[type=range] { width:140px; }
.controls input[type=color] { width:44px; height:30px; padding:0 2px; margin:0; }
"""

LOGIN = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Local AI login</title>
<style>""" + STYLE + """</style></head><body><main>
<h1>Local AI</h1><p class="sub">Sign in with the AI API key (API_KEY in /opt/stacks/ai/data/.env on the server).</p>
<form class="card" method="post" action="/_ai/login">
<p class="err" id="wrong" hidden>That key is wrong.</p>
<label for="key">API key</label><input id="key" name="key" type="password" autocomplete="current-password" autofocus>
<input type="hidden" name="next" id="next"><button class="primary">Sign in</button></form>
<script>
const q = new URLSearchParams(location.search);
document.getElementById('next').value = q.get('next') || '/';
document.getElementById('wrong').hidden = !q.get('wrong');
</script></main></body></html>"""

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Local AI</title>
<style>""" + STYLE + """</style></head><body><main>
<div class="row"><div><h1>Local AI</h1><p class="sub">One service at a time has the GPU.</p></div>
<a class="btn" href="/_ai/logout">Sign out</a></div>
<div class="card" id="gpu">Loading…</div>
<div id="services"></div>
<div id="leds"></div>
<div class="card"><div class="row"><div><div class="title">Power</div>
<div class="about">The model is stopped before suspend and started again after waking.
Wake it with <code>./ai-model.sh wake</code> (Wake-on-LAN) or the power button.</div>
<div class="state" id="power-state"></div></div>
<div class="actions"><button data-power="suspend" data-label="Suspend">Suspend</button>
<button data-power="reboot" data-label="Restart">Restart</button>
<button data-power="poweroff" data-label="Shut down">Shut down</button></div></div></div>
<p class="err" id="error"></p>
<div class="card"><div class="title">API</div>
<div class="about">OpenAI-compatible base URL for the chat models: <code id="api"></code>,
key as Bearer token. Strata also speaks Anthropic's <code>/v1/messages</code>.</div></div>
<script>
const api = location.origin + '/v1';
document.getElementById('api').textContent = api;
// SwarmUI's address: the next port with plain ports, or swarm.<domain> with Pi-hole names.
function swarmUrl() {
  if (location.port) return location.protocol + '//' + location.hostname + ':' + (Number(location.port) + 1) + '/';
  return location.protocol + '//' + location.hostname.replace(/^[^.]+/, 'swarm') + '/';
}
function stateText(s, switchingTo) {
  if (switchingTo === s.id) return ['busy', 'switching…'];
  if (switchingTo && s.state === 'running') return ['busy', 'stopping…'];
  if (s.state !== 'running') return ['off', 'stopped'];
  if (s.health === 'starting') return ['busy', 'starting (first start downloads the model)'];
  if (s.health === 'unhealthy') return ['bad', 'running, not answering'];
  return ['on', 'running'];
}
async function load() {
  let r;
  try { r = await fetch('/_ai/api/status'); } catch (e) { return; }
  if (r.status === 401) { location.href = '/_ai/login?next=/'; return; }
  const d = await r.json();
  const g = d.gpu;
  document.getElementById('gpu').innerHTML = g
    ? `<div class="row"><span class="title">${g.name}</span><span class="state">${(g.used/1024).toFixed(1)} of ${(g.total/1024).toFixed(1)} GB · ${g.util}% busy · ${g.temp} °C</span></div>
       <div class="bar"><div style="width:${Math.round(100*g.used/g.total)}%"></div></div>`
    : 'GPU status unavailable';
  const busy = !!d.switching;
  document.getElementById('services').innerHTML = (d.services || []).map(s => {
    const [cls, text] = stateText(s, d.switching);
    const running = s.state === 'running';
    const open = s.kind === 'image' && running ? `<a class="btn" href="${swarmUrl()}">Open</a>` : '';
    const btn = running
      ? `<button ${busy ? 'disabled' : ''} onclick="go('none')">Stop</button>`
      : `<button class="primary" ${busy ? 'disabled' : ''} onclick="go('${s.id}')">Switch to this</button>`;
    return `<div class="card"><div class="row"><div><div class="title">${s.title}</div>
      <div class="about">${s.about}</div><div class="state ${cls}">${text}</div></div>
      <div class="actions">${open}${btn}</div></div></div>`;
  }).join('');
  document.getElementById('error').textContent = d.error || '';
  renderLeds(d.leds);
}
// The LED card is built once; later loads only update it, so a control you're using isn't redrawn.
function renderLeds(l) {
  const box = document.getElementById('leds');
  if (!l) { box.innerHTML = ''; return; }
  if (!box.firstChild) {
    box.innerHTML = `<div class="card"><div class="row"><div><div class="title">Case lighting</div>
      <div class="about" id="leds-devs"></div><div class="state" id="leds-state"></div></div>
      <div class="actions"><button id="leds-off">Off</button><button id="leds-on">On</button></div></div>
      <div class="controls">
        <label>Effect <select id="leds-effect"></select></label>
        <label>Speed <span>slow</span><input type="range" id="leds-speed" min="0" max="100" step="5"><span>fast</span></label>
        <label>Color <input type="color" id="leds-color"></label>
      </div></div>`;
    document.getElementById('leds-off').onclick = () => setLeds({state: 'off'});
    document.getElementById('leds-on').onclick = () => setLeds({state: 'on'});
    document.getElementById('leds-effect').onchange = e => setLeds({state: 'on', effect: e.target.value});
    document.getElementById('leds-speed').onchange = e => setLeds({state: 'on', speed: e.target.value});
    document.getElementById('leds-color').onchange = e => setLeds({state: 'on', color: e.target.value});
  }
  const s = l.settings, on = s.state === 'on';
  document.getElementById('leds-devs').textContent =
    l.devices.map(x => x.name).join(' · ') || 'RGB on the board headers, through OpenRGB';
  const st = document.getElementById('leds-state');
  st.textContent = !s.state ? 'not set yet' : (l.applied ? (on ? 'on' : 'off') : 'applying… (about 20 s)');
  st.className = 'state ' + (l.applied ? (on ? 'on' : 'off') : 'busy');
  document.getElementById('leds-off').disabled = s.state === 'off';
  document.getElementById('leds-on').disabled = on;
  const sel = document.getElementById('leds-effect');
  if (sel.options.length !== l.effects.length) {
    sel.replaceChildren(...l.effects.map(name => new Option(name, name)));
  }
  const active = document.activeElement;
  if (active !== sel) {
    const match = l.effects.find(e => e.toLowerCase() === (s.effect || '').toLowerCase());
    if (match) sel.value = match;
  }
  const speed = document.getElementById('leds-speed');
  if (active !== speed) speed.value = s.speed === '' ? 50 : s.speed;
  const color = document.getElementById('leds-color');
  if (active !== color) color.value = '#' + (s.color || 'FFFFFF').toLowerCase();
}
async function setLeds(change) {
  const r = await fetch('/_ai/api/leds', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(change)});
  if (!r.ok) document.getElementById('error').textContent = (await r.json()).error;
  load();
}
// Power buttons confirm with a second click on the button itself (no browser dialog, which a
// browser may block): the first click arms it for 5 seconds.
const powerText = {
  suspend: ['Really suspend?', 'Suspending… the page stops answering until the box wakes up.'],
  reboot: ['Really restart?', 'Restarting… back in about two minutes.'],
  poweroff: ['Really shut down?', 'Shutting down… it comes back with the power button (or Wake-on-LAN, if the BIOS allows it).'],
};
document.querySelectorAll('[data-power]').forEach(b => b.onclick = () => power(b));
async function power(b) {
  const action = b.dataset.power, state = document.getElementById('power-state');
  if (!b.classList.contains('confirm')) {
    document.querySelectorAll('[data-power]').forEach(o => { o.classList.remove('confirm'); o.textContent = o.dataset.label; });
    b.classList.add('confirm');
    b.textContent = powerText[action][0];
    clearTimeout(b.timer);
    b.timer = setTimeout(() => { b.classList.remove('confirm'); b.textContent = b.dataset.label; }, 5000);
    return;
  }
  clearTimeout(b.timer);
  b.classList.remove('confirm');
  b.textContent = b.dataset.label;
  state.className = 'state busy';
  state.textContent = 'Sending…';
  try {
    const r = await fetch('/_ai/api/power', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action})});
    if (r.status === 401) { location.href = '/_ai/login?next=/'; return; }
    state.textContent = r.ok ? powerText[action][1] : (await r.json()).error;
    if (!r.ok) state.className = 'state bad';
  } catch (e) {
    state.className = 'state bad';
    state.textContent = 'Could not reach the box: ' + e.message;
  }
}
async function go(id) {
  const r = await fetch('/_ai/api/switch', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({service: id})});
  if (!r.ok) document.getElementById('error').textContent = (await r.json()).error;
  load();
}
load(); setInterval(load, 3000);
</script></main></body></html>"""

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8079), Handler).serve_forever()
