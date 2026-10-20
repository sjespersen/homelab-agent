"""The Studio's API log: what it sent to the chat models and ComfyUI, and what came back. One
JSON object per line in MEDIA/logs/api.jsonl, rotated at 10 MB with 5 old files kept (~60 MB).
Pictures are left out (their size instead); prompts and answers are in full.

    tail -f /srv/media/studio/logs/api.jsonl | jq
"""
import json
import logging
import logging.handlers
import os
import time

DIR = os.path.join(os.environ.get("MEDIA", "/media"), "logs")
_log = logging.getLogger("studio.api")
_log.propagate = False


def _handler():
    if not _log.handlers:
        os.makedirs(DIR, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(os.path.join(DIR, "api.jsonl"), maxBytes=10 * 2**20,
                                                 backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        _log.addHandler(h)
        _log.setLevel(logging.INFO)


def write(kind, **fields):
    """Appends one entry; a log that can't be written never stops a job."""
    try:
        _handler()
        _log.info(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "kind": kind, **fields},
                             ensure_ascii=False, default=str))
    except (OSError, ValueError):
        pass


def without_images(body):
    """A copy of a chat request with its base64 pictures replaced by their size."""
    msgs = []
    for m in body.get("messages", []):
        if m.get("images"):
            m = {**m, "images": [f"<picture, {len(i) * 3 // 4 // 1024} KB>" for i in m["images"]]}
        msgs.append(m)
    return {**body, "messages": msgs}
