#!/usr/bin/env python3
# =============================================================================
# hub/showctl.py
# Start / stop / status / refresh the headless CHROMA engine. Wired to the `lite` CLI as
# `lite show start|stop|status|restart|refresh`, but also usable directly:
#   ./.venv/bin/python showctl.py start
#
# Since M7 the engine is chromad (hub/chromad.py): start spawns it detached, stop
# signals the pid in $CHROMA_HOME/chromad.pid (so it also stops an engine the app or a
# LaunchAgent started), status/refresh talk to its Unix socket ($CHROMA_HOME/chromad.sock,
# NDJSON, hub/ipc.py). The old hub (server.py: Music page, Lights agent, TV Remote) is
# still available with `--legacy` (PID → ~/.chroma/hub.pid, LOG → ~/.chroma/hub.log);
# the two refuse to run on the same home, so only one drives the bulbs.
# Idempotent: `start` is a no-op if already running; `stop` is safe if stopped.
# =============================================================================

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HUB_DIR, ".venv", "bin", "python")
# Launch the control-plane server (Pass 3), which embeds the engine and serves
# the HTTP/WebSocket API. engine.py stays runnable standalone for headless debug.
ENGINE = os.path.join(HUB_DIR, "server.py")

CHROMA_DIR = os.path.abspath(os.path.expanduser(os.environ.get("CHROMA_HOME") or "~/.chroma"))
PID_FILE = os.path.join(CHROMA_DIR, "hub.pid")
LOG_FILE = os.path.join(CHROMA_DIR, "hub.log")
STATE_FILE = os.path.join(CHROMA_DIR, "state.json")


def _read_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = existence check, doesn't touch the process
        return True
    except OSError:
        return False


def _running_pid():
    pid = _read_pid()
    return pid if _alive(pid) else None


def _clear_pid():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def start() -> int:
    pid = _running_pid()
    if pid:
        print(f"chroma show already running (pid {pid}) → tail -f {LOG_FILE}")
        return 0
    os.makedirs(CHROMA_DIR, exist_ok=True)
    py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    log = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        [py, ENGINE],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=HUB_DIR,
    )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    time.sleep(1.0)  # give it a beat to fail loudly (bad creds, etc.)
    if proc.poll() is not None:  # exited (a dead child still answers kill(pid, 0) until reaped)
        print(f"chroma show failed to start — see {LOG_FILE}", file=sys.stderr)
        _clear_pid()
        return 1
    print(f"chroma show started (pid {proc.pid}) → tail -f {LOG_FILE}")
    return 0


def stop() -> int:
    pid = _running_pid()
    if not pid:
        print("chroma show not running")
        _clear_pid()
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):  # up to ~3s for a clean shutdown
        if not _alive(pid):
            break
        time.sleep(0.1)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    _clear_pid()
    print(f"chroma show stopped (pid {pid})")
    return 0


def status() -> int:
    pid = _running_pid()
    if not pid:
        print("chroma show: stopped")
        return 0
    print(f"chroma show: running (pid {pid})")
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        conn = "connected" if st.get("connected") else "disconnected"
        print(f"  device: {st.get('device')} ({conn})   lights: {st.get('lights')}")
        state = "playing" if st.get("playing") else "paused"
        print(f"  track:  {st.get('track')}  ({state})")
    except Exception:
        pass
    return 0


def _api_port() -> int:
    try:
        with open(os.path.join(CHROMA_DIR, "config.json")) as f:
            return int(json.load(f).get("api_port", 8765))
    except (OSError, ValueError, TypeError):
        return 8765


def refresh() -> int:
    """Ask the running hub to re-read what's playing (current album art) and
    repaint the lights now: POST /refresh on the local control plane."""
    if not _running_pid():
        print("chroma show: not running — start it with: lite show start", file=sys.stderr)
        return 1
    req = urllib.request.Request(
        f"http://127.0.0.1:{_api_port()}/refresh", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            res = json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"chroma show: refresh failed — {e}  (see {LOG_FILE})", file=sys.stderr)
        return 1
    msg = res.get("message", "done")
    if res.get("reconnecting"):
        print(f"chroma show: {msg}; lights update once it's back (tail -f {LOG_FILE})")
        return 0
    if res.get("track"):
        print(f"chroma show: {msg} — {res['track']}  [{res.get('album') or ''}]")
    else:
        print(f"chroma show: {msg}")
    if res.get("ok") and res.get("enabled") is False:
        print("  painting is off, so the lights weren't touched — turn it on in the GUI "
              "(or: curl -X POST localhost:%d/enabled -d '{\"on\":true}')" % _api_port())
    return 0 if res.get("ok") else 1


# ---- chromad (the default engine since M7) -----------------------------------------------

ENGINE_PID_FILE = os.path.join(CHROMA_DIR, "chromad.pid")
ENGINE_LOG_FILE = os.path.join(CHROMA_DIR, "chromad.log")
ENGINE_SOCKET = os.path.join(CHROMA_DIR, "chromad.sock")
CHROMAD = os.path.join(HUB_DIR, "chromad.py")


def _engine_cmd() -> list:
    """The command that runs chromad: $CHROMAD_CMD (e.g. a bundled sidecar), else the hub venv."""
    override = os.environ.get("CHROMAD_CMD", "").strip()
    if override:
        return shlex.split(override)
    return [VENV_PY if os.path.exists(VENV_PY) else sys.executable, CHROMAD]


def _engine_pid():
    """Pid of a running chromad on this home (pid file, verified against the process
    command line so a recycled pid is never signalled), else None."""
    try:
        with open(ENGINE_PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if not _alive(pid):
        return None
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return pid if "chromad" in cmd else None


def _ipc(op: str, timeout: float = 5.0) -> dict:
    """One request over the chromad socket; raises OSError / ValueError when it can't be reached."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(ENGINE_SOCKET)
        s.sendall(json.dumps({"id": 1, "op": op}).encode() + b"\n")
        buf = b""
        while True:
            while b"\n" not in buf:
                chunk = s.recv(1 << 16)
                if not chunk:
                    raise OSError("chromad closed the connection")
                buf += chunk
            line, buf = buf.split(b"\n", 1)
            msg = json.loads(line)
            if msg.get("id") == 1:
                return msg


def _engine_up() -> bool:
    try:
        return bool(_ipc("get_state", timeout=2.0).get("ok"))
    except (OSError, ValueError):
        return False


def engine_start() -> int:
    pid = _engine_pid()
    if pid:
        print(f"chroma show already running (pid {pid}) → tail -f {ENGINE_LOG_FILE}")
        return 0
    if _engine_up():   # an engine from before chromad.pid existed
        print(f"chroma show already running (pid unknown) → tail -f {ENGINE_LOG_FILE}")
        return 0
    if _running_pid():
        print("chroma show: the old hub (server.py) is running — stop it first: "
              "lite show stop  (or use --legacy)", file=sys.stderr)
        return 1
    os.makedirs(CHROMA_DIR, exist_ok=True)
    log = open(ENGINE_LOG_FILE, "a")
    proc = subprocess.Popen(
        _engine_cmd() + ["--home", CHROMA_DIR],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=HUB_DIR,
    )
    for _ in range(100):   # up to ~10 s for the socket to come up
        if proc.poll() is not None:
            break
        if _engine_up():
            print(f"chroma show started (pid {proc.pid}) → tail -f {ENGINE_LOG_FILE}")
            return 0
        time.sleep(0.1)
    if proc.poll() is None:   # slow to listen, but alive: don't leave it half-started
        print(f"chroma show started (pid {proc.pid}), not answering yet → tail -f {ENGINE_LOG_FILE}")
        return 0
    if proc.returncode == 2 or _engine_up():   # lost the lock/clash: someone else is the engine
        pid = _engine_pid()
        if pid:
            print(f"chroma show already running (pid {pid}) → tail -f {ENGINE_LOG_FILE}")
            return 0
    print(f"chroma show failed to start — see {ENGINE_LOG_FILE}", file=sys.stderr)
    return 1


def engine_stop() -> int:
    pid = _engine_pid()
    if not pid:
        if _engine_up():
            print("chroma show: an engine is running without a pid file (started by an older build); "
                  "quit it from the Chroma app or kill the chromad process", file=sys.stderr)
            return 1
        if _running_pid():
            return stop()   # only the old hub is up
        print("chroma show not running")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):   # chromad saves the console and closes its devices: allow ~10 s
        if not _alive(pid):
            break
        time.sleep(0.1)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    try:
        os.remove(ENGINE_PID_FILE)
    except OSError:
        pass
    print(f"chroma show stopped (pid {pid})")
    if _running_pid():
        stop()
    return 0


def engine_status() -> int:
    pid = _engine_pid()
    if not pid and _engine_up():
        print("chroma show: running (pid unknown: started by an older build)")
        return 0
    if not pid:
        if _running_pid():
            print("chroma show: the old hub (server.py) is running — use --legacy")
            return status()
        print("chroma show: stopped")
        return 0
    print(f"chroma show: running (pid {pid})")
    try:
        with open(os.path.join(CHROMA_DIR, "state.json")) as f:
            st = json.load(f)
        conn = "connected" if st.get("connected") else "disconnected"
        print(f"  device: {st.get('device')} ({conn})   lights: {st.get('lights')}")
        state = "playing" if st.get("playing") else "paused"
        print(f"  track:  {st.get('track')}  ({state})")
    except Exception:
        pass
    return 0


def engine_refresh() -> int:
    """Ask chromad to re-read what's playing and repaint the lights now (IPC `refresh`)."""
    if not _engine_pid() and not _engine_up():
        print("chroma show: not running — start it with: lite show start", file=sys.stderr)
        return 1
    try:
        reply = _ipc("refresh", timeout=45.0)
    except (OSError, ValueError) as e:
        print(f"chroma show: refresh failed — {e}  (see {ENGINE_LOG_FILE})", file=sys.stderr)
        return 1
    if not reply.get("ok") and "result" not in reply:
        print(f"chroma show: refresh failed — {reply.get('error', 'unknown error')}", file=sys.stderr)
        return 1
    res = reply.get("result") or {}
    msg = res.get("message", "done")
    if res.get("reconnecting"):
        print(f"chroma show: {msg}; lights update once it's back (tail -f {ENGINE_LOG_FILE})")
        return 0
    if res.get("track"):
        print(f"chroma show: {msg} — {res['track']}  [{res.get('album') or ''}]")
    else:
        print(f"chroma show: {msg}")
    if res.get("ok") and res.get("enabled") is False:
        print("  painting is off, so the lights weren't touched — click AUTO in the app "
              "(or: python3 tools/chromactl.py intent who who=auto)")
    return 0 if res.get("ok") else 1


USAGE = "usage: showctl.py [--legacy] {start|stop|restart|status|refresh}"


def main() -> None:
    args = sys.argv[1:]
    legacy = "--legacy" in args
    args = [a for a in args if a != "--legacy"]
    cmd = args[0] if args else "status"
    table = {"start": start, "stop": stop, "status": status, "refresh": refresh} if legacy else \
            {"start": engine_start, "stop": engine_stop, "status": engine_status, "refresh": engine_refresh}
    if cmd in table:
        sys.exit(table[cmd]())
    if cmd == "restart":
        table["stop"]()
        sys.exit(table["start"]())
    print(USAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
