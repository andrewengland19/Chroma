# =============================================================================
# hub/oklab.py — OKLab colour maths (Björn Ottosson, 2020) for the console:
# the D2 board seed (mean of a group's art colours) and the D7 strobe edge
# (colour jump >= 0.10). Pure functions; colours are HSB (outputs.base).
# =============================================================================

from __future__ import annotations

import math

from outputs.base import HSB


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gamma(c: float) -> float:
    c = min(1.0, max(0.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def rgb_to_oklab(rgb: tuple) -> tuple:
    """sRGB 0..1 -> (L, a, b)."""
    r, g, b = (_lin(x) for x in rgb)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab_to_rgb(lab: tuple) -> tuple:
    L, a, b = lab
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return tuple(_gamma(x) for x in (r, g, bl))


def chroma_lab(h: float, s: float) -> tuple:
    """OKLab of a hue/sat at full brightness: the colour, independent of level."""
    import colorsys
    return rgb_to_oklab(colorsys.hsv_to_rgb((h % 360.0) / 360.0, min(1.0, max(0.0, s)), 1.0))


def distance(a: HSB, b: HSB) -> float:
    """OKLab distance between the colours (hue/sat) of two HSB values."""
    return math.dist(chroma_lab(a.h, a.s), chroma_lab(b.h, b.s))


def mean_hue_sat(colors: list) -> tuple:
    """(hue, sat) of the OKLab mean of HSB colours (at full brightness)."""
    import colorsys
    labs = [chroma_lab(c.h, c.s) for c in colors]
    n = len(labs)
    rgb = oklab_to_rgb(tuple(sum(x[i] for x in labs) / n for i in range(3)))
    h, s, _v = colorsys.rgb_to_hsv(*rgb)
    return (h * 360.0) % 360.0, s
