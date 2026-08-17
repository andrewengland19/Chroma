# =============================================================================
# hub/enhancers.py  —  Pass 3.5
# The PaletteEnhancer seam: refine the deterministic per-region palette with a
# local vision model (Ollama on the PC, over the LAN). Model-agnostic — the model
# name is config. Contract: return one RGB per region, or None to fall back to the
# deterministic colors. NEVER raises into the engine; any error/timeout → None.
#
# Key separation: the model returns *hues* (cached per album+layout); the engine
# still applies brightness/HSBK from live config, so sliders stay instant.
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
from typing import Optional, Protocol

import aiohttp

log = logging.getLogger("chroma-hub")


class PaletteEnhancer(Protocol):
    async def enhance(self, artwork: bytes, meta: dict,
                      coords: list, base_rgb: list) -> Optional[list]: ...


class NullEnhancer:
    """Default: no AI. Always defers to the deterministic palette."""
    last_ok = None
    last_reasoning = ""
    last_error = ""

    async def enhance(self, artwork, meta, coords, base_rgb):
        return None


def _hex_to_rgb(s: str) -> Optional[tuple]:
    s = (s or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _rgb_to_hex(c) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c)


_SCHEMA = {
    "type": "object",
    "properties": {
        "colors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"region": {"type": "integer"}, "hex": {"type": "string"}},
                "required": ["region", "hex"],
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["colors"],
}


class OllamaEnhancer:
    """Calls an Ollama vision model over the LAN. Returns one RGB per region or None."""

    def __init__(self, url: str, model: str, timeout_ms: int):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_ms = timeout_ms
        self.last_ok: Optional[bool] = None
        self.last_reasoning = ""
        self.last_error = ""

    def _prompt(self, meta: dict, coords: list, base_rgb: list) -> str:
        lines = []
        for i, ((x, y), rgb) in enumerate(zip(coords, base_rgb)):
            lines.append(f"  region {i}: at (x={x:.2f}, y={y:.2f}), current guess {_rgb_to_hex(rgb)}")
        track = meta.get("track") or "unknown"
        return (
            "You are a lighting color director choosing ambient room-light colors from an "
            "album cover. The image is split into regions positioned over the artwork "
            "(x 0=left→1=right, y 0=top→1=bottom). For EACH region, pick the single hex color "
            "that best captures the artwork's dominant, emotionally salient color at that "
            "location. Prefer vivid, on-mood colors; AVOID near-white/near-black backgrounds, "
            "borders, and text/logos. Keep each region's color faithful to that part of the art.\n"
            f"Now playing: {track}\n"
            f"Regions ({len(coords)}):\n" + "\n".join(lines) + "\n"
            "Respond as JSON: {\"colors\":[{\"region\":<int>,\"hex\":\"#rrggbb\"}...],\"reasoning\":\"<short>\"}"
        )

    async def enhance(self, artwork, meta, coords, base_rgb):
        if not self.url or not coords:
            return None
        b64 = base64.b64encode(artwork).decode()
        payload = {
            "model": self.model,
            "stream": False,
            "format": _SCHEMA,
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": self._prompt(meta, coords, base_rgb),
                          "images": [b64]}],
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_ms / 1000)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(f"{self.url}/api/chat", json=payload) as r:
                    r.raise_for_status()
                    data = await r.json()
            content = (data.get("message") or {}).get("content", "")
            parsed = json.loads(content)
            by_region = {int(c["region"]): _hex_to_rgb(c.get("hex", "")) for c in parsed.get("colors", [])}
            out = [by_region.get(i) or base_rgb[i] for i in range(len(coords))]
            self.last_ok = True
            self.last_reasoning = str(parsed.get("reasoning", ""))[:300]
            self.last_error = ""
            return out
        except Exception as e:
            self.last_ok = False
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning(f"  ! Ollama enhance failed ({self.last_error}); using deterministic palette")
            return None


def build_enhancer(cfg) -> PaletteEnhancer:
    if getattr(cfg, "ai_enhance", False) and getattr(cfg, "ollama_url", ""):
        return OllamaEnhancer(cfg.ollama_url, cfg.ollama_model, cfg.ai_timeout_ms)
    return NullEnhancer()
