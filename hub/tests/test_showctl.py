# =============================================================================
# Characterization tests for hub/showctl.py (`lite show start|stop|status|
# restart|refresh`). Paths resolve inside the sandboxed HOME; `start` launches a
# dummy script instead of server.py, and `refresh` talks to a stub HTTP server,
# so the user's live hub is never touched.
# =============================================================================

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


@pytest.fixture
def sc(monkeypatch, tmp_path):
    import showctl
    assert showctl.PID_FILE.startswith(os.environ["HOME"])
    yield showctl
    pid = showctl._running_pid()
    if pid and pid != os.getpid():
        showctl.stop()


@pytest.fixture
def autoreap():
    """Real `lite show stop` runs in a different process from `start`, so the hub
    is never its zombie child. Auto-reap here so os.kill(pid, 0) sees the same."""
    import signal
    old = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    yield
    signal.signal(signal.SIGCHLD, old)


def dummy_engine(tmp_path, body="import time\ntime.sleep(30)\n"):
    p = tmp_path / "dummy_engine.py"
    p.write_text(body)
    return str(p)


def test_status_when_stopped(sc, capsys):
    assert sc.status() == 0
    assert capsys.readouterr().out == "chroma show: stopped\n"


def test_status_when_running_reads_state(sc, capsys):
    with open(sc.PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    with open(sc.STATE_FILE, "w") as f:
        json.dump({"connected": True, "device": "Living Room", "lights": 5,
                   "track": "Sasha Alex Sloan — Older", "playing": True}, f)
    assert sc.status() == 0
    assert capsys.readouterr().out == (
        f"chroma show: running (pid {os.getpid()})\n"
        "  device: Living Room (connected)   lights: 5\n"
        "  track:  Sasha Alex Sloan — Older  (playing)\n")
    os.remove(sc.PID_FILE)


def test_stop_when_not_running(sc, capsys):
    assert sc.stop() == 0
    assert capsys.readouterr().out == "chroma show not running\n"


def test_start_stop_restart_cycle(sc, autoreap, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "ENGINE", dummy_engine(tmp_path))
    monkeypatch.setattr(sc, "VENV_PY", sys.executable)
    assert sc.start() == 0
    pid = sc._running_pid()
    assert pid and capsys.readouterr().out == f"chroma show started (pid {pid}) → tail -f {sc.LOG_FILE}\n"
    assert sc.start() == 0
    assert capsys.readouterr().out == f"chroma show already running (pid {pid}) → tail -f {sc.LOG_FILE}\n"
    assert sc.stop() == 0
    assert capsys.readouterr().out == f"chroma show stopped (pid {pid})\n"
    assert not sc._alive(pid) and not os.path.exists(sc.PID_FILE)


def test_start_reports_immediate_failure(sc, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "ENGINE", dummy_engine(tmp_path, "raise SystemExit(1)\n"))
    monkeypatch.setattr(sc, "VENV_PY", sys.executable)
    assert sc.start() == 1
    assert capsys.readouterr().err == f"chroma show failed to start — see {sc.LOG_FILE}\n"
    assert not os.path.exists(sc.PID_FILE)


def test_api_port_from_config(sc):
    assert sc._api_port() == 8765
    with open(os.path.join(sc.CHROMA_DIR, "config.json"), "w") as f:
        json.dump({"api_port": 9123}, f)
    assert sc._api_port() == 9123


def test_refresh_when_not_running(sc, capsys):
    assert sc.refresh() == 1
    assert capsys.readouterr().err == "chroma show: not running — start it with: lite show start\n"


class _Stub(BaseHTTPRequestHandler):
    reply: dict = {}

    def do_POST(self):
        assert self.path == "/refresh"
        body = json.dumps(_Stub.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_hub(sc, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sc, "_api_port", lambda: srv.server_address[1])
    with open(sc.PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    yield srv
    srv.shutdown()
    os.remove(sc.PID_FILE)


@pytest.mark.parametrize("reply,rc,out", [
    ({"ok": True, "reconnecting": True, "message": "Apple TV connection was stale — reconnecting"}, 0,
     "chroma show: Apple TV connection was stale — reconnecting; lights update once it's back (tail -f {log})\n"),
    ({"ok": True, "enabled": True, "message": "lights refreshed", "track": "Sasha Alex Sloan — Older",
      "album": "Loser - EP"}, 0,
     "chroma show: lights refreshed — Sasha Alex Sloan — Older  [Loser - EP]\n"),
    ({"ok": False, "message": "nothing playing on the Apple TV"}, 1,
     "chroma show: nothing playing on the Apple TV\n"),
])
def test_refresh_output(sc, stub_hub, capsys, reply, rc, out):
    _Stub.reply = reply
    assert sc.refresh() == rc
    assert capsys.readouterr().out == out.format(log=sc.LOG_FILE)


def test_refresh_disabled_painting_hint(sc, stub_hub, capsys):
    _Stub.reply = {"ok": True, "enabled": False, "message": "refreshed, but painting is disabled",
                   "track": "A — B", "album": ""}
    assert sc.refresh() == 0
    out = capsys.readouterr().out
    assert out.startswith("chroma show: refreshed, but painting is disabled — A — B  []\n")
    assert "painting is off, so the lights weren't touched" in out


def test_refresh_connection_refused(sc, capsys, monkeypatch):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(sc, "_api_port", lambda: port)
    with open(sc.PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    assert sc.refresh() == 1
    assert capsys.readouterr().err.startswith("chroma show: refresh failed — ")
    os.remove(sc.PID_FILE)


def test_usage(sc, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["showctl.py", "bogus"])
    with pytest.raises(SystemExit) as e:
        sc.main()
    assert e.value.code == 2
    assert capsys.readouterr().err == "usage: showctl.py [--legacy] {start|stop|restart|status|refresh}\n"


# ---- M7: the default engine is chromad (socket + pid file), --legacy is the old hub ----------

DUMMY_CHROMAD = r"""
import json, os, signal, socket, sys, threading
home = sys.argv[sys.argv.index("--home") + 1]
if os.environ.get("DUMMY_FAIL"):
    sys.exit(int(os.environ["DUMMY_FAIL"]))
state = json.loads(os.environ.get("DUMMY_REPLY", "{}"))
srv = socket.socket(socket.AF_UNIX); srv.bind(os.path.join(home, "chromad.sock")); srv.listen(4)
open(os.path.join(home, "chromad.pid"), "w").write(str(os.getpid()))
signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
def serve(c):
    f = c.makefile("rwb")
    for line in f:
        req = json.loads(line)
        out = {"id": req["id"], "ok": True}
        if req["op"] == "refresh":
            out["result"] = state
        f.write(json.dumps(out).encode() + b"\n"); f.flush()
while True:
    c, _ = srv.accept(); threading.Thread(target=serve, args=(c,), daemon=True).start()
"""


@pytest.fixture
def eng(monkeypatch, tmp_path_factory):
    """showctl pointed at a short throwaway home; chromad replaced by a dummy that listens on the socket."""
    import tempfile
    import showctl
    home = tempfile.mkdtemp(prefix="cx")
    monkeypatch.setattr(showctl, "CHROMA_DIR", home)
    monkeypatch.setattr(showctl, "PID_FILE", os.path.join(home, "hub.pid"))
    monkeypatch.setattr(showctl, "LOG_FILE", os.path.join(home, "hub.log"))
    monkeypatch.setattr(showctl, "STATE_FILE", os.path.join(home, "state.json"))
    monkeypatch.setattr(showctl, "ENGINE_PID_FILE", os.path.join(home, "chromad.pid"))
    monkeypatch.setattr(showctl, "ENGINE_LOG_FILE", os.path.join(home, "chromad.log"))
    monkeypatch.setattr(showctl, "ENGINE_SOCKET", os.path.join(home, "chromad.sock"))
    script = os.path.join(home, "dummy_chromad.py")
    with open(script, "w") as f:
        f.write(DUMMY_CHROMAD)
    monkeypatch.setenv("CHROMAD_CMD", f"{sys.executable} {script}")
    yield showctl
    pid = showctl._engine_pid()
    if pid:
        os.kill(pid, 9)


def test_engine_status_stopped(eng, capsys):
    assert eng.engine_status() == 0
    assert capsys.readouterr().out == "chroma show: stopped\n"


def test_engine_start_status_refresh_stop_restart(eng, autoreap, capsys, monkeypatch):
    monkeypatch.setenv("DUMMY_REPLY", json.dumps(
        {"ok": True, "enabled": True, "message": "lights refreshed", "track": "A — B", "album": "C"}))
    assert eng.engine_start() == 0
    pid = eng._engine_pid()
    assert pid and capsys.readouterr().out == f"chroma show started (pid {pid}) → tail -f {eng.ENGINE_LOG_FILE}\n"
    assert eng.engine_start() == 0
    assert capsys.readouterr().out == f"chroma show already running (pid {pid}) → tail -f {eng.ENGINE_LOG_FILE}\n"
    with open(os.path.join(eng.CHROMA_DIR, "state.json"), "w") as f:
        json.dump({"connected": True, "device": "Living Room", "lights": 5, "track": "A — B", "playing": False}, f)
    assert eng.engine_status() == 0
    assert capsys.readouterr().out == (
        f"chroma show: running (pid {pid})\n"
        "  device: Living Room (connected)   lights: 5\n"
        "  track:  A — B  (paused)\n")
    assert eng.engine_refresh() == 0                                   # through the socket, no HTTP
    assert capsys.readouterr().out == "chroma show: lights refreshed — A — B  [C]\n"
    assert eng.engine_stop() == 0
    assert capsys.readouterr().out == f"chroma show stopped (pid {pid})\n"
    assert not eng._alive(pid) and not os.path.exists(eng.ENGINE_PID_FILE)
    assert eng.engine_stop() == 0
    assert capsys.readouterr().out == "chroma show not running\n"


@pytest.mark.parametrize("reply,rc,out", [
    ({"ok": True, "reconnecting": True, "message": "Apple TV not connected — reconnecting now"}, 0,
     "chroma show: Apple TV not connected — reconnecting now; lights update once it's back (tail -f {log})\n"),
    ({"ok": False, "message": "nothing playing on the Apple TV"}, 1,
     "chroma show: nothing playing on the Apple TV\n"),
])
def test_engine_refresh_outputs(eng, autoreap, capsys, monkeypatch, reply, rc, out):
    monkeypatch.setenv("DUMMY_REPLY", json.dumps(reply))
    assert eng.engine_start() == 0
    capsys.readouterr()
    assert eng.engine_refresh() == rc
    assert capsys.readouterr().out == out.format(log=eng.ENGINE_LOG_FILE)


def test_engine_refresh_painting_off_hint(eng, autoreap, capsys, monkeypatch):
    monkeypatch.setenv("DUMMY_REPLY", json.dumps({"ok": True, "enabled": False, "message": "refreshed, but painting is disabled"}))
    eng.engine_start()
    capsys.readouterr()
    assert eng.engine_refresh() == 0
    assert "painting is off, so the lights weren't touched" in capsys.readouterr().out


def test_engine_refresh_when_not_running(eng, capsys):
    assert eng.engine_refresh() == 1
    assert capsys.readouterr().err == "chroma show: not running — start it with: lite show start\n"


def test_engine_start_reports_immediate_failure(eng, capsys, monkeypatch):
    monkeypatch.setenv("DUMMY_FAIL", "1")
    assert eng.engine_start() == 1
    assert capsys.readouterr().err == f"chroma show failed to start — see {eng.ENGINE_LOG_FILE}\n"


def test_engine_start_refuses_next_to_the_old_hub(eng, capsys):
    with open(eng.PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    assert eng.engine_start() == 1
    assert "the old hub (server.py) is running" in capsys.readouterr().err
    os.remove(eng.PID_FILE)


def test_engine_ignores_a_stale_pid_file(eng, capsys):
    with open(eng.ENGINE_PID_FILE, "w") as f:
        f.write(str(os.getpid()))   # alive, but not a chromad: never signalled
    assert eng._engine_pid() is None
    assert eng.engine_stop() == 0
    assert capsys.readouterr().out == "chroma show not running\n"


def test_main_routes_legacy_flag(eng, monkeypatch):
    calls = []
    monkeypatch.setattr(eng, "start", lambda: calls.append("legacy") or 0)
    monkeypatch.setattr(eng, "engine_start", lambda: calls.append("engine") or 0)
    for argv, want in ((["showctl.py", "start"], "engine"), (["showctl.py", "--legacy", "start"], "legacy")):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit):
            eng.main()
        assert calls[-1] == want


def test_engine_without_a_pid_file_is_reported_not_ignored(eng, autoreap, capsys):
    """An engine from before chromad.pid existed: socket answers, no pid file."""
    assert eng.engine_start() == 0
    pid = eng._engine_pid()
    os.remove(eng.ENGINE_PID_FILE)
    capsys.readouterr()
    assert eng.engine_status() == 0
    assert "running (pid unknown" in capsys.readouterr().out
    assert eng.engine_start() == 0
    assert "already running (pid unknown)" in capsys.readouterr().out
    assert eng.engine_stop() == 1
    assert "without a pid file" in capsys.readouterr().err
    os.kill(pid, 9)
