# =============================================================================
# hub/state.py — assembles the IPC State (contracts/state.schema.json) from the
# Engine, the SongClock and the console model.
#
# `now` comes from the latest now-playing sample (Engine._last_np only moves once
# artwork is fetched, so a track without art would otherwise show the previous
# one). pos_ms is refreshed at most every 250 ms (or on a SongClock event); the
# UI interpolates between updates.
# =============================================================================

from __future__ import annotations

import re
from typing import Optional

ART_MODE_OUT = {"deterministic": "det", "paint": "paint", "ai": "ai"}
ART_MODE_IN = {v: k for k, v in ART_MODE_OUT.items()}


def _unit(v) -> float:
    return round(min(1.0, max(0.0, float(v))), 3)


def slugify(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:48].strip("-")
    return s or "fixture"


class StateAssembler:
    def __init__(self, engine, song, console, clock, pos_interval_s: float = 0.25):
        self.engine = engine
        self.song = song
        self.console = console
        self.clock = clock
        self.pos_interval_s = pos_interval_s
        self.latest_np = None               # updated by chromad's now-playing listener
        self.rec: dict = {"state": "idle", "take_id": None, "elapsed_ms": 0, "events": 0,
                          "mode": "replace", "name": "", "groups_touched": 0}
        self.play: dict = {"state": "idle", "take_id": None, "pos_ms": 0, "loop": False}
        self.board_health = "none"
        self.fixtures_rev = 0
        self.takes_rev = 0
        self._art_ref = None
        self._art_rev = 0
        self._pos_val: Optional[int] = None
        self._pos_t = float("-inf")

    def force_pos(self) -> None:
        """Publish the SongClock position on the next build (resync / seek / track)."""
        self._pos_t = float("-inf")

    def _pos(self) -> Optional[int]:
        t = self.clock.monotonic()
        if t - self._pos_t >= self.pos_interval_s:
            p = self.song.pos_ms()
            self._pos_val = None if p is None else int(p)
            self._pos_t = t
        return self._pos_val

    def _art_url(self) -> Optional[str]:
        art = getattr(self.engine, "_last_art", None)
        if art is None:
            return None
        if art is not self._art_ref:
            self._art_ref = art
            self._art_rev += 1
        return f"chroma-art://current?rev={self._art_rev}"

    def _now(self) -> dict:
        np = self.latest_np or getattr(self.engine, "_last_np", None)
        if np is None or not np.has_track():
            return {"track_id": "", "title": "", "artist": "", "album": "", "playing": False,
                    "pos_ms": None, "dur_ms": None, "art_url": self._art_url()}
        same = self.song.track_id == np.track_id()
        return {"track_id": np.track_id(), "title": np.title, "artist": np.artist, "album": np.album,
                "playing": bool(np.playing),
                "pos_ms": self._pos() if same else (np.pos_ms if np.pos_ms is not None else None),
                "dur_ms": (self.song.dur_ms if same and self.song.dur_ms else np.dur_ms),
                "art_url": self._art_url()}

    def _room(self) -> list:
        eng = self.engine
        per_light = getattr(eng, "_last_per_light", None) or []
        if per_light:
            return [{"fixture_id": slugify(p["label"]), "label": p["label"], "x": _unit(p["x"]), "y": _unit(p["y"]),
                     "hex": p.get("hex"), "online": True} for p in per_light]
        out = []
        for label in eng.lifx.labels():
            x, y = eng.layout.pos_for(label)
            out.append({"fixture_id": slugify(label), "label": label, "x": _unit(x), "y": _unit(y),
                        "hex": None, "online": True})
        return out

    def build(self) -> dict:
        eng = self.engine
        state = {
            "now": self._now(),
            "who": "auto" if eng.enabled else "board",
            "art_mode": ART_MODE_OUT.get(eng.cfg.mode, "det"),
            **self.console.to_state_fields(),
            "room": self._room(),
            "rec": dict(self.rec),
            "play": dict(self.play),
            "health": {"atv": "up" if eng.provider is not None else "down",
                       "lifx": {"found": eng.lifx.count, "total": eng.lifx.count},
                       "board": self.board_health},
            "fixtures_rev": self.fixtures_rev,
            "takes_rev": self.takes_rev,
        }
        return state
