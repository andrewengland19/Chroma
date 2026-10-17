# Agent tab: agent.ask / agent.undo / agent.save in chromad (hub/agentsvc.py).
# lights_agent is replaced by stand-ins, so no API key, network or bulb is used.

import threading

import pytest

from conftest import assert_valid
from test_chromad import daemon, ipc, wait_for  # noqa: F401  (daemon is a fixture)

ROOM = [{"label": "Couch Left", "group": "Living", "power": "on", "brightness": 0.4, "kelvin": 2700,
         "white": False, "hex": "#ff8800"},
        {"label": "TV Strip", "group": "Living", "power": "off", "brightness": 0.0, "kelvin": 3500,
         "white": True, "hex": "#ffd8a0"},
        {"label": "Hall", "group": "Other", "power": "unreachable"}]


@pytest.fixture
def agent(daemon, monkeypatch):  # noqa: F811
    """A daemon whose agent is allowed (a real-devices engine) with a gated fake run."""
    import lights_agent
    daemon.fake = False
    gate = threading.Event()
    calls = {"run": [], "undo": 0, "save": []}

    def run(prompt):
        calls["run"].append(prompt)
        assert gate.wait(5)
        if prompt == "explode":
            raise RuntimeError("no Anthropic API key — run `lite show setkey`")
        return {"reply": "Warm pool by the couch, TV side dark.", "suggested_name": "Rainy Evening!",
                "actions": [{"tool": "list_lights", "input": {}, "result": [{}, {}, {}]},
                            {"tool": "set_lights", "input": {"changes": []},
                             "result": {"applied": ["Couch Left: #ff8800 @0.40"], "errors": ["no bulb/group matches 'lamp'"]}}]}

    def undo():
        calls["undo"] += 1
        return {"restored": "rainy"}

    def save(args):
        calls["save"].append(args["name"])
        return {"error": f"scene {args['name']!r} already exists — pick a new name"} \
            if args["name"] == "agent.taken" else {"saved": args["name"], "lights": 1}

    monkeypatch.setattr(lights_agent, "run", run)
    monkeypatch.setattr(lights_agent, "undo", undo)
    monkeypatch.setattr(lights_agent, "t_save_scene", save)
    monkeypatch.setattr(lights_agent, "room_view", lambda: ROOM)
    return daemon, gate, calls


async def state(d):
    res, _ = await ipc(d.ipc.path, {"op": "get_state"})
    assert_valid("state", res[0]["state"])
    return res[0]["state"]


async def test_refused_on_a_fake_engine(daemon):  # noqa: F811
    res, _ = await ipc(daemon.ipc.path, {"op": "agent.ask", "prompt": "cozy"}, {"op": "agent.undo"},
                       {"op": "agent.save", "name": "x"})
    for r in res:
        assert_valid("ipc-response", r)
        assert r["ok"] is False and "Devices → Real" in r["error"]
    s = await state(daemon)
    assert s["agent"]["status"] == "idle" and s["who"] == "auto"
    assert "Devices → Real" in s["agent"]["blocked"]


async def test_ask_runs_in_the_background_and_publishes_the_result(agent):
    d, gate, calls = agent
    res, _ = await ipc(d.ipc.path, {"op": "agent.ask", "prompt": "  rainy evening  "})
    assert res[0] == {"id": 1, "ok": True}                       # answers before the run finishes
    s = await state(d)
    assert s["agent"]["status"] == "running" and s["agent"]["prompt"] == "rainy evening"
    assert await wait_for(lambda: d.engine.enabled is False)    # AUTO stops painting (BOARD)
    assert (await state(d))["who"] == "board"
    busy, _ = await ipc(d.ipc.path, {"op": "agent.ask", "prompt": "again"}, {"op": "agent.undo"})
    assert all(not r["ok"] and "still working" in r["error"] for r in busy)
    gate.set()
    assert await wait_for(lambda: d.agent.s["status"] == "done")
    a = (await state(d))["agent"]
    assert calls["run"] == ["rainy evening"]
    assert a["reply"] == "Warm pool by the couch, TV side dark."
    assert a["suggested_name"] == "rainy_evening"
    assert a["actions"] == [{"tool": "list_lights", "summary": "read 3 bulbs"},
                            {"tool": "set_lights",
                             "summary": "Couch Left: #ff8800 @0.40, ✗ no bulb/group matches 'lamp'"}]
    assert a["look"] == [{"label": "Couch Left", "power": "on", "hex": "#ff8800", "brightness": 0.4},
                         {"label": "TV Strip", "power": "off", "hex": "#ffd8a0", "brightness": 0.0},
                         {"label": "Hall", "power": "unreachable", "hex": None, "brightness": None}]
    assert a["can_undo"] is True and a["error"] is None and a["blocked"] is None


async def test_a_failed_run_reports_the_error(agent):
    d, gate, _ = agent
    gate.set()
    res, _ = await ipc(d.ipc.path, {"op": "agent.ask", "prompt": "explode"})
    assert res[0]["ok"]
    assert await wait_for(lambda: d.agent.s["status"] == "error")
    a = (await state(d))["agent"]
    assert "lite show setkey" in a["error"] and a["can_undo"] is False
    again, _ = await ipc(d.ipc.path, {"op": "agent.ask", "prompt": "cozy"})   # not stuck busy
    assert again[0]["ok"]


async def test_prompt_is_validated(agent):
    d, _, calls = agent
    res, _ = await ipc(d.ipc.path, {"op": "agent.ask", "prompt": "   "}, {"op": "agent.ask", "prompt": "x" * 501},
                       {"op": "agent.ask"})
    assert [r["ok"] for r in res] == [False, False, False]
    assert "500" in res[1]["error"] and calls["run"] == []
    assert d.agent.s["status"] == "idle" and d.engine.enabled is True


async def test_undo_only_after_a_run(agent):
    d, gate, calls = agent
    res, _ = await ipc(d.ipc.path, {"op": "agent.undo"})
    assert res[0]["error"] == "nothing to undo" and calls["undo"] == 0
    gate.set()
    await ipc(d.ipc.path, {"op": "agent.ask", "prompt": "cozy"})
    assert await wait_for(lambda: d.agent.s["status"] == "done")
    res, _ = await ipc(d.ipc.path, {"op": "agent.undo"}, {"op": "agent.undo"})
    assert res[0] == {"id": 1, "ok": True} and res[1]["error"] == "nothing to undo"
    assert calls["undo"] == 1 and (await state(d))["agent"]["can_undo"] is False


async def test_save_is_create_only_and_namespaced(agent):
    d, _, calls = agent
    res, _ = await ipc(d.ipc.path, {"op": "agent.save", "name": "rainy_evening"},
                       {"op": "agent.save", "name": "taken"},
                       {"op": "agent.save", "name": "agent.vibes1"},
                       {"op": "agent.save", "name": "Vibes"})
    assert res[0] == {"id": 1, "ok": True, "saved": "agent.rainy_evening"}
    assert "already exists" in res[1]["error"]
    assert not res[2]["ok"] and not res[3]["ok"]
    assert calls["save"] == ["agent.rainy_evening", "agent.taken"]   # bad names never reach lite's file
    assert (await state(d))["agent"]["saved_as"] == "agent.rainy_evening"
