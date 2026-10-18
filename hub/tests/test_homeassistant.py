# Home Assistant connector against an in-process fake HA (aiohttp server on 127.0.0.1;
# nothing leaves the machine, the Keychain is never read).
import asyncio

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

import homeassistant as H
from config import Config
from outputs.base import HSB
from outputs.ha import HaOutput

TOKEN = "good-token"


class FakeHA:
    def __init__(self):
        self.calls = []            # (path, json) of every REST service call
        self.requests = 0          # every HTTP request, any path
        self.ws_connects = 0
        self.ws_open = []
        self.drop_next_ws = False
        self.states = [
            {"entity_id": "light.tv_left", "state": "on", "attributes": {"friendly_name": "TV Left", "supported_color_modes": ["hs"]}},
            {"entity_id": "switch.fan", "state": "off", "attributes": {}},
            {"entity_id": "light.lamp", "state": "off", "attributes": {"friendly_name": "Lamp"}},
        ]
        self.app = web.Application(middlewares=[self._count])
        self.app.add_routes([web.get("/api/config", self.config), web.get("/api/states", self.get_states),
                             web.post("/api/services/light/{svc}", self.service),
                             web.get("/api/websocket", self.ws)])
        self.server = TestServer(self.app, host="127.0.0.1")

    @web.middleware
    async def _count(self, request, handler):
        self.requests += 1
        return await handler(request)

    def _authed(self, r):
        return r.headers.get("Authorization") == f"Bearer {TOKEN}"

    async def config(self, r):
        if not self._authed(r):
            return web.Response(status=401)
        return web.json_response({"version": "2026.1.0", "location_name": "Home"})

    async def get_states(self, r):
        return web.json_response(self.states) if self._authed(r) else web.Response(status=401)

    async def service(self, r):
        if not self._authed(r):
            return web.Response(status=401)
        self.calls.append((r.path, await r.json()))
        return web.json_response([])

    async def ws(self, r):
        ws = web.WebSocketResponse()
        await ws.prepare(r)
        self.ws_connects += 1
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        sub = await ws.receive_json()
        await ws.send_json({"id": sub["id"], "type": "result", "success": True})
        self.ws_open.append(ws)
        if self.drop_next_ws:
            self.drop_next_ws = False
            await ws.close()
            return ws
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                break
        return ws

    async def push_state(self, eid, state):
        ev = {"id": 1, "type": "event", "event": {"data": {"entity_id": eid, "old_state": None, "new_state": {"state": state}}}}
        for ws in self.ws_open:
            if not ws.closed:
                await ws.send_json(ev)


@pytest.fixture
async def ha():
    f = FakeHA()
    await f.server.start_server()
    yield f
    await f.server.close()


def cfg_for(ha, **kw):
    d = {"enabled": True, "url": f"http://127.0.0.1:{ha.server.port}",
         "entities": {"light.tv_left": {"fixture": "tv-left", "transition_ms": 400}, "light.lamp": "lamp"}}
    d.update(kw)
    return H.HaConfig.from_dict(d)


async def make(ha, token=TOKEN, **kw):
    c = H.HaClient(cfg_for(ha, **kw), token=token, token_loader=lambda: None)
    return c


# ---- config -------------------------------------------------------------------------

def test_default_config_is_disabled_and_roundtrips_through_config_json():
    assert H.HaConfig.from_dict(None).enabled is False
    assert Config().ha == {}
    c = Config()
    c.ha = {"enabled": False, "url": "http://homeassistant.local:8123/", "entities": {"light.a": "a"}}
    c.save()
    loaded = Config.load()
    assert loaded.ha["entities"] == {"light.a": "a"}
    assert H.HaConfig.from_dict(loaded.ha).url == "http://homeassistant.local:8123"


@pytest.mark.parametrize("bad", [
    {"url": "homeassistant.local:8123"}, {"url": "ftp://x"},
    {"entities": {"switch.fan": "x"}}, {"entities": {"light.a": {}}},
    {"entities": {"light.a": {"fixture": "a", "transition_ms": -1}}},
    {"max_brightness": 2}, {"default_transition_ms": "fast"},
])
def test_bad_config_rejected(bad):
    with pytest.raises(H.HaConfigError):
        H.HaConfig.from_dict(bad)


def test_strobe_interval_cannot_be_lowered_below_3hz():
    assert H.HaConfig.from_dict({"min_interval_ms": 10}).min_interval_ms >= 340


def test_token_is_never_part_of_config():
    assert "token" not in H.HaConfig().to_dict()


# ---- disabled: nothing is sent ---------------------------------------------------------

async def test_disabled_sends_nothing(ha):
    c = await make(ha, enabled=False)
    assert (await c.health())["state"] == "disabled"
    for call in (c.list_lights(), c.turn_on("light.tv_left", 0.5), c.turn_off("light.tv_left")):
        with pytest.raises(H.HaDisabled):
            await call
    with pytest.raises(H.HaDisabled):
        c.subscribe_state_changes(lambda *a: None)
    out = HaOutput(c)
    out.push(type("F", (), {"id": "tv-left"})(), HSB(10, 1, 1))
    assert out.pending == {} and await out.flush() == 0
    await c.close()
    assert ha.requests == 0 and ha.ws_connects == 0 and c._session is None


# ---- health / auth ----------------------------------------------------------------------

async def test_health_ok_bad_token_no_token_unreachable(ha):
    c = await make(ha)
    h = await c.health()
    assert h["ok"] and h["version"] == "2026.1.0"
    await c.close()

    c = await make(ha, token="nope")
    h = await c.health()
    assert (h["ok"], h["state"]) == (False, "auth_failed") and "nope" not in str(h)
    await c.close()

    c = await make(ha, token=None)
    assert (await c.health())["state"] == "no_token"
    await c.close()

    port = ha.server.port
    await ha.server.close()
    c = H.HaClient(H.HaConfig.from_dict({"enabled": True, "url": f"http://127.0.0.1:{port}", "timeout_s": 1}),
                   token=TOKEN)
    assert (await c.health())["state"] == "unreachable"
    await c.close()


# ---- listing / commands -------------------------------------------------------------------

async def test_list_lights_filters_and_marks_mapping(ha):
    c = await make(ha)
    lights = await c.list_lights()
    assert [l["entity_id"] for l in lights] == ["light.lamp", "light.tv_left"]
    assert lights[1]["mapped_to"] == "tv-left" and lights[1]["name"] == "TV Left"
    await c.close()


async def test_turn_on_call_shape(ha):
    c = await make(ha)
    await c.turn_on("light.tv_left", brightness=0.5, hs=(120.0, 0.8))
    path, body = ha.calls[-1]
    assert path == "/api/services/light/turn_on"
    assert body == {"entity_id": "light.tv_left", "brightness": 128, "hs_color": [120.0, 80.0], "transition": 0.4}
    await c.turn_on("light.lamp", brightness=0.2, transition_ms=2500)
    assert ha.calls[-1][1]["transition"] == 2.5 and ha.calls[-1][1]["transition"] != 0.4
    await c.turn_on("light.lamp", brightness=0.0)               # zero level = off, not a turn_on
    assert ha.calls[-1][0] == "/api/services/light/turn_off"
    await c.turn_off("light.tv_left")
    assert ha.calls[-1] == ("/api/services/light/turn_off", {"entity_id": "light.tv_left", "transition": 0.4})
    await c.close()


async def test_brightness_cap_and_non_light_refused(ha):
    c = await make(ha, max_brightness=0.5)
    await c.turn_on("light.lamp", brightness=1.0)
    assert ha.calls[-1][1]["brightness"] == 128
    await c.turn_on("light.lamp")                               # bare turn_on must not restore a brighter level
    assert ha.calls[-1][1]["brightness"] == 128
    n = len(ha.calls)
    with pytest.raises(H.HaError):
        await c.turn_on("switch.fan", brightness=1)
    with pytest.raises(H.HaError):
        await c.turn_off("homeassistant.stop")
    assert len(ha.calls) == n
    await c.close()


async def test_errors_raise_and_never_retry(ha):
    c = await make(ha, token="nope")
    with pytest.raises(H.HaAuthError):
        await c.turn_on("light.lamp", brightness=0.3)
    assert ha.calls == []
    await c.close()


# ---- websocket ----------------------------------------------------------------------------

async def _until(pred, t=3.0):
    async def w():
        while not pred():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(w(), t)


async def test_state_changed_subscription_filters_lights(ha):
    c = await make(ha)
    seen = []
    c.subscribe_state_changes(lambda e, o, n: seen.append((e, n["state"])))
    await asyncio.wait_for(c.connected.wait(), 3)
    await ha.push_state("switch.fan", "on")
    await ha.push_state("light.tv_left", "off")
    await _until(lambda: seen)
    assert seen == [("light.tv_left", "off")]
    await c.close()
    assert not c.connected.is_set()


async def test_websocket_bad_token_stops_without_retry(ha):
    c = await make(ha, token="nope")
    t = c.subscribe_state_changes(lambda *a: None)
    await asyncio.wait_for(t, 3)
    assert c.auth_failed and ha.ws_connects == 1 and not c.connected.is_set()
    await c.close()


async def test_websocket_reconnects_after_drop(ha):
    sleeps = []

    async def fast_sleep(d):
        sleeps.append(d)
        await asyncio.sleep(0)
    c = H.HaClient(cfg_for(ha), token=TOKEN, sleep=fast_sleep)
    ha.drop_next_ws = True
    seen = []
    c.subscribe_state_changes(lambda e, o, n: seen.append(e))
    await _until(lambda: c.reconnects >= 1 and c.connected.is_set())
    assert ha.ws_connects >= 2 and sleeps[0] == 1.0
    await ha.push_state("light.lamp", "on")
    await _until(lambda: seen)
    await c.close()


async def test_websocket_retries_while_server_down_with_backoff():
    sleeps = []

    async def fast_sleep(d):
        sleeps.append(d)
        await asyncio.sleep(0)
    cfg = H.HaConfig.from_dict({"enabled": True, "url": "http://127.0.0.1:1", "timeout_s": 1})
    c = H.HaClient(cfg, token=TOKEN, sleep=fast_sleep)
    c.subscribe_state_changes(lambda *a: None)
    await _until(lambda: len(sleeps) >= 4)
    assert sleeps[:4] == [1.0, 2.0, 4.0, 8.0]
    await c.close()


# ---- output stub --------------------------------------------------------------------------

class Fx:
    def __init__(self, id):
        self.id = id


async def test_output_maps_coalesces_and_rate_limits(ha):
    t = [100.0]
    c = await make(ha)
    out = HaOutput(c, clock=lambda: t[0])
    out.push(Fx("tv-left"), HSB(0, 1, 0.2), 0)
    out.push(Fx("tv-left"), HSB(240, 1, 0.9), 0)          # newest wins
    out.push(Fx("unmapped"), HSB(0, 0, 1), 0)               # ignored
    assert await out.flush() == 1
    assert ha.calls[-1][1]["hs_color"] == [240.0, 100.0]
    out.push(Fx("tv-left"), HSB(10, 1, 0.5), 0)
    assert await out.flush() == 0 and len(ha.calls) == 1   # inside the 3 Hz window: held
    t[0] += 0.4
    assert await out.flush() == 1 and len(ha.calls) == 2
    await c.close()


async def test_output_error_drops_frame_and_never_raises():
    c = H.HaClient(H.HaConfig.from_dict({"enabled": True, "url": "http://127.0.0.1:1", "timeout_s": 1,
                                         "entities": {"light.a": "a"}}), token=TOKEN)
    out = HaOutput(c)
    out.push(Fx("a"), HSB(0, 0, 1), 0)
    assert await out.flush() == 0 and out.pending == {}
    await c.close()


def test_chromactl_ha_status_disabled_does_not_connect(capsys):
    from tools import chromactl
    assert chromactl.main(["ha", "status"]) == 1
    assert "disabled" in capsys.readouterr().out
