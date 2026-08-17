#!/usr/bin/env python3
# =============================================================================
# hub/showctl.py
# Start / stop / status the headless CHROMA hub engine as a background process.
# Wired to the `lite` CLI as `lite show start|stop|status|restart`, but also
# usable directly:  ./.venv/bin/python showctl.py start
#
# PID  → ~/.chroma/hub.pid      LOG → ~/.chroma/hub.log
# Idempotent: `start` is a no-op if already running; `stop` is safe if stopped.
# =============================================================================

import json
import os
import signal
import subprocess
import sys
import time

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HUB_DIR, ".venv", "bin", "python")
ENGINE = os.path.join(HUB_DIR, "engine.py")

CHROMA_DIR = os.path.expanduser("~/.chroma")
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
    if not _alive(proc.pid):
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


USAGE = "usage: showctl.py {start|stop|restart|status}"


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        sys.exit(start())
    if cmd == "stop":
        sys.exit(stop())
    if cmd == "status":
        sys.exit(status())
    if cmd == "restart":
        stop()
        sys.exit(start())
    print(USAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
