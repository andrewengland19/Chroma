#!/usr/bin/env python3
# =============================================================================
# hub/engine.py  —  Pass 2: the headless, unattended CHROMA hub.
#
# Runs forever. Connects to the paired Apple TV, reacts to track changes over
# pyatv's push updater (with a periodic reconcile as a safety net), extracts the
# album-art palette, pushes it to the LIFX bulbs, and writes the two contract
# files the rest of the ecosystem reads:
#   ~/.chroma/current_colors.json  — SPECTRUM's BEAT-mode palette contract
#   ~/.chroma/current_artwork.jpg  — the artwork the Electron UI serves
#
# Auto-reconnects when the Apple TV sleeps or the network blips, so it can run
# under launchd (Pass 5) as a true daily driver.
#
# Run:  ./.venv/bin/python engine.py
# =============================================================================

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Optional

from pyatv.interface import DeviceListener, PushListener

from providers import AppleTVProvider, NowPlaying
from color_pipeline import artwork_to_hsbk
from config import Config

CHROMA_DIR = os.path.expanduser("~/.chroma")
COLORS_FILE = os.path.join(CHROMA_DIR, "current_colors.json")
ARTWORK_FILE = os.path.join(CHROMA_DIR, "current_artwork.jpg")
STATE_FILE = os.path.join(CHROMA_DIR, "state.json")

RECONNECT_BACKOFF = (2, 5, 10, 20, 30)  # seconds, then holds at last value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chroma-hub")


# =============================================================================
# Atomic state writers
# =============================================================================

def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_colors_file(track: str, hsbk_colors: list) -> None:
    """Write the SPECTRUM contract file (~/.chroma/current_colors.json)."""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "track": track,
        "colors": [{"h": h, "s": s, "b": b, "k": k} for (h, s, b, k) in hsbk_colors],
    }
    _atomic_write(COLORS_FILE, json.dumps(payload, indent=2).encode())


def write_state_file(state: dict) -> None:
    """Engine status for the Pass 3 control plane / debugging."""
    _atomic_write(STATE_FILE, json.dumps(state, indent=2).encode())


# =============================================================================
# LIFX control (sync lifxlan, always called via run_in_executor)
# =============================================================================

class LifxController:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lights: list = []

    def discover(self) -> int:
        try:
            from lifxlan import LifxLAN
        except ImportError:
            log.warning("lifxlan not installed — running without bulbs")
            self._lights = []
            return 0
        try:
            lights = LifxLAN().get_lights()
        except Exception as e:
            log.warning(f"LIFX discovery failed: {e}")
            self._lights = []
            return 0
        if self.cfg.use_group:
            lights = [l for l in lights if _safe(l.get_group) == self.cfg.group_name]
        self._lights = lights
        for l in lights:
            log.info(f"  • {_safe(l.get_label)} @ {_safe(l.get_ip_addr)}")
        return len(lights)

    @property
    def count(self) -> int:
        return len(self._lights)

    def push(self, hsbk_colors: list, duration_ms: int) -> None:
        for i, light in enumerate(self._lights):
            color = hsbk_colors[i % len(hsbk_colors)]
            try:
                light.set_color(color, duration=duration_ms, rapid=True)
            except Exception as e:
                log.warning(f"  ! failed to set a light: {e}")


def _safe(fn):
    try:
        return fn()
    except Exception:
        return "?"


# =============================================================================
# Engine
# =============================================================================

class Engine(DeviceListener, PushListener):
    def __init__(self, loop):
        self.loop = loop
        self.cfg = Config.load()
        self.lifx = LifxController(self.cfg)
        self.provider: Optional[AppleTVProvider] = None

        self._stop = asyncio.Event()       # process shutdown
        self._disconnected = asyncio.Event()  # ATV dropped → reconnect
        self._apply_lock = asyncio.Lock()
        self._last_track_id: Optional[str] = None

    # ---- pyatv DeviceListener (connection lifecycle) -----------------------
    def connection_lost(self, exception) -> None:
        log.warning(f"Apple TV connection lost: {exception}")
        self.loop.call_soon_threadsafe(self._disconnected.set)

    def connection_closed(self) -> None:
        log.warning("Apple TV connection closed")
        self.loop.call_soon_threadsafe(self._disconnected.set)

    # ---- pyatv PushListener (track changes) --------------------------------
    def playstatus_update(self, updater, playstatus) -> None:
        np = NowPlaying.from_playing(playstatus)
        asyncio.ensure_future(self._on_playing(np, "push"), loop=self.loop)

    def playstatus_error(self, updater, exception) -> None:
        log.debug(f"push error: {exception}")

    # ---- core: dedup + apply -----------------------------------------------
    async def _on_playing(self, np: Optional[NowPlaying], source: str) -> None:
        if not np or not np.has_track():
            return
        async with self._apply_lock:
            tid = np.track_id()
            if tid == self._last_track_id:
                return
            self._last_track_id = tid
            await self._apply(np, source)

    async def _apply(self, np: NowPlaying, source: str) -> None:
        # Re-read config each track so Pass 3's live edits take effect.
        self.cfg = Config.load()
        self.lifx.cfg = self.cfg
        log.info(f"♪  {np.label()}  [{np.album}]  ({source})")

        art = await self.provider.artwork_bytes()
        if not art:
            log.info("   → no artwork; leaving lights as-is")
            return
        _atomic_write(ARTWORK_FILE, art)

        count = self.cfg.color_count(self.lifx.count)
        hsbk = artwork_to_hsbk(art, count, self.cfg.pipeline_params())
        if not hsbk:
            log.info("   → majority-white artwork; skipping (last palette kept)")
            return

        # Bulbs first (visible latency), then the contract files.
        if self.lifx.count:
            await self.loop.run_in_executor(
                None, self.lifx.push, hsbk, self.cfg.transition_ms
            )
            log.info(f"   → {len(hsbk)} color(s) → {self.lifx.count} light(s)  🎨")
        write_colors_file(np.label(), hsbk)
        self._write_state(np, hsbk, connected=True)

    def _write_state(self, np: Optional[NowPlaying], hsbk: list, connected: bool) -> None:
        write_state_file({
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "connected": connected,
            "device": self.provider.name if self.provider else None,
            "lights": self.lifx.count,
            "track": np.label() if np else None,
            "playing": np.playing if np else False,
            "colors": [{"h": h, "s": s, "b": b, "k": k} for (h, s, b, k) in hsbk],
        })

    # ---- supervisor: connect, run, reconnect -------------------------------
    async def run(self) -> None:
        log.info("CHROMA hub starting — headless Apple TV → LIFX")
        self._install_signals()
        attempt = 0
        while not self._stop.is_set():
            try:
                log.info("Connecting to the paired Apple TV…")
                self.provider = await AppleTVProvider.connect(self.loop)
            except Exception as e:
                delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
                attempt += 1
                log.warning(f"  connect failed: {e} — retrying in {delay}s")
                self._write_state(None, [], connected=False)
                if await self._sleep_or_stop(delay):
                    break
                continue

            attempt = 0
            log.info(f"  ✓ connected to {self.provider.name}")
            await self.loop.run_in_executor(None, self.lifx.discover)
            log.info(f"  {self.lifx.count} light(s)"
                     + (f" in group '{self.cfg.group_name}'" if self.cfg.use_group else ""))

            self._disconnected.clear()
            self.provider.set_device_listener(self)
            # Prime with whatever is playing, then react to changes.
            self._last_track_id = None
            await self._on_playing(await self.provider.now_playing(), "prime")
            self.provider.start_push(self)

            await self._serve_until_disconnect()

            try:
                await self.provider.close()
            except Exception:
                pass
            self.provider = None

        log.info("CHROMA hub stopped.")

    async def _serve_until_disconnect(self) -> None:
        """Idle here until the ATV drops or we're told to stop; reconcile periodically."""
        while not self._stop.is_set() and not self._disconnected.is_set():
            try:
                await asyncio.wait_for(self._disconnected.wait(),
                                       timeout=self.cfg.reconcile_seconds)
            except asyncio.TimeoutError:
                # Safety net: catch any track change the push updater missed.
                if self.provider:
                    await self._on_playing(await self.provider.now_playing(), "reconcile")

    # ---- shutdown plumbing --------------------------------------------------
    def _install_signals(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                pass  # e.g. Windows; not our target

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Sleep, returning True if a stop was requested during the wait."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False


async def main() -> None:
    loop = asyncio.get_event_loop()
    await Engine(loop).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
