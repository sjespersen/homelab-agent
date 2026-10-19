"""The Studio: from a brief to finished shots on the GPU box.

  brief ─(Qwen writes the shot list)→ stills (Krea 2 + a reference photo per character)
        → previews (MiniMax H3, ~0.3 MP) → you approve → finals (same seed, latent upscale +
        refine to ~1 MP) → upscale (SeedVR2, 1080p) → export (clips + an EDL for Resolve)

Projects (brief, characters, reference photos, the shot list) live in DATA, which is backed up;
the renders in MEDIA, on the models disk. One job runs at a time; the jobs ask the switcher for
the GPU (Qwen for the shot list, SwarmUI for the rest) and hold it while they run.
"""
import base64
import glob
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import shutil
import struct
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
KINDS = ("shots", "looks", "prompt", "still", "preview", "final", "upscale")
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
                p = json.load(f)
            p.setdefault("places", [])
            p.setdefault("outfits", [])
            projects[pid] = p
        except (OSError, ValueError):
            continue


def new_project(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:30] or "project"
    p = {"id": f"{slug}-{new_id()}", "title": title[:120] or "Untitled", "created": int(time.time()),
         "brief": "", "aspect": "16:9", "target_shots": 12, "refine": "standard",
         "characters": [], "places": [], "outfits": [], "shots": [], "log": []}
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
    if kind == "prompt":
        return not in_h3_format(s["motion_prompt"])
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


def has_gpu(st, svc):
    """Whether svc runs and answers. Ollama always runs, so it also needs the others stopped."""
    if st.get("switching") or svc.get("state") != "running" or svc.get("health") not in (None, "healthy"):
        return False
    return not svc.get("always_on") or not any(
        o["state"] in ("running", "restarting") for o in st.get("services", []) if not o.get("always_on"))


def gpu_for(service_id, ready, label):
    """Makes the switcher give the GPU to service_id and waits until ready() says so."""
    st = switcher("GET", "/_ai/api/status")
    svc = next((s for s in st.get("services", []) if s["id"] == service_id), None)
    if svc is None:
        raise RuntimeError(f"{label} isn't in this stack")
    if not has_gpu(st, svc):
        switcher("POST", "/_ai/api/switch", {"service": service_id, "force": True})
    deadline = time.time() + 45 * 60  # a first start can download a lot
    while time.time() < deadline:
        if current["stop"]:
            raise RuntimeError("stopped")
        st = switcher("GET", "/_ai/api/status")
        svc = next((s for s in st.get("services", []) if s["id"] == service_id), {})
        if st.get("error"):
            raise RuntimeError(st["error"])
        if has_gpu(st, svc) and ready():
            return
        time.sleep(5)
    raise RuntimeError(f"{label} didn't start")


def need_swarmui():
    gpu_for("swarmui", comfy.ready, "SwarmUI")


def llm_models():
    """The models the shot list can use, as in Open WebUI: the switcher's (Qwen 27B, Strata)
    and the ones downloaded into Ollama."""
    st = switcher("GET", "/_ai/api/status")
    out = [{"id": s["models"][0], "label": s["title"], "service": s["id"], "via": "switcher"}
           for s in st.get("services", []) if s.get("models") and not s.get("always_on")]
    ollama = next((s for s in st.get("services", []) if s.get("always_on")), None)
    if ollama and ollama["state"] == "running":
        try:
            tags = switcher("GET", "/ollama/api/tags", timeout=10)
        except RuntimeError:
            tags = {}
        # hf.co/org/Some-Model-GGUF:Q5_K_M -> Some-Model-GGUF:Q5_K_M, as Open WebUI shows it.
        out += [{"id": m["name"], "label": m["name"].rsplit("/", 1)[-1].removesuffix(":latest"),
                 "service": ollama["id"], "via": "ollama", "vision": sees(m)}
                for m in sorted(tags.get("models", []), key=lambda m: m.get("name", "")) if m.get("name")]
    return out


capabilities = {}  # an Ollama model's digest -> its capabilities ("vision", "tools", ...)


def sees(m):
    """Whether an Ollama model reads images (its /api/show capabilities)."""
    digest = m.get("digest") or m["name"]
    if digest not in capabilities:
        try:
            capabilities[digest] = switcher("POST", "/ollama/api/show", {"model": m["name"]}, timeout=20).get("capabilities") or []
        except RuntimeError:
            return False
    return "vision" in capabilities[digest]


def vision_model(p):
    """The model that reads a project's picture: the chosen one, else the first that can see
    (Qwen first)."""
    seeing = [m for m in llm_models() if m.get("vision")]
    chosen = next((m for m in seeing if m["id"] == p.get("vision_llm")), None)
    if chosen:
        return chosen["id"]
    seeing.sort(key=lambda m: "qwen" not in m["id"].lower())
    if not seeing:
        raise RuntimeError("no model on this box reads pictures: download one with vision in Open WebUI (Qwen3.8, Gemma 3)")
    return seeing[0]["id"]


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
1. A still image (Krea 2) that is the shot's FIRST FRAME. It is made on its own: the image model never sees the other shots, only this shot's text. Characters in it come from one reference photo each, so never describe their faces.
2. A video clip (MiniMax H3, with sound) that starts from that frame.

So that the shots look like one film, first define every place and every outfit once; the pipeline adds a place's and the outfits' descriptions to each still by itself.

Answer with JSON only, no other text:
{"places": [{"name": "short name", "look": "..."}],
 "outfits": [{"character": "exact character name", "name": "short name", "look": "..."}],
 "shots": [{"title": "...", "place": "a place name", "seconds": 5-12,
   "cast": [{"character": "exact character name", "outfit": "an outfit name", "position": "..."}],
   "still_prompt": "...", "motion_prompt": "..."}]}

- A place's look: the room or landscape, the fixed furniture and props and where they stand, colours and materials, the time of day, and the light sources and where they are. Concrete enough that two pictures of it match.
- An outfit's look: each garment with colour and material, shoes, hair, jewellery, and what is NOT worn (the model copies clothes from the reference photo otherwise: "no jacket, bare arms"). A character keeps an outfit until the brief changes it.
- cast: everyone recurring in the shot. position: where they are in the frame ("on the left", "on the right", "in the centre", "in the background"), different for each person in the shot.
- still_prompt: only what this shot adds: the framing, how much of each person is visible ("full body", "waist up", "close-up of her hands"), the camera angle, poses, actions, expressions, which part of the place is seen, and the light by where it lands ("warm window light across her left cheek"). Refer to people by name. Don't repeat the place's or the outfits' looks.
- motion_prompt: one string holding the three H3 fields described below.
- Keep the story continuous: what a shot shows (props, state of things, positions) follows from the shot before unless there's a cut in time.
- Vary the framing (wide, medium, close-up); keep motion simple and physical; no on-screen text unless asked.
"""

REWRITE_PROMPT = """You rewrite one shot's motion prompt for MiniMax H3, a video model with sound. You get what the shot's first frame shows, its length and a draft motion prompt (plain words, or an older format). Keep the draft's intent: the action, the camera, any dialogue word for word, the sounds and music. Fill in what's missing so it fits the first frame and the length.

Answer with the three fields only, no other text.
"""

MOTION_FORMAT = """Write each motion prompt in MiniMax H3's I2VA format (a clip that starts from a given first frame), but leave out the first-line instruction: the pipeline adds it. Only the three core fields, in this order, separated by a blank line:

integrated_multimodal_description: [Shot 1] <style and composition as in the first frame, then what moves along the timeline>

overall_soundscape: <1-4 sentences>

non_diegetic_music: <1-3 sentences, or N/A>

The first frame is <Picture 1> at 0.00 seconds. Use a single [Shot 1] unless the brief asks for cuts; a cut time must fall inside the shot's length. Describe the camera as motion type + amplitude + speed. Give people who speak an ID like (S1) and put their words in <d>[English] ...</d>. Write in English.
"""

# The first line of an H3 prompt that starts from a given first frame (I2VA, in MiniMax's
# prompt guide). The Studio adds it, so the prompts on the page are just the three fields.
I2VA = "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
GUIDE = os.path.join(DATA, "h3-prompt-guide.txt")  # fetched by before-up.sh


def h3_prompt(motion):
    m = motion.strip()
    return m if m.startswith(("For the target video", "How the reference")) else f"{I2VA}\n\n{m}"


def with_guide(system):
    """The system prompt plus the motion format and, when it's there, MiniMax's whole guide."""
    try:
        with open(GUIDE, encoding="utf-8") as f:
            guide = f.read()
    except OSError:
        guide = ""
    out = system + "\n" + MOTION_FORMAT
    if guide:
        out += ("\nMiniMax's H3 prompt-writing guide follows. Use its I2VA mode; the cases show finished prompts."
                f"\n\n<guide>\n{guide}\n</guide>\n")
    return out


def llm(system, user, model, images=()):
    found = next((m for m in llm_models() if m["id"] == model), None)
    if found is None:
        raise RuntimeError(f"{model} isn't on this box (any more); pick another model")
    gpu_for(found["service"], lambda: True, model)
    if found["via"] == "ollama":
        # Ollama's own API: its OpenAI one can't set the context, and the guide needs ~8K.
        path = "/ollama/api/chat"
        body = {"model": model, "stream": False, "options": {"num_ctx": 32768, "temperature": 0.7},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user, **({"images": list(images)} if images else {})}]}
    elif images:
        raise RuntimeError(f"{model} doesn't read pictures here")
    else:
        path = "/v1/chat/completions"
        body = {"model": model, "max_tokens": 30000, "temperature": 0.7,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "reasoning_effort": "medium", "chat_template_kwargs": {"reasoning_effort": "medium"}}
    req = urllib.request.Request(SWITCHER + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3 * 3600) as r:
            answer = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{model}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}") from None
    if found["via"] == "ollama":
        return (answer.get("message") or {}).get("content") or ""
    return answer["choices"][0]["message"].get("content") or ""


PICTURE_PROMPT = """You read one picture for an AI film pipeline. It is the first frame of the film's first shot; the rest of the film must match it. Answer with JSON only, no other text:
{"still_prompt": "...", "place": {"name": "short name", "look": "..."},
 "people": [{"name": "...", "who": "...", "outfit": "...", "position": "...", "box": [x1, y1, x2, y2]}]}

- still_prompt: this picture as a prompt for an image model: the framing, the camera angle, how much of each person is visible, their poses and expressions, which part of the place is seen, and the light by where it lands. Refer to people by name. Don't describe faces.
- place.look: the room or landscape, the furniture and props and where they stand, colours and materials, the time of day, and the light sources and where they are. Concrete enough to draw it again from another angle.
- people: everyone who matters in the picture. name: the user's name for them if the text or the known characters say who they are, else "Person 1", "Person 2", ... who: apparent age, build, hair. outfit: each garment with colour and material, shoes, hair, jewellery. position: where they are in the frame ("on the left", "on the right", "in the centre", "in the background"), different for each. box: from the top of the head down to as much of the body as is visible, on a 0-1000 scale of the picture's width and height.
"""


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def read_picture(p):
    """The vision model's reading of the project's picture: still_prompt, place, people."""
    path = os.path.join(project_dir(p["id"]), p["picture"]["file"])
    with open(path, "rb") as f:
        image = base64.b64encode(f.read()).decode()
    chars = ", ".join(f"{c['name']} ({c.get('description') or 'no description'})" for c in p["characters"]) or "none"
    model = vision_model(p)
    text = llm(PICTURE_PROMPT, f"What should happen (the user's text):\n{p['brief']}\n\nKnown characters: {chars}",
               model, [image])
    start, end = text.find("{"), text.rfind("}")
    try:
        answer = json.loads(text[start:end + 1])
    except ValueError:
        raise RuntimeError(f"{model} didn't describe the picture: " + text[:300]) from None
    with lock:
        log(p, f"{model} read the picture")
    return answer if isinstance(answer, dict) else {}


def take_picture(p, reading):
    """Puts what the picture showed into the project: its place, a character per person (the
    photo: their box in the picture) and their outfits. Returns the text for the shot list."""
    w, h = p["picture"]["w"], p["picture"]["h"]
    place = reading.get("place") if isinstance(reading.get("place"), dict) else {}
    lines = [f"The film opens on a picture the user gave; shot 1 starts from exactly this picture. "
             f"Copy this as shot 1's still_prompt:\n{reading.get('still_prompt', '')}"]
    if place.get("look"):
        p["places"].append({"id": new_id("l"), "name": str(place.get("name") or "The picture")[:60],
                            "look": str(place["look"])[:2000]})
        lines.append(f"Shot 1's place is {p['places'][-1]['name']}.")
    by_name = {c["name"].lower(): c for c in p["characters"]}
    for i, person in enumerate(reading.get("people") or []):
        if not isinstance(person, dict):
            continue
        name = str(person.get("name") or f"Person {i + 1}").strip()[:40]
        c = by_name.get(name.lower())
        if c is None:
            c = {"id": new_id("c"), "name": name, "description": str(person.get("who", ""))[:1000], "ref": None}
            p["characters"].append(c)
            by_name[name.lower()] = c
        box = person.get("box")
        if not c.get("ref") and isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            x1, y1, x2, y2 = (max(0, min(1000, v)) for v in box)
            if x2 - x1 > 20 and y2 - y1 > 20:
                # A little room around them, in the picture's pixels.
                pad_x, pad_y = (x2 - x1) * 0.08, (y2 - y1) * 0.04
                c["ref"] = p["picture"]["file"]
                c["box"] = [int(max(0, x1 - pad_x) * w / 1000), int(max(0, y1 - pad_y) * h / 1000),
                            int(min(1000, x2 + pad_x) * w / 1000), int(min(1000, y2 + pad_y) * h / 1000)]
        if person.get("outfit"):
            p["outfits"].append({"id": new_id("o"), "character": c["id"], "name": "in the picture",
                                 "look": str(person["outfit"])[:2000]})
        lines.append(f"{name} is {person.get('position') or 'in the picture'}, wearing the outfit \"in the picture\".")
    return "\n".join(lines)


PORTRAIT = (768, 1024)  # character photos: full body, upright


def make_looks(p, up):
    """From the project's descriptions: a reference photo for each character cut out of the
    picture (the identity LoRA restages the crop full body on a plain background, in their
    outfit, which is the photo the stills work best from) and an image of each place."""
    need_swarmui()
    stop = lambda: current["stop"]
    with lock:
        todo = [("character", c) for c in p["characters"] if c.get("box")] + \
               [("place", x) for x in p["places"] if not x.get("image") and x["look"].strip()]
    sizes = wf.SIZES.get(p["aspect"], wf.SIZES["16:9"])
    for what, x in todo:
        seed = random.randrange(1, 2**31)
        if what == "character":
            with lock:
                outfit = next((o["look"] for o in p["outfits"] if o["character"] == x["id"]), "")
                ref, box = x["ref"], list(x["box"])
            name = upload_once(os.path.join(project_dir(p["id"]), ref), f"studio_{p['id']}_{ref.split('/')[-1]}", up)
            prompt = ("Full-body photo of the person from the photo, standing upright and facing the camera, arms "
                      "relaxed, the whole body from head to shoes in frame" + (f", wearing {outfit}" if outfit else "")
                      + ". Plain light grey studio background, soft even light, nothing else in the picture.")
            file = f"refs/{x['id']}-{int(time.time())}.png"
            comfy.run(wf.still([prompt], [(name, box)], PORTRAIT, seed, f"studio/{p['id']}/looks"),
                      os.path.join(project_dir(p["id"]), file), stop)
            with lock:
                x["ref"] = file
                x.pop("box", None)
                save(p)
        else:
            file = f"refs/{x['id']}-{int(time.time())}.png"
            prompt = f"Wide establishing photo of an empty place, no people: {x['look']}"
            comfy.run(wf.still([prompt], [], sizes["final"], seed, f"studio/{p['id']}/looks"),
                      os.path.join(project_dir(p["id"]), file), stop)
            with lock:
                x["image"] = file
                save(p)
    with lock:
        log(p, f"made {len(todo)} character photos and place images")
        save(p)


def write_shots(p, replace):
    opening = ""
    if p.get("picture") and (replace or not p["shots"]):
        reading = read_picture(p)
        with lock:
            if replace:
                p["places"], p["outfits"] = [], []
                replace = "done"  # cleared already: keep what the picture added
            opening = take_picture(p, reading)
            save(p)
    chars = "\n".join(f"- {c['name']}: {c.get('description') or '(no description)'}"
                      for c in p["characters"]) or "(none: no recurring people)"
    user = (f"Brief:\n{p['brief']}\n\nCharacters (use these exact names):\n{chars}\n\n"
            f"Format: {p['aspect']}. About {p['target_shots']} shots.")
    if replace is not True and (p["places"] or p["outfits"]):
        user += ("\n\nThe film so far has these places and outfits; reuse them by name, list only new ones:\n"
                 + json.dumps(looks_for_llm(p), ensure_ascii=False))
    if opening:
        user += "\n\n" + opening
    model = p.get("llm") or LLM_MODEL
    text = llm(with_guide(SHOT_PROMPT), user, model)
    with open(os.path.join(DATA, "last-shot-list.txt"), "w", encoding="utf-8") as f:
        f.write(text)  # for when an answer doesn't parse as expected
    start, end = text.find("{"), text.rfind("}")
    try:
        answer = json.loads(text[start:end + 1])
        shots = answer["shots"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("the answer wasn't a shot list: " + text[:300]) from None
    names = {c["name"].lower(): c["id"] for c in p["characters"]}
    with lock:
        if replace is True:
            p["places"], p["outfits"] = [], []
        place_ids = {x["name"].lower(): x["id"] for x in p["places"]}
        outfit_ids = {(x["character"], x["name"].lower()): x["id"] for x in p["outfits"]}
        for x in answer.get("places") or []:
            if isinstance(x, dict) and str(x.get("name", "")).strip() and str(x["name"]).strip().lower() not in place_ids:
                place = {"id": new_id("l"), "name": str(x["name"]).strip()[:60], "look": str(x.get("look", ""))[:2000]}
                p["places"].append(place)
                place_ids[place["name"].lower()] = place["id"]
        for x in answer.get("outfits") or []:
            if not isinstance(x, dict):
                continue
            cid, name = names.get(str(x.get("character", "")).lower()), str(x.get("name", "")).strip()
            if cid and name and (cid, name.lower()) not in outfit_ids:
                outfit = {"id": new_id("o"), "character": cid, "name": name[:60], "look": str(x.get("look", ""))[:2000]}
                p["outfits"].append(outfit)
                outfit_ids[(cid, name.lower())] = outfit["id"]
    out = []
    for s in shots:
        if not isinstance(s, dict):
            continue
        try:
            seconds = max(4, min(15, int(round(float(s.get("seconds", 6))))))
        except (TypeError, ValueError):
            seconds = 6
        cast = {}
        for m in s.get("cast") or []:
            cid = names.get(str(m.get("character", "")).lower()) if isinstance(m, dict) else None
            if cid and cid not in cast:
                cast[cid] = {"position": str(m.get("position", ""))[:80],
                             "outfit": outfit_ids.get((cid, str(m.get("outfit", "")).strip().lower()))}
        # Older answers list names only.
        for n in s.get("characters") or []:
            cid = names.get(n.lower()) if isinstance(n, str) else None
            if cid and cid not in cast:
                cast[cid] = {"position": "", "outfit": None}
        out.append({"id": new_id("s"), "title": str(s.get("title", ""))[:80],
                    "characters": list(cast), "cast": cast,
                    "place": place_ids.get(str(s.get("place", "")).strip().lower()),
                    "seconds": seconds, "still_prompt": str(s.get("still_prompt", "")),
                    "motion_prompt": fields_only(motion_of(s)), "review": "pending"})
    with lock:
        if opening and out:
            # Shot 1's still is the picture itself.
            os.makedirs(media_dir(p["id"], out[0]["id"]), exist_ok=True)
            name = f"still-picture-{int(time.time())}.png"
            shutil.copyfile(os.path.join(project_dir(p["id"]), p["picture"]["file"]),
                            os.path.join(media_dir(p["id"], out[0]["id"]), name))
            out[0]["still"] = {"file": name, "seed": 0, "took": 0, "at": int(time.time()), "picture": True}
        if replace:
            p["shots"] = out
        else:
            p["shots"].extend(out)
        log(p, f"{model} wrote {len(out)} shots")
        save(p)
    return bool(opening)


H3_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")


def motion_of(s):
    """The shot's motion prompt as one string, also when the model wrote H3's fields as an
    object or as keys of the shot."""
    m = s.get("motion_prompt")
    if isinstance(m, str) and m.strip():
        return m
    src = m if isinstance(m, dict) else s
    parts = [f"{k}: {src[k]}" for k in H3_FIELDS if isinstance(src.get(k), str) and src[k].strip()]
    return "\n\n".join(parts)


def looks_for_llm(p):
    names = {c["id"]: c["name"] for c in p["characters"]}
    return {"places": [{"name": x["name"], "look": x["look"]} for x in p["places"]],
            "outfits": [{"character": names.get(x["character"], "?"), "name": x["name"], "look": x["look"]}
                        for x in p["outfits"]]}


# --- What Krea gets for a still ------------------------------------------------------------

# Where people stand when a shot doesn't say (older shots, shots added by hand).
DEFAULT_POSITIONS = {1: [""], 2: ["on the left", "on the right"], 3: ["on the left", "in the centre", "on the right"]}


def cast_of(p, s):
    """The shot's people with a reference photo, in photo order: [(character, position, outfit look)]."""
    chars = [c for c in (find(p["characters"], cid) for cid in s["characters"]) if c and c.get("ref")]
    defaults = DEFAULT_POSITIONS.get(len(chars), [""] * len(chars))
    out = []
    for i, c in enumerate(chars):
        m = (s.get("cast") or {}).get(c["id"]) or {}
        outfit = find(p["outfits"], m.get("outfit")) if m.get("outfit") else None
        out.append((c, (m.get("position") or "").strip() or defaults[i], outfit["look"] if outfit else ""))
    return out


def name_to(text, name, label):
    """Replaces a character's name (and "Name's") in a prompt with a label."""
    text = re.sub(rf"\b{re.escape(name)}'s\b", label + "'s", text, flags=re.I)
    return re.sub(rf"\b{re.escape(name)}\b", label, text, flags=re.I)


def still_prompts(p, s):
    """One prompt per identity pass (one per person with a photo), or one without people.

    Krea doesn't know names: what ties a person to a photo is the pass. Pass 1 has the first
    person's photo and stages the whole scene, the others as stand-ins at their positions;
    each further pass swaps one stand-in for the person in its photo, keeping the rest."""
    place = find(p["places"], s.get("place")) if s.get("place") else None
    setting = f"\n\nThe place: {place['look']}" if place and place["look"].strip() else ""
    cast = cast_of(p, s)
    prompt = s["still_prompt"].strip()
    if not cast:
        return [prompt + setting]
    first = cast[0]
    label = lambda c, pos: f"the person {pos}" if pos else "the person"
    scene = prompt
    for c, pos, _ in cast:
        scene = name_to(scene, c["name"], "the person from the photo" if c is first[0] else label(c, pos))
    scene = re.sub(r"(^|[.!?]\s+)the person", lambda m: m.group(1) + "The person", scene)
    who = []
    for c, pos, look in cast:
        if len(cast) == 1 and not look:
            break
        it = "The person from the photo" + (f" is {pos}" if pos and len(cast) > 1 else "") if c is first[0] \
            else f"Another person is {pos}" if pos else "Another person is in the shot"
        # The model copies the photo's clothes unless told they're gone (its model card says so).
        who.append(it + (f" and wears {look}" if look and c is not first[0]
                         else f", wearing {look}. Not the clothes from the photo" if look else "") + ".")
    passes = [scene + ("\n\n" + " ".join(who) if who else "") + setting]
    for c, pos, look in cast[1:]:
        passes.append(f"Replace {label(c, pos)} in the first image with the person from the second image, "
                      f"in the same pose, place and framing" + (f", wearing {look}. Not the clothes from the second image" if look else "")
                      + ". Keep everything else in the first image exactly as it is.")
    return passes


def fields_only(motion):
    """Drops an instruction line the model wrote anyway (small ones get its times and shot
    numbers wrong): h3_prompt adds the right one."""
    at = motion.find("integrated_multimodal_description:")
    return motion[at:].strip() if at > 0 else motion.strip()


def in_h3_format(motion):
    return "integrated_multimodal_description:" in motion


def rewrite_prompt(p, s):
    """Rewrites one shot's motion prompt in H3's format, with MiniMax's guide."""
    with lock:
        names = ", ".join(c["name"] for c in p["characters"] if c["id"] in s["characters"]) or "nobody recurring"
        draft = s["motion_prompt"]
        place = find(p["places"], s.get("place")) if s.get("place") else None
        looks = "".join(f"\n{c['name']} {pos or ''} wears: {look}" for c, pos, look in cast_of(p, s) if look)
        user = (f"First frame (the still's prompt):\n{s['still_prompt'] or '(none)'}"
                + (f"\nThe place: {place['look']}" if place else "") + looks
                + f"\n\nCharacters in it: {names}\n"
                f"Length: {s['seconds']} seconds\n\nDraft motion prompt:\n{draft or '(none: invent fitting motion and sound)'}")
    text = llm(with_guide(REWRITE_PROMPT), user, p.get("llm") or LLM_MODEL)
    at = text.find("integrated_multimodal_description:")
    if at < 0:
        raise RuntimeError("the answer wasn't an H3 prompt: " + text[:300])
    with lock:
        if s["motion_prompt"] != draft:
            raise RuntimeError("the prompt was edited meanwhile; rewrite it again")
        s["motion_prompt"] = text[at:].strip()
        save(p)


# --- Rendering ------------------------------------------------------------------------------

def upload_once(path, name, cache):
    if name not in cache:
        cache[name] = comfy.upload(path, name)
    return cache[name]


def forget_comfy(pid=None, sid=None):
    """ComfyUI's leftover copies of a project's (or a shot's, or with no pid every project's)
    uploads and renders."""
    prefix = "studio_" + (f"{pid}_" + (f"{sid}_" if sid else "") if pid else "")
    names = [os.path.basename(x) for x in glob.glob(os.path.join(comfy.COMFY_DIR, "input", glob.escape(prefix) + "*"))]
    comfy.forget(names=names, output="studio/" + "/".join(x for x in (pid, sid) if x) if pid else None)
    if not pid:
        for d in glob.glob(os.path.join(comfy.COMFY_DIR, "output", "studio", "*")):
            shutil.rmtree(d, ignore_errors=True)


def forget_uploads(cache):
    """Deletes ComfyUI's copies of a job's uploads (the next job uploads again)."""
    comfy.forget(names=list(cache.values()))


def render(p, s, kind, up):
    pid, sid = p["id"], s["id"]
    out = media_dir(pid, sid)
    os.makedirs(out, exist_ok=True)
    sizes = wf.SIZES.get(p["aspect"], wf.SIZES["16:9"])
    stop = lambda: current["stop"]
    seed = random.randrange(1, 2**31)
    if kind == "still":
        with lock:
            photos = [(c["ref"], c.get("box")) for c, _, _ in cast_of(p, s)]
            prompts = still_prompts(p, s)
        refs = [(upload_once(os.path.join(project_dir(pid), ref), f"studio_{pid}_{ref.split('/')[-1]}", up), box)
                for ref, box in photos]
        name = f"still-{seed}.png"
        took = comfy.run(wf.still(prompts, refs, sizes["final"], seed, f"studio/{pid}/{sid}"),
                         os.path.join(out, name), stop)
        return "still", {"file": name, "seed": seed, "took": took, "at": int(time.time())}
    if kind == "preview":
        still = s["still"]["file"]
        frame = upload_once(os.path.join(out, still), f"studio_{pid}_{sid}_{still}", up)
        name = f"preview-{seed}.mp4"
        took = comfy.run(wf.preview(h3_prompt(s["motion_prompt"]), frame, sizes["preview"], s["seconds"], seed, f"studio/{pid}/{sid}"),
                         os.path.join(out, name), stop)
        return "preview", {"file": name, "seed": seed, "still": still, "prompt": s["motion_prompt"],
                           "seconds": s["seconds"], "took": took, "at": int(time.time())}
    if kind == "final":
        prev = s["preview"]  # the approved one: its seed, frame, prompt and length again
        frame = upload_once(os.path.join(out, prev["still"]), f"studio_{pid}_{sid}_{prev['still']}", up)
        name = f"final-{prev['seed']}.mp4"
        took = comfy.run(wf.final(h3_prompt(prev["prompt"]), frame, sizes["preview"], sizes["final"], prev["seconds"],
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
        # Character photos and place images first: the stills need them.
        pick = next((t for t in queue if t["kind"] == "looks"), None) \
            or next((t for t in queue if t["kind"] == last), queue[0])
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
                if write_shots(p, task.get("replace", False)):
                    enqueue(p, "looks", "all")
            elif task["kind"] == "looks":
                up = {}
                try:
                    make_looks(p, up)
                finally:
                    forget_uploads(up)
            elif task["kind"] == "prompt":
                with lock:
                    s = find(p["shots"], task["shot"])
                if s is None:
                    raise RuntimeError("shot gone")
                rewrite_prompt(p, s)
                with lock:
                    s.pop("error", None)
                    save(p)
            else:
                with lock:
                    s = find(p["shots"], task["shot"])
                if s is None:
                    raise RuntimeError("shot gone")
                need_swarmui()
                up = {}
                try:
                    field, record = render(p, s, task["kind"], up)
                finally:
                    forget_uploads(up)
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
        if kind in ("shots", "looks"):
            if not any(t["project"] == p["id"] and t["kind"] == kind for t in queue):
                queue.append({"id": new_id("t"), "project": p["id"], "kind": kind, "replace": replace})
            return 1
        added = 0
        for s in p["shots"]:
            if shot_ids != "all" and s["id"] not in shot_ids:
                continue
            if shot_ids == "all" and not wanted(p, kind, s):
                continue
            if kind == "prompt" and not (s["motion_prompt"].strip() or s["still_prompt"].strip()):
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
                    "llm": LLM_MODEL, "sizes": wf.SIZES, "now": time.time()})
        if parts == ["api", "models"]:
            try:
                return self.json({"models": llm_models(), "default": LLM_MODEL})
            except RuntimeError as e:
                return self.json({"error": str(e)}, 502)
        if len(parts) >= 3 and parts[:2] == ["api", "projects"]:
            with lock:
                p = projects.get(parts[2])
            if p is None:
                return self.json({"error": "no such project"}, 404)
            if len(parts) == 3:
                with lock:
                    return self.json(public(p))
            if len(parts) == 4 and parts[3] == "picture":
                if not p.get("picture"):
                    return self.send(404, "no picture")
                return self.file(os.path.join(project_dir(p["id"]), p["picture"]["file"]))
            if len(parts) == 6 and parts[3] == "places" and parts[5] == "image":
                x = find(p["places"], parts[4])
                if not x or not x.get("image"):
                    return self.send(404, "no image")
                return self.file(os.path.join(project_dir(p["id"]), x["image"]))
            if len(parts) == 5 and parts[3] == "refs":
                c = find(p["characters"], parts[4])
                if not c or not c.get("ref"):
                    return self.send(404, "no photo")
                return self.file(os.path.join(project_dir(p["id"]), c["ref"]))
            if len(parts) == 4 and parts[3] == "export.zip":
                return self.export(p)
        if len(parts) == 4 and parts[0] == "media" and all(ID.match(x) for x in parts[1:3]) \
                and re.match(r"^[a-z]+(-[a-z]+)?-\d+\.(png|mp4)$", parts[3]):  # still-123.png, still-picture-123.png
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
                forget_comfy(p["id"])
                return self.json({"deleted": p["id"]})
            if len(parts) == 4 and parts[3] == "picture":
                self.drop_picture(p)
                save(p)
                return self.json(public(p))
            if len(parts) == 5 and parts[3] == "characters":
                c = find(p["characters"], parts[4])
                if c:
                    p["characters"].remove(c)
                    for s in p["shots"]:
                        s["characters"] = [x for x in s["characters"] if x != c["id"]]
                        (s.get("cast") or {}).pop(c["id"], None)
                    p["outfits"] = [o for o in p["outfits"] if o["character"] != c["id"]]
                    save(p)
                return self.json(public(p))
            if len(parts) == 5 and parts[3] == "shots":
                s = find(p["shots"], parts[4])
                if s:
                    p["shots"].remove(s)
                    queue[:] = [t for t in queue if t.get("shot") != s["id"]]
                    shutil.rmtree(media_dir(p["id"], s["id"]), ignore_errors=True)
                    forget_comfy(p["id"], s["id"])
                    save(p)
                return self.json(public(p))
        self.json({"error": "not found"}, 404)

    def put_picture(self, pid):
        """PUT /api/projects/<id>/picture: the picture a film starts from, as a PNG the page
        has already cut to the project's format and final size."""
        try:
            data = self.body(MAX_UPLOAD)
        except ValueError:
            return self.json({"error": "the picture is over 25 MB"}, 413)
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return self.json({"error": "a PNG, please"}, 415)
        with lock:
            p = projects.get(pid)
            if p is None:
                return self.json({"error": "not found"}, 404)
            os.makedirs(os.path.join(project_dir(pid), "refs"), exist_ok=True)
            self.drop_picture(p)
            file = f"refs/picture-{int(time.time())}.png"
            path = os.path.join(project_dir(pid), file)
            with open(path, "wb") as f:
                f.write(data)
            w, h = png_size(path)
            p["picture"] = {"file": file, "w": w, "h": h}
            save(p)
            return self.json(public(p))

    @staticmethod
    def drop_picture(p):
        """Forgets the project's picture; the file goes unless a character's photo still cuts from it."""
        old = (p.get("picture") or {}).get("file")
        p.pop("picture", None)
        if old and not any(c.get("ref") == old for c in p["characters"]):
            try:
                os.remove(os.path.join(project_dir(p["id"]), old))
            except OSError:
                pass

    def do_PUT(self):
        """PUT /api/projects/<id>/characters/<cid>/photo: the reference photo, as the body."""
        if not self.authed():
            return self.json({"error": "login needed"}, 401)
        parts = self.route()
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "picture":
            return self.put_picture(parts[2])
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
            if c.get("ref") and not c.get("box"):  # a box: the project's picture, which stays
                try:
                    os.remove(os.path.join(project_dir(p["id"]), c["ref"]))
                except OSError:
                    pass
            c.pop("box", None)
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
                if isinstance(req.get("llm"), str):
                    p["llm"] = req["llm"].strip()[:200] or None
                if isinstance(req.get("vision_llm"), str):
                    p["vision_llm"] = req["vision_llm"].strip()[:200] or None
                if isinstance(req.get("target_shots"), int):
                    p["target_shots"] = max(1, min(200, req["target_shots"]))
                if isinstance(req.get("places"), list):
                    old = {x["id"]: x for x in p["places"]}
                    p["places"] = [{"id": x.get("id") if ID.match(str(x.get("id", ""))) else new_id("l"),
                                    "name": str(x.get("name", ""))[:60], "look": str(x.get("look", ""))[:2000]}
                                   for x in req["places"][:50] if isinstance(x, dict)]
                    for x in p["places"]:
                        was = old.get(x["id"])
                        # An image stays while the look it was made from does.
                        if was and was.get("image") and was["look"] == x["look"]:
                            x["image"] = was["image"]
                if isinstance(req.get("outfits"), list):
                    chars = {c["id"] for c in p["characters"]}
                    p["outfits"] = [{"id": x.get("id") if ID.match(str(x.get("id", ""))) else new_id("o"),
                                     "character": x["character"], "name": str(x.get("name", ""))[:60],
                                     "look": str(x.get("look", ""))[:2000]}
                                    for x in req["outfits"][:100] if isinstance(x, dict) and x.get("character") in chars]
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
                    old, c["name"] = c["name"], req["name"].strip()[:40]
                    # The prompts name people (the picture's are "Person 1", ...): rename them there too.
                    for s in p["shots"]:
                        if c["id"] in s["characters"]:
                            s["still_prompt"] = name_to(s["still_prompt"], old, c["name"])
                            s["motion_prompt"] = name_to(s["motion_prompt"], old, c["name"])
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
                if "place" in req:
                    s["place"] = req["place"] if find(p["places"], req["place"]) else None
                if isinstance(req.get("cast"), dict):
                    cast = s.setdefault("cast", {})
                    for cid, m in req["cast"].items():
                        if cid in s["characters"] and isinstance(m, dict):
                            cur = cast.setdefault(cid, {"position": "", "outfit": None})
                            if isinstance(m.get("position"), str):
                                cur["position"] = m["position"][:80]
                            if "outfit" in m:
                                o = find(p["outfits"], m["outfit"]) if m["outfit"] else None
                                cur["outfit"] = o["id"] if o and o["character"] == cid else None
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
                if kind == "shots" and not p["brief"].strip() and not p.get("picture"):
                    return self.json({"error": "write the brief first"}, 400)
                if missing_models(kind):
                    return self.json({"error": "models missing: " + ", ".join(missing_models(kind))}, 409)
                shots = req.get("shots", "all")
                added = enqueue(p, kind, shots if isinstance(shots, list) else "all", bool(req.get("replace")))
                return self.json({"queued": added})
        self.json({"error": "not found"}, 404)


if __name__ == "__main__":
    load_all()
    forget_comfy()  # no job runs yet: whatever ComfyUI still has of ours is a copy
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=hold_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
