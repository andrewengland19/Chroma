# =============================================================================
# hub/distribute.py  —  Pass 4
# Map N focus colors onto M lights arranged on a 2-D plane, two ways:
#   • round_robin: split the lights into N contiguous, near-equal groups
#     (sorted by position so it reads left→right), one color per group.
#   • idw_blend:  Shepard inverse-distance weighting — each light is a blend of
#     the nearby anchors, mixed in OKLab so the result stays vivid, not muddy.
# Pure functions; RGB tuples are 0-255 ints.
# =============================================================================

from __future__ import annotations

import math

Rgb = tuple  # (int, int, int)


# ---- hex <-> rgb ------------------------------------------------------------

def hex_to_rgb(s: str) -> Rgb:
    s = (s or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rgb_to_hex(c: Rgb) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in c)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---- sRGB <-> linear <-> OKLab (Björn Ottosson) -----------------------------

def _srgb_to_lin(u: float) -> float:
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4


def _lin_to_srgb(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * 12.92 if u <= 0.0031308 else 1.055 * (u ** (1 / 2.4)) - 0.055


def rgb_to_oklab(c: Rgb) -> tuple:
    r, g, b = (_srgb_to_lin(v / 255.0) for v in c)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_rgb(lab: tuple) -> Rgb:
    L, a, b = lab
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return tuple(int(round(_lin_to_srgb(v) * 255)) for v in (r, g, bb))


def blend_oklab(weighted: list) -> Rgb:
    """weighted = [(rgb, w), ...] → weighted mean in OKLab → rgb."""
    tot = sum(w for _, w in weighted) or 1.0
    L = a = b = 0.0
    for rgb, w in weighted:
        ll, aa, bb = rgb_to_oklab(rgb)
        L += ll * w; a += aa * w; b += bb * w
    return oklab_to_rgb((L / tot, a / tot, b / tot))


# ---- distribution strategies ------------------------------------------------

def round_robin(positions: list, colors: list) -> list:
    """N colors → M lights as contiguous, near-equal groups sorted by position.
    positions: [(x,y)] per light; colors: [rgb]. Returns [rgb] aligned to
    the ORIGINAL light order."""
    m = len(positions)
    n = len(colors)
    if m == 0 or n == 0:
        return []
    order = sorted(range(m), key=lambda i: (positions[i][0], positions[i][1]))
    base, rem = divmod(m, n)
    out = [None] * m
    k = 0
    for ci in range(n):
        size = base + (1 if ci < rem else 0)
        for _ in range(size):
            out[order[k]] = colors[ci]
            k += 1
    return out


def idw_blend(positions: list, anchors: list, power: float = 2.0, eps: float = 1e-3) -> list:
    """Shepard IDW. positions: [(x,y)] per light; anchors: [(rgb,(x,y))].
    Returns [rgb] per light, blended in OKLab."""
    if not positions or not anchors:
        return []
    out = []
    for (px, py) in positions:
        weighted = []
        exact = None
        for rgb, (ax, ay) in anchors:
            d2 = (px - ax) ** 2 + (py - ay) ** 2
            if d2 < 1e-9:
                exact = rgb
                break
            w = 1.0 / (d2 ** (power / 2) + eps)
            weighted.append((rgb, w))
        out.append(exact if exact is not None else blend_oklab(weighted))
    return out
