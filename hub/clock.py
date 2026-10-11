# =============================================================================
# hub/clock.py — time sources and the SongClock (BUILD_ORDER §5.4).
#
# Clock          monotonic() + time() in seconds (MonotonicClock = the OS;
#                hub/testing/clock.FakeClock in tests)
# SongClock      the current song position in ms, interpolated with a monotonic
#                clock while playing, frozen while paused, and resynced on every
#                Apple TV sample with slew limiting.
#
# Two kinds of sample:
#   precise  MRP's float elapsedTime + the wall time it was sampled + rate
#            (AppleTVProvider.raw_anchor()). Measured position = elapsed +
#            (now - anchor_wall) * rate.
#   coarse   pyatv's public Playing.position, truncated to whole seconds. A
#            sample p only says the true position is in [p, p + 1000) ms, so it
#            is used as an interval constraint, never as a point — otherwise the
#            clock would saw back by up to a second on every update.
#
# Rules: an error >= SEEK_MS is a seek (snap + "seek" event). Smaller errors are
# corrected at once when forward or <= MAX_BACK_MS backward; a larger backward
# error is applied MAX_BACK_MS now and the rest is absorbed by running the clock
# at (1 - SLEW) speed, so position never jumps back more than 50 ms mid-take.
# =============================================================================

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def time(self) -> float: ...


class MonotonicClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()


@dataclass
class ClockSample:
    track_id: str
    playing: bool
    pos_ms: Optional[int] = None      # coarse: whole seconds * 1000 (pyatv Playing.position)
    dur_ms: Optional[int] = None
    anchor: Optional[dict] = None     # precise: {elapsed_s, anchor_wall, rate}


class SongClock:
    SEEK_MS = 1500.0
    MAX_BACK_MS = 50.0
    SLEW = 0.1                # absorb backward error at 100 ms per second of playback
    COARSE_TOL_MS = 80.0      # latency allowance around the [p, p+1000) interval
    ANCHOR_MAX_AGE_S = 3600.0

    def __init__(self, clock: Clock, on_event: Optional[Callable[[str, "SongClock"], None]] = None):
        self.clock = clock
        self.listeners: list = [on_event] if on_event else []
        self.track_id: Optional[str] = None
        self.playing = False
        self.dur_ms: Optional[int] = None
        self.source = "none"           # "precise" | "coarse" | "none"
        self.last_err_ms = 0.0
        self.max_err_ms = 0.0          # largest non-seek correction since reset_error_stats()
        self._base = 0.0               # position (ms) at _t0, debt excluded
        self._t0 = clock.monotonic()
        self._debt = 0.0               # backward correction still to absorb (<= 0)

    # ---- reading -------------------------------------------------------------
    def pos_ms(self) -> Optional[float]:
        if self.track_id is None:
            return None
        return self._clamp(self._raw(self.clock.monotonic()))

    def _rate(self) -> float:
        return 1.0 if self.playing else 0.0

    def _absorbed(self, t: float) -> float:
        if self._debt >= 0:
            return 0.0
        return max(self._debt, -self.SLEW * (t - self._t0) * 1000.0 * self._rate())

    def _raw(self, t: float) -> float:
        return self._base + (t - self._t0) * 1000.0 * self._rate() + self._absorbed(t)

    def _clamp(self, p: float) -> float:
        p = max(0.0, p)
        return min(p, float(self.dur_ms)) if self.dur_ms else p

    def _rebase(self, t: float) -> None:
        absorbed = self._absorbed(t)
        self._base = self._raw(t)
        self._debt -= absorbed
        self._t0 = t

    # ---- measuring -------------------------------------------------------------
    def _measure(self, s: ClockSample) -> tuple:
        """(point_ms, lo_ms, hi_ms, source). Precise samples have lo == hi == point."""
        a = s.anchor
        if a and a.get("elapsed_s") is not None and a.get("anchor_wall"):
            age = self.clock.time() - float(a["anchor_wall"])
            if -60.0 <= age <= self.ANCHOR_MAX_AGE_S:
                rate = float(a.get("rate") or 0.0) if s.playing else 0.0
                m = float(a["elapsed_s"]) * 1000.0 + age * 1000.0 * rate   # signed: skew either way
                return m, m, m, "precise"
        if s.pos_ms is not None:
            p = float(s.pos_ms)
            if s.playing:
                return p + 500.0, p - self.COARSE_TOL_MS, p + 1000.0 + self.COARSE_TOL_MS, "coarse"
            return p + 500.0, p, p + 1000.0, "coarse"
        return None, None, None, "none"

    # ---- updating ----------------------------------------------------------------
    def update(self, s: ClockSample) -> Optional[str]:
        """Feed one Apple TV sample. Returns "track" | "seek" | "pause" | "play" |
        "resync" | None, and notifies listeners of every non-None event."""
        t = self.clock.monotonic()
        point, lo, hi, source = self._measure(s)
        if s.dur_ms:
            self.dur_ms = int(s.dur_ms)
        event = None

        if s.track_id != self.track_id:
            self.track_id = s.track_id
            self.dur_ms = int(s.dur_ms) if s.dur_ms else None
            self.playing = s.playing
            self._base, self._t0, self._debt = (point or 0.0), t, 0.0
            self.source = source
            self.reset_error_stats()
            event = "track"
        else:
            self._rebase(t)
            est = self._base
            if point is None:
                err = 0.0
            elif lo <= est <= hi:
                err = 0.0
            else:
                err = (lo - est) if est < lo else (hi - est)
                if source == "coarse":     # aim just inside the interval, not at its edge
                    err += 1.0 if est < lo else -1.0
            if s.playing != self.playing:
                # pause / play: freeze or start from the sample, a plain correction otherwise
                self.playing = s.playing
                event = "play" if s.playing else "pause"
            if point is not None and abs(err) >= self.SEEK_MS:
                self._base, self._debt = point, 0.0
                event = "seek"
            elif err:
                self._debt = 0.0
                if err >= -self.MAX_BACK_MS or not self.playing:
                    self._base += err
                else:
                    self._base -= self.MAX_BACK_MS
                    self._debt = err + self.MAX_BACK_MS
                self.last_err_ms = err
                self.max_err_ms = max(self.max_err_ms, abs(err))
                event = event or "resync"
            if source != "none":
                self.source = source

        self._base = self._clamp(self._base)
        if event:
            for cb in list(self.listeners):
                try:
                    cb(event, self)
                except Exception:
                    pass
        return event

    def reset_error_stats(self) -> None:
        self.last_err_ms = 0.0
        self.max_err_ms = 0.0

    def snapshot(self) -> dict:
        p = self.pos_ms()
        return {"track_id": self.track_id, "playing": self.playing,
                "pos_ms": None if p is None else int(p), "dur_ms": self.dur_ms,
                "source": self.source, "last_err_ms": round(self.last_err_ms, 1),
                "max_err_ms": round(self.max_err_ms, 1)}
