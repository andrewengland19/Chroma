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


def test_start_reports_immediate_failure(sc, autoreap, capsys, monkeypatch, tmp_path):
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
    assert capsys.readouterr().err == "usage: showctl.py {start|stop|restart|status|refresh}\n"
