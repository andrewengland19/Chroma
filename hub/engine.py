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
from dataclasses import asdict, fields
from datetime import datetime, timezone
from typing import Optional

from pyatv.interface import DeviceListener, PushListener

from providers import AppleTVProvider, NowPlaying
from color_pipeline import artwork_to_hsbk
from config import Config


def _hsbk_dicts(hsbk: list) -> list:
    return [{"h": h, "s": s, "b": b, "k": k} for (h, s, b, k) in hsbk]

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
# EventBus — in-memory pub/sub for WebSocket clients (Pass 3)
# =============================================================================

class EventBus:
    """Fan-out of engine events to any number of subscriber queues. Publishing is
    non-blocking: a slow/full subscriber drops frames rather than stalling the engine."""

    def __init__(self, maxsize: int = 64):
        self._subs: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: str, **data) -> None:
        payload = {"event": event, **data}
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow client — drop this frame


# =============================================================================
# Engine
# =============================================================================

class Engine(DeviceListener, PushListener):
    def __init__(self, loop, install_signals: bool = True):
        self.loop = loop
        self.cfg = Config.load()
        self.lifx = LifxController(self.cfg)
        self.provider: Optional[AppleTVProvider] = None
        self.bus = EventBus()
        self.enabled = self.cfg.enabled
        self._install_signals_enabled = install_signals

        self._stop = asyncio.Event()       # process shutdown
        self._disconnected = asyncio.Event()  # ATV dropped → reconnect
        self._apply_lock = asyncio.Lock()
        self._last_track_id: Optional[str] = None
        # Cached inputs of the last paint, so live config edits can repaint at once.
        self._last_np: Optional[NowPlaying] = None
        self._last_art: Optional[bytes] = None
        self._last_hsbk: list = []

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
                # Same track — but refresh play-state (pause/resume) for the UI,
                # without repainting the bulbs.
                if self._last_np is not None and np.playing != self._last_np.playing:
                    self._last_np = np
                    self._write_state()
                    self.bus.publish("status",
                                     message="playing" if np.playing else "paused",
                                     playing=np.playing)
                return
            self._last_track_id = tid
            await self._apply(np, source)

    async def _apply(self, np: NowPlaying, source: str) -> None:
        """New track: re-read config, fetch fresh artwork, then render."""
        self.cfg = Config.load()
        self.enabled = self.cfg.enabled
        self.lifx.cfg = self.cfg
        log.info(f"♪  {np.label()}  [{np.album}]  ({source})")
        self.bus.publish("track_change", track=np.label(), artist=np.artist,
                         title=np.title, album=np.album, playing=np.playing)

        art = await self.provider.artwork_bytes()
        if not art:
            log.info("   → no artwork; leaving lights as-is")
            self.bus.publish("status", message="no artwork for this track")
            return
        _atomic_write(ARTWORK_FILE, art)
        self._last_np = np
        self._last_art = art
        await self._render(np, art, source)

    async def _render(self, np: NowPlaying, art: bytes, source: str) -> None:
        """Artwork → palette → (optionally) bulbs → contract files → events.
        Shared by new-track paints and live config re-paints (cached artwork)."""
        count = self.cfg.color_count(self.lifx.count)
        hsbk = artwork_to_hsbk(art, count, self.cfg.pipeline_params())
        if not hsbk:
            log.info("   → majority-white artwork; skipping (last palette kept)")
            self.bus.publish("status", message="majority-white artwork; palette kept")
            return
        self._last_hsbk = hsbk

        if self.enabled and self.lifx.count:
            await self.loop.run_in_executor(
                None, self.lifx.push, hsbk, self.cfg.transition_ms
            )
            log.info(f"   → {len(hsbk)} color(s) → {self.lifx.count} light(s)  🎨")
        elif not self.enabled:
            log.info(f"   → painting off; computed {len(hsbk)} color(s) ({source})")

        write_colors_file(np.label(), hsbk)
        self._write_state()
        self.bus.publish("colors_pushed", track=np.label(),
                         colors=_hsbk_dicts(hsbk), enabled=self.enabled)

    # ---- state snapshot (GET /state) ---------------------------------------
    def snapshot(self) -> dict:
        np = self._last_np
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "connected": self.provider is not None,
            "device": self.provider.name if self.provider else None,
            "lights": self.lifx.count,
            "track": np.label() if np else None,
            "artist": np.artist if np else None,
            "title": np.title if np else None,
            "album": np.album if np else None,
            "playing": np.playing if np else False,
            "enabled": self.enabled,
            "colors": _hsbk_dicts(self._last_hsbk),
            "config": asdict(self.cfg),
        }

    def _write_state(self) -> None:
        write_state_file(self.snapshot())

    # ---- control-plane commands (Pass 3) -----------------------------------
    async def set_config(self, partial: dict) -> dict:
        """Apply a partial config, persist it, and repaint from cached artwork so
        slider moves are visible immediately (mirrors the old Electron 'Apply=live')."""
        known = {f.name for f in fields(Config)}
        updates = {k: v for k, v in partial.items() if k in known}
        for k, v in updates.items():
            setattr(self.cfg, k, v)
        self.cfg.save()
        self.lifx.cfg = self.cfg
        if "enabled" in updates:
            self.enabled = bool(updates["enabled"])
        async with self._apply_lock:
            if self._last_np and self._last_art:
                await self._render(self._last_np, self._last_art, "config")
            else:
                self._write_state()
        return asdict(self.cfg)

    async def set_enabled(self, on: bool) -> dict:
        self.enabled = on
        self.cfg.enabled = on
        self.cfg.save()
        async with self._apply_lock:
            if on and self._last_np and self._last_art:
                await self._render(self._last_np, self._last_art, "enable")
            else:
                self._write_state()
                self.bus.publish("status",
                                 message="painting enabled" if on else "painting disabled")
        return self.snapshot()

    async def transport(self, action: str) -> bool:
        if not self.provider:
            return False
        fn = {"play_pause": self.provider.play_pause,
              "next": self.provider.next,
              "previous": self.provider.previous}.get(action)
        if not fn:
            return False
        await fn()
        return True

    def stop(self) -> None:
        self._stop.set()

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
                self._write_state()
                self.bus.publish("status", message=f"connecting… (retry in {delay}s)")
                if await self._sleep_or_stop(delay):
                    break
                continue

            attempt = 0
            log.info(f"  ✓ connected to {self.provider.name}")
            self.bus.publish("status", message=f"connected to {self.provider.name}")
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
        if not self._install_signals_enabled:
            return  # embedded under uvicorn — it owns signal handling
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
