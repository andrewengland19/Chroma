#!/usr/bin/env python3
# =============================================================================
# tools/board_sim.py — a virtual CHROMA board (BUILD_ORDER §5.5, M6).
#
# Speaks the real UDP protocol (contracts/board-map.md): full state snapshots,
# HMAC-signed with ~/.chroma/board.key, so chromad cannot tell it from the
# hardware. Its id starts with "sim", which makes State.health.board "virtual".
#
# Python API (tests use it in-process on a fake clock; see hub/tests/test_board_link.py):
#     sim = BoardSim(key, send=link_deliver, sleep=advance_clock, loss=0.3, seed=7)
#     await sim.intent({"intent": "fader", "ch": 3, "v": 0.6})
#     await sim.replay_take("~/.chroma/takes/…jsonl")
#
# CLI (needs `board.pairing` once, so that <home>/board.key exists):
#     board_sim.py [--home DIR] [--host 127.0.0.1] [--port 9876] [--loss 0.3] COMMAND
#       perform            a canned 8-second performance (faders, flashes, a bank switch, knobs)
#       replay TAKE        replay a take as if it were played on the board (file, or id in <home>/takes)
#       send JSON …        board-playable intents, e.g. '{"intent":"fader","ch":3,"v":0.6}'
#       idle SECONDS       just stay online (heartbeats at 2 Hz) and print the UI frames
#
# A real board repeats a changed snapshot a few times (SETTLE_REPEATS, 16 ms apart), so one
# lost datagram never costs a heartbeat (500 ms); the sim does the same.
# =============================================================================

import argparse
import asyncio
import glob
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "hub"),):
    if p not in sys.path:
        sys.path.insert(0, p)

import board_link as BL  # noqa: E402
from takes import read_take  # noqa: E402

SETTLE_REPEATS = 4          # copies of a changed snapshot ...
SETTLE_GAP_S = 0.016        # ... one 60 Hz frame apart
HEARTBEAT_S = 0.5
PRESS_S = 0.05              # how long a tap is held
HOLD_S = BL.HOLD_S + 0.05   # a "hold" press (palette / cue store)
BTN = {"flash": BL.B_FLASH, "assign": BL.B_ASSIGN, "palette": BL.B_PALETTE, "cue": BL.B_CUE}
ENC = {"hue": 0, "sat": 1, "nudge": 2}


class Unplayable(ValueError):
    """An intent a board cannot express (e.g. hue by absolute value, assign to a group)."""


class BoardSim:
    def __init__(self, key: bytes, send, *, board_id: str = "sim-1", boot: int = None, sleep=None,
                 loss: float = 0.0, seed: int = None):
        """`send(data) -> awaitable|None` delivers one datagram; `sleep(seconds)` is awaited
        between repeats and holds (default asyncio.sleep; tests pass a fake-clock stepper)."""
        self.key = key
        self._send = send
        self._sleep = sleep or asyncio.sleep
        self.now_s = 0.0            # simulated seconds this board has slept (script timing)
        self.id = board_id
        self.rng = random.Random(seed)
        self.boot = self.rng.getrandbits(32) if boot is None else boot
        self.loss = loss
        self.seq = 0
        self.t_ms = 0
        self.f = [0.0] * 9
        self.mask = 0
        self.e = [0, 0, 0]
        self.s = [0, 0, 0]
        self._blackout = False
        self.sent = 0
        self.lost = 0
        self.frames: list = []      # engine -> board UI frames received (UDP mode)

    async def sleep(self, seconds: float) -> None:
        self.now_s += seconds
        await self._sleep(seconds)

    # ---- the wire -----------------------------------------------------------------------------
    def packet(self) -> bytes:
        self.seq += 1
        return BL.seal(self.key, {"v": 1, "id": self.id, "boot": self.boot, "seq": self.seq, "t": self.t_ms,
                                  "f": [round(x, 3) for x in self.f], "b": "0x%010X" % self.mask,
                                  "e": list(self.e), "s": list(self.s)})

    async def emit(self) -> bool:
        """Send the current state as one datagram (False if the simulated network lost it)."""
        data = self.packet()
        self.sent += 1
        if self.loss and self.rng.random() < self.loss:
            self.lost += 1
            return False
        r = self._send(data)
        if asyncio.iscoroutine(r):
            await r
        return True

    async def changed(self) -> None:
        """Something changed: send the snapshot, then repeat it a few times (60 Hz)."""
        for k in range(SETTLE_REPEATS):
            await self.emit()
            if k < SETTLE_REPEATS - 1:
                await self.sleep(SETTLE_GAP_S)

    async def heartbeat(self, seconds: float, hz: float = 2.0) -> None:
        """Stay online for `seconds`, sending the state at `hz` (the board's idle heartbeat)."""
        n = max(1, int(seconds * hz))
        for _ in range(n):
            await self.emit()
            await self.sleep(1.0 / hz)

    # ---- controls -----------------------------------------------------------------------------
    async def fader(self, ch, v: float) -> None:
        self.f[8 if ch == "master" else int(ch) - 1] = min(1.0, max(0.0, float(v)))
        await self.changed()

    def place_faders(self, values) -> None:
        """Put the physical faders at `values` (8 channels [+ master]) without sending."""
        for i, v in enumerate(values[:9]):
            self.f[i] = min(1.0, max(0.0, float(v)))

    async def sweep(self, ch, a: float, b: float, steps: int = 12, dt: float = 0.02) -> None:
        for k in range(1, steps + 1):
            await self.fader(ch, a + (b - a) * k / steps)
            await self.sleep(dt)

    def _bit(self, kind: str, n: int = 1) -> int:
        if kind in BTN:
            return 1 << (BTN[kind] + n - 1)                   # n = 1..8
        return 1 << ({"bank": BL.B_BANK + n, "rec": BL.B_REC, "play": BL.B_PLAY, "stop": BL.B_STOP,
                      "blackout": BL.B_BLACKOUT}[kind])        # bank: n = 0..2

    async def down(self, kind: str, n: int = 1) -> None:
        self.mask |= self._bit(kind, n)
        await self.changed()

    async def up(self, kind: str, n: int = 1) -> None:
        self.mask &= ~self._bit(kind, n)
        await self.changed()

    async def tap(self, kind: str, n: int = 1, hold: float = PRESS_S) -> None:
        await self.down(kind, n)
        await self.sleep(hold)
        await self.up(kind, n)

    async def turn(self, enc: str, detents: int) -> None:
        self.e[ENC[enc]] += int(detents)
        await self.changed()

    async def toggle(self, which: str, value: int) -> None:
        self.s[("art", "who", "fn").index(which)] = value
        await self.changed()

    # ---- console intents played as board gestures -----------------------------------------------
    async def intent(self, i: dict) -> None:
        """Play a console intent as the equivalent board gesture (raises Unplayable if a board
        cannot express it). Channel numbers refer to the bank the board is in."""
        n = i["intent"]
        if n == "fader":
            return await self.fader(i["ch"], i["v"])
        if n == "flash":
            return await (self.down if i["down"] else self.up)("flash", i["ch"])
        if n == "bank":
            return await self.tap("bank", "ABC".index(i["bank"]))
        if n == "fader_fn":
            return await self.toggle("fn", ("level", "hue", "sat").index(i["fn"]))
        if n in ("hue", "sat"):
            if "delta" not in i:
                raise Unplayable(f"{n} by absolute value: a board has encoders")
            return await self.turn(n, i["delta"])
        if n == "assign":
            if "group_id" in i:
                raise Unplayable("assign to a group (hold ASSIGN and turn HUE on a board)")
            return await self.tap("assign", i["ch"])
        if n in ("palette_apply", "cue_recall"):
            return await self.tap(n.split("_")[0], i["i"] + 1)
        if n in ("palette_store", "cue_store"):
            return await self.tap(n.split("_")[0], i["i"] + 1, hold=HOLD_S)
        if n == "blackout":
            if bool(i["on"]) != self._blackout:
                self._blackout = bool(i["on"])
                await self.tap("blackout")
            return None
        if n == "who":
            return await self.toggle("who", ("auto", "board").index(i["who"]))
        raise Unplayable(f"intent {n!r}")

    async def play(self, script: list) -> int:
        """script: [(seconds after start, intent), …]. Gestures take time (a settle burst is
        ~50 ms), so each intent waits until its own time, not for the previous one's end.
        Returns how many intents a board cannot play."""
        skipped, start = 0, self.now_s
        for at, i in script:
            wait = at - (self.now_s - start)
            if wait > 0:
                await self.sleep(wait)
            try:
                await self.intent(i)
            except Unplayable as e:
                skipped += 1
                print(f"board_sim: skipped {i}: {e}", file=sys.stderr)
        return skipped

    async def replay_take(self, path: str, speed: float = 1.0) -> int:
        """Replay a take's intents as if a person played them on the board (event `i`)."""
        _header, events = read_take(path)
        script = [(ev["p"] / 1000.0 / speed, ev["i"]) for ev in events if ev.get("i")]
        base = script[0][0] if script else 0.0
        return await self.play([(t - base, i) for t, i in script])

    # ---- UI frames (UDP mode) ---------------------------------------------------------------------
    def on_frame(self, data: bytes) -> None:
        body = BL.unseal(self.key, data)
        if body is not None:
            self.frames.append(json.loads(body))
            del self.frames[:-50]


# A short, board-playable performance (seconds, intent): faders, a bank switch, flashes, knobs.
CANNED = [
    (0.10, {"intent": "fader", "ch": 1, "v": 0.20}), (0.30, {"intent": "fader", "ch": 1, "v": 0.55}),
    (0.60, {"intent": "fader", "ch": 8, "v": 0.40}), (1.00, {"intent": "flash", "ch": 8, "down": True}),
    (1.40, {"intent": "flash", "ch": 8, "down": False}), (1.80, {"intent": "fader", "ch": "master", "v": 0.80}),
    (2.20, {"intent": "hue", "delta": 6}), (2.40, {"intent": "sat", "delta": -3}),
    (2.80, {"intent": "bank", "bank": "B"}), (3.20, {"intent": "bank", "bank": "A"}),
    (3.60, {"intent": "fader", "ch": 2, "v": 0.65}), (4.40, {"intent": "flash", "ch": 2, "down": True}),
    (4.90, {"intent": "flash", "ch": 2, "down": False}), (5.50, {"intent": "fader", "ch": 1, "v": 0.10}),
    (6.50, {"intent": "fader", "ch": 2, "v": 0.0}),
]


def canned_performance() -> list:
    return [(t, dict(i)) for t, i in CANNED]


# ---- CLI --------------------------------------------------------------------------------------------

def find_take(home: str, ref: str) -> str:
    if os.path.exists(ref):
        return ref
    hits = sorted(glob.glob(os.path.join(home, "takes", f"{ref}*.jsonl")))
    if not hits:
        raise SystemExit(f"board_sim: no take {ref!r} (file or id in {home}/takes)")
    return hits[-1]


async def amain(args) -> int:
    home = os.path.abspath(os.path.expanduser(args.home or os.environ.get("CHROMA_HOME") or "~/.chroma"))
    key = BL.load_key(BL.key_path(home))
    if key is None:
        print(f"board_sim: no {BL.key_path(home)}: send the board.pairing op first "
              "(Setup → Board → Pair board, or `chromactl.py op board.pairing`)", file=sys.stderr)
        return 2
    loop = asyncio.get_running_loop()
    holder = {}
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Proto(holder), remote_addr=(args.host, args.port))
    sim = BoardSim(key, transport.sendto, board_id=args.id, loss=args.loss, seed=args.seed)
    holder["sim"] = sim
    try:
        await sim.emit()                                  # baseline
        await sim.sleep(0.2)
        if args.cmd == "perform":
            skipped = await sim.play(canned_performance())
        elif args.cmd == "replay":
            skipped = await sim.replay_take(find_take(home, args.arg[0]), args.speed)
        elif args.cmd == "send":
            skipped = await sim.play([(0.0, json.loads(a)) for a in args.arg])
        else:
            await sim.heartbeat(float(args.arg[0]) if args.arg else 5.0)
            skipped = 0
        await sim.heartbeat(1.0, hz=10)                   # let the last state converge
        print(f"board_sim: sent {sim.sent} datagrams ({sim.lost} lost), skipped {skipped} intent(s); "
              f"last UI frame: {sim.frames[-1]['ui'] if sim.frames else 'none'}")
        return 0
    finally:
        transport.close()


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, holder):
        self.holder = holder

    def datagram_received(self, data, addr):
        sim = self.holder.get("sim")
        if sim is not None:
            sim.on_frame(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="board_sim", description="virtual CHROMA board (UDP)")
    ap.add_argument("--home", help="chromad home with board.key (default $CHROMA_HOME or ~/.chroma)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=BL.BOARD_PORT)
    ap.add_argument("--id", default="sim-1", help="board id (must start with 'sim' to show as virtual)")
    ap.add_argument("--loss", type=float, default=0.0, help="simulated packet loss, 0..1")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed (2 = twice as fast)")
    ap.add_argument("cmd", choices=["perform", "replay", "send", "idle"])
    ap.add_argument("arg", nargs="*")
    args = ap.parse_args(argv)
    if not args.id.startswith("sim"):
        ap.error("--id must start with 'sim'")
    if args.cmd in ("replay", "send") and not args.arg:
        ap.error(f"{args.cmd} needs an argument")
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
