"""A small client for ComfyUI's HTTP API, through SwarmUI's pass-through (/ComfyBackendDirect):
upload an input file, queue a graph, wait for it, fetch its output."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("COMFY_URL", "http://swarmui:7801/ComfyBackendDirect")
CLIENT = "homelab-studio"


class ComfyError(Exception):
    pass


def _call(method, path, body=None, ctype="application/json", timeout=60):
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Content-Type": ctype} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ComfyError(f"{path}: HTTP {e.code}: {e.read().decode(errors='replace')[:2000]}") from None
    except OSError as e:
        raise ComfyError(f"{path}: {e}") from None


def ready():
    try:
        json.loads(_call("GET", "/system_stats", timeout=5))
        return True
    except (ComfyError, ValueError):
        return False


def upload(path, name):
    """Puts a local file into ComfyUI's input folder as `name` (LoadImage and LoadVideo read it)."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n".encode(), data, f"\r\n--{boundary}--\r\n".encode()])
    out = json.loads(_call("POST", "/upload/image", body, f"multipart/form-data; boundary={boundary}", 300))
    return (out.get("subfolder") + "/" if out.get("subfolder") else "") + out["name"]


def queue(graph):
    out = json.loads(_call("POST", "/prompt", json.dumps({"prompt": graph, "client_id": CLIENT}).encode()))
    if out.get("node_errors"):
        raise ComfyError(json.dumps(out["node_errors"])[:2000])
    return out["prompt_id"]


def wait(prompt_id, timeout=6 * 3600, stop=lambda: False):
    """Waits for the graph; returns its history entry. stop(): True cancels it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop():
            cancel(prompt_id)
            raise ComfyError("stopped")
        hist = json.loads(_call("GET", f"/history/{prompt_id}"))
        entry = hist.get(prompt_id)
        if entry and entry.get("status", {}).get("completed") is not None:
            status = entry["status"]
            if status.get("status_str") == "error":
                msgs = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
                err = msgs[0] if msgs else {}
                raise ComfyError(f"{err.get('node_type', '?')}: {err.get('exception_message', 'failed')}".strip())
            return entry
        time.sleep(2)
    raise ComfyError("timed out")


def cancel(prompt_id):
    try:
        _call("POST", "/queue", json.dumps({"delete": [prompt_id]}).encode())
        _call("POST", "/interrupt", b"{}")
    except ComfyError:
        pass


def outputs(entry):
    """The files a finished graph saved: [(filename, subfolder, type)]."""
    files = []
    for out in entry.get("outputs", {}).values():
        for key in ("images", "videos", "gifs", "video"):
            for f in out.get(key, []) or []:
                if isinstance(f, dict) and f.get("filename"):
                    files.append((f["filename"], f.get("subfolder", ""), f.get("type", "output")))
    return files


def fetch(filename, subfolder, kind, dest):
    q = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": kind})
    data = _call("GET", "/view?" + q, timeout=600)
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)


def run(graph, dest, stop=lambda: False):
    """Queues the graph, waits, saves its (last) output file to dest. Returns seconds taken."""
    t = time.time()
    entry = wait(queue(graph), stop=stop)
    files = outputs(entry)
    if not files:
        raise ComfyError("the graph saved nothing")
    fetch(*files[-1], dest)
    return round(time.time() - t)
