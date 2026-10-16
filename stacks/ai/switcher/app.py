"""Local AI switcher: shows which GPU service runs and switches between them.

Only one service can have the GPU, so switching stops the others first. Talks to Docker
through a proxy that allows listing, starting and stopping containers and nothing else.
The services are the containers labelled homelab.ai.title (see compose.yaml). Login is the
AI API key; Caddy also asks /_ai/auth before letting anyone into SwarmUI.

The chat API (/v1) goes through here too: a request naming the other chat model's name
(homelab.ai.model) switches to it first, and after IDLE_MINUTES without requests or GPU load
the chat model is stopped; the next request starts it again.
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
from datetime import datetime, timezone
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
# The chat model last given the GPU, for API requests that name no model while none runs
# (STATE says "none" after Stop or an idle stop).
LAST_CHAT_FILE = os.path.join(os.path.dirname(STATE), "last-chat")
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
OLLAMA_HOST, OLLAMA_PORT = "ollama", 11434
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
            "site": labels.get("homelab.ai.site", ""),
            # The names API requests use for it (the first is the one /v1/models lists).
            "models": [m.strip().lower() for m in labels.get("homelab.ai.model", "").split(",") if m.strip()],
            "order": int(labels.get("homelab.ai.order", "9")),
            # Never stopped for a switch; it gives the GPU back by unloading its models.
            "always_on": labels.get("homelab.ai.always-on") == "true",
            "state": c["State"],  # running, exited, created, ...
            "health": health,     # starting, healthy, unhealthy or None
            "started": detail["State"].get("StartedAt"),
        })
    return sorted(found, key=lambda s: s["order"])


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
            if s["id"] == target:
                continue
            if s["always_on"]:
                if s["state"] == "running":
                    ollama_unload()
            elif s["state"] in ("running", "restarting"):
                # Strata needs up to a minute to release its page-locked RAM.
                docker("POST", f"/containers/{s['container']}/stop?t=60")
        for s in all_services:
            if (s["id"] == target or s["always_on"]) and s["state"] != "running":
                docker("POST", f"/containers/{s['container']}/start")
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
        if s["state"] != "running":
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
                self.proxy(target["id"], API_PORT, self.path, body)
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
            return self.json({"services": svcs, "gpu": gpu(), "ram": ram(), "switching": switching["to"],
                              "error": switching["error"], "leds": leds(),
                              "idle_minutes": IDLE_MINUTES, "idle_stop": activity["idle_stop"]})
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
:root { --bg:#f5f5f2; --card:#fff; --ink:#1c1c1a; --muted:#6b6b64; --faint:#9a9a92; --line:#e4e4de;
  --soft:#efefea; --accent:#2f6f4f; --accent-soft:#e3efe8; --warn:#9a6b00; --warn-soft:#f7ecd2;
  --bad:#b03a3a; --bad-soft:#f6e1e1; --on-accent:#fff; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#131312; --card:#1d1d1b; --ink:#ecece7; --muted:#a0a098;
  --faint:#77776f; --line:#30302c; --soft:#262623; --accent:#72c296; --accent-soft:#1f3329; --warn:#e3b350;
  --warn-soft:#3a2f17; --bad:#e57b7b; --bad-soft:#3b2020; --on-accent:#0f2419; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 20px 16px 56px; }
a { color: inherit; }
h1 { font-size: 22px; margin: 0; letter-spacing: -.01em; }
h2 { font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
  margin: 32px 0 10px; }
h3 { font-size: 16px; margin: 0; }
.sub, .hint { color: var(--muted); font-size: 13px; margin: 2px 0 0; }
.hint { margin: -4px 0 12px; }
.top { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;
  padding-bottom: 16px; border-bottom: 1px solid var(--line); }
.brand { display:flex; align-items:center; gap:12px; }
.logo { width:38px; height:38px; border-radius:10px; background:var(--accent); color:var(--on-accent); display:grid;
  place-items:center; font-weight:700; font-size:18px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
.grid { display:grid; gap:12px; grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr)); }
.tiles { display:grid; gap:12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-top: 20px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }
.tile .label { font-size:12px; color:var(--muted); }
.tile .value { font-size:22px; font-weight:600; margin-top:2px; font-variant-numeric: tabular-nums; }
.tile .sub { font-size:12px; color:var(--faint); margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.meter { height:6px; background:var(--soft); border-radius:3px; overflow:hidden; margin-top:8px; }
.meter > i { display:block; height:100%; background:var(--accent); border-radius:3px; transition: width .4s; }
.meter.warn > i { background:var(--warn); } .meter.bad > i { background:var(--bad); }
.group + .group { margin-top: 18px; }
.group-title { font-size:14px; font-weight:600; margin: 0 0 2px; }
.svc { display:flex; flex-direction:column; gap:10px; position:relative; }
.svc.active { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
.svc-head { display:flex; justify-content:space-between; align-items:flex-start; gap:10px; }
.about { color:var(--muted); font-size:13px; margin:0; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.chip { font-size:12px; padding:2px 8px; border-radius:999px; background:var(--soft); color:var(--muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.chip.plain { font-family: inherit; }
.pill { display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:500; padding:3px 9px;
  border-radius:999px; white-space:nowrap; background:var(--soft); color:var(--muted); }
.pill::before { content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }
.pill.on { background:var(--accent-soft); color:var(--accent); }
.pill.busy { background:var(--warn-soft); color:var(--warn); }
.pill.bad { background:var(--bad-soft); color:var(--bad); }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:auto; }
button, .btn { font:inherit; font-size:14px; padding:7px 14px; border-radius:8px; border:1px solid var(--line);
  background:var(--card); color:var(--ink); cursor:pointer; text-decoration:none; display:inline-flex;
  align-items:center; gap:6px; }
button:hover:not(:disabled), .btn:hover { border-color: var(--faint); }
button.primary { background:var(--accent); border-color:var(--accent); color:var(--on-accent); font-weight:500; }
button.small { font-size:13px; padding:4px 10px; }
button:disabled { opacity:.45; cursor:default; }
button.confirm { background:var(--bad); border-color:var(--bad); color:#fff; }
.links { display:grid; gap:10px; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.link { display:block; text-decoration:none; background:var(--card); border:1px solid var(--line);
  border-radius:12px; padding:12px 14px; }
.link:hover { border-color: var(--faint); }
.link .t { font-weight:600; } .link .d { color:var(--muted); font-size:13px; }
.link .u { color:var(--faint); font-size:12px; margin-top:4px; }
.kv { display:grid; grid-template-columns: 110px 1fr; gap:10px 16px; align-items:baseline; font-size:14px; }
.kv > dt { color:var(--muted); font-size:13px; }
.kv > dd { margin:0; min-width:0; }
.copyrow { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
code { font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; background:var(--soft); padding:2px 6px;
  border-radius:5px; overflow-wrap: anywhere; }
.banner { background:var(--bad-soft); color:var(--bad); border-radius:10px; padding:10px 14px; margin-top:16px; font-size:14px; }
.state { font-size:13px; color:var(--muted); }
.state.on { color:var(--accent); } .state.busy { color:var(--warn); } .state.bad { color:var(--bad); }
.controls { display:flex; gap:12px 20px; flex-wrap:wrap; margin-top:4px; padding-top:12px; border-top:1px solid var(--line); }
.controls label { display:flex; gap:8px; align-items:center; font-size:13px; color:var(--muted); }
.controls select { font:inherit; font-size:13px; padding:5px 8px; border-radius:7px; border:1px solid var(--line);
  background:var(--bg); color:var(--ink); }
.controls input[type=range] { width:130px; }
.controls input[type=color] { width:40px; height:28px; padding:0 2px; border:1px solid var(--line); border-radius:6px; background:var(--bg); }
.login { max-width: 380px; margin: 12vh auto 0; }
.login input { font:inherit; padding:9px 10px; width:100%; border:1px solid var(--line); border-radius:8px;
  background:var(--bg); color:var(--ink); margin:6px 0 14px; }
.err { color:var(--bad); font-size:14px; }
footer { margin-top: 36px; color: var(--faint); font-size: 12px; }
@media (max-width: 560px) { .kv { grid-template-columns: 1fr; gap: 2px; } .kv > dd { margin-bottom: 10px; } }
"""

LOGIN = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · Local AI</title>
<style>""" + STYLE + """</style></head><body><main>
<form class="card login" method="post" action="/_ai/login">
<div class="brand" style="margin-bottom:14px"><span class="logo">AI</span><div><h1>Local AI</h1>
<p class="sub">Sign in with the AI API key.</p></div></div>
<p class="err" id="wrong" hidden>That key is wrong.</p>
<label for="key" class="state">API key (<code>API_KEY</code> in /opt/stacks/ai/data/.env on the server)</label>
<input id="key" name="key" type="password" autocomplete="current-password" autofocus>
<input type="hidden" name="next" id="next"><button class="primary">Sign in</button></form>
<script>
const q = new URLSearchParams(location.search);
document.getElementById('next').value = q.get('next') || '/';
document.getElementById('wrong').hidden = !q.get('wrong');
</script></main></body></html>"""

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Local AI</title>
<style>""" + STYLE + """</style></head><body><main>
<header class="top">
  <div class="brand"><span class="logo">AI</span>
    <div><h1 id="name">Local AI</h1><div class="sub" id="holder">Loading…</div></div></div>
  <a class="btn" href="/_ai/logout">Sign out</a>
</header>
<div class="banner" id="error" hidden></div>
<div class="tiles" id="tiles"></div>

<h2>Models</h2>
<p class="hint">One service at a time has the GPU. Picking a model in the web chat or through the API
switches to it by itself; the buttons here do it by hand.</p>
<div id="groups"></div>

<h2>Services</h2>
<div class="links" id="links"></div>

<h2>Connect</h2>
<div class="card"><dl class="kv">
  <dt>API base URL</dt><dd><div class="copyrow"><code id="api"></code>
    <button class="small" id="copy-api">Copy</button></div></dd>
  <dt>Key</dt><dd>The API key, as <code>Authorization: Bearer &lt;key&gt;</code> or <code>x-api-key</code>.</dd>
  <dt>Models</dt><dd><div class="chips" id="api-models"></div>
    <div class="state">OpenAI-compatible; Strata also speaks Anthropic's <code>/v1/messages</code>.
    Naming a model in a request gives it the GPU first.</div></dd>
  <dt>Idle</dt><dd class="state" id="idle"></dd>
</dl></div>

<h2>Box</h2>
<div class="grid">
  <div class="card svc"><div class="svc-head"><h3>Power</h3></div>
    <p class="about">The model is stopped before suspend and started again after waking. Wake it with
    <code>./ai-model.sh wake</code> (Wake-on-LAN) or the power button.</p>
    <div class="state" id="power-state"></div>
    <div class="actions"><button data-power="suspend" data-label="Suspend">Suspend</button>
    <button data-power="reboot" data-label="Restart">Restart</button>
    <button data-power="poweroff" data-label="Shut down">Shut down</button></div></div>
  <div id="leds"></div>
</div>
<footer>Updates every 3 seconds.</footer>
<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let LINKS = [];
// The services' links (install.sh writes them; {{.Host}} is the address this page was opened at).
function siteLink(module, site) {
  const l = LINKS.find(x => x.module === module && x.site === site);
  return l ? l.url : '';
}
async function loadLinks() {
  try {
    const r = await fetch('/_ai/api/links');
    if (!r.ok) return;
    const d = await r.json();
    if (d.name) { document.getElementById('name').textContent = d.name; document.title = d.name + ' · Local AI'; }
    LINKS = (d.links || []).map(l => ({...l, url: l.url.replace('{{.Host}}', location.hostname)}));
  } catch (e) { return; }
  document.getElementById('links').innerHTML = LINKS.filter(l => !(l.module === 'ai' && l.site === 'main')).map(l =>
    `<a class="link" href="${esc(l.url)}"><div class="t">${esc(l.title)}</div><div class="d">${esc(l.description)}</div>
     <div class="u">${esc(l.url.replace(/^https?:\\/\\//, '').replace(/\\/$/, ''))}</div></a>`).join('')
    || '<div class="state">No other services on this box.</div>';
  const main = siteLink('ai', 'main');
  document.getElementById('api').textContent = (main ? main.replace(/\\/$/, '') : location.origin) + '/v1';
}
document.getElementById('copy-api').onclick = e => copy(document.getElementById('api').textContent, e.target);
async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch (err) {  // plain http on the LAN has no clipboard API
    const t = document.createElement('textarea'); t.value = text; document.body.append(t); t.select();
    document.execCommand('copy'); t.remove();
  }
  btn.textContent = 'Copied'; setTimeout(() => btn.textContent = 'Copy', 1500);
}
const holds = s => s.always_on ? !!(s.loaded && s.loaded.length) : s.state === 'running';
const shortName = m => String(m).split('/').pop();
function stateText(s, switchingTo) {
  if (switchingTo === s.id) return ['busy', 'switching…'];
  if (switchingTo && s.always_on && s.loaded && s.loaded.length) return ['busy', 'unloading…'];
  if (switchingTo && s.state === 'running' && !s.always_on) return ['busy', 'stopping…'];
  if (s.state !== 'running') return ['', 'stopped'];
  if (s.always_on) return holds(s) ? ['on', 'has the GPU'] : ['', 'ready'];
  if (s.health === 'starting') return ['busy', 'starting'];
  if (s.health === 'unhealthy') return ['bad', 'not answering'];
  return ['on', 'has the GPU'];
}
const GROUPS = [
  ['Chat models', 'For the API (Hermes, scripts) and the web chat.', s => s.models && s.models.length],
  ['Web chat models', 'Your Ollama models, in Open WebUI. Ollama runs all the time; a chat loads a model.', s => s.always_on],
  ['Images', 'Image generation.', s => s.kind === 'image'],
  ['Other', '', s => true],
];
function svcCard(s, busy, switchingTo) {
  const [cls, text] = stateText(s, switchingTo);
  const chips = [];
  if (s.models && s.models.length) chips.push(`<span class="chip">${esc(s.models[0])}</span>`);
  if (s.always_on) {
    if (s.installed != null) chips.push(`<span class="chip plain">${s.installed} installed</span>`);
    (s.loaded || []).forEach(m => chips.push(`<span class="chip" title="${esc(m)}">loaded: ${esc(shortName(m))}</span>`));
  }
  if (!s.always_on && s.health === 'starting') chips.push('<span class="chip plain">a first start downloads the model</span>');
  const link = s.site ? siteLink('ai', s.site) : '';
  const open = link && s.state === 'running' ? `<a class="btn" href="${esc(link)}">Open</a>` : '';
  const btn = holds(s)
    ? `<button ${busy ? 'disabled' : ''} onclick="go('none')">${s.always_on ? 'Unload' : 'Stop'}</button>`
    : `<button class="primary" ${busy ? 'disabled' : ''} onclick="go('${esc(s.id)}')">Switch to this</button>`;
  return `<div class="card svc ${holds(s) ? 'active' : ''}">
    <div class="svc-head"><h3>${esc(s.title)}</h3><span class="pill ${cls}">${text}</span></div>
    <p class="about">${esc(s.about)}</p>
    ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
    <div class="actions">${open}${btn}</div></div>`;
}
function tile(label, value, sub, pct, level) {
  const meter = pct == null ? '' : `<div class="meter ${level || ''}"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></div>`;
  return `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div>${meter}<div class="sub">${sub}</div></div>`;
}
const gb = mib => (mib / 1024).toFixed(1);
async function load() {
  let r;
  try { r = await fetch('/_ai/api/status'); } catch (e) { return; }
  if (r.status === 401) { location.href = '/_ai/login?next=/'; return; }
  const d = await r.json();
  const g = d.gpu, m = d.ram, t = [];
  if (g) {
    const memPct = 100 * g.used / g.total;
    t.push(tile('GPU memory', gb(g.used) + ' GB', 'of ' + gb(g.total) + ' GB', memPct, memPct > 95 ? 'warn' : ''));
    t.push(tile('GPU load', g.util + '%', esc(g.name.replace(/^NVIDIA (GeForce )?/, '')), g.util));
    t.push(tile('GPU temperature', g.temp + ' °C', g.temp >= 83 ? 'hot' : 'normal', 100 * g.temp / 90, g.temp >= 83 ? 'bad' : g.temp >= 75 ? 'warn' : ''));
    if (g.power != null) t.push(tile('GPU power', g.power + ' W', 'right now', null));
  } else t.push(tile('GPU', '–', 'status unavailable', null));
  if (m) { const p = 100 * m.used / m.total;
    t.push(tile('RAM', gb(m.used) + ' GB', 'of ' + gb(m.total) + ' GB', p, p > 92 ? 'bad' : p > 80 ? 'warn' : '')); }
  document.getElementById('tiles').innerHTML = t.join('');
  const svcs = d.services || [], busy = !!d.switching;
  const holder = svcs.find(holds), target = svcs.find(s => s.id === d.switching);
  document.getElementById('holder').innerHTML = d.switching
    ? `<span class="state busy">Switching to ${esc(target ? target.title : d.switching)}…</span>`
    : holder ? `<span class="state on">● ${esc(holder.title)}${holder.always_on ? ' (' + esc(shortName(holder.loaded[0])) + ')' : ''} has the GPU</span>`
    : 'The GPU is free';
  const used = new Set();
  document.getElementById('groups').innerHTML = GROUPS.map(([title, note, match]) => {
    const list = svcs.filter(s => !used.has(s.id) && match(s));
    list.forEach(s => used.add(s.id));
    return list.length ? `<div class="group"><div class="group-title">${title}</div>${note ? `<p class="hint" style="margin:0 0 8px">${note}</p>` : ''}
      <div class="grid">${list.map(s => svcCard(s, busy, d.switching)).join('')}</div></div>` : '';
  }).join('');
  document.getElementById('api-models').innerHTML = svcs.filter(s => s.models && s.models.length)
    .map(s => `<span class="chip">${esc(s.models[0])}</span>`).join('');
  const err = document.getElementById('error');
  err.textContent = d.error || ''; err.hidden = !d.error;
  const idle = d.idle_stop;
  document.getElementById('idle').textContent = (d.idle_minutes
    ? `The chat model stops after ${d.idle_minutes} min without requests; the next request starts it again (1-2 min).`
    : 'Chat models stay loaded until you stop them.')
    + (idle ? ` Last stopped when idle: ${idle.service}, ${new Date(idle.at * 1000).toLocaleString()}.` : '');
  renderLeds(d.leds);
}
// The LED card is built once; later loads only update it, so a control you're using isn't redrawn.
function renderLeds(l) {
  const box = document.getElementById('leds');
  if (!l) { box.innerHTML = ''; return; }
  if (!box.firstChild) {
    box.innerHTML = `<div class="card svc"><div class="svc-head"><h3>Case lighting</h3><span class="pill" id="leds-state"></span></div>
      <p class="about" id="leds-devs"></p>
      <div class="controls">
        <label>Effect <select id="leds-effect"></select></label>
        <label>Speed <span>slow</span><input type="range" id="leds-speed" min="0" max="100" step="5"><span>fast</span></label>
        <label>Color <input type="color" id="leds-color"></label>
      </div>
      <div class="actions"><button id="leds-off">Off</button><button id="leds-on">On</button></div></div>`;
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
  st.textContent = !s.state ? 'not set' : (l.applied ? (on ? 'on' : 'off') : 'applying…');
  st.className = 'pill ' + (l.applied ? (on ? 'on' : '') : 'busy');
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
function showError(text) { const e = document.getElementById('error'); e.textContent = text; e.hidden = !text; }
async function setLeds(change) {
  const r = await fetch('/_ai/api/leds', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(change)});
  if (!r.ok) showError((await r.json()).error);
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
  if (!r.ok) showError((await r.json()).error);
  load();
}
loadLinks().then(load); setInterval(load, 3000);
</script></main></body></html>"""

if __name__ == "__main__":
    if IDLE_MINUTES > 0:
        threading.Thread(target=idle_watch, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8079), Handler).serve_forever()
