# =============================================================================
# Characterization tests for hub/server.py (the live control plane) — including
# the user's Pass 5 routes (/agent*, /lights*, /scenes*, /tv*) and pages, which
# must keep working through every milestone. The engine is a real Engine with
# fakes (no lifespan → no Apple TV connect); lights_agent and the `tv` CLI are
# stubbed so nothing touches real devices.
# =============================================================================

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
async def api(make_engine, monkeypatch):
    import server
    eng, bulbs, prov = await make_engine()
    monkeypatch.setattr(server, "engine", eng)
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub") as c:
        yield c, eng, bulbs, prov


@pytest.fixture
def agent_stub(monkeypatch):
    import lights_agent
    calls = []

    def rec(name, result):
        def f(*a):
            calls.append((name, a))
            if isinstance(result, Exception):
                raise result
            return result
        return f
    monkeypatch.setattr(lights_agent, "run", rec("run", {"reply": "cozy", "suggested_name": "cozy",
                                                          "actions": []}))
    monkeypatch.setattr(lights_agent, "undo", rec("undo", {"restored": "cozy"}))
    monkeypatch.setattr(lights_agent, "t_save_scene", rec("save", {"saved": "x", "lights": 5}))
    monkeypatch.setattr(lights_agent, "room_view", rec("room", [{"label": "TV Left"}]))
    monkeypatch.setattr(lights_agent, "t_set_lights", rec("set", {"applied": [], "errors": []}))
    monkeypatch.setattr(lights_agent, "scenes_view", rec("scenes", [{"name": "s"}]))
    monkeypatch.setattr(lights_agent, "t_recall_scene", rec("recall", {"recalled": "s"}))
    return calls


@pytest.fixture
def tv_stub(monkeypatch):
    import server
    calls = []

    async def fake_tv(*argv):
        calls.append(argv)
        if argv == ("apps",):
            return {"ok": True, "output": "  1. Music (com.apple.TVMusic)\n  2. YouTube (com.google)\n"}
        return {"ok": True, "output": "ok"}
    monkeypatch.setattr(server, "_tv", fake_tv)
    return calls


# ---- core REST ------------------------------------------------------------------

async def test_state_config_and_layout(api):
    c, eng, *_ = api
    r = await c.get("/state")
    assert r.status_code == 200 and r.json()["lights"] == 5
    assert (await c.get("/config")).json()["mode"] == "deterministic"
    r = await c.post("/config", json={"brightness": 0.5, "nonsense": 1})
    assert r.json()["brightness"] == 0.5 and "nonsense" not in r.json()
    assert eng.cfg.brightness == 0.5
    assert (await c.get("/layout")).json()["lights"]["TV Left"] == {"x": 0.25, "y": 0.25}
    r = await c.post("/layout", json={"lights": {"TV Left": {"x": 0.1}}})
    assert r.json()["lights"]["TV Left"]["x"] == 0.1
    assert (await c.get("/keystatus")).json() == {"claude_available": False}
    assert "backend" in (await c.get("/enhancer")).json()


async def test_enabled_mode_transport(api):
    c, eng, bulbs, prov = api
    assert (await c.post("/enabled", json={"on": False})).json()["enabled"] is False
    assert eng.enabled is False
    assert (await c.post("/mode", json={"mode": "paint"})).json()["mode"] == "paint"
    r = await c.post("/transport", json={"action": "next"})
    assert r.json() == {"ok": True, "action": "next"} and prov.calls == ["next"]
    r = await c.post("/transport", json={"action": "explode"})
    assert r.status_code == 400


async def test_refresh_route_strips_state_and_publishes(api):
    c, eng, bulbs, prov = api
    q = eng.bus.subscribe()
    r = await c.post("/refresh")
    body = r.json()
    assert body["ok"] is True and body["message"] == "lights refreshed" and "state" not in body
    events = []
    while not q.empty():
        events.append(q.get_nowait()["event"])
    assert "state" in events


async def test_artwork_route(api):
    c, eng, bulbs, prov = api
    assert (await c.get("/artwork.jpg")).status_code == 404
    await eng._on_playing(await prov.now_playing(), "push")
    r = await c.get("/artwork.jpg")
    assert r.status_code == 200 and r.content == prov.track.art


async def test_stop_route(api):
    c, eng, *_ = api
    assert (await c.post("/stop")).json() == {"ok": True, "stopping": True}
    assert eng._stop.is_set()


async def test_static_pages(api):
    c, *_ = api
    for path in ("/lights.html", "/remote.html", "/style.css"):
        assert (await c.get(path)).status_code == 200, path
    r = await c.get("/", follow_redirects=False)         # the Music page is retired: "/" -> Lights
    assert r.status_code in (302, 307) and r.headers["location"] == "/lights.html"


# ---- Pass 5: agent, lights, scenes, tv (user-owned; must keep working) ------------

async def test_agent_routes(api, agent_stub):
    c, eng, *_ = api
    assert (await c.post("/agent", json={"prompt": "  "})).status_code == 400
    assert eng.enabled is True
    r = await c.post("/agent", json={"prompt": "rainy sunday"})
    assert r.json()["reply"] == "cozy"
    assert eng.enabled is False                       # painting stopped so the look sticks
    assert ("run", ("rainy sunday",)) in agent_stub
    assert (await c.post("/agent/undo")).json() == {"restored": "cozy"}
    assert (await c.post("/agent/save", json={"name": "cozy"})).json()["saved"] == "x"
    assert ("save", ({"name": "cozy"},)) in agent_stub


async def test_agent_error_is_500(api, agent_stub, monkeypatch):
    import lights_agent
    c, *_ = api

    def boom(_p):
        raise RuntimeError("no key")
    monkeypatch.setattr(lights_agent, "run", boom)
    r = await c.post("/agent", json={"prompt": "x"})
    assert r.status_code == 500 and r.json() == {"error": "no key"}


async def test_lights_and_scenes_routes(api, agent_stub):
    c, eng, *_ = api
    assert (await c.get("/lights")).json() == [{"label": "TV Left"}]
    r = await c.post("/lights/set", json={"changes": [{"target": "all", "power": "off"}]})
    assert r.json() == {"applied": [], "errors": []} and eng.enabled is False
    assert ("set", ({"changes": [{"target": "all", "power": "off"}]},)) in agent_stub
    await eng.set_enabled(True)
    assert (await c.get("/scenes")).json() == [{"name": "s"}]
    assert (await c.post("/scenes/recall", json={"name": "s"})).json() == {"recalled": "s"}
    assert eng.enabled is False
    assert ("recall", ({"name": "s"},)) in agent_stub


async def test_tv_routes(api, tv_stub):
    c, *_ = api
    assert (await c.post("/tv", json={"cmd": "play"})).json()["ok"] is True
    await c.post("/tv", json={"cmd": "skip", "arg": "30"})
    await c.post("/tv", json={"cmd": "vol", "arg": "up 3"})
    await c.post("/tv", json={"cmd": "launch", "arg": "Music"})
    assert tv_stub == [("play",), ("skip", "30"), ("vol", "up", "3"), ("launch", "Music")]
    r = await c.post("/tv", json={"cmd": "rm -rf"})
    assert r.status_code == 400
    assert (await c.get("/tv/apps")).json() == ["Music", "YouTube"]


# ---- WebSocket ------------------------------------------------------------------

def test_websocket_state_and_commands(monkeypatch):
    import engine as E
    import server
    from conftest import GOLDEN_LAYOUT, write_layout
    write_layout(GOLDEN_LAYOUT)
    eng = E.Engine(asyncio.new_event_loop(), install_signals=False)
    monkeypatch.setattr(server, "engine", eng)
    client = TestClient(server.app)          # no `with` → lifespan (real engine) never runs
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["event"] == "state" and first["mode"] == "deterministic"
        ws.send_json({"cmd": "set_mode", "mode": "paint"})
        msg = ws.receive_json()
        assert msg["event"] == "state" and msg["mode"] == "paint"
        ws.send_json({"cmd": "set_enabled", "on": False})
        msg = ws.receive_json()
        assert msg == {"event": "status", "message": "painting disabled"}
        msg = ws.receive_json()
        assert msg["event"] == "state" and msg["enabled"] is False
