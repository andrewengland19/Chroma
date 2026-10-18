# M6: the physical board over UDP (hub/board_link.py) driven by tools/board_sim.py.
#
# Everything runs on a fake clock: the sim "sleeps" by stepping the clock, the output scheduler,
# the replay loop and the link's timers by hand, so loss, offline and hold timing are exact.
# One test at the end talks real UDP on loopback through `board.pairing`.

import asyncio
import json
import os
import socket
import sys

import pytest

import board_link as BL
from conftest import REPO, assert_valid
from test_rig_ops import op, wait_for
from test_takes_daemon import advance, fake_daemon, vl_frames

sys.path.insert(0, os.path.join(REPO, "tools"))
from board_sim import BoardSim, Unplayable, canned_performance  # noqa: E402


class Bench:
    """A fake-clock daemon in BOARD mode with a paired key, plus sims that feed its link directly."""

    def __init__(self):
        self.d, self.fc = fake_daemon(board_port=0, board_host="127.0.0.1")
        self.key, _ = BL.create_key(self.d.board.path)
        self.d.board.key = self.key
        self.link = self.d.board

    async def sleep(self, seconds):
        advance(self.d, self.fc, seconds * 1000.0, step=2.0)
        await self.link.tick()

    def sim(self, **kw) -> BoardSim:
        kw.setdefault("seed", 1)
        s = BoardSim(self.key, self.link.receive, sleep=self.sleep, **kw)
        s.place_faders(self.faders())
        return s

    def faders(self):
        con = self.d.console
        return [c.level for c in con.channels if c.bank == con.bank] + [con.master]

    async def online(self, **kw) -> BoardSim:
        s = self.sim(**kw)
        await s.emit()                                          # the baseline
        assert self.link.status == "virtual"
        return s

    def ch(self, n, bank=None):
        return self.d.console.channel(bank or self.d.console.bank, n)


@pytest.fixture
async def bench():
    b = Bench()
    yield b
    await b.d.shutdown()


# ---- wire format and acceptance -----------------------------------------------------------------

def test_seal_puts_the_mac_last_and_unseal_checks_it():
    key = bytes(range(32))
    data = BL.seal(key, {"v": 1, "x": [1, 2]})
    assert data.endswith(b'"}') and data.count(b'"mac"') == 1 and data.rfind(b'"mac"') > data.rfind(b']')
    assert BL.unseal(key, data) == b'{"v":1,"x":[1,2]}'
    assert BL.unseal(bytes(32), data) is None
    assert BL.unseal(key, data.replace(b"[1,2]", b"[1,3]")) is None


async def test_a_simulated_packet_and_a_frame_validate_against_the_contract(bench):
    s = bench.sim()
    pkt = json.loads(s.packet())
    assert_valid("board-packet", pkt)
    await s.emit()
    frame = json.loads(bench.link.frame(bench.link.ui()))
    assert_valid("board-frame", frame)
    assert BL.unseal(bench.key, bench.link.frame(bench.link.ui())) is not None
    assert len(bench.link.frame(bench.link.ui())) <= 512


async def test_bad_mac_wrong_key_oversize_and_bad_format_are_ignored(bench):
    b, link = bench, bench.link
    s = await b.online()
    base = dict(link.stats)
    lv = b.ch(1).level
    mine = BoardSim(b.key, None, seed=2)
    mine.boot, mine.seq, mine.f[0] = s.boot, 500, 0.9
    good = mine.packet()
    other = BoardSim(bytes(32), None, seed=2)                 # right shape, wrong key
    other.boot, other.seq, other.f[0] = s.boot, 501, 0.9
    tampered = good.replace(b"0.9", b"0.1")
    junk = BL.seal(b.key, {"v": 2, "hello": 1})               # authentic but not a board packet
    huge = BL.seal(b.key, {"pad": "x" * 600})
    for data in (other.packet(), tampered, b"not json", junk, huge, b'{"mac":"0000000000000000"}'):
        assert await link.receive(data) is False
    assert link.stats["bad_mac"] - base["bad_mac"] == 4         # wrong key, tampered, "not json", short json
    assert link.stats["bad_format"] - base["bad_format"] == 1
    assert link.stats["bad_size"] - base["bad_size"] == 1
    assert b.ch(1).level == lv and link.status == "virtual"


async def test_replayed_and_reordered_seq_are_never_applied(bench):
    b, link = bench, bench.link
    s = await b.online()
    await s.fader(1, 0.30)
    await s.fader(1, 0.60)
    assert b.ch(1).level == pytest.approx(0.60)
    old = BoardSim(b.key, None)
    old.boot, old.f[0] = s.boot, 0.10
    for seq in (s.seq - 5, s.seq - 1, s.seq, 3):              # late (within 8), duplicate, long gone
        old.seq = seq - 1
        assert await link.receive(old.packet()) is False
    assert link.stats["late"] + link.stats["replay"] >= 4
    assert b.ch(1).level == pytest.approx(0.60)
    # a captured datagram sent again is a duplicate seq
    cap = s.packet()
    assert await link.receive(cap) is True
    assert await link.receive(cap) is False


async def test_a_boot_nonce_seen_before_cannot_be_replayed_after_a_restart(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.4)
    b.link._flush_boots(b.fc.monotonic())
    seen_seq = s.seq
    link2 = BL.BoardLink(b.d)                                  # chromad restarted: same home
    assert link2.key == b.key and link2._boots[s.id][str(s.boot)] == seen_seq
    s2 = BoardSim(b.key, None)
    s2.boot = s.boot
    s2.seq = seen_seq - 3
    assert link2._accept(s2.packet(), 0.0) is None             # an old capture of the same boot
    s2.seq = seen_seq + 10                                     # the real board, still running
    assert link2._accept(s2.packet(), 0.0) is not None
    s3 = BoardSim(b.key, None, boot=(s.boot + 1) % 2 ** 32)
    assert link2._accept(s3.packet(), 0.0) is not None         # a fresh boot


async def test_listener_stays_off_without_a_key():
    d, fc = fake_daemon(board_port=0, board_host="127.0.0.1")
    assert d.board.key is None and d.board.transport is None
    await d.board.start()
    assert d.board.transport is None
    assert await d.board.receive(b"{}") is False
    assert d.state.build()["board_link"] == {"paired": False, "port": 9876, "id": None}
    assert d.state.build()["health"]["board"] == "none"
    await d.shutdown()


# ---- snapshot diff -> intents ------------------------------------------------------------------------

async def test_the_first_snapshot_is_a_baseline_and_changes_nothing(bench):
    b = bench
    def look():
        return ([(c.level, c.hue, c.sat, c.flash) for c in b.d.console.channels], b.d.console.master, b.d.console.bank)
    before = look()
    s = b.sim()
    s.f = [0.9] * 9
    s.mask = 0b11 | (1 << BL.B_BLACKOUT)                       # buttons already down when it connects
    await s.emit()
    assert look() == before                                    # (pickup flags may change: that is not the look)
    assert not b.d.console.blackout and not b.ch(1).flash
    await s.up("flash", 1)                                     # releasing what was never pressed: nothing
    assert not b.ch(1).flash


async def test_faders_flash_blackout_bank_and_toggles_become_intents(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.5)
    assert b.ch(1).level == pytest.approx(0.5) and b.d.console.last_touched == ("A", 1)
    await s.fader("master", 0.7)
    assert b.d.console.master == pytest.approx(0.7)
    await s.down("flash", 3)
    assert b.ch(3).flash
    await s.up("flash", 3)
    assert not b.ch(3).flash
    await s.tap("blackout")
    assert b.d.console.blackout
    await s.tap("blackout")
    assert not b.d.console.blackout
    await s.toggle("fn", 1)
    assert b.d.console.fader_fn == "hue"
    await s.toggle("fn", 0)
    await s.tap("bank", 1)
    assert b.d.console.bank == "B"
    await s.toggle("art", 1)
    assert b.d.engine.cfg.mode == "paint"
    await s.toggle("who", 1)                                   # already BOARD: no change, no error
    assert b.d.engine.enabled is False


async def test_end_stops_are_reported_inside_the_deadband(bench):
    b = bench
    s = await b.online()
    await s.fader(2, 0.5)
    await s.fader(2, 0.002 + 0.5)                               # inside the deadband: not an intent
    assert b.ch(2).level == pytest.approx(0.5)
    await s.fader(2, 0.0)                                       # but the bottom and top stops always are
    assert b.ch(2).level == 0.0
    await s.fader(2, 0.998)
    await s.fader(2, 1.0)
    assert b.ch(2).level == 1.0


async def test_encoders_are_absolute_counts_that_may_wrap(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.5)
    h0 = b.ch(1).hue
    await s.turn("hue", 4)
    assert b.ch(1).hue == pytest.approx((h0 + 20) % 360)
    await s.turn("sat", 10)
    assert b.ch(1).sat == pytest.approx(0.2)
    await s.turn("sat", -4)
    assert b.ch(1).sat == pytest.approx(0.12)
    # counts are signed 32-bit and wrap: the difference is taken modulo 2**32
    assert BL._delta32(-2 ** 31 + 5, 2 ** 31 - 1) == 6
    assert BL._delta32(2 ** 31 - 1, -2 ** 31 + 5) == -6
    assert BL._delta32(7, 3) == 4 and BL._delta32(3, 7) == -4
    s.e[1] = 2 ** 31                                           # outside int32: not a valid packet
    assert await s.emit() is True and b.link.stats["bad_format"] >= 1
    assert b.ch(1).sat == pytest.approx(0.12)


async def test_palette_and_cue_short_press_applies_long_press_stores(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.5)                                       # touch a channel
    b.d.console.palette[2] = "#336699"
    await s.tap("palette", 3)                                   # short: apply swatch 3
    assert b.ch(1).hue == pytest.approx(210.0, abs=0.5)
    await s.fader(1, 0.5)
    b.ch(1).hue, b.ch(1).sat = 100.0, 0.5
    await s.tap("palette", 4, hold=0.7)                         # held: store the current colour in swatch 4
    assert b.d.console.palette[3] != "#336699" and b.d.console.cues[0].look is None
    stored = b.d.console.palette[3]
    await s.tap("palette", 4)                                   # a later short tap applies what was stored
    assert HSB_hex(b.ch(1)) == stored
    await s.tap("cue", 2, hold=0.7)                             # held: store cue 2
    assert b.d.console.cues[1].look is not None
    b.ch(1).level = 0.0
    await s.tap("cue", 2)                                       # short: recall it
    assert b.ch(1).level == pytest.approx(0.5)


def HSB_hex(c):
    from outputs.base import HSB
    return HSB(c.hue, c.sat, 1.0).hex()


async def test_a_held_palette_button_stores_once_without_waiting_for_release(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.5)
    b.ch(1).hue, b.ch(1).sat = 40.0, 1.0
    await s.down("palette", 5)
    await b.sleep(0.5)
    assert b.d.console.palette[4] != HSB_hex(b.ch(1))           # not yet
    await b.sleep(0.2)                                          # 0.7 s: the timer fires, button still down
    assert b.d.console.palette[4] == HSB_hex(b.ch(1))
    b.ch(1).hue = 200.0
    await s.up("palette", 5)                                    # release after a store: no apply
    assert b.ch(1).hue == 200.0


async def test_assign_selects_and_assign_with_hue_turn_remaps_the_group(bench):
    b = bench
    s = await b.online()
    await s.tap("assign", 4)
    assert b.d.console.last_touched == ("A", 4)
    ids = list(b.d.store.groups)
    cur = b.ch(4).group_id
    await s.down("assign", 4)
    await s.turn("hue", 1)
    await s.up("assign", 4)
    assert b.ch(4).group_id == ids[(ids.index(cur) + 1) % len(ids)]
    assert b.d.store.banks["A"][3] == b.ch(4).group_id          # persisted in the rig
    hue = b.ch(4).hue
    await s.turn("hue", 2)                                      # ASSIGN no longer held: it is a hue move
    assert b.ch(4).hue != hue


async def test_rec_play_stop_buttons_use_the_take_handlers(bench):
    b = bench
    s = await b.online()
    await s.tap("rec")
    assert b.d.takes.rec is not None
    await s.fader(8, 0.6)
    advance(b.d, b.fc, 500)
    await s.tap("stop")
    assert b.d.takes.rec is None and len(b.d.takes.store.list()) == 1
    b.d.fake_provider.seek(0)
    b.d.poll_now()
    await s.tap("play")
    assert b.d.takes.play_take is not None
    await s.turn("nudge", 25)
    assert b.d.takes.play_take["nudge_ms"] == 25
    await s.tap("stop")
    assert b.d.takes.play_take is None


# ---- bank switch + pickup ----------------------------------------------------------------------------

async def test_bank_switch_disengages_and_a_fader_must_pick_up(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.9)
    assert b.ch(1).engaged
    await s.tap("bank", 1)                                      # bank B
    stored = b.ch(1, "B").level
    assert stored == 0.0 and not b.ch(1, "B").engaged            # physical fader sits at 0.9: ghost marker
    await s.fader(1, 0.85)                                      # moving it away from B's value: ignored
    assert b.ch(1, "B").level == stored
    await s.sweep(1, 0.85, 0.0, steps=10)                       # reaching the stored value picks it up
    assert b.ch(1, "B").engaged
    await s.fader(1, 0.3)
    assert b.ch(1, "B").level == pytest.approx(0.3, abs=0.002)
    assert b.ch(1, "A").level == pytest.approx(0.9)             # bank A untouched


async def test_a_flash_held_through_a_bank_switch_is_released_and_stays_ignored(bench):
    b = bench
    s = await b.online()
    await s.down("flash", 2)
    assert b.ch(2).flash
    await s.tap("bank", 1)
    assert not b.ch(2).flash and not b.ch(2, "B").flash         # nothing stuck in either bank
    await s.up("flash", 2)
    assert not b.ch(2, "B").flash
    await s.down("flash", 2)                                    # pressing again in bank B works
    assert b.ch(2, "B").flash


# ---- offline -------------------------------------------------------------------------------------------

async def test_offline_after_3s_releases_flashes_keeps_faders_and_recovers(bench):
    b = bench
    s = await b.online()
    await s.fader(1, 0.5)
    await s.down("flash", 1)
    await s.down("flash", 5)
    assert b.ch(1).flash and b.ch(5).flash
    await b.sleep(2.9)
    assert b.ch(1).flash and b.d.state.build()["health"]["board"] == "virtual"
    await b.sleep(0.2)
    assert not any(c.flash for c in b.d.console.channels)
    assert b.ch(1).level == pytest.approx(0.5)                  # faders are kept
    assert b.d.state.build()["health"]["board"] == "offline"
    assert_valid("state", b.d.state.build())
    # output: the lit flash went back to the fader level
    s.mask = 1                                                  # it comes back with FLASH 1 still down
    await s.emit()
    assert b.d.state.build()["health"]["board"] == "virtual" and not b.ch(1).flash   # baseline: not a press
    await s.up("flash", 1)
    await s.down("flash", 1)
    assert b.ch(1).flash


async def test_a_flash_released_by_offline_goes_dark_on_the_lights(bench):
    b = bench
    s = await b.online()
    await s.fader(8, 0.0)
    await s.down("flash", 8)
    advance(b.d, b.fc, 200)
    assert vl_frames(b.d)[-1][1].b == 1.0
    since = len(b.d.virtual.frames)
    await b.sleep(3.2)
    advance(b.d, b.fc, 1500)                                    # the strobe budget recovers
    assert vl_frames(b.d, since)[-1][1].b == 0.0


# ---- UI frames -------------------------------------------------------------------------------------------

async def test_ui_frames_go_out_on_change_and_at_2hz(bench):
    b = bench
    frames = []
    b.link.sink = frames.append
    s = await b.online()
    first = len(frames)
    assert first == 1                                           # right after the baseline
    f0 = json.loads(frames[0])
    assert_valid("board-frame", f0)
    assert f0["ui"]["bank"] == "A" and f0["ui"]["who"] == "board" and len(f0["ui"]["labels"]) == 8
    await b.sleep(0.4)
    assert len(frames) == first                                 # nothing changed, heartbeat not due
    await s.emit()
    await b.sleep(0.2)
    assert len(frames) == first + 1                             # 2 Hz heartbeat
    await s.fader(1, 0.77)
    await b.sleep(0.1)
    last = json.loads(frames[-1])
    assert last["ui"]["level"][0] == pytest.approx(0.77)
    assert last["seq"] > f0["seq"]
    await s.tap("bank", 2)
    await b.sleep(0.1)
    assert json.loads(frames[-1])["ui"]["bank"] == "C"


# ---- canned performance, packet loss, parity ----------------------------------------------------------------

def _outputs(d):
    return [(fid, h.h, h.s, h.b, f) for _t, fid, h, f in d.virtual.frames]


def _times(d):
    return [t for t, *_ in d.virtual.frames]


async def test_parity_the_same_intents_via_ipc_and_via_the_board_give_identical_output():
    script = canned_performance() + [
        (7.0, {"intent": "palette_apply", "i": 1}), (7.6, {"intent": "cue_store", "i": 0}),
        (8.4, {"intent": "fader", "ch": 1, "v": 0.15}), (8.8, {"intent": "cue_recall", "i": 0}),
        (9.4, {"intent": "blackout", "on": True}), (9.8, {"intent": "flash", "ch": 8, "down": True}),
        (10.2, {"intent": "flash", "ch": 8, "down": False}), (10.8, {"intent": "blackout", "on": False}),
        (11.4, {"intent": "fader", "ch": 8, "v": 0.9}), (11.8, {"intent": "hue", "delta": 12}),
        (12.2, {"intent": "sat", "delta": 20}), (12.6, {"intent": "fader", "ch": 8, "v": 0.35}),
        (13.4, {"intent": "fader", "ch": 8, "v": 0.5}), (14.2, {"intent": "fader", "ch": 8, "v": 0.6})]
    def tap(d):
        log, orig = [], d.scheduler.push

        def push(fid, hsb, fade):
            log.append((fid, round(hsb.h, 6), round(hsb.s, 6), round(hsb.b, 6), fade))
            orig(fid, hsb, fade)
        d.scheduler.push = push
        return log
    # via IPC
    a, fca = fake_daemon()
    pushed_a = tap(a)
    t = 0.0
    for at, i in script:
        advance(a, fca, (at - t) * 1000.0, step=2.0)
        t = at
        await op(a, "intent", intent=dict(i))
    advance(a, fca, 2000, step=2.0)
    # via the board
    b = Bench()
    pushed_b = tap(b.d)
    s = b.sim()
    await s.emit()
    await s.play(script)
    await b.sleep(2.0)
    assert s.lost == 0                                          # lossless link
    ours, theirs = _outputs(a), _outputs(b.d)
    assert len(ours) >= 15 and len(pushed_a) >= 15
    assert pushed_b == pushed_a                                 # every fixture, LIFX included (scheduler output)
    assert theirs == ours                                       # same values, same fades, same order
    # taps act on release (50 ms later); everything else lands on the same tick
    assert all(abs(x - y) <= 0.06 for x, y in zip(_times(a), _times(b.d)))
    assert a.console.to_state_fields() == b.d.console.to_state_fields() or \
        [c.level for c in a.console.channels] == [c.level for c in b.d.console.channels]
    await a.shutdown()
    await b.d.shutdown()


@pytest.mark.parametrize("seed", range(12))
async def test_with_30_percent_loss_nothing_sticks_and_state_converges_within_500ms(seed):
    b = Bench()
    s = b.sim(loss=0.3, seed=seed)
    s.loss = 0.0
    await s.emit()                                              # the baseline is not lost (a board retries it)
    s.loss = 0.3
    await s.play([(0.0, {"intent": "fader", "ch": 1, "v": 0.4}), (0.3, {"intent": "flash", "ch": 2, "down": True}),
                  (0.6, {"intent": "fader", "ch": 3, "v": 0.7}), (0.9, {"intent": "flash", "ch": 2, "down": False}),
                  (1.2, {"intent": "flash", "ch": 4, "down": True}), (1.5, {"intent": "fader", "ch": 1, "v": 0.8}),
                  (1.8, {"intent": "flash", "ch": 4, "down": False}), (2.1, {"intent": "fader", "ch": "master", "v": 0.6})])
    await s.heartbeat(0.5)                                      # <= 500 ms after the last change
    assert s.lost > 0
    assert not any(c.flash for c in b.d.console.channels), "a flash stuck"
    assert b.ch(1).level == pytest.approx(0.8, abs=0.002) and b.ch(3).level == pytest.approx(0.7, abs=0.002)
    assert b.d.console.master == pytest.approx(0.6, abs=0.002)
    await b.d.shutdown()


async def test_a_lost_flash_release_is_repaired_by_the_next_datagram(bench):
    b = bench
    s = await b.online()
    await s.down("flash", 6)
    assert b.ch(6).flash
    s.mask = 0                                                  # the release datagrams were all lost ...
    await s.emit()                                              # ... the heartbeat carries the state
    assert not b.ch(6).flash


async def test_replaying_a_take_as_a_board_reproduces_its_intents(bench):
    b = bench
    from test_takes_daemon import SCRIPT
    d = b.d
    tid = (await op(d, "rec.start"))["take_id"]
    t = 0
    for at, intent in SCRIPT:
        advance(d, b.fc, at - t)
        t = at
        await d.apply_intent(dict(intent), "ui")
    advance(d, b.fc, 400)
    await op(d, "rec.stop")
    path = d.takes.store.path(tid)
    recorded = [e["i"] for e in d.takes.store.get(tid)[1]]
    d.console.channel("A", 8).level, d.console.master = 0.0, 1.0
    seen = []
    orig = d.apply_intent

    async def spy(intent, src, at=None):
        seen.append((dict(intent), src))
        return await orig(intent, src, at)
    d.apply_intent = spy
    s = b.sim()
    await s.emit()
    skipped = await s.replay_take(path)
    assert skipped == 0
    assert [(i, src) for i, src in seen] == [(i, "board") for i in recorded]


async def test_board_sim_refuses_what_a_board_cannot_do(bench):
    s = bench.sim()
    for bad in ({"intent": "hue", "v": 120}, {"intent": "assign", "ch": 1, "group_id": "lr-all"}, {"intent": "nope"}):
        with pytest.raises(Unplayable):
            await s.intent(bad)


# ---- pairing + real UDP -------------------------------------------------------------------------------------

async def test_pairing_creates_the_key_once_and_the_listener_then_takes_real_udp():
    from daemon import Chromad
    sock = os.path.join(__import__("tempfile").mkdtemp(prefix="cx"), "chromad.sock")
    d = Chromad(asyncio.get_running_loop(), fake=True, socket_path=sock, board_port=0, board_host="127.0.0.1")
    task = asyncio.create_task(d.run())
    try:
        assert await wait_for(lambda: os.path.exists(sock))
        assert await wait_for(lambda: d.store.active("lifx") and d.virtual.current and d.engine._last_per_light)
        await op(d, "set_art_mode", mode="det")
        await d.handle({"op": "intent", "intent": {"intent": "who", "who": "board"}})
        assert await wait_for(lambda: d.engine.enabled is False)
        assert d.board.transport is None and d.state.build()["health"]["board"] == "none"
        res = await op(d, "board.pairing")
        assert res["created"] is True and len(res["key"]) == 64 and res["port"] == d.board.port > 0
        assert_valid("ipc-response", {"id": 1, "ok": True, **res})
        path = BL.key_path(d.home)
        assert os.stat(path).st_mode & 0o777 == 0o600 and bytes.fromhex(res["key"]) == open(path, "rb").read()
        again = await op(d, "board.pairing")
        assert again["key"] == res["key"] and again["created"] is False
        assert d.state.build()["board_link"]["paired"] is True
        assert_valid("state", d.state.build())

        loop = asyncio.get_running_loop()
        got = []

        class P(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                got.append(data)
        tr, _ = await loop.create_datagram_endpoint(P, remote_addr=("127.0.0.1", d.board.port))
        key = bytes.fromhex(res["key"])
        sim = BoardSim(key, tr.sendto, seed=3)
        sim.place_faders([c.level for c in d.console.channels[:8]] + [d.console.master])
        await sim.emit()
        assert await wait_for(lambda: d.state.build()["health"]["board"] == "virtual")
        assert d.state.build()["board_link"]["id"] == "sim-1"
        await sim.fader(1, 0.42)
        assert await wait_for(lambda: d.console.channel("A", 1).level == pytest.approx(0.42))
        tr.sendto(b"garbage")                                   # an unauthenticated stranger
        tr.sendto(BoardSim(bytes(32), None).packet())
        await sim.heartbeat(0.6)
        assert d.board.stats["bad_mac"] >= 2
        assert got, "the engine sent UI frames back to the board"
        frame = json.loads(got[-1])
        assert BL.unseal(key, got[-1]) is not None
        assert_valid("board-frame", frame)
        tr.close()
    finally:
        d.request_stop()
        await asyncio.wait_for(task, timeout=5)

