# =============================================================================
# hub/testing/clock.py — a manual clock for deterministic tests.
#
# Same surface as the real clock (hub/clock.py, M1): monotonic() and time(), both
# in seconds. Nothing moves until a test calls advance().
# =============================================================================

from __future__ import annotations


class FakeClock:
    def __init__(self, start_mono: float = 1000.0, start_wall: float = 1_791_600_000.0):
        self._mono = float(start_mono)
        self._wall = float(start_wall)

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("FakeClock cannot go backwards")
        self._mono += seconds
        self._wall += seconds

    def advance_ms(self, ms: float) -> None:
        self.advance(ms / 1000.0)


class RealClock:
    """Wall + monotonic time from the OS. Default for FakeProvider outside tests."""

    def monotonic(self) -> float:
        import time
        return time.monotonic()

    def time(self) -> float:
        import time
        return time.time()
