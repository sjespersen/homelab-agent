#!/usr/bin/env python3
"""Wakes the box with Wake-on-LAN over a direct cable.

The Pi has a USB-Ethernet adapter cabled straight to the box's Ethernet port, so the
magic packet reaches the box while the box itself uses Wi-Fi. It goes out as a raw
Ethernet frame (EtherType 0x0842), so the link needs no IP addresses.
  GET  /           a page with a Wake button; links the box's page once it answers
  POST /api/wake   send the magic packet
  GET  /api/state  link state of the cable (up when the box's port has power) and, with
                   AIWAKE_BOX, whether the box answers
Settings come from the environment (aiwake.service): AIWAKE_MAC, AIWAKE_IFACE, AIWAKE_PORT,
AIWAKE_BOX (host:port that answers while the box is awake, e.g. 192.168.0.141:8005) and
AIWAKE_OPEN (the page the Wake page links then, e.g. http://aibox.local:8005).
"""
import glob
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAC = os.environ.get("AIWAKE_MAC", "")
PORT = int(os.environ.get("AIWAKE_PORT", "80"))
BOX = os.environ.get("AIWAKE_BOX", "")
OPEN = os.environ.get("AIWAKE_OPEN", "")


def iface():
    """AIWAKE_IFACE, else the first wired interface (the USB-Ethernet adapter)."""
    if os.environ.get("AIWAKE_IFACE"):
        return os.environ["AIWAKE_IFACE"]
    for path in sorted(glob.glob("/sys/class/net/*")):
        name = os.path.basename(path)
        if name != "lo" and not os.path.exists(path + "/wireless") and os.path.exists(path + "/device"):
            return name
    return None


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def box_awake():
    host, _, port = BOX.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=1.5).close()
        return True
    except (OSError, ValueError):
        return False


def link_state():
    i = iface()
    if not i:
        state = {"iface": None, "link": "no adapter"}
    else:
        state = {"iface": i, "link": "up" if read(f"/sys/class/net/{i}/carrier", "0") == "1" else "down"}
    if BOX:
        state["box"] = "awake" if box_awake() else "asleep"
    return state


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wake the box</title>
<style>
:root { color-scheme: light dark; --bg: #f6f6f4; --fg: #1d1d1b; --muted: #6b6b66; --accent: #2f6fde; }
@media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #ececea; --muted: #9a9a94; --accent: #6b9cf0; } }
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
  color: var(--fg); font: 16px/1.4 system-ui, sans-serif; text-align: center; padding: 0 16px; }
button { font: inherit; font-size: 1.2rem; padding: .8em 2.2em; border: 0; border-radius: 999px;
  background: var(--accent); color: #fff; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
p { color: var(--muted); }
a { color: var(--accent); }
</style></head><body><main>
<h1 id="title">Box</h1>
<button id="wake">Wake</button>
<p id="status">Checking…</p>
</main><script>
const OPEN = %s, status = document.getElementById("status"), btn = document.getElementById("wake");
let waking = false;
async function check() {
  try {
    const s = await (await fetch("/api/state", {cache: "no-store"})).json();
    if (s.box === "awake") {
      document.getElementById("title").textContent = "The box is awake";
      btn.hidden = true; btn.disabled = false; waking = false;
      if (OPEN) status.innerHTML = '<a href="' + OPEN + '">' + OPEN + '</a>';
      else status.textContent = "";
    } else {
      document.getElementById("title").textContent = s.box === "asleep" ? "The box is asleep" : "Box";
      btn.hidden = false;
      if (!waking) status.textContent = s.link === "up" ? "Cable connected." : "Cable: " + s.link;
    }
  } catch (e) { status.textContent = "No answer from the relay."; }
  setTimeout(check, 3000);
}
btn.onclick = async () => {
  btn.disabled = true; waking = true;
  try { status.textContent = await (await fetch("/api/wake", {method: "POST"})).text() + " Waiting for the box…"; }
  catch (e) { status.textContent = "Sending failed."; btn.disabled = false; waking = false; }
};
check();
</script></body></html>
"""


def wake():
    i = iface()
    if not i:
        return "no Ethernet adapter found"
    if not MAC:
        return "AIWAKE_MAC is not set"
    target = bytes.fromhex(MAC.replace(":", ""))
    with open(f"/sys/class/net/{i}/address") as f:
        src = bytes.fromhex(f.read().strip().replace(":", ""))
    if read(f"/sys/class/net/{i}/operstate") == "down":
        os.system(f"ip link set {i} up")
    frame = b"\xff" * 6 + src + b"\x08\x42" + b"\xff" * 6 + target * 16
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as s:
        s.bind((i, 0))
        for _ in range(3):
            s.send(frame)
    return f"magic packet sent to {MAC} on {i} (link {link_state()['link']})"


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="text/plain"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/state":
            self.reply(200, json.dumps(link_state()) + "\n", "application/json")
        elif self.path == "/":
            self.reply(200, PAGE % json.dumps(OPEN), "text/html; charset=utf-8")
        else:
            self.reply(404, "not found\n")

    def do_POST(self):
        if self.path == "/api/wake":
            try:
                self.reply(200, wake() + "\n")
            except OSError as e:
                self.reply(500, f"failed: {e}\n")
        else:
            self.reply(404, "not found\n")

    def log_message(self, fmt, *args):
        print(self.address_string(), fmt % args, flush=True)


class DualStackServer(ThreadingHTTPServer):
    """Listens on IPv6 and IPv4: mDNS announces both addresses for aiwake.local."""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


if __name__ == "__main__":
    DualStackServer(("::", PORT), Handler).serve_forever()
