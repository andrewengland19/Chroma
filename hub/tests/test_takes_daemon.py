# M5 through chromad (fake devices): record a performance, replay it on the SongClock,
# REPLACE / MERGE / OVERDUB, nudge, track change, the take ops and restart survival.
#
# The ±20 ms AC runs on a fake clock: nothing moves until the test advances it, and the
# output scheduler and replay loop are stepped by hand (`scheduler.step`, `takes.tick`).

import asyncio
import json
import os
import tempfile

import pytest

from conftest import assert_valid
from outputs.base import HSB
from testing.clock import FakeClock
from test_rig_ops import op, start, stop

VL = "virtual-light-1"           # on channel A8 (FX) in the default rig


def fake_daemon(**kw):
    from daemon import Chromad
    fc = FakeClock()
    sock = os.path.join(tempfile.mkdtemp(prefix="cx"), "chromad.sock")
    d = Chromad(asyncio.get_running_loop(), fake=True, clock=fc, socket_path=sock, **kw)
    d.engine.provider = d.fake_provider          # what engine.run() would connect
    d.fake_provider.set_index(0)                 # "Older", playing from 0
    d.poll_now()
    d.engine.enabled = False                     # BOARD
    d._ensure_board()
    return d, fc


def advance(d, fc, ms, step=1.0):
    """Move time forward in `step` ms slices, running the replay loop and the scheduler."""
    t = 0.0
    while t < ms:
        dt = min(step, ms - t)
        fc.advance_ms(dt)
        t += dt
        d.takes.tick()
        d.scheduler.step()


def vl_frames(d, since=0):
    return [(t, h) for t, fid, h, _f in list(d.virtual.frames)[since:] if fid == VL]


# Fader 8 moves and FLASH 8 presses, irregularly spaced (ms after REC), slower than 3 Hz.
SCRIPT = [(120, {"intent": "fader", "ch": 8, "v": 0.20}), (410, {"intent": "fader", "ch": 8, "v": 0.45}),
          (777, {"intent": "fader", "ch": 8, "v": 0.70}), (1333, {"intent": "flash", "ch": 8, "down": True}),
          (1601, {"intent": "flash", "ch": 8, "down": False}), (2250, {"intent": "fader", "ch": 8, "v": 0.30}),
          (2999, {"intent": "fader", "ch": 8, "v": 0.95}), (3500, {"intent": "fader", "ch": "master", "v": 0.5}),
          (4100, {"intent": "flash", "ch": 8, "down": True}), (4370, {"intent": "flash", "ch": 8, "down": False}),
          (5005, {"intent": "fader", "ch": 8, "v": 0.10})]


async def record_script(d, fc):
    res = await op(d, "rec.start")
    tid = res["take_id"]
    t = 0
    for at, intent in SCRIPT:
        advance(d, fc, at - t)
        t = at
        await d.apply_intent(dict(intent), "ui")
        d.scheduler.step()
    advance(d, fc, 400)
    assert (await op(d, "rec.stop"))["take_id"] == tid
    return tid


async def test_a_recorded_take_validates_and_replays_within_20ms_on_the_virtual_output():
    d, fc = fake_daemon()
    advance(d, fc, 2000)                                   # song at ~2 s
    rec_since = len(d.virtual.frames)
    p0 = d.song.pos_ms()
    tid = await record_script(d, fc)
    recorded = [(round((t - (1000 + 2.0)) * 1000), h) for t, h in vl_frames(d, rec_since)]

    # the file: header + events validate; p = SongClock position at each intent
    h, events = d.takes.store.get(tid)
    assert_valid("take-header", h)
    for e in events:
        assert_valid("take-event", e)
    assert [e["p"] for e in events] == [round(p0 + at) for at, _ in SCRIPT]
    assert [e["i"] for e in events] == [i for _, i in SCRIPT]
    assert h["stats"]["events"] == len(SCRIPT) and h["track_id"] == d.fake_provider.track.hash
    assert h["init"]["channels"][7]["group_id"] == "fx" and h["sync_err_ms"] is not None

    # replay from the song's start: a different live look first, so replay must set it
    await d.apply_intent({"intent": "fader", "ch": 8, "v": 0.6}, "ui")
    await d.apply_intent({"intent": "fader", "ch": "master", "v": 1.0}, "ui")
    d.scheduler.step()
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "play.start", take_id=tid)
    play_since = len(d.virtual.frames)
    play_mono0 = fc.monotonic()
    advance(d, fc, 2000 + 6000)
    replayed = [(round((t - play_mono0) * 1000) - 2000, h) for t, h in vl_frames(d, play_since)]

    # same light values in the same order, each within ±20 ms of the recorded time
    rec_vals = [(t, round(h.b, 3)) for t, h in recorded]
    rep_vals = [(t, round(h.b, 3)) for t, h in replayed if t >= -1]
    assert [v for _, v in rep_vals[-len(rec_vals):]] == [v for _, v in rec_vals]
    for (tr, _), (tp, _) in zip(rec_vals, rep_vals[-len(rec_vals):]):
        assert abs(tp - tr) <= 20, (tr, tp)
    assert d.state.build()["play"]["state"] == "playing"
    assert_valid("state", d.state.build())
    await d.shutdown()


async def test_nudge_shifts_playback_later():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    await op(d, "take.nudge", take_id=tid, ms=200)
    assert d.takes.store.get(tid)[0]["nudge_ms"] == 200
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "play.start", take_id=tid)
    since = len(d.virtual.frames)
    m0 = fc.monotonic()
    advance(d, fc, 1500)
    first = [(round((t - m0) * 1000), round(h.b, 2)) for t, h in vl_frames(d, since)]
    hit = next(t for t, b in first if b == 0.2)            # fader 0.20 recorded at 120 ms
    assert 315 <= hit <= 325
    await d.shutdown()


async def test_track_change_stops_replay_and_a_seek_rebuilds_the_look():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "play.start", take_id=tid)
    advance(d, fc, 3200)
    assert d.console.channel("A", 8).level == 0.95
    await op(d, "play.seek", pos_ms=200)                   # back before the 0.45 move (a >= 1.5 s jump)
    assert d.console.channel("A", 8).level == 0.20 and d.console.master == 1.0
    d.fake_provider.skip(1)
    d.poll_now()
    assert d.takes.play_take is None and d.state.build()["play"]["state"] == "idle"
    with pytest.raises(Exception, match="play that song"):
        await op(d, "play.start", take_id=tid)            # a take only plays with its song
    await d.shutdown()


async def test_rec_refuses_without_a_playing_track_and_arm_starts_on_play():
    d, fc = fake_daemon()
    d.fake_provider.pause()
    d.poll_now()
    with pytest.raises(Exception, match="nothing is playing"):
        await op(d, "rec.start")
    await op(d, "rec.arm")
    assert d.state.build()["rec"]["state"] == "armed"
    d.fake_provider.play()
    d.poll_now()
    await d.takes.run_once()                               # the loop starts an armed take
    assert d.state.build()["rec"]["state"] == "recording"
    await d.apply_intent({"intent": "fader", "ch": 1, "v": 0.3}, "ui")
    advance(d, fc, 100)
    st = d.state.build()
    assert st["rec"]["events"] == 1 and st["rec"]["groups_touched"] == 1
    assert st["activity"]["source"] == "rec" and st["activity"]["lanes"][0]["group_id"] == "lr-all"
    assert_valid("state", st)
    d.fake_provider.skip(1)                                # track change: saved, not lost
    d.poll_now()
    assert d.takes.rec is None and len(d.takes.store.list()) == 1
    await d.shutdown()


async def test_replay_source_is_not_recorded_and_overdub_punches_in_live():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    old = d.takes.store.get(tid)[1]
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "rec.start", take_id=tid, mode="overdub")
    assert d.takes.play_take is not None                   # OVERDUB plays the old take
    advance(d, fc, 300)
    await d.apply_intent({"intent": "fader", "ch": 8, "v": 0.55}, "ui")   # live at ~300 ms
    advance(d, fc, 200)
    assert d.console.channel("A", 8).level == 0.55         # old 0.45 at 410 ms is overridden
    advance(d, fc, 600)
    assert d.console.channel("A", 8).level == 0.55         # 0.70 at 777 ms is inside the touch span
    advance(d, fc, 1500)
    assert d.console.channel("A", 8).level == 0.30         # touch over: the old take plays again (2250 ms)
    await op(d, "rec.stop")
    events = d.takes.store.get(tid)[1]
    vals = [e["v"] for e in events if e.get("a") == "level" and e["c"] == 8]
    assert vals == [0.2, 0.55, 0.3, 0.95, 0.1]             # 0.45 and 0.70 punched out
    assert len(events) == len(old) - 1                     # no replay echoes recorded
    await d.shutdown()


async def test_merge_and_replace_into_an_existing_take():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    n = len(d.takes.store.get(tid)[1])
    await op(d, "rec.start", take_id=tid, mode="merge")
    assert d.takes.play_take is None                       # MERGE does not play the old take
    await d.apply_intent({"intent": "blackout", "on": True}, "ui")
    await op(d, "rec.stop")
    assert len(d.takes.store.get(tid)[1]) == n + 1
    await op(d, "rec.start", take_id=tid, mode="replace")
    await d.apply_intent({"intent": "blackout", "on": False}, "ui")
    await op(d, "rec.stop")
    assert [e["a"] for e in d.takes.store.get(tid)[1]] == ["blackout"]
    await d.shutdown()


# ---- through a running chromad (real clock) ----------------------------------------------

async def test_take_ops_and_a_take_survives_a_restart():
    d, task = await start()
    home_sock = d.ipc.path
    await d.handle({"op": "intent", "intent": {"intent": "who", "who": "board"}})
    res = await op(d, "rec.start")
    tid = res["take_id"]
    await d.handle({"op": "intent", "intent": {"intent": "fader", "ch": 3, "v": 0.6}})
    await asyncio.sleep(0.05)
    await op(d, "rec.stop")
    assert d.state.build()["takes_rev"] >= 1
    lst = (await op(d, "takes.list"))["takes"]
    assert lst[0]["id"] == tid and lst[0]["events"] == 1 and lst[0]["groups_used"] == ["counter"]
    await d.handle({"op": "takes.rename", "take_id": tid, "name": "Older — final"})
    await op(d, "takes.set_flag", take_id=tid, flag="training", value=False)
    await op(d, "takes.marker.add", take_id=tid, p_ms=72000, label="DROP")
    await op(d, "takes.marker.add", take_id=tid, p_ms=30000, label="VERSE")
    await op(d, "takes.marker.remove", take_id=tid, p_ms=72000)
    exp = await op(d, "takes.export", take_id=tid)
    first = json.loads(exp["jsonl"].splitlines()[0])
    assert first["name"] == "Older — final" and first["markers"] == [{"p": 30000, "label": "VERSE"}]
    assert first["training"] is False and exp["filename"].startswith(tid)
    dup = (await op(d, "takes.duplicate", take_id=tid))["take_id"]
    await op(d, "takes.delete", take_id=dup)
    for bad in ({"op": "takes.rename", "take_id": tid, "name": ""}, {"op": "takes.get", "take_id": "nope"},
                {"op": "take.nudge", "take_id": tid, "ms": 5000}, {"op": "rec.start", "mode": "punch"}):
        with pytest.raises(Exception):
            await d.handle(bad)
    await d.handle({"op": "intent", "intent": {"intent": "who", "who": "auto"}})   # the helper waits for art
    await asyncio.sleep(0.2)
    await stop(d, task)
    d2, task2 = await start(home_sock)
    try:
        lst = (await op(d2, "takes.list"))["takes"]
        assert [t["id"] for t in lst] == [tid] and lst[0]["name"] == "Older — final"
        got = await op(d2, "takes.get", take_id=tid)
        assert_valid("take-header", got["header"])
        assert len(got["events"]) == 1
    finally:
        await stop(d2, task2)


async def test_rec_switches_to_board_and_stopping_the_engine_saves_a_recording():
    d, task = await start()
    assert d.engine.enabled                                # AUTO
    await op(d, "rec.start")
    assert not d.engine.enabled                            # REC drives the lights: BOARD
    await d.handle({"op": "intent", "intent": {"intent": "fader", "ch": 1, "v": 0.4}})
    await stop(d, task)
    assert len(d.takes.store.list()) == 1 and d.takes.store.list()[0]["events"] == 1


async def test_real_time_replay_through_the_running_loop_stays_within_20ms():
    """The asyncio replay loop (not a hand-stepped one) on the real clock."""
    d, task = await start()
    try:
        await op(d, "rec.start")
        moves = [0.15, 0.35, 0.55, 0.75, 0.25, 0.65]
        t_rec = []
        for v in moves:
            await asyncio.sleep(0.23)
            await d.handle({"op": "intent", "intent": {"intent": "fader", "ch": 8, "v": v}})
            t_rec.append(d.song.pos_ms())
        tid = (await op(d, "rec.stop"))["take_id"]
        evs = d.takes.store.get(tid)[1]
        d.fake_provider.seek(evs[0]["p"] - 500)
        d.poll_now()
        since = len(d.virtual.frames)
        await op(d, "play.start", take_id=tid)
        t_play = d.clock.monotonic()
        await asyncio.sleep(2.0)
        frames = [(t, round(h.b, 2)) for t, fid, h, _f in list(d.virtual.frames)[since:]
                  if fid == VL and t >= t_play + 0.3]           # the first event is due 0.5 s in
        errs = []
        k = 0
        for e in evs:
            k = next(j for j in range(k, len(frames)) if frames[j][1] == round(e["v"], 2))
            hit = frames[k][0]
            song_at = d.song.pos_ms() - (d.clock.monotonic() - hit) * 1000.0
            errs.append(song_at - e["p"])
        assert max(abs(x) for x in errs) <= 20, errs
    finally:
        await d.handle({"op": "play.stop"})
        await d.handle({"op": "intent", "intent": {"intent": "who", "who": "auto"}})
        await stop(d, task)


# ---- code-review regressions ---------------------------------------------------------------

async def test_overdub_keeps_a_live_flash_and_releases_only_replayed_ones():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)                       # old take flashes A8 at 1333..1601 ms
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "rec.start", take_id=tid, mode="overdub")
    await d.apply_intent({"intent": "flash", "ch": 1, "down": True}, "ui")    # live, not in the old take
    assert d.console.channel("A", 1).flash                 # not released by the overdub logic
    advance(d, fc, 1400)
    assert d.console.channel("A", 8).flash                 # replay holds A8 down
    await d.apply_intent({"intent": "fader", "ch": 8, "v": 0.4}, "ui")       # live takes A8
    assert not d.console.channel("A", 8).flash             # the replayed flash lets go
    await d.apply_intent({"intent": "flash", "ch": 1, "down": False}, "ui")
    await op(d, "rec.stop")
    await d.shutdown()


async def test_recording_into_a_nudged_take_is_stored_in_take_time():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    await op(d, "take.nudge", take_id=tid, ms=200)
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "rec.start", take_id=tid, mode="merge")
    advance(d, fc, 3000)
    await d.apply_intent({"intent": "blackout", "on": True}, "ui")
    await op(d, "rec.stop")
    ev = next(e for e in d.takes.store.get(tid)[1] if e.get("a") == "blackout")
    assert ev["p"] == 2800                                 # heard at 3.0 s = take time 2.8 s
    await d.shutdown()


async def test_merge_rec_stops_a_running_playback_and_init_is_the_seeded_look():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    d.fake_provider.seek(0)
    d.poll_now()
    await op(d, "play.start", take_id=tid)
    await op(d, "rec.start", take_id=tid, mode="merge")
    assert d.takes.play_take is None and d.takes.replayer is None
    await op(d, "rec.stop")
    await d.shutdown()
    # init is taken after the AUTO -> BOARD seed (D2)
    d, fc = fake_daemon()
    d.engine.enabled = True
    d._leave_board()
    d.art_look = lambda: {VL: HSB(200, 0.8, 0.66)}
    await op(d, "rec.start")
    assert d.takes.rec.header["init"]["channels"][7]["level"] == pytest.approx(0.66)
    await op(d, "rec.stop")
    await d.shutdown()


async def test_a_refused_arm_is_not_left_armed():
    d, fc = fake_daemon()
    tid = await record_script(d, fc)
    d.fake_provider.skip(1)                                # another song plays
    d.poll_now()
    with pytest.raises(Exception, match="play that song"):
        await op(d, "rec.arm", take_id=tid, mode="merge")
    assert d.takes.armed is None and d.state.build()["rec"]["state"] == "idle"
    await d.shutdown()
