# =============================================================================
# hub/outputs/virtual.py — in-app lights. Keeps the current colour per fixture
# (for the Room view) and a timestamped frame log (for tests and latency
# measurement). "fast": no rate limit beyond the scheduler's 60 Hz.
# =============================================================================

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Optional

from .base import HSB


class VirtualOutput:
    klass = "fast"
    capabilities = frozenset({"color", "brightness"})

    def __init__(self, clock=None, max_frames: int = 20_000):
        self._now: Callable[[], float] = (clock.monotonic if clock else time.monotonic)
        self.current: dict = {}                       # fixture id -> HSB
        self.frames: deque = deque(maxlen=max_frames)  # (t_monotonic, fixture id, HSB, fade_ms)
        self.listeners: list = []

    def push(self, f, hsb: HSB, fade_ms: int = 0) -> None:
        hsb = hsb.clamped()
        self.current[f.id] = hsb
        self.frames.append((self._now(), f.id, hsb, int(fade_ms)))
        for cb in list(self.listeners):
            try:
                cb(f.id, hsb)
            except Exception:
                pass

    def blink(self, f) -> list:
        """Two pulses at 1 Hz, then back: returns the (delay_s, HSB) steps; the caller
        schedules them (so tests can run them on a fake clock)."""
        base = self.current.get(f.id, HSB(0, 0, 0.5))
        dim = HSB(base.h, base.s, 0.15)
        return [(0.0, dim), (0.5, base), (1.0, dim), (1.5, base)]

    def hex_for(self, fid: str) -> Optional[str]:
        c = self.current.get(fid)
        return c.hex() if c else None
