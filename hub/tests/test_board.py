# M4 wiring through chromad (fake devices): intents -> console -> scheduler -> outputs,
# AUTO/BOARD, strobe limit and rate limits end to end, console.json persistence.

import asyncio
import json
import os
import time

import pytest

from conftest import assert_valid
from console import is_edge
from outputs.base import HSB
from test_rig_ops import op, start, stop, wait_for
from test_takes_daemon import advance, fake_daemon


@pytest.fixture
async def daemon():
    d, task = await start()
    yield d
    await stop(d, task)


async def intent(d, **kw):
    return await d.handle({"op": "intent", "intent": kw})


async def board(d):
    await d.set_who("board")
    assert d.engine.enabled is False


def bulb(d, label):
    return next(b for b in d.fake_bulbs if b.label == label)


def colors(b):
    return [c for c in b.calls if c[0] == "set_color"]


def vframes(d, since=0):
    return [f for f in list(d.virtual.frames)[since:] if f[1] == "virtual-light-1"]


# ---- AUTO / BOARD -----------------------------------------------------------------------------

async def test_switching_to_board_pushes_nothing_and_keeps_the_art_look(daemon):
    d = daemon
    tv = bulb(d, "TV Left")
    n = len(colors(tv))
    art = d.art_look()
    assert "tv-left" in art and "virtual-light-1" in art
    await board(d)
    await asyncio.sleep(0.3)
    assert len(colors(tv)) == n                                # D2: nothing pushed
    ch1 = d.console.channel("A", 1)
    assert 0 < ch1.level <= 1                                  # seeded: mean art brightness
    # the console's idea of every fixture equals what it shows (no colour jump on the first move)
    from console import resolve
    out = resolve(d.console, d._members(), d.store.limits)
    for fid, hsb in art.items():
        if fid in out:
            assert out[fid].b == pytest.approx(hsb.b, abs=1e-6) and out[fid].h == pytest.approx(hsb.h, abs=1e-6)
    assert_valid("state", d.state.build())


async def test_a_ui_fader_drives_a_real_bulb_and_a_virtual_fixture(daemon):
    d = daemon
    await board(d)
    tv, couch = bulb(d, "TV Left"), bulb(d, "Couch Left")
    n_tv, n_v = len(colors(tv)), len(d.virtual.frames)
    await intent(d, intent="fader", ch=1, v=0.2)               # lr-all: the five Living Room bulbs
    assert await wait_for(lambda: len(colors(tv)) > n_tv)
    assert colors(tv)[-1][1][2] == round(0.2 * 65535)          # brightness 0.2 x master 1
    assert len(colors(couch)) > 0
    await intent(d, intent="fader", ch=8, v=0.6)               # fx: the virtual light
    assert await wait_for(lambda: vframes(d, n_v) and vframes(d, n_v)[-1][2].b == pytest.approx(0.6))
    assert d.scheduler.latency_stats()["fast"]["max"] < 20     # intent -> virtual output
    assert "slow" in d.scheduler.latency_stats()
    room = {r["fixture_id"]: r for r in d.state.build()["room"]}
    assert room["tv-left"]["hex"] == HSB(*_hsb_of(colors(tv)[-1][1])).hex()   # Room shows the console


def _hsb_of(hsbk):
    h, s, b, _k = hsbk
    return h / 65535 * 360, s / 65535, b / 65535


async def test_level_moves_keep_the_art_colour_until_colour_is_touched(daemon):
    d = daemon
    art = d.art_look()["tv-left"]
    await board(d)
    tv = bulb(d, "TV Left")
    await intent(d, intent="fader", ch=1, v=0.5)
    assert await wait_for(lambda: colors(tv) and colors(tv)[-1][1][2] == round(0.5 * 65535))
    h, s, _b = _hsb_of(colors(tv)[-1][1])
    assert h == pytest.approx(art.h, abs=0.01) and s == pytest.approx(art.s, abs=0.001)
    await intent(d, intent="hue", v=200)
    await intent(d, intent="sat", v=1.0)
    assert await wait_for(lambda: _hsb_of(colors(tv)[-1][1])[0] == pytest.approx(200, abs=0.01))


async def test_flash_holds_and_releases_and_punches_through_blackout(daemon):
    d = daemon
    await board(d)
    tv = bulb(d, "TV Left")
    await intent(d, intent="fader", ch=1, v=0.3)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == round(0.3 * 65535))
    await intent(d, intent="flash", ch=1, down=True)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == 65535)
    await asyncio.sleep(0.4)                                    # held: still full
    assert colors(tv)[-1][1][2] == 65535
    await intent(d, intent="flash", ch=1, down=False)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == round(0.3 * 65535))
    await asyncio.sleep(0.4)                                    # strobe budget recovers
    await intent(d, intent="blackout", on=True)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == 0)
    assert not [c for c in tv.calls if c[0] == "set_power" and c[1] == 0]   # D15: never powered off
    await intent(d, intent="flash", ch=8, down=True)            # FX (virtual light) only
    assert await wait_for(lambda: d.virtual.current["virtual-light-1"].b == 1.0)
    await asyncio.sleep(0.2)
    assert colors(tv)[-1][1][2] == 0                            # the rest stays dark
    await intent(d, intent="flash", ch=8, down=False)
    assert await wait_for(lambda: d.virtual.current["virtual-light-1"].b == 0.0)


async def test_auto_faders_ride_on_the_art_and_back_to_auto_repaints(daemon):
    d = daemon
    tv = bulb(d, "TV Left")
    n = len(colors(tv))
    await intent(d, intent="fader", ch=1, v=0.9)
    await asyncio.sleep(0.2)
    assert await wait_for(lambda: len(colors(tv)) > n and colors(tv)[-1][1][2] == round(0.9 * 65535))
    assert d.console.channel("A", 1).level == 0.9 and d.engine.enabled
    await board(d)
    await intent(d, intent="fader", ch=1, v=0.1)
    assert await wait_for(lambda: len(colors(tv)) > n)
    n = len(colors(tv))
    await d.set_who("auto")
    assert await wait_for(lambda: len(colors(tv)) == n + 1)     # the art painter repaints at once
    await intent(d, intent="fader", ch=1, v=0.7)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == round(0.7 * 65535))


async def test_disabled_elsewhere_seeds_on_the_first_console_intent(daemon):
    d = daemon
    await d.engine.set_enabled(False)                           # e.g. /agent or /lights/set (D3)
    tv = bulb(d, "TV Left")
    n = len(colors(tv))
    await asyncio.sleep(0.2)
    assert len(colors(tv)) == n                                 # console pushes nothing yet
    await intent(d, intent="fader", ch=5, v=0.4)                # lr-l
    assert await wait_for(lambda: len(colors(tv)) > n)
    assert d._board_ready


async def test_an_intent_right_after_who_board_is_driven_not_seeded_over(daemon):
    d = daemon
    tv = bulb(d, "TV Left")
    n = len(colors(tv))
    await intent(d, intent="who", who="board")                  # no await of the background part
    await intent(d, intent="fader", ch=1, v=0.25)
    assert d.console.channel("A", 1).level == 0.25
    assert await wait_for(lambda: len(colors(tv)) > n and colors(tv)[-1][1][2] == round(0.25 * 65535))
    await asyncio.sleep(0.3)                                     # the queued set_who must not reseed
    assert d.console.channel("A", 1).level == 0.25 and d.engine.enabled is False


async def test_nothing_is_pushed_once_auto_resumes_elsewhere(daemon):
    d = daemon
    await board(d)
    await intent(d, intent="fader", ch=8, v=0.0)
    for i in range(12):                                          # use up the strobe budget: values get held
        await intent(d, intent="flash", ch=8, down=i % 2 == 0)
        await asyncio.sleep(0.02)
    await d.engine.set_enabled(True)                             # /agent or /lights/set
    since = len(d.virtual.frames)
    await asyncio.sleep(1.3)                                     # the hold would have expired by now
    assert vframes(d, since) == [] or all(f[2] == d.virtual.current["virtual-light-1"] for f in vframes(d, since))
    await intent(d, intent="fader", ch=8, v=0.9)                 # AUTO: rides on the art, no BOARD
    assert not d._board_ready and d._auto_ready and d.scheduler.limiter.pending == {}


# ---- safety and pacing ---------------------------------------------------------------------------

async def test_flash_hammering_is_held_to_3hz_on_every_fixture():
    # Fake clock: nothing moves until the test advances it, so load cannot change the edge count.
    d, fc = fake_daemon()
    await d.handle({"op": "intent", "intent": {"intent": "fader", "ch": 8, "v": 0.0}})
    advance(d, fc, 100)
    since = len(d.virtual.frames)
    for i in range(100):                                        # 20 Hz press/release for 2.5 s
        await d.handle({"op": "intent", "intent": {"intent": "flash", "ch": 8, "down": i % 2 == 0}})
        advance(d, fc, 25)
    advance(d, fc, 1200)
    edges, last = [], None
    for t, _fid, hsb, _f in vframes(d, since):
        if is_edge(last, hsb):
            edges.append(t)
        last = hsb
    assert len(edges) >= 8                                       # it still flashes ...
    for a in edges:                                              # ... but <= 6 edges in any second
        assert len([b for b in edges if a <= b < a + 1.0]) <= 6
    await d.shutdown()


async def test_blackout_is_not_delayed_by_the_strobe_hold(daemon):
    d = daemon
    await board(d)
    await intent(d, intent="fader", ch=8, v=0.0)
    vl = "virtual-light-1"
    for _ in range(80):                                          # flash until "off" is held while lit
        lit = d.virtual.current[vl].b > 0.5
        await intent(d, intent="flash", ch=8, down=not lit)      # toggle away from what shows
        await asyncio.sleep(0.03)
        if vl in d.scheduler.limiter.pending and d.virtual.current[vl].b > 0.5:
            break
    assert vl in d.scheduler.limiter.pending and d.virtual.current[vl].b > 0.5
    t0 = time.monotonic()
    await intent(d, intent="blackout", on=True)
    assert await wait_for(lambda: d.virtual.current["virtual-light-1"].b == 0.0, timeout=0.5)
    assert time.monotonic() - t0 < 0.1
    await intent(d, intent="flash", ch=8, down=False)
    await intent(d, intent="blackout", on=False)


async def test_lifx_gets_at_most_20_messages_a_second_and_the_newest_value(daemon):
    d = daemon
    await board(d)
    tv = bulb(d, "TV Left")
    n = len(colors(tv))
    for i in range(100):                                         # a fast drag: 100 moves in ~1 s
        await intent(d, intent="fader", ch=1, v=0.30 + 0.005 * (i % 20))
        await asyncio.sleep(0.01)
    await intent(d, intent="fader", ch=1, v=0.42)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == round(0.42 * 65535))
    sent = colors(tv)[n:]
    ts = [c[4] for c in sent]
    assert all(b - a >= 0.049 for a, b in zip(ts, ts[1:]))       # >= 50 ms apart
    assert len(sent) < 40                                        # coalesced, no backlog


async def test_group_cap_limits_console_output(daemon):
    d = daemon
    await op(d, "groups.set_limits", group_id="lr-all", max_level=0.5)
    await board(d)
    tv = bulb(d, "TV Left")
    n = len(colors(tv))
    await intent(d, intent="fader", ch=1, v=1.0)
    await asyncio.sleep(0.3)
    await intent(d, intent="flash", ch=1, down=True)
    await asyncio.sleep(0.3)
    sent = colors(tv)[n:]
    assert sent and max(c[1][2] for c in sent) == round(0.5 * 65535)   # D8: capped, flash too


# ---- intents with side effects ------------------------------------------------------------------

async def test_assign_persists_in_the_banks_and_rejects_unknown_groups(daemon):
    from ipc import IpcError
    d = daemon
    with pytest.raises(IpcError, match="no group 'nope'"):
        await intent(d, intent="assign", ch=2, group_id="nope")
    await intent(d, intent="assign", ch=2, group_id="lr-l")
    assert d.store.banks["A"][1] == "lr-l" and d.console.channel("A", 2).group_id == "lr-l"
    res = await op(d, "banks.get")
    assert res["banks"]["A"][1] == "lr-l"
    with pytest.raises(IpcError, match="touch a channel first|0-7"):
        await intent(d, intent="palette_apply", i=9)


async def test_console_survives_a_restart():
    d, task = await start()
    home_sock = d.ipc.path
    await intent(d, intent="fader", ch=3, v=0.66)
    await intent(d, intent="cue_store", i=2)
    await d.handle({"op": "cues.rename", "i": 2, "name": "Big chorus"})
    await intent(d, intent="assign", ch=3)
    await intent(d, intent="hue", v=0)
    await intent(d, intent="sat", v=1)
    await intent(d, intent="palette_store", i=0)
    await intent(d, intent="bank", bank="B")
    await stop(d, task)
    saved = json.load(open(d.console_path))
    assert_valid("console", saved)
    d2, task2 = await start(home_sock)
    try:
        st = d2.state.build()
        assert st["bank"] == "B" and st["palette"][0] == "#ff0000"
        assert st["cues"][2] == {"id": 2, "name": "Big chorus", "stored": True}
        assert d2.console.channel("A", 3).level == 0.66
        assert st["blackout"] is False and st["who"] == "auto"
    finally:
        await stop(d2, task2)


async def test_cues_rename_validates(daemon):
    from ipc import IpcError
    with pytest.raises(IpcError, match="1-24"):
        await daemon.handle({"op": "cues.rename", "i": 0, "name": "x" * 25})
    with pytest.raises(IpcError, match="0-7"):
        await daemon.handle({"op": "cues.rename", "i": 8, "name": "ok"})


async def test_corrupt_console_file_is_set_aside():
    d, task = await start()
    path, sock = d.console_path, d.ipc.path
    await stop(d, task)
    with open(path, "w") as fh:
        fh.write("{not json")
    d2, task2 = await start(sock)
    try:
        assert os.path.exists(path + ".bad") and d2.console.bank == "A"
    finally:
        await stop(d2, task2)


async def test_master_fader_changes_real_bulb_brightness(daemon):
    d = daemon
    await board(d)
    tv = bulb(d, "TV Left")
    await intent(d, intent="fader", ch=1, v=0.8)
    assert await wait_for(lambda: colors(tv) and colors(tv)[-1][1][2] == round(0.8 * 65535))
    n = len(colors(tv))
    await intent(d, intent="fader", ch="master", v=0.5)
    assert await wait_for(lambda: len(colors(tv)) > n)
    assert colors(tv)[-1][1][2] == round(0.4 * 65535)


async def test_master_alone_dims_the_art_look_in_board(daemon):
    d = daemon
    await board(d)
    tv = bulb(d, "TV Left")
    art = d.art_look()["tv-left"]
    n = len(colors(tv))
    await intent(d, intent="fader", ch="master", v=0.5)
    assert await wait_for(lambda: len(colors(tv)) > n)
    assert colors(tv)[-1][1][2] == round(art.b * 0.5 * 65535)


async def test_master_in_auto_dims_without_leaving_auto(daemon):
    d = daemon
    assert d.engine.enabled
    tv = bulb(d, "TV Left")
    art = d.art_look()["tv-left"]
    n = len(colors(tv))
    await intent(d, intent="fader", ch="master", v=0.5)
    assert d.engine.enabled is True
    assert await wait_for(lambda: len(colors(tv)) > n)
    assert colors(tv)[-1][1][2] == round(art.b * 0.5 * 65535)


async def test_auto_fader_blackout_and_flash_follow_the_bank(daemon):
    d = daemon
    tv = bulb(d, "TV Left")
    gid = d.store.banks["A"][0]
    assert gid and "tv-left" in d._members()[gid]
    n = len(colors(tv))
    await intent(d, intent="fader", ch=1, v=0.3)
    assert d.engine.enabled is True
    assert await wait_for(lambda: len(colors(tv)) > n and colors(tv)[-1][1][2] == round(0.3 * 65535))
    hue = colors(tv)[-1][1][0]
    await d.engine.refresh()                       # a repaint keeps the fader's brightness
    assert await wait_for(lambda: colors(tv)[-1][1][2] == round(0.3 * 65535))
    n = len(colors(tv))
    await intent(d, intent="blackout", on=True)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == 0)
    await intent(d, intent="blackout", on=False)
    await intent(d, intent="flash", ch=1, down=True)
    assert await wait_for(lambda: colors(tv)[-1][1][2] == 65535)
    assert d.engine.enabled is True


# ---- smooth faders ----------------------------------------------------------------------------

async def test_fader_moves_fade_but_flash_and_blackout_stay_hard(daemon):
    d = daemon
    await board(d)
    since = len(d.virtual.frames)
    await intent(d, intent="fader", ch=8, v=0.8)
    await asyncio.sleep(0.2)
    fr = vframes(d, since)
    assert fr and all(f[3] == 120 for f in fr)                # a fader value glides (SMOOTH_FADE_MS)
    await intent(d, intent="fader", ch=8, v=0.0)
    await asyncio.sleep(0.2)
    since = len(d.virtual.frames)
    await intent(d, intent="flash", ch=8, down=True)
    await asyncio.sleep(0.2)
    fr = vframes(d, since)
    assert fr and all(f[3] == 0 for f in fr)                  # the flash punches in at once
