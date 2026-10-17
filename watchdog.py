#!/usr/bin/env python3
"""Linux Process Watchdog and Auto-Restart System (Python 3 standard library only).

Usage:
  python3 watchdog.py run [--config config/watchdog.json]   start the daemon + web dashboard
  python3 watchdog.py status | start NAME | stop NAME | restart NAME   talk to a running daemon
  python3 watchdog.py validate [--config ...]                 check a config file
"""
import argparse, json, logging, logging.handlers, os, shlex, signal, smtplib, socket
import subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from collections import deque
from datetime import datetime
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CLK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
DEFAULTS = dict(policy="always", max_retries=5, backoff_base=1.0, backoff_max=30.0,
                stable_after=20.0, cpu_limit=90.0, mem_limit_mb=512.0, cwd=None, env={}, autostart=True)


def resolve(p):
    return p if os.path.isabs(p) else os.path.join(BASE, p)


def validate(cfg):
    procs = cfg.get("processes")
    if not isinstance(procs, list) or not procs:
        raise ValueError("config needs a non-empty 'processes' list")
    seen = set()
    for p in procs:
        for k in ("name", "command"):
            if not p.get(k):
                raise ValueError("every process needs a '%s'" % k)
        if p["name"] in seen:
            raise ValueError("duplicate process name: %s" % p["name"])
        seen.add(p["name"])
        if p.get("policy", "always") not in ("always", "on-failure", "never"):
            raise ValueError("%s: policy must be always, on-failure or never" % p["name"])
    return cfg


def load_config(path):
    with open(path) as f:
        return validate(json.load(f))


def sd_notify(msg):
    """Tell systemd we are alive (no-op when not started by systemd)."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.connect(addr); s.sendall(msg.encode()); s.close()
    except OSError:
        pass


class Managed:
    def __init__(self, pc):
        self.c = {**DEFAULTS, **pc}
        self.name = pc["name"]
        self.proc = None; self.pid = None; self.status = "stopped"; self.started = None
        self.restarts = 0; self.crashes = 0; self.streak = 0; self.next_at = 0.0
        self.last_exit = None; self.cpu = 0.0; self.rss = 0.0; self.prev = None; self.hot = 0
        self.manual_stop = False; self.hist = deque(maxlen=60)

    def view(self, now):
        c = self.c
        up = now - self.started if self.status == "running" and self.started else 0
        return {"name": self.name, "command": c["command"], "status": self.status, "pid": self.pid,
                "cpu": round(self.cpu, 1), "rss_mb": round(self.rss, 1), "mem_limit_mb": c["mem_limit_mb"],
                "cpu_limit": c["cpu_limit"], "uptime": int(up), "restarts": self.restarts,
                "crashes": self.crashes, "streak": self.streak, "max_retries": c["max_retries"],
                "policy": c["policy"], "last_exit": self.last_exit,
                "next_restart_in": max(0, round(self.next_at - now, 1)) if self.status == "backoff" else None,
                "history": list(self.hist)}


class Watchdog:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.events = deque(maxlen=300)
        self.t0 = time.time()
        self.log_dir = resolve(cfg.get("log_dir", "logs"))
        self.state_file = resolve(cfg.get("state_file", "state/state.json"))
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        self.logger = logging.getLogger("watchdog." + str(id(self)))
        self.logger.setLevel(logging.INFO)
        fh = logging.handlers.RotatingFileHandler(os.path.join(self.log_dir, "watchdog.log"), maxBytes=1_000_000, backupCount=3)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(fh)
        if cfg.get("console_log", False):
            self.logger.addHandler(logging.StreamHandler())
        state = self._read_state()
        self.procs = {}
        for pc in cfg["processes"]:
            m = Managed(pc)
            s = state.get(m.name, {})
            m.restarts = s.get("restarts", 0); m.crashes = s.get("crashes", 0)
            self.procs[m.name] = m

    # ---------- persistence, logging, alerts ----------
    def _read_state(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        data = {n: {"restarts": m.restarts, "crashes": m.crashes} for n, m in self.procs.items()}
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.state_file)

    def event(self, level, name, msg):
        self.events.append({"time": datetime.now().strftime("%H:%M:%S"), "level": level, "process": name, "message": msg})
        self.logger.log({"info": 20, "warn": 30, "error": 40}.get(level, 20), "[%s] %s" % (name, msg))

    def alert(self, level, name, msg):
        self.event(level, name, "ALERT: " + msg)
        a = self.cfg.get("alerts", {})
        payload = {"process": name, "level": level, "message": msg, "host": socket.gethostname(), "time": time.time()}
        if a.get("webhook"):
            threading.Thread(target=self._webhook, args=(a["webhook"], payload), daemon=True).start()
        if a.get("email", {}).get("enabled"):
            threading.Thread(target=self._email, args=(a["email"], payload), daemon=True).start()

    def _webhook(self, url, payload):
        try:
            req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as e:
            self.event("error", "alerts", "webhook failed: %s" % e)

    def _email(self, e, payload):
        try:
            msg = EmailMessage()
            msg["Subject"] = "[watchdog] %s: %s" % (payload["process"], payload["level"])
            msg["From"] = e["from"]; msg["To"] = e["to"]
            msg.set_content(payload["message"] + "\nHost: " + payload["host"])
            with smtplib.SMTP(e["smtp_host"], e.get("smtp_port", 587), timeout=10) as s:
                s.starttls()
                if e.get("user"):
                    s.login(e["user"], e["password"])
                s.send_message(msg)
        except Exception as ex:
            self.event("error", "alerts", "email failed: %s" % ex)

    # ---------- process control ----------
    def start_all(self):
        with self.lock:
            for m in self.procs.values():
                if m.c["autostart"]:
                    self._spawn(m)

    def _spawn(self, m, restart=False):
        c = m.c
        argv = [sys.executable if t == "{python}" else t for t in shlex.split(c["command"])]
        env = {**os.environ, **{k: str(v) for k, v in c["env"].items()}}
        try:
            out = open(os.path.join(self.log_dir, m.name + ".out"), "ab")
            m.proc = subprocess.Popen(argv, cwd=resolve(c["cwd"]) if c["cwd"] else BASE, env=env,
                                      stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            out.close()
        except OSError as e:
            self.event("error", m.name, "could not start: %s" % e)
            m.pid = None
            self._exited(m, 127)
            return
        m.pid = m.proc.pid; m.status = "running"; m.started = time.time(); m.prev = None; m.hot = 0
        m.manual_stop = False; m.hist.clear()
        self.event("info", m.name, ("restarted" if restart else "started") + " (pid %d)" % m.pid)
        self._save_state()

    def _terminate(self, m):
        if m.proc and m.proc.poll() is None:
            try:
                os.killpg(m.proc.pid, signal.SIGTERM)
                m.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(m.proc.pid, signal.SIGKILL); m.proc.wait()
            except ProcessLookupError:
                pass
        m.proc = None; m.pid = None; m.cpu = 0; m.rss = 0; m.status = "stopped"

    def control(self, name, action):
        with self.lock:
            m = self.procs.get(name)
            if not m:
                return False, "unknown process"
            if action in ("stop", "restart"):
                m.manual_stop = True
                self._terminate(m)
                self.event("info", name, "stopped manually (will not auto-restart)")
            if action in ("start", "restart"):
                if m.status == "running":
                    return True, "already running"
                m.streak = 0
                self._spawn(m)
            elif action != "stop":
                return False, "unknown action"
            return True, "ok"

    def shutdown(self):
        with self.lock:
            for m in self.procs.values():
                m.manual_stop = True
                self._terminate(m)
            self._save_state()
        self.event("info", "watchdog", "shut down, all managed processes stopped")

    def reload(self, path):
        cfg = load_config(path)
        with self.lock:
            names = {p["name"] for p in cfg["processes"]}
            for n in list(self.procs):
                if n not in names:
                    self._terminate(self.procs.pop(n)); self.event("info", n, "removed by config reload")
            for pc in cfg["processes"]:
                if pc["name"] in self.procs:
                    self.procs[pc["name"]].c = {**DEFAULTS, **pc}
                else:
                    m = Managed(pc); self.procs[m.name] = m; self._spawn(m)
            self.cfg = cfg
        self.event("info", "watchdog", "configuration reloaded")

    # ---------- monitoring ----------
    def tick(self):
        now = time.time()
        with self.lock:
            for m in self.procs.values():
                if m.status == "running":
                    rc = m.proc.poll()
                    if rc is not None:
                        self._exited(m, rc)
                    else:
                        self._health(m, now)
                elif m.status == "backoff" and now >= m.next_at:
                    m.restarts += 1
                    self._spawn(m, restart=True)

    def _sample(self, m, now):
        try:
            with open("/proc/%d/stat" % m.pid) as f:
                s = f.read()
            parts = s[s.rindex(")") + 2:].split()
            ticks = int(parts[11]) + int(parts[12])  # utime + stime
            with open("/proc/%d/statm" % m.pid) as f:
                rss = int(f.read().split()[1]) * PAGE / 1048576.0
        except (OSError, ValueError):
            return
        if m.prev:
            dt = now - m.prev[1]
            if dt > 0:
                m.cpu = max(0.0, (ticks - m.prev[0]) / CLK / dt * 100)
        m.prev = (ticks, now); m.rss = rss

    def _health(self, m, now):
        self._sample(m, now)
        c = m.c
        m.hist.append([round(m.cpu, 1), round(m.rss / c["mem_limit_mb"] * 100, 1)])
        if m.cpu > c["cpu_limit"]:
            m.hot += 1
            if m.hot == 3:
                self.alert("warn", m.name, "CPU above %.0f%% for 3 checks (%.0f%%)" % (c["cpu_limit"], m.cpu))
        else:
            m.hot = 0
        if m.rss > c["mem_limit_mb"]:
            self.alert("error", m.name, "memory %.0f MB exceeds limit %.0f MB, killing" % (m.rss, c["mem_limit_mb"]))
            try:
                os.killpg(m.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return
        if m.streak and now - m.started >= c["stable_after"]:
            m.streak = 0
            self.event("info", m.name, "stable for %ds, retry counter reset" % c["stable_after"])

    def _exited(self, m, rc):
        c = m.c
        m.proc = None; m.pid = None; m.cpu = 0; m.rss = 0; m.prev = None; m.last_exit = rc
        why = "signal %d" % -rc if rc < 0 else "exit code %d" % rc
        if m.manual_stop:
            m.status = "stopped"; return
        m.crashes += 1
        self.event("error", m.name, "terminated unexpectedly (%s)" % why)
        if c["policy"] == "never" or (c["policy"] == "on-failure" and rc == 0):
            m.status = "stopped"
            self.alert("warn", m.name, "stopped (%s); policy '%s' does not restart it" % (why, c["policy"]))
        elif m.streak >= c["max_retries"]:
            m.status = "gaveup"
            self.alert("critical", m.name, "gave up after %d failed restarts, needs manual attention" % c["max_retries"])
        else:
            m.streak += 1
            delay = min(c["backoff_max"], c["backoff_base"] * 2 ** (m.streak - 1))
            m.status = "backoff"; m.next_at = time.time() + delay
            self.event("warn", m.name, "restart %d/%d in %.1fs (exponential backoff)" % (m.streak, c["max_retries"], delay))
        self._save_state()

    # ---------- views ----------
    def snapshot(self):
        with self.lock:
            now = time.time()
            return {"time": now, "uptime": int(now - self.t0), "processes": [m.view(now) for m in self.procs.values()]}

    def recent_events(self, n=100):
        with self.lock:
            return list(self.events)[-n:][::-1]


def make_handler(wd):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            p = self.path.split("?")[0]
            if p == "/api/status":
                return self._send(200, wd.snapshot())
            if p == "/api/events":
                return self._send(200, wd.recent_events())
            if p in ("/", "/index.html"):
                with open(os.path.join(BASE, "web", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            self._send(404, {"error": "not found"})

        def do_POST(self):
            parts = [urllib.parse.unquote(x) for x in self.path.strip("/").split("/")]
            if self.headers.get("X-Requested-With") != "watchdog":
                return self._send(403, {"error": "missing X-Requested-With header"})
            if len(parts) == 4 and parts[:2] == ["api", "proc"]:
                ok, msg = wd.control(parts[2], parts[3])
                return self._send(200 if ok else 400, {"ok": ok, "message": msg})
            self._send(404, {"error": "not found"})
    return H


def run_daemon(args):
    cfg = load_config(args.config)
    cfg["console_log"] = True
    wd = Watchdog(cfg)
    d = cfg.get("dashboard", {})
    server = ThreadingHTTPServer((d.get("host", "127.0.0.1"), d.get("port", 8080)), make_handler(wd))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *a: stop.set())
    signal.signal(signal.SIGINT, lambda *a: stop.set())
    signal.signal(signal.SIGHUP, lambda *a: wd.reload(args.config))
    wd.event("info", "watchdog", "started, dashboard on http://%s:%s" % server.server_address)
    wd.start_all()
    sd_notify("READY=1")
    interval = float(cfg.get("check_interval", 1))
    while not stop.is_set():
        wd.tick()
        sd_notify("WATCHDOG=1")
        stop.wait(interval)
    sd_notify("STOPPING=1")
    server.shutdown()
    wd.shutdown()


def api(args, method, path):
    cfg = load_config(args.config)
    d = cfg.get("dashboard", {})
    url = "http://%s:%s%s" % (d.get("host", "127.0.0.1"), d.get("port", 8080), path)
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None, headers={"X-Requested-With": "watchdog"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)
    except OSError:
        sys.exit("cannot reach the watchdog daemon; is it running? (python3 watchdog.py run)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "status", "start", "stop", "restart", "validate"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--config", default=os.path.join(BASE, "config", "watchdog.json"))
    a = ap.parse_args()
    if a.command == "run":
        run_daemon(a)
    elif a.command == "validate":
        cfg = load_config(a.config); print("config OK: %d processes" % len(cfg["processes"]))
    elif a.command == "status":
        s = api(a, "GET", "/api/status")
        print("%-14s %-10s %-7s %6s %9s %8s %9s" % ("NAME", "STATUS", "PID", "CPU%", "MEM(MB)", "UPTIME", "RESTARTS"))
        for p in s["processes"]:
            print("%-14s %-10s %-7s %6.1f %9.1f %7ds %5d/%d" % (p["name"], p["status"], p["pid"] or "-", p["cpu"], p["rss_mb"], p["uptime"], p["streak"], p["max_retries"]))
    else:
        if not a.name:
            sys.exit("give a process name, e.g.: python3 watchdog.py %s stable-service" % a.command)
        r = api(a, "POST", "/api/proc/%s/%s" % (urllib.parse.quote(a.name), a.command))
        print(r.get("message", r))


if __name__ == "__main__":
    main()
