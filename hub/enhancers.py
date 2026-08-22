# =============================================================================
# hub/enhancers.py  —  Pass 3.5 (Ollama) + Pass 4.6 (Claude fallback)
# The PaletteEnhancer seam: refine the deterministic per-region palette with a
# vision model. Two backends share one contract (return one RGB per region, or
# None to fall back to deterministic; NEVER raise into the engine):
#   • OllamaEnhancer — local model on the LAN (auto-preferred).
#   • ClaudeEnhancer — Anthropic API (manual "Use Claude" fallback).
# The model returns hues (cached per album+layout by the engine); brightness/HSBK
# are applied from live config, so sliders stay instant.
# =============================================================================

from __future__ import annotations

import base64
import json
import logging
from typing import Optional, Protocol

import aiohttp

from keystore import load_anthropic_key

log = logging.getLogger("chroma-hub")


class PaletteEnhancer(Protocol):
    active: bool
    async def enhance(self, artwork: bytes, meta: dict,
                      coords: list, base_rgb: list) -> Optional[list]: ...


# ---- shared helpers ---------------------------------------------------------

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


def _build_prompt(meta: dict, coords: list, base_rgb: list) -> str:
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
        "Also give the overall mood (2-4 words), an ordered palette (dominant→least, hex), "
        "and one or two sentences of reasoning. Respond as JSON: "
        "{\"mood\":\"<words>\",\"palette\":[\"#rrggbb\"...],"
        "\"colors\":[{\"region\":<int>,\"hex\":\"#rrggbb\"}...],\"reasoning\":\"<short>\"}"
    )


def _parse_result(parsed: dict, coords: list, base_rgb: list):
    by_region = {int(c["region"]): _hex_to_rgb(c.get("hex", "")) for c in parsed.get("colors", [])}
    out = [by_region.get(i) or base_rgb[i] for i in range(len(coords))]
    mood = str(parsed.get("mood", ""))[:60]
    pal = [h for h in (parsed.get("palette", []) or []) if _hex_to_rgb(h)][:8]
    reasoning = str(parsed.get("reasoning", ""))[:300]
    return out, mood, pal, reasoning


# Ollama accepts a plain JSON-schema `format`; Anthropic strict json_schema needs
# additionalProperties:false + required on every object.
_OLLAMA_SCHEMA = {
    "type": "object",
    "properties": {
        "mood": {"type": "string"},
        "palette": {"type": "array", "items": {"type": "string"}},
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
_CLAUDE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mood": {"type": "string"},
        "palette": {"type": "array", "items": {"type": "string"}},
        "colors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"region": {"type": "integer"}, "hex": {"type": "string"}},
                "required": ["region", "hex"],
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["mood", "palette", "colors", "reasoning"],
}


class _StatusMixin:
    def __init__(self):
        self.last_ok: Optional[bool] = None
        self.last_reasoning = ""
        self.last_error = ""
        self.last_mood = ""
        self.last_palette: list = []


class NullEnhancer(_StatusMixin):
    """Default: no AI. Always defers to the deterministic palette."""
    active = False

    async def enhance(self, artwork, meta, coords, base_rgb):
        return None


# =============================================================================
# Ollama (local, LAN)
# =============================================================================

class OllamaEnhancer(_StatusMixin):
    active = True

    def __init__(self, url: str, model: str, timeout_ms: int):
        super().__init__()
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_ms = timeout_ms

    async def enhance(self, artwork, meta, coords, base_rgb):
        if not self.url or not coords:
            return None
        b64 = base64.b64encode(artwork).decode()
        payload = {
            "model": self.model, "stream": False, "format": _OLLAMA_SCHEMA,
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": _build_prompt(meta, coords, base_rgb),
                          "images": [b64]}],
        }
        # Fast-fail if the host is unreachable so the room isn't blank for the full budget.
        timeout = aiohttp.ClientTimeout(total=self.timeout_ms / 1000, sock_connect=3)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.post(f"{self.url}/api/chat", json=payload) as r:
                    r.raise_for_status()
                    data = await r.json()
            parsed = json.loads((data.get("message") or {}).get("content", ""))
            out, self.last_mood, self.last_palette, self.last_reasoning = \
                _parse_result(parsed, coords, base_rgb)
            self.last_ok, self.last_error = True, ""
            return out
        except Exception as e:
            self.last_ok, self.last_error = False, f"{type(e).__name__}: {e}"
            log.warning(f"  ! Ollama enhance failed ({self.last_error}); using deterministic palette")
            return None


# =============================================================================
# Claude / Anthropic API (manual fallback)
# =============================================================================

class ClaudeEnhancer(_StatusMixin):
    active = True

    def __init__(self, model: str, api_key: str, timeout_ms: int):
        super().__init__()
        self.model = model
        self.api_key = api_key
        self.timeout_ms = timeout_ms

    async def enhance(self, artwork, meta, coords, base_rgb):
        if not coords:
            return None
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            self.last_ok, self.last_error = False, "anthropic SDK not installed"
            return None
        b64 = base64.b64encode(artwork).decode()
        try:
            client = AsyncAnthropic(api_key=self.api_key, timeout=self.timeout_ms / 1000)
            msg = await client.messages.create(
                model=self.model, max_tokens=1024,
                output_config={"format": {"type": "json_schema", "schema": _CLAUDE_SCHEMA},
                               "effort": "low"},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                                                 "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": _build_prompt(meta, coords, base_rgb)},
                ]}],
            )
            if getattr(msg, "stop_reason", None) == "refusal":
                self.last_ok, self.last_error = False, "refusal"
                log.warning("  ! Claude declined; using deterministic palette")
                return None
            text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), "")
            parsed = json.loads(text)
            out, self.last_mood, self.last_palette, self.last_reasoning = \
                _parse_result(parsed, coords, base_rgb)
            self.last_ok, self.last_error = True, ""
            return out
        except Exception as e:
            self.last_ok, self.last_error = False, f"{type(e).__name__}: {e}"
            log.warning(f"  ! Claude enhance failed ({self.last_error}); using deterministic palette")
            return None


# =============================================================================
# Backend selection
# =============================================================================

def build_enhancer(cfg) -> PaletteEnhancer:
    if not getattr(cfg, "ai_enhance", False):
        return NullEnhancer()
    backend = getattr(cfg, "ai_backend", "ollama")
    if backend == "claude":
        key = load_anthropic_key()
        if key:
            return ClaudeEnhancer(cfg.claude_model, key, cfg.ai_timeout_ms)
        log.warning("  ! Claude backend selected but no API key found — deterministic")
        return NullEnhancer()
    if getattr(cfg, "ollama_url", ""):
        return OllamaEnhancer(cfg.ollama_url, cfg.ollama_model, cfg.ai_timeout_ms)
    return NullEnhancer()
