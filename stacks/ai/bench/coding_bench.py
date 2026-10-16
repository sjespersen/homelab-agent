#!/usr/bin/env python3
"""Coding bench for the AI box's chat models: asks a model to solve the tasks in tasks.py
through an OpenAI-compatible API, runs each answer against the task's hidden tests, and
records whether it passed, how long the answer took and how many tokens it used.

  # one model (repeat with the other, then compare)
  ./coding_bench.py run --label strata --base-url http://aibox:8005/v1 --model qwen3.8-flash-next
  ./coding_bench.py report results/strata results/glyd
  ./coding_bench.py self-check        # the reference solutions pass their own tests

The key comes from --key or $AI_API_KEY. Answers and test output are saved under
results/<label>/; an interrupted run picks up where it stopped. The model's code runs on this
computer (python3 -I in a temporary folder, with a time limit): it's code you asked for, but
read it if a model starts doing odd things.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tasks import TASKS  # noqa: E402

INSTRUCTIONS = ("\n\nAnswer with the complete Python code in one ```python block. Standard library "
                "only. No tests, example usage or input() calls.")


def run_tests(code, tests, timeout):
    """Runs <code> followed by <tests>; returns (passed, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "solution.py")
        with open(path, "w") as f:
            f.write(code + "\n\n# --- tests ---\n" + tests + "\n")
        try:
            p = subprocess.run([sys.executable, "-I", path], cwd=tmp, capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout} s"
        return p.returncode == 0, (p.stdout + p.stderr)[-3000:]


def extract_code(text):
    """The last ```python block of the answer (after any thinking), else the last ``` block."""
    text = text.split("</think>")[-1]
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.S)
    blocks = blocks or re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    return blocks[-1] if blocks else None


def ask(args, prompt):
    body = {"model": args.model, "messages": [{"role": "user", "content": prompt + INSTRUCTIONS}],
            "max_tokens": args.max_tokens, "temperature": args.temperature, "top_p": args.top_p,
            "stream": False}
    if args.effort != "none":
        body["reasoning_effort"] = args.effort
    req = urllib.request.Request(args.base_url.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {args.key}"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=args.request_timeout) as resp:
        data = json.load(resp)
    return data, time.time() - start


def cmd_run(args):
    if not args.key:
        sys.exit("No API key: pass --key or set AI_API_KEY.")
    out = os.path.join(args.out, args.label)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "settings.json"), "w") as f:
        json.dump({k: v for k, v in vars(args).items() if k not in ("key", "func")}, f, indent=2)
    tasks = [t for t in TASKS if args.set in ("all", t.get("set", "basic"))
             and (not args.tasks or t["id"] in args.tasks.split(","))]
    for run in range(1, args.runs + 1):
        for t in tasks:
            path = os.path.join(out, f"{t['id']}.{run}.json")
            if os.path.exists(path):
                continue
            print(f"[{args.label}] run {run}/{args.runs}  {t['id']:<20}", end=" ", flush=True)
            try:
                data, secs = ask(args, t["prompt"])
            except (urllib.error.URLError, OSError, ValueError) as e:
                detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else ""
                print(f"request failed: {e} {detail}")
                if args.stop_on_error:
                    sys.exit(1)
                continue
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            thinking = msg.get("reasoning_content") or msg.get("reasoning") or ""
            usage = data.get("usage") or {}
            tokens = usage.get("completion_tokens") or 0
            code = extract_code(content)
            passed, output = run_tests(code, t["tests"], args.test_timeout) if code else (False, "no code block")
            result = {"task": t["id"], "run": run, "passed": passed, "seconds": round(secs, 1),
                      "completion_tokens": tokens, "tok_per_s": round(tokens / secs, 1) if secs else 0,
                      "finish_reason": data["choices"][0].get("finish_reason"),
                      "test_output": output, "code": code, "content": content, "thinking": thinking}
            with open(path, "w") as f:
                json.dump(result, f, indent=1)
            print(f"{'PASS' if passed else 'fail'}  {secs:6.1f} s  {tokens:6d} tok  "
                  f"{result['tok_per_s']:5.1f} tok/s" + ("" if passed else f"  ({output.strip().splitlines()[-1][:60] if output.strip() else ''})"))
    report([out])


def load(folder):
    rows = []
    for name in sorted(os.listdir(folder)):
        if re.fullmatch(r".+\.\d+\.json", name):
            with open(os.path.join(folder, name)) as f:
                rows.append(json.load(f))
    return rows


def report(folders):
    data = {os.path.basename(os.path.normpath(f)): load(f) for f in folders}
    labels = list(data)
    task_ids = [t["id"] for t in TASKS if any(r["task"] == t["id"] for rows in data.values() for r in rows)]
    w = max(12, *(len(l) for l in labels))
    print("\n" + "task".ljust(20) + "".join(l.rjust(w + 2) for l in labels))
    for tid in task_ids:
        line = tid.ljust(20)
        for l in labels:
            rs = [r for r in data[l] if r["task"] == tid]
            line += (f"{sum(r['passed'] for r in rs)}/{len(rs)}" if rs else "-").rjust(w + 2)
        print(line)
    print("-" * (20 + (w + 2) * len(labels)))
    def stat(name, fn):
        print(name.ljust(20) + "".join(fn(data[l]).rjust(w + 2) for l in labels))
    def pct(rs):
        return f"{sum(r['passed'] for r in rs)}/{len(rs)} ({100 * sum(r['passed'] for r in rs) / len(rs):.0f}%)" if rs else "-"
    def med(rs, key):
        v = sorted(r[key] for r in rs)
        return v[len(v) // 2] if v else 0
    stat("passed", pct)
    stat("tasks solved >=1x", lambda rs: f"{len({r['task'] for r in rs if r['passed']})}/{len({r['task'] for r in rs})}")
    stat("median s / answer", lambda rs: f"{med(rs, 'seconds'):.0f}")
    stat("median tokens", lambda rs: f"{med(rs, 'completion_tokens')}")
    stat("median tok/s", lambda rs: f"{med(rs, 'tok_per_s'):.1f}")
    stat("total minutes", lambda rs: f"{sum(r['seconds'] for r in rs) / 60:.0f}")
    stat("cut off (length)", lambda rs: str(sum(r["finish_reason"] == "length" for r in rs)))


def cmd_self_check(args):
    bad = 0
    for t in TASKS:
        ok, output = run_tests(t["reference"], t["tests"], args.test_timeout)
        print(f"{t['id']:<20} {'ok' if ok else 'FAILED'}")
        if not ok:
            bad += 1
            print(output)
    sys.exit(1 if bad else 0)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    r = sub.add_parser("run", help="run the tasks against one model")
    r.add_argument("--label", required=True, help="name for this model/setting, e.g. strata")
    r.add_argument("--base-url", required=True, help="OpenAI-compatible base URL ending in /v1")
    r.add_argument("--model", default="default")
    r.add_argument("--key", default=os.environ.get("AI_API_KEY", ""))
    r.add_argument("--runs", type=int, default=3, help="tries per task (default 3)")
    r.add_argument("--set", default="basic", choices=["basic", "hard", "all"],
                   help="basic: 12 shorter tasks (default); hard: 5 longer programs; all: both")
    r.add_argument("--tasks", help="comma-separated task ids (default: all in the set)")
    r.add_argument("--effort", default="medium", help="reasoning_effort, or none to leave it out")
    r.add_argument("--temperature", type=float, default=0.6)
    r.add_argument("--top-p", type=float, default=0.95)
    r.add_argument("--max-tokens", type=int, default=28000, help="fits a 32K context")
    r.add_argument("--request-timeout", type=int, default=3600)
    r.add_argument("--test-timeout", type=int, default=60)
    r.add_argument("--stop-on-error", action="store_true")
    r.add_argument("--out", default=os.path.join(HERE, "results"))
    r.set_defaults(func=cmd_run)
    rep = sub.add_parser("report", help="compare result folders side by side")
    rep.add_argument("folders", nargs="+")
    rep.set_defaults(func=lambda a: report(a.folders))
    s = sub.add_parser("self-check", help="check the tests against the reference solutions")
    s.add_argument("--test-timeout", type=int, default=60)
    s.set_defaults(func=cmd_self_check)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
