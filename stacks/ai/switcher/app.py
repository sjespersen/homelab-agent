"""Local AI switcher: shows which GPU service runs and switches between them.

Only one service can have the GPU, so switching stops the others first. Talks to Docker
through a proxy that allows listing, starting and stopping containers and nothing else.
The services are the containers labelled homelab.ai.title (see compose.yaml). Login is the
AI API key; Caddy also asks /_ai/auth before letting anyone into SwarmUI.

Without Docker (SERVICES_FILE set, e.g. on a Mac), the services are commands listed in that
JSON file, and the switcher starts and stops them itself (see process_services).

The chat API (/v1) goes through here too: a request naming the other chat model's name
(homelab.ai.model) switches to it first, and after IDLE_MINUTES without requests or GPU load
the chat model is stopped; the next request starts it again.
"""

import glob
import hashlib
import hmac
import http.client
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ["API_KEY"]
DOCKER = os.environ.get("DOCKER_PROXY", "socket-proxy:2375")
# The services as commands to run, in place of Docker containers (see process_services).
SERVICES_FILE = os.environ.get("SERVICES_FILE", "")
STATE = os.environ.get("STATE_FILE", "/state/active")
# Where the page and the API listen: all interfaces in a container, 127.0.0.1 on your own computer.
LISTEN = os.environ.get("LISTEN", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8079"))
# The LED switch (AI_LEDS=yes): this only writes "on"/"off"; a root service on the host applies
# it with OpenRGB and writes leds.status (stacks/ai/host/homelab-leds.sh).
LEDS = os.environ.get("LEDS") == "yes"
LEDS_FILE = os.path.join(os.path.dirname(STATE), "leds")
LEDS_STATUS = LEDS_FILE + ".status"
# White unless a color is picked: "on" with a color effect (Static, Breathing ...) and no color
# would keep the board's last one, which after "off" is black.
# Suspend, restart, shut down: also only written to a file, for a root service on the host
# (stacks/ai/host/homelab-power.sh), which accepts nothing but these three words. Only with
# POWER=yes: without that service nothing would act on them.
POWER = os.environ.get("POWER") == "yes"
POWER_FILE = os.path.join(os.path.dirname(STATE), "power")
POWER_ACTIONS = ("suspend", "reboot", "poweroff")
# The chat model last given the GPU, for API requests that name no model while none runs
# (STATE says "none" after Stop or an idle stop).
LAST_CHAT_FILE = os.path.join(os.path.dirname(STATE), "last-chat")
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")   # AI_MODELS_DIR, read-only
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media")      # AI_MEDIA_DIR, read-only
# The page is also the box's landing page (port 80): its name, and the other services' links.
NAME = os.environ.get("NAME", "")
LINKS_FILE = os.environ.get("LINKS_FILE", "/site/links.json")

LEDS_DEFAULTS = {"state": "", "effect": os.environ.get("LEDS_EFFECT", "Rainbow wave"),
                 "speed": os.environ.get("LEDS_SPEED", ""), "color": "FFFFFF"}
SESSION = hmac.new(API_KEY.encode(), b"ai-switcher-session", hashlib.sha256).hexdigest()
COOKIE = "ai_session"

# The chat models' API port inside the stack's network (both listen on 8080).
API_PORT = 8080
# Ollama runs all the time (homelab.ai.always-on): it only takes the GPU while it has a model
# loaded. Open WebUI reaches it through /ollama here; requests that run a model give it the
# GPU first (stopping the 27B or Strata), and switching to another service unloads its models.
_ollama = urllib.parse.urlsplit(os.environ.get("OLLAMA_URL", "http://ollama:11434"))
OLLAMA_HOST, OLLAMA_PORT = _ollama.hostname, _ollama.port or 11434
OLLAMA_GPU_PATHS = {"/api/chat", "/api/generate", "/api/embed", "/api/embeddings",
                    "/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/responses"}
# Stop the chat model after this many minutes without API requests or GPU load; 0: never.
IDLE_MINUTES = float(os.environ.get("IDLE_MINUTES") or 0)
# A first start downloads the model: wait that long for it before giving up on a request.
START_TIMEOUT = 60 * 60
MAX_BODY = 200 * 1024 * 1024
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
               "trailer", "transfer-encoding", "upgrade", "host", "content-length"}

switching = {"to": None, "error": None}
# A hold on the GPU (the Studio while it renders): switching from the page is refused until it
# ends, so a click doesn't stop a batch halfway. The holder renews it; it lapses by itself.
hold = {"by": None, "until": 0}
lock = threading.Lock()
# API requests in flight per service, and when the API or the GPU was last busy. A switch for
# a request waits until the other service has no requests left. "waiting" counts requests
# from arrival to the end of their answer (an idle stop never starts while one is), and
# "stopping" is set while an idle stop runs (requests wait for it, then start the model again).
api = threading.Condition()
inflight = {}
activity = {"last": time.time(), "chat": None, "idle_stop": None, "waiting": 0, "stopping": False}


def docker(method, path):
    conn = http.client.HTTPConnection(DOCKER, timeout=90)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    if resp.status >= 400 and resp.status != 304:
        raise RuntimeError(f"docker {method} {path}: {resp.status} {body[:200]!r}")
    return json.loads(body) if body else None


def services():
    """The GPU services, in their order (homelab.ai.order, or the order in SERVICES_FILE)."""
    return process_services() if SERVICES_FILE else docker_services()


def start_service(s):
    if SERVICES_FILE:
        process_start(s)
    else:
        docker("POST", f"/containers/{s['container']}/start")


def stop_service(s):
    if SERVICES_FILE:
        process_stop(s)
    else:
        # Strata needs up to a minute to release its page-locked RAM.
        docker("POST", f"/containers/{s['container']}/stop?t=60")


def docker_services():
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
            "site": labels.get("homelab.ai.site", ""),
            # The names API requests use for it (the first is the one /v1/models lists).
            "models": [m.strip().lower() for m in labels.get("homelab.ai.model", "").split(",") if m.strip()],
            "order": int(labels.get("homelab.ai.order", "9")),
            # Never stopped for a switch; it gives the GPU back by unloading its models.
            "always_on": labels.get("homelab.ai.always-on") == "true",
            "state": c["State"],  # running, exited, created, ...
            "health": health,     # starting, healthy, unhealthy or None
            "started": detail["State"].get("StartedAt"),
            # Its model files, under MODELS_DIR: dropped from the page cache when another one starts.
            "files": labels.get("homelab.ai.files", ""),
            # Where its API answers: the compose service's name in the stack's network.
            "host": labels["com.docker.compose.service"],
            "port": int(labels.get("homelab.ai.port", API_PORT)),
        })
    return sorted(found, key=lambda s: s["order"])


# The process backend: SERVICES_FILE is {"services": [...]}, each entry with
#   id, title, about, kind (chat/image), models (API names), site, files, always_on: as the labels
#   command: what to run, as a list (e.g. ["llama-server", "-m", "...gguf", "--port", "8080"])
#   cwd, env: optional; port: where its API answers on 127.0.0.1 (host to change that)
#   health: a path that answers 200 once it's ready (e.g. /health); without one, an open port
# The switcher's own children: they stop with it, and a restart of the switcher finds nothing
# running (STATE then says what ran; the next request or switch starts it again).
procs = {}
proc_started = {}


def process_services():
    with open(SERVICES_FILE) as f:
        conf = json.load(f)
    found = []
    for i, e in enumerate(conf.get("services", [])):
        sid = e["id"]
        p = procs.get(sid)
        running = p is not None and p.poll() is None
        host, port = e.get("host", "127.0.0.1"), int(e.get("port", API_PORT))
        found.append({
            "id": sid,
            "title": e.get("title", sid),
            "about": e.get("about", ""),
            "kind": e.get("kind", "chat"),
            "site": e.get("site", ""),
            "models": [m.strip().lower() for m in e.get("models", []) if m.strip()],
            "order": int(e.get("order", i + 1)),
            "always_on": bool(e.get("always_on")),
            "state": "running" if running else ("exited" if p is not None else "created"),
            "health": answers(host, port, e.get("health")) if running else None,
            "started": proc_started.get(sid),
            "files": e.get("files", ""),
            "host": host,
            "port": port,
            "_entry": e,
        })
    return sorted(found, key=lambda s: s["order"])


def answers(host, port, path):
    """Docker's health words for a process: healthy once it answers, starting until then."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            if path:
                conn.request("GET", path)
                ok = conn.getresponse().status == 200
            else:
                conn.connect()
                ok = True
        finally:
            conn.close()
    except OSError:
        ok = False
    return "healthy" if ok else "starting"


def process_start(s):
    e = s["_entry"]
    log = open(os.path.join(os.path.dirname(STATE), f"{s['id']}.log"), "ab")
    procs[s["id"]] = subprocess.Popen(
        [os.path.expanduser(a) for a in e["command"]], cwd=os.path.expanduser(e.get("cwd") or "~"),
        env={**os.environ, **{k: str(v) for k, v in e.get("env", {}).items()}},
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)  # its own process group, so stopping it also stops its children
    log.close()
    proc_started[s["id"]] = datetime.now(timezone.utc).isoformat()


def process_stop(s, timeout=60):
    p = procs.get(s["id"])
    if p is None or p.poll() is not None:
        return
    os.killpg(p.pid, signal.SIGTERM)
    try:
        p.wait(timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def stop_all_processes(*_):
    for sid, p in list(procs.items()):
        if p.poll() is None:
            process_stop({"id": sid}, timeout=20)
    raise SystemExit(0)


def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        name, used, total, util, temp, power = [x.strip() for x in out.splitlines()[0].split(",")]
        try:
            power = round(float(power))
        except ValueError:
            power = None  # "[N/A]" on some cards
        return {"name": name, "used": int(used), "total": int(total), "util": int(util), "temp": int(temp),
                "power": power}
    except Exception:
        return None


# The CPU package's power in watts (homelab-cpu-power.service writes it every 2 s, from RAPL),
# and what the rest of the box roughly draws on top of CPU and GPU (board, RAM, SSDs, fans,
# power supply losses), for the page's total: an estimate, not a reading at the socket.
CPU_POWER_FILE = os.path.join(os.path.dirname(STATE), "cpu-power")
REST_WATTS = 35
_cpu_last = {}


def cpu():
    """CPU load in % since the last call (the host's /proc/stat), temperature, power."""
    out = {"cores": os.cpu_count()}

    def sample():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:9]]
        return v[3] + v[4], sum(v)  # idle + iowait, total

    try:
        if "v" not in _cpu_last:
            _cpu_last["v"] = sample()
            time.sleep(0.25)
        idle, total = sample()
        p_idle, p_total = _cpu_last["v"]
        if total > p_total:
            out["load"] = round(100 * (1 - (idle - p_idle) / (total - p_total)))
        _cpu_last["v"] = (idle, total)
    except (OSError, ValueError, IndexError):
        pass
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(h + "/name") as f:
                if f.read().strip() not in ("k10temp", "zenpower", "coretemp"):
                    continue
            with open(h + "/temp1_input") as f:
                out["temp"] = round(int(f.read()) / 1000)
            break
        except (OSError, ValueError):
            continue
    try:
        with open(CPU_POWER_FILE) as f:
            watts, at = f.read().split()
        if time.time() - int(at) < 10:
            out["power"] = int(watts)
    except (OSError, ValueError):
        pass
    return out


def ram():
    """The host's RAM in MiB (a container's /proc/meminfo is the host's): Strata locks ~50 GB."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0]) // 1024
        return {"total": info["MemTotal"], "used": info["MemTotal"] - info["MemAvailable"]}
    except (OSError, KeyError, ValueError, IndexError):
        return None


def disks():
    """Fill level in MiB of the models, system and media disks. The system disk is read through the
    state folder, a bind mount like the others (the container's own / is an overlay with another
    fsid); a media folder that isn't its own disk counts as part of that disk."""
    out, seen = [], set()
    for name, path in (("Models", MODELS_DIR), ("System", os.path.dirname(STATE)), ("Media", MEDIA_DIR)):
        try:
            st = os.statvfs(path)
        except OSError:
            continue
        if st.f_fsid in seen:
            continue
        seen.add(st.f_fsid)
        # Like df: blocks reserved for root count as neither used nor free.
        mib = lambda blocks: blocks * st.f_frsize // 2**20
        out.append({"name": name, "total": mib(st.f_blocks), "used": mib(st.f_blocks - st.f_bfree),
                    "free": mib(st.f_bavail)})
    return out


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


def drop_cache(sub):
    """Drops a stopped service's model files from the page cache (posix_fadvise; no root needed).
    The kernel frees that cache by itself, but slowly when the next service wants a lot of RAM at
    once: Strata's 47 GB of huge pages waited ~80 s for SwarmUI's cached models, 0 s after this
    (which takes under a second)."""
    root = os.path.realpath(MODELS_DIR)
    top = os.path.realpath(os.path.join(root, sub))
    if not top.startswith(root + os.sep) or not hasattr(os, "posix_fadvise"):
        return
    for d, _, files in os.walk(top):
        for f in files:
            try:
                fd = os.open(os.path.join(d, f), os.O_RDONLY | os.O_NOFOLLOW)
            except OSError:
                continue
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except OSError:
                pass
            finally:
                os.close(fd)


def switch(target):
    """Gives the GPU to service <target>, or to none ("none": stop them all)."""
    try:
        all_services = services()
        if target != "none" and target not in {s["id"] for s in all_services}:
            raise RuntimeError(f"unknown service {target}")
        for s in all_services:
            if s["id"] == target:
                continue
            if s["always_on"]:
                if s["state"] == "running":
                    ollama_unload()
            elif s["state"] in ("running", "restarting"):
                stop_service(s)
        if target != "none":
            for s in all_services:
                if s["id"] != target and not s["always_on"] and s["files"]:
                    drop_cache(s["files"])
        for s in all_services:
            if (s["id"] == target or s["always_on"]) and s["state"] != "running":
                start_service(s)
        with open(STATE, "w") as f:
            f.write(target + "\n")
        if any(s["id"] == target and s["models"] for s in all_services):
            activity["chat"] = target
            with open(LAST_CHAT_FILE, "w") as f:
                f.write(target + "\n")
        switching["error"] = None
    except Exception as e:
        switching["error"] = str(e)
    finally:
        switching["to"] = None
        lock.release()


def ollama_call(method, path, body=None, timeout=10):
    conn = http.client.HTTPConnection(OLLAMA_HOST, OLLAMA_PORT, timeout=timeout)
    try:
        conn.request(method, path, body=json.dumps(body) if body is not None else None,
                     headers={"Content-Type": "application/json"})
        data = conn.getresponse().read()
        return json.loads(data) if data else {}
    finally:
        conn.close()


def ollama_loaded():
    """The models Ollama has in memory (its /api/ps), or [] if it doesn't answer."""
    try:
        return [m.get("name") or m.get("model") for m in ollama_call("GET", "/api/ps").get("models", [])]
    except (OSError, ValueError):
        return []


def ollama_unload():
    """Unloads Ollama's models (keep_alive 0) and waits until their GPU memory is free."""
    for name in ollama_loaded():
        try:
            ollama_call("POST", "/api/generate", {"model": name, "keep_alive": 0}, timeout=60)
        except (OSError, ValueError):
            pass
    for _ in range(30):
        if not ollama_loaded():
            return
        time.sleep(1)
    raise RuntimeError("Ollama didn't unload its models")


def ensure_ollama():
    """Gives the GPU to Ollama for a request that runs a model: stops the 27B or Strata after
    their requests finish. Returns an error message, or None."""
    deadline = time.time() + START_TIMEOUT
    while True:
        svcs = services()
        ollama = next((s for s in svcs if s["always_on"]), None)
        if ollama is None:
            return "no Ollama in this stack"
        others = [s for s in svcs if not s["always_on"] and s["state"] in ("running", "restarting")]
        if ollama["state"] == "running" and not others:
            return None
        image = [s for s in others if s["kind"] == "image"]
        if image:
            return f"{image[0]['title']} has the GPU; switch on the Local AI page"
        # Let requests to the other model finish first.
        with api:
            api.wait_for(lambda: not any(n for sid, n in inflight.items() if sid != ollama["id"]),
                         timeout=max(1, deadline - time.time()))
        if lock.acquire(timeout=120):
            switching["to"] = ollama["id"]
            switch(ollama["id"])  # releases the lock
            if switching["error"]:
                return switching["error"]
        if time.time() > deadline:
            return "Ollama didn't get the GPU in time"
        time.sleep(1)


def started_at(service):
    """Docker's StartedAt as a Unix time (0 if it never ran)."""
    try:
        when = datetime.fromisoformat(service["started"][:19]).replace(tzinfo=timezone.utc)
        return when.timestamp()
    except (TypeError, ValueError):
        return 0


def chat_target(model, all_services):
    """The chat service for a request's model name: the service whose homelab.ai.model names
    it (or a longer name starting with it, like Strata's qwen3.8-flash-next-iq3_s), else the
    chat model that runs, else the last one used (kept across restarts), else the first."""
    chat = [s for s in all_services if s["models"]]
    if not chat:
        return None
    name = model.strip().lower() if isinstance(model, str) else ""
    for s in chat:
        if name and (name == s["id"] or any(name == m or name.startswith(m + "-") for m in s["models"])):
            return s
    for s in chat:
        if s["state"] == "running":
            return s
    candidates = [activity["chat"]]
    for path in (LAST_CHAT_FILE, STATE):
        try:
            with open(path) as f:
                candidates.append(f.read().strip())
        except OSError:
            pass
    for last in candidates:
        found = next((s for s in chat if s["id"] == last), None)
        if found:
            return found
    return chat[0]


def ensure_running(target):
    """Gives the GPU to <target> unless it has it, and waits until it answers. Returns an
    error message, or None."""
    deadline = time.time() + START_TIMEOUT
    while True:
        # An idle stop that began before this request: let it finish, then start the model.
        with api:
            api.wait_for(lambda: not activity["stopping"], timeout=max(1, deadline - time.time()))
        svcs = {s["id"]: s for s in services()}
        s = svcs[target]
        if s["state"] == "running" and s["health"] in ("healthy", None):
            return None
        if s["state"] != "running" and switching["to"] == target:
            pass  # the page is switching to it already (the image service may not have stopped yet)
        elif s["state"] != "running":
            image = [o for o in svcs.values() if o["kind"] == "image" and o["state"] == "running"]
            if image:
                return f"{image[0]['title']} has the GPU; switch on the Local AI page"
            # Let requests to the other model finish first.
            with api:
                api.wait_for(lambda: not any(n for sid, n in inflight.items() if sid != target),
                             timeout=max(1, deadline - time.time()))
            if lock.acquire(timeout=120):
                if {x["id"]: x for x in services()}[target]["state"] != "running":
                    switching["to"] = target
                    switch(target)  # releases the lock
                    if switching["error"]:
                        return switching["error"]
                else:
                    lock.release()
        elif s["state"] == "running" and s["health"] == "unhealthy" and time.time() - started_at(s) > 600:
            return f"{s['title']} runs but doesn't answer"
        if time.time() > deadline:
            return f"{s['title']} didn't start in time"
        time.sleep(2)


def idle_watch():
    """Stops the chat model after IDLE_MINUTES without API requests or GPU load."""
    while True:
        time.sleep(30)
        g = gpu()
        if g and g["util"] >= 10:
            activity["last"] = time.time()  # e.g. Open WebUI talking to it directly
        with api:
            busy = activity["waiting"] > 0
        if busy or switching["to"]:
            continue
        try:
            running = [s for s in services() if s["models"] and s["state"] == "running"]
        except Exception:
            continue
        for s in running:
            if time.time() - max(activity["last"], started_at(s)) < IDLE_MINUTES * 60:
                continue
            if not lock.acquire(blocking=False):
                continue
            # Check again together with the requests' counter: a request that arrived since
            # cancels the stop, and one arriving from now on waits for it.
            with api:
                if activity["waiting"] or time.time() - activity["last"] < IDLE_MINUTES * 60:
                    lock.release()
                    continue
                activity["stopping"] = True
            try:
                switching["to"] = "none"
                switch("none")  # releases the lock
                activity["idle_stop"] = {"service": s["title"], "at": int(time.time())}
            finally:
                with api:
                    activity["stopping"] = False
                    api.notify_all()


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

    def api_authed(self):
        """The API key as a Bearer token, or as x-api-key (Anthropic clients). Not the page's
        login cookie: a browser sends that along with requests other sites make."""
        auth = self.headers.get("Authorization", "")
        key = auth[7:].strip() if auth.startswith("Bearer ") else self.headers.get("x-api-key", "").strip()
        return bool(key and hmac.compare_digest(key, API_KEY))

    def read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return body
                body += self.rfile.read(size)
                self.rfile.readline()
                if len(body) > MAX_BODY:
                    return None
        length = int(self.headers.get("Content-Length") or 0)
        return None if length > MAX_BODY else self.rfile.read(length)

    def api_error(self, code, message):
        self.json({"error": {"message": message, "type": "homelab_switcher"}}, code)

    def chat_api(self):
        """/v1: answers /v1/models itself, sends everything else to the chat model the request
        names (switching to it first) or the one that runs."""
        if not self.api_authed():
            return self.api_error(401, "missing or wrong API key")
        try:
            all_services = services()
        except Exception as e:
            return self.api_error(502, str(e))
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if self.command == "GET" and path == "/v1/models":
            return self.json({"object": "list", "data": [
                {"id": s["models"][0], "object": "model", "owned_by": "homelab", "service": s["id"],
                 "loaded": s["state"] == "running"} for s in all_services if s["models"]]})
        body = self.read_body() if self.command in ("POST", "PUT", "PATCH") else b""
        if body is None:
            return self.api_error(413, "request too large")
        model = None
        if body and "json" in self.headers.get("Content-Type", "json"):
            try:
                req = json.loads(body)
                model = req.get("model") if isinstance(req, dict) else None
            except ValueError:
                pass
        target = chat_target(model, all_services)
        if target is None:
            return self.api_error(503, "no chat model in this stack")
        with api:
            activity["waiting"] += 1
            activity["last"] = time.time()
        try:
            error = ensure_running(target["id"])
            if error:
                return self.api_error(503, error)
            activity["chat"] = target["id"]
            with api:
                inflight[target["id"]] = inflight.get(target["id"], 0) + 1
            try:
                self.proxy(target["host"], target["port"], self.path, body)
            finally:
                with api:
                    inflight[target["id"]] -= 1
                    api.notify_all()
        finally:
            with api:
                activity["waiting"] -= 1
                activity["last"] = time.time()
                api.notify_all()

    def ollama_api(self):
        """/ollama: Ollama's own API for Open WebUI (models, downloads, chats). A request that
        runs a model gives the GPU to Ollama first. No login: Caddy doesn't pass /ollama on, so
        only the stack's network (Open WebUI) and the host reach it, as they reach Ollama."""
        path = self.path[len("/ollama"):] or "/"
        # Caddy blocks /ollama; anything it passes on anyway (it adds X-Forwarded-For) is refused.
        if not path.startswith("/") or self.headers.get("X-Forwarded-For"):
            return self.send(404, "not found")
        body = self.read_body() if self.command in ("POST", "PUT", "PATCH", "DELETE") else b""
        if body is None:
            return self.json({"error": "request too large"}, 413)
        runs_model = urllib.parse.urlsplit(path).path in OLLAMA_GPU_PATHS
        if runs_model and body:
            try:
                req = json.loads(body)
                # An unload request (keep_alive 0 and nothing to answer) needs no GPU.
                if isinstance(req, dict) and str(req.get("keep_alive")) in ("0", "0s") \
                        and not (req.get("prompt") or req.get("messages")):
                    runs_model = False
            except ValueError:
                pass
        if not runs_model:
            return self.proxy(OLLAMA_HOST, OLLAMA_PORT, path, body)
        error = ensure_ollama()
        if error:
            return self.json({"error": error}, 503)
        with api:
            inflight["ollama"] = inflight.get("ollama", 0) + 1
        try:
            self.proxy(OLLAMA_HOST, OLLAMA_PORT, path, body)
        finally:
            with api:
                inflight["ollama"] -= 1
                api.notify_all()

    def proxy(self, host, port, path, body):
        """Passes the request on and streams the answer back as it comes (server-sent events)."""
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        conn = http.client.HTTPConnection(host, port, timeout=3600)
        try:
            conn.request(self.command, path, body=body or None, headers=headers)
            resp = conn.getresponse()
        except OSError as e:
            conn.close()
            return self.api_error(502, f"{host}: {e}")
        try:
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_HEADERS or (k.lower() == "content-length" and resp.length is not None):
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            while chunk := resp.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass  # the client went away; closing the upstream connection ends the generation
        finally:
            conn.close()

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
        if url.path == "/ollama" or url.path.startswith("/ollama/"):
            return self.ollama_api()
        if url.path == "/v1" or url.path.startswith("/v1/"):
            return self.chat_api()
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
        if url.path == "/_ai/api/links":
            try:
                with open(LINKS_FILE) as f:
                    links = json.load(f)
            except (OSError, ValueError):
                links = []
            return self.json({"name": NAME, "links": links})
        if url.path == "/_ai/api/status":
            try:
                svcs = services()
            except Exception as e:
                return self.json({"error": str(e)}, 502)
            for s in svcs:
                if s["always_on"] and s["state"] == "running":
                    s["loaded"] = ollama_loaded()
                    try:
                        s["installed"] = len(ollama_call("GET", "/api/tags", timeout=5).get("models", []))
                    except (OSError, ValueError):
                        s["installed"] = None
            for s in svcs:
                s.pop("_entry", None)  # the command and its env stay on the box
                # When the idle watcher stops it (for the page's countdown), if nothing comes first.
                if IDLE_MINUTES > 0 and s["models"] and s["state"] == "running":
                    s["idle_stop_at"] = max(activity["last"], started_at(s)) + IDLE_MINUTES * 60
            g, c = gpu(), cpu()
            total = None
            if g and g.get("power") is not None and c.get("power") is not None:
                total = g["power"] + c["power"] + REST_WATTS
            return self.json({"services": svcs, "gpu": g, "cpu": c, "ram": ram(), "disks": disks(), "total_power": total,
                              "rest_watts": REST_WATTS, "switching": switching["to"],
                              "error": switching["error"], "leds": leds(), "power": POWER,
                              "idle_minutes": IDLE_MINUTES, "idle_stop": activity["idle_stop"],
                              "requests": activity["waiting"], "now": time.time(),
                              "hold": hold["by"] if time.time() < hold["until"] else None})
        self.send(404, "not found")

    def do_DELETE(self):
        # Ollama's DELETE /api/delete (Open WebUI removing a model).
        if urllib.parse.urlsplit(self.path).path.startswith("/ollama/"):
            return self.ollama_api()
        self.send(404, "not found")

    def do_OPTIONS(self):
        # CORS preflight for browser clients of the API; the answers carry the model's own headers.
        self.send(204, headers=[("Access-Control-Allow-Origin", "*"),
                                ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                                ("Access-Control-Allow-Headers", "Authorization, Content-Type, x-api-key")])

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/ollama" or url.path.startswith("/ollama/"):
            return self.ollama_api()
        if url.path == "/v1" or url.path.startswith("/v1/"):
            return self.chat_api()
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
            if not POWER:
                return self.json({"error": "the power buttons are off (POWER)"}, 404)
            try:
                action = json.loads(raw or "{}").get("action")
            except (ValueError, AttributeError):
                action = None
            if action not in POWER_ACTIONS:
                return self.json({"error": "action must be suspend, reboot or poweroff"}, 400)
            with open(POWER_FILE, "w") as f:
                f.write(action + "\n")
            return self.json({"power": action}, 202)
        if url.path == "/_ai/api/hold":
            try:
                req = json.loads(raw or "{}")
                seconds = max(0, min(600, int(req.get("seconds", 0))))
                by = str(req.get("by") or "something")[:40]
            except (ValueError, TypeError, AttributeError):
                return self.json({"error": "bad request"}, 400)
            hold.update(by=by if seconds else None, until=time.time() + seconds if seconds else 0)
            return self.json({"hold": hold["by"], "until": hold["until"]})
        if url.path == "/_ai/api/switch":
            try:
                req = json.loads(raw or "{}")
                target = req.get("service", "")
            except (ValueError, AttributeError):
                return self.json({"error": "bad request"}, 400)
            if hold["by"] and time.time() < hold["until"] and not req.get("force"):
                return self.json({"error": f"{hold['by']} is using the GPU; stop it there first"}, 409)
            if not lock.acquire(blocking=False):
                return self.json({"error": f"already switching to {switching['to']}"}, 409)
            switching["to"] = target
            threading.Thread(target=switch, args=(target,), daemon=True).start()
            return self.json({"switching": target}, 202)
        self.send(404, "not found")


# The mark: a chip on green, for the favicon and the header logo (the page adds a status dot).
CHIP = ("<rect x='9' y='9' width='14' height='14' rx='2.5' fill='none' stroke='white' stroke-width='2.2'/>"
        "<path d='M13 4.5v4.5M19 4.5v4.5M13 23v4.5M19 23v4.5M4.5 13h4.5M4.5 19h4.5M23 13h4.5M23 19h4.5'"
        " stroke='white' stroke-width='2.2' stroke-linecap='round'/><circle cx='16' cy='16' r='2.6' fill='white'/>")
FAVICON = ("<link rel='icon' id='favicon' href=\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'"
           " viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='%232f6f4f'/>" + CHIP + "</svg>\">")
LOGO = ("<span class='logo'><svg viewBox='0 0 32 32' aria-hidden='true'>" + CHIP + "</svg></span>")


def template(name):
    """The page's HTML, from its file next to this one, with the shared style and logo filled in."""
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        html = f.read()
    return html.replace("{{style}}", STYLE).replace("{{favicon}}", FAVICON).replace("{{logo}}", LOGO)


HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "style.css"), encoding="utf-8") as f:
    STYLE = f.read()
LOGIN = template("login.html")
PAGE = template("page.html")

if __name__ == "__main__":
    if SERVICES_FILE:
        signal.signal(signal.SIGTERM, stop_all_processes)
        signal.signal(signal.SIGINT, stop_all_processes)
    if IDLE_MINUTES > 0:
        threading.Thread(target=idle_watch, daemon=True).start()
    ThreadingHTTPServer((LISTEN, PORT), Handler).serve_forever()
