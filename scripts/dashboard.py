"""Stage dashboard: one page, four panels (service board, agent trace, power, WAN + approval), pushed over SSE.
Standard library only; it reads the logs the other pieces write and never calls the ops API, so it adds nothing to
logs/ops-api.log and is safe to leave open beside a running drill.

  .venv/bin/python scripts/dashboard.py                    # http://0.0.0.0:8097/  (DASH_HOST, DASH_PORT)
  DASH_ACTIONS=1 .venv/bin/python scripts/dashboard.py     # enable the inject / reset / WAN buttons (stage only)

  GET  /                  the page (scripts/dashboard.html)
  GET  /events            SSE: {"state": ...} every second, {"trace": [...]} as rows arrive, {"power": [...]}
  GET  /api/state         the same state once (scripts/preflight.sh checks it)
  GET  /api/runs          archived runs (logs/runs/*/meta.json, newest first)
  GET  /api/replay?run=X  every row of one archived run with its time, for the page's REPLAY mode
  GET  /board/            the victim's board page, from victim/board/ (proxied from DASH_BOARD_URL if the file is
                          missing): served here so a front end that is down shows red instead of a blank page
  GET  /api/              the victim's /api/, for that page's own poll (exact path only)
  POST /api/action        {"action": "inject"|"reset"|"wan-down"|"wan-up", "case": ...}   only with DASH_ACTIONS=1
  POST /api/approval      {"id": ..., "decision": "approve"|"reject"}

Files read (each optional; a panel without its file says so instead of failing):
  logs/ops-api.log         agent tool calls (truncated by every drill: the tail starts over when the file shrinks)
  logs/reviews.jsonl       reviewer verdicts            logs/incidents.jsonl   watchdog events
  logs/tier-state.json     {"tier": 1|2, "mode": ..., "reason": ..., "ts": ...}   written by the Tier controller
  logs/tier.jsonl          Tier changes                 logs/power.jsonl       {"ts", "mw", ...} from scripts/power.py
  logs/approvals/pending.json  {"id", "action", "args", "reason", "ts", "channel"?}   written by the approval gate
The approval contract: this server answers a pending request by writing logs/approvals/<id>.decision.json
{"id", "decision": "approve"|"reject", "ts", "by": "dashboard"}; the gate polls for that file.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGS = pathlib.Path(os.environ.get("DASH_LOGS", ROOT / "logs"))   # override only to test the page on a copy
PAGE = pathlib.Path(__file__).resolve().parent / "dashboard.html"
HEALTH = [u.strip() for u in os.environ.get(
    "DASH_HEALTH", "http://127.0.0.1:8880/,http://127.0.0.1:8880/api/").split(",") if u.strip()]
BOARD = os.environ.get("DASH_BOARD_URL", "http://127.0.0.1:8880/board/")
BOARD_FILE = ROOT / "victim" / "board" / "index.html"   # the same page the victim serves; DASH_BOARD_URL is the fallback
ACTIONS = os.environ.get("DASH_ACTIONS") == "1"
CARDS = ("nginx-stopped", "container-removed", "bad-config", "upstream-paused", "upstream-500")   # stage buttons
# DASH_EXTRA_CARDS=compound-config-upstream-down,compound-detached-paused lets the API inject the compound cases too
CARDS += tuple(c for c in os.environ.get("DASH_EXTRA_CARDS", "").split(",") if c)
APPROVALS = LOGS / "approvals"


def http_status(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def read_json(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def pgrep(pattern):
    return bool(subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.strip())


def last_rows(p, n):
    try:
        with open(p, errors="replace") as f:
            rows = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in rows:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# --- state, cached: one probe a second whatever the number of open pages -------------------------------------------
_state, _state_t, _lock = None, 0.0, threading.Lock()


def wan_status():
    w = ROOT / "scripts" / "wan.sh"
    if not w.exists():
        return "unknown (no scripts/wan.sh)"
    r = subprocess.run([str(w), "status"], capture_output=True, text=True, timeout=10)
    return (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "unknown"


def incident_state():
    open_inc = None
    for e in last_rows(LOGS / "incidents.jsonl", 200):
        if e.get("event") == "open":
            open_inc = e
        elif e.get("event") == "close" and open_inc and e.get("id") == open_inc.get("id"):
            open_inc = None
        elif open_inc and e.get("id") == open_inc.get("id"):
            open_inc = dict(open_inc, last=e.get("event"))
    return open_inc


def state():
    global _state, _state_t
    with _lock:
        if _state is not None and time.time() - _state_t < 1.0:
            return _state
        health = {u: http_status(u) for u in HEALTH}
        pending = read_json(APPROVALS / "pending.json")
        if pending and (APPROVALS / f"{pending.get('id')}.decision.json").exists():
            pending = None
        wd = LOGS / "watchdog.pid"
        try:
            wd_alive = wd.exists() and os.kill(int(wd.read_text()), 0) is None
        except (OSError, ValueError):
            wd_alive = False
        _state = dict(
            ts=time.time(), health=health, healthy=all(c == 200 for c in health.values()),
            wan=wan_status(), tier=read_json(LOGS / "tier-state.json"), incident=incident_state(),
            approval=pending, approval_mode=os.environ.get("APPROVAL", "unset"),
            drill_running=pgrep("nemoclaw-drill.sh"), watchdog=wd_alive, actions=ACTIONS)
        _state_t = time.time()
        return _state


# --- trace rows: what the page shows in the trace panel ------------------------------------------------------------
def classify(result):
    r = str(result)
    for k in ("review-rejected", "refused", "denied"):        # the guard prefixes agent.py puts on a blocked call
        if r.startswith(k + ":"):
            return k
    return "ok"


def call_row(d):
    return dict(src="call", t=d.get("ts"), tool=d.get("tool"), args=d.get("args"), cls=classify(d.get("result")),
                text=str(d.get("result", ""))[:600], error=bool(d.get("is_error")))


def review_row(d):
    return dict(src="review", t=d.get("ts"), tool=d.get("action"), args=d.get("args"),
                cls="ok" if d.get("verdict") == "APPROVE" else "review-rejected",
                text=f"{d.get('verdict')}: {d.get('reason', '')}"[:600])


def event_row(d, src):
    return dict(src=src, t=d.get("ts"), tool=d.get("event") or f"tier {d.get('tier')}", cls="info",
                text=json.dumps({k: v for k, v in d.items() if k not in ("ts",)}, ensure_ascii=False)[:400])


TAILS = (("ops-api.log", call_row), ("reviews.jsonl", review_row),
         ("incidents.jsonl", lambda d: event_row(d, "incident")), ("tier.jsonl", lambda d: event_row(d, "tier")))


class Tail:
    """Per-connection reader of one log; starts at the last `back` rows, starts over when the file shrinks."""

    def __init__(self, path, back=40):
        self.path, self.pos, self.back = path, None, back

    def read(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return [], False
        reset = False
        if self.pos is None or size < self.pos:
            reset = self.pos is not None
            with open(self.path, "rb") as f:
                data = f.read()
            lines = data.decode(errors="replace").splitlines()[-self.back:]
            self.pos = size
        else:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
            # keep a partial last line for the next read
            cut = data.rfind(b"\n") + 1
            self.pos += cut
            lines = data[:cut].decode(errors="replace").splitlines()
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
        return rows, reset


# --- replay ---------------------------------------------------------------------------------------------------------
def runs():
    out = []
    for m in sorted((LOGS / "runs").glob("*/meta.json"), reverse=True):
        meta = read_json(m) or {}
        out.append(dict(meta, run=m.parent.name))
    return out


def clock_to_epoch(hms, start):
    """ops-api.log keeps HH:MM:SS only; place it on the run's start day (next day if it is before the start)."""
    try:
        h, m, s = (int(x) for x in str(hms).split(":"))
    except ValueError:
        return None
    day = datetime.datetime.fromtimestamp(start)
    t = day.replace(hour=h, minute=m, second=s, microsecond=0).timestamp()
    return t + 86400 if t < start - 3600 else t


def replay(name):
    if name not in {r["run"] for r in runs()}:
        return None
    d = LOGS / "runs" / name
    meta = read_json(d / "meta.json") or {}
    start = float(meta.get("start") or 0)
    rows = []
    for fname, fn in TAILS:
        for raw in last_rows(d / fname, 100000):
            r = fn(raw)
            t = r["t"]
            t = clock_to_epoch(t, start) if isinstance(t, str) else t
            if t is not None:
                rows.append(dict(r, t=t))
    rows.sort(key=lambda r: r["t"])
    power = [p for p in last_rows(d / "power.jsonl", 100000) if "ts" in p]
    return dict(meta=meta, rows=rows, power=power)


# --- actions ----------------------------------------------------------------------------------------------------------
def action(body):
    if not ACTIONS:
        return 403, {"error": "actions disabled (start with DASH_ACTIONS=1 on the stage machine)"}
    a = body.get("action")
    if pgrep("nemoclaw-drill.sh"):
        return 409, {"error": "a drill or A/B is running on the victim; not touching it"}
    py = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
    if a == "inject":
        case = body.get("case")
        if case not in CARDS:
            return 400, {"error": f"case must be one of {CARDS}"}
        cmd = [py, "scripts/faults.py", "inject", case]
    elif a == "reset":
        cmd = [py, "scripts/faults.py", "reset"]
    elif a in ("wan-down", "wan-up"):
        cmd = ["scripts/wan.sh", "down", "--yes"] if a == "wan-down" else ["scripts/wan.sh", "up"]
    else:
        return 400, {"error": "unknown action"}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=ROOT)
    global _state
    _state = None                                      # show the effect on the next tick
    return (200 if r.returncode == 0 else 500), {"cmd": " ".join(cmd[1:]), "rc": r.returncode,
                                                 "out": (r.stdout + r.stderr)[-1500:]}


def approval(body):
    p = read_json(APPROVALS / "pending.json")
    if not p or p.get("id") != body.get("id"):
        return 404, {"error": "no such pending approval"}
    if body.get("decision") not in ("approve", "reject"):
        return 400, {"error": "decision must be approve or reject"}
    out = APPROVALS / f"{p['id']}.decision.json"
    out.write_text(json.dumps(dict(id=p["id"], decision=body["decision"], ts=time.time(), by="dashboard")) + "\n")
    return 200, {"ok": True}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if u.path == "/api/state":
            return self._send(200, state())
        if u.path == "/api/runs":
            return self._send(200, {"runs": runs()})
        if u.path == "/api/replay":
            r = replay((urllib.parse.parse_qs(u.query).get("run") or [""])[0])
            return self._send(200 if r else 404, r or {"error": "no such run"})
        if u.path.startswith("/board"):
            if BOARD_FILE.exists():     # served from here: a front end that is down must show red, not a blank page
                return self._send(200, BOARD_FILE.read_bytes(), "text/html; charset=utf-8")
            return self.proxy_board(BOARD + u.path[len("/board"):].lstrip("/"))
        if u.path == "/api/":           # the board page polls /api/ on its own origin; through this proxy that is here
            return self.proxy_board(BOARD.rsplit("/board/", 1)[0] + "/api/")
        if u.path == "/events":
            return self.events()
        self._send(404, {"error": "not found"})

    def proxy_board(self, url):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return self._send(r.status, r.read(), r.headers.get("Content-Type", "text/html"))
        except urllib.error.HTTPError as e:
            return self._send(e.code, e.read() or b"", e.headers.get("Content-Type", "text/plain"))
        except Exception as e:
            return self._send(502, f"board unreachable: {type(e).__name__}".encode(), "text/plain")

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        tails = [(Tail(LOGS / f), fn, f) for f, fn in TAILS]
        power = Tail(LOGS / "power.jsonl", back=240)
        try:
            while True:
                msg = {"state": state()}
                trace, resets = [], []
                for t, fn, name in tails:
                    rows, reset = t.read()
                    if reset:
                        resets.append(name)
                    trace += [fn(r) for r in rows]
                if trace or resets:
                    msg["trace"], msg["reset"] = trace, resets
                prow, _ = power.read()
                if prow:
                    msg["power"] = [dict(ts=p.get("ts"), mw=p.get("mw")) for p in prow if "ts" in p]
                self.wfile.write(f"data: {json.dumps(msg, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if self.path == "/api/action":
            return self._send(*action(body))
        if self.path == "/api/approval":
            return self._send(*approval(body))
        self._send(404, {"error": "not found"})


def main():
    APPROVALS.mkdir(parents=True, exist_ok=True)
    host, port = os.environ.get("DASH_HOST", "0.0.0.0"), int(os.environ.get("DASH_PORT", "8097"))
    print(f"dashboard on http://{host}:{port}/  actions={'on' if ACTIONS else 'off'}  health={HEALTH}", flush=True)
    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == "__main__":
    main()
