# =============================================================================
# hub/takes.py — takes: recordings of console performances (BUILD_ORDER §5.4,
# DECISIONS D9/D10).
#
#   TakeStore                     $CHROMA_HOME/takes/<id>_<slug>.jsonl (header line +
#                                 one event per line), atomic rewrites
#   resolve_event(state, i, src)  D9: the resolved b/c/a/v (+ g) beside the verbatim
#                                 intent, read from the console state after apply
#   Recorder                      events stamped with the SongClock; REPLACE / MERGE /
#                                 OVERDUB (punch-in by channel touch spans) at finish
#   Replayer                      which events are due at a song position (nudge +
#                                 global offset), seek, skip predicate for OVERDUB
#   activity_lanes(...)           event spans per group for the Console strip
#
# Pure except TakeStore (files). Time is always passed in. The daemon wiring lives
# in TakeService (bottom), which the daemon owns.
# =============================================================================

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import console as C
from outputs.base import HSB

log = logging.getLogger("chroma-hub")

MODES = ("replace", "overdub", "merge")
TOUCH_MS = 750                    # D10: a channel stays "touched" 750 ms after live input
FLASH_SPAN_MIN_MS = 60            # activity strip: shortest visible span
SPAN_PAD_MS = 150                 # a single event shows as a short bar
SPAN_JOIN_MS = 400                # bars closer than this merge
MAX_SPANS = 200                   # per lane (contract limit)
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}(-[0-9]{1,3})?$")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "take"


# ---- D9: resolved events --------------------------------------------------------------

def _chev(c, a: str, v) -> dict:
    return {"g": c.group_id, "b": c.bank, "c": c.ch, "a": a, "v": v}


def resolve_event(st, intent: dict, src: str) -> Optional[dict]:
    """b/c/a/v (+ g) for an intent that `console.apply` just applied to `st`, or None when
    it is not recorded: `who`, `palette_store`, `cue_store` (editing, not performing) and a
    board fader move that did not pick up."""
    name = intent.get("intent")
    if name in ("who", "palette_store", "cue_store"):
        return None
    if name == "fader":
        if intent.get("ch") == "master":
            return {"c": "master", "a": "master", "v": round(st.master, 4)}
        c = st.channel(st.bank, intent["ch"])
        if src == "board" and not c.engaged:
            return None
        fn = st.fader_fn
        v = c.level if fn == "level" else (c.hue % 360.0 if fn == "hue" else c.sat)
        return _chev(c, fn, round(v, 2 if fn == "hue" else 4))
    if name == "flash":
        c = st.channel(st.bank, intent["ch"])
        return _chev(c, "flash", bool(intent["down"]))
    if name == "assign":
        c = st.channel(st.bank, intent["ch"])
        if "group_id" in intent:
            return _chev(c, "assign", intent["group_id"])
        return _chev(c, "select", None)
    if name == "bank":
        return {"b": st.bank, "a": "bank", "v": st.bank}
    if name == "fader_fn":
        return {"a": "fader_fn", "v": st.fader_fn}
    if name in ("hue", "sat", "palette_apply"):
        c = st.channel(*st.last_touched)
        if name == "hue":
            return _chev(c, "hue", round(c.hue % 360.0, 2))
        if name == "sat":
            return _chev(c, "sat", round(c.sat, 4))
        return _chev(c, "palette", st.palette[intent["i"]])
    if name == "cue_recall":
        return {"a": "cue", "v": intent["i"]}
    if name == "blackout":
        return {"a": "blackout", "v": st.blackout}
    return None


def touch_key(ev: dict):
    """What an event 'touches' for OVERDUB (D10): its channel, or master / blackout / cue."""
    c = ev.get("c")
    if isinstance(c, int) and not isinstance(c, bool) and ev.get("b"):
        return (ev["b"], c)
    return ev.get("a") or (ev.get("i") or {}).get("intent")


# ---- take files --------------------------------------------------------------------------

def read_take(path: str) -> tuple:
    with open(path) as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty take file")
    header = json.loads(lines[0])
    events = [json.loads(ln) for ln in lines[1:]]
    return header, events


def write_take(path: str, header: dict, events: list) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n")
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def take_stats(header: dict, events: list) -> dict:
    groups = []
    for ev in events:
        g = ev.get("g")
        if g and ev.get("a") not in ("select", "assign") and g not in groups:
            groups.append(g)
    last = max((ev["p"] for ev in events), default=0)
    length = header.get("dur_ms") or last
    return {"events": len(events), "groups_used": groups, "length_ms": int(max(length, last))}


class TakeError(ValueError):
    """A take op that cannot be done (answered as an IPC error)."""


class TakeStore:
    """The take library. Headers are cached by file mtime; events are read on demand."""

    def __init__(self, root: str):
        self.root = root
        self._cache: dict = {}        # path -> (mtime_ns, header)

    def _ensure(self) -> None:
        os.makedirs(self.root, exist_ok=True)

    def paths(self) -> dict:
        """take id -> path."""
        out = {}
        try:
            names = os.listdir(self.root)
        except FileNotFoundError:
            return out
        for n in names:
            if not n.endswith(".jsonl") or n.startswith("."):
                continue
            tid = n[:-6].split("_", 1)[0]
            if ID_RE.match(tid):
                out[tid] = os.path.join(self.root, n)
        return out

    def path(self, take_id: str) -> str:
        p = self.paths().get(take_id) if isinstance(take_id, str) else None
        if p is None:
            raise TakeError(f"no take {take_id!r}")
        return p

    def header(self, path: str) -> Optional[dict]:
        try:
            mt = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            return None
        hit = self._cache.get(path)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            with open(path) as fh:
                h = json.loads(fh.readline())
        except (OSError, ValueError) as e:
            log.warning(f"take {os.path.basename(path)} unreadable: {e}")
            return None
        self._cache[path] = (mt, h)
        return h

    def list(self) -> list:
        """Summaries (ipc-response take_summary), newest first."""
        out = []
        for tid, p in self.paths().items():
            h = self.header(p)
            if h is None:
                continue
            st = h.get("stats") or {}
            out.append({"id": h.get("id", tid), "name": h.get("name", ""), "title": h.get("title", ""),
                        "artist": h.get("artist", ""), "album": h.get("album", ""), "dur_ms": h.get("dur_ms"),
                        "created_ms": int(h.get("created_ms") or 0), "events": int(st.get("events", 0)),
                        "groups_used": list(st.get("groups_used", [])), "training": bool(h.get("training")),
                        "art_hex": h.get("art_hex"), "track_id": h.get("track_id", "")})
        out.sort(key=lambda s: (s["created_ms"], s["id"]), reverse=True)
        return out

    def get(self, take_id: str) -> tuple:
        return read_take(self.path(take_id))

    def new_id(self, now_s: float) -> str:
        base = time.strftime("%Y%m%d-%H%M%S", time.localtime(now_s))
        taken = set(self.paths())
        if base not in taken:
            return base
        for n in range(1, 1000):
            if f"{base}-{n}" not in taken:
                return f"{base}-{n}"
        raise TakeError("too many takes in one second")

    def save(self, header: dict, events: list) -> str:
        """Write a take (new or existing); the header's stats are recomputed."""
        self._ensure()
        events = sorted(events, key=lambda e: e["p"])     # stable: keeps the order at equal p
        header["stats"] = take_stats(header, events)
        try:
            path = self.path(header["id"])
        except TakeError:
            path = os.path.join(self.root, f"{header['id']}_{slugify(header.get('title', ''))}.jsonl")
        write_take(path, header, events)
        return path

    def update_header(self, take_id: str, fn: Callable[[dict], None]) -> dict:
        header, events = self.get(take_id)
        fn(header)
        self.save(header, events)
        return header

    def delete(self, take_id: str) -> None:
        """Moved to takes/.deleted/ (recoverable by hand), never unlinked."""
        p = self.path(take_id)
        trash = os.path.join(self.root, ".deleted")
        os.makedirs(trash, exist_ok=True)
        os.replace(p, os.path.join(trash, os.path.basename(p)))
        self._cache.pop(p, None)

    def duplicate(self, take_id: str, now_s: float) -> str:
        header, events = self.get(take_id)
        header = dict(header)
        header["id"] = self.new_id(now_s)
        header["name"] = (header.get("name") or header.get("title") or "Take")[:59] + " copy"
        self.save(header, events)
        return header["id"]


# ---- recording ------------------------------------------------------------------------

@dataclass
class Recorder:
    """One recording pass. `old` = the take's existing events (MERGE / OVERDUB / REPLACE
    into an existing take); `events` = what was recorded live in this pass."""
    header: dict
    mode: str = "replace"
    old: list = field(default_factory=list)
    events: list = field(default_factory=list)
    spans: dict = field(default_factory=dict)       # touch key -> [[p0, p1], ...] (OVERDUB)
    start_p: int = 0
    start_mono: float = 0.0

    def add(self, p_ms: float, w_ms: int, intent: dict, resolved: dict) -> dict:
        ev = {"p": max(0, int(round(p_ms))), "w": int(w_ms), "i": intent}
        ev.update({k: v for k, v in resolved.items()})
        if ev.get("g", "") is None:
            ev.pop("g")
        self.events.append(ev)
        k = touch_key(ev)
        sp = self.spans.setdefault(k, [])
        p0, p1 = ev["p"], ev["p"] + TOUCH_MS
        if sp and p0 <= sp[-1][1]:
            sp[-1][1] = max(sp[-1][1], p1)
        else:
            sp.append([p0, p1])
        return ev

    def touched(self, key, p_ms: float) -> bool:
        """OVERDUB: is `key` under live control at song position `p_ms`?"""
        for p0, p1 in self.spans.get(key, ()):
            if p0 <= p_ms <= p1:
                return True
        return False

    def groups_touched(self) -> int:
        return len({e.get("g") for e in self.events if e.get("g")})

    def finish(self) -> list:
        """The take's events after this pass (D10)."""
        if self.mode == "replace" or not self.old:
            return list(self.events)
        if self.mode == "merge":
            return sorted(self.old + self.events, key=lambda e: (e["p"], e.get("w", 0)))
        # OVERDUB: punch-in. Old events of a touched channel inside its spans are replaced;
        # a flash the old take holds at a span's start is released there (before the live
        # events at that position), so a press whose release was punched out never sticks.
        old = sorted(self.old, key=lambda e: (e["p"], e.get("w", 0)))
        releases = []
        for k, spans in self.spans.items():
            if not isinstance(k, tuple):
                continue
            flashes = [e for e in old if touch_key(e) == k and e.get("a") == "flash"]
            for p0, _p1 in spans:
                before = [e for e in flashes if e["p"] < p0]
                if before and before[-1].get("v") is True:
                    rel = {"p": p0, "w": before[-1].get("w", 0), "i": {"intent": "flash", "ch": k[1], "down": False},
                           "b": k[0], "c": k[1], "a": "flash", "v": False}
                    if before[-1].get("g"):
                        rel["g"] = before[-1]["g"]
                    releases.append(rel)
        kept = [e for e in old if not self.touched(touch_key(e), e["p"])]
        tagged = [(e["p"], 1, e.get("w", 0), n, e) for n, e in enumerate(kept + self.events)]
        tagged += [(e["p"], 0, 0, n, e) for n, e in enumerate(releases)]
        return [t[-1] for t in sorted(tagged, key=lambda t: t[:4])]


# ---- replay -----------------------------------------------------------------------------

class Replayer:
    """Which events are due at a song position. An event at `p` plays when the song is at
    `p + nudge_ms + offset_ms` (positive nudge = later)."""

    def __init__(self, events: list, nudge_ms: int = 0, offset_ms: int = 0):
        self.events = sorted(events, key=lambda e: e["p"])     # stable: keeps the order at equal p
        self._ps = [e["p"] for e in self.events]
        self.shift = int(nudge_ms) + int(offset_ms)
        self.i = 0

    def set_shift(self, nudge_ms: int, offset_ms: int = 0) -> None:
        self.shift = int(nudge_ms) + int(offset_ms)

    def due(self, pos_ms: float) -> list:
        """Events not yet played whose time has come (in order)."""
        out = []
        while self.i < len(self.events) and self.events[self.i]["p"] + self.shift <= pos_ms:
            out.append(self.events[self.i])
            self.i += 1
        return out

    def seek(self, pos_ms: float) -> list:
        """Start over at `pos_ms`: the events up to it (to rebuild the look, no output)."""
        self.i = bisect.bisect_right(self._ps, pos_ms - self.shift)
        return self.events[:self.i]

    def next_due(self) -> Optional[float]:
        """Song position of the next event, or None when the take is played out."""
        if self.i >= len(self.events):
            return None
        return self.events[self.i]["p"] + self.shift


# ---- activity strip -------------------------------------------------------------------

def activity_lanes(events: list, groups: dict, order: Optional[list] = None) -> list:
    """Event spans per group (State.activity.lanes): each event is a short bar; flash
    down..up is one bar; bars closer than SPAN_JOIN_MS merge; at most MAX_SPANS per lane
    (the latest kept)."""
    by_g: dict = {}
    flash_at: dict = {}
    for ev in events:
        g = ev.get("g")
        if not g or ev.get("a") in ("select", "assign"):
            continue
        p = int(ev["p"])
        if ev.get("a") == "flash":
            k = (ev.get("b"), ev.get("c"))
            if ev.get("v"):
                flash_at[k] = p
                continue
            p0 = flash_at.pop(k, p)
            span = [p0, max(p, p0 + FLASH_SPAN_MIN_MS)]
        else:
            span = [p, p + SPAN_PAD_MS]
        by_g.setdefault(g, []).append(span)
    for (b, c), p0 in flash_at.items():                     # still held
        g = next((e.get("g") for e in reversed(events) if e.get("b") == b and e.get("c") == c), None)
        if g:
            by_g.setdefault(g, []).append([p0, p0 + FLASH_SPAN_MIN_MS])
    lanes = []
    for g in (order or []) + [g for g in by_g if g not in (order or [])]:
        if g not in by_g:
            continue
        spans = sorted(by_g[g])
        merged: list = []
        for s in spans:
            if merged and s[0] - merged[-1][1] <= SPAN_JOIN_MS:
                merged[-1][1] = max(merged[-1][1], s[1])
            else:
                merged.append(list(s))
        lanes.append({"group_id": g, "color": (groups.get(g) or {}).get("color", "#888888"),
                      "spans": merged[-MAX_SPANS:]})
    return lanes[:24]


# ---- daemon wiring ----------------------------------------------------------------------

class TakeService:
    """Recording and playback inside chromad. Owns the TakeStore, the current Recorder and
    Replayer; State's rec / play / activity / takes_rev come from here."""

    TICK_MAX_S = 0.05             # the replay loop re-reads the SongClock at least every 50 ms

    def __init__(self, d, root: str, offset_ms: int = 0):
        self.d = d
        self.store = TakeStore(root)
        self.offset_ms = int(offset_ms)
        self.rev = 0
        self.rec: Optional[Recorder] = None
        self.armed: Optional[dict] = None             # {"take_id", "mode"} waiting for playback
        self.play_take: Optional[dict] = None         # header of the take being played
        self.replayer: Optional[Replayer] = None
        self.loop = False
        self._kick = asyncio.Event()
        self._activity_cache: Optional[tuple] = None

    # ---- State -------------------------------------------------------------------------
    def state_fields(self) -> dict:
        d = self.d
        pos = d.song.pos_ms()
        if self.rec is not None:
            h = self.rec.header
            rec = {"state": "recording", "take_id": h["id"], "mode": self.rec.mode, "name": h.get("name", ""),
                   "elapsed_ms": max(0, int((d.clock.monotonic() - self.rec.start_mono) * 1000)),
                   "events": len(self.rec.events), "groups_touched": self.rec.groups_touched()}
        else:
            a = self.armed or {}
            rec = {"state": "armed" if self.armed else "idle", "take_id": a.get("take_id"),
                   "mode": a.get("mode", "replace"), "name": a.get("name", ""), "elapsed_ms": 0, "events": 0,
                   "groups_touched": 0}
        if self.play_take is not None:
            play = {"state": "playing", "take_id": self.play_take["id"], "pos_ms": int(pos or 0), "loop": self.loop}
        else:
            play = {"state": "idle", "take_id": None, "pos_ms": 0, "loop": self.loop}
        return {"rec": rec, "play": play, "takes_rev": self.rev, "activity": self.activity()}

    def activity(self) -> dict:
        if self.rec is not None:
            h = self.rec.header
            evs = (self.rec.old if self.rec.mode == "overdub" else []) + self.rec.events
            key = ("rec", h["id"], len(self.rec.events))
            src = "rec"
        elif self.play_take is not None:
            h = self.play_take
            evs = self.replayer.events if self.replayer else []
            key = ("play", h["id"], len(evs))
            src = "play"
        else:
            return {"source": None, "dur_ms": None, "lanes": []}
        if self._activity_cache and self._activity_cache[0] == key:
            return self._activity_cache[1]
        order = [c["group_id"] for c in (h.get("init") or {}).get("channels", []) if c.get("group_id")]
        out = {"source": src, "dur_ms": h.get("dur_ms") or self.d.song.dur_ms,
               "lanes": activity_lanes(evs, h.get("groups") or {}, list(dict.fromkeys(order)))}
        self._activity_cache = (key, out)
        return out

    def changed(self, library: bool = False) -> None:
        if library:
            self.rev += 1
        self.d.ipc.mark_dirty()

    # ---- now playing ----------------------------------------------------------------------
    def _np(self):
        return self.d.state.latest_np

    def _playing_track(self) -> bool:
        np, song = self._np(), self.d.song
        return np is not None and np.has_track() and song.track_id is not None and song.playing

    def _matches(self, header: dict) -> bool:
        np = self._np()
        if np is None or not np.has_track():
            return False
        if header.get("track_id") and header["track_id"] == np.track_id():
            return True
        return (header.get("title"), header.get("artist")) == (np.title or "", np.artist or "")

    # ---- recording ------------------------------------------------------------------------
    def _groups_meta(self) -> dict:
        out = {}
        for c in self.d.console.channels:
            g = self.d.store.groups.get(c.group_id) if c.group_id else None
            if g is not None and g.id not in out:
                out[g.id] = {"name": g.name, "color": g.color}
        return out

    def _art_hex(self) -> Optional[str]:
        try:
            looks = list(self.d.art_look().values())
        except Exception:
            looks = []
        if not looks:
            return None
        best = max(looks, key=lambda h: h.s * max(h.b, 0.2))
        return HSB(best.h, best.s, 1.0).hex()

    def arm(self, take_id: Optional[str], mode: str) -> None:
        if self.rec is not None:
            raise TakeError("already recording")
        mode = mode or ("replace" if take_id is None else "merge")
        if mode not in MODES:
            raise TakeError(f"mode must be one of {', '.join(MODES)}")
        name = ""
        if take_id is not None:
            name = self.store.get(take_id)[0].get("name", "")
        self.armed = {"take_id": take_id, "mode": mode, "name": name}
        if self._playing_track():
            self.start(take_id, mode)
        else:
            self.changed()

    def start(self, take_id: Optional[str], mode: Optional[str]) -> dict:
        d = self.d
        if self.rec is not None:
            raise TakeError("already recording")
        if not self._playing_track():
            raise TakeError("nothing is playing: start a song on the Apple TV, or arm REC to start with it")
        mode = mode or ("replace" if take_id is None else "merge")
        if mode not in MODES:
            raise TakeError(f"mode must be one of {', '.join(MODES)}")
        np = self._np()
        now_s = d.clock.time()
        if take_id is None:
            header = {"v": 1, "id": self.store.new_id(now_s), "name": "", "track_id": np.track_id(),
                      "title": np.title or "", "artist": np.artist or "", "album": np.album or "",
                      "dur_ms": d.song.dur_ms, "fixtures_rev": d.store.rev, "nudge_ms": 0, "training": True,
                      "markers": [], "created_ms": int(now_s * 1000), "performer": "You", "sync_err_ms": None,
                      "art_hex": self._art_hex()}
            n = 1 + sum(1 for s in self.store.list() if s.get("track_id") == header["track_id"])
            header["name"] = f"{header['title'] or 'Take'} — draft {n:02d}"[:64]
            old = []
            mode = "replace"
        else:
            header, old = self.store.get(take_id)
            if not self._matches(header):
                raise TakeError(f"this take is for “{header.get('title')}”; play that song to record into it")
        if mode == "replace" or not old:
            header["init"] = C.snapshot_init(d.console)
            header["groups"] = self._groups_meta()
        else:
            header.setdefault("groups", {}).update({k: v for k, v in self._groups_meta().items()
                                                    if k not in header["groups"]})
        d.ensure_board("recording")
        d.song.reset_error_stats()
        self.armed = None
        self.rec = Recorder(header=header, mode=mode, old=old, start_p=int(d.song.pos_ms() or 0),
                            start_mono=d.clock.monotonic())
        if mode == "overdub" and old:
            self._start_replay(header, old, d.song.pos_ms() or 0)
        self._activity_cache = None
        self.changed()
        log.info(f"recording take {header['id']} ({mode})")
        return {"take_id": header["id"]}

    def record(self, intent: dict, resolved: dict) -> None:
        """daemon.apply_intent hook: one live intent (src != replay) after console.apply."""
        r = self.rec
        if r is None:
            return
        pos = self.d.song.pos_ms()
        if pos is None:
            return
        ev = r.add(pos, int(self.d.clock.time() * 1000), {k: v for k, v in intent.items() if k != "src"}, resolved)
        if r.mode == "overdub" and self.replayer is not None:
            self._release_replayed_flash(touch_key(ev))
        self.changed()

    def stop_rec(self, reason: str = "") -> Optional[str]:
        r = self.rec
        if r is None:
            if self.armed is not None:
                self.armed = None
                self.changed()
            return None
        self.rec = None
        events = r.finish()
        h = r.header
        err = float(self.d.song.max_err_ms)
        if r.mode != "replace" and r.old:
            err = max(err, float(h.get("sync_err_ms") or 0))
        h["sync_err_ms"] = round(err, 1)
        if h.get("dur_ms") is None:
            h["dur_ms"] = self.d.song.dur_ms
        try:
            self.store.save(h, events)
        except OSError as e:
            log.warning(f"could not save take {h['id']}: {e}")
            self.d.ipc.toast("error", f"Could not save the take: {e}")
        if r.mode == "overdub" and self.play_take is not None and self.play_take["id"] == h["id"]:
            self.stop_play()
        self._activity_cache = None
        self.changed(library=True)
        log.info(f"take {h['id']} saved: {len(events)} events ({len(r.events)} new){' — ' + reason if reason else ''}")
        if reason:
            self.d.ipc.toast("info", f"Recording stopped ({reason}); saved “{h.get('name')}”")
        return h["id"]

    # ---- playback -------------------------------------------------------------------------
    def play(self, take_id: str, pos_ms: Optional[int] = None, loop: Optional[bool] = None) -> None:
        if self.rec is not None:
            raise TakeError("stop recording first")
        header, events = self.store.get(take_id)
        if not self._matches(header):
            raise TakeError(f"this take is for “{header.get('title')}”; play that song on the Apple TV first")
        if loop is not None:
            self.loop = bool(loop)
        if header.get("fixtures_rev") is not None and header["fixtures_rev"] != self.d.store.rev:
            log.info(f"take {take_id}: rig changed since recording (rev {header['fixtures_rev']} → {self.d.store.rev})")
        self.d.ensure_board("playback")
        if pos_ms is not None:
            self._seek_player(pos_ms)
        self._start_replay(header, events, self.d.song.pos_ms() or 0)
        self.changed()

    def _start_replay(self, header: dict, events: list, pos_ms: float) -> None:
        self.play_take = header
        self.replayer = Replayer(events, header.get("nudge_ms", 0), self.offset_ms)
        self._activity_cache = None
        self._rebuild(pos_ms)
        self._kick.set()

    def _rebuild(self, pos_ms: float) -> None:
        """Seek: the take's init + every event up to pos, applied silently, then one output."""
        d, h = self.d, self.play_take
        if h.get("init"):
            C.load_init(d.console, h["init"])
        for ev in self.replayer.seek(pos_ms or 0):
            if not self._skip(ev):
                self._apply(ev, drive=False)
        d.drive_now()

    def stop_play(self) -> None:
        if self.play_take is None:
            return
        self.play_take = None
        self.replayer = None
        self._activity_cache = None
        self.changed()

    def stop_all(self, reason: str = "") -> None:
        self.stop_rec(reason)
        self.armed = None
        self.stop_play()

    def _seek_player(self, pos_ms: int) -> None:
        """Move the song. The replay follows the SongClock's "seek" event (on_song_event).
        Fake provider: at once; Apple TV: in the background (pending-hardware H5)."""
        if self.d.fake_provider is not None:
            self.d.fake_provider.seek(pos_ms)
            self.d.poll_now()
            return
        p = self.d.engine.provider
        if p is None or not hasattr(p, "seek"):
            raise TakeError("the player cannot seek")
        self.d.spawn(p.seek(pos_ms))

    def seek(self, pos_ms: int) -> None:
        """play.seek (timeline scrub)."""
        if self._np() is None or not self._np().has_track():
            raise TakeError("nothing is playing")
        self._seek_player(pos_ms)

    def set_nudge(self, take_id: str, ms: int) -> None:
        self.store.update_header(take_id, lambda h: h.__setitem__("nudge_ms", int(ms)))
        if self.play_take is not None and self.play_take["id"] == take_id:
            self.play_take["nudge_ms"] = int(ms)
            self.replayer.set_shift(ms, self.offset_ms)
            self._rebuild(self.d.song.pos_ms() or 0)
        self.changed(library=True)

    def _skip(self, ev: dict) -> bool:
        r = self.rec
        return r is not None and r.mode == "overdub" and r.touched(touch_key(ev), ev["p"])

    def _apply(self, ev: dict, drive: bool = True) -> None:
        try:
            self.d.apply_replay(ev, drive=drive)
        except Exception as e:                       # a bad event never stops the show
            log.debug(f"replay event skipped ({e}): {ev}")

    def _release_replayed_flash(self, key) -> None:
        """OVERDUB: live input takes a channel; a flash the old take holds there lets go."""
        if not isinstance(key, tuple):
            return
        c = self.d.console.channel(*key)
        if c.flash:
            self._apply({"p": 0, "i": {"intent": "flash", "ch": key[1], "down": False},
                         "b": key[0], "c": key[1], "a": "flash", "v": False})

    def tick(self) -> Optional[float]:
        """Apply what is due at the SongClock position; returns seconds until the next look."""
        rp = self.replayer
        if rp is None:
            return None
        pos = self.d.song.pos_ms()
        if pos is None:
            return self.TICK_MAX_S
        due = [ev for ev in rp.due(pos) if not self._skip(ev)]
        for ev in due:
            self._apply(ev, drive=False)
        if due:
            self.d.drive_now()
        nxt = rp.next_due()
        if nxt is None:
            if self.loop and self.d.song.dur_ms and pos >= self.d.song.dur_ms - 1:
                self._rebuild(0)
            return self.TICK_MAX_S
        if not self.d.song.playing:
            return self.TICK_MAX_S
        return min(self.TICK_MAX_S, max(0.0, (nxt - pos) / 1000.0))

    async def run(self) -> None:
        while True:
            delay = None
            try:
                delay = self.tick()
            except Exception as e:
                log.warning(f"replay tick failed: {e}")
            if self.armed is not None and self.rec is None and self._playing_track():
                try:
                    self.start(self.armed["take_id"], self.armed["mode"])
                except TakeError as e:
                    self.armed = None
                    self.d.ipc.toast("warn", str(e))
                    self.changed()
            if self.rec is not None:
                self.changed()                        # elapsed time
            try:
                await asyncio.wait_for(self._kick.wait(), delay if delay is not None else 0.25)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()

    def on_song_event(self, event: str) -> None:
        """SongClock events: a track change stops recording (saved) and replay; a seek
        rebuilds the replayed look at the new position."""
        if event == "track":
            if self.rec is not None:
                self.stop_rec("track changed")
            if self.play_take is not None:
                if self.loop and self._matches(self.play_take):
                    self._rebuild(self.d.song.pos_ms() or 0)
                else:
                    self.stop_play()
                    self.d.ipc.toast("info", "Playback stopped: the track changed")
        elif event == "seek" and self.replayer is not None:
            self._rebuild(self.d.song.pos_ms() or 0)
        self._kick.set()

    # ---- ops --------------------------------------------------------------------------------
    async def handle(self, op: str, req: dict) -> Optional[dict]:
        tid = req.get("take_id")
        now_s = self.d.clock.time()
        if op == "takes.list":
            return {"takes": self.store.list()}
        if op == "takes.get":
            h, evs = self.store.get(tid)
            return {"header": h, "events": evs}
        if op == "takes.delete":
            if (self.rec and self.rec.header["id"] == tid) or (self.play_take and self.play_take["id"] == tid):
                raise TakeError("stop it first")
            self.store.delete(tid)
            self.changed(library=True)
            return None
        if op == "takes.rename":
            name = req.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 64:
                raise TakeError("name must be 1-64 characters")
            self.store.update_header(tid, lambda h: h.__setitem__("name", name.strip()))
            self.changed(library=True)
            return None
        if op == "takes.export":
            p = self.store.path(tid)
            with open(p) as fh:
                return {"jsonl": fh.read(), "filename": os.path.basename(p)}
        if op == "takes.duplicate":
            new = self.store.duplicate(tid, now_s)
            self.changed(library=True)
            return {"take_id": new}
        if op == "takes.set_flag":
            if req.get("flag") != "training" or not isinstance(req.get("value"), bool):
                raise TakeError("flag must be 'training' with a true/false value")
            self.store.update_header(tid, lambda h: h.__setitem__("training", req["value"]))
            self.changed(library=True)
            return None
        if op == "takes.marker.add":
            p, label = req.get("p_ms"), req.get("label")
            if isinstance(p, bool) or not isinstance(p, int) or p < 0:
                raise TakeError("p_ms must be a position in ms")
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 16:
                raise TakeError("label must be 1-16 characters")

            def add(h):
                h["markers"] = sorted(h.get("markers", []) + [{"p": p, "label": label.strip()}], key=lambda m: m["p"])
            self.store.update_header(tid, add)
            self.changed(library=True)
            return None
        if op == "takes.marker.remove":
            p, label = req.get("p_ms"), req.get("label")

            def rm(h):
                ms = h.get("markers", [])
                hit = next((m for m in ms if m["p"] == p and (label is None or m["label"] == label)), None)
                if hit is None:
                    raise TakeError("no such marker")
                ms.remove(hit)
            self.store.update_header(tid, rm)
            self.changed(library=True)
            return None
        if op == "take.nudge":
            ms = req.get("ms")
            if isinstance(ms, bool) or not isinstance(ms, int) or not -2000 <= ms <= 2000:
                raise TakeError("ms must be -2000..2000")
            self.set_nudge(tid, ms)
            return None
        if op == "rec.arm":
            self.arm(tid, req.get("mode"))
            return None
        if op == "rec.start":
            return self.start(tid, req.get("mode"))
        if op == "rec.stop":
            saved = self.stop_rec()
            return {"take_id": saved} if saved else None
        if op == "play.start":
            pos = req.get("pos_ms")
            if pos is not None and (isinstance(pos, bool) or not isinstance(pos, int) or pos < 0):
                raise TakeError("pos_ms must be a position in ms")
            self.play(tid, pos, req.get("loop"))
            return None
        if op == "play.stop":
            self.armed = None if self.rec is None else self.armed
            self.stop_play()
            return None
        if op == "play.seek":
            pos = req.get("pos_ms")
            if isinstance(pos, bool) or not isinstance(pos, int) or pos < 0:
                raise TakeError("pos_ms must be a position in ms")
            self.seek(pos)
            return None
        raise TakeError(f"unknown op {op!r}")
