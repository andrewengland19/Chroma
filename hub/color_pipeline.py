# =============================================================================
# hub/color_pipeline.py
# Pure color functions copied (logic-wise) from lifx_music_app.py:233-276.
#
# WHY A COPY: lifx_music_app.py imports rumps + tkinter at module load, which
# crashes in a headless context. Rather than couple the hub to that, Pass 1
# lifted just the pure functions here. Pass 2 parametrizes them so the engine
# can drive thresholds/brightness from live config while the spike keeps calling
# them with zero args (defaults below mirror the original Config defaults).
# Pass N unifies this duplicate into the shared engine.
# =============================================================================

import colorsys
import io
from dataclasses import dataclass

from colorthief import ColorThief

# --- Defaults (mirror lifx_music_app.py Config defaults) ---------------------
WHITE_SAT_THRESHOLD = 45
WHITE_VAL_THRESHOLD = 215
PALETTE_OVERSAMPLE = 6
BRIGHTNESS = 0.75
BRIGHTNESS_DYNAMIC_RANGE = 1.0
BRIGHTNESS_FLOOR = 0.25
NEUTRAL_KELVIN = 3500


@dataclass
class PipelineParams:
    """Tunables the engine passes in from live config. Defaults == originals."""
    white_sat_threshold: int = WHITE_SAT_THRESHOLD
    white_val_threshold: int = WHITE_VAL_THRESHOLD
    palette_oversample: int = PALETTE_OVERSAMPLE
    brightness: float = BRIGHTNESS
    brightness_dynamic_range: float = BRIGHTNESS_DYNAMIC_RANGE
    brightness_floor: float = BRIGHTNESS_FLOOR
    neutral_kelvin: int = NEUTRAL_KELVIN


_DEFAULTS = PipelineParams()


def is_white(r: int, g: int, b: int, p: PipelineParams = _DEFAULTS) -> bool:
    _, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    sat = s * 255
    val = v * 255
    if sat < p.white_sat_threshold:
        return True
    if val > p.white_val_threshold and sat < p.white_sat_threshold * 1.5:
        return True
    return False


def extract_colors(image_bytes: bytes, count: int, p: PipelineParams = _DEFAULTS) -> list:
    """Up to `count` non-white dominant RGB colors, or [] if all are white."""
    buf = io.BytesIO(image_bytes)
    ct = ColorThief(buf)
    needed = count + p.palette_oversample
    raw = ct.get_palette(color_count=needed, quality=1) if needed > 1 else [ct.get_color(quality=1)]
    filtered = [c for c in raw if not is_white(*c, p)]
    return filtered[:count]


def compute_brightness_scales(rgb_colors: list, p: PipelineParams = _DEFAULTS) -> list:
    if not rgb_colors:
        return []
    raw_values = [colorsys.rgb_to_hsv(r / 255., g / 255., b / 255.)[2] for r, g, b in rgb_colors]
    if len(raw_values) == 1 or p.brightness_dynamic_range == 0.0:
        return [p.brightness] * len(rgb_colors)
    mean_v = sum(raw_values) / len(raw_values)
    scales = []
    for v in raw_values:
        dev = (v - mean_v) * p.brightness_dynamic_range
        scale = max(p.brightness_floor, min(1.0, p.brightness + dev))
        scales.append(scale)
    return scales


def rgb_to_hsbk(r: int, g: int, b: int, brightness_scale: float,
                p: PipelineParams = _DEFAULTS) -> tuple:
    """RGB 0-255 → LIFX HSBK (each channel 0-65535, kelvin 2500-9000)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    h, s, _ = colorsys.rgb_to_hsv(rf, gf, bf)
    return (
        int(h * 65535),
        int(s * 65535),
        int(brightness_scale * 65535),
        p.neutral_kelvin,
    )


def artwork_to_hsbk(image_bytes: bytes, count: int, p: PipelineParams = _DEFAULTS) -> list:
    """Convenience: artwork bytes → list of HSBK tuples (empty if majority white)."""
    rgb = extract_colors(image_bytes, count, p)
    if not rgb:
        return []
    scales = compute_brightness_scales(rgb, p)
    return [rgb_to_hsbk(r, g, b, bs, p) for (r, g, b), bs in zip(rgb, scales)]


def region_colors(image_bytes: bytes, coords: list, layout, p: PipelineParams = _DEFAULTS) -> list:
    """One dominant non-white RGB per (x, y) coordinate, cropping the art around
    each. Falls back to the whole-image dominant color for a region that's all
    white (so a bulb over a white patch still gets an on-theme color, not skipped).

    `coords` is a list of (x, y) in 0..1; `layout` supplies region_w/region_h.
    Returns a list of RGB tuples aligned with `coords`.
    """
    from PIL import Image
    from layout import crop_region, region_to_jpeg

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    # Whole-image fallback color (first non-white dominant, else raw dominant).
    whole = extract_colors(image_bytes, 1, p)
    fallback = whole[0] if whole else ColorThief(io.BytesIO(image_bytes)).get_color(quality=1)

    out = []
    for (x, y) in coords:
        crop = crop_region(img, x, y, layout.region_w, layout.region_h)
        got = extract_colors(region_to_jpeg(crop), 1, p)
        out.append(got[0] if got else fallback)
    return out
