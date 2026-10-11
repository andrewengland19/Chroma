# M1: chromad end to end — in-process with FakeProvider, and as a real subprocess
# driven by tools/chromactl.py (the AC: `chromactl watch` streams patches).

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest

from conftest import HUB, REPO, assert_valid

PY = sys.executable
CHROMAD = os.path.join(HUB, "chromad.py")
CHROMACTL = os.path.join(REPO, "tools", "chromactl.py")


def short_home():
    return tempfile.mkdtemp(prefix="cx")


async def wait_for(pred, timeout=5.0, step=0.02):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


@pytest.fixture
async def daemon():
    from daemon import Chromad
    home = short_home()
    d = Chromad(asyncio.get_running_loop(), fake=True, socket_path=os.path.join(home, "chromad.sock"))
    task = asyncio.create_task(d.run())
    assert await wait_for(lambda: os.path.exists(d.ipc.path))
    assert await wait_for(lambda: d.engine._last_per_light)          # first paint done
    yield d
    d.request_stop()
    await asyncio.wait_for(task, timeout=5)


async def ipc(path, *reqs, subscribe_events=0):
    r, w = await asyncio.open_unix_connection(path, limit=1 << 22)
    out = []
    for i, req in enumerate(reqs, 1):
        w.write(json.dumps({"id": i, **req}).encode() + b"\n")
        await w.drain()
        while True:
            m = json.loads(await asyncio.wait_for(r.readline(), 10))
            if m.get("id") == i:
                out.append(m)
                break
    events = [json.loads(await asyncio.wait_for(r.readline(), 3)) for _ in range(subscribe_events)]
    w.close()
    return out, events


async def test_state_is_schema_valid_and_reflects_the_engine(daemon):
    (res,), _ = await ipc(daemon.ipc.path, {"op": "get_state"})
    st = res["state"]
    assert_valid("state", st)
    assert st["now"]["title"] == "Older" and st["now"]["dur_ms"] == 205_000
    assert st["now"]["art_url"] == "chroma-art://current?rev=1"
    assert st["who"] == "auto" and st["art_mode"] == "det"
    assert [r["label"] for r in st["room"]] == ["TV Left", "TV Right", "Couch Left", "Right couch", "Washer"]
    assert all(r["hex"] for r in st["room"])
    assert st["health"] == {"atv": "up", "lifx": {"found": 5, "total": 5}, "board": "none"}


async def test_watch_streams_position_patches(daemon):
    (ok,), events = await ipc(daemon.ipc.path, {"op": "subscribe"}, subscribe_events=4)
    assert ok == {"id": 1, "ok": True}
    assert events[0]["ev"] == "state"
    assert_valid("ipc-event", events[0])
    positions = [events[0]["state"]["now"]["pos_ms"]] + [e["patch"]["now"]["pos_ms"] for e in events[1:]]
    assert positions == sorted(positions) and positions[-1] > positions[0]
    for e in events[1:]:
        assert_valid("ipc-event", e)


async def test_ops_art_mode_who_refresh_and_errors(daemon):
    from config import Config
    reqs = [{"op": "set_art_mode", "mode": "paint"},
            {"op": "set_art_mode", "mode": "deterministic"},
            {"op": "intent", "intent": {"intent": "who", "who": "board"}},
            {"op": "refresh"},
            {"op": "intent", "intent": {"intent": "strobe"}},
            {"op": "intent", "intent": {"intent": "fader", "ch": 1, "v": 0.5}},
            {"op": "format_disk"}]
    res, _ = await ipc(daemon.ipc.path, *reqs)
    for r in res:
        assert_valid("ipc-response", r)
    assert res[0] == {"id": 1, "ok": True}
    assert res[1]["ok"] is False and "det, paint, ai" in res[1]["error"]
    assert Config.load().mode == "paint"
    assert res[2] == {"id": 3, "ok": True} and daemon.engine.enabled is False
    assert res[3]["ok"] and res[3]["result"]["message"] == "refreshed, but painting is disabled"
    assert res[4]["error"] == "unknown intent 'strobe'"
    assert "M4" in res[5]["error"]
    assert res[6]["error"] == "unknown op 'format_disk'"
    # back to AUTO repaints at once from cached art (DECISIONS X3)
    before = len(daemon.engine.lifx.fake_bulbs[0].calls)
    res, _ = await ipc(daemon.ipc.path, {"op": "intent", "intent": {"intent": "who", "who": "auto"}})
    assert res[0]["ok"] and daemon.engine.enabled is True
    assert len(daemon.engine.lifx.fake_bulbs[0].calls) == before + 1


async def test_song_clock_follows_seek_and_track_change(daemon):
    prov = daemon.fake_provider
    prov.seek(120_000)
    await asyncio.sleep(0.05)
    assert abs(daemon.song.pos_ms() - 120_000) < 300
    prov.skip(1)
    assert await wait_for(lambda: daemon.song.track_id == prov.track.hash)
    (res,), _ = await ipc(daemon.ipc.path, {"op": "get_state"})
    assert res["state"]["now"]["title"] == "Levitating (Remix)"
    assert res["state"]["now"]["pos_ms"] < 1500


# ---- as a real process, driven by chromactl -----------------------------------------

def spawn(home, *extra):
    return subprocess.Popen([PY, CHROMAD, "--home", home, *extra], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def wait_socket(path, proc, timeout=10):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if os.path.exists(path):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return False


def ctl(home, *args, timeout=15):
    env = dict(os.environ, CHROMA_HOME=home)
    return subprocess.run([PY, CHROMACTL, *args], env=env, capture_output=True, text=True, timeout=timeout)


def test_chromactl_watch_streams_patches_from_a_fake_chromad():
    home = short_home()
    proc = spawn(home, "--fake")
    sock = os.path.join(home, "chromad.sock")
    try:
        assert wait_socket(sock, proc), proc.stdout.read() if proc.poll() is not None else "no socket"
        for _ in range(100):                          # let the first paint land
            st = json.loads(ctl(home, "state").stdout)
            if st["room"] and all(x["hex"] for x in st["room"]):
                break
            time.sleep(0.05)
        r = ctl(home, "watch", "--count", "5")
        lines = r.stdout.strip().splitlines()
        assert r.returncode == 0 and len(lines) == 5
        assert "state  Sasha Alex Sloan — Older" in lines[0]
        assert all('patch {"now":{"pos_ms":' in l for l in lines[1:])
        st = json.loads(ctl(home, "state").stdout)
        assert_valid("state", st)
        r = ctl(home, "op", "set_art_mode", "mode=ai")
        assert r.returncode == 0 and json.loads(r.stdout) == {"id": 1, "ok": True}
        r = ctl(home, "intent", '{"intent":"who","who":"board"}')
        assert r.returncode == 0
        r = ctl(home, "intent", "bank", "bank=Z")
        assert r.returncode == 1
        # a second chromad on the same home refuses to start
        second = spawn(home, "--fake")
        out, _ = second.communicate(timeout=10)
        assert second.returncode == 1 and "already running" in out
    finally:
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0, out
    assert not os.path.exists(sock)
    assert "chromad stopped" in out


def test_real_chromad_refuses_to_run_alongside_the_old_hub(tmp_path):
    home = short_home()
    fake_server = tmp_path / "server.py"            # cmdline contains server.py, like the live hub
    fake_server.write_text("import time\ntime.sleep(30)\n")
    hub = subprocess.Popen([PY, str(fake_server)])
    try:
        with open(os.path.join(home, "hub.pid"), "w") as f:
            f.write(str(hub.pid))
        # --no-lifx + no Apple TV credentials in this home: even a failed guard touches no device
        proc = spawn(home, "--no-lifx")
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise
        assert proc.returncode == 2
        assert f"the old hub is running (server.py, pid {hub.pid})" in out
    finally:
        hub.kill()
        hub.wait()
