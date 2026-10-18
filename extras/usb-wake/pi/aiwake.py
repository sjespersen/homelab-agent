#!/usr/bin/env python3
"""Wakes the aibox over USB, like the desk buddy does for the Mac.

The Pi is plugged into the aibox as a USB keyboard (aiwake-gadget.sh).
  POST /api/wake   remote wakeup while the host sleeps, Shift tap while it is awake
  GET  /api/state  USB state: configured (host awake), suspended (asleep), not attached
"""
import glob
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 80
SHIFT = bytes([0x02, 0, 0, 0, 0, 0, 0, 0])
RELEASE = bytes(8)


def udc():
    found = glob.glob("/sys/class/udc/*")
    return found[0] if found else None


def usb_state():
    d = udc()
    if not d:
        return "no udc"
    with open(d + "/state") as f:
        return f.read().strip()


def tap_shift():
    with open("/dev/hidg0", "wb", buffering=0) as hid:
        hid.write(SHIFT)
        time.sleep(0.03)
        hid.write(RELEASE)


def wake():
    state = usb_state()
    if state == "suspended":
        # Asks the UDC to signal remote wakeup (usb_gadget_wakeup)
        try:
            with open(udc() + "/srp", "w") as f:
                f.write("1")
        except OSError as e:
            return f"remote wakeup failed: {e}"
        for _ in range(30):
            time.sleep(0.1)
            if usb_state() != "suspended":
                return "remote wakeup sent, host resumed"
        return "remote wakeup sent, host still suspended"
    if state == "configured":
        try:
            tap_shift()
        except OSError as e:
            return f"shift tap failed: {e}"
        return "host awake, shift tapped"
    return f"cannot wake: usb state '{state}'"


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
            self.reply(200, json.dumps({"usb": usb_state()}) + "\n", "application/json")
        else:
            self.reply(200, "aiwake: POST /api/wake wakes the aibox, GET /api/state\n")

    def do_POST(self):
        if self.path == "/api/wake":
            self.reply(200, wake() + "\n")
        else:
            self.reply(404, "not found\n")

    def log_message(self, fmt, *args):
        print(self.address_string(), fmt % args, flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
