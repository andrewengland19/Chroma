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
from color_pipeline import (artwork_to_hsbk, region_colors,
                            compute_brightness_scales, rgb_to_hsbk)
from config import Config
from layout import Layout
from enhancers import build_enhancer
from keystore import has_anthropic_key
from scenes import SceneStore
from distribute import round_robin, idw_blend, hex_to_rgb, rgb_to_hex


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
        self._labels: list = []  # cached at discovery (get_label is a network call)

    def discover(self) -> int:
        try:
            from lifxlan import LifxLAN
        except ImportError:
            log.warning("lifxlan not installed — running without bulbs")
            self._lights, self._labels = [], []
            return 0
        try:
            lights = LifxLAN().get_lights()
        except Exception as e:
            log.warning(f"LIFX discovery failed: {e}")
            self._lights, self._labels = [], []
            return 0
        if self.cfg.use_group:
            lights = [l for l in lights if _safe(l.get_group) == self.cfg.group_name]
        self._lights = lights
        self._labels = [str(_safe(l.get_label)) for l in lights]
        for lbl, l in zip(self._labels, lights):
            log.info(f"  • {lbl} @ {_safe(l.get_ip_addr)}")
        return len(lights)

    @property
    def count(self) -> int:
        return len(self._lights)

    def labels(self) -> list:
        return list(self._labels)

    def push(self, hsbk_colors: list, duration_ms: int) -> None:
        """Cycle a small palette across the bulbs (classic single/palette modes)."""
        for i, light in enumerate(self._lights):
            color = hsbk_colors[i % len(hsbk_colors)]
            try:
                light.set_color(color, duration=duration_ms, rapid=True)
            except Exception as e:
                log.warning(f"  ! failed to set a light: {e}")

    def push_aligned(self, hsbk_per_light: list, duration_ms: int) -> None:
        """One color per bulb, aligned to discovery order (spatial mode)."""
        for light, color in zip(self._lights, hsbk_per_light):
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

        # Pass 3.5: spatial layout + AI palette enhancement
        self.layout = Layout.load()
        self.enhancer = build_enhancer(self.cfg)
        self.scenes = SceneStore.load()   # persistent per-album scenes
        self._scene_active = False
        # Pass 4.6: Ollama reachability + Claude fallback
        self.ollama_reachable: Optional[bool] = None
        self.ai_offline = False        # banner flag: local AI down while AI was wanted
        self._health_task = None

        self._stop = asyncio.Event()       # process shutdown
        self._disconnected = asyncio.Event()  # ATV dropped → reconnect
        self._apply_lock = asyncio.Lock()
        self._last_track_id: Optional[str] = None
        # Cached inputs of the last paint, so live config edits can repaint at once.
        self._last_np: Optional[NowPlaying] = None
        self._last_art: Optional[bytes] = None
        self._last_hsbk: list = []
        self._last_per_light: list = []  # [{label,x,y,hex}] for the room canvas

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

    async def _render(self, np: Optional[NowPlaying], art: Optional[bytes], source: str) -> None:
        """Per-bulb color for the active mode → bulbs → contract files → events.
        Modes: deterministic (art region per bulb), paint (user focus colors),
        ai (art region + Ollama). Paint doesn't need artwork."""
        # Mode is authoritative; it drives whether AI runs.
        self.cfg.ai_enhance = (self.cfg.mode == "ai")
        self._sync_enhancer()
        params = self.cfg.pipeline_params()

        labels = self.lifx.labels()
        if labels and self.layout.ensure_labels(labels):
            self.layout.save()
        coords = [self.layout.pos_for(lbl) for lbl in labels]

        if self.lifx.count == 0:
            self._write_state()
            return

        mode = self.cfg.mode
        key = self._scene_key(np)
        self._scene_active = False
        rgb = self._scene_rgb(self.scenes.get(key), coords)   # saved album scene?
        if rgb is not None:
            self._scene_active = True
        elif mode == "paint" and self.cfg.paint_colors:
            rgb = self._paint_rgb(coords)
        elif art is not None:
            rgb = await self._spatial_rgb(np, art, coords, params,
                                          use_ai=(mode == "ai"), scene_key=key)
        else:
            self._write_state()   # paint with no colors + no art yet → nothing to paint
            return

        if not rgb:
            log.info("   → no colors; keeping last palette")
            self.bus.publish("status", message="no colors for this frame")
            return

        scales = compute_brightness_scales(rgb, params)
        hsbk = [rgb_to_hsbk(r, g, b, s, params) for (r, g, b), s in zip(rgb, scales)]
        self._last_hsbk = hsbk
        self._last_per_light = [
            {"label": l, "x": round(c[0], 3), "y": round(c[1], 3), "hex": rgb_to_hex(rr)}
            for l, c, rr in zip(labels, coords, rgb)
        ]

        if self.enabled and self.lifx.count:
            await self.loop.run_in_executor(
                None, self.lifx.push_aligned, hsbk, self.cfg.transition_ms)
            log.info(f"   → {len(hsbk)} color(s) → {self.lifx.count} light(s)  🎨 ({mode})")
        elif not self.enabled:
            log.info(f"   → painting off; computed {len(hsbk)} color(s) ({source})")

        track = np.label() if np else "paint"
        write_colors_file(track, hsbk)
        self._write_state()
        self.bus.publish("colors_pushed", track=track, colors=_hsbk_dicts(hsbk),
                         per_light=self._last_per_light, enabled=self.enabled, mode=mode,
                         scene_active=self._scene_active)

    def _paint_rgb(self, coords: list, picks=None, distribution=None) -> list:
        """User focus colors → one RGB per light, via round-robin or spatial IDW."""
        picks = self.cfg.paint_colors if picks is None else picks
        distribution = self.cfg.paint_distribution if distribution is None else distribution
        if distribution == "spatial":
            anchors = [(hex_to_rgb(c["hex"]), (float(c.get("x", 0.5)), float(c.get("y", 0.5))))
                       for c in picks]
            return idw_blend(coords, anchors)
        return round_robin(coords, [hex_to_rgb(c["hex"]) for c in picks])

    # ---- per-album scenes (Pass 4.5) ---------------------------------------
    def _scene_key(self, np):
        if not np or not np.has_track():
            return None
        if np.album:
            return f"{np.artist}|||{np.album}".strip("| ")
        return np.track_id()

    def _scene_rgb(self, scene, coords):
        """Per-light RGB for a saved scene, or None if not usable now."""
        if not scene:
            return None
        if scene.get("type") == "paint" and scene.get("colors"):
            return self._paint_rgb(coords, scene["colors"], scene.get("distribution"))
        if (scene.get("type") == "ai" and scene.get("rgb")
                and scene.get("layout_fp") == self.layout.fingerprint()
                and len(scene["rgb"]) == len(coords)):
            self.bus.publish("ai_reasoning", model=self.cfg.ollama_model, ok=True,
                             mood=scene.get("mood", ""), palette=scene.get("palette", []),
                             reasoning=scene.get("reasoning", ""), error="", cached=True)
            return [tuple(c) for c in scene["rgb"]]
        return None

    async def _spatial_rgb(self, np, art, coords, params, use_ai: bool, scene_key=None) -> list:
        """Per-region album-art color per bulb, optionally AI-refined then saved
        as this album's scene so it is reused without re-running the model."""
        base_rgb = await self.loop.run_in_executor(
            None, region_colors, art, coords, self.layout, params)
        rgb = base_rgb
        if use_ai and self.enhancer.active:
            model = self._active_model()
            enhanced = await self.enhancer.enhance(
                art, {"track": np.label() if np else ""}, coords, base_rgb)
            self.bus.publish("ai_reasoning", model=model, backend=self.cfg.ai_backend,
                             ok=bool(enhanced), mood=self.enhancer.last_mood,
                             palette=self.enhancer.last_palette,
                             reasoning=self.enhancer.last_reasoning,
                             error=self.enhancer.last_error, cached=False)
            if enhanced:
                rgb = enhanced
                log.info(f"   → AI palette ({model}) — scene saved")
                if scene_key:
                    self.scenes.set(scene_key, {
                        "type": "ai", "rgb": [list(c) for c in enhanced],
                        "layout_fp": self.layout.fingerprint(),
                        "source": f"ai_{self.cfg.ai_backend}",
                        "mood": self.enhancer.last_mood,
                        "palette": self.enhancer.last_palette,
                        "reasoning": self.enhancer.last_reasoning,
                    })
        return rgb

    def _active_model(self) -> str:
        return self.cfg.claude_model if self.cfg.ai_backend == "claude" else self.cfg.ollama_model

    def _sync_enhancer(self) -> None:
        """Rebuild the enhancer + clear the AI cache when ai-config changes (via API
        or a config-file edit picked up on the next track)."""
        sig = (self.cfg.ai_enhance, self.cfg.ai_backend, self.cfg.ollama_url,
               self.cfg.ollama_model, self.cfg.claude_model, self.cfg.ai_timeout_ms,
               has_anthropic_key())
        if sig != getattr(self, "_enh_sig", None):
            self.enhancer = build_enhancer(self.cfg)
            self._enh_sig = sig

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
            "mode": self.cfg.mode,
            "colors": _hsbk_dicts(self._last_hsbk),
            "per_light": self._last_per_light,
            "config": asdict(self.cfg),
            "layout": self.layout.to_dict(),
            "ai": {
                "enabled": bool(self.cfg.mode == "ai"),
                "backend": self.cfg.ai_backend,
                "model": self._active_model(),
                "url": self.cfg.ollama_url,
                "auto": self.cfg.ai_auto,
                "ollama_reachable": self.ollama_reachable,
                "claude_available": has_anthropic_key(),
                "offline": self.ai_offline,
                "last_ok": getattr(self.enhancer, "last_ok", None),
                "mood": getattr(self.enhancer, "last_mood", ""),
                "palette": getattr(self.enhancer, "last_palette", []),
                "reasoning": getattr(self.enhancer, "last_reasoning", ""),
                "error": getattr(self.enhancer, "last_error", ""),
            },
            "scene": {
                "saved": self.scenes.has(self._scene_key(self._last_np)),
                "active": self._scene_active,
                "type": (self.scenes.get(self._scene_key(self._last_np)) or {}).get("type"),
            },
        }

    def _write_state(self) -> None:
        write_state_file(self.snapshot())

    async def _repaint(self, source: str) -> None:
        """Re-render from cached state (used by mode/paint/layout/config edits)."""
        async with self._apply_lock:
            if self._last_np and self._last_art:
                await self._render(self._last_np, self._last_art, source)
            elif self.cfg.mode == "paint" and self.cfg.paint_colors:
                await self._render(None, None, source)   # paint needs no artwork
            else:
                self._write_state()

    # ---- Pass 4 mode/paint commands ----------------------------------------
    async def set_mode(self, mode: str) -> dict:
        if mode not in ("deterministic", "paint", "ai"):
            return self.snapshot()
        self.cfg.mode = mode
        self.cfg.ai_enhance = (mode == "ai")
        self.cfg.save()
        await self._repaint("mode")
        return self.snapshot()

    async def set_backend(self, backend: str) -> dict:
        """Manual backend switch (the 'Use Claude' trigger). Selecting claude also
        turns AI mode on; ollama is only useful when reachable."""
        if backend not in ("ollama", "claude"):
            return self.snapshot()
        self.cfg.ai_backend = backend
        if backend == "claude":
            self.cfg.mode = "ai"
            self.cfg.ai_enhance = True
            self.ai_offline = False
        self.cfg.save()
        await self._repaint("backend")
        return self.snapshot()

    async def set_focus(self, distribution: Optional[str], colors: list) -> dict:
        """Set paint focus colors ([{hex,x,y}]) and switch to paint mode."""
        if distribution in ("round_robin", "spatial"):
            self.cfg.paint_distribution = distribution
        self.cfg.paint_colors = colors or []
        self.cfg.mode = "paint"
        self.cfg.ai_enhance = False
        self.cfg.save()
        key = self._scene_key(self._last_np)
        if key and self.cfg.paint_colors:
            self.scenes.set(key, {"type": "paint",
                                  "distribution": self.cfg.paint_distribution,
                                  "colors": self.cfg.paint_colors,
                                  "source": "user_paint"})
        await self._repaint("paint")
        return self.snapshot()

    async def paint_clear(self) -> dict:
        self.cfg.paint_colors = []
        self.cfg.save()
        await self._repaint("paint_clear")
        return self.snapshot()

    async def scene_clear(self) -> dict:
        """Forget the current album's saved scene → it reverts to the global mode."""
        self.scenes.clear(self._scene_key(self._last_np))
        await self._repaint("scene_clear")
        return self.snapshot()

    async def move_light(self, label: str, x: float, y: float) -> dict:
        await self.set_layout({"lights": {label: {"x": x, "y": y}}})
        return self.snapshot()

    # ---- layout (GET/POST /layout) -----------------------------------------
    async def set_layout(self, partial: dict) -> dict:
        """Merge layout changes (positions and/or region size), persist, invalidate
        the AI cache (positions changed), and repaint from cached artwork."""
        if "region_w" in partial:
            self.layout.region_w = float(partial["region_w"])
        if "region_h" in partial:
            self.layout.region_h = float(partial["region_h"])
        for label, pos in (partial.get("lights") or {}).items():
            cur = self.layout.positions.get(label, {})
            if "x" in pos:
                cur["x"] = float(pos["x"])
            if "y" in pos:
                cur["y"] = float(pos["y"])
            self.layout.positions[label] = cur
        self.layout.save()
        async with self._apply_lock:
            if self._last_np and self._last_art:
                await self._render(self._last_np, self._last_art, "layout")
            else:
                self._write_state()
        return self.layout.to_dict()

    # ---- control-plane commands (Pass 3) -----------------------------------
    async def set_config(self, partial: dict) -> dict:
        """Apply a partial config, persist it, and repaint from cached artwork so
        slider moves are visible immediately (mirrors the old Electron 'Apply=live')."""
        known = {f.name for f in fields(Config)}
        updates = {k: v for k, v in partial.items() if k in known}
        for k, v in updates.items():
            setattr(self.cfg, k, v)
        if "mode" in updates:
            self.cfg.ai_enhance = (self.cfg.mode == "ai")   # mode stays authoritative
        self.cfg.save()
        self.lifx.cfg = self.cfg
        if "enabled" in updates:
            self.enabled = bool(updates["enabled"])
        await self._repaint("config")
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

    # ---- Pass 4.6: Ollama reachability → auto AI / manual Claude ------------
    async def _ping_ollama(self) -> bool:
        url = self.cfg.ollama_url
        if not url:
            return False
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=3, sock_connect=3)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(f"{url.rstrip('/')}/api/tags") as r:
                    return r.status == 200
        except Exception:
            return False

    async def _health_monitor(self) -> None:
        """Poll Ollama; auto-enable AI when it comes online, flag + fall back when
        it goes offline. Claude is never auto-selected."""
        while not self._stop.is_set():
            reachable = await self._ping_ollama()
            prev = self.ollama_reachable
            self.ollama_reachable = reachable
            if prev is None or prev != reachable:
                await self._on_ollama_transition(reachable)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass

    async def _on_ollama_transition(self, reachable: bool) -> None:
        if reachable:
            self.ai_offline = False
            if self.cfg.ai_auto and not (self.cfg.mode == "ai" and self.cfg.ai_backend == "ollama"):
                self.cfg.ai_backend = "ollama"
                self.cfg.mode = "ai"
                self.cfg.ai_enhance = True
                self.cfg.save()
                log.info("  ● Local AI (Ollama) online — AI mode enabled")
                self.bus.publish("status", message="Local AI online — AI mode enabled")
                await self._repaint("ollama-online")
            else:
                self.bus.publish("status", message="Local AI online")
        else:
            if self.cfg.mode == "ai" and self.cfg.ai_backend == "ollama":
                self.cfg.mode = "deterministic"
                self.cfg.ai_enhance = False
                self.cfg.save()
                self.ai_offline = True
                log.warning("  ○ Local AI (Ollama) offline — deterministic (use Claude to override)")
                self.bus.publish("status", message="Local AI (Ollama) offline — deterministic")
                await self._repaint("ollama-offline")
            else:
                self.ai_offline = True
                self.bus.publish("status", message="Local AI (Ollama) offline")
        # push fresh state so the GUI banner/backend chip updates
        self.bus.publish("state", **self.snapshot())

    # ---- supervisor: connect, run, reconnect -------------------------------
    async def run(self) -> None:
        log.info("CHROMA hub starting — headless Apple TV → LIFX")
        self._install_signals()
        self._health_task = self.loop.create_task(self._health_monitor())
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
            # Seed layout positions for any new bulbs so /layout is populated
            # for the phone UI even before spatial mode runs.
            if self.layout.ensure_labels(self.lifx.labels()):
                self.layout.save()

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

        if self._health_task:
            self._health_task.cancel()
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
