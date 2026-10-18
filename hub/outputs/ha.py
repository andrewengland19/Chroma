# =============================================================================
# hub/outputs/ha.py — Home Assistant light output (STUB, inactive unless ha.enabled).
#
# Maps Chroma fixture ids to HA light entities (HaConfig.entities) and pushes HSB via
# HaClient.turn_on. Not yet registered in daemon._push: wiring it there is a two-line
# change once HA is real (docs/mvp/HOMEASSISTANT.md §Wiring). push() is synchronous and
# only records the newest value per entity (coalesce, never a backlog); `await flush()`
# sends, at most one command per entity per min_interval_ms (strobe cap, §8.1).
# Errors drop the frame — never a retry, never a brighter value (§8.4).
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from .base import HSB

log = logging.getLogger("chroma-hub")


class HaOutput:
    klass = "slow"
    capabilities = frozenset({"color", "brightness", "fade"})

    def __init__(self, client=None, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self._now = clock
        self.pending: dict = {}      # entity_id -> (HSB, fade_ms)
        self._last_sent: dict = {}   # entity_id -> monotonic time

    @property
    def active(self) -> bool:
        return self.client is not None and self.client.cfg.enabled

    def push(self, f, hsb: HSB, fade_ms: int = 0) -> None:
        if not self.active:
            return
        eid = self.client.cfg.entity_for_fixture(f.id)
        if eid:
            self.pending[eid] = (hsb.clamped(), int(fade_ms))

    async def flush(self) -> int:
        """Send what the rate limit allows; returns the number of commands sent."""
        if not self.active:
            self.pending.clear()
            return 0
        gap = self.client.cfg.min_interval_ms / 1000.0
        now = self._now()
        due = [e for e in self.pending if now - self._last_sent.get(e, -1e9) >= gap]
        jobs = []
        for eid in due:                       # others are held; their newest value goes out later
            hsb, fade_ms = self.pending.pop(eid)
            self._last_sent[eid] = now
            jobs.append(self._send(eid, hsb, fade_ms))
        # concurrently: one unreachable entity must not stall the rest for a full timeout each
        return sum(await asyncio.gather(*jobs))

    async def _send(self, eid: str, hsb: HSB, fade_ms: int) -> int:
        try:
            await self.client.turn_on(eid, brightness=hsb.b, hs=(hsb.h, hsb.s),
                                      transition_ms=fade_ms or self.client.cfg.transition_for(eid))
            return 1
        except Exception as e:                # drop the frame: no retry, never a brighter value
            log.warning(f"Home Assistant push to {eid} failed: {e}")
            return 0

    def blink(self, f) -> None:
        """Not implemented for HA (stub): Setup's Test is a no-op for HA fixtures."""
