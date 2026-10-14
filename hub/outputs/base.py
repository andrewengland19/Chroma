# =============================================================================
# hub/outputs/base.py — the Output interface (BUILD_ORDER §3, M3).
#
# An output pushes one colour to one fixture. Colours are HSB: hue in degrees
# [0, 360), saturation and brightness 0..1. `klass` tells the scheduler (M4) how
# fast the device is: "slow" (LIFX, ~100–250 ms, washes) or "fast" (WLED/virtual).
# Outputs never raise into the engine and never drive a fixture brighter on error
# (BUILD_ORDER §8.4) — they log and drop the frame.
# =============================================================================

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HSB:
    h: float
    s: float
    b: float

    def clamped(self) -> "HSB":
        return HSB(self.h % 360.0, min(1.0, max(0.0, self.s)), min(1.0, max(0.0, self.b)))

    def hex(self) -> str:
        c = self.clamped()
        r, g, b = colorsys.hsv_to_rgb(c.h / 360.0, c.s, c.b)
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

    @classmethod
    def from_rgb(cls, rgb, b: float | None = None) -> "HSB":
        h, s, v = colorsys.rgb_to_hsv(*(x / 255.0 for x in rgb))
        return cls(h * 360.0, s, v if b is None else b)


class Output(Protocol):
    klass: str
    capabilities: frozenset

    def push(self, fixture, hsb: HSB, fade_ms: int = 0) -> None: ...

    def blink(self, fixture) -> None:
        """Identify the fixture: two gentle pulses at 1 Hz (inside the 3 Hz strobe cap)."""
