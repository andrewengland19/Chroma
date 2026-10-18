# =============================================================================
# hub/board_link.py — the physical board <-> chromad over UDP (BUILD_ORDER §5.5,
# contracts/board-map.md).
#
# The board sends full state snapshots; the engine DIFFS consecutive accepted
# snapshots into intents (src "board") and feeds them through daemon.apply_intent,
# the same path as the UI, so pickup, takes and the strobe limit apply unchanged.
# Because state is level-triggered a lost datagram is harmless: the next one
# carries everything.
#
# Off until ~/.chroma/board.key exists (board.pairing creates it). Datagrams are
# authenticated (HMAC-SHA256, first 8 bytes) BEFORE they are parsed.
#
# Time comes from daemon.clock, and `receive` / `tick` are plain coroutines, so
# tests and tools/board_sim.py drive the link on a fake clock without a socket.
# =============================================================================

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import socket
from hashlib import sha256
from typing import Optional

from ipc import IpcError
from outputs.base import HSB

log = logging.getLogger("chroma-hub")

BOARD_PORT = 9876
MAX_DATAGRAM = 512
OFFLINE_S = 3.0                 # no accepted datagram for this long -> offline
HOLD_S = 0.6                    # PALETTE / CUE held this long = store, shorter = apply/recall
REORDER_WINDOW = 8              # older-than-newest seqs this close are late, not replays
FADER_DEADBAND = 0.004
BOOTS_KEEP = 64
FRAME_MIN_GAP_S = 0.05          # engine -> board: changes at most 20 Hz ...
FRAME_HEARTBEAT_S = 0.5         # ... and at least 2 Hz
BOOTS_FLUSH_S = 2.0
TICK_S = 0.05

# button mask bit offsets (contracts/board-map.md)
B_FLASH, B_ASSIGN, B_PALETTE, B_CUE, B_BANK = 0, 8, 16, 24, 32
B_REC, B_PLAY, B_STOP, B_BLACKOUT = 36, 37, 38, 39
ART_MODES = ("det", "paint", "ai")
WHO = ("auto", "board")
FADER_FNS = ("level", "hue", "sat")
BANKS = ("A", "B", "C")
NUDGE_LIMIT_MS = 2000

_MAC_TAIL = re.compile(rb',"mac":"([0-9a-f]{16})"\}\Z')
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
_MASK = re.compile(r"^0x[0-9A-Fa-f]{10}$")


# ---- key + wire format ------------------------------------------------------------------------

def key_path(home: str) -> str:
    return os.path.join(home, "board.key")


def load_key(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as fh:
            key = fh.read()
    except OSError:
        return None
    return key if len(key) == 32 else None


def create_key(path: str) -> tuple:
    """(key, created). Never overwrites: a second pairing returns the same key."""
    key = load_key(path)
    if key is not None:
        return key, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_bytes(32)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    os.replace(tmp, path)
    return key, True


def sign(key: bytes, body: bytes) -> str:
    return hmac.new(key, body, sha256).digest()[:8].hex()


def seal(key: bytes, obj: dict) -> bytes:
    """Datagram bytes with `mac` as the LAST member: ...,"mac":"<16 hex>"} (board-map.md).
    The MAC covers the datagram with that suffix removed (and the closing brace put back)."""
    body = json.dumps(obj, separators=(",", ":")).encode()
    return body[:-1] + b',"mac":"' + sign(key, body).encode() + b'"}'


def unseal(key: bytes, data: bytes) -> Optional[bytes]:
    """The signed body if the MAC checks out, else None."""
    m = _MAC_TAIL.search(data)
    if m is None:
        return None
    body = data[:m.start()] + b"}"
    return body if hmac.compare_digest(sign(key, body), m.group(1).decode()) else None


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def parse_packet(data: bytes) -> Optional[dict]:
    """A validated board-packet (contracts/board-packet.schema.json), or None."""
    try:
        o = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(o, dict) or set(o) != {"v", "id", "boot", "seq", "t", "f", "b", "e", "s", "mac"}:
        return None
    if o["v"] != 1 or not isinstance(o["id"], str) or not _ID.match(o["id"]):
        return None
    for k, hi in (("boot", 2 ** 32 - 1), ("seq", 2 ** 32 - 1)):
        if not _is_int(o[k]) or not 0 <= o[k] <= hi:
            return None
    if not _is_int(o["t"]) or o["t"] < 0:
        return None
    f, e, s = o["f"], o["e"], o["s"]
    if not (isinstance(f, list) and len(f) == 9 and all(_is_num(x) and 0 <= x <= 1 for x in f)):
        return None
    if not (isinstance(e, list) and len(e) == 3 and all(_is_int(x) and -2 ** 31 <= x < 2 ** 31 for x in e)):
        return None
    if not (isinstance(s, list) and len(s) == 3 and all(_is_int(x) and 0 <= x <= 2 for x in s)):
        return None
    if not isinstance(o["b"], str) or not _MASK.match(o["b"]):
        return None
    return o


def _delta32(cur: int, prev: int) -> int:
    """Signed difference of two 32-bit encoder counts (they may wrap)."""
    return ((cur - prev + 2 ** 31) % 2 ** 32) - 2 ** 31


def _ascii(text: str) -> str:
    """The board's display is ASCII: other characters become '?' (and never inflate the datagram)."""
    return text.encode("ascii", "replace").decode()


def lan_hosts() -> list:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))          # no packet is sent: this only picks the LAN interface
            return [s.getsockname()[0]]
        finally:
            s.close()
    except OSError:
        return []


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, link: "BoardLink"):
        self.link = link

    def datagram_received(self, data, addr):
        self.link.enqueue(data, addr)

    def error_received(self, exc):
        log.debug(f"board link socket error: {exc}")


class BoardLink:
    def __init__(self, d, port: int = BOARD_PORT, host: str = "0.0.0.0"):
        self.d = d
        self.host = host
        self.port = port
        self.path = key_path(d.home)
        self.boots_path = os.path.join(d.home, "board_boots.json")
        self.key: Optional[bytes] = load_key(self.path)
        self.transport = None
        self.sink = None                        # in-process boards (board_sim): called with each frame
        self.status = "none"                    # none | virtual | online | offline
        self.board_id: Optional[str] = None
        self.addr = None                        # where engine -> board frames go
        self.last_rx = 0.0
        self.stats = {"accepted": 0, "bad_size": 0, "bad_mac": 0, "bad_format": 0, "late": 0, "replay": 0,
                      "errors": 0, "dropped": 0, "frames": 0}
        self._q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._boots = self._load_boots()        # id -> {str(boot): newest seq}
        self._boots_dirty = False
        self._boots_flushed = 0.0
        self._session: Optional[dict] = None    # {"id", "boot", "seq"} of the board in use
        # diff state — all dropped on offline / a new boot, so the next snapshot is a baseline
        self.prev: Optional[dict] = None
        self._f_ref: list = []
        self._flash: set = set()                # FLASH channels (1-8) this board holds down
        self._flash_masked: set = set()         # held through a bank change: ignored until released
        self._assign_held: set = set()
        self._holds: dict = {}                  # (kind, n) -> [t_pressed, fired]
        # engine -> board frames
        self._frame_seq = 0
        self._last_ui: Optional[dict] = None
        self._last_frame_t = float("-inf")
        self._tasks: list = []

    # ---- persistence of used boot nonces ------------------------------------------------------
    def _load_boots(self) -> dict:
        try:
            with open(self.boots_path) as fh:
                raw = json.load(fh)
            return {str(i): {str(b): int(s) for b, s in v.items()} for i, v in raw.items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _flush_boots(self, now: float) -> None:
        if not self._boots_dirty:
            return
        self._boots_dirty = False
        self._boots_flushed = now
        tmp = self.boots_path + ".tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(self._boots, fh)
            os.replace(tmp, self.boots_path)
        except OSError as e:
            log.warning(f"could not save board_boots.json: {e}")

    # ---- pairing + socket ---------------------------------------------------------------------
    def info(self) -> dict:
        return {"paired": self.key is not None, "port": self.port or BOARD_PORT, "id": self.board_id}

    async def pair(self) -> dict:
        key, created = create_key(self.path)
        self.key = key
        await self.start()
        return {"key": key.hex(), "port": self.port, "hosts": lan_hosts(), "created": created}

    async def start(self) -> None:
        """Bind the UDP port — only once a key exists (§8.5)."""
        if self.key is None or self.transport is not None:
            return
        try:
            self.transport, _ = await self.d.loop.create_datagram_endpoint(
                lambda: _Protocol(self), local_addr=(self.host, self.port))
        except OSError as e:
            raise IpcError(f"could not listen for the board on UDP port {self.port}: {e}") from None
        self.port = self.transport.get_extra_info("sockname")[1]
        log.info(f"board link listening on UDP {self.host}:{self.port}")
        self.d.ipc.mark_dirty()

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self._flush_boots(self.d.clock.monotonic())

    async def run(self) -> None:
        """Main loop: datagrams in order, timers (holds, offline, frames) every 50 ms."""
        try:
            await self.start()
        except IpcError as e:
            log.warning(str(e))
            self.d.ipc.toast("warn", str(e))
        while True:
            try:
                data, addr = await asyncio.wait_for(self._q.get(), TICK_S)
                await self.receive(data, addr)
            except asyncio.TimeoutError:
                pass
            await self.tick()

    def enqueue(self, data: bytes, addr) -> None:
        try:
            self._q.put_nowait((data, addr))
        except asyncio.QueueFull:
            self.stats["dropped"] += 1

    # ---- receive ------------------------------------------------------------------------------
    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.d.state.board_health = status
            self.d.ipc.mark_dirty()

    def _accept(self, data: bytes, now: float) -> Optional[dict]:
        """Authenticate, parse and order one datagram. Returns the packet, or None (counted)."""
        if self.key is None:
            return None
        if len(data) > MAX_DATAGRAM:
            self.stats["bad_size"] += 1
            return None
        body = unseal(self.key, data)
        if body is None:
            self.stats["bad_mac"] += 1
            return None
        pkt = parse_packet(data)
        if pkt is None:
            self.stats["bad_format"] += 1
            return None
        bid, boot, seq = pkt["id"], pkt["boot"], pkt["seq"]
        hist = self._boots.setdefault(bid, {})
        sess = self._session
        if sess is not None and sess["id"] == bid and sess["boot"] == boot:
            newest, new_session = sess["seq"], False
        elif str(boot) in hist:
            newest, new_session = hist[str(boot)], True     # seen before: only a newer seq resumes it
        else:
            newest, new_session = -1, True
        if seq <= newest:
            self.stats["late" if newest - seq < REORDER_WINDOW else "replay"] += 1
            return None
        hist.pop(str(boot), None)
        hist[str(boot)] = seq
        while len(hist) > BOOTS_KEEP:
            hist.pop(next(iter(hist)))
        self._boots_dirty = True
        if new_session:
            self._boots_flushed = min(self._boots_flushed, now - BOOTS_FLUSH_S)   # save a new boot at once
        self._session = {"id": bid, "boot": boot, "seq": seq}
        pkt["_new"] = new_session or self.prev is None
        return pkt

    async def receive(self, data: bytes, addr=None) -> bool:
        """One datagram from the board. Returns True if it was accepted."""
        now = self.d.clock.monotonic()
        pkt = self._accept(data, now)
        if pkt is None:
            return False
        self.stats["accepted"] += 1
        if addr is not None:
            self.addr = addr
        was_id = self.board_id
        self.board_id = pkt["id"]
        self.last_rx = now
        self._set_status("virtual" if pkt["id"].startswith("sim") else "online")
        if was_id != self.board_id:
            self.d.ipc.mark_dirty()
        snap = {"f": [float(x) for x in pkt["f"]], "m": int(pkt["b"], 16), "e": list(pkt["e"]), "s": list(pkt["s"])}
        if pkt["_new"]:
            self._baseline(snap, now)
            self._send_frame(now, force=True)
        else:
            await self._run(self._diff(snap, now))
            self.prev = snap
        return True

    def _baseline(self, snap: dict, now: float) -> None:
        """First snapshot (or the first after offline / a reboot): where the board is, no intents.
        Buttons already down are ignored until released (nothing was pressed in this session)."""
        import console as console_logic
        self._reset_diff()
        self.prev = snap
        self._f_ref = list(snap["f"])
        console_logic.observe_board(self.d.console, snap["f"])
        self.d._console_changed()

    def _reset_diff(self) -> None:
        self.prev = None
        self._flash.clear()
        self._flash_masked.clear()
        self._assign_held.clear()
        self._holds.clear()

    # ---- snapshot diff -> actions -----------------------------------------------------------------
    def _diff(self, cur: dict, now: float) -> list:
        """Actions between the previous accepted snapshot and `cur`: ("intent", {...}) and
        ("op", {...}), in a fixed order (toggles, bank, faders, buttons, encoders)."""
        prev, acts = self.prev, []
        con = self.d.console
        self._fire_holds(now, acts)
        pm, cm = prev["m"], cur["m"]

        def rose(bit): return not (pm >> bit) & 1 and (cm >> bit) & 1
        def fell(bit): return (pm >> bit) & 1 and not (cm >> bit) & 1

        # toggles
        if cur["s"][0] != prev["s"][0]:
            acts.append(("op", {"op": "set_art_mode", "mode": ART_MODES[cur["s"][0]]}))
        if cur["s"][1] != prev["s"][1]:
            acts.append(("intent", {"intent": "who", "who": WHO[min(cur["s"][1], 1)]}))
        if cur["s"][2] != prev["s"][2]:
            acts.append(("intent", {"intent": "fader_fn", "fn": FADER_FNS[cur["s"][2]]}))

        # bank: release what this board flashes first (a held FLASH must not stay on in the old
        # bank), then switch; buttons held through the switch are ignored until released
        bank = next((BANKS[i] for i in range(3) if rose(B_BANK + i)), None)
        if bank is not None:
            for n in sorted(self._flash):
                acts.append(("intent", {"intent": "flash", "ch": n, "down": False}))
            self._flash_masked = {n for n in range(1, 9) if (cm >> (B_FLASH + n - 1)) & 1}
            self._flash.clear()
            acts.append(("intent", {"intent": "bank", "bank": bank}))

        # faders (8 channels, then master): deadband, but always report the end stops
        for i in range(9):
            v, ref = cur["f"][i], self._f_ref[i]
            if abs(v - ref) > FADER_DEADBAND or (v != ref and v in (0.0, 1.0)):
                self._f_ref[i] = v
                acts.append(("intent", {"intent": "fader", "ch": i + 1 if i < 8 else "master", "v": v}))

        # FLASH 1-8: level-triggered, momentary
        for n in range(1, 9):
            bit = B_FLASH + n - 1
            if rose(bit) and n not in self._flash_masked:
                self._flash.add(n)
                acts.append(("intent", {"intent": "flash", "ch": n, "down": True}))
            elif fell(bit):
                if n in self._flash_masked:
                    self._flash_masked.discard(n)
                elif n in self._flash:
                    self._flash.discard(n)
                    acts.append(("intent", {"intent": "flash", "ch": n, "down": False}))

        # ASSIGN 1-8: a press selects; holding it while the hue encoder turns re-maps the channel
        for n in range(1, 9):
            bit = B_ASSIGN + n - 1
            if rose(bit):
                self._assign_held.add(n)
                acts.append(("intent", {"intent": "assign", "ch": n}))
            elif fell(bit):
                self._assign_held.discard(n)

        # PALETTE / CUE 1-8: short = apply / recall on release, held HOLD_S = store (once)
        for kind, base, short, long_ in (("palette", B_PALETTE, "palette_apply", "palette_store"),
                                         ("cue", B_CUE, "cue_recall", "cue_store")):
            for n in range(1, 9):
                bit = base + n - 1
                if rose(bit):
                    self._holds[(kind, n)] = [now, False]
                elif fell(bit):
                    hold = self._holds.pop((kind, n), None)
                    if hold is not None and not hold[1]:
                        acts.append(("intent", {"intent": short, "i": n - 1}))

        if rose(B_BLACKOUT):
            acts.append(("intent", {"intent": "blackout", "on": not con.blackout}))
        if rose(B_REC):
            acts.append(("op", {"op": "board.rec"}))
        if rose(B_PLAY):
            acts.append(("op", {"op": "board.play"}))
        if rose(B_STOP):
            acts.append(("op", {"op": "board.stop"}))

        # encoders (absolute counts -> detents since the last accepted snapshot)
        dh, ds, dn = (_delta32(cur["e"][i], prev["e"][i]) for i in range(3))
        if dh:
            if self._assign_held:
                acts.append(("assign_cycle", {"ch": min(self._assign_held), "delta": dh}))
            else:
                acts.append(("intent", {"intent": "hue", "delta": dh}))
        if ds:
            acts.append(("intent", {"intent": "sat", "delta": ds}))
        if dn:
            acts.append(("op", {"op": "board.nudge", "delta": dn}))
        return acts

    def _fire_holds(self, now: float, acts: list) -> None:
        for (kind, n), hold in self._holds.items():
            if not hold[1] and now - hold[0] >= HOLD_S:
                hold[1] = True
                acts.append(("intent", {"intent": f"{kind}_store", "i": n - 1}))

    # ---- executing actions -----------------------------------------------------------------------
    async def _run(self, acts: list) -> None:
        for kind, a in acts:
            try:
                await self._do(kind, a)
            except Exception as e:                      # a refused intent must not stop the rest
                self.stats["errors"] += 1
                log.debug(f"board action {a} refused: {e}")

    async def _do(self, kind: str, a: dict) -> None:
        d = self.d
        if kind == "intent":
            await d.apply_intent(a, "board")
        elif kind == "assign_cycle":
            ids = list(d.store.groups)
            if not ids:
                return
            c = d.console.channel(d.console.bank, a["ch"])
            i = ids.index(c.group_id) if c.group_id in ids else (-1 if a["delta"] > 0 else 0)
            await d.apply_intent({"intent": "assign", "ch": a["ch"], "group_id": ids[(i + a["delta"]) % len(ids)]},
                                 "board")
        elif a["op"] == "board.rec":                    # the Console REC button
            if d.takes.rec is not None or d.takes.armed is not None:
                await d.handle({"op": "rec.stop"})
            else:
                await d.handle({"op": "rec.arm"})
        elif a["op"] == "board.play":                   # newest take of the song now playing
            np = d.state.latest_np
            if np is None or not np.has_track():
                raise IpcError("nothing is playing")
            take = next((t for t in d.takes.store.list()
                         if (t.get("track_id") and t["track_id"] == np.track_id())
                         or (t.get("title") == np.title and t.get("artist") == np.artist)), None)
            if take is None:
                raise IpcError("no take of this song")
            await d.handle({"op": "play.start", "take_id": take["id"]})
        elif a["op"] == "board.stop":
            if d.takes.rec is not None or d.takes.armed is not None:
                await d.handle({"op": "rec.stop"})
            if d.takes.play_take is not None:
                await d.handle({"op": "play.stop"})
        elif a["op"] == "board.nudge":
            take = d.takes.play_take
            if take is None:
                return
            ms = max(-NUDGE_LIMIT_MS, min(NUDGE_LIMIT_MS, int(take.get("nudge_ms") or 0) + a["delta"]))
            await d.handle({"op": "take.nudge", "take_id": take["id"], "ms": ms})
        else:
            await d.handle(a)

    # ---- timers ---------------------------------------------------------------------------------
    async def tick(self) -> None:
        now = self.d.clock.monotonic()
        if self.key is None:
            return
        if self.status in ("online", "virtual") and now - self.last_rx >= OFFLINE_S:
            await self._go_offline()
        elif self.status in ("online", "virtual") and self.prev is not None:
            acts: list = []
            self._fire_holds(now, acts)
            await self._run(acts)
            self._send_frame(now)
        if self._boots_dirty and now - self._boots_flushed >= BOOTS_FLUSH_S:
            self._flush_boots(now)

    async def _go_offline(self) -> None:
        """No valid datagram for 3 s: release every flash this board holds, keep the fader
        values, and treat the next datagram as a baseline."""
        held = sorted(self._flash)
        self._reset_diff()
        self._set_status("offline")
        log.warning("board link offline (no valid datagram for 3 s)")
        await self._run([("intent", {"intent": "flash", "ch": n, "down": False}) for n in held])

    # ---- engine -> board frames ------------------------------------------------------------------
    def ui(self) -> dict:
        d = self.d
        con = d.console
        row = [c for c in con.channels if c.bank == con.bank]
        groups = d.store.groups
        np = d.state.latest_np
        return {"bank": con.bank,
                "labels": [_ascii(groups[c.group_id].name if c.group_id in groups else "")[:16] for c in row],
                "colors": [HSB(c.hue, c.sat, 1.0).hex() for c in row],
                "track": _ascii(np.title or "")[:48] if np is not None and np.has_track() else "",
                "who": "auto" if d.engine.enabled else "board",
                "rec": d.takes.rec is not None,
                "level": [round(c.level, 3) for c in row]}

    def frame(self, ui: dict) -> bytes:
        """A sealed engine -> board datagram, <= 512 bytes (the track title is trimmed to fit)."""
        ui = dict(ui)
        while True:
            data = seal(self.key, {"v": 1, "seq": self._frame_seq, "ui": ui})
            if len(data) <= MAX_DATAGRAM or not ui["track"]:
                return data
            ui["track"] = ui["track"][:-4]

    def _send_frame(self, now: float, force: bool = False) -> None:
        if self.key is None or (self.sink is None and (self.transport is None or self.addr is None)):
            return
        ui = self.ui()
        changed = ui != self._last_ui
        if not (force or (changed and now - self._last_frame_t >= FRAME_MIN_GAP_S)
                or now - self._last_frame_t >= FRAME_HEARTBEAT_S):
            return
        self._frame_seq += 1
        self._last_ui, self._last_frame_t = ui, now
        data = self.frame(ui)
        try:
            if self.sink is not None:
                self.sink(data)
            else:
                self.transport.sendto(data, self.addr)
            self.stats["frames"] += 1
        except OSError as e:
            log.debug(f"board frame not sent: {e}")
