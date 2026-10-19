#!/usr/bin/env python3
"""Wakes the box with Wake-on-LAN over a direct cable.

The Pi has a USB-Ethernet adapter cabled straight to the box's Ethernet port, so the
magic packet reaches the box while the box itself uses Wi-Fi. It goes out as a raw
Ethernet frame (EtherType 0x0842), so the link needs no IP addresses.
  POST /api/wake   send the magic packet
  GET  /api/state  link state of the cable (up when the box's port has power)
Settings come from the environment (aiwake.service): AIWAKE_MAC, AIWAKE_IFACE, AIWAKE_PORT.
"""
import glob
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAC = os.environ.get("AIWAKE_MAC", "")
PORT = int(os.environ.get("AIWAKE_PORT", "80"))


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


def link_state():
    i = iface()
    if not i:
        return {"iface": None, "link": "no adapter"}
    return {"iface": i, "link": "up" if read(f"/sys/class/net/{i}/carrier", "0") == "1" else "down"}


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
        else:
            self.reply(200, "aiwake: POST /api/wake wakes the aibox, GET /api/state\n")

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


if __name__ == "__main__":
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
