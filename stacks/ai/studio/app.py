"""The Studio: from a brief to finished shots on the GPU box.

  brief ─(Qwen writes the shot list)→ stills (Krea 2 + a reference photo per character)
        → previews (MiniMax H3, ~0.3 MP) → you approve → finals (same seed, latent upscale +
        refine to ~1 MP) → upscale (SeedVR2, 1080p) → export (clips + an EDL for Resolve)

Projects (brief, characters, reference photos, the shot list) live in DATA, which is backed up;
the renders in MEDIA, on the models disk. One job runs at a time; the jobs ask the switcher for
the GPU (Qwen for the shot list, SwarmUI for the rest) and hold it while they run.
"""
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import shutil
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import comfy
import workflows as wf

API_KEY = os.environ["API_KEY"]
DATA = os.environ.get("DATA", "/data")
MEDIA = os.environ.get("MEDIA", "/media")
MODELS = os.environ.get("MODELS", "/models")
SWITCHER = os.environ.get("SWITCHER", "http://switcher:8079")
LLM_MODEL = os.environ.get("STUDIO_LLM") or "qwen3.8-flash-next"
PORT = int(os.environ.get("PORT", "8078"))
# The switcher's login cookie: the same value, so one login covers the Local AI page and this.
SESSION = hmac.new(API_KEY.encode(), b"ai-switcher-session", hashlib.sha256).hexdigest()
COOKIE = "ai_session"
MAX_UPLOAD = 25 * 1024 * 1024
ID = re.compile(r"^[a-z0-9-]{1,48}$")
KINDS = ("shots", "still", "preview", "final", "upscale")
HERE = os.path.dirname(os.path.abspath(__file__))

lock = threading.RLock()       # the projects and the queue
projects = {}                   # id -> project dict (project.json)
queue = []                      # [{"project", "kind", "shot", "id"}]
current = {"task": None, "since": 0, "stop": False}
history = []                    # the last finished jobs, for the page


# --- Projects ------------------------------------------------------------------------------

def new_id(prefix=""):
    return prefix + secrets.token_hex(3)


def project_dir(pid):
    return os.path.join(DATA, "projects", pid)


def media_dir(pid, sid=None):
    return os.path.join(MEDIA, "projects", pid, *([sid] if sid else []))


def save(p):
    p["updated"] = int(time.time())
    d = project_dir(p["id"])
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "project.json.tmp")
    with open(tmp, "w") as f:
        json.dump(p, f, indent=1)
    os.replace(tmp, os.path.join(d, "project.json"))


def load_all():
    root = os.path.join(DATA, "projects")
    os.makedirs(root, exist_ok=True)
    for pid in os.listdir(root):
        try:
            with open(os.path.join(root, pid, "project.json")) as f:
                projects[pid] = json.load(f)
        except (OSError, ValueError):
            continue


def new_project(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30] or "project"
    p = {"id": f"{slug}-{new_id()}", "title": title[:120] or "Untitled", "created": int(time.time()),
         "brief": "", "aspect": "16:9", "target_shots": 12, "refine": "standard",
         "characters": [], "shots": [], "log": []}
    save(p)
    return p


def log(p, text):
    p.setdefault("log", []).append({"at": int(time.time()), "text": text})
    del p["log"][:-50]


def find(items, xid):
    return next((x for x in items if x["id"] == xid), None)


def shot_state(s):
    """What the page shows for a shot, and what a batch would do next with it."""
    still, prev, fin, up = s.get("still"), s.get("preview"), s.get("final"), s.get("upscale")
    prompt_changed = bool(prev and prev.get("prompt") != s["motion_prompt"])
    return {
        "has_still": bool(still),
        "preview_outdated": bool(prev and (prev.get("still") != (still or {}).get("file") or prompt_changed)),
        "prompt_changed": prompt_changed,
        "final_outdated": bool(fin and fin.get("preview") != (prev or {}).get("file")),
        "upscale_outdated": bool(up and up.get("final") != (fin or {}).get("file")),
    }


def wanted(p, kind, s):
    """Whether a batch run of `kind` should do this shot."""
    st = shot_state(s)
    if kind == "still":
        return not st["has_still"]
    if kind == "preview":
        return st["has_still"] and (not s.get("preview") or st["preview_outdated"])
    if kind == "final":
        return s.get("review") == "approved" and s.get("preview") and not st["preview_outdated"] \
            and (not s.get("final") or st["final_outdated"])
    if kind == "upscale":
        return bool(s.get("final")) and not st["final_outdated"] and (not s.get("upscale") or st["upscale_outdated"])
    return False


# --- The GPU (through the switcher) --------------------------------------------------------

def switcher(method, path, body=None, timeout=30):
    req = urllib.request.Request(SWITCHER + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise RuntimeError(f"switcher: {msg or e.code}") from None
    except OSError as e:
        raise RuntimeError(f"switcher: {e}") from None


def gpu_for(service_id, ready, label):
    """Makes the switcher give the GPU to service_id and waits until ready() says so."""
    st = switcher("GET", "/_ai/api/status")
    svc = next((s for s in st.get("services", []) if s["id"] == service_id), None)
    if svc is None:
        raise RuntimeError(f"{label} isn't in this stack")
    if not (svc["state"] == "running" and svc.get("health") in (None, "healthy") and not st.get("switching")):
        switcher("POST", "/_ai/api/switch", {"service": service_id, "force": True})
    deadline = time.time() + 45 * 60  # a first start can download a lot
    while time.time() < deadline:
        if current["stop"]:
            raise RuntimeError("stopped")
        st = switcher("GET", "/_ai/api/status")
        svc = next((s for s in st.get("services", []) if s["id"] == service_id), {})
        if st.get("error"):
            raise RuntimeError(st["error"])
        if not st.get("switching") and svc.get("state") == "running" and svc.get("health") in (None, "healthy") and ready():
            return
        time.sleep(5)
    raise RuntimeError(f"{label} didn't start")


def need_swarmui():
    gpu_for("swarmui", comfy.ready, "SwarmUI")


def llm_service():
    st = switcher("GET", "/_ai/api/status")
    for s in st.get("services", []):
        if LLM_MODEL in s.get("models", []):
            return s["id"]
    raise RuntimeError(f"no service serves {LLM_MODEL}")


def hold_loop():
    """Renews the switcher's hold while a job runs, and lets it go when the queue is empty."""
    held = False
    while True:
        busy = current["task"] is not None or bool(queue)
        try:
            if busy:
                switcher("POST", "/_ai/api/hold", {"by": "Studio", "seconds": 90}, timeout=10)
                held = True
            elif held:
                switcher("POST", "/_ai/api/hold", {"by": "Studio", "seconds": 0}, timeout=10)
                held = False
        except RuntimeError:
            pass
        time.sleep(20)


# --- The shot list (Qwen) ------------------------------------------------------------------

SHOT_PROMPT = """You are a film director's assistant. Turn the brief into a shot list for an AI video pipeline.

Each shot is made in two steps:
1. A still image (Krea 2) that is the shot's FIRST FRAME. Characters in it come from one reference photo each, so never describe their faces; do say how much of each person is visible ("full body", "waist up", "close-up of her hands"), what they wear (concretely, and what's gone if it differs from the photo), their pose, the place, and the light by where it lands ("warm window light across her left cheek").
2. A video clip (MiniMax H3, with sound) that starts from that frame.

Answer with JSON only, no other text:
{"shots": [{"title": "...", "characters": ["exact character names in the shot"], "seconds": 5-12,
  "still_prompt": "...", "motion_prompt": "..."}]}

Write motion_prompt like this (English, one shot, no hard cuts unless the brief asks for them):
<one line: look and mood, lens, light>

Timeline:
[0s-2s] <what moves, who does what>
[2s-5s] <...>
(cover the whole length in 2-4 steps; describe the camera: static, slow push-in, handheld follow, ...)

Audio: <ambience, sounds, music; dialogue in quotes with who says it, if any>

Rules: keep each character looking the same across shots (same outfit unless the brief changes it); vary the framing (wide, medium, close-up); keep motion simple and physical; no on-screen text unless asked.
"""


def write_shots(p, replace):
    gpu_for(llm_service(), lambda: True, LLM_MODEL)
    chars = "\n".join(f"- {c['name']}: {c.get('description') or '(no description)'}"
                      for c in p["characters"]) or "(none: no recurring people)"
    user = (f"Brief:\n{p['brief']}\n\nCharacters (use these exact names):\n{chars}\n\n"
            f"Format: {p['aspect']}. About {p['target_shots']} shots.")
    body = {"model": LLM_MODEL, "max_tokens": 30000, "temperature": 0.7,
            "messages": [{"role": "system", "content": SHOT_PROMPT}, {"role": "user", "content": user}],
            "reasoning_effort": "medium", "chat_template_kwargs": {"reasoning_effort": "medium"}}
    req = urllib.request.Request(SWITCHER + "/v1/chat/completions", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3 * 3600) as r:
            answer = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{LLM_MODEL}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}") from None
    text = answer["choices"][0]["message"].get("content") or ""
    start, end = text.find("{"), text.rfind("}")
    try:
        shots = json.loads(text[start:end + 1])["shots"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("the answer wasn't a shot list: " + text[:300]) from None
    names = {c["name"].lower(): c["id"] for c in p["characters"]}
    out = []
    for s in shots:
        if not isinstance(s, dict):
            continue
        try:
            seconds = max(4, min(15, int(round(float(s.get("seconds", 6))))))
        except (TypeError, ValueError):
            seconds = 6
        out.append({"id": new_id("s"), "title": str(s.get("title", ""))[:80],
                    "characters": [names[n.lower()] for n in s.get("characters", []) if isinstance(n, str) and n.lower() in names],
                    "seconds": seconds, "still_prompt": str(s.get("still_prompt", "")),
                    "motion_prompt": str(s.get("motion_prompt", "")), "review": "pending"})
    with lock:
        if replace:
            p["shots"] = out
        else:
            p["shots"].extend(out)
        log(p, f"{LLM_MODEL} wrote {len(out)} shots")
        save(p)


# --- Rendering ------------------------------------------------------------------------------

def upload_once(path, name, cache):
    if name not in cache:
        cache[name] = comfy.upload(path, name)
    return cache[name]


def render(p, s, kind):
    pid, sid = p["id"], s["id"]
    out = media_dir(pid, sid)
    os.makedirs(out, exist_ok=True)
    sizes = wf.SIZES.get(p["aspect"], wf.SIZES["16:9"])
    stop = lambda: current["stop"]
    up = {}
    seed = random.randrange(1, 2**31)
    if kind == "still":
        refs = []
        for cid in s["characters"]:
            c = find(p["characters"], cid)
            if c and c.get("ref"):
                refs.append(upload_once(os.path.join(project_dir(pid), c["ref"]), f"studio_{pid}_{c['ref'].split('/')[-1]}", up))
        name = f"still-{seed}.png"
        took = comfy.run(wf.still(s["still_prompt"], refs, sizes["final"], seed, f"studio/{pid}/{sid}"),
                         os.path.join(out, name), stop)
        return "still", {"file": name, "seed": seed, "took": took, "at": int(time.time())}
    if kind == "preview":
        still = s["still"]["file"]
        frame = upload_once(os.path.join(out, still), f"studio_{pid}_{sid}_{still}", up)
        name = f"preview-{seed}.mp4"
        took = comfy.run(wf.preview(s["motion_prompt"], frame, sizes["preview"], s["seconds"], seed, f"studio/{pid}/{sid}"),
                         os.path.join(out, name), stop)
        return "preview", {"file": name, "seed": seed, "still": still, "prompt": s["motion_prompt"],
                           "seconds": s["seconds"], "took": took, "at": int(time.time())}
    if kind == "final":
        prev = s["preview"]  # the approved one: its seed, frame, prompt and length again
        frame = upload_once(os.path.join(out, prev["still"]), f"studio_{pid}_{sid}_{prev['still']}", up)
        name = f"final-{prev['seed']}.mp4"
        took = comfy.run(wf.final(prev["prompt"], frame, sizes["preview"], sizes["final"], prev["seconds"],
                                  prev["seed"], f"studio/{pid}/{sid}", refine=p.get("refine", "standard")),
                         os.path.join(out, name), stop)
        return "final", {"file": name, "preview": prev["file"], "took": took, "at": int(time.time())}
    if kind == "upscale":
        fin = s["final"]["file"]
        video = upload_once(os.path.join(out, fin), f"studio_{pid}_{sid}_{fin}", up)
        name = f"upscale-{fin.split('-', 1)[1]}"
        took = comfy.run(wf.upscale(video, sizes["upscale"], seed, f"studio/{pid}/{sid}"), os.path.join(out, name), stop)
        return "upscale", {"file": name, "final": fin, "took": took, "at": int(time.time())}
    raise RuntimeError(kind)


def next_task():
    """The next job: same kind as the last one first (fewer model loads), else the oldest."""
    with lock:
        if not queue:
            return None
        last = current.get("last_kind")
        pick = next((t for t in queue if t["kind"] == last), queue[0])
        queue.remove(pick)
        return pick


def worker():
    while True:
        task = next_task()
        if task is None:
            time.sleep(1)
            continue
        with lock:
            current.update(task=task, since=time.time(), stop=False, last_kind=task["kind"])
            p = projects.get(task["project"])
        result, error = "done", None
        try:
            if p is None:
                raise RuntimeError("project gone")
            if task["kind"] == "shots":
                write_shots(p, task.get("replace", False))
            else:
                with lock:
                    s = find(p["shots"], task["shot"])
                if s is None:
                    raise RuntimeError("shot gone")
                need_swarmui()
                field, record = render(p, s, task["kind"])
                with lock:
                    s[field] = record
                    s.pop("error", None)
                    if field == "preview":
                        s["review"] = "pending"
                    save(p)
        except Exception as e:  # one failed shot shouldn't stop the batch
            result, error = "failed", str(e)
            if not isinstance(e, (RuntimeError, comfy.ComfyError)):
                traceback.print_exc()
            with lock:
                if p is not None:
                    s = find(p["shots"], task.get("shot")) if task.get("shot") else None
                    if s is not None:
                        s["error"] = f"{task['kind']}: {error}"[:500]
                    log(p, f"{task['kind']} failed: {error}"[:500])
                    save(p)
        with lock:
            took = round(time.time() - current["since"])
            history.insert(0, {**task, "result": result, "error": error, "took": took, "at": int(time.time())})
            del history[20:]
            if current["stop"]:
                queue[:] = []
            current.update(task=None, since=0)


def enqueue(p, kind, shot_ids, replace=False):
    with lock:
        if kind == "shots":
            if not any(t["project"] == p["id"] and t["kind"] == "shots" for t in queue):
                queue.append({"id": new_id("t"), "project": p["id"], "kind": "shots", "replace": replace})
            return 1
        added = 0
        for s in p["shots"]:
            if shot_ids != "all" and s["id"] not in shot_ids:
                continue
            if shot_ids == "all" and not wanted(p, kind, s):
                continue
            if kind == "preview" and not s.get("still") or kind == "final" and not s.get("preview") \
                    or kind == "upscale" and not s.get("final"):
                continue
            busy = (current["task"] or {})
            if any(t["shot"] == s["id"] and t["kind"] == kind for t in queue if "shot" in t) or \
                    (busy.get("shot") == s["id"] and busy.get("kind") == kind):
                continue
            queue.append({"id": new_id("t"), "project": p["id"], "kind": kind, "shot": s["id"]})
            added += 1
        return added


# --- Export ---------------------------------------------------------------------------------

def tc(frame):
    s, f = divmod(frame, wf.FPS)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def export_files(p):
    """[(path, name in the zip)] plus the EDL and the shot list as text."""
    files, edl, notes, rec = [], [f"TITLE: {p['title']}", "FCM: NON-DROP FRAME", ""], [], 3600 * wf.FPS
    n = 0
    for s in p["shots"]:
        best = next((s[k] for k in ("upscale", "final") if s.get(k)), None)
        if not best:
            continue
        n += 1
        slug = re.sub(r"[^A-Za-z0-9]+", "_", s.get("title") or s["id"]).strip("_")[:40]
        name = f"{n:03d}_{slug}.mp4"
        files.append((os.path.join(media_dir(p["id"], s["id"]), best["file"]), "clips/" + name))
        length = wf.frames(s["preview"]["seconds"] if s.get("preview") else s["seconds"])
        edl += [f"{n:03d}  AX       AA/V  C        {tc(0)} {tc(length)} {tc(rec)} {tc(rec + length)}",
                f"* FROM CLIP NAME: {name}", ""]
        rec += length
        notes.append(f"{name}\n  still: {s['still_prompt']}\n  motion: {(s.get('preview') or {}).get('prompt', s['motion_prompt'])}\n")
    return files, "\n".join(edl), "\n".join(notes)


class ZipOut:
    """A write-only stream for zipfile: the HTTP answer, with no seeking."""
    def __init__(self, w):
        self.w, self.n = w, 0

    def write(self, b):
        self.w.write(b)
        self.n += len(b)
        return len(b)

    def tell(self):
        return self.n

    def flush(self):
        self.w.flush()


# --- HTTP -----------------------------------------------------------------------------------

def public(p):
    """A project for the page: the stored fields plus each shot's state."""
    out = dict(p)
    out["shots"] = [{**s, "state": shot_state(s)} for s in p["shots"]]
    return out


def missing_models(kind=None):
    names = wf.NEEDS.get(kind, []) if kind else wf.MODEL_FILES
    return [n for n in names if not os.path.exists(os.path.join(MODELS, wf.MODEL_FILES[n], n))]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
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
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def body(self, limit=1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too large")
        return self.rfile.read(n)

    def jbody(self):
        try:
            d = json.loads(self.body() or b"{}")
        except ValueError:
            d = None
        return d if isinstance(d, dict) else {}

    def route(self):
        """(project, rest of the path parts) for /api/projects/<id>/..."""
        parts = [urllib.parse.unquote(x) for x in urllib.parse.urlsplit(self.path).path.strip("/").split("/")]
        return parts

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parts = self.route()
        if not self.authed():
            if parts[:1] == ["api"]:
                return self.json({"error": "login needed"}, 401)
            return self.send(302, headers=[("Location", "/_ai/login?next=/")])
        if parts == [""]:
            with open(os.path.join(HERE, "page.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if parts == ["api", "state"]:
            with lock:
                task = current["task"]
                return self.json({
                    "projects": sorted(({"id": p["id"], "title": p["title"], "shots": len(p["shots"]),
                                         "updated": p.get("updated", 0)} for p in projects.values()),
                                        key=lambda x: -x["updated"]),
                    "running": task and {**task, "since": current["since"]},
                    "queue": queue, "history": history, "missing": missing_models(),
                    "llm": LLM_MODEL, "now": time.time()})
        if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
            with lock:
                p = projects.get(parts[2])
            if p is None:
                return self.json({"error": "no such project"}, 404)
            if len(parts) == 3:
                with lock:
                    return self.json(public(p))
            if len(parts) == 5 and parts[3] == "refs":
                c = find(p["characters"], parts[4])
                if not c or not c.get("ref"):
                    return self.send(404, "no photo")
                return self.file(os.path.join(project_dir(p["id"]), c["ref"]))
            if len(parts) == 4 and parts[3] == "export.zip":
                return self.export(p)
        if len(parts) == 4 and parts[0] == "media" and all(ID.match(x) for x in parts[1:3]) \
                and re.match(r"^[a-z]+-\d+\.(png|mp4)$", parts[3]):
            return self.file(os.path.join(media_dir(parts[1], parts[2]), parts[3]))
        self.send(404, "not found")

    def file(self, path):
        """A file, with Range (a video player seeks with it)."""
        try:
            size = os.path.getsize(path)
            f = open(path, "rb")
        except OSError:
            return self.send(404, "not found")
        ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
                 "mp4": "video/mp4"}.get(path.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        with f:
            if m and size:
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2) or 0))
                if start > end:
                    return self.send(416, headers=[("Content-Range", f"bytes */{size}")])
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            if self.command == "HEAD":
                return
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0 and (chunk := f.read(min(1 << 20, left))):
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except OSError:
                pass

    def export(self, p):
        with lock:
            files, edl, notes = export_files(p)
        if not files:
            return self.send(409, "No finished shots yet: render finals first.")
        name = re.sub(r"[^A-Za-z0-9]+", "_", p["title"]).strip("_") or "studio"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{name}.zip"')
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            with zipfile.ZipFile(ZipOut(self.wfile), "w", zipfile.ZIP_STORED) as z:
                z.writestr(f"{name}/timeline.edl", edl)
                z.writestr(f"{name}/shots.txt", notes)
                for path, arc in files:
                    z.write(path, f"{name}/{arc}")
        except OSError:
            pass

    def do_DELETE(self):
        if not self.authed():
            return self.json({"error": "login needed"}, 401)
        parts = self.route()
        with lock:
            p = projects.get(parts[2]) if len(parts) >= 3 and parts[:2] == ["api", "projects"] else None
            if p is None:
                return self.json({"error": "not found"}, 404)
            if len(parts) == 3:
                if any(t["project"] == p["id"] for t in queue) or (current["task"] or {}).get("project") == p["id"]:
                    return self.json({"error": "it has jobs queued or running; stop them first"}, 409)
                del projects[p["id"]]
                shutil.rmtree(project_dir(p["id"]), ignore_errors=True)
                shutil.rmtree(media_dir(p["id"]), ignore_errors=True)
                return self.json({"deleted": p["id"]})
            if len(parts) == 5 and parts[3] == "characters":
                c = find(p["characters"], parts[4])
                if c:
                    p["characters"].remove(c)
                    for s in p["shots"]:
                        s["characters"] = [x for x in s["characters"] if x != c["id"]]
                    save(p)
                return self.json(public(p))
            if len(parts) == 5 and parts[3] == "shots":
                s = find(p["shots"], parts[4])
                if s:
                    p["shots"].remove(s)
                    queue[:] = [t for t in queue if t.get("shot") != s["id"]]
                    shutil.rmtree(media_dir(p["id"], s["id"]), ignore_errors=True)
                    save(p)
                return self.json(public(p))
        self.json({"error": "not found"}, 404)

    def do_PUT(self):
        """PUT /api/projects/<id>/characters/<cid>/photo: the reference photo, as the body."""
        if not self.authed():
            return self.json({"error": "login needed"}, 401)
        parts = self.route()
        if not (len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3] == "characters" and parts[5] == "photo"):
            return self.json({"error": "not found"}, 404)
        try:
            data = self.body(MAX_UPLOAD)
        except ValueError:
            return self.json({"error": "the photo is over 25 MB"}, 413)
        ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg" if data[:3] == b"\xff\xd8\xff" else \
            "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None
        if not ext:
            return self.json({"error": "a PNG, JPEG or WebP photo, please"}, 415)
        with lock:
            p = projects.get(parts[2])
            c = find(p["characters"], parts[4]) if p else None
            if c is None:
                return self.json({"error": "not found"}, 404)
            os.makedirs(os.path.join(project_dir(p["id"]), "refs"), exist_ok=True)
            if c.get("ref"):
                try:
                    os.remove(os.path.join(project_dir(p["id"]), c["ref"]))
                except OSError:
                    pass
            c["ref"] = f"refs/{c['id']}-{int(time.time())}.{ext}"
            with open(os.path.join(project_dir(p["id"]), c["ref"]), "wb") as f:
                f.write(data)
            save(p)
            return self.json(public(p))

    def do_POST(self):
        if not self.authed():
            return self.json({"error": "login needed"}, 401)
        parts = self.route()
        req = self.jbody()
        if parts == ["api", "projects"]:
            with lock:
                p = new_project(str(req.get("title", "")).strip())
                projects[p["id"]] = p
                return self.json(public(p))
        if parts == ["api", "stop"]:
            with lock:
                queue[:] = []
                current["stop"] = current["task"] is not None
            return self.json({"stopped": True})
        if not (len(parts) >= 4 and parts[:2] == ["api", "projects"]):
            return self.json({"error": "not found"}, 404)
        with lock:
            p = projects.get(parts[2])
            if p is None:
                return self.json({"error": "no such project"}, 404)
            what = parts[3]
            if what == "update" and len(parts) == 4:
                for k in ("title", "brief"):
                    if isinstance(req.get(k), str):
                        p[k] = req[k][:20000]
                if req.get("aspect") in wf.SIZES:
                    p["aspect"] = req["aspect"]
                if req.get("refine") in wf.REFINE_SIGMAS:
                    p["refine"] = req["refine"]
                if isinstance(req.get("target_shots"), int):
                    p["target_shots"] = max(1, min(200, req["target_shots"]))
                save(p)
                return self.json(public(p))
            if what == "characters" and len(parts) == 4:
                p["characters"].append({"id": new_id("c"), "name": str(req.get("name", "")).strip()[:40] or "Someone",
                                        "description": str(req.get("description", ""))[:1000], "ref": None})
                save(p)
                return self.json(public(p))
            if what == "characters" and len(parts) == 5:
                c = find(p["characters"], parts[4])
                if not c:
                    return self.json({"error": "not found"}, 404)
                if isinstance(req.get("name"), str) and req["name"].strip():
                    c["name"] = req["name"].strip()[:40]
                if isinstance(req.get("description"), str):
                    c["description"] = req["description"][:1000]
                save(p)
                return self.json(public(p))
            if what == "shots" and len(parts) == 4:
                p["shots"].append({"id": new_id("s"), "title": "New shot", "characters": [], "seconds": 6,
                                   "still_prompt": "", "motion_prompt": "", "review": "pending"})
                save(p)
                return self.json(public(p))
            if what == "shots" and len(parts) == 5:
                s = find(p["shots"], parts[4])
                if not s:
                    return self.json({"error": "not found"}, 404)
                for k in ("title", "still_prompt", "motion_prompt"):
                    if isinstance(req.get(k), str):
                        s[k] = req[k][:8000]
                if isinstance(req.get("seconds"), (int, float)):
                    s["seconds"] = max(4, min(15, int(req["seconds"])))
                if isinstance(req.get("characters"), list):
                    ids = {c["id"] for c in p["characters"]}
                    s["characters"] = [x for x in req["characters"] if x in ids]
                if req.get("review") in ("pending", "approved", "rejected"):
                    s["review"] = req["review"]
                if isinstance(req.get("move"), int):
                    p["shots"].remove(s)
                    p["shots"].insert(max(0, min(len(p["shots"]), req["move"])), s)
                save(p)
                return self.json(public(p))
            if what == "run" and len(parts) == 4:
                kind = req.get("kind")
                if kind not in KINDS:
                    return self.json({"error": "unknown job"}, 400)
                if kind == "shots" and not p["brief"].strip():
                    return self.json({"error": "write the brief first"}, 400)
                if missing_models(kind):
                    return self.json({"error": "models missing: " + ", ".join(missing_models(kind))}, 409)
                shots = req.get("shots", "all")
                added = enqueue(p, kind, shots if isinstance(shots, list) else "all", bool(req.get("replace")))
                return self.json({"queued": added})
        self.json({"error": "not found"}, 404)


if __name__ == "__main__":
    load_all()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=hold_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
