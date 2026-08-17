# =============================================================================
# hub/layout.py  —  Pass 3.5
# Where each LIFX bulb physically sits, as normalized (x, y) coordinates on the
# album-art canvas: x 0→1 left→right, y 0→1 top→bottom. "spatial" paint mode
# crops the region of the artwork around each bulb's coordinate and colors that
# bulb from it, so the art projects across the room.
#
# Persisted to ~/.chroma/layout.json. Seeded heuristically from bulb labels on
# first use; the Pass 4 phone app will let you drag bulbs on a room canvas.
# =============================================================================

from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass, field

from PIL import Image

LAYOUT_PATH = os.path.expanduser("~/.chroma/layout.json")


@dataclass
class Layout:
    # label → {"x": float, "y": float}   (normalized 0..1)
    positions: dict[str, dict] = field(default_factory=dict)
    region_w: float = 0.40   # crop width  as a fraction of the image
    region_h: float = 0.40   # crop height as a fraction of the image

    # ---- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {"region_w": self.region_w, "region_h": self.region_h,
                "lights": self.positions}

    def save(self) -> None:
        os.makedirs(os.path.dirname(LAYOUT_PATH), exist_ok=True)
        tmp = LAYOUT_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        os.replace(tmp, LAYOUT_PATH)

    @classmethod
    def load(cls) -> "Layout":
        if os.path.exists(LAYOUT_PATH):
            try:
                with open(LAYOUT_PATH) as f:
                    d = json.load(f)
                return cls(
                    positions=d.get("lights", {}),
                    region_w=float(d.get("region_w", 0.40)),
                    region_h=float(d.get("region_h", 0.40)),
                )
            except Exception:
                pass
        return cls()

    # ---- identity (for cache keys) -----------------------------------------
    def fingerprint(self) -> str:
        return hashlib.sha1(
            json.dumps(self.to_dict(), sort_keys=True).encode()
        ).hexdigest()[:12]

    # ---- lookups -----------------------------------------------------------
    def pos_for(self, label: str) -> tuple[float, float]:
        p = self.positions.get(label)
        if not p:
            return (0.5, 0.5)  # unknown bulb → center of the art
        return (float(p.get("x", 0.5)), float(p.get("y", 0.5)))

    def ensure_labels(self, labels: list[str]) -> bool:
        """Add any missing bulbs with seeded positions. Returns True if changed."""
        changed = False
        for lbl in labels:
            if lbl not in self.positions:
                x, y = _seed_xy(lbl, labels)
                self.positions[lbl] = {"x": round(x, 3), "y": round(y, 3)}
                changed = True
        return changed


def _seed_xy(label: str, all_labels: list[str]) -> tuple[float, float]:
    """Heuristic first guess from the bulb's label; the user refines later.
    x from left/right words; y from tv (near screen, upper) vs couch (lower)."""
    lo = label.lower()
    if "left" in lo:
        x = 0.20
    elif "right" in lo:
        x = 0.80
    else:  # spread unlabeled bulbs evenly across the middle
        others = [l for l in all_labels if "left" not in l.lower() and "right" not in l.lower()]
        i = others.index(label) if label in others else 0
        x = 0.5 if len(others) <= 1 else 0.30 + 0.40 * (i / (len(others) - 1))
    if "tv" in lo or "screen" in lo:
        y = 0.30
    elif "couch" in lo or "sofa" in lo:
        y = 0.72
    else:
        y = 0.50
    return (x, y)


# =============================================================================
# Region cropping
# =============================================================================

def crop_region(img: Image.Image, x: float, y: float,
                w: float, h: float) -> Image.Image:
    """Crop a w×h (fractional) box centered on (x, y), clamped to the image."""
    W, H = img.size
    bw, bh = max(1, int(w * W)), max(1, int(h * H))
    cx, cy = int(x * W), int(y * H)
    left = max(0, min(W - bw, cx - bw // 2))
    top = max(0, min(H - bh, cy - bh // 2))
    return img.crop((left, top, left + bw, top + bh))


def region_to_jpeg(region: Image.Image) -> bytes:
    buf = io.BytesIO()
    region.convert("RGB").save(buf, "JPEG")
    return buf.getvalue()
