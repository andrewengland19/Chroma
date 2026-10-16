# =============================================================================
# hub/scheduler.py — console output scheduling (BUILD_ORDER §5.3 "Output
# scheduling", §8.1).
#
# Coalesces to the newest value per fixture (never a backlog), paces each fixture
# by its class (LIFX "slow": <= 20 messages/s per bulb; "fast"/virtual: 60 Hz),
# runs every value through the D7 StrobeLimiter, and logs intent -> output
# latency per fixture (debug) with running stats per class.
#
# `step(now_ms)` is synchronous and does all the work, so tests drive it on a
# fake clock; `run()` is the asyncio loop around it.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Callable, Optional

from console import StrobeLimiter
from outputs.base import HSB

log = logging.getLogger("chroma-hub")

MIN_INTERVAL_MS = {"slow": 50.0, "fast": 1000.0 / 60.0}   # LIFX 20 msg/s per bulb; virtual 60 Hz


class OutputScheduler:
    def __init__(self, now_ms: Callable[[], float], push: Callable[[str, HSB, int], None],
                 klass: Callable[[str], str], limits: Callable[[str], tuple]):
        self.now_ms = now_ms
        self.push = push              # (fixture id, HSB, fade_ms) -> None; never raises
        self.klass = klass            # fixture id -> "slow" | "fast"
        self.limits = limits          # fixture id -> (max_level, max_flash_hz)
        self.limiter = StrobeLimiter()
        self.pending: dict = {}       # fid -> (HSB, t_intent_ms, fade_ms, dark)
        self.held_t: dict = {}        # fid -> t_intent_ms of a value the strobe limit holds
        self.last_ms: dict = {}       # fid -> when it was last sent
        self.latency: dict = {"slow": deque(maxlen=500), "fast": deque(maxlen=500)}
        self.sent_count = 0
        self._wake = asyncio.Event()

    # ---- inputs -----------------------------------------------------------------------
    def submit(self, targets: dict, t_intent_ms: float, fade_ms: int = 0, dark: bool = False) -> None:
        """Newest value per fixture replaces whatever is still waiting. `dark` = BLACKOUT is
        on: going dark is never held by the strobe limit (it still counts as an edge)."""
        for fid, hsb in targets.items():
            self.pending[fid] = (hsb, t_intent_ms, int(fade_ms), dark)
        self._wake.set()

    def prime(self, showing: dict) -> None:
        """What the lights show right now (D2: BOARD starts from the art look), so the
        first console value is compared with reality: no push for 'no change', and the
        strobe limit counts the first jump."""
        self.limiter.sent.update(showing)

    def reset(self) -> None:
        self.pending.clear()
        self.held_t.clear()
        self.last_ms.clear()
        self.limiter = StrobeLimiter()

    # ---- work -------------------------------------------------------------------------
    def step(self, now: Optional[float] = None) -> Optional[float]:
        """Send everything that is due; return when to run again (ms) or None."""
        now = self.now_ms() if now is None else now
        wake = []
        for fid, (hsb, t, fade, dark) in list(self.pending.items()):
            last = self.last_ms.get(fid)
            gap = MIN_INTERVAL_MS.get(self.klass(fid), MIN_INTERVAL_MS["slow"])
            if last is not None and now - last < gap:
                wake.append(last + gap)          # stays pending; newer values keep replacing it
                continue
            del self.pending[fid]
            v = self.limiter.offer(fid, hsb, now, self.limits(fid)[1], dark)
            if v is not None:
                self.held_t.pop(fid, None)
                self._send(fid, v, fade, t, now)
            elif fid in self.limiter.pending:
                self.held_t.setdefault(fid, t)
            else:
                self.held_t.pop(fid, None)       # already showing: nothing to send
        for fid, v in self.limiter.poll(now).items():
            self._send(fid, v, 0, self.held_t.pop(fid, now), now)
        due = self.limiter.next_due_ms()
        if due is not None:
            wake.append(due)
        return min(wake) if wake else None

    def _send(self, fid: str, hsb: HSB, fade: int, t_intent: float, now: float) -> None:
        self.push(fid, hsb, fade)
        sent = self.now_ms()
        self.last_ms[fid] = now
        self.sent_count += 1
        k = self.klass(fid)
        lat = max(0.0, sent - t_intent)
        self.latency.setdefault(k, deque(maxlen=500)).append(lat)
        log.debug(f"output {fid} t_intent_received={t_intent:.1f} t_output_sent={sent:.1f} "
                  f"latency_ms={lat:.1f} ({k})")

    def latency_stats(self) -> dict:
        """class -> {n, p50, p95, max} in ms over the last 500 sends."""
        out = {}
        for k, q in self.latency.items():
            if q:
                s = sorted(q)
                out[k] = {"n": len(s), "p50": round(s[len(s) // 2], 2),
                          "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2), "max": round(s[-1], 2)}
        return out

    async def run(self) -> None:
        while True:
            nxt = self.step()
            timeout = None if nxt is None else max(0.0, (nxt - self.now_ms()) / 1000.0)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
