# =============================================================================
# hub/testing/art.py — deterministic album art for tests and `chromad --fake`.
#
# Solid-colour blocks survive JPEG round trips and ColorThief quantisation with
# stable results, so golden colour tests don't drift with encoder noise.
# =============================================================================

from __future__ import annotations

import io

from PIL import Image


def make_art(colors: list, size: int = 300, fmt: str = "PNG") -> bytes:
    """Art split into vertical stripes, one per colour (left → right).
    With 4 colours you get a 2x2 grid instead (TL, TR, BL, BR)."""
    img = Image.new("RGB", (size, size))
    n = len(colors)
    h = size // 2
    if n == 4:
        boxes = [(0, 0, h, h), (h, 0, size, h), (0, h, h, size), (h, h, size, size)]
    else:
        edges = [i * size // n for i in range(n)] + [size]
        boxes = [(edges[i], 0, edges[i + 1], size) for i in range(n)]
    for color, box in zip(colors, boxes):
        img.paste(tuple(color), box)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


# Named palettes used by the default fake scenario (loosely the mockup covers).
OLDER = [(214, 64, 84), (240, 140, 70), (120, 60, 160), (40, 30, 90)]
LEVITATING = [(225, 150, 90), (250, 200, 120), (180, 80, 60), (90, 40, 30)]
MIDNIGHT = [(80, 180, 120), (30, 90, 140), (20, 40, 80), (150, 220, 180)]
