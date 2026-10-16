# =============================================================================
# hub/console.py — the console model (BUILD_ORDER §5.2–5.3). Pure logic, no I/O.
#
#   apply(state, intent, src)       one §5.2 intent -> state change (+ effects for
#                                   the daemon: bank re-binding, cue fades)
#   resolve(state, members, limits) console state -> one HSB per fixture
#                                   (HTP level, LTP hue/sat, master, blackout,
#                                   flash, per-fixture caps — D8)
#   seed_from_art(state, art, ...)  D2: what BOARD starts from (no colour jump)
#   StrobeLimiter                   D7: <= 2 x max_flash_hz edges per fixture
#   to_file / from_file             console.json (contracts/console.schema.json)
#
# Time is always passed in (ms); nothing here reads a clock or touches a device.
# =============================================================================

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import oklab
from outputs.base import HSB

BANKS = ("A", "B", "C")
CHANNELS_PER_BANK = 8
FADER_FNS = ("level", "hue", "sat")
SRCS = ("ui", "board", "replay", "cli")

HUE_PER_DETENT = 5.0          # contracts/board-map.md
SAT_PER_DETENT = 0.02
PICKUP_TOLERANCE = 0.02       # §5.3: engage within 0.02 of the stored value
EDGE_LEVEL = 0.10             # D7 strobe edge: |Δlevel| >= 0.10 ...
EDGE_COLOR = 0.10             # ... or an OKLab jump >= 0.10 while level >= 0.10
MAX_FLASH_HZ = 3.0            # §8.1
HUE_TOP = 359.9               # a hue fader at the top stays at the top

# gui-1 palette swatches and cue names (editable; persisted in console.json from M4).
DEFAULT_PALETTE = ["#e5484d", "#f5a05b", "#f2c94c", "#4ade80", "#2dd4bf", "#3b82f6", "#a855f7", "#f472b6"]
DEFAULT_CUE_NAMES = ["Intro", "Build", "DROP", "Chill", "Chorus", "Break", "Outro", "Blackout"]


class ConsoleError(ValueError):
    """A rejected intent (answered as an IPC error; never silently dropped)."""


@dataclass
class Channel:
    bank: str
    ch: int
    group_id: Optional[str] = None
    level: float = 0.0
    hue: float = 0.0
    sat: float = 0.0
    flash: bool = False
    engaged: bool = True          # the physical fader matches the stored value (false = ghost marker)
    level_seq: int = 0            # when level/flash last moved (HTP source after D2)
    color_seq: int = 0            # when hue/sat last moved (LTP)

    def to_state(self) -> dict:
        return {"bank": self.bank, "ch": self.ch, "group_id": self.group_id,
                "level": round(self.level, 4), "hue": round(self.hue % 360.0, 2),
                "sat": round(self.sat, 4), "flash": self.flash, "engaged": self.engaged}

    def look(self) -> dict:
        return {"level": round(self.level, 4), "hue": round(self.hue % 360.0, 2), "sat": round(self.sat, 4)}

    def fn_value(self, fn: str) -> float:
        """The stored value a fader controls in this fader function, as 0..1."""
        if fn == "level":
            return self.level
        return (self.hue % 360.0) / 360.0 if fn == "hue" else self.sat


@dataclass
class Cue:
    name: str
    look: Optional[list] = None      # 24 x {level, hue, sat} or None (empty)
    fade_ms: int = 0


@dataclass
class ConsoleState:
    bank: str = "A"
    fader_fn: str = "level"
    master: float = 1.0
    blackout: bool = False
    channels: list = field(default_factory=list)
    last_touched: Optional[tuple] = None      # (bank, ch)
    palette: list = field(default_factory=lambda: list(DEFAULT_PALETTE))
    cues: list = field(default_factory=lambda: [Cue(n) for n in DEFAULT_CUE_NAMES])
    cue_active: Optional[int] = None
    # not in the State contract:
    seq: int = 0                                                       # touch counter (LTP order)
    board_raw: list = field(default_factory=lambda: [None] * CHANNELS_PER_BANK)  # physical faders 1-8
    board_seq: int = 0                                                 # seq when BOARD was seeded (D2)
    art: dict = field(default_factory=dict)                            # fixture id -> HSB at the seed

    @classmethod
    def default(cls, banks: Optional[dict] = None) -> "ConsoleState":
        """24 channels; `banks` maps "A"/"B"/"C" -> 8 group ids (or None)."""
        banks = banks or {}
        chans = [Channel(b, i + 1, (banks.get(b) or [None] * 8)[i])
                 for b in BANKS for i in range(CHANNELS_PER_BANK)]
        return cls(channels=chans)

    def channel(self, bank: str, ch: int) -> Channel:
        return self.channels[BANKS.index(bank) * CHANNELS_PER_BANK + (ch - 1)]

    def to_state_fields(self) -> dict:
        lt = self.last_touched
        return {
            "bank": self.bank,
            "fader_fn": self.fader_fn,
            "master": round(self.master, 4),
            "blackout": self.blackout,
            "channels": [c.to_state() for c in self.channels],
            "last_touched": {"bank": lt[0], "ch": lt[1]} if lt else None,
            "cues": [{"id": i, "name": c.name, "stored": c.look is not None} for i, c in enumerate(self.cues)],
            "cue_active": self.cue_active,
            "palette": list(self.palette),
        }


# ---- intents (§5.2) ---------------------------------------------------------------------

def apply(st: ConsoleState, intent: dict, src: str = "ui") -> list:
    """Apply one intent. Returns effects for the caller:
    ("assign", bank, ch, group_id)  persist the binding in the rig's banks
    ("recall", i, fade_ms)          a cue was recalled (the scheduler may fade)
    Raises ConsoleError for anything it cannot apply (`who` belongs to the daemon)."""
    if not isinstance(intent, dict) or not isinstance(intent.get("intent"), str):
        raise ConsoleError("intent must be an object with an 'intent' name")
    if src not in SRCS:
        raise ConsoleError(f"unknown src {src!r}")
    name = intent["intent"]
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ConsoleError(f"intent {name!r} is not a console intent" if name == "who"
                           else f"unknown intent {name!r}")
    return handler(st, intent, src) or []


def _ch(intent: dict) -> int:
    ch = intent.get("ch")
    if isinstance(ch, bool) or not isinstance(ch, int) or not 1 <= ch <= CHANNELS_PER_BANK:
        raise ConsoleError(f"ch must be 1-{CHANNELS_PER_BANK}")
    return ch


def _unit(x, what: str = "v") -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or math.isnan(x) or not 0.0 <= x <= 1.0:
        raise ConsoleError(f"{what} must be 0..1")
    return float(x)


def _slot(intent: dict) -> int:
    i = intent.get("i")
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i <= 7:
        raise ConsoleError("i must be 0-7")
    return i


def _flag(intent: dict, key: str) -> bool:
    v = intent.get(key)
    if not isinstance(v, bool):
        raise ConsoleError(f"{key} must be true or false")
    return v


def _touch(st: ConsoleState, c: Channel, level: bool = False, color: bool = False) -> None:
    st.seq += 1
    if level:
        c.level_seq = st.seq
    if color:
        c.color_seq = st.seq
    st.last_touched = (c.bank, c.ch)


def picked_up(stored: float, prev_raw: Optional[float], raw: float) -> bool:
    """§5.3 pickup: the physical fader reaches (within 0.02) or crosses the stored value."""
    if abs(raw - stored) <= PICKUP_TOLERANCE:
        return True
    return prev_raw is not None and (prev_raw - stored) * (raw - stored) <= 0


def _recheck_pickup(st: ConsoleState, c: Channel) -> None:
    """The stored value moved (or the bank / function changed) under a physical fader:
    it stays engaged only if it still matches. With no board position seen yet there is
    nothing to pick up; the first board move checks for itself."""
    if c.bank != st.bank:
        return
    raw = st.board_raw[c.ch - 1]
    c.engaged = raw is None or abs(raw - c.fn_value(st.fader_fn)) <= PICKUP_TOLERANCE


def _disengage_all(st: ConsoleState) -> None:
    """§5.3: on a bank or fader-function change every channel is disengaged; the current
    bank's channels re-engage at once where the physical fader already matches."""
    for c in st.channels:
        c.engaged = False
        _recheck_pickup(st, c)


def _fader(st: ConsoleState, intent: dict, src: str) -> None:
    v = _unit(intent.get("v"))
    if intent.get("ch") == "master":
        st.master = v
        return
    c = st.channel(st.bank, _ch(intent))
    fn = st.fader_fn
    if src == "board":
        prev, st.board_raw[c.ch - 1] = st.board_raw[c.ch - 1], v
        if prev is None and abs(v - c.fn_value(fn)) > PICKUP_TOLERANCE:
            c.engaged = False                      # first sight of this fader: never jump to it
        if not c.engaged:
            if not picked_up(c.fn_value(fn), prev, v):
                return                             # ignored until it picks up (ghost marker)
            c.engaged = True
    if fn == "level":
        c.level = v
    elif fn == "hue":
        c.hue = min(v * 360.0, HUE_TOP)            # the top of the fader is 359.9°, not 0°
    else:
        c.sat = v
    _touch(st, c, level=fn == "level", color=fn != "level")
    st.cue_active = None
    if src != "board":                             # UI drags apply at once; the board may now differ
        _recheck_pickup(st, c)


def _flash(st: ConsoleState, intent: dict, src: str) -> None:
    c = st.channel(st.bank, _ch(intent))
    down = _flag(intent, "down")
    c.flash = down
    if down:                                       # the flashing channel's colour wins (LTP)
        _touch(st, c, level=True, color=True)


def _assign(st: ConsoleState, intent: dict, src: str) -> list:
    c = st.channel(st.bank, _ch(intent))
    gid = intent.get("group_id")
    if gid is not None and (not isinstance(gid, str) or not gid):
        raise ConsoleError("group_id must be a group id or null")
    st.last_touched = (c.bank, c.ch)               # X2: no group_id = select only
    if "group_id" not in intent:
        return []
    c.group_id = gid
    return [("assign", c.bank, c.ch, gid)]


def _bank(st: ConsoleState, intent: dict, src: str) -> None:
    b = intent.get("bank")
    if b not in BANKS:
        raise ConsoleError("bank must be A, B or C")
    if b != st.bank:
        st.bank = b
        _disengage_all(st)


def _fader_fn(st: ConsoleState, intent: dict, src: str) -> None:
    fn = intent.get("fn")
    if fn not in FADER_FNS:
        raise ConsoleError("fn must be level, hue or sat")
    if fn != st.fader_fn:
        st.fader_fn = fn
        _disengage_all(st)


def _target(st: ConsoleState) -> Channel:
    if st.last_touched is None:
        raise ConsoleError("touch a channel first (fader, FLASH or ASSIGN)")
    return st.channel(*st.last_touched)


def _delta_or_v(intent: dict, what: str) -> tuple:
    has_d, has_v = "delta" in intent, "v" in intent
    if has_d == has_v:
        raise ConsoleError(f"{what} needs exactly one of delta or v")
    if has_d:
        d = intent["delta"]
        if isinstance(d, bool) or not isinstance(d, int):
            raise ConsoleError("delta must be an integer (detents)")
        return d, None
    return None, intent["v"]


def _hue(st: ConsoleState, intent: dict, src: str) -> None:
    d, v = _delta_or_v(intent, "hue")
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 360):
        raise ConsoleError("hue v must be 0..360")
    c = _target(st)
    c.hue = (c.hue + d * HUE_PER_DETENT) % 360.0 if d is not None else float(v)
    _touch(st, c, color=True)
    st.cue_active = None
    _recheck_pickup(st, c)


def _sat(st: ConsoleState, intent: dict, src: str) -> None:
    d, v = _delta_or_v(intent, "sat")
    if v is not None:
        v = _unit(v)
    c = _target(st)
    c.sat = min(1.0, max(0.0, c.sat + d * SAT_PER_DETENT)) if d is not None else v
    _touch(st, c, color=True)
    st.cue_active = None
    _recheck_pickup(st, c)


def _hex_to_hsb(hx: str) -> HSB:
    return HSB.from_rgb(tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5)))


def _palette_apply(st: ConsoleState, intent: dict, src: str) -> None:
    i = _slot(intent)
    c = _target(st)
    col = _hex_to_hsb(st.palette[i])
    c.hue, c.sat = col.h % 360.0, col.s
    _touch(st, c, color=True)
    st.cue_active = None
    _recheck_pickup(st, c)


def _palette_store(st: ConsoleState, intent: dict, src: str) -> None:
    i = _slot(intent)
    c = _target(st)
    st.palette[i] = HSB(c.hue, c.sat, 1.0).hex()


def _cue_store(st: ConsoleState, intent: dict, src: str) -> None:
    i = _slot(intent)
    st.cues[i].look = [c.look() for c in st.channels]
    st.cue_active = i


def _cue_recall(st: ConsoleState, intent: dict, src: str) -> list:
    i = _slot(intent)
    cue = st.cues[i]
    if cue.look is None:
        raise ConsoleError(f"cue {i + 1} ({cue.name}) is empty")
    st.seq += 1                                    # one touch for the whole look; ties break by level
    for c, look in zip(st.channels, cue.look):
        c.level, c.hue, c.sat = look["level"], look["hue"] % 360.0, look["sat"]
        c.level_seq = c.color_seq = st.seq
        _recheck_pickup(st, c)
    st.cue_active = i
    return [("recall", i, cue.fade_ms)]


def _blackout(st: ConsoleState, intent: dict, src: str) -> None:
    st.blackout = _flag(intent, "on")


_HANDLERS: dict = {
    "fader": _fader, "flash": _flash, "assign": _assign, "bank": _bank, "fader_fn": _fader_fn,
    "hue": _hue, "sat": _sat, "palette_apply": _palette_apply, "palette_store": _palette_store,
    "cue_recall": _cue_recall, "cue_store": _cue_store, "blackout": _blackout,
}


# ---- arbitration (§5.3) ------------------------------------------------------------------

def resolve(st: ConsoleState, members: dict, limits: Callable[[str], tuple]) -> dict:
    """fixture id -> HSB for every fixture in a group bound to a channel (others hold — D16).
    `members`: group id -> [fixture ids]; `limits(fid)` -> (max_level, max_flash_hz) (D8).
    Level = HTP (flash = 1.0) across the fixture's channels, hue/sat = LTP. After D2
    seeding the art colour / brightness stand until a channel moves colour / level, and
    only channels moved since the seed take part (seeded values are display/pickup only).
    Then x master, blackout -> 0 (beats flash), capped at max_level (flash cannot exceed)."""
    by_fixture: dict = {}
    for c in st.channels:
        for fid in (members.get(c.group_id, ()) if c.group_id else ()):
            by_fixture.setdefault(fid, []).append(c)
    out = {}
    for fid, chans in by_fixture.items():
        art = st.art.get(fid)
        if art is None:
            level = max(1.0 if c.flash else c.level for c in chans)
        else:                                      # seeded levels are display-only (D2)
            moved = [c for c in chans if c.level_seq > st.board_seq or c.flash]
            level = max(1.0 if c.flash else c.level for c in moved) if moved else art.b
        if art is not None and not any(c.color_seq > st.board_seq for c in chans):
            h, s = art.h, art.s
        else:
            src = max(chans, key=lambda c: (c.color_seq, 1.0 if c.flash else c.level))
            h, s = src.hue, src.sat
        max_level, _hz = limits(fid)
        level = 0.0 if st.blackout else min(level * st.master, max_level)
        out[fid] = HSB(h % 360.0, s, level)
    return out


def seed_from_art(st: ConsoleState, art: dict, members: dict) -> None:
    """D2 — switching to BOARD pushes nothing: each fixture keeps its art colour and
    brightness until a channel moves it. For display and pickup, each bound channel shows
    the OKLab mean hue/sat of its group's art colours and their mean brightness."""
    st.art = dict(art)
    st.seq += 1
    st.board_seq = st.seq
    for c in st.channels:
        cols = [art[f] for f in members.get(c.group_id, ()) if f in art] if c.group_id else []
        if cols:
            c.hue, c.sat = oklab.mean_hue_sat(cols)
            c.level = sum(x.b for x in cols) / len(cols)
            _recheck_pickup(st, c)


# ---- strobe limit (D7, §8.1) --------------------------------------------------------------

def is_edge(a: Optional[HSB], b: HSB) -> bool:
    """A visible on/off-like change: |Δlevel| >= 0.10, or an OKLab colour jump >= 0.10
    while both ends are at least 0.10 bright."""
    if a is None:
        return False
    if abs(a.b - b.b) >= EDGE_LEVEL - 1e-9:
        return True
    return min(a.b, b.b) >= EDGE_LEVEL and oklab.distance(a, b) >= EDGE_COLOR


def edge_budget(max_hz: float) -> tuple:
    """(edges, window_ms): 2 edges per on/off cycle; 3 Hz = 6 edges in any 1 s (D7).
    Below 0.5 Hz: one edge per 1 / (2 x hz) s. Never above 3 Hz."""
    hz = min(MAX_FLASH_HZ, max_hz) if max_hz and max_hz > 0 else MAX_FLASH_HZ
    n = max(1, int(math.floor(2 * hz + 1e-9)))
    return n, 1000.0 * n / (2 * hz)


class StrobeLimiter:
    """Per fixture, after caps and master. A value that would exceed the edge budget is
    held; the newest held value goes out as soon as the budget allows (`poll`).
    `dark=True` (BLACKOUT on) sends a brightness-0 value at once even over budget; the edge
    still counts, so coming back up waits (user decision 2026-10-16: never delay dark)."""

    def __init__(self):
        self.sent: dict = {}          # fid -> last HSB sent
        self.edges: dict = {}         # fid -> deque of edge times (ms)
        self.pending: dict = {}       # fid -> (HSB, max_hz)

    def offer(self, fid: str, hsb: HSB, now_ms: float, max_hz: float,
              dark: bool = False) -> Optional[HSB]:
        """The value to send now, or None: held (see `pending`) or already showing."""
        if hsb == self.sent.get(fid):
            self.pending.pop(fid, None)
            return None
        if is_edge(self.sent.get(fid), hsb):
            n, window = edge_budget(max_hz)
            q = self.edges.setdefault(fid, deque())
            while q and q[0] <= now_ms - window:
                q.popleft()
            if len(q) >= n and not (dark and hsb.b <= 0.0):
                self.pending[fid] = (hsb, max_hz)
                return None
            q.append(now_ms)
        self.pending.pop(fid, None)
        self.sent[fid] = hsb
        return hsb

    def poll(self, now_ms: float) -> dict:
        """Held values that may go out now."""
        out = {}
        for fid, (hsb, hz) in list(self.pending.items()):
            v = self.offer(fid, hsb, now_ms, hz)
            if v is not None:
                out[fid] = v
        return out

    def next_due_ms(self) -> Optional[float]:
        """When the earliest held value can go out (for the scheduler's timer)."""
        due = [self.edges[f][0] + edge_budget(hz)[1] for f, (_h, hz) in self.pending.items() if self.edges.get(f)]
        return min(due) if due else None


# ---- console.json -------------------------------------------------------------------------

def to_file(st: ConsoleState) -> dict:
    out = {"v": 1, "bank": st.bank, "fader_fn": st.fader_fn, "master": round(st.master, 4),
           "channels": [c.look() for c in st.channels], "palette": list(st.palette), "cues": []}
    for cue in st.cues:
        d = {"name": cue.name, "look": cue.look}
        if cue.fade_ms:
            d["fade_ms"] = cue.fade_ms
        out["cues"].append(d)
    return out


def from_file(d: dict, banks: Optional[dict] = None) -> ConsoleState:
    """Restore a saved console (bindings come from the rig's banks). Flash, blackout and
    who are never restored; no physical fader position is known yet."""
    st = ConsoleState.default(banks)
    st.bank = d.get("bank") if d.get("bank") in BANKS else "A"
    st.fader_fn = d.get("fader_fn") if d.get("fader_fn") in FADER_FNS else "level"
    st.master = min(1.0, max(0.0, float(d.get("master", 1.0))))
    for c, look in zip(st.channels, d.get("channels") or []):
        c.level, c.hue, c.sat = float(look["level"]), float(look["hue"]) % 360.0, float(look["sat"])
    if isinstance(d.get("palette"), list) and len(d["palette"]) == 8:
        st.palette = list(d["palette"])
    for cue, saved in zip(st.cues, d.get("cues") or []):
        cue.name = saved.get("name", cue.name)
        cue.look = saved.get("look")
        cue.fade_ms = int(saved.get("fade_ms", 0))
    return st
