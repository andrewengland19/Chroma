# M3 through chromad: fixtures/groups/banks ops, discovery, Test, Room + health,
# persistence across restarts, and the AUTO path left untouched.

import asyncio
import os
import tempfile
import time

import pytest

from conftest import assert_valid


async def wait_for(pred, timeout=5.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


async def start(home_sock=None):
    from daemon import Chromad
    sock = home_sock or os.path.join(tempfile.mkdtemp(prefix="cx"), "chromad.sock")
    d = Chromad(asyncio.get_running_loop(), fake=True, socket_path=sock)
    task = asyncio.create_task(d.run())
    assert await wait_for(lambda: os.path.exists(sock))
    assert await wait_for(lambda: d.store.active("lifx") and d.virtual.current and d.engine._last_per_light)
    return d, task


async def stop(d, task):
    d.request_stop()
    await asyncio.wait_for(task, timeout=5)


@pytest.fixture
async def daemon():
    d, task = await start()
    yield d
    await stop(d, task)


async def op(d, name, **kw):
    return await d.handle({"op": name, **kw})


async def test_first_start_imports_the_discovered_bulbs(daemon):
    res = await op(daemon, "fixtures.list")
    assert_valid("fixtures", {k: res[k] for k in ("v", "rev", "fixtures", "groups", "banks")})
    labels = [f["label"] for f in res["fixtures"]]
    assert labels[:5] == ["TV Left", "TV Right", "Couch Left", "Right couch", "Washer"]
    assert {"Bright Light", "Cedar Chest", "Desk 2"} <= set(labels)
    assert labels[-1] == "Virtual Light 1"
    assert res["online"]["tv-left"] is True and res["online"]["bright-light"] is False
    st = daemon.state.build()
    assert_valid("state", st)
    assert st["health"]["lifx"] == {"found": 5, "total": 5}
    assert st["fixtures_rev"] == res["rev"]
    assert [c["group_id"] for c in st["channels"][:8]] == ["lr-all", "bed", "counter", "playbill", "lr-l",
                                                           "lr-r", "accent", "fx"]


async def test_virtual_fixture_appears_in_the_room_with_an_art_colour(daemon):
    st = daemon.state.build()
    v = [r for r in st["room"] if r["fixture_id"] == "virtual-light-1"]
    assert v and v[0]["hex"] and v[0]["online"]
    res = await op(daemon, "fixtures.upsert", fixture={"label": "Shelf", "type": "virtual", "status": "active",
                                                       "class": "fast", "x": 0.9, "y": 0.9})
    assert res["fixture"]["id"] == "shelf"
    daemon.engine.bus.publish("colors_pushed")            # next paint colours it too
    assert await wait_for(lambda: daemon.virtual.hex_for("shelf"))
    assert any(r["fixture_id"] == "shelf" for r in daemon.state.build()["room"])


async def test_discover_is_idempotent(daemon):
    res = await op(daemon, "fixtures.discover")
    assert res["result"]["found"] == 8 and res["result"]["added"] == []
    assert len([f for f in res["fixtures"] if f["type"] == "lifx"]) == 8


async def test_test_blinks_the_right_bulb_and_refuses_what_it_cannot_drive(daemon):
    from ipc import IpcError
    bulbs = {b.label: b for b in daemon.fake_bulbs}
    await op(daemon, "fixtures.test", fixture_id="couch-left")
    assert [c[0] for c in bulbs["Couch Left"].calls if c[0] == "set_waveform"] == ["set_waveform"]
    assert not [c for c in bulbs["TV Left"].calls if c[0] == "set_waveform"]
    n = len(daemon.virtual.frames)
    await op(daemon, "fixtures.test", fixture_id="virtual-light-1")
    assert await wait_for(lambda: len(daemon.virtual.frames) >= n + 4)
    for fid, msg in (("counter-strip", "planned"), ("bright-light", "not added"), ("ghost", "no fixture")):
        with pytest.raises(IpcError, match=msg):
            await op(daemon, "fixtures.test", fixture_id=fid)


async def test_test_reports_an_unreachable_or_switched_off_bulb(daemon):
    from ipc import IpcError
    tv = next(b for b in daemon.fake_bulbs if b.label == "TV Left")
    tv.fail = True
    with pytest.raises(IpcError, match="could not reach TV Left"):
        await op(daemon, "fixtures.test", fixture_id="tv-left")
    tv.fail, tv.power = False, 0
    with pytest.raises(IpcError, match="TV Left is switched off"):
        await op(daemon, "fixtures.test", fixture_id="tv-left")
    assert not [c for c in tv.calls if c[0] in ("set_waveform", "set_power")]


async def test_test_toast_says_the_bulb_is_fake_in_fake_mode(daemon):
    toasts = []
    daemon.rig.toast = lambda lv, t: toasts.append((lv, t))
    await op(daemon, "fixtures.test", fixture_id="tv-left")
    assert toasts == [("info", "Blinked TV Left (fake bulb — no real light changes)")]
    tv = next(b for b in daemon.fake_bulbs if b.label == "TV Left")
    assert [c[5] for c in tv.calls if c[0] == "set_waveform"] == [False]     # one acked waveform


async def test_discover_refreshes_the_ip_of_a_known_bulb(daemon):
    tv = next(b for b in daemon.fake_bulbs if b.label == "TV Left")
    before = (await op(daemon, "fixtures.list"))["fixtures"]
    tv.ip = "192.168.0.131"                                  # DHCP moved it
    res = await op(daemon, "fixtures.discover")
    assert res["result"]["updated"] == ["tv-left"] and res["result"]["added"] == []
    after = {f["id"]: f for f in res["fixtures"]}
    assert after["tv-left"]["ip"] == "192.168.0.131" and after["tv-left"]["label"] == "TV Left"
    assert len(res["fixtures"]) == len(before)
    assert "tv-left" in daemon.store.groups["lr-all"].members
    await op(daemon, "fixtures.test", fixture_id="tv-left")  # Test follows the new address
    assert [c for c in tv.calls if c[0] == "set_waveform"]


async def test_safety_limits_are_enforced_on_edit(daemon):
    from ipc import IpcError
    g = (await op(daemon, "groups.list"))["groups"]
    pb = next(x for x in g if x["id"] == "playbill")
    with pytest.raises(IpcError, match="at most 3 Hz"):
        await op(daemon, "groups.upsert", group={**pb, "max_flash_hz": 4})
    res = await op(daemon, "groups.upsert", group={**pb, "max_level": 0.5, "max_flash_hz": 1.5})
    assert res["group"]["max_level"] == 0.5
    assert daemon.store.limits("playbill-01") == (0.5, 1.5)


async def test_banks_drive_the_console_channels(daemon):
    res = await op(daemon, "banks.set", bank="C", channel=3, group_id="tv")
    assert res["banks"]["C"][2] == "tv"
    st = daemon.state.build()
    assert next(c for c in st["channels"] if c["bank"] == "C" and c["ch"] == 3)["group_id"] == "tv"
    await op(daemon, "groups.remove", group_id="tv")
    st = daemon.state.build()
    assert st["channels"][8]["group_id"] is None and st["channels"][18]["group_id"] is None


async def test_moving_a_lifx_fixture_moves_it_for_the_art_painter_too(daemon):
    fx = next(f for f in (await op(daemon, "fixtures.list"))["fixtures"] if f["id"] == "washer")
    await op(daemon, "fixtures.upsert", fixture={**fx, "x": 0.11, "y": 0.22})
    assert daemon.engine.layout.pos_for("Washer") == (0.11, 0.22)


async def test_edits_persist_across_a_chromad_restart():
    d, task = await start()
    sock = d.ipc.path
    await op(d, "fixtures.upsert", fixture={"label": "Shelf", "type": "virtual", "status": "active",
                                            "class": "fast", "x": 0.9, "y": 0.9})
    await op(d, "banks.set", bank="C", channel=1, group_id="fx")
    rev = d.store.rev
    await stop(d, task)
    d2, task2 = await start(sock)
    try:
        assert "shelf" in d2.store.fixtures and d2.store.banks["C"][0] == "fx" and d2.store.rev == rev
        assert d2.store.fresh is False
        assert d2.state.build()["channels"][16]["group_id"] == "fx"
    finally:
        await stop(d2, task2)


async def test_auto_painting_path_is_unchanged_by_the_rig(daemon):
    """LIFX bulbs still get exactly the engine's per-bulb colours via push_aligned —
    the console's LifxOutput never pushes in AUTO."""
    eng = daemon.engine
    bulbs = eng.lifx.fake_bulbs
    for b in bulbs:
        b.calls.clear()
    await eng.refresh()
    assert [b.colors_set() for b in bulbs] == [[tuple(h)] for h in eng._last_hsbk]
    assert all(c[0] == "set_color" and c[2] == eng.cfg.transition_ms for b in bulbs for c in b.calls)


async def test_unknown_rig_op_and_bad_args(daemon):
    from ipc import IpcError
    with pytest.raises(IpcError, match="unknown op"):
        await op(daemon, "fixtures.explode")
    with pytest.raises(IpcError, match="fixture must be an object"):
        await op(daemon, "fixtures.upsert", fixture=None)


async def test_renamed_lifx_fixture_stays_linked_to_its_bulb(daemon):
    fx = next(f for f in (await op(daemon, "fixtures.list"))["fixtures"] if f["id"] == "right-couch")
    await op(daemon, "fixtures.upsert", fixture={**fx, "label": "Couch R"})
    st = daemon.state.build()
    r = next(x for x in st["room"] if x["fixture_id"] == "right-couch")
    assert r["label"] == "Couch R" and r["hex"] and r["online"]
    assert st["health"]["lifx"] == {"found": 5, "total": 5}
    await op(daemon, "fixtures.upsert", fixture={**fx, "label": "Couch R", "x": 0.7, "y": 0.8})
    assert daemon.engine.layout.pos_for("Right couch") == (0.7, 0.8)      # the bulb's own label


async def test_set_limits_and_swap_ops(daemon):
    from ipc import IpcError
    res = await op(daemon, "groups.set_limits", group_id="accent", max_level=0.3)
    assert res["group"]["max_level"] == 0.3 and res["group"]["members"] == ["washer"]
    with pytest.raises(IpcError, match="at most 3 Hz"):
        await op(daemon, "groups.set_limits", group_id="accent", max_flash_hz=6)
    res = await op(daemon, "banks.swap", **{"from": {"bank": "A", "channel": 1}, "to": {"bank": "B", "channel": 1}})
    assert res["banks"]["A"][0] == "tv" and res["banks"]["B"][0] == "lr-all"


async def test_import_is_retried_when_the_rig_has_no_bulbs():
    from fixtures import FixtureStore
    from paths import chroma_dir
    s = FixtureStore.load(os.path.join(chroma_dir(), "fixtures.json"))   # seeded, but no import ran
    assert not any(f.type == "lifx" for f in s.fixtures.values()) and s.fresh
    d, task = await start()                                               # fresh=False now, still imports
    try:
        assert d.store.fresh is False and len(d.store.active("lifx")) == 5
    finally:
        await stop(d, task)
